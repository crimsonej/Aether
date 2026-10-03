import unittest

from aether.core.strategy.validation import ValidationFirewall


class FakeConfig:
    def get(self, path, default=None):
        values = {
            "validation.min_confidence": 60,
            "validation.max_spread_multiplier": 2.0,
        }
        return values.get(path, default)


def candidate(**overrides):
    payload = {
        "score": 90,
        "direction": "BUY",
        "context": {
            "session": "London",
            "volatility_state": "Normal",
            "news_risk": {"active": False, "impact": None},
            "liquidity_state": "High",
        },
        "trade": {
            "entry": {"price": 1.1},
            "stop_loss": {"price": 1.09},
            "take_profit": {"price": 1.12},
        },
        "current_spread": 0.0001,
        "typical_spread": 0.0002,
    }
    payload.update(overrides)
    return payload


class TestValidationFirewall(unittest.TestCase):
    def setUp(self):
        self.firewall = ValidationFirewall(FakeConfig())

    def test_hard_context_vetoes_are_reported(self):
        payload = candidate(context={
            "session": "Weekend",
            "volatility_state": "Extreme",
            "news_risk": {"active": True, "impact": "high"},
            "liquidity_state": "High",
        })

        result = self.firewall.validate(payload)

        self.assertFalse(result["valid"])
        self.assertEqual(result["failed_checks"], [
            "disallowed_session",
            "extreme_volatility",
            "high_impact_news",
        ])

    def test_invalid_stop_and_target_geometry_is_rejected(self):
        result = self.firewall.validate(candidate(trade={
            "entry": {"price": 1.1},
            "stop_loss": {"price": 1.11},
            "take_profit": {"price": 1.12},
        }))

        self.assertFalse(result["valid"])
        self.assertIn("invalid_trade_structure", result["failed_checks"])

    def test_spread_limit_and_soft_penalties_are_applied(self):
        payload = candidate(
            context={
                "session": "London",
                "volatility_state": "Normal",
                "news_risk": {"active": True, "impact": "medium"},
                "liquidity_state": "Low",
            },
            current_spread=0.0005,
        )

        result = self.firewall.validate(payload)

        self.assertFalse(result["valid"])
        self.assertEqual(result["adjusted_score"], 60)
        self.assertIn("medium_impact_news", result["warnings"])
        self.assertIn("low_liquidity", result["warnings"])
        self.assertIn("excessive_spread", result["failed_checks"])

    def test_missing_spread_is_explicitly_reported(self):
        payload = candidate()
        payload.pop("current_spread")
        payload.pop("typical_spread")

        result = self.firewall.validate(payload)

        self.assertTrue(result["valid"])
        self.assertIn("spread_data_unavailable", result["warnings"])


if __name__ == "__main__":
    unittest.main()