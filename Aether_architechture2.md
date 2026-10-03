Aether Trading Intelligence Platform – Architecture Specification v1.0
System Overview
Aether is a deterministic, non‑execution signal generation framework. It consumes market data, computes market context and technical features, applies modular strategies with weighted scoring, constructs trade parameters, validates signals through a multi‑layer firewall, and delivers structured signals to users via messaging platforms. An LLM layer explains signals but never creates them. An operations AI maintains system health. All signals are reproducible, auditable, and independent of any AI hallucination.

This specification describes the complete architecture. No user profiles, no experience tiers, no automatic complexity assignment – everything is explicit configuration.

System Map (Top‑Level Pipeline)
text
￼
Copy
￼
Download
Market Data (Area 1)
       ↓
Market Context (Area 2)
       ↓
Feature Extraction (Area 3)
       ↓
Strategy Registry (Area 4.1)
       ↓
Direction Detection + Confluence Scoring (Area 4.2)
       ↓
Trade Construction (Area 4.3)
       ↓
Validation Firewall (Area 4.4)
       ↓
Structured Signal Output (Area 4.5)
       ↓ (parallel)
       ├→ Signal State Manager (Area 7)
       ├→ Risk & Capital Logic (Area 6)
       ├→ Signal Analytics & Backtesting (Area 5)
       ├→ LLM Explanation Layer (Area 9)
       └→ Delivery Manager (Area 11)

Supporting layers:
- Platform Adapters (Area 16) – Telegram, Discord, Web
- Ops AI (Area 14) – health, recovery, diagnostics
- Monitoring & Observability (Area 12)
- Config & Security (Area 13)
- User Personalisation (Area 10) – watchlist & risk only
- Capability Configuration (Area 15) – static registry + enables
System Guarantees (Inviolable)
Guarantee	Description
Deterministic signals	No randomness, no LLM influence. Same data + config → same output.
No LLM signal creation	LLM only explains structured outputs; never creates, modifies, or vetoes signals.
Validation mandatory gate	No signal is emitted without passing all hard validation checks (Area 4.4).
Feature isolation	Strategies consume only pre‑computed features (Area 3). They never access raw data or compute indicators directly.
Reproducibility	Every signal includes a frozen context_snapshot and a market_snapshot_hash.
Schema versioning	All outputs and configuration files carry a schema_version. Backward compatibility enforced.
No hidden state leakage	Signal state (Area 7) is managed independently; strategies are stateless.
Area 1: Market Data Layer
Purpose: Acquire, normalise, validate, and cache OHLCV data.

Data Sources (V1)
Forex & Gold: Oanda API (primary). TradingView TA library allowed for prototyping only.

Crypto (future): Binance REST + WebSocket.

Timeframes (V1)
Supported: 15m, 1h, 4h. Default exposed timeframe: 15m. Additional timeframes enabled via config.

Normalised Data Schema
json
￼
Copy
￼
Download
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
Staleness Thresholds
Timeframe	Max Age
15m	20 min
1h	70 min
4h	4h 15m
Reliability & Validation
Signals only from closed candles (is_closed == true).

Warmup: indicator validity requires sufficient history (e.g., EMA200 needs 200 candles).

Circuit breaker: 5 consecutive failures → disable source for 60 seconds.

Time synchronisation: use exchange/server time as authoritative reference.

Internal layers: Raw → Normalised → Validated → Strategy Engine.

Area 2: Market Context Engine
Purpose: Provide situational awareness independent of any strategy. Output is frozen per signal.

Context Dimensions
Dimension	Values	Source
Directional state	Bullish / Bearish / Neutral	EMA alignment + market structure + ADX
Volatility state	Low / Normal / High / Extreme	ATR(14) percentile over 50‑100 candles
Session	Asian, London Open, London, NY Open, NY, London‑NY Overlap, Off Hours, Weekend	Time‑based rules
Session transition	true / false	15‑30 min around session opens
News risk	{ active: bool, impact: "low" | "medium" | "high" }	Economic calendar API
Liquidity state	Low / Medium / High	Session + volatility + spread estimate
Market structure	Higher Highs, Lower Lows, Break of Structure, Consolidation, Expansion	Price action analysis
Context Output Example
json
￼
Copy
￼
Download
{
  "directional_state": "Bullish",
  "volatility_state": "Normal",
  "session": "London-NY Overlap",
  "session_transition": false,
  "news_risk": { "active": false, "impact": null },
  "liquidity_state": "Medium",
  "market_structure": "Higher Highs"
}
Area 3: Feature & Indicator Engine
Purpose: Compute technical features from validated OHLCV. Strategies consume only these pre‑computed features.

Architecture
Plugin‑based: indicators as modules (name, inputs, outputs, parameters).

Dependency graph to avoid redundant recomputation.

Feature metadata: value, lookback_required, source, is_valid.

Built‑in Indicators (V1)
Category	Indicators
Trend	EMA20, EMA50, EMA200, ADX, +DI, -DI
Momentum	RSI(14), MACD(12,26,9)
Volatility	ATR(14), Bollinger Bands (width)
Structure	Higher highs, lower lows, BOS, consolidation, expansion, swing high/low
Composite	RSI divergence (simple), EMA slope, ATR expansion/contraction
Excluded from V1
Ichimoku, Stochastic, most candle patterns, OBV, momentum acceleration.

Feature Output Example
json
￼
Copy
￼
Download
{
  "features": {
    "ema_20": { "value": 1.0842, "lookback_required": 20, "is_valid": true, "category": "trend" },
    "adx": { "value": 28, "lookback_required": 14, "is_valid": true, "category": "trend" },
    "rsi": { "value": 62, "lookback_required": 14, "is_valid": true, "category": "momentum" },
    "bos_up": { "value": false, "lookback_required": 20, "is_valid": true, "category": "structure" }
  },
  "timestamp": "2025-01-15T14:30:00Z"
}
Area 4: Strategy Engine (Core Signal Pipeline)
4.1 Strategy Registry
Hybrid: strategy logic in code, parameters in JSON configuration.

Multiple strategies can be defined; only those enabled: true run.

Activation conditions use Area 2 context.

Each strategy declares capabilities: supports_long/short, supported_assets, supported_timeframes.

Priority system resolves conflicts.

Strategy config example:

json
￼
Copy
￼
Download
{
  "name": "trend_following_v1",
  "version": "1.0.0",
  "enabled": true,
  "priority": 10,
  "compatible_contexts": {
    "directional_state": ["Bullish", "Bearish"],
    "volatility_state": ["Normal", "High"],
    "sessions": ["London", "NY", "London-NY Overlap"]
  },
  "capabilities": {
    "supports_long": true,
    "supports_short": true,
    "supported_assets": ["forex"],
    "supported_timeframes": ["15m", "1h", "4h"]
  },
  "minimum_confidence": 65,
  "weights": { ... }
}
4.2 Weighted Confluence Scoring
Direction detection (separate from scoring): Bullish/Bearish/Neutral using EMA alignment + market structure + higher timeframe trend.

Positive weights:

Factor	Weight
Higher timeframe alignment	25
Market structure (HH/LL, BOS)	20
EMA structure	15
ADX strength (>25)	15
RSI confirmation	10
Volatility quality (ATR percentile 30‑70%)	10
Session quality (London/NY overlap)	5
Negative penalties and contradictions:

Penalty	Value
Weak market structure	-15
RSI divergence (bearish in uptrend)	-10
Session transition window	-10
Volatility instability (ATR% >90 but not Extreme)	-10
Low liquidity	-10
Context multipliers:

London‑NY overlap: ×1.05

High volatility (70‑90%): ×0.9

Asian session: ×0.95

Veto conditions (hard block):

News risk = High

Volatility state = Extreme

Session = Off‑hours or Weekend

Spread > 2× typical

Score bands:

Score	Band	Action
0‑39	Weak	No signal
40‑59	Moderate	Signal only if config lowers threshold (not default)
60‑79	Strong	Signal issued
80‑100	Exceptional	Rare, high conviction
Raw confidence = weighted sum + penalties × multipliers, capped 0–100. Adjusted confidence = raw minus soft validation penalties (Area 4.4).

4.3 Trade Construction
Entry: Market only (next available price).

Stop loss: ATR‑based + structural override.

Volatility State	ATR Multiplier
Low	1.2×
Normal	1.5×
High	1.8×
Extreme	Signal vetoed
Final stop = max(ATR_stop, structural_support_resistance) (e.g., BUY stop below recent swing low).

Take profit: Fixed 1:2 (default); 1:1 and 1:3 optional via configuration.

Expiry:

Time‑based: 15m → 2h, 1h → 8h, 4h → 24h.

Context expiry: early expiry if directional state flips, news risk becomes High, or volatility becomes Extreme.

Position sizing hint (optional):

risk_amount = account_balance × risk_per_trade%

suggested_units = risk_amount / stop_distance_pips

Included only if user provides risk parameters; never implied as safe.

4.4 Signal Validation (Multi‑Layer Firewall)
All checks pass (or apply soft penalties) before emission.

Check	Type	Rule
Minimum confidence	Hard	Adjusted confidence ≥ 60
Spread	Hard	Current spread ≤ 2× typical
Cooldown	Hard	Per (symbol + direction + strategy). Adaptive: low vol 20m, normal 30m, high 45m
Uniqueness	Hard	Fingerprint (symbol, direction, strategy, entry proximity) not seen in last 2h
Session	Hard	Not off‑hours unless strategy explicitly allows
Volatility cap	Hard	Volatility state ≠ Extreme
News risk	Hard/Soft	High impact → hard reject; Medium → -15 confidence
Liquidity	Soft/Hard	Low → -15 confidence; Extremely low → hard reject
Structure validity	Hard	Stop loss respects recent swing high/low
Validation result object:

json
￼
Copy
￼
Download
{
  "valid": true,
  "adjusted_confidence": 74,
  "failed_checks": [],
  "warnings": ["low_liquidity"]
}
4.5 Structured Signal Output (Contract)
Every emitted signal conforms to this JSON schema.

json
￼
Copy
￼
Download
{
  "signal_id": "a1b2c3d4-...",
  "schema_version": "1.0",
  "generated_at": "2025-01-15T14:30:00Z",
  "expires_at": "2025-01-15T22:30:00Z",
  "symbol": "EURUSD",
  "timeframe": "1h",
  "strategy": { "name": "trend_following_v1", "version": "1.0.0", "priority": 10 },
  "direction": "BUY",
  "direction_source": {
    "method": "ema_structure + market_structure + htf_alignment",
    "confidence_alignment": "strong_consensus"
  },
  "confidence": { "raw": 78, "adjusted": 74, "band": "Strong" },
  "context_snapshot": { ... },
  "trade_construction": {
    "entry": { "type": "market", "price": 1.0842 },
    "stop_loss": { "price": 1.0824, "method": "ATR_DYNAMIC", "multiplier": 1.5, "distance_pips": 18 },
    "take_profit": { "price": 1.0890, "method": "FIXED_RR", "rr_ratio": 2.0, "distance_pips": 48 },
    "expiry": { "time": "2025-01-15T22:30:00Z", "reason": "timeframe_expiry" },
    "risk_metrics": { "stop_distance_pips": 18, "reward_pips": 36, "rr_ratio": 2.0 }
  },
  "reason_tags": ["ema_alignment", "adx_strength", "higher_highs", "rsi_confirmation"],
  "validation": { "valid": true, "adjusted_confidence": 74, "failed_checks": [], "warnings": [] },
  "market_snapshot_hash": "sha256(...)",
  "position_sizing_hint": { "risk_percent": 1.0, "suggested_units": 5555 }
}
Area 5: Signal Analytics & Backtesting
Purpose: Evaluate strategy performance historically.

Track every emitted signal; after expiry, record outcome (WIN/LOSS/BREAKEVEN), MFE, MAE, RR achieved.

Backtesting: replay historical OHLCV, re‑run full pipeline (Context → Features → Strategy → Scoring → Validation → Signal), compare direction vs future price move after N candles.

Metrics: win rate, profit factor, average RR, breakdown by strategy, symbol, timeframe, market regime.

Storage: signal_journal.json, backtest_results.json.

Area 6: Risk & Capital Logic
Purpose: Provide risk guidance (no execution).

Inputs from user configuration: account_size, risk_per_trade%, max_daily_risk%, max_open_positions.

Position sizing hint: risk_amount = account_size × risk_per_trade%, suggested_units = risk_amount / stop_distance_pips.

Portfolio heat: sum of risk_amount across active signals; block new signals if heat exceeds max_heat (e.g., 5%).

Area 7: Signal State Manager
Purpose: Lifecycle control and deduplication.

State types: CREATED → ACTIVE → EXPIRED | INVALIDATED | CLOSED.

Manage cooldown (per symbol + direction + strategy, volatility‑adaptive).

Deduplicate via fingerprint (symbol, direction, strategy, entry proximity).

Context invalidation: expire early if market context becomes incompatible.

Storage: active_signals.json, signal_history.json, cooldown_registry.json.

Area 9: LLM Communication Layer (User‑facing)
Purpose: Explain structured signals in natural language.

Never creates or modifies signals.

Input: full signal object (Area 4.5) + optional conversation history.

Output: plain language explanation, teaching, answers to /explain, /why, /teach.

Explanation style is configurable per chat (simple/detailed) – no automatic tier detection.

Area 10: User Personalisation (Minimal)
Purpose: Per‑user settings for delivery and risk.

No experience tiers, no profiles. Only:

Watchlist (symbols user wants to see)

Risk parameters (account size, risk per trade)

Notification preferences (quiet hours)

Storage: users/{user_id}/watchlist.json, users/{user_id}/risk_profile.json.

Area 11: Delivery Manager
Purpose: Route signals to the correct platform adapters and handle retries.

Receives structured signals (Area 4.5).

Looks up user’s platform registrations (Area 16).

Sends signal to each applicable platform adapter.

Implements retry queue (max 3 attempts, exponential backoff).

Central command router: interprets /signals, /watchlist, /status, /explain uniformly across all platforms.

Area 12: Monitoring & Observability
Purpose: System health and performance metrics.

Metrics: signals per hour, win rate, API latency, data fetch failures, validation rejection rate, cooldown hits.

Structured JSON logs; event types: signal_emitted, validation_failed, data_fetch_error, llm_response, user_command.

Health checks every 5 minutes: data pipeline alive, strategy engine responsive, validation stable.

Alerts (Telegram admin): repeated API failures, system crash, high rejection rate anomaly (>50% in 1h).

Area 13: Config & Security
Purpose: Central configuration and secrets management.

Single configuration file (YAML or JSON): strategies, weights, risk, cooldown, validation thresholds, API key references.

Secrets via environment variables (no plaintext keys in config).

Security rules:

Strategies cannot access filesystem outside their sandbox.

LLM cannot access secrets or raw market data.

User‑defined strategies run in restricted evaluation.

All config changes validated against schema before reload.

Schema versioning tied to signal version.

Area 14: AI Operations & Maintenance
Purpose: Autonomous system health, recovery, and diagnostics.

Responsible for:

Monitoring all subsystems (data, features, strategies, validation, delivery).

Auto‑restart failed components.

Switching data sources on failure (circuit breaker + failover).

Detecting performance anomalies (e.g., validation rejection spike).

Suggesting configuration changes via structured proposals (admin approval required for permanent changes).

Never influences signal generation or trading logic.

Operational tools:

Read (unrestricted): system_status, get_logs, run_backtest

Write (auto + notify): restart_component, switch_data_source

Write (approval required): adjust_config, suggest_optimization, apply_update

V1 capabilities: health monitoring, auto‑restart, failover, anomaly detection, admin alerting.

Area 15: Capability Configuration Layer (Simplified)
Purpose: Static registry + explicit enable/disable of system capabilities. No profiles, no user tiers, no automatic grouping.

15.1 Indicator Registry
Each indicator is defined as:

json
￼
Copy
￼
Download
{
  "name": "RSI",
  "enabled_by_default": true,
  "supported_timeframes": ["15m", "1h", "4h"],
  "inputs": ["close"]
}
Users can enable/disable indicators via configuration – no AI inference.

15.2 Timeframe Control
Default exposed timeframe = 15m. Additional timeframes enabled by explicit config:

json
￼
Copy
￼
Download
"timeframes_enabled": ["15m", "1h"]
15.3 Symbol Control
json
￼
Copy
￼
Download
"symbols_enabled": ["EURUSD", "GBPUSD", "XAUUSD"]
15.4 Capability Snapshot (derived from config)
json
￼
Copy
￼
Download
{
  "enabled_indicators": ["RSI", "EMA", "MACD", "ATR"],
  "enabled_timeframes": ["15m"],
  "enabled_symbols": ["EURUSD", "XAUUSD"],
  "enabled_strategies": ["trend_following_v1"]
}
15.5 Expansion Model
Users add or remove items from the above lists. No hidden abstraction.

15.6 Relationship
Area 3 consumes only enabled indicators.

Area 4 uses only enabled strategies.

Area 11 respects enabled symbols.

Area 16: Messaging Platforms (Presentation Layer)
Core principle: Platforms are dumb pipes – only receive commands, display signals, forward responses. No business logic in adapters.

16.1 V1 Platforms
Platform	Status	Notes
Telegram	✅ MUST HAVE	Full interactive bot, inline buttons, commands
Discord	✅ OPTIONAL V1	Read‑only signal feed, basic commands
Web Dashboard	✅ INTERNAL TOOL	Debug and admin view (served by Gateway)
Excluded from V1: WhatsApp, Signal, Slack, iMessage (V1.1+ plugins).

16.2 Architecture
text
￼
Copy
￼
Download
Core Engine (Areas 1–14)
       ↓
Structured Signal (4.5)
       ↓
Delivery Manager (Area 11)
       ↓
Platform Adapters (Telegram, Discord, Web)
       ↓
Users
16.3 Adapter Responsibilities
Input: Normalise messages to { platform, user_id, message, timestamp }

Output: Render signal + buttons, send via platform API

No signal calculation, strategy logic, or validation.

16.4 Command Router (Unified)
Commands /signals, /watchlist, /status, /explain <id> work identically across all platforms. Centralised in Area 11.

16.5 Identity Model (Simplified)
V1: Separate internal user ID per platform. No automatic cross‑platform merging.

Manual linking possible via admin command, but not required.

16.6 Message Flow
Outgoing: Signal → Delivery Manager → Adapter → User

Incoming: User → Adapter → Command Router → Core → Response

16.7 Platform Adapter Examples
Telegram:

Uses python-telegram-bot or similar.

Renders signal with Markdown, inline buttons.

Discord:

Uses discord.py.

Renders signal as embed, buttons for interaction.

Web Dashboard:

Simple WebSocket client served by Gateway.

Shows raw JSON for debugging and admin controls.

Final Scope Summary
Area	Title
1	Market Data Layer
2	Market Context Engine
3	Feature & Indicator Engine
4	Strategy Engine (4.1‑4.5)
5	Signal Analytics & Backtesting
6	Risk & Capital Logic
7	Signal State Manager
9	LLM Communication Layer
10	User Personalisation (minimal)
11	Delivery Manager
12	Monitoring & Observability
13	Config & Security
14	AI Operations & Maintenance
15	Capability Configuration Layer
16	Messaging Platforms (Telegram, Discord, Web)
Version: 1.0
Date: 2025-01-29
Status: Final architecture specification for V1 implementation

End of Architecture Specification
