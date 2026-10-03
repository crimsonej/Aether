"""OrderExecutionManager – bridges signals to broker via risk management."""

import asyncio
import logging
from typing import Dict, Any, Optional
from datetime import datetime, timezone

from aether.core.risk.capital import CapitalManager, RiskProfile
from aether.core.utils.logger import logger


class OrderExecutionManager:
    """
    Manages the complete order lifecycle:
    signal.emitted -> risk validation -> position sizing -> broker execution -> position tracking
    """
    
    def __init__(self, config, bus):
        self.config = config
        self.bus = bus
        self._broker = None
        self._capital_manager: Optional[CapitalManager] = None
        self._running = False
        self._subscribed = False
        
        # Risk profile from config
        self._load_risk_profile()
        
        # Track signal -> position mapping
        self._signal_positions: Dict[str, str] = {}  # signal_id -> position_id
        
        # Pending limit/stop orders
        self._pending_orders: Dict[str, Dict[str, Any]] = {}

    def _load_risk_profile(self) -> None:
        """Load risk profile from config."""
        try:
            risk_cfg = self.config.get("risk", {})
            profile = RiskProfile(
                account_size=risk_cfg.get("account_size", 10000.0),
                risk_per_trade_percent=risk_cfg.get("risk_per_trade_percent", 1.0),
                max_daily_risk_percent=risk_cfg.get("max_daily_risk_percent", 3.0),
                max_open_positions=risk_cfg.get("max_open_positions", 10),
                max_portfolio_heat=risk_cfg.get("max_portfolio_heat", 5.0),
            )
            self._capital_manager = CapitalManager(profile)
            logger.info("[OrderExecutionManager] risk profile loaded", 
                       account_size=profile.account_size,
                       risk_per_trade=profile.risk_per_trade_percent)
        except Exception as e:
            logger.exception("[OrderExecutionManager] failed to load risk profile: %s", e)
            # Default conservative profile
            profile = RiskProfile(
                account_size=10000.0,
                risk_per_trade_percent=1.0,
                max_daily_risk_percent=3.0,
                max_open_positions=5,
                max_portfolio_heat=5.0,
            )
            self._capital_manager = CapitalManager(profile)

    def set_broker(self, broker) -> None:
        """Inject broker adapter after it's loaded."""
        self._broker = broker
        logger.info("[OrderExecutionManager] broker connected")

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        logger.info("[OrderExecutionManager] started")

    async def stop(self) -> None:
        self._running = False
        logger.info("[OrderExecutionManager] stopped")

    async def register_events(self, bus) -> None:
        """Subscribe to signal events."""
        await bus.subscribe("signal.emitted", self._on_signal_emitted)
        await bus.subscribe("signal.activated", self._on_signal_activated)
        await bus.subscribe("signal.closed", self._on_signal_closed)
        await bus.subscribe("signal.expired", self._on_signal_expired)
        await bus.subscribe("signal.missed", self._on_signal_missed)
        
        # Subscribe to price updates for pending order checks
        await bus.subscribe("data.quote", self._on_quote_update)
        await bus.subscribe("data.candle", self._on_candle_update)
        
        self._subscribed = True
        logger.info("[OrderExecutionManager] subscribed to signal and market events")

    async def _on_signal_emitted(self, signal: Dict[str, Any]) -> None:
        """Handle new signal - validate risk and prepare order."""
        if not self._running or not self._broker:
            return
            
        signal_id = signal.get("signal_id")
        symbol = signal.get("symbol")
        direction = signal.get("direction")
        trade = signal.get("trade_construction") or signal.get("trade") or {}
        
        logger.info("[OrderExecutionManager] signal received", signal_id=signal_id, symbol=symbol)
        
        # Check if we already have a position for this signal
        if signal_id in self._signal_positions:
            logger.warning("[OrderExecutionManager] signal already processed", signal_id=signal_id)
            return
        
        # Extract trade parameters
        entry = trade.get("entry", {})
        sl = trade.get("stop_loss", {})
        tp = trade.get("take_profit", {})
        
        entry_price = entry.get("price")
        sl_price = sl.get("price")
        tp_price = tp.get("price")
        entry_type = entry.get("type", "market")
        expiry = trade.get("expiry")
        
        if not entry_price or not sl_price:
            logger.warning("[OrderExecutionManager] incomplete trade data", signal_id=signal_id)
            await self._publish_rejected(signal, "incomplete_trade_data")
            return
        
        # Calculate stop distance for position sizing
        stop_distance = abs(entry_price - sl_price)
        if stop_distance <= 0:
            logger.warning("[OrderExecutionManager] invalid stop distance", signal_id=signal_id)
            await self._publish_rejected(signal, "invalid_stop_distance")
            return
        
        # Risk validation
        if not self._capital_manager.can_open_position(
            self._capital_manager.get_portfolio_heat(0)  # Will recalc with actual risk
        ):
            logger.warning("[OrderExecutionManager] risk limits reached", signal_id=signal_id)
            await self._publish_rejected(signal, "risk_limits_reached")
            return
        
        # Calculate position size
        volume = self._capital_manager.calculate_position_size(stop_distance)
        if volume is None or volume <= 0:
            logger.warning("[OrderExecutionManager] invalid position size", signal_id=signal_id)
            await self._publish_rejected(signal, "invalid_position_size")
            return
        
        # Apply max volume limits
        max_volume = self.config.get("trading.max_volume_per_trade", 10.0)
        volume = min(volume, max_volume)
        
        # Round to broker step (0.01 lots typical)
        volume = round(volume, 2)
        
        # Prepare order
        order = {
            "symbol": symbol,
            "direction": direction,
            "volume": volume,
            "order_type": "MARKET" if entry_type == "market" else "LIMIT",
            "price": entry_price if entry_type != "market" else None,
            "sl": sl_price,
            "tp": tp_price,
            "comment": signal.get("strategy_name", "aether"),
            "magic": hash(signal_id) % 100000,
        }
        
        # Store pending order info for tracking
        self._pending_orders[signal_id] = {
            "signal": signal,
            "order": order,
            "entry_type": entry_type,
            "expiry": expiry,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        
        # Execute if market order
        if entry_type == "market":
            await self._execute_order(signal_id, order)
        else:
            logger.info("[OrderExecutionManager] limit/stop order queued", 
                       signal_id=signal_id, price=entry_price)

    async def _execute_order(self, signal_id: str, order: Dict[str, Any]) -> None:
        """Execute order via broker."""
        if not self._broker:
            logger.error("[OrderExecutionManager] no broker available")
            await self._publish_rejected(self._pending_orders[signal_id]["signal"], "no_broker")
            return
        
        result = await self._broker.execute_order(order)
        
        if result.get("success"):
            position_id = result.get("position_id")
            fill_price = result.get("fill_price")
            
            self._signal_positions[signal_id] = position_id
            self._capital_manager.active_positions_count += 1
            
            logger.info("[OrderExecutionManager] order filled", 
                       signal_id=signal_id, position_id=position_id, fill_price=fill_price)
            
            # Publish execution event
            await self.bus.publish("order.executed", {
                "signal_id": signal_id,
                "position_id": position_id,
                "symbol": order["symbol"],
                "direction": order["direction"],
                "volume": order["volume"],
                "fill_price": fill_price,
                "sl": order.get("sl"),
                "tp": order.get("tp"),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })
            
            # Remove from pending
            self._pending_orders.pop(signal_id, None)
        else:
            error = result.get("error", "unknown")
            logger.warning("[OrderExecutionManager] order failed", signal_id=signal_id, error=error)
            
            # Check if pending order (limit/stop not triggered)
            if result.get("pending"):
                logger.info("[OrderExecutionManager] order pending", signal_id=signal_id)
                return
            
            await self._publish_rejected(self._pending_orders[signal_id]["signal"], error)
            self._pending_orders.pop(signal_id, None)

    async def _on_signal_activated(self, signal: Dict[str, Any]) -> None:
        """Handle signal activation (pending -> active)."""
        # For limit orders that get filled
        signal_id = signal.get("signal_id")
        if signal_id in self._pending_orders:
            pending = self._pending_orders[signal_id]
            if pending["entry_type"] != "market":
                await self._execute_order(signal_id, pending["order"])

    async def _on_signal_closed(self, signal: Dict[str, Any]) -> None:
        """Handle signal closed (TP/SL hit) - close position."""
        signal_id = signal.get("signal_id")
        position_id = self._signal_positions.get(signal_id)
        
        if not position_id or not self._broker:
            return
        
        result = await self._broker.close_position(position_id)
        if result.get("success"):
            self._capital_manager.active_positions_count = max(0, 
                self._capital_manager.active_positions_count - 1)
            profit = result.get("profit", 0)
            logger.info("[OrderExecutionManager] position closed", 
                       position_id=position_id, profit=profit)
            
            await self.bus.publish("order.closed", {
                "signal_id": signal_id,
                "position_id": position_id,
                "profit": profit,
                "fill_price": result.get("fill_price"),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })
        else:
            logger.error("[OrderExecutionManager] failed to close position", 
                        position_id=position_id, error=result.get("error"))

    async def _on_signal_expired(self, signal: Dict[str, Any]) -> None:
        """Handle signal expiry - cancel pending orders."""
        signal_id = signal.get("signal_id")
        pending = self._pending_orders.pop(signal_id, None)
        
        if pending:
            logger.info("[OrderExecutionManager] expired signal, cancelled pending order", 
                       signal_id=signal_id)
            await self._publish_rejected(pending["signal"], "signal_expired")

    async def _on_signal_missed(self, signal: Dict[str, Any]) -> None:
        """Handle missed entry - cancel pending orders."""
        signal_id = signal.get("signal_id")
        pending = self._pending_orders.pop(signal_id, None)
        
        if pending:
            logger.info("[OrderExecutionManager] missed entry, cancelled pending order", 
                       signal_id=signal_id)
            await self._publish_rejected(pending["signal"], "entry_missed")

    async def _on_quote_update(self, quote: Dict[str, Any]) -> None:
        """Update broker price feed and check pending limit/stop orders."""
        if not self._broker:
            return
        
        symbol = quote.get("symbol")
        bid = quote.get("bid")
        ask = quote.get("ask")
        
        if symbol and bid is not None and ask is not None:
            self._broker.update_price(symbol, bid, ask, quote.get("timestamp"))
        
        # Check pending limit/stop orders
        await self._check_pending_orders(symbol, bid, ask)

    async def _on_candle_update(self, candle: Dict[str, Any]) -> None:
        """Check pending orders against candle data."""
        if not self._broker:
            return
        
        symbol = candle.get("symbol")
        high = candle.get("high")
        low = candle.get("low")
        
        if symbol and high is not None and low is not None:
            # Use midpoint for checking
            bid = low
            ask = high
            await self._check_pending_orders(symbol, bid, ask)

    async def _check_pending_orders(self, symbol: str, bid: float, ask: float) -> None:
        """Check if any pending limit/stop orders should be triggered."""
        to_execute = []
        
        for signal_id, pending in self._pending_orders.items():
            if pending["order"]["symbol"] != symbol:
                continue
            
            order = pending["order"]
            order_type = order["order_type"]
            price = order["price"]
            direction = order["direction"]
            
            triggered = False
            if order_type == "LIMIT":
                if direction == "BUY" and ask <= price:
                    triggered = True
                elif direction == "SELL" and bid >= price:
                    triggered = True
            elif order_type == "STOP":
                if direction == "BUY" and ask >= price:
                    triggered = True
                elif direction == "SELL" and bid <= price:
                    triggered = True
            
            if triggered:
                to_execute.append(signal_id)
        
        for signal_id in to_execute:
            pending = self._pending_orders.pop(signal_id)
            await self._execute_order(signal_id, pending["order"])

    async def _publish_rejected(self, signal: Dict[str, Any], reason: str) -> None:
        """Publish signal rejected event."""
        await self.bus.publish("signal.rejected", {
            "signal_id": signal.get("signal_id"),
            "symbol": signal.get("symbol"),
            "direction": signal.get("direction"),
            "reason": reason,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

    # -----------------------------------------------------------------
    # Status and monitoring
    # -----------------------------------------------------------------
    def get_status(self) -> Dict[str, Any]:
        """Get execution manager status."""
        account = self._broker.get_account() if self._broker else {}
        positions = self._broker.get_positions() if self._broker else []
        
        return {
            "running": self._running,
            "broker_connected": self._broker is not None,
            "risk_profile": {
                "account_size": self._capital_manager.profile.account_size,
                "risk_per_trade": self._capital_manager.profile.risk_per_trade_percent,
                "max_positions": self._capital_manager.profile.max_open_positions,
                "max_heat": self._capital_manager.profile.max_portfolio_heat,
            },
            "active_signals": len(self._signal_positions),
            "pending_orders": len(self._pending_orders),
            "account": account,
            "positions": len(positions),
        }

    async def health(self) -> Dict[str, Any]:
        return {
            "status": "OK" if self._running else "STOPPED",
            "component": "order_execution_manager",
            "broker_connected": self._broker is not None,
            "active_positions": len(self._signal_positions),
        }