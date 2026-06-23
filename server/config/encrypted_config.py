import os
from typing import Any, Dict

from utils import setup_logger

logger = setup_logger(__name__)


# Environment-to-Config Mapping

def load_secrets_from_env() -> Dict[str, Any]:
    """
    Load all secrets from environment variables.

    Returns a dict of secret keys ready to be merged into the config system.
    """
    env_mapping = {
        "groq_api_key":          "GROQ_API_KEY",
        "local_api_key":         "LOCAL_LLM_API_KEY",
        "local_api_base":        "LOCAL_LLM_API_BASE_URL",
        "bedrock_credentials":   "BEDROCK_CREDENTIALS",
        "admin_sync_code":       "ADMIN_SYNC_CODE",
    }

    secrets: Dict[str, Any] = {}

    for config_key, env_var in env_mapping.items():
        value = os.getenv(env_var, "").strip()
        if value:
            secrets[config_key] = value

    if secrets:
        logger.info("Secrets loaded from environment variables")
    else:
        logger.warning("No secrets found in environment variables")

    return secrets

__all__ = [
    "load_secrets_from_env",
]
