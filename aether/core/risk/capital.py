from pydantic import BaseModel
from typing import Optional
from aether.core.utils.logger import logger

class RiskProfile(BaseModel):
    account_size: float
    risk_per_trade_percent: float
    max_daily_risk_percent: float
    max_open_positions: int
    max_portfolio_heat: float = 5.0 # 5%

class CapitalManager:
    """
    Provides risk guidance and position sizing.
    """
    def __init__(self, profile: RiskProfile):
        self.profile = profile
        self.current_daily_risk = 0.0
        self.active_positions_count = 0

    def calculate_position_size(self, stop_distance_pips: float) -> Optional[float]:
        """
        Calculates suggested units based on risk per trade.
        """
        if stop_distance_pips <= 0:
            return None

        risk_amount = self.profile.account_size * (self.profile.risk_per_trade_percent / 100.0)
        # suggested_units = risk_amount / stop_distance_pips
        # This is a simplification. Actual pips-to-units depends on asset.
        # For Forex, 1 pip is usually 0.0001.
        units = risk_amount / (stop_distance_pips * 0.0001)
        return units

    def get_portfolio_heat(self, active_signals_risk: float) -> float:
        """
        Returns current portfolio heat as a percentage.
        """
        return (active_signals_risk / self.profile.account_size) * 100.0

    def can_open_position(self, current_heat: float) -> bool:
        if self.active_positions_count >= self.profile.max_open_positions:
            logger.warn("risk_max_positions_reached")
            return False
        if current_heat > self.profile.max_portfolio_heat:
            logger.warn("risk_max_heat_reached", current_heat=current_heat)
            return False
        return True
