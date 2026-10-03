import os
import yaml
from pathlib import Path
from typing import Any, Dict
from dotenv import load_dotenv
from aether.core.config.schema import AetherConfig
from aether.core.utils.logger import logger

def load_config(config_path: str = "aether/config/aether.yaml") -> AetherConfig:
    """
    Loads and validates the Aether platform configuration.

    Args:
        config_path (str): Path to the YAML configuration file.

    Returns:
        AetherConfig: The validated configuration object.
    """
    # Load environment variables from .env if present
    load_dotenv()

    path = Path(config_path)
    if not path.exists():
        logger.error("config_file_missing", path=str(path))
        raise FileNotFoundError(f"Configuration file not found: {config_path}")

    try:
        with open(path, 'r') as f:
            raw_config = yaml.safe_load(f)

        # Validate against Pydantic schema
        config = AetherConfig(**raw_config)
        logger.info("config_loaded_successfully", version=config.schema_version)
        return config
    except Exception as e:
        logger.error("config_loading_failed", error=str(e))
        raise

def get_secret(env_var_name: str) -> str:
    """
    Retrieves a secret from environment variables.

    Args:
        env_var_name (str): The name of the environment variable.

    Returns:
        str: The secret value.
    """
    value = os.getenv(env_var_name)
    if not value:
        logger.error("secret_missing", env_var=env_var_name)
        raise EnvironmentError(f"Missing required environment variable: {env_var_name}")
    return value
