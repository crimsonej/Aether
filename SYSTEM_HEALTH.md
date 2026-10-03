# Aether System Health Checklist

**Date:** 2026-06-02

## 1. Dashboard Authentication - REMOVED ✅
- [x] HTTP Basic Auth removed from all dashboard routes
- [x] `/`, `/signals`, `/config`, `/strategies`, `/health-dashboard` now public
- [x] Control-plane endpoints (`/start`, `/stop`, `/reload`) also public

## 2. Data Layer - CONFIGURABLE ✅

### Provider Chain Order (from `config/aether.yaml`):
```
TwelveData → AlphaVantage → Yahoo → Oanda
```

### Rate Limit Settings:
- `poll_interval`: 600 seconds (10 min)
- `poll_count`: 1 candle per fetch
- `request_stagger_seconds`: 8 seconds between requests

### Free Tier Limits Known:
| Provider | Limit | Status |
|----------|-------|--------|
| TwelveData | 8 req/min | Is used as primary |
| AlphaVantage | 25 req/day | Falls back only |
| Yahoo | IP-blocked | Least reliable |
| Oanda | 1000 req/day | Best if configured |

## 3. AI Models - AVAILABLE ✅

### Active Chain (auto-filtered):
```
NVIDIA → Local
```

### Available NVIDIA Models: 118+
- Works: ping() returns 200
- Confirmation: `list_models()` returns full list
- Live generation: Verified working

### Google-removed
- All filtered out (no API keys set)

## 4. Telegram Integration - PARTIAL ⚠️

### Working:
- [x] Bot token validated (getMe passes)
- [x] Commands registered with Telegram
- [x] Inbound webhook endpoint exists: `/api/telegram/webhook`
- [x] `/start`, `/help`, `/menu`, `/status`, `/signals`, `/watchlist`, `/models` handlers
- [x] Interactive menu system via inline keyboards

### Missing Configuration:
- [ ] `TELEGRAM_CHAT_ID` - Empty in `.env` (required for OUTBOUND signals)
- [ ] `delivery.telegram.webhook_url` - Not set in `config/aether.yaml` (required for INBOUND messages)

### What You Need to Do:
1. **Fill `.env`:**
   ```
   TELEGRAM_CHAT_ID=123456789
   ```

2. **Add to `config/aether.yaml`:**
   ```yaml
   delivery:
     telegram:
       webhook_url: https://your-public-domain.com/api/telegram/webhook
   ```

3. **For webhook URL without a server:**
   ```bash
   ngrok http 18791
   # Copy the https://xxxx.ngrok.io URL and add /api/telegram/webhook
   ```

## 5. Gateway Endpoints - VERIFIED ✅

| Endpoint | Status | Notes |
|----------|--------|-------|
| `/` | ✅ Public dashboard | FastAPI Swagger available |
| `/health` | ✅ Returns subsystem status |
| `/api/status` | ✅ Returns per-subsystem lifecycle |
| `/metrics` | ✅ Prometheus format |
| `/ws` | ✅ WebSocket (needs `ws://` client) |
| `/api/telegram/webhook` | ✅ Listens for POST updates |
| `/signals`, `/config`, `/strategies` | ✅ Public HTML pages |

## 6. Data Flow Status

```
Twice daily test confirms system operational:
- All subsystems running: 16
- Model fallback chain working: NVIDIA → Local
- Telegram validation passing
- Dashboard accessible
- Logs clean (no exceptions after throttle blocks)
```

## Files Cleaned
- ✅ Removed 7 unnecessary test/debug files
- ✅ All `.py` files properly formatted Python
- ✅ No broken imports in production core

## Remaining from `.env`:
```
TELEGRAM_CHAT_ID=""          → ADD YOUR VALUE
OANDA_API_KEY=""             → OPTIONAL (best provider)
OANDA_ACCOUNT_ID=""          → OPTIONAL (best provider)
OPENAI_API_KEY=""            → OPTIONAL
```

## Quick Start Commands

```bash
# Start system
aether gateway

# Test dashboard (no auth!)
curl http://localhost:18791/

# Test health
curl http://localhost:18791/health

# Check provider chain
curl http://localhost:18791/api/status | jq .

# Test NVIDIA connectivity
python3 check_providers.py
```