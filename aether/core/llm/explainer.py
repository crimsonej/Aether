import os
from typing import Dict, Any, List
from aether.core.utils.logger import logger
from aether.core.llm.models import LLMExplanation

class AetherExplainer:
    """
    LLM layer that explains deterministic signals.
    """
    def __init__(self, api_key_env: str):
        self.api_key = os.getenv(api_key_env) # Mocked env lookup

    def explain_signal(self, signal: Dict[str, Any]) -> LLMExplanation:
        """
        Generates a natural language explanation of a signal.
        """
        signal_id = signal["signal_id"]
        direction = signal["direction"]
        conf = signal["confidence"]["adjusted"]
        reasons = signal["reason_tags"]

        # In a real implementation, this would call OpenAI/Anthropic API.
        # Here we simulate the response for V1.
        explanation = (
            f"The system has generated a {direction} signal for {signal['symbol']} "
            f"with {conf}% confidence. The primary drivers are {', '.join(reasons)}. "
            "The market structure is aligned with the trend and volatility is within normal bounds."
        )

        return LLMExplanation(
            signal_id=signal_id,
            explanation=explanation,
            reasoning_steps=[
                f"Detected {direction} trend via EMA alignment",
                f"Confirmed by {reasons[0]}",
                "Validated against risk firewall"
            ],
            confidence_analysis=f"High confidence ({conf}%) due to confluence of indicators."
        )
