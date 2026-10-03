# Aether

## Overview
Aether is a deterministic trading‑intelligence platform that ingests market data from a configurable provider chain, normalises it, runs a feature‑engine pipeline, and emits trade signals.  The system is built around a lightweight, async‐first architecture with clearly separated layers: data providers, context engine, feature engine, strategy engine and delivery adapters (Telegram, Discord, Web, WhatsApp).

## Architecture Summary
- **Data Layer** – `DataProviderManager` aggregates multiple market data providers (Yahoo, AlphaVantage, TwelveData, Oanda) and performs fail‑over.
- **Context Engine** – maintains per‑symbol market context (prices, timestamps).
- **Feature Engine** – computes technical indicators (EMA, ADX, RSI, MACD, etc.).
- **Strategy Engine** – evaluates a deterministic strategy (`trend_following_v1`) and produces a `Signal` object.
- **Delivery Adapters** – send signals to external channels (Telegram, Discord, Web UI, WhatsApp).  Adapters are loaded by `AdapterRegistry` and respect the `delivery` section of the configuration.
- **Configuration Store** – `ConfigStore` provides a single source of truth (`config/aether.yaml`).

## Installation
```bash
# Clone the repository
git clone <repo‑url>
cd aether

# Install dependencies in a virtual environment
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## First Run (Onboarding)
```bash
# Initialise configuration interactively
aether onboard
```
The wizard:
1. Creates a non‑empty default watchlist.
2. Writes `config/aether.yaml` with the provider chain `Yahoo → AlphaVantage → TwelveData → Oanda`.
3. Generates a minimal strategy config.
4. Skips LLM and Telegram setup for RC1 (can be added later).
5. Runs a health check (`aether doctor`).

## Running Aether
```bash
# Start the gateway (REST API, Web UI, signal bus)
aether gateway
```
Or use the placeholder start command:
```bash
aether start --mode dev
```
The service will:
- Connect to the enabled data providers.
- Publish market updates on the internal `EventBus`.
- Emit signals when the strategy conditions are met.

## Troubleshooting
- **Import errors** – ensure `PYTHONPATH` includes the project root or install the package with `pip install -e .`.
- **Missing env vars** – see `CONFIGURATION.md` for required variables (`OANDA_API_KEY`, `TELEGRAM_BOT_TOKEN`, etc.).
- **Health check failures** – run `aether doctor` for a detailed diagnostic.
