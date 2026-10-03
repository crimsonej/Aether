# Aether: Deterministic Trading Intelligence Platform

Aether is a production-grade trading intelligence platform designed for reliability, clarity, and performance. It moves beyond "a collection of scripts" to a cohesive operational system.

## Quick Start

1. **Clone & Install**:
   ```bash
   git clone <repo-url>
   cd aether
   pip install -e .
   ```

2. **Onboard**:
   ```bash
   aether onboard
   ```
   *This validates dependencies, initializes directories, and creates a `.env` template.*

3. **Configure**:
   Edit the generated `.env` file with your Oanda API credentials and Telegram bot token.

4. **Start the Platform**:
   ```bash
   aether start
   ```

## Key Features

- **Operational Gateway**: Central orchestration of data, strategy, and delivery layers.
- **Native CLI**: Full control via `aether` command (status, logs, doctor, config).
- **Web Dashboard**: Real-time health monitoring and system diagnostics.
- **Professional Telegram UX**: Structured, actionable trade signals with Markdown formatting.
- **Production Hardened**: Built-in health heartbeats, auto-recovery, and graceful shutdown.

## CLI Commands

| Command | Description |
| :--- | :--- |
| `aether onboard` | Initialize the workspace and environment. |
| `aether gateway` | Launch the standalone Aether operational gateway. |
| `aether start` | Launch the platform in dev/paper/production mode. |
| `aether stop` | Gracefully shut down all services. |
| `aether status` | View system health and component status. |
| `aether doctor` | Run comprehensive platform diagnostics. |
| `aether logs` | Stream system logs for troubleshooting. |

## Gateway Endpoints

- **Dashboard**: `http://localhost:18791/`
- **Health**: `http://localhost:18791/health`
- **System Status**: `http://localhost:18791/system/status`
- **WS Feed**: `ws://localhost:18791/ws`

## Architecture Overview

- **Core**: Deterministic trading intelligence engine.
- **Gateway**: Orchestrates service lifecycles and provides the management API.
- **Adapters**: Connects to external services (Telegram, Discord, Oanda) with professional-grade formatting.
- **Ops**: Health monitoring and runtime watchdog services.
