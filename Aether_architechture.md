# Aether Trading Intelligence Platform – Architecture Specification v1.1

## System Overview

Aether is a deterministic, non‑execution trading intelligence framework. It consumes market data, computes market context and technical features, applies modular strategies with weighted scoring, constructs trade parameters, validates signals through a multi‑layer firewall, and delivers structured signals to users through messaging platforms.

The platform is designed around five core principles:

1. Determinism
2. Reproducibility
3. Isolation of responsibilities
4. Event‑driven architecture
5. Explainability without AI authority over trading decisions

LLMs never create signals, modify confidence, bypass validation, or override deterministic logic.

All signal generation is reproducible from:

* identical OHLCV data
* identical configuration
* identical feature versions
* identical strategy versions

The system is modular, stateful, observable, and extensible.

---

# System Architecture Philosophy

Aether is not a chatbot with indicators.

It is an event‑driven market intelligence engine with:

* deterministic trading logic
* frozen market context snapshots
* modular strategies
* reproducible outputs
* isolated platform delivery
* operational self‑healing

The architecture intentionally separates:

| Layer         | Responsibility                           |
| ------------- | ---------------------------------------- |
| Intelligence  | Market understanding + signal generation |
| Validation    | Risk and integrity enforcement           |
| Communication | Human explanation and platform delivery  |
| Operations    | Monitoring, recovery, diagnostics        |
| Presentation  | Telegram/Discord/Web rendering           |

---

# High‑Level System Flow

```text
                ┌─────────────────────┐
                │  Market Data Layer  │
                │      (Area 1)       │
                └──────────┬──────────┘
                           │
                    normalized candles
                           │
                ┌──────────▼──────────┐
                │ Market Context      │
                │ Engine (Area 2)     │
                └──────────┬──────────┘
                           │
                ┌──────────▼──────────┐
                │ Feature & Indicator │
                │ Engine (Area 3)     │
                └──────────┬──────────┘
                           │
                ┌──────────▼──────────┐
                │ Strategy Registry   │
                │ + Scoring (Area 4)  │
                └──────────┬──────────┘
                           │
                ┌──────────▼──────────┐
                │ Trade Construction  │
                │ + Validation        │
                └──────────┬──────────┘
                           │
                ┌──────────▼──────────┐
                │ Structured Signal   │
                │ Output (Area 4.5)   │
                └───────┬─────┬───────┘
                        │     │
          ┌─────────────┘     └──────────────┐
          │                                  │
┌─────────▼────────┐             ┌───────────▼──────────┐
│ Signal State     │             │ Analytics + Replay   │
│ Manager (Area 7) │             │ Engine (Area 5)      │
└─────────┬────────┘             └───────────┬──────────┘
          │                                  │
          └──────────────┬───────────────────┘
                         │
                ┌────────▼────────┐
                │ Delivery Layer  │
                │ (Area 11)       │
                └────────┬────────┘
                         │
             ┌───────────┴───────────┐
             │ Platform Adapters     │
             │ Telegram/Discord/Web  │
             │      (Area 16)        │
             └───────────────────────┘
```

---

# Core System Guarantees

| Guarantee               | Description                                              |
| ----------------------- | -------------------------------------------------------- |
| Deterministic Signals   | Same market data + same configuration = same output      |
| No LLM Signal Authority | LLMs never create, alter, veto, or validate signals      |
| Validation Mandatory    | No signal bypasses Area 4.4                              |
| Feature Isolation       | Strategies consume only pre‑computed features            |
| Reproducibility         | Signals include frozen snapshots + hashes                |
| Stateless Strategies    | Strategies cannot mutate system state                    |
| Version Locking         | Signals, strategies, configs, and features are versioned |
| Event Integrity         | Internal communication uses immutable events             |
| Time Normalization      | All timestamps normalized to UTC                         |
| Platform Isolation      | Platform adapters contain zero trading logic             |

---

# Event‑Driven Core (NEW FOUNDATIONAL LAYER)

## Purpose

Provide decoupled communication between all major subsystems.

Without this layer, modules become tightly coupled and difficult to scale or debug.

## Core Principle

All major actions are emitted as immutable events.

Subsystems subscribe to events instead of directly calling each other.

---

## Example Events

| Event                     | Description                        |
| ------------------------- | ---------------------------------- |
| candle_closed             | Closed candle available            |
| context_computed          | Market context ready               |
| features_ready            | Feature vector computed            |
| strategy_signal_candidate | Strategy produced candidate signal |
| signal_validated          | Validation passed                  |
| signal_rejected           | Validation failed                  |
| signal_emitted            | Signal delivered                   |
| signal_expired            | Signal expired or invalidated      |
| data_source_failed        | Provider failure detected          |
| component_restarted       | Ops AI restarted subsystem         |

---

## Event Structure

```json
{
  "event_type": "signal_emitted",
  "timestamp": "2025-01-15T14:30:00Z",
  "schema_version": "1.0",
  "payload": {}
}
```

---

# Scheduling & Timing System (NEW)

## Purpose

Coordinate all periodic operations.

## Responsibilities

* candle close triggers
* session transitions
* news polling
* health checks
* cooldown expiration
* signal expiry
* replay scheduling
* metrics aggregation

---

## Scheduling Rules

| Task                  | Frequency        |
| --------------------- | ---------------- |
| 15m candle evaluation | every 15 minutes |
| 1h candle evaluation  | hourly           |
| 4h candle evaluation  | every 4 hours    |
| health checks         | every 5 minutes  |
| news calendar sync    | every 15 minutes |
| metrics aggregation   | hourly           |
| journal persistence   | immediate append |

---

# Candle Lifecycle Manager (NEW)

## Purpose

Prevent premature analysis and duplicate computation.

## Candle States

```text
RAW → OPEN → CLOSED → ARCHIVED
```

## Rules

* Features compute only on CLOSED candles
* Signals never use OPEN candles in V1
* Duplicate CLOSED candles rejected
* Historical corrections logged separately

---

# Data Normalization Rules (NEW)

## Symbol Standardization

All symbols internally normalized:

| External | Internal |
| -------- | -------- |
| EUR/USD  | EURUSD   |
| XAU_USD  | XAUUSD   |
| BTC-USDT | BTCUSDT  |

---

## Time Rules

* UTC only
* ISO‑8601 timestamps
* Exchange/server time authoritative

---

## Missing Candle Policy

| Scenario                 | Action                           |
| ------------------------ | -------------------------------- |
| single missing candle    | fetch repair attempt             |
| multiple missing candles | invalidate timeframe temporarily |
| duplicate timestamps     | keep newest validated candle     |
| future timestamps        | reject                           |

---

# Feature Store (NEW)

## Purpose

Persistent storage of computed indicators/features.

Avoids:

* redundant recomputation
* replay inconsistency
* strategy drift

---

## Responsibilities

* cache computed features
* store feature versions
* support replay/backtesting
* preserve reproducibility

---

## Example

```json
{
  "symbol": "EURUSD",
  "timeframe": "15m",
  "timestamp": "2025-01-15T14:30:00Z",
  "features": {
    "ema_20": 1.0842,
    "rsi": 62
  },
  "feature_engine_version": "1.0.0"
}
```

---

# Strategy Sandbox & Isolation (NEW)

## Purpose

Prevent user‑defined strategies from destabilizing the platform.

## Restrictions

Strategies cannot:

* access filesystem
* access secrets
* access raw exchange connections
* execute shell commands
* mutate global state
* bypass validation

---

## Runtime Constraints

| Constraint        | Limit        |
| ----------------- | ------------ |
| execution timeout | configurable |
| memory limit      | configurable |
| filesystem access | denied       |
| network access    | denied       |

---

# Conflict Resolution Layer (NEW)

## Purpose

Resolve conflicts between multiple active strategies.

## Conflict Scenarios

| Strategy A     | Strategy B              |
| -------------- | ----------------------- |
| BUY            | SELL                    |
| Strong BUY     | Weak BUY                |
| Trend strategy | Mean reversion strategy |

---

## Resolution Rules

1. Higher priority strategy wins
2. Stronger confidence wins if priorities equal
3. Contradictory strong signals may result in HOLD
4. Strategy compatibility rules configurable

---

# Deterministic Replay Guarantee (NEW)

## Principle

Replay execution must reproduce original signals.

## Requirements

* versioned features
* versioned strategies
* frozen configs
* frozen market snapshots
* immutable replay datasets

---

# External Dependency Manager (NEW)

## Purpose

Manage external providers safely.

## Responsibilities

* provider health scoring
* rate limiting
* fallback routing
* retry logic
* circuit breakers

---

## Provider Priority Example

```json
{
  "EURUSD": ["oanda", "twelve_data"]
}
```

---

# Area 1: Market Data Layer

## Purpose

Acquire, normalize, validate, cache, and distribute OHLCV market data.

## V1 Sources

| Asset              | Source                   |
| ------------------ | ------------------------ |
| Forex              | Oanda API                |
| Gold               | Oanda API                |
| Prototype fallback | TradingView TA           |
| Future crypto      | Binance REST + WebSocket |

---

## Timeframes

| Timeframe | Status  |
| --------- | ------- |
| 15m       | default |
| 1h        | enabled |
| 4h        | enabled |

---

## Normalized Candle Schema

```json
{
  "symbol": "EURUSD",
  "timeframe": "1h",
  "open": 1.0842,
  "high": 1.0850,
  "low": 1.0835,
  "close": 1.0845,
  "volume": 12345,
  "timestamp": 1705312200,
  "close_time": 1705315800,
  "source": "oanda",
  "is_closed": true
}
```

---

## Reliability Rules

| Rule                | Description                 |
| ------------------- | --------------------------- |
| closed candles only | no open candle analysis     |
| warmup required     | EMA200 requires 200 candles |
| circuit breaker     | 5 failures → 60s cooldown   |
| staleness checks    | timeframe dependent         |

---

# Area 2: Market Context Engine

## Purpose

Describe current market conditions independently of any strategy.

## Dimensions

| Dimension          | Values                                    |
| ------------------ | ----------------------------------------- |
| directional_state  | Bullish / Bearish / Neutral               |
| volatility_state   | Low / Normal / High / Extreme             |
| session            | Asian / London / NY / Overlap / Off Hours |
| session_transition | true / false                              |
| news_risk          | low / medium / high                       |
| liquidity_state    | Low / Medium / High                       |
| market_structure   | HH / LL / BOS / Consolidation             |

---

## Output Example

```json
{
  "directional_state": "Bullish",
  "volatility_state": "Normal",
  "session": "London-NY Overlap",
  "session_transition": false,
  "news_risk": {
    "active": false,
    "impact": null
  },
  "liquidity_state": "Medium",
  "market_structure": "Higher Highs"
}
```

---

# Area 3: Feature & Indicator Engine

## Purpose

Compute all technical indicators and derived market features.

Strategies consume only these pre‑computed features.

---

## Architecture

* plugin‑based indicators
* dependency graph
* metadata support
* feature caching
* feature validation

---

## Built‑in Indicators

| Category   | Indicators                               |
| ---------- | ---------------------------------------- |
| Trend      | EMA20, EMA50, EMA200, ADX, +DI, -DI      |
| Momentum   | RSI, MACD                                |
| Volatility | ATR, Bollinger width                     |
| Structure  | HH/LL, BOS, swing highs/lows             |
| Composite  | RSI divergence, EMA slope, ATR expansion |

---

## Excluded From V1

* Ichimoku
* Stochastic
* OBV
* advanced candle pattern systems

---

# Area 4: Strategy Engine

## 4.1 Strategy Registry

Hybrid architecture:

* logic in code
* parameters in JSON

Supports:

* priority
* activation conditions
* capabilities
* versioning

---

## 4.2 Weighted Confluence Scoring

### Positive Weights

| Factor             | Weight |
| ------------------ | ------ |
| HTF alignment      | 25     |
| market structure   | 20     |
| EMA structure      | 15     |
| ADX strength       | 15     |
| RSI confirmation   | 10     |
| volatility quality | 10     |
| session quality    | 5      |

---

### Penalties

| Penalty                 | Value |
| ----------------------- | ----- |
| weak structure          | -15   |
| RSI divergence conflict | -10   |
| transition window       | -10   |
| volatility instability  | -10   |
| low liquidity           | -10   |

---

### Veto Conditions

* High news risk
* Extreme volatility
* Off hours/weekend
* Spread > 2× typical

---

## 4.3 Trade Construction

### Entry

* market only (V1)

### Stop Loss

| Volatility | ATR Multiplier |
| ---------- | -------------- |
| Low        | 1.2×           |
| Normal     | 1.5×           |
| High       | 1.8×           |

Structural override mandatory.

---

### Take Profit

* default 1:2 RR
* optional 1:1 and 1:3

---

### Expiry

| TF  | Expiry |
| --- | ------ |
| 15m | 2h     |
| 1h  | 8h     |
| 4h  | 24h    |

---

## 4.4 Validation Firewall

### Hard Checks

* minimum confidence
* spread
* cooldown
* uniqueness
* session filter
* volatility cap
* structure validity

### Soft Checks

* medium news
* low liquidity

---

## 4.5 Structured Signal Contract

Signals include:

* signal_id
* strategy snapshot
* confidence
* trade construction
* context snapshot
* validation results
* market snapshot hash
* optional sizing hints

All signals schema‑versioned.

---

# Area 5: Signal Analytics & Backtesting

## Purpose

Historical evaluation and replay testing.

## Features

* replay engine
* WIN/LOSS/BREAKEVEN evaluation
* MFE/MAE tracking
* RR analysis
* strategy performance metrics

---

## Limitations (V1)

* no slippage model
* no commission model
* no Monte Carlo simulation

---

# Area 6: Risk & Capital Logic

## Purpose

Risk guidance only.

No trade execution.

## Inputs

* account size
* risk per trade
* max daily risk
* max open positions

## Portfolio Heat

Tracks aggregate exposure.

Can suppress new signals if heat exceeds configured threshold.

---

# Area 7: Signal State Manager

## Purpose

Lifecycle control for signals.

## Signal States

```text
CREATED → ACTIVE → EXPIRED | INVALIDATED | CLOSED
```

---

## Responsibilities

* cooldown management
* deduplication
* expiry handling
* journal persistence
* context invalidation

---

# Area 9: LLM Communication Layer

## Purpose

Explain deterministic signals.

Never generate trading decisions.

## Allowed Actions

* explain indicators
* explain signal reasoning
* answer questions
* teach concepts

## Forbidden Actions

* creating signals
* modifying confidence
* overriding validation
* changing risk

---

# Area 10: User Personalization (Minimal)

## Purpose

Store lightweight user preferences.

## Supported Settings

* watchlist
* risk parameters
* notification preferences

No profiles.
No experience tiers.
No automatic grouping.

---

# Area 11: Delivery Manager

## Purpose

Route signals to messaging platforms.

## Responsibilities

* command routing
* retry queue
* signal dispatch
* formatting delegation

Commands behave identically across all platforms.

---

# Area 12: Monitoring & Observability

## Metrics

* signals/hour
* win rate
* API latency
* rejection rate
* cooldown hits
* provider failures

---

## Logging

Structured JSON logs.

Example events:

* signal_emitted
* validation_failed
* data_fetch_error
* llm_response
* component_restart

---

## Health Checks

Every 5 minutes:

* data pipeline alive
* strategy responsiveness
* validation integrity
* provider connectivity

---

# Area 13: Config & Security

## Purpose

Centralized configuration + secrets handling.

## Rules

* secrets only in environment variables
* sandboxed strategies
* schema‑validated config changes
* isolated execution boundaries

---

## Permission Boundaries (NEW)

| Role             | Permissions               |
| ---------------- | ------------------------- |
| Admin            | full system control       |
| Ops AI           | limited operational tools |
| Strategy Runtime | feature consumption only  |
| LLM Layer        | explanation only          |

---

# Area 14: AI Operations & Maintenance

## Purpose

Autonomous operational management.

Never involved in trading decisions.

---

## Responsibilities

* subsystem monitoring
* auto‑restart
* failover management
* anomaly detection
* optimization suggestions
* diagnostics

---

## V1 Scope

* restart failed components
* provider failover
* rejection spike detection
* admin alerting
* health reports

---

# Area 15: Capability Configuration Layer

## Purpose

Static enable/disable registry.

No hidden AI behavior.

---

## Configurable Items

* indicators
* timeframes
* symbols
* strategies

---

## Example

```json
{
  "enabled_indicators": ["RSI", "EMA", "MACD", "ATR"],
  "enabled_timeframes": ["15m", "1h"],
  "enabled_symbols": ["EURUSD", "XAUUSD"],
  "enabled_strategies": ["trend_following_v1"]
}
```

---

# Area 16: Messaging Platforms

## Core Principle

Platforms are dumb pipes.

No trading logic inside adapters.

---

## V1 Platforms

| Platform      | Status         |
| ------------- | -------------- |
| Telegram      | full support   |
| Discord       | optional/basic |
| Web dashboard | internal/debug |

---

## Message Flow

### Outgoing

```text
Signal Engine → Delivery Manager → Adapter → User
```

### Incoming

```text
User → Adapter → Command Router → Core
```

---

## Adapter Responsibilities

* normalize commands
* format signals
* send messages
* render buttons

No signal generation.

---

# Final Scope Summary

| Area | Title                          |
| ---- | ------------------------------ |
| 1    | Market Data Layer              |
| 2    | Market Context Engine          |
| 3    | Feature & Indicator Engine     |
| 4    | Strategy Engine                |
| 5    | Signal Analytics & Backtesting |
| 6    | Risk & Capital Logic           |
| 7    | Signal State Manager           |
| 9    | LLM Communication Layer        |
| 10   | User Personalization           |
| 11   | Delivery Manager               |
| 12   | Monitoring & Observability     |
| 13   | Config & Security              |
| 14   | AI Operations & Maintenance    |
| 15   | Capability Configuration Layer |
| 16   | Messaging Platforms            |

---

# Final Notes

Aether V1 is intentionally conservative.

The system prioritizes:

* reliability over complexity
* deterministic logic over AI autonomy
* modularity over shortcuts
* reproducibility over hype

Future versions may add:

* additional strategies
* crypto support
* advanced replay simulation
* machine learning ranking layers
* richer dashboards
* more messaging platforms

But the foundational guarantees of deterministic signal generation and validation integrity remain non‑negotiable.

---

Version: 1.1
Date: 2026-05-29
Status: Finalized Architecture Specification for V1
