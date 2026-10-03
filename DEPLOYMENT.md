# Deployment Guide

## Local Machine Deployment
1. **Prerequisites**
   - Python 3.13+ (or the version used in `requirements.txt`).
   - `git` and `virtualenv`.
2. **Clone and install** (see README).
3. **Run onboarding** to generate a valid `config/aether.yaml`.
4. **Start the gateway**:
   ```bash
   aether gateway
   ```
   The service listens on `0.0.0.0:8000` (FastAPI) and on the configured webhook port for adapters.

## VPS / Cloud Deployment
1. **Choose a VM** – at least 2 vCPU, 4 GB RAM, Ubuntu 22.04 LTS.
2. **Create a non‑root user** (e.g., `aether`).
3. **Copy the repository** (via Git or tarball) to `/opt/aether`.
4. **Create a persistent volume** for data and config:
   ```bash
   mkdir -p /var/lib/aether/data /var/lib/aether/config
   cp config/aether.yaml /var/lib/aether/config/
   ```
5. **Install dependencies** inside a virtualenv owned by the `aether` user.
6. **Run as a systemd service** (example unit file `aether.service` provided in the repo):
   ```ini
   [Unit]
   Description=Aether Trading Platform
   After=network.target

   [Service]
   User=aether
   Group=aether
   WorkingDirectory=/opt/aether
   EnvironmentFile=/var/lib/aether/config/.env
   ExecStart=/opt/aether/.venv/bin/aether gateway
   Restart=on-failure

   [Install]
   WantedBy=multi-user.target
   ```
   Enable and start:
   ```bash
   sudo systemctl enable aether
   sudo systemctl start aether
   ```
7. **Logs** are written to `logs/` inside the data volume; view via `journalctl -u aether`.

## Environment Variables
All secrets are read from environment variables referenced in `config/aether.yaml` (e.g., `OANDA_API_KEY`, `TELEGRAM_BOT_TOKEN`).  Provide them via a `.env` file in the config volume or through your orchestration platform.

## Health Checks
The container includes a Docker `HEALTHCHECK` that runs `aether doctor` every 30 seconds.  It returns `0` on success and `1` otherwise, allowing orchestration tools (Docker, Kubernetes) to restart unhealthy instances.

## Backup Strategy
- **Configuration** – keep `config/aether.yaml` and the `.env` file under version control or in a secure backup location.
- **Data** – the `data/` directory contains watchlists, signal journals and cooldown registries.  Snapshot the volume daily (e.g., using `rsync` or your cloud provider's snapshot feature).
- **Logs** – rotate logs via `logrotate` or the container’s built‑in rotation.
