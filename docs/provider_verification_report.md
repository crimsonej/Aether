Provider Verification Report

Summary:
- Tests added: `aether/tests/test_providers.py` (timeout, rate limit, stale data, invalid key/malformed responses implicitly covered by mocks), all tests passed on 2026-05-31.

What was verified:
- Candle normalization consistency: provider implementations return `NormalizedCandle` with UTC timestamps.
- Timestamps: providers now use `dateutil` or `datetime` to ensure timezone-aware UTC timestamps (`datetime_from_ts` helper).
- Symbol mapping: `SymbolMapper` covers TwelveData, AlphaVantage, Yahoo, Oanda mappings. Tests exercise provider manager failover which depends on mapping correctness.
- Staleness detection: `compute_staleness()` used by `DataProviderManager` to mark stale providers and emit `data.stale_detected`.
- Failover events: `data.provider_failed` and `data.provider_changed` are published by `DataProviderManager` and covered by tests.
- Recovery: test `test_recovery_of_provider_via_health_refresh` validates provider can be recovered via health refresh.
- Health reporting: `ProviderHealth` models created by each provider; `DataProviderManager.health()` exposes collected health data.

How to run verification locally:

```bash
cd '/mnt/data/edit room/aether'
PYTHONPATH=. pytest -q aether/tests/test_providers.py -q
```

Files of interest:
- [aether/core/data/provider.py](aether/core/data/provider.py)
- [aether/core/data/provider_manager.py](aether/core/data/provider_manager.py)
- [aether/core/data/providers/twelvedata.py](aether/core/data/providers/twelvedata.py)
- [aether/core/data/providers/alphavantage.py](aether/core/data/providers/alphavantage.py)
- [aether/core/data/providers/yahoo.py](aether/core/data/providers/yahoo.py)
- [aether/core/data/providers/oanda.py](aether/core/data/providers/oanda.py)
- [aether/tests/test_providers.py](aether/tests/test_providers.py)

Notes / Recommendations:
- Migrate `ProviderHealth.dict()` calls to `model_dump()` to avoid Pydantic deprecation warnings (`aether/core/data/provider_manager.py`).
- Consider adding schema-driven validation for provider responses to catch malformed payloads earlier.
