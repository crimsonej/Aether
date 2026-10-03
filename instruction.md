Purpose of This File
This file is NOT the architecture specification.

This file exists to instruct any coding agent, AI engineer, autonomous coding system, or developer on:

how to build Aether correctly,

how to behave while working on the codebase,

what MUST NEVER be done,

implementation philosophy,

development priorities,

project constraints,

architectural boundaries,

coding standards,

operational expectations.

The architecture.md file defines WHAT Aether is. This instruction.md defines HOW an implementation agent must behave while building it.

The coding agent must treat this document as operational law.

1. Core Identity of Aether
Aether is a deterministic trading intelligence platform.

It is NOT:

a broker,

an auto-trader,

a gambling system,

an AI that invents trades,

an unrestricted experiment sandbox.

Aether is:

a structured signal engine,

a market analysis framework,

a deterministic decision pipeline,

a reproducible analytics system,

a messaging-driven trading intelligence assistant.

The system's primary objective is:

Produce reproducible, explainable, structured trading signals from deterministic market logic.

Everything in the project must support this goal.

2. Absolute Non-Negotiable Rules
These rules override everything else.

2.1 No AI-Generated Signals
LLMs MUST NEVER:

generate BUY/SELL/HOLD decisions,

modify confidence scores,

alter stop losses,

alter take profits,

override validation,

bypass risk controls,

create indicators,

fabricate market conditions.

LLMs are explanation systems only.

Signal generation is fully deterministic.

2.2 No Trade Execution
Aether MUST NEVER:

place trades,

connect to broker execution endpoints,

manage accounts,

submit orders,

auto-scale positions.

The system is signal-only.

Any attempt to add auto-trading functionality must be rejected unless explicitly requested by the project owner.

2.3 Deterministic Outputs Only
Given:

identical market data,

identical configuration,

identical strategy version,

identical timeframe,

Aether MUST produce the exact same signal output.

No randomness. No probabilistic generation. No hidden state. No temperature-based inference.

2.4 Validation Cannot Be Bypassed
Every signal MUST pass the validation firewall.

No module may:

emit signals directly,

skip validation,

bypass cooldown,

bypass uniqueness checks,

bypass spread checks.

Validation is mandatory.

2.5 Strategies Are Sandboxed
Strategies:

cannot fetch market data directly,

cannot access secrets,

cannot access filesystem arbitrarily,

cannot modify global state,

cannot call external APIs.

Strategies only consume:

context,

features,

strategy config.

3. Primary Development Philosophy
The coding agent must optimize for:

reliability,

determinism,

observability,

maintainability,

modularity,

reproducibility,

clarity.

NOT:

unnecessary complexity,

fancy abstractions,

overengineering,

trendy infrastructure,

premature optimization.

Simple systems are preferred unless complexity is justified.

4. What the Coding Agent MUST Understand
Aether is intended to evolve.

The V1 system is intentionally constrained.

The coding agent MUST:

build extensible architecture,

avoid hardcoded assumptions,

avoid tightly coupled modules,

preserve separation between areas.

However:

The coding agent MUST NOT add speculative features that were never requested.

5. Required Development Order
The coding agent MUST follow this order.

Do not skip ahead.

Phase 0 — Foundation
Implement:

project structure,

configuration loader,

logging,

environment management,

schema validation,

startup bootstrap.

No strategy work before this phase is stable.

Phase 1 — Market Data Layer
Implement:

data fetching,

normalization,

candle validation,

staleness detection,

circuit breakers,

timeframe management,

caching.

No indicators before normalized data is verified.

Phase 2 — Market Context Engine
Implement:

directional state,

volatility state,

session detection,

liquidity state,

news risk,

structure state.

Context must be frozen per signal.

Phase 3 — Feature & Indicator Engine
Implement indicators only after:

market data,

context,

warmup handling,

are stable.

Indicators must:

expose metadata,

expose validity,

expose lookback requirements.

Phase 4 — Strategy Engine
Implement:

strategy registry,

direction detection,

scoring,

trade construction,

validation,

structured output.

Validation firewall must be completed before any delivery integrations.

Phase 5 — State, Analytics & Risk
Implement:

signal state manager,

cooldown system,

deduplication,

backtesting,

analytics,

portfolio heat.

Phase 6 — Delivery & LLM
Implement:

Telegram integration,

command router,

explanation layer,

retry queues,

formatting.

No LLM integration before deterministic pipeline is stable.

Phase 7 — Monitoring & Ops
Implement:

health checks,

observability,

ops AI,

auto-recovery,

diagnostics,

failover systems.

6. Mandatory Architectural Boundaries
6.1 Data Layer Isolation
Only Area 1 may:

fetch market data,

normalize candles,

manage providers.

No other area may directly access exchanges.

6.2 Context Isolation
Area 2 is strategy-independent.

Strategies cannot modify context.

Context is immutable once frozen.

6.3 Feature Isolation
Indicators are computed centrally.

Strategies MUST NOT compute indicators internally.

This prevents:

duplicated calculations,

inconsistent logic,

hidden feature drift.

6.4 Delivery Isolation
Telegram/Discord/Web adapters are presentation-only.

Adapters cannot:

compute signals,

modify validation,

alter confidence,

store business logic.

6.5 LLM Isolation
The LLM layer:

cannot access secrets,

cannot access raw market feeds,

cannot mutate signals,

cannot bypass validation.

The LLM is a read-only explanation layer.

7. Required Technical Behaviour
7.1 Logging
Everything important must be logged.

Use structured JSON logs.

Every major event must include:

timestamp,

subsystem,

symbol,

timeframe,

correlation ID,

event type.

7.2 Error Handling
Never silently fail.

Every exception must:

be logged,

include context,

propagate correctly,

avoid crashing unrelated subsystems.

7.3 Type Safety
All public functions must use:

type hints,

explicit return types,

dataclasses or typed schemas where appropriate.

7.4 Config Validation
All configuration:

validated at startup,

schema checked,

version checked.

Invalid config must fail fast.

7.5 Testing Requirements
Every public module requires:

unit tests,

deterministic replay tests,

validation tests.

Backtesting must be reproducible.

8. Complexity Rules
The coding agent MUST actively avoid unnecessary complexity.

DO NOT introduce:

Kubernetes,

microservices,

distributed queues,

Redis,

Kafka,

Celery,

Docker orchestration,

event sourcing,

plugin marketplaces,

AI orchestration frameworks,

vector databases,

graph databases,

multi-agent systems,

auto-generated infrastructure.

Unless explicitly requested.

V1 should remain:

local-first,

understandable,

inspectable,

debuggable.

9. Capability Philosophy
Aether supports expansion without changing architecture.

The coding agent must build:

indicator registries,

strategy registries,

timeframe registries,

symbol registries.

But:

The system should expose only explicitly enabled capabilities.

No automatic AI-driven capability discovery.

No hidden features.

No automatic indicator activation.

10. Indicator Expansion Rules
The owner may later add:

new indicators,

new symbols,

new timeframes,

new strategies.

The coding agent must:

make this easy,

avoid refactoring requirements,

avoid hardcoded lists.

However:

New indicators must remain disabled by default unless enabled in configuration.

Indicators must register:

name,

category,

supported timeframes,

dependencies,

lookback requirements,

output schema.

11. Strategy Rules
Strategies are modular.

Each strategy must:

declare compatible contexts,

declare supported timeframes,

declare supported symbols,

expose version,

expose weights,

expose minimum confidence.

Strategies must return:

direction,

raw confidence,

reason tags,

trade construction proposal.

Strategies must NEVER:

send messages,

access Telegram,

write files directly,

fetch data.

12. Ops AI Rules
The operations AI exists to maintain infrastructure.

NOT trading logic.

Ops AI may:

restart components,

detect failures,

switch providers,

analyze logs,

run diagnostics,

suggest optimizations.

Ops AI may NOT:

invent strategies,

change signals directly,

bypass validation,

change risk logic autonomously.

Permanent config changes require approval.

13. Messaging Platform Rules
V1 priority:

Telegram,

Web dashboard,

Discord.

All platform adapters must:

normalize incoming messages,

route commands centrally,

render structured outputs,

support retry queues.

Platform adapters must remain thin.

14. Required File Structure
The coding agent should generally follow this structure:

/aether
  /core
  /adapters
  /config
  /scripts
  /tests
  /data
  /logs
Internal organization may evolve slightly if architecture remains clean.

15. Documentation Rules
Every important subsystem must contain:

docstrings,

inline rationale for complex logic,

README sections where needed.

Complex algorithms must explain:

why they exist,

inputs,

outputs,

edge cases,

failure modes.

16. What the Coding Agent MUST NOT Do
Do NOT:

invent missing requirements,

silently change architecture,

add AI-generated signal logic,

create hidden dependencies,

tightly couple modules,

bypass validation,

hardcode secrets,

overabstract simple systems,

introduce experimental frameworks,

create unnecessary async complexity,

add unsupported indicators,

add unsupported platforms,

implement auto-trading.

If uncertain:

ASK.

Do not guess.

17. Success Definition
The project is considered successful when:

the deterministic pipeline operates end-to-end,



Aether Implementation Instructions for Coding Agents
This document is the single source of truth for implementing the Aether trading intelligence platform. Any coding agent (Claude Code, GPT, etc.) must follow these rules and guidelines exactly.

Do not deviate from this specification. Do not add features not described here. Do not remove required components.

Core Principles (Inviolable)
Deterministic signals only – No randomness, no LLM influence on signal generation.

LLM never creates signals – LLM layer (Area 9) only explains structured outputs.

No trade execution – Aether is a signal bot, never connects to brokers.

Validation is mandatory – No signal emitted without passing Area 4.4 checks.

Platforms are dumb pipes – No business logic in Telegram/Discord adapters.

Configuration over code – Strategies, weights, thresholds are in config files, not hardcoded.

No user profiles or tiers – Only watchlists and risk parameters per user. No experience levels.

Architecture Overview (Areas 1–16)
The system is built as a deterministic pipeline:

text
￼
Copy
￼
Download
Data (1) → Context (2) → Features (3) → Strategy (4.1) → Scoring (4.2) → Trade Construction (4.3) → Validation (4.4) → Signal (4.5)
Supporting areas: Analytics (5), Risk (6), State Manager (7), LLM Explanation (9), User Settings (10), Delivery (11), Monitoring (12), Config (13), Ops AI (14), Capability Registry (15), Platform Adapters (16).

All code must respect area boundaries. Cross‑layer leakage (e.g., strategy directly calling an API) is forbidden.

Implementation Order (Mandatory Sequence)
Do not jump ahead. Complete each phase before starting the next.

Phase 0: Foundation
Set up project structure: aether/ with core/, adapters/, config/, scripts/.

Implement configuration loader (Area 13) – JSON/YAML, environment variables for secrets.

Implement logging (Area 12) – structured JSON logs.

Phase 1: Market Data Layer (Area 1)
Oanda REST client for forex (EURUSD, GBPUSD, USDJPY, AUDUSD, USDCHF, USDCAD, XAUUSD).

Data normalisation to schema in spec.

Staleness checks, circuit breaker, warmup validation.

Unit tests with mocked Oanda responses.

Phase 2: Market Context Engine (Area 2)
Directional state (EMA + structure + ADX).

Volatility state (ATR percentile).

Session detection (time‑based).

News risk (lightweight calendar API or static JSON for V1).

Liquidity state (session + volatility).

Market structure (HH/LL, BOS, consolidation, expansion).

Output frozen context object per signal.

Phase 3: Feature & Indicator Engine (Area 3)
Compute EMA20/50/200, ADX, RSI, MACD, ATR, Bollinger width.

Structure features: HH/LL, BOS, consolidation, expansion.

Composite: RSI divergence, EMA slope, ATR expansion/contraction.

Dependency graph to avoid recomputation.

Feature metadata (value, lookback_required, is_valid).

Phase 4: Strategy Engine (Areas 4.1–4.5)
Strategy registry with JSON configs. Only trend_following_v1 enabled in V1.

Direction detection (separate from scoring).

Weighted scoring with positive/negative weights, context multipliers, vetoes.

Trade construction: market entry, ATR stop with structural override, fixed 1:2 TP, expiry.

Validation firewall (all 9 checks).

Structured signal output (JSON conforming to Area 4.5 schema).

Phase 5: Supporting Engines
Signal State Manager (Area 7) – JSON storage, cooldown, expiry, deduplication.

Risk & Capital Logic (Area 6) – position sizing hint, portfolio heat.

Signal Analytics & Backtesting (Area 5) – journal, replay backtester.

Phase 6: LLM & Delivery
LLM Communication Layer (Area 9) – explain signal object, never modify. Use OpenAI or Anthropic API.

Delivery Manager (Area 11) – route signals to adapters, retry queue.

Platform Adapters (Area 16) – Telegram (full), Discord (optional), Web dashboard.

Phase 7: Operations & Configuration
Ops AI (Area 14) – health checks, auto‑restart, failover, anomaly detection, structured suggestions.

Capability Configuration Layer (Area 15) – static registry for indicators, timeframes, symbols, strategies.

Phase 8: Testing & Documentation
Unit tests for each area (mocked dependencies).

Integration test: full pipeline with fake Oanda data.

Backtest validation against historical data.

Write README.md with setup, configuration, and usage.

Technical Stack (No Negotiation)
Component	Choice	Reason
Language	Python 3.13	Rich data ecosystem, TA libraries, simple deployment
Data fetching	requests + Oanda REST API	Stable, official
Indicators	pandas-ta or manual numpy	Lightweight, deterministic
LLM	OpenAI API (gpt-3.5-turbo) or Anthropic	For explanation only
Telegram	python-telegram-bot v20+	Stable, async
Discord	discord.py	Optional
Scheduling	apscheduler or system cron	Simple
Config	pyyaml + python-dotenv	Human‑editable
Logging	structlog	JSON logs
Backtesting	Custom replay engine	Simple, transparent
Do not introduce unnecessary frameworks (no Celery, no Redis, no Docker for V1 unless requested).

File Structure (Generated by Agent)
text
￼
Copy
￼
Download
aether/
├── core/
│   ├── data/
│   │   ├── oanda_client.py
│   │   ├── normalizer.py
│   │   └── validator.py
│   ├── context/
│   │   ├── directional.py
│   │   ├── volatility.py
│   │   ├── session.py
│   │   ├── news.py
│   │   └── structure.py
│   ├── features/
│   │   ├── indicator_registry.py
│   │   ├── trend.py
│   │   ├── momentum.py
│   │   ├── volatility.py
│   │   └── composite.py
│   ├── strategy/
│   │   ├── registry.py
│   │   ├── trend_following.py
│   │   ├── scoring.py
│   │   ├── trade_construction.py
│   │   └── validation.py
│   ├── signal/
│   │   ├── state_manager.py
│   │   ├── journal.py
│   │   └── backtest.py
│   ├── risk/
│   │   └── capital.py
│   ├── llm/
│   │   └── explainer.py
│   ├── delivery/
│   │   ├── manager.py
│   │   └── command_router.py
│   ├── ops/
│   │   └── health.py
│   └── config/
│       ├── loader.py
│       └── schema.yaml
├── adapters/
│   ├── telegram/
│   ├── discord/         (optional)
│   └── web/
├── config/
│   ├── aether.yaml
│   ├── strategies/
│   │   └── trend_following_v1.yaml
│   └── indicators.yaml
├── scripts/
│   ├── run.py
│   ├── backtest.py
│   └── doctor.py
├── tests/
├── data/                (JSON state files)
└── README.md
Coding Rules for the Agent
No code outside specified areas – Do not add new modules unless explicitly required by an area.

Every function must have a docstring – Explain what it does, inputs, outputs.

Use type hints – All function signatures typed.

Write unit tests for each component – At least one test per public function.

Log everything – Use structlog, include correlation IDs.

Never hardcode credentials – Always read from environment or secrets file.

Never let LLM write signal‑generating code – The only LLM code allowed is in core/llm/explainer.py.

Respect the order of operations – Do not skip to later phases.

If something is unclear, ask – Do not guess or invent missing parts.

Configuration File Example (aether.yaml)
yaml
￼
Copy
￼
Download
schema_version: 1

data:
  oanda:
    api_key_env: OANDA_API_KEY
    account_id_env: OANDA_ACCOUNT_ID
    base_url: "https://api-fxtrade.oanda.com"
  fallback: null

context:
  adx_threshold: 22
  atr_period: 14
  atr_percentiles: [20, 70, 90]

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

strategies:
  - name: trend_following_v1
    enabled: true
    config_path: config/strategies/trend_following_v1.yaml

validation:
  min_confidence: 60
  max_spread_multiplier: 2.0
  cooldown:
    low_volatility: 20
    normal_volatility: 30
    high_volatility: 45

delivery:
  telegram:
    token_env: TELEGRAM_BOT_TOKEN
    chat_id_env: TELEGRAM_CHAT_ID
  discord:
    enabled: false
  web:
    enabled: true
    port: 8080

ops:
  health_check_interval: 300
  auto_restart: true
  anomaly_detection: true
Success Criteria
The implementation is complete when:

python scripts/run.py starts the bot, connects to Oanda, computes features, generates signals.

A signal appears on Telegram when conditions are met (test with mocked data first).

python scripts/doctor.py passes all checks (data source, indicators, config, LLM key).

python scripts/backtest.py --symbol EURUSD --start 2024-01-01 produces a report.

The LLM explanation command /explain <signal_id> returns plain English text that matches the signal.

No runtime errors for 24 hours in simulation mode.

Final Instruction
Do not deviate from this document. If you are uncertain about any detail, re‑read the area specifications above. Do not add features like "user experience tiers", "automatic strategy tuning", "WhatsApp integration", or "portfolio optimisation". Stick to the V1 scope exactly.

When implementation is complete, output a summary of created files and any configuration required. Do not generate extraneous documentation.

Now begin implementation in the order specified.
