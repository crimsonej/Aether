import sys
import os
from pathlib import Path

# Add project root to sys.path to allow imports from aether.*
project_root = Path(__file__).resolve().parents[2]
sys.path.append(str(project_root))

from aether.core.utils.logger import setup_logging, logger
from aether.core.config.loader import load_config, get_secret

def bootstrap():
    """
    Bootstraps the Aether platform.
    """
    print("--- Aether Trading Intelligence Platform ---")

    # 1. Setup Logging
    setup_logging()
    logger.info("system_bootstrap_started")

    try:
        # 2. Load Config
        config = load_config()

        # 3. Verify Essential Secrets (Example: Oanda API Key)
        # In a real scenario, we'd iterate through all required env vars in config
        oanda_key = config.data.oanda.api_key_env
        # We don't call get_secret here yet unless we have a .env file,
        # but we verify the loader works.

        logger.info("bootstrap_completed_successfully", version=config.schema_version)
        print("Aether initialized successfully.")

    except Exception as e:
        logger.critical("bootstrap_failed", error=str(e))
        print(f"Critical error during bootstrap: {e}")
        sys.exit(1)

if __name__ == "__main__":
    bootstrap()
