"""Paper Trading Broker Adapter – simulated execution for testing/validation."""

import asyncio
import json
import time
import uuid
from decimal import Decimal
from typing import Dict, Any, Optional, List
from datetime import datetime, timezone
from pathlib import Path

from aether.core.adapters.registry import AdapterProtocol
from aether.core.utils.logger import logger


MANIFEST = {
    "name": "paper_broker",
    "type": "both",
    "config_schema": {
        "type": "object",
        "properties": {
            "account_size": {"type": "number", "default": 10000},
            "leverage": {"type": "number", "default": 1},
            "commission_per_lot": {"type": "number", "default": 0.0},
            "spread_markup_pips": {"type": "number", "default": 0.0},
            "slippage_pips": {"type": "number", "default": 0.0},
            "data_dir": {"type": "string", "default": "data/paper_broker"}
        },
        "required": []
    }
}


class PaperBrokerAdapter(AdapterProtocol):
    """
    Paper trading broker that simulates order execution.
    Maintains virtual account state: balance, equity, positions, orders.
    """

    MANIFEST = MANIFEST

    def __init__(self):
        self.config: Dict[str, Any] = {}
        self.data_dir: Optional[Path] = None
        
        # Account state
        self.account_size: float = 10000.0
        self.balance: float = 10000.0
        self.equity: float = 10000.0
        self.leverage: float = 1.0
        self.commission_per_lot: float = 0.0
        self.spread_markup_pips: float = 0.0
        self.slippage_pips: float = 0.0
        
        # Positions and orders
        self.positions: Dict[str, Dict[str, Any]] = {}
        self.orders: Dict[str, Dict[str, Any]] = {}
        self.trade_history: List[Dict[str, Any]] = []
        
        # Price feed (symbol -> {bid, ask, timestamp})
        self.price_feed: Dict[str, Dict[str, Any]] = {}
        
        # Persistence
        self._state_file: Optional[Path] = None
        self._running = False
        self._update_task: Optional[asyncio.Task] = None

    # -----------------------------------------------------------------
    # AdapterProtocol interface
    # -----------------------------------------------------------------
    async def validate(self) -> bool:
        """Validate the adapter configuration."""
        if self.account_size <= 0:
            logger.warning("[PaperBroker] invalid account_size")
            return False
        logger.info("[PaperBroker] validation successful")
        return True

    async def send_signal(self, signal: Dict[str, Any]) -> bool:
        """Send a signal notification (not execution). Delegates to Telegram/etc."""
        return False  # Paper broker doesn't send notifications

    async def receive_message(self, message: Dict[str, Any]) -> None:
        """Handle inbound messages (not used for broker)."""
        pass

    async def close(self) -> None:
        """Shutdown and persist state."""
        self._running = False
        if self._update_task:
            self._update_task.cancel()
            try:
                await self._update_task
            except asyncio.CancelledError:
                pass
        await self._persist_state()
        logger.info("[PaperBroker] closed")

    # -----------------------------------------------------------------
    # Broker-specific methods
    # -----------------------------------------------------------------
    def configure(self, config: Dict[str, Any]) -> None:
        """Configure the adapter from ConfigStore."""
        self.config = config
        self.account_size = config.get("account_size", 10000.0)
        self.balance = self.account_size
        self.equity = self.account_size
        self.leverage = config.get("leverage", 1.0)
        self.commission_per_lot = config.get("commission_per_lot", 0.0)
        self.spread_markup_pips = config.get("spread_markup_pips", 0.0)
        self.slippage_pips = config.get("slippage_pips", 0.0)
        
        data_dir = config.get("data_dir", "data/paper_broker")
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._state_file = self.data_dir / "state.json"
        
        # Load persisted state
        self._load_state()
        logger.info("[PaperBroker] configured", account_size=self.account_size)

    async def start(self) -> None:
        """Start the price update loop."""
        self._running = True
        self._update_task = asyncio.create_task(self._update_loop())
        logger.info("[PaperBroker] started")

    # -----------------------------------------------------------------
    # Trading API
    # -----------------------------------------------------------------
    async def execute_order(self, order_request: Dict[str, Any]) -> Dict[str, Any]:
        """
        Execute a market or limit order.
        
        Args:
            order_request: {
                "symbol": "EURUSD",
                "direction": "BUY" | "SELL",
                "volume": 0.1,  # lots
                "order_type": "MARKET" | "LIMIT" | "STOP",
                "price": 1.0850,  # required for LIMIT/STOP
                "sl": 1.0800,
                "tp": 1.0900,
                "comment": "strategy_name",
                "magic": 12345
            }
        
        Returns:
            {"success": bool, "order_id": str, "position_id": str, "fill_price": float, "error": str}
        """
        try:
            symbol = order_request.get("symbol")
            direction = order_request.get("direction", "").upper()
            volume = float(order_request.get("volume", 0))
            order_type = order_request.get("order_type", "MARKET").upper()
            price = order_request.get("price")
            sl = order_request.get("sl")
            tp = order_request.get("tp")
            comment = order_request.get("comment", "")
            magic = order_request.get("magic", 0)
            
            if not symbol or not direction or volume <= 0:
                return {"success": False, "error": "Invalid order parameters"}
            
            # Get current price
            current_price = self._get_current_price(symbol, direction)
            if current_price is None:
                return {"success": False, "error": f"No price feed for {symbol}"}
            
            # Determine fill price based on order type
            if order_type == "MARKET":
                fill_price = self._apply_slippage(current_price, direction)
            elif order_type in ("LIMIT", "STOP"):
                if price is None:
                    return {"success": False, "error": "Price required for LIMIT/STOP orders"}
                fill_price = price
                # Check if limit/stop price is reachable
                if order_type == "LIMIT":
                    if direction == "BUY" and fill_price > current_price["ask"]:
                        return {"success": False, "error": "Limit price above market", "pending": True}
                    if direction == "SELL" and fill_price < current_price["bid"]:
                        return {"success": False, "error": "Limit price below market", "pending": True}
                elif order_type == "STOP":
                    if direction == "BUY" and fill_price < current_price["ask"]:
                        return {"success": False, "error": "Stop price below market", "pending": True}
                    if direction == "SELL" and fill_price > current_price["bid"]:
                        return {"success": False, "error": "Stop price above market", "pending": True}
            else:
                return {"success": False, "error": f"Unknown order type: {order_type}"}
            
            # Calculate margin required
            margin_required = self._calculate_margin(symbol, volume, fill_price)
            if margin_required > self.equity * 0.95:  # 5% buffer
                return {"success": False, "error": "Insufficient margin"}
            
            # Apply commission
            commission = self.commission_per_lot * volume
            
            # Create position
            position_id = str(uuid.uuid4())[:8]
            position = {
                "position_id": position_id,
                "symbol": symbol,
                "direction": direction,
                "volume": volume,
                "entry_price": fill_price,
                "sl": sl,
                "tp": tp,
                "comment": comment,
                "magic": magic,
                "opened_at": datetime.now(timezone.utc).isoformat(),
                "commission": commission,
                "swap": 0.0,
                "profit": 0.0,
                "current_price": fill_price,
            }
            
            self.positions[position_id] = position
            self.balance -= commission
            self._update_equity()
            
            # Record trade
            trade_record = {
                "trade_id": str(uuid.uuid4())[:8],
                "position_id": position_id,
                "symbol": symbol,
                "direction": direction,
                "volume": volume,
                "price": fill_price,
                "type": "OPEN",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "commission": commission,
            }
            self.trade_history.append(trade_record)
            
            await self._persist_state()
            
            logger.info("[PaperBroker] position opened", position_id=position_id, symbol=symbol, 
                       direction=direction, volume=volume, price=fill_price)
            
            return {
                "success": True,
                "order_id": str(uuid.uuid4())[:8],
                "position_id": position_id,
                "fill_price": fill_price,
                "commission": commission
            }
            
        except Exception as e:
            logger.exception("[PaperBroker] execute_order error: %s", e)
            return {"success": False, "error": str(e)}

    async def close_position(self, position_id: str, volume: Optional[float] = None) -> Dict[str, Any]:
        """Close an open position (full or partial)."""
        position = self.positions.get(position_id)
        if not position:
            return {"success": False, "error": "Position not found"}
        
        close_volume = volume or position["volume"]
        if close_volume > position["volume"]:
            return {"success": False, "error": "Volume exceeds position size"}
        
        symbol = position["symbol"]
        direction = "SELL" if position["direction"] == "BUY" else "BUY"
        current_price = self._get_current_price(symbol, direction)
        if current_price is None:
            return {"success": False, "error": f"No price feed for {symbol}"}
        
        fill_price = self._apply_slippage(current_price, direction)
        commission = self.commission_per_lot * close_volume
        
        # Calculate profit
        entry = position["entry_price"]
        if position["direction"] == "BUY":
            profit = (fill_price - entry) * close_volume * 100000  # 1 lot = 100k units
        else:
            profit = (entry - fill_price) * close_volume * 100000
        
        profit -= commission
        
        # Update position
        if close_volume == position["volume"]:
            # Full close
            del self.positions[position_id]
        else:
            # Partial close
            position["volume"] -= close_volume
            position["commission"] += commission
        
        self.balance += profit - commission
        self._update_equity()
        
        # Record trade
        trade_record = {
            "trade_id": str(uuid.uuid4())[:8],
            "position_id": position_id,
            "symbol": symbol,
            "direction": direction,
            "volume": close_volume,
            "price": fill_price,
            "type": "CLOSE",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "commission": commission,
            "profit": profit,
        }
        self.trade_history.append(trade_record)
        
        await self._persist_state()
        
        logger.info("[PaperBroker] position closed", position_id=position_id, 
                   profit=profit, fill_price=fill_price)
        
        return {
            "success": True,
            "fill_price": fill_price,
            "profit": profit,
            "commission": commission
        }

    async def modify_position(self, position_id: str, sl: Optional[float] = None, 
                             tp: Optional[float] = None) -> Dict[str, Any]:
        """Modify SL/TP of an open position."""
        position = self.positions.get(position_id)
        if not position:
            return {"success": False, "error": "Position not found"}
        
        if sl is not None:
            position["sl"] = sl
        if tp is not None:
            position["tp"] = tp
        
        await self._persist_state()
        return {"success": True, "sl": position.get("sl"), "tp": position.get("tp")}

    def get_positions(self) -> List[Dict[str, Any]]:
        """Get all open positions with current PnL."""
        self._update_position_pnl()
        return list(self.positions.values())

    def get_account(self) -> Dict[str, Any]:
        """Get account summary."""
        self._update_equity()
        return {
            "balance": round(self.balance, 2),
            "equity": round(self.equity, 2),
            "margin": round(self._calculate_used_margin(), 2),
            "free_margin": round(self.equity - self._calculate_used_margin(), 2),
            "margin_level": round((self.equity / self._calculate_used_margin() * 100) 
                                  if self._calculate_used_margin() > 0 else 0, 2),
            "open_positions": len(self.positions),
            "leverage": self.leverage,
        }

    def get_trade_history(self, limit: int = 100) -> List[Dict[str, Any]]:
        """Get recent trade history."""
        return self.trade_history[-limit:]

    def update_price(self, symbol: str, bid: float, ask: float, timestamp: Optional[int] = None) -> None:
        """Update price feed from external source (called by data engine)."""
        self.price_feed[symbol] = {
            "bid": bid,
            "ask": ask,
            "timestamp": timestamp or int(time.time()),
        }
        # Update position PnL immediately
        self._update_position_pnl()

    # -----------------------------------------------------------------
    # Internal helpers
    # -----------------------------------------------------------------
    def _get_current_price(self, symbol: str, direction: str) -> Optional[Dict[str, float]]:
        """Get current bid/ask for symbol."""
        feed = self.price_feed.get(symbol)
        if not feed:
            return None
        return {"bid": feed["bid"], "ask": feed["ask"]}

    def _apply_slippage(self, price: Dict[str, float], direction: str) -> float:
        """Apply slippage and spread markup."""
        slippage = self.slippage_pips * 0.0001
        markup = self.spread_markup_pips * 0.0001
        
        if direction == "BUY":
            return price["ask"] + slippage + markup
        else:
            return price["bid"] - slippage - markup

    def _calculate_margin(self, symbol: str, volume: float, price: float) -> float:
        """Calculate required margin for a position."""
        # Simplified: 1 lot = 100,000 units, margin = notional / leverage
        notional = volume * 100000 * price
        return notional / self.leverage

    def _calculate_used_margin(self) -> float:
        """Calculate total used margin for all open positions."""
        total = 0.0
        for pos in self.positions.values():
            total += self._calculate_margin(pos["symbol"], pos["volume"], pos["entry_price"])
        return total

    def _update_position_pnl(self) -> None:
        """Update unrealized PnL for all positions."""
        for position in self.positions.values():
            symbol = position["symbol"]
            feed = self.price_feed.get(symbol)
            if not feed:
                continue
            
            current_bid = feed["bid"]
            current_ask = feed["ask"]
            
            if position["direction"] == "BUY":
                position["current_price"] = current_bid
                position["profit"] = (current_bid - position["entry_price"]) * position["volume"] * 100000
            else:
                position["current_price"] = current_ask
                position["profit"] = (position["entry_price"] - current_ask) * position["volume"] * 100000
            
            # Check SL/TP
            self._check_sl_tp(position, current_bid, current_ask)

    def _check_sl_tp(self, position: Dict[str, Any], bid: float, ask: float) -> None:
        """Check if SL or TP hit - would trigger in real broker."""
        direction = position["direction"]
        sl = position.get("sl")
        tp = position.get("tp")
        
        if direction == "BUY":
            if sl and bid <= sl:
                # SL hit - in real broker this would auto-close
                logger.warning("[PaperBroker] SL hit", position_id=position["position_id"])
            if tp and bid >= tp:
                logger.warning("[PaperBroker] TP hit", position_id=position["position_id"])
        else:
            if sl and ask >= sl:
                logger.warning("[PaperBroker] SL hit", position_id=position["position_id"])
            if tp and ask <= tp:
                logger.warning("[PaperBroker] TP hit", position_id=position["position_id"])

    def _update_equity(self) -> None:
        """Recalculate equity = balance + unrealized PnL."""
        unrealized = sum(p.get("profit", 0) for p in self.positions.values())
        self.equity = self.balance + unrealized

    # -----------------------------------------------------------------
    # Persistence
    # -----------------------------------------------------------------
    def _load_state(self) -> None:
        """Load state from disk."""
        if not self._state_file or not self._state_file.exists():
            return
        try:
            with open(self._state_file, 'r') as f:
                state = json.load(f)
            self.balance = state.get("balance", self.account_size)
            self.positions = state.get("positions", {})
            self.trade_history = state.get("trade_history", [])
            self.orders = state.get("orders", {})
            self._update_equity()
            logger.info("[PaperBroker] state loaded", balance=self.balance, 
                       positions=len(self.positions))
        except Exception as e:
            logger.exception("[PaperBroker] load state error: %s", e)

    async def _persist_state(self) -> None:
        """Persist state to disk."""
        if not self._state_file:
            return
        try:
            state = {
                "balance": self.balance,
                "equity": self.equity,
                "positions": self.positions,
                "trade_history": self.trade_history[-1000:],  # Keep last 1000
                "orders": self.orders,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
            # Atomic write
            tmp = self._state_file.with_suffix(".tmp")
            with open(tmp, 'w') as f:
                json.dump(state, f, indent=2)
            tmp.replace(self._state_file)
        except Exception as e:
            logger.exception("[PaperBroker] persist state error: %s", e)

    async def _update_loop(self) -> None:
        """Periodic state persistence and equity updates."""
        while self._running:
            try:
                self._update_equity()
                await self._persist_state()
                await asyncio.sleep(30)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.exception("[PaperBroker] update loop error: %s", e)
                await asyncio.sleep(5)

    # -----------------------------------------------------------------
    # Health check
    # -----------------------------------------------------------------
    async def health(self) -> Dict[str, Any]:
        self._update_equity()
        return {
            "status": "OK" if self._running else "STOPPED",
            "component": "paper_broker",
            "account": self.get_account(),
            "open_positions": len(self.positions),
        }


PaperBrokerAdapter._event_bus = None  # type: ignore