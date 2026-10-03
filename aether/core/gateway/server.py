"""Aether Gateway – FastAPI server exposing the dashboard and control‑plane API.

Architecture decisions:
  * ``ServiceManager`` is the single source of truth for all subsystems.
  * ``MetricsExporter`` is retrieved from ``ServiceManager._subsystems`` after
    startup so we do not create a duplicate instance at module load time.
  * WebSocket connections use a query‑param token rather than HTTP Basic Auth
    because browsers cannot send Basic Auth headers over WebSocket.
"""

import asyncio
import hmac
import json
import os
from typing import Optional

from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Request, Depends, status
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
from fastapi.templating import Jinja2Templates

from aether.core.utils.logger import logger
from aether.core.gateway.manager import GatewayManager
from aether.core.service_manager import ServiceManager

# Load environment variables from .env file
load_dotenv()

app = FastAPI(title="Aether Gateway", version="1.0.0")

# Single ServiceManager instance for the process lifetime.
service_manager = ServiceManager()

# Templates directory.
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))

# NOTE: Dashboard is public. No authentication required.


def require_admin(request: Request) -> None:
    expected = os.getenv("AETHER_ADMIN_TOKEN")
    if not expected:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Administrative access is not configured",
        )
    supplied = request.headers.get("Authorization", "")
    scheme, _, token = supplied.partition(" ")
    if scheme.lower() != "bearer" or not hmac.compare_digest(token, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid administrative credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )


def require_telegram_webhook_secret(request: Request, adapter) -> None:
    expected = getattr(adapter, "webhook_secret", None)
    if not expected:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Telegram webhook secret is not configured",
        )
    supplied = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
    if not hmac.compare_digest(supplied, expected):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid webhook secret")


def redact_config(value):
    sensitive_terms = ("token", "secret", "password", "api_key", "account_id", "credential")
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            if any(term in str(key).lower() for term in sensitive_terms):
                result[key] = "[REDACTED]"
            else:
                result[key] = redact_config(item)
        return result
    if isinstance(value, list):
        return [redact_config(item) for item in value]
    return value


# ---------------------------------------------------------------------------
# Lifecycle hooks
# ---------------------------------------------------------------------------
@app.on_event("startup")
async def startup_event():
    logger.info("gateway_starting")
    await service_manager.start()
    # Start the MetricsExporter that was registered by ServiceManager.
    metrics_sub = service_manager._subsystems.get("metrics")
    if metrics_sub:
        metrics_sub.instance.bus = service_manager.bus
    logger.info("gateway_started")

    # After everything is up, send a ping to Telegram (if configured).
    try:
        adapters_sub = service_manager._subsystems.get("adapters")
        if adapters_sub:
            try:
                tg = adapters_sub.instance.get("telegram")
            except KeyError:
                tg = None
            if tg and hasattr(tg, "send_startup_ping"):
                from datetime import datetime, timezone
                chain = []
                model_sub = service_manager._subsystems.get("model")
                if model_sub and hasattr(model_sub.instance, "chain"):
                    chain = model_sub.instance.chain
                providers = []
                dpm_sub = service_manager._subsystems.get("data")
                # providers list is gathered from the data engine if available
                if dpm_sub and hasattr(dpm_sub.instance, "_provider_manager"):
                    providers = list(dpm_sub.instance._provider_manager.providers.keys())
                subsystems = service_manager.status()
                await tg.send_startup_ping({
                    "started_at": datetime.now(timezone.utc).isoformat(),
                    "model_chain": " -> ".join(chain) if chain else "n/a",
                    "data_providers": ", ".join(providers) if providers else "n/a",
                    "subsystems": len(subsystems),
                })
    except Exception:
        logger.exception("startup_ping_failed")


@app.on_event("shutdown")
async def shutdown_event():
    await service_manager.stop()
    logger.info("gateway_stopped")


# ---------------------------------------------------------------------------
# Control‑plane endpoints
# ---------------------------------------------------------------------------
@app.post("/start", dependencies=[Depends(require_admin)])
async def start_system():
    await service_manager.start()
    return JSONResponse({"status": "started"})


@app.post("/stop", dependencies=[Depends(require_admin)])
async def stop_system():
    await service_manager.stop()
    return JSONResponse({"status": "stopped"})


@app.post("/reload", dependencies=[Depends(require_admin)])
async def reload_config():
    try:
        await service_manager.reload()
        return JSONResponse({"status": "reloaded"})
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)


# ---------------------------------------------------------------------------
# Status & Health
# ---------------------------------------------------------------------------
@app.get("/api/status", response_class=JSONResponse)
async def api_status():
    """Returns overall system running state and per‑subsystem lifecycle."""
    return {
        "status": "running" if service_manager._running else "stopped",
        "subsystems": service_manager.status(),
    }


@app.get("/health", response_class=JSONResponse)
async def health_endpoint():
    """Returns the aggregated health dict from HealthMonitor."""
    health_sub = service_manager._subsystems.get("health")
    if health_sub:
        raw = health_sub.instance
        if hasattr(raw, "health"):
            return await raw.health()
    return JSONResponse({"status": "unknown"})


@app.get("/metrics")
async def metrics_endpoint():
    metrics_sub = service_manager._subsystems.get("metrics")
    if metrics_sub:
        body, content_type = await metrics_sub.instance.metrics_endpoint()
        return PlainTextResponse(body, media_type=content_type)
    return PlainTextResponse("# metrics unavailable\n", media_type="text/plain")


# ---------------------------------------------------------------------------
# Trading endpoints
# ---------------------------------------------------------------------------
@app.get("/api/trading/account", response_class=JSONResponse)
async def trading_account():
    """Get paper trading account summary."""
    order_manager = service_manager._subsystems.get("order_manager")
    if not order_manager:
        raise HTTPException(status_code=503, detail="Order manager not available")
    return order_manager.instance.get_status()


@app.get("/api/trading/positions", response_class=JSONResponse)
async def trading_positions():
    """Get all open positions with PnL."""
    order_manager = service_manager._subsystems.get("order_manager")
    if not order_manager or not order_manager.instance._broker:
        raise HTTPException(status_code=503, detail="Broker not connected")
    positions = order_manager.instance._broker.get_positions()
    return {"positions": positions, "count": len(positions)}


@app.get("/api/trading/history", response_class=JSONResponse)
async def trading_history(limit: int = 100):
    """Get trade history."""
    order_manager = service_manager._subsystems.get("order_manager")
    if not order_manager or not order_manager.instance._broker:
        raise HTTPException(status_code=503, detail="Broker not connected")
    history = order_manager.instance._broker.get_trade_history(limit)
    return {"trades": history, "count": len(history)}


@app.get("/api/trading/status", response_class=JSONResponse)
async def trading_status():
    """Get order execution manager status."""
    order_manager = service_manager._subsystems.get("order_manager")
    if not order_manager:
        raise HTTPException(status_code=503, detail="Order manager not available")
    return order_manager.instance.get_status()


@app.post("/api/trading/close/{position_id}", dependencies=[Depends(require_admin)])
async def close_position(position_id: str, volume: Optional[float] = None):
    """Close a position (admin only)."""
    order_manager = service_manager._subsystems.get("order_manager")
    if not order_manager or not order_manager.instance._broker:
        raise HTTPException(status_code=503, detail="Broker not connected")
    result = await order_manager.instance._broker.close_position(position_id, volume)
    return JSONResponse(result)


# ---------------------------------------------------------------------------
# Telegram inbound webhook
# ---------------------------------------------------------------------------
@app.post("/api/telegram/webhook")
async def telegram_webhook(request: Request):
    """Receives Telegram Update payloads and routes them to TelegramAdapter."""
    adapters_sub = service_manager._subsystems.get("adapters")
    if not adapters_sub:
        raise HTTPException(status_code=503, detail="Adapters not ready")
    registry = adapters_sub.instance
    try:
        adapter = registry.get("telegram")
        require_telegram_webhook_secret(request, adapter)
        update = await request.json()
        await adapter.receive_message(update)
    except KeyError:
        raise HTTPException(status_code=404, detail="Telegram adapter not loaded")
    return JSONResponse({"ok": True})


# ---------------------------------------------------------------------------
# Dashboard page routes (server‑rendered HTML)
# ---------------------------------------------------------------------------
@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    health_sub = service_manager._subsystems.get("health")
    health_data = {}
    if health_sub and hasattr(health_sub.instance, "health"):
        health_data = await health_sub.instance.health()
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={"health": health_data},
    )


@app.get("/signals", response_class=HTMLResponse)
async def signals_page(request: Request):
    signal_sub = service_manager._subsystems.get("signal")
    recent_signals = []
    if signal_sub and hasattr(signal_sub.instance, "get_recent"):
        recent_signals = signal_sub.instance.get_recent(50)
    return templates.TemplateResponse(
        request=request,
        name="signals.html",
        context={"signals": recent_signals},
    )


@app.get("/config", response_class=HTMLResponse)
async def config_page(request: Request):
    cfg = service_manager.config._data
    return templates.TemplateResponse(
        request=request,
        name="config.html",
        context={"config": json.dumps(redact_config(cfg), indent=2)},
    )


@app.get("/strategies", response_class=HTMLResponse)
async def strategies_page(request: Request):
    try:
        strategies = service_manager.config.get("strategies")
    except KeyError:
        strategies = []
    return templates.TemplateResponse(
        request=request,
        name="strategies.html",
        context={"strategies": strategies},
    )


@app.get("/health-dashboard", response_class=HTMLResponse)
async def health_page(request: Request):
    health_sub = service_manager._subsystems.get("health")
    health_data = {}
    if health_sub and hasattr(health_sub.instance, "health"):
        health_data = await health_sub.instance.health()
    subsystems = service_manager.status()
    return templates.TemplateResponse(
        request=request,
        name="health.html",
        context={"health": health_data, "subsystems": subsystems},
    )


@app.get("/trading", response_class=HTMLResponse)
async def trading_page(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="trading.html",
        context={},
    )


# ---------------------------------------------------------------------------
# WebSocket – real‑time event stream
# ---------------------------------------------------------------------------
@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """Stream all EventBus events to connected dashboard clients.

    Browsers cannot send Basic Auth headers over WebSocket, so authentication
    is handled via an optional query‑param token that clients must supply.
    For the initial implementation we accept all connections; production
    deployments should verify the token against the user store.
    """
    await websocket.accept()

    async def forward(event_payload: dict):
        try:
            await websocket.send_json(event_payload)
        except Exception:
            pass

    await service_manager.bus.subscribe("*", forward)
    try:
        while True:
            # Keep the connection alive; we only push outbound data.
            data = await websocket.receive_text()
            # Ignore inbound messages for now; could be used for ping/pong.
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        service_manager.bus.unsubscribe("*", forward)


# ---------------------------------------------------------------------------
# Module‑level run helper
# ---------------------------------------------------------------------------
def run_gateway(port: int = 18791):
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=port)
