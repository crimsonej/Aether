# Configuration Reference (`config/aether.yaml`)

The platform reads a single YAML file located at `config/aether.yaml`.  All runtime components obtain their settings through the `ConfigStore`, which loads this file on start‑up.

## Top‑level keys
| Key | Description | Example |
|-----|-------------|---------|
| `schema_version` | Version of the configuration schema (must be >= 1). | `1` |
| `data` | Settings for market data providers and the provider chain. | See *Data* section below |
| `context` | Global context parameters used by the strategy engine. | `adx_threshold: 22` |
| `features` | List of enabled technical indicator modules. | `enabled: ["ema20", "adx"]` |
| `strategies` | Strategy definitions. Each entry points to a YAML file under `config/strategies`. | `- name: trend_following_v1` |
| `validation` | Trading‑risk validation parameters. | `min_confidence: 60` |
| `watchlist` | Default list of symbols to monitor (must be non‑empty). | `- EURUSD` |
| `delivery` | Configuration for outbound adapters (Telegram, Discord, Web, WhatsApp). | See *Delivery* section |
| `ops` | Operational flags such as health‑check interval and auto‑restart. | `health_check_interval: 300` |

---

## `data`
```yaml
data:
  provider_chain:
    - Yahoo
    - AlphaVantage
    - TwelveData
    - Oanda
  oanda:
    api_key_env: OANDA_API_KEY
    account_id_env: OANDA_ACCOUNT_ID
    base_url: "https://api-fxtrade.oanda.com"
  fallback: null
```
- **`provider_chain`** – Ordered list of provider names used by `DataProviderManager`.  The first healthy provider supplies candles/quotes.
- **`oanda`** – Credentials are read from the environment variables named in `api_key_env` and `account_id_env`.  `base_url` can be overridden for testing.

## Onboarding data sources

Aether's onboarding CLI now supports provider-specific prompts and verification for the following data sources: OANDA, AlphaVantage, TwelveData, Yahoo, CSV, and Custom. See the `aether/cli/commands/onboard.py` implementation for exact prompts and behavior. Runtime API keys are persisted to `.env` as `OANDA_API_KEY`, `OANDA_ACCOUNT_ID`, `ALPHAVANTAGE_API_KEY`, and `TWELVEDATA_API_KEY` when provided.
- **`fallback`** – Reserved for future use; keep `null`.

---

## `context`
```yaml
context:
  adx_threshold: 22
  atr_period: 14
  atr_percentiles: [20, 70, 90]
```
Values are passed to the strategy engine and can be referenced in strategy configuration files.

---

## `features`
```yaml
features:
  enabled:
    - ema20
    - ema50
    - ema200
    - adx
    - rsi
    - macd
    - atr
    - bollinger_width
    - higher_highs
    - lower_lows
    - bos
    - consolidation
    - expansion
```
Each name corresponds to a module under `aether.core.features`.  Only the listed modules are instantiated.

---

## `strategies`
```yaml
strategies:
  - name: trend_following_v1
    enabled: true
    config_path: config/strategies/trend_following_v1.yaml
```
The `config_path` points to a strategy‑specific YAML file containing parameters such as ADR thresholds, confidence levels, and weightings.

---

## `validation`
```yaml
validation:
  min_confidence: 60
  max_spread_multiplier: 2.0
  cooldown:
    low_volatility: 20
    normal_volatility: 30
    high_volatility: 45
```
Used by the signal engine to filter out low‑confidence signals and to enforce cooldown periods based on market volatility.

## Gateway administration

The `/start`, `/stop`, and `/reload` control-plane endpoints require a Bearer token. Configure `AETHER_ADMIN_TOKEN` in the process environment or `.env` before using them. Requests without a configured token or with an invalid token are rejected. Keep this value secret and do not commit it.

```http
Authorization: Bearer <AETHER_ADMIN_TOKEN>
```

---

## `watchlist`
A list of symbols that the data provider will always request.  The onboarding wizard guarantees this list is non‑empty; you may edit it manually after the first run.
```yaml
watchlist:
  - EURUSD
  - GBPUSD
  - USDJPY
  - AUDUSD
  - USDCHF
  - USDCAD
  - XAUUSD
```

---

## `delivery`
```yaml
delivery:
  telegram:
    token_env: TELEGRAM_BOT_TOKEN
    chat_id_env: TELEGRAM_CHAT_ID
    auth_token_env: TELEGRAM_WEBHOOK_SECRET
    enabled: true
  discord:
    enabled: false
  web:
    enabled: true
    port: 8080
  whatsapp:
    auth_token_env: WHATSAPP_AUTH_TOKEN
    phone_number_env: WHATSAPP_PHONE_NUMBER
    enabled: true
```
- **Telegram** – Sends outbound signals.  The bot token and chat ID are read from the environment variables indicated.
- **Telegram inbound webhook** – Set `auth_token_env` to `TELEGRAM_WEBHOOK_SECRET`, set that environment variable, and register the same secret with Telegram. The gateway rejects webhook updates unless the matching `X-Telegram-Bot-Api-Secret-Token` header is present.
- **Discord** – Disabled by default.
- **Web** – Provides a simple HTTP UI on the configured `port`.
- **WhatsApp** – Optional; requires Twilio credentials.

---

## `ops`
```yaml
ops:
  health_check_interval: 300   # seconds between internal health polls
  auto_restart: true          # whether the service restarts after fatal errors
  anomaly_detection: true    # enable outlier detection in data streams
```
These flags control the internal watchdog and can be tuned for production environments.

---

**Important:** After editing `config/aether.yaml`, run `aether doctor` to validate the file.  The onboarding wizard writes a correct example configuration automatically.
