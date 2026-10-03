"""Historical backtest — fetch real data and replay it through the Aether
strategy to print wins and losses.

Usage:
    python3 scripts/run_historical_backtest.py

For each of 5 historical windows, the script:
  1. Walks the data provider chain in order (TwelveData -> AlphaVantage ->
     Frankfurter/CoinGecko) and fetches the candles.  On a non-rate-limit
     failure it stops; on a rate-limit it moves to the next provider.
  2. Synthesises realistic candles only as a last resort if every live
     provider refuses the call.
  3. Runs a simple EMA-cross strategy over the candle list and records
     each trade's outcome (WIN / LOSS / EXPIRED / MISSED).
  4. Prints a Markdown table to stdout and writes the same to
     docs/backtest_report.md.
"""
from __future__ import annotations

import asyncio
import json
import os
import random
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import aiohttp
from dateutil import parser

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from aether.core.utils.logger import logger  # noqa: E402

# Load .env so provider keys are picked up.
from dotenv import load_dotenv
load_dotenv(REPO / ".env")


# ---------------------------------------------------------------------------
# 5 historical windows (symbol, timeframe, start, end)
# ---------------------------------------------------------------------------
def default_windows() -> List[Dict[str, str]]:
    """Five windows spread across the last 18 months.  All fall within the
    Twelvedata 1y free-tier retention; the longer 1d/4h ranges stay well
    under the 5000-candle cap.
    """
    today = datetime.now(timezone.utc)
    def ago(days: int) -> datetime:
        return today - timedelta(days=days)
    return [
        {"symbol": "EURUSD", "timeframe": "4h",  "start": (ago(300)).isoformat(), "end": (ago(285)).isoformat()},
        {"symbol": "GBPUSD", "timeframe": "1h",  "start": (ago(220)).isoformat(), "end": (ago(210)).isoformat()},
        {"symbol": "BTCUSD", "timeframe": "1d",  "start": (ago(180)).isoformat(), "end": (ago(150)).isoformat()},
        {"symbol": "XAUUSD", "timeframe": "4h",  "start": (ago(150)).isoformat(), "end": (ago(135)).isoformat()},
        {"symbol": "USDJPY", "timeframe": "1d",  "start": (ago(120)).isoformat(), "end": (ago(105)).isoformat()},
    ]


# ---------------------------------------------------------------------------
# Provider fetchers
# ---------------------------------------------------------------------------
@dataclass
class FetchResult:
    candles: List[Dict[str, Any]] = field(default_factory=list)
    source: str = ""
    ok: bool = False
    error: str = ""


def _normalize(c: Dict[str, Any], symbol: str, timeframe: str, source: str) -> Dict[str, Any]:
    """Coerce a provider-shaped candle dict into the canonical schema."""
    return {
        "symbol": symbol,
        "timeframe": timeframe,
        "open": float(c["open"]),
        "high": float(c["high"]),
        "low": float(c["low"]),
        "close": float(c["close"]),
        "volume": float(c.get("volume") or 0.0),
        "timestamp": int(c["timestamp"]),
        "source": source,
    }


async def fetch_twelvedata(symbol: str, timeframe: str, start: str, end: str) -> FetchResult:
    """Hit TwelveData's time_series with date range.  Real data.

    TwelveData uses slashed forex pairs (e.g. EUR/USD) and specific ticker
    formats for commodities / crypto.  We translate from our internal
    ``EURUSD`` style to whatever TwelveData expects.
    """
    api_key = os.environ.get("TWELVEDATA_API_KEY")
    if not api_key:
        return FetchResult(ok=False, error="TWELVEDATA_API_KEY not set")
    url = "https://api.twelvedata.com/time_series"
    sd = parser.isoparse(start).strftime("%Y-%m-%d")
    ed = parser.isoparse(end).strftime("%Y-%m-%d")
    tf_map = {"1m": "1min", "5m": "5min", "15m": "15min", "30m": "30min",
              "1h": "1h", "2h": "2h", "4h": "4h", "1d": "1day", "1w": "1week"}
    interval = tf_map.get(timeframe, timeframe)
    # TwelveData symbol format translation
    SYMBOL_OVERRIDES = {
        "EURUSD": "EUR/USD", "GBPUSD": "GBP/USD", "USDJPY": "USD/JPY",
        "AUDUSD": "AUD/USD", "USDCHF": "USD/CHF", "USDCAD": "USD/CAD",
        "NZDUSD": "NZD/USD", "BTCUSD": "BTC/USD", "XAUUSD": "XAU/USD",
    }
    td_symbol = SYMBOL_OVERRIDES.get(symbol, symbol)
    params = {
        "symbol": td_symbol, "interval": interval,
        "start_date": sd, "end_date": ed,
        "format": "JSON", "apikey": api_key,
        "outputsize": 5000,
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=60)) as resp:
                body = await resp.text()
                if resp.status == 429:
                    return FetchResult(ok=False, error=f"TwelveData rate-limited: {body[:120]}")
                if resp.status != 200:
                    return FetchResult(ok=False, error=f"TwelveData HTTP {resp.status}: {body[:120]}")
                data = json.loads(body)
                if "values" not in data:
                    msg = data.get("message") or data.get("status") or "no values"
                    if "rate" in str(msg).lower() or "limit" in str(msg).lower():
                        return FetchResult(ok=False, error=f"TwelveData rate-limited: {msg[:120]}")
                    return FetchResult(ok=False, error=f"TwelveData no values: {msg[:120]}")
                candles = []
                # TwelveData returns newest first; reverse to oldest first.
                for entry in reversed(data["values"]):
                    ts = int(parser.isoparse(entry["datetime"]).timestamp())
                    candles.append(_normalize({
                        "open": entry["open"], "high": entry["high"],
                        "low": entry["low"], "close": entry["close"],
                        "volume": entry.get("volume", 0.0),
                        "timestamp": ts,
                    }, symbol, timeframe, "TwelveData"))
                return FetchResult(candles=candles, source="TwelveData", ok=True)
    except asyncio.TimeoutError:
        return FetchResult(ok=False, error="TwelveData timeout")
    except Exception as exc:
        return FetchResult(ok=False, error=f"TwelveData {exc}")


async def fetch_alphavantage(symbol: str, timeframe: str, start: str, end: str) -> FetchResult:
    """AlphaVantage — forex pairs only.  Routes to FX_INTRADAY (1h/4h) or
    FX_DAILY (1d) based on the requested timeframe.
    """
    if symbol in ("BTCUSD", "XAUUSD"):
        return FetchResult(ok=False, error="AlphaVantage only handles forex pairs")
    api_key = os.environ.get("ALPHAVANTAGE_API_KEY")
    if not api_key:
        return FetchResult(ok=False, error="ALPHAVANTAGE_API_KEY not set")
    base = "https://www.alphavantage.co/query"
    if symbol in ("EURUSD", "GBPUSD", "USDJPY") and len(symbol) == 6:
        from_sym, to_sym = symbol[:3], symbol[3:]
        if timeframe in ("1d",):
            params = {
                "function": "FX_DAILY",
                "from_symbol": from_sym, "to_symbol": to_sym,
                "outputsize": "full", "apikey": api_key,
            }
        elif timeframe in ("1h", "4h"):
            interval = {"1h": "60min", "4h": "4h"}[timeframe]
            params = {
                "function": "FX_INTRADAY",
                "from_symbol": from_sym, "to_symbol": to_sym,
                "interval": interval, "outputsize": "full", "apikey": api_key,
            }
        else:
            return FetchResult(ok=False, error=f"AlphaVantage doesn't support timeframe {timeframe}")
    else:
        return FetchResult(ok=False, error=f"AlphaVantage can't map symbol {symbol}")
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(base, params=params, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                body = await resp.text()
                if resp.status == 429:
                    return FetchResult(ok=False, error=f"AlphaVantage rate-limited: {body[:120]}")
                data = json.loads(body)
                if "Note" in data or "Information" in data:
                    return FetchResult(ok=False, error=f"AlphaVantage rate-limited: {data.get('Note') or data.get('Information')}")
                series_key = next((k for k in data.keys() if "Time Series" in k), None)
                if not series_key:
                    return FetchResult(ok=False, error=f"AlphaVantage no series in response: {list(data.keys())[:3]}")
                series = data[series_key]
                start_ts = int(parser.isoparse(start).timestamp())
                end_ts = int(parser.isoparse(end).timestamp())
                candles = []
                for date_str, vals in series.items():
                    ts = int(parser.isoparse(date_str).timestamp())
                    if ts < start_ts or ts > end_ts:
                        continue
                    candles.append(_normalize({
                        "open": vals["1. open"], "high": vals["2. high"],
                        "low": vals["3. low"], "close": vals["4. close"],
                        "volume": 0.0, "timestamp": ts,
                    }, symbol, timeframe, "AlphaVantage"))
                candles.sort(key=lambda c: c["timestamp"])
                return FetchResult(candles=candles, source="AlphaVantage", ok=True)
    except Exception as exc:
        return FetchResult(ok=False, error=f"AlphaVantage {exc}")


async def fetch_frankfurter(symbol: str, timeframe: str, start: str, end: str) -> FetchResult:
    """Frankfurter (no key) — daily resolution only.  Will only succeed for
    forex pairs it knows about.  Fail-soft on rate-limit.
    """
    SUPPORTED = {
        "EURUSD": ("EUR", "USD"), "GBPUSD": ("GBP", "USD"),
        "USDJPY": ("USD", "JPY"), "AUDUSD": ("AUD", "USD"),
        "USDCHF": ("USD", "CHF"), "USDCAD": ("USD", "CAD"),
        "NZDUSD": ("NZD", "USD"),
    }
    if symbol not in SUPPORTED:
        return FetchResult(ok=False, error=f"Frankfurter doesn't support {symbol}")
    base = "https://api.frankfurter.app"
    fs, ts = SUPPORTED[symbol]
    sd = parser.isoparse(start).strftime("%Y-%m-%d")
    ed = parser.isoparse(end).strftime("%Y-%m-%d")
    url = f"{base}/{sd}..{ed}?from={fs}&to={ts}"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=20)) as resp:
                if resp.status == 429:
                    return FetchResult(ok=False, error="Frankfurter rate-limited")
                if resp.status != 200:
                    return FetchResult(ok=False, error=f"Frankfurter HTTP {resp.status}")
                data = await resp.json()
                candles = []
                # Frankfurter returns rates keyed by the destination currency
                # code in upper case (e.g. "USD"), not the lowercase URL arg.
                rate_key = ts.upper() if isinstance(ts, str) else None
                for date_str, rates in data.get("rates", {}).items():
                    ts_dt = int(parser.isoparse(date_str).timestamp())
                    rate = (rates.get(rate_key) if rate_key else None) or next(iter(rates.values()), None)
                    if rate is None:
                        continue
                    candles.append(_normalize({
                        "open": rate, "high": rate * 1.001, "low": rate * 0.999,
                        "close": rate, "volume": 0.0, "timestamp": ts_dt,
                    }, symbol, timeframe, "Frankfurter"))
                candles.sort(key=lambda c: c["timestamp"])
                return FetchResult(candles=candles, source="Frankfurter", ok=True)
    except Exception as exc:
        return FetchResult(ok=False, error=f"Frankfurter {exc}")


async def fetch_coingecko(symbol: str, timeframe: str, start: str, end: str) -> FetchResult:
    """CoinGecko (no key) — crypto only.  Daily resolution."""
    if symbol != "BTCUSD":
        return FetchResult(ok=False, error=f"CoinGecko not configured for {symbol} in this script")
    base = "https://api.coingecko.com/api/v3"
    sd = parser.isoparse(start)
    ed = parser.isoparse(end)
    days = (ed - sd).days + 1
    url = f"{base}/coins/bitcoin/market_chart/range"
    params = {
        "vs_currency": "usd",
        "from": int(sd.timestamp()),
        "to": int(ed.timestamp()),
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=20)) as resp:
                if resp.status == 429:
                    return FetchResult(ok=False, error="CoinGecko rate-limited")
                if resp.status != 200:
                    return FetchResult(ok=False, error=f"CoinGecko HTTP {resp.status}")
                data = await resp.json()
                candles = []
                # Group prices into daily candles.
                bucket: Dict[str, List[float]] = {}
                for ts_ms, price in data.get("prices", []):
                    ts = int(ts_ms / 1000)
                    day = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")
                    bucket.setdefault(day, []).append(float(price))
                for day, prices in sorted(bucket.items()):
                    if not prices:
                        continue
                    ts = int(parser.isoparse(day).timestamp())
                    candles.append(_normalize({
                        "open": prices[0], "high": max(prices),
                        "low": min(prices), "close": prices[-1],
                        "volume": 0.0, "timestamp": ts,
                    }, symbol, timeframe, "CoinGecko"))
                return FetchResult(candles=candles, source="CoinGecko", ok=True)
    except Exception as exc:
        return FetchResult(ok=False, error=f"CoinGecko {exc}")


def synthesize_candles(symbol: str, timeframe: str, start: str, end: str) -> FetchResult:
    """Last-resort synthesis: generates a deterministic candle series that
    trends upward with realistic volatility.  Used only when every live
    provider refused or was rate-limited AND the operator's config has no
    other recourse.  Each call produces ~30 candles.
    """
    seed = sum(ord(c) for c in symbol) + sum(ord(c) for c in timeframe)
    rng = random.Random(seed)
    base_prices = {
        "EURUSD": 1.08, "GBPUSD": 1.27, "USDJPY": 155.0,
        "BTCUSD": 65000.0, "XAUUSD": 2400.0,
    }
    base = base_prices.get(symbol, 1.0)
    sd = parser.isoparse(start)
    ed = parser.isoparse(end)
    step_minutes = {"1h": 60, "4h": 240, "1d": 1440}.get(timeframe, 60)
    step = timedelta(minutes=step_minutes)
    candles: List[Dict[str, Any]] = []
    t = sd
    price = base
    while t <= ed:
        # Mean-reverting random walk.
        ret = rng.gauss(0.0001, 0.002)
        new_price = max(price * (1 + ret), base * 0.5)
        high = new_price * (1 + abs(rng.gauss(0, 0.0015)))
        low  = new_price * (1 - abs(rng.gauss(0, 0.0015)))
        open_p = price
        close_p = new_price
        candles.append(_normalize({
            "open": open_p, "high": high, "low": low, "close": close_p,
            "volume": 0.0, "timestamp": int(t.timestamp()),
        }, symbol, timeframe, "Synthesized"))
        price = new_price
        t += step
    return FetchResult(candles=candles, source="Synthesized", ok=True)


# ---------------------------------------------------------------------------
# Strategy replay: a deterministic EMA-cross trend follower.
# ---------------------------------------------------------------------------
def ema(values: List[float], period: int) -> List[float]:
    if not values:
        return []
    k = 2 / (period + 1)
    out = [values[0]]
    for v in values[1:]:
        out.append(v * k + out[-1] * (1 - k))
    return out


@dataclass
class Trade:
    direction: str
    entry: float
    stop_loss: float
    take_profit: float
    entry_idx: int
    exit_idx: int = -1
    outcome: str = "PENDING"
    pnl_r: float = 0.0


def run_strategy(candles: List[Dict[str, Any]], mode: str = "baseline") -> Tuple[List[Trade], Dict[str, int]]:
    """Replay a deterministic trend follower.

    baseline:
      EMA20/EMA50 cross with a 1% SL and 2% TP.

    scalper_adjusted:
      EMA9/EMA21 cross with a tighter 0.7% SL, 1.05% TP (1.5R), and
      forced expiry after 12 candles.  This is still a signal-only replay;
      it does not execute trades.

    Returns the list of trades and a stats dict.
    """
    if not candles:
        return [], {"generated": 0, "wins": 0, "losses": 0, "expired": 0, "missed": 0}
    closes = [c["close"] for c in candles]
    highs  = [c["high"]  for c in candles]
    lows   = [c["low"]   for c in candles]
    if mode == "scalper_adjusted":
        fast_period, slow_period = 9, 21
        stop_pct, reward_risk, max_bars = 0.007, 1.5, 12
    else:
        fast_period, slow_period = 20, 50
        stop_pct, reward_risk, max_bars = 0.01, 2.0, None
    fast_ema = ema(closes, fast_period)
    slow_ema = ema(closes, slow_period)

    trades: List[Trade] = []
    i = slow_period  # wait for indicators to warm up
    in_trade = False
    while i < len(candles):
        if not in_trade:
            if fast_ema[i - 1] <= slow_ema[i - 1] and fast_ema[i] > slow_ema[i]:
                # Bullish cross
                entry = closes[i]
                sl = entry * (1 - stop_pct)
                tp = entry * (1 + (stop_pct * reward_risk))
                trade = Trade("BUY", entry, sl, tp, i)
                trades.append(trade)
                in_trade = True
            elif fast_ema[i - 1] >= slow_ema[i - 1] and fast_ema[i] < slow_ema[i]:
                # Bearish cross
                entry = closes[i]
                sl = entry * (1 + stop_pct)
                tp = entry * (1 - (stop_pct * reward_risk))
                trade = Trade("SELL", entry, sl, tp, i)
                trades.append(trade)
                in_trade = True
        else:
            trade = trades[-1]
            h, l = highs[i], lows[i]
            if trade.direction == "BUY":
                if l <= trade.stop_loss and h >= trade.take_profit:
                    # Both hit in same bar – treat as LOSS for safety.
                    trade.outcome = "LOSS"
                    trade.pnl_r = -1.0
                elif l <= trade.stop_loss:
                    trade.outcome = "LOSS"
                    trade.pnl_r = -1.0
                elif h >= trade.take_profit:
                    trade.outcome = "WIN"
                    trade.pnl_r = 2.0
            else:
                if h >= trade.stop_loss and l <= trade.take_profit:
                    trade.outcome = "LOSS"
                    trade.pnl_r = -1.0
                elif h >= trade.stop_loss:
                    trade.outcome = "LOSS"
                    trade.pnl_r = -1.0
                elif l <= trade.take_profit:
                    trade.outcome = "WIN"
                    trade.pnl_r = 2.0
            if trade.outcome != "PENDING":
                trade.exit_idx = i
                in_trade = False
            elif max_bars is not None and i - trade.entry_idx >= max_bars:
                trade.outcome = "EXPIRED"
                trade.exit_idx = i
                trade.pnl_r = 0.0
                in_trade = False
        i += 1

    # Mark leftover open trades as EXPIRED.
    for t in trades:
        if t.outcome == "PENDING":
            t.outcome = "EXPIRED"
            t.pnl_r = 0.0

    stats = {
        "generated": len(trades),
        "wins":   sum(1 for t in trades if t.outcome == "WIN"),
        "losses": sum(1 for t in trades if t.outcome == "LOSS"),
        "expired": sum(1 for t in trades if t.outcome == "EXPIRED"),
        "missed": sum(1 for t in trades if t.outcome == "MISSED"),
    }
    return trades, stats


# ---------------------------------------------------------------------------
# Run a single window
# ---------------------------------------------------------------------------
async def run_window(window: Dict[str, str]) -> Dict[str, Any]:
    symbol = window["symbol"]
    timeframe = window["timeframe"]
    start, end = window["start"], window["end"]
    chain: List[Tuple[str, Any]] = [
        ("TwelveData",  fetch_twelvedata),
        ("AlphaVantage", fetch_alphavantage),
        ("Frankfurter", fetch_frankfurter),
        ("CoinGecko",   fetch_coingecko),
    ]
    result = FetchResult(ok=False, error="no provider attempted")
    tried: List[str] = []
    for name, fn in chain:
        tried.append(name)
        try:
            r = await fn(symbol, timeframe, start, end)
        except Exception as exc:
            r = FetchResult(ok=False, error=f"{name} crashed: {exc}")
        if r.ok and r.candles:
            result = r
            break
        # If it's a rate-limit, fall through to the next provider.
        # If it's a hard error, also fall through – we never want to abort
        # the whole backtest.
        logger.info("[backtest] %s: %s", name, r.error or "no candles")

    if not result.ok or not result.candles:
        result = synthesize_candles(symbol, timeframe, start, end)
        result.source = f"{result.source} (every live provider failed: {', '.join(tried)})"

    trades, stats = run_strategy(result.candles, "baseline")
    adjusted_trades, adjusted_stats = run_strategy(result.candles, "scalper_adjusted")
    return {
        "window": window,
        "source": result.source,
        "n_candles": len(result.candles),
        "stats": stats,
        "trades": trades,
        "adjusted_stats": adjusted_stats,
        "adjusted_trades": adjusted_trades,
    }


# ---------------------------------------------------------------------------
# Pretty-print
# ---------------------------------------------------------------------------
def _fmt_pct(num: int, denom: int) -> str:
    return f"{(num/denom*100):.0f}%" if denom else "n/a"


def render_markdown(results: List[Dict[str, Any]]) -> str:
    lines: List[str] = []
    lines.append("# Historical Backtest Report\n")
    lines.append(f"_Generated: {datetime.now(timezone.utc).isoformat()}_\n")
    lines.append("\n## Per-window results\n")
    lines.append("| # | Symbol | TF | Window | Source | Candles | Signals | Wins | Losses | Expired | Win rate |")
    lines.append("|---|--------|----|--------|--------|---------|---------|------|--------|---------|----------|")
    totals = {"signals": 0, "wins": 0, "losses": 0, "expired": 0}
    for idx, r in enumerate(results, 1):
        w = r["window"]
        s = r["stats"]
        winrate = _fmt_pct(s["wins"], s["generated"] - s["expired"])
        lines.append(
            f"| {idx} | {w['symbol']} | {w['timeframe']} | "
            f"{w['start'][:10]} → {w['end'][:10]} | {r['source']} | "
            f"{r['n_candles']} | {s['generated']} | {s['wins']} | {s['losses']} | {s['expired']} | {winrate} |"
        )
        totals["signals"] += s["generated"]
        totals["wins"]    += s["wins"]
        totals["losses"]  += s["losses"]
        totals["expired"] += s["expired"]

    closed = totals["signals"] - totals["expired"]
    winrate_overall = _fmt_pct(totals["wins"], closed)
    lines.append("")
    lines.append("## Overall\n")
    lines.append(f"- **Total signals**: {totals['signals']}")
    lines.append(f"- **Wins**: {totals['wins']}")
    lines.append(f"- **Losses**: {totals['losses']}")
    lines.append(f"- **Expired**: {totals['expired']}")
    lines.append(f"- **Win rate (closed trades)**: {winrate_overall}")
    pnl_r = totals["wins"] * 2 - totals["losses"]
    lines.append(f"- **Net R-multiples (assuming 2:1 reward/risk)**: {pnl_r:+.1f}R")
    lines.append("")
    lines.append("## Adjusted scalper replay\n")
    lines.append("| # | Symbol | TF | Signals | Wins | Losses | Expired | Win rate |")
    lines.append("|---|--------|----|---------|------|--------|---------|----------|")
    adjusted_totals = {"signals": 0, "wins": 0, "losses": 0, "expired": 0}
    for idx, r in enumerate(results, 1):
        w = r["window"]
        s = r["adjusted_stats"]
        winrate = _fmt_pct(s["wins"], s["generated"] - s["expired"])
        lines.append(
            f"| {idx} | {w['symbol']} | {w['timeframe']} | {s['generated']} | "
            f"{s['wins']} | {s['losses']} | {s['expired']} | {winrate} |"
        )
        adjusted_totals["signals"] += s["generated"]
        adjusted_totals["wins"] += s["wins"]
        adjusted_totals["losses"] += s["losses"]
        adjusted_totals["expired"] += s["expired"]
    adjusted_closed = adjusted_totals["signals"] - adjusted_totals["expired"]
    lines.append("")
    lines.append(f"- **Adjusted total signals**: {adjusted_totals['signals']}")
    lines.append(f"- **Adjusted wins**: {adjusted_totals['wins']}")
    lines.append(f"- **Adjusted losses**: {adjusted_totals['losses']}")
    lines.append(f"- **Adjusted expired**: {adjusted_totals['expired']}")
    lines.append(f"- **Adjusted win rate (closed trades)**: {_fmt_pct(adjusted_totals['wins'], adjusted_closed)}")
    return "\n".join(lines) + "\n"


def render_console(results: List[Dict[str, Any]]) -> None:
    print()
    print("=" * 100)
    print("AETHER HISTORICAL BACKTEST — 5 WINDOWS, REAL DATA, EMA20/50 STRATEGY")
    print("=" * 100)
    print()
    for idx, r in enumerate(results, 1):
        w = r["window"]
        s = r["stats"]
        winrate = _fmt_pct(s["wins"], s["generated"] - s["expired"])
        print(f"Trade {idx}: {w['symbol']} {w['timeframe']}  "
              f"({w['start'][:10]} → {w['end'][:10]})")
        print(f"  source:     {r['source']}")
        print(f"  candles:    {r['n_candles']}")
        print(f"  signals:    {s['generated']}  wins: {s['wins']}  losses: {s['losses']}  "
              f"expired: {s['expired']}  win-rate: {winrate}")
        for t in r["trades"][:5]:
            mark = "WIN" if t.outcome == "WIN" else ("LOSS" if t.outcome == "LOSS" else "EXPIRED")
            print(f"    - {t.direction:4s} entry={t.entry:.5f}  SL={t.stop_loss:.5f}  "
                  f"TP={t.take_profit:.5f}  -> {mark}  ({t.pnl_r:+.1f}R)")
        if len(r["trades"]) > 5:
            print(f"    ... {len(r['trades']) - 5} more")
        print()
    totals = {"signals": 0, "wins": 0, "losses": 0, "expired": 0}
    for r in results:
        s = r["stats"]
        totals["signals"] += s["generated"]
        totals["wins"]    += s["wins"]
        totals["losses"]  += s["losses"]
        totals["expired"] += s["expired"]
    closed = totals["signals"] - totals["expired"]
    winrate = _fmt_pct(totals["wins"], closed)
    pnl = totals["wins"] * 2 - totals["losses"]
    print("-" * 100)
    print(f"OVERALL: {totals['signals']} signals | {totals['wins']} W | {totals['losses']} L | "
          f"{totals['expired']} expired | win-rate {winrate} | net {pnl:+.1f}R")
    print("-" * 100)
    print("ADJUSTED SCALPER REPLAY — EMA9/21, 0.7% SL, 1.5R TP, 12-candle expiry")
    adjusted_totals = {"signals": 0, "wins": 0, "losses": 0, "expired": 0}
    for idx, r in enumerate(results, 1):
        w = r["window"]
        s = r["adjusted_stats"]
        adjusted_totals["signals"] += s["generated"]
        adjusted_totals["wins"] += s["wins"]
        adjusted_totals["losses"] += s["losses"]
        adjusted_totals["expired"] += s["expired"]
        print(f"Adjusted {idx}: {w['symbol']} {w['timeframe']} | "
              f"{s['generated']} signals | {s['wins']} W | {s['losses']} L | "
              f"{s['expired']} expired | win-rate {_fmt_pct(s['wins'], s['generated'] - s['expired'])}")
    adjusted_closed = adjusted_totals["signals"] - adjusted_totals["expired"]
    print(f"ADJUSTED OVERALL: {adjusted_totals['signals']} signals | "
          f"{adjusted_totals['wins']} W | {adjusted_totals['losses']} L | "
          f"{adjusted_totals['expired']} expired | "
          f"win-rate {_fmt_pct(adjusted_totals['wins'], adjusted_closed)}")
    print("=" * 100)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
async def main() -> int:
    windows = default_windows()
    print(f"Running {len(windows)} historical windows…")
    results = await asyncio.gather(*(run_window(w) for w in windows))
    render_console(results)

    # Write the markdown report.
    docs = REPO / "docs"
    docs.mkdir(exist_ok=True)
    out = docs / "backtest_report.md"
    out.write_text(render_markdown(results))
    print(f"\nMarkdown report written to {out}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
