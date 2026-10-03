from pydantic import BaseModel
from datetime import datetime
from typing import Optional

class NormalizedCandle(BaseModel):
    symbol: str
    timeframe: str
    open: float
    high: float
    low: float
    close: float
    volume: float
    timestamp: int # Unix timestamp
    close_time: int # Unix timestamp
    source: str
    is_closed: bool
