from pydantic import BaseModel
from typing import Optional, Dict

class NewsRisk(BaseModel):
    active: bool
    impact: Optional[str] = None # "low", "medium", "high"

class MarketContext(BaseModel):
    directional_state: str # "Bullish", "Bearish", "Neutral"
    volatility_state: str # "Low", "Normal", "High", "Extreme"
    session: str # "Asian", "London", "NY", "London-NY Overlap", "Off Hours", "Weekend"
    session_transition: bool
    news_risk: NewsRisk
    liquidity_state: str # "Low", "Medium", "High"
    market_structure: str # "Higher Highs", "Lower Lows", "BOS", "Consolidation", "Expansion"
