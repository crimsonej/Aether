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

### Free data resources

The provider registry includes no-key sources such as Yahoo Finance, Frankfurter/ECB rates, and CoinGecko public data, plus providers with free tiers that require credentials. Availability, permitted use, symbols, update frequency, and quotas vary by provider and can change; Aether does not guarantee a particular service remains free. Keep a provider enabled only when it supports the symbols and timeframes you request. Frankfurter is daily-resolution data and is not a replacement for intraday FX candles.

---

## `context`
```yaml
context:
  adx_threshold: 22
  atr_period: 14
  atr_percentiles: [20, 70, 90]
  directional_fast_period: 20
  directional_slow_period: 50
  directional_slope_period: 5
  swing_order: 2
  structure_lookback: 20
```
Direction uses fast/slow EMA alignment and slow-EMA slope; it remains Neutral until the slow period plus slope history is available. Market structure uses confirmed swing points and labels a close beyond the recent range as BOS. Values shown are defaults and can be tuned explicitly.

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
  max_quote_age_seconds: 120
  typical_spread_by_symbol: {}
  cooldown:
    low_volatility: 20
    normal_volatility: 30
    high_volatility: 45
```

## Local AI resource

Ollama can run an operator-selected model on the same machine. It is optional and disabled by default. Install Ollama separately, choose and download a model yourself, then configure the exact installed model name. Aether does not automatically download models because model files can be large and their licenses/resources differ.

```yaml
model_manager:
  chain: [Ollama, Local]
  ollama:
    enabled: true
    base_url: http://127.0.0.1:11434
    model: <installed-model-name>
    keep_alive: 5m
```

Use `ollama list` to find an installed model name. Ollama inference needs local memory and compute; “no API charge” does not mean zero resource cost. The cloud providers in the model chain may incur charges depending on the account and current plan. LLMs remain explanation/assistant resources only and never create or modify trading signals, confidence, stops, targets, or validation.
Used by the signal engine to filter out low‑confidence signals and to enforce cooldown periods based on market volatility.

## Conversational alerts

The assistant accepts bounded, permission-checked requests such as “alert me if EURUSD goes above 1.0950”, “remove my EURUSD alert below 1.0700”, “alert me about high-impact news for EURUSD and GBPUSD”, and “show my alerts”. Price alerts use fresh provider quotes, trigger once, and are disabled after firing. They do not create or modify trading signals.

News preferences are pair-specific and match calendar currencies against both sides of the pair (EURUSD matches EUR and USD). Notifications only run when a calendar CSV source is configured. The source must be HTTPS and provide `Date`, `Time`, `Currency`, `Impact`, and `Event` columns; date/time are interpreted in `source_timezone`. No source is configured by default, so the assistant must report news notifications as inactive until an operator supplies and verifies a suitable feed.

Conversational control uses a provider-independent tool registry. The model can request only registered tools; Aether validates each tool's JSON schema, checks the user's role permission, executes at most the requested bounded operation, and records the result. Current tools cover configured system overview, watchlist, alert listing and management, symbol watchlist changes, delivery toggles, and supported indicator toggles. Arbitrary shell commands, unrestricted URL fetching, filesystem edits, strategy-code changes, and signal/risk overrides are not exposed as tools.

```yaml
alerts:
  price: []
  news:
    enabled: false
    pairs: []
    minimum_impact: high
    source_url: null
    source_timezone: America/New_York
    poll_interval_seconds: 300
    lead_minutes: 60
```

Signal validation also requires a fresh quote with valid bid and ask, plus a measured typical spread for the symbol. `typical_spread_by_symbol` values are absolute price differences in the same units as `ask - bid`; populate them from your broker/provider observations for each enabled instrument. An empty map, stale quote, or provider response without bid/ask blocks signal emission by design. Historical replay requires a `spread` (or `current_spread`) field on each candle row and uses the same per-symbol baseline.

```yaml
validation:
  max_quote_age_seconds: 120
  typical_spread_by_symbol:
    # Add measured values for each enabled symbol, for example EURUSD: 0.00012
```

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
