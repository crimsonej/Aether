# Aether Data-Flow Pipeline

This document describes every component that touches a piece of data on its
way from a market event to a Telegram message, with diagrams and per-component
purpose tables.

Last updated: 2026-06-03

---

## 1. System Context

External actors (operator, market data, AI providers) and the Aether
subsystems that interact with them.

```mermaid
flowchart LR
    Operator([Operator on Telegram])
    Gateway[Aether Gateway<br/>FastAPI on :18791]
    Watchlist[ConfigStore<br/>watchlist + strategies]
    LLMProviders{{LLM Providers<br/>NVIDIA · OpenAI · Claude · Gemini · OpenRouter · Local}}
    DataProviders{{Data Providers<br/>TwelveData · Finnhub · AlphaVantage · Yahoo · Oanda · Frankfurter · CoinGecko}}

    Operator -- "Telegram Update<br/>/menu /hi /signals" --> Gateway
    Gateway -- "bot messages" --> Operator
    Gateway -- "fetch candles" --> DataProviders
    Gateway -- "generate reply" --> LLMProviders
    Gateway --- Watchlist
    DataProviders -- "candles" --> Gateway
    LLMProviders -- "tokens" --> Gateway
```

| Component        | Purpose                                                              |
|------------------|----------------------------------------------------------------------|
| Operator         | The end user interacting with Aether via Telegram.                   |
| Gateway          | FastAPI process hosting the dashboard and Telegram webhook.          |
| Watchlist        | Persistent list of symbols the platform monitors.                    |
| LLM Providers    | External AI services used for natural-language replies.              |
| Data Providers   | External market data sources (some need API keys, some don't).        |

---

## 2. Signal Pipeline (happy path)

The flow of a single market candle all the way to a Telegram signal
message, when every subsystem is healthy.

```mermaid
sequenceDiagram
    participant Ext as External Provider
    participant DPM as DataProviderManager
    participant DE as DataEngine
    participant Bus as EventBus
    participant CTX as ContextEngine
    participant FE as FeatureEngine
    participant ST as StrategyEngine
    participant SSM as SignalStateManager
    participant DM as DeliveryManager
    participant TG as TelegramAdapter

    DE->>DPM: get_candles(EURUSD, 4h, 1)
    DPM->>Ext: HTTP GET time_series
    Ext-->>DPM: 68 candles
    DPM-->>DE: NormalizedCandle
    DE->>Bus: publish("data.candle", event)
    Bus->>CTX: deliver
    Bus->>FE: deliver
    Bus->>ST: deliver
    CTX->>Bus: publish("context.updated")
    FE->>Bus: publish("features.calculated")
    Bus->>ST: deliver
    ST->>ST: ValidationFirewall.validate()
    ST->>Bus: publish("signal.emitted", {trade, trade_construction})
    Bus->>SSM: deliver
    SSM->>Bus: publish("signal.generated")
    Bus->>DM: deliver
    DM->>TG: send_signal(signal)
    TG-->>Operator: formatted Telegram message
```

| Component         | Purpose                                                                |
|-------------------|------------------------------------------------------------------------|
| DataProviderManager | Walks the configured chain, returns the first healthy provider's data. |
| DataEngine        | Polls candles for the watchlist on a fixed cadence.                    |
| EventBus          | In-process pub/sub; decouples producers from consumers.                |
| ContextEngine     | Computes market regime (ADX, ATR percentiles, etc.).                   |
| FeatureEngine     | Computes technical indicators (EMA, RSI, MACD, etc.).                  |
| StrategyEngine    | Combines context + features, runs the strategy, emits signals.         |
| ValidationFirewall| Final gate: confidence, spread, news risk, etc.                        |
| SignalStateManager| Tracks the active signal through PENDING → ACTIVE → CLOSED.            |
| DeliveryManager   | Sends the signal through every enabled outbound adapter.               |
| TelegramAdapter   | Formats the signal as Markdown and posts it to the operator.           |

---

## 3. User Inbound (free-form chat and slash commands)

How a user message reaches the AI assistant and how the reply gets back
to the user.

```mermaid
sequenceDiagram
    participant U as User
    participant TG as Telegram
    participant GW as Gateway
    participant AR as AdapterRegistry
    participant Bus as EventBus
    participant TM as TelegramMenuService
    participant AI as AIConfigManager
    participant MM as ModelManager
    participant LLM as LLM Provider
    participant Senders as bound senders

    U->>TG: type "hi"
    TG->>GW: POST /api/telegram/webhook
    GW->>AR: registry.get("telegram").receive_message(update)
    AR->>Bus: publish("user_command", {raw, user, source})
    Bus->>TM: deliver
    TM->>TM: parse command
    alt known slash command
        TM->>Senders: send_message(formatted)
        Senders->>TG: sendMessage
    else free-form text
        TM->>Bus: publish("user.free_text", payload)
        Bus->>AI: deliver
        AI->>MM: generate(prompt)
        MM->>LLM: HTTP stream
        LLM-->>MM: tokens
        MM-->>AI: text
        AI->>Senders: send_message(reply)
        Senders->>TG: sendMessage
        TG->>U: reply rendered
    end
```

| Component         | Purpose                                                                 |
|-------------------|-------------------------------------------------------------------------|
| Telegram          | Pushes the user's message to our webhook.                               |
| Gateway           | The single FastAPI entrypoint; routes to the adapter registry.          |
| AdapterRegistry   | Resolves the adapter by name and dispatches inbound updates.            |
| EventBus          | Fans the event out to multiple subscribers.                             |
| TelegramMenuService | Handles known slash commands (`/menu`, `/status`, etc.).              |
| AIConfigManager   | Handles free-form chat, parses JSON intents, applies config mutations.  |
| ModelManager      | Failover chain across the configured LLM providers.                     |
| LLM Provider      | Generates a reply; the response is streamed token-by-token.             |
| bound senders     | `bind_default_senders` wires the adapter's `send_message` to the AI.    |

---

## 4. Failure Paths

How Aether self-heals when a single provider is rate-limited, an LLM
provider is exhausted, or the signal pipeline is silent.

```mermaid
flowchart TB
    subgraph Data Layer
        DPM[DataProviderManager]
        POLL[Polling loop]
        BACKOFF[Per-provider backoff map]
        NOOP_KEYED[60 s backoff keyed]
        NOOP_KEYLESS[15 s backoff keyless]
        CHAIN[provider_chain + NO_KEY_FALLBACKS]
    end

    subgraph LLM Layer
        MM[ModelManager]
        UNHEALTHY[unhealthy dict]
        LOCAL[Local fallback]
    end

    subgraph Signal Layer
        ST[StrategyEngine]
        VF[ValidationFirewall]
        SSM[SignalStateManager]
        RETRY[RetryQueue]
    end

    POLL --> DPM
    DPM -- "provider raises" --> BACKOFF
    BACKOFF -- "keyed" --> NOOP_KEYED
    BACKOFF -- "keyless" --> NOOP_KEYLESS
    DPM -- "next provider" --> CHAIN

    ST --> VF
    VF -- "low score" --> X[drop]
    VF -- "passes" --> SSM
    SSM -- "delivery fails" --> RETRY
    RETRY -- "exponential backoff" --> SSM

    MM -- "ping fails" --> UNHEALTHY
    UNHEALTHY -- "60 s" --> MM
    MM -- "all providers exhausted" --> LOCAL
```

| Failure                  | How it's handled                                                                                          |
|--------------------------|-----------------------------------------------------------------------------------------------------------|
| Provider rate-limit      | Marked failed, put in `BACKOFF_SECONDS_KEYED=60s` (or `_KEYLESS=15s`); the next provider in chain is tried.|
| All keyed providers down | The candidate chain is automatically extended with `Frankfurter` + `CoinGecko` (no-key fallbacks).         |
| All LLM providers down   | `ModelManager.generate` raises; `AIConfigManager` replies with a friendly "no AI provider configured".     |
| LLM returns no JSON      | Treated as free-form text and echoed back to the user (so greetings still get answered).                  |
| Strategy emits no signal | `data.candle` events still publish; the operator sees the cycle summary but no signal message arrives.     |
| Telegram send fails      | `DeliveryManager` enqueues the signal in `RetryQueue`; exponential backoff retries until success.        |
| `_event_bus` back-pressure | Oldest event is dropped, newest enqueued, an `EventBus back-pressure` log line is emitted.              |

---

## How the wired components are produced

1. `ServiceManager._load_subsystems()` instantiates every subsystem with
   references to the `ConfigStore` and the shared `EventBus`.
2. `ServiceManager._start_subsystems()` calls each subsystem's `start()` —
   for the data layer, this kicks off the polling loop; for the
   `AdapterRegistry`, this loads the enabled adapters.
3. `ServiceManager._wire_subsystems()` then:
   - Calls `register_events` on every subsystem (subscribes to bus topics).
   - Wires the `DeliveryManager` to the registry + retry queue.
   - **Binds the AI's senders to the loaded adapters** via
     `AIConfigManager.bind_default_senders(adapters)` — this is the wiring
     that makes free-form chat work.
4. Once started, the gateway logs `gateway_started` and the
   `TelegramAdapter.send_startup_ping` is called with the model chain,
   data provider list, and subsystem count.

## Verification commands

```bash
# After the gateway has been running for ~30 s:
curl http://localhost:18791/health
curl http://localhost:18791/api/status
curl http://localhost:18791/metrics

# Watch the EventBus in real time (WebSocket):
#   ws://localhost:18791/ws   (no auth required)

# Watch the live data fetch cycle:
python3 -c "import asyncio, yaml; cfg = yaml.safe_load(open('config/aether.yaml')); print('chain:', cfg['data']['provider_chain']); print('providers:', list(cfg['data']['providers'].keys()))"
```
