from pydantic import BaseModel
from typing import List, Optional

class LLMExplanation(BaseModel):
    signal_id: str
    explanation: str
    reasoning_steps: List[str]
    confidence_analysis: str
