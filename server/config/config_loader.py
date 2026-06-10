"""
Production-grade configuration loader.

Features:
- Singleton cached config manager
- Dot notation access
- Deep merge with defaults
- Runtime reload support
- Safe fallback handling
- Production-safe logging
- Dynamic prompt configuration support
- Dynamic workflow template support
- Runtime cache support
- Dynamic pattern support
- Encrypted secrets integration
- Environment-specific configuration
"""

import json
import os
from copy import deepcopy
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Optional

from utils import deep_merge_dicts, setup_logger

logger = setup_logger(__name__)


# ─────────────────────────────────────────────────────────────
# Paths
# ─────────────────────────────────────────────────────────────

BASE_DIR = Path(__file__).resolve().parent

CONFIG_PATH = BASE_DIR / "config.json"

PATTERN_EXCEL_PATH = (
    BASE_DIR / "dynamic_step_patterns.xlsx"
)


# ─────────────────────────────────────────────────────────────
# Default Configuration
# ─────────────────────────────────────────────────────────────

DEFAULT_CONFIG: Dict[str, Any] = {
    "app": {
        "name": "Veeva Vault Step Generator",
        "version": "3.0.0",
        "debug": False,
        "environment": "production",
    },

    "model": {
        "name": "llama-3.3-70b-versatile",
        "temperature": 0.12,
        "max_tokens": 180,
        "timeout": 120,
        "streaming_enabled": True,
    },

    "generation": {
        "use_multi_candidate": True,
        "num_candidates": 3,
        "temperature_variance": 0.05,
        "max_temperature": 0.4,

        "max_retries": 5,
        "retry_backoff_base": 2,
        "default_wait_time": 5,

        "enable_template_generation": True,
        "enable_validation": True,
        "enable_candidate_scoring": True,

        "fallback_to_original": True,

        "max_parallel_generations": 5,
        "max_session_cache": 100,
    },

    "rag": {
        "enabled": True,

        "top_k": 5,
        "diversity_weight": 0.3,

        "bm25_k1": 1.5,
        "bm25_b": 0.75,

        "similarity_threshold": 0.7,

        "enable_workflow_boost": True,
        "enable_label_similarity": True,
        "enable_value_similarity": True,

        "candidate_pool_multiplier": 3,
    },

    "validation": {
        "enabled": True,

        "min_confidence_threshold": 0.55,
        "template_confidence_threshold": 0.80,

        "min_output_length": 5,

        "min_word_count": 2,
        "max_word_count": 40,

        "max_sentences": 2,

        "require_period": False,
        "require_action_verb": True,

        "strict_forbidden_word_check": True,

        "allow_partial_label_match": True,
    },

    "context": {
        "previous_steps_count": 5,
        "max_rag_examples": 5,

        "enable_context_summary": True,
        "enable_previous_steps_context": True,
    },

    "actions": {
        "click": [
            "Click",
            "Click on",
            "Press",
            "Tap",
        ],

        "enter": [
            "Enter",
            "Type",
            "Input",
            "Fill in",
        ],

        "select": [
            "Select",
            "Choose",
            "Pick",
        ],

        "verify": [
            "Verify",
            "Check",
            "Confirm",
            "Ensure",
            "Validate",
        ],

        "navigate": [
            "Navigate to",
            "Go to",
            "Open",
            "Navigate Back",
            "Reload",
        ],

        "memory": [
            "Fetch",
            "Store",
        ],

        "generate": [
            "Generate",
            "Create",
        ],

        "upload": [
            "Upload",
        ],

        "capture": [
            "Capture",
        ],
    },

    "prompting": {
        "forbidden_words": [
            "span",
            "div",
            "path",
            "svg",
            "rect",
            "li ",
            " li",
            "undefined",
            "null",
            "element type",
            "node",
            "dom",
            "html",
            "class",
            "id=",
        ]
    },

    "logging": {
        "level": "INFO",
        "format": (
            "%(asctime)s | "
            "%(levelname)s | "
            "%(name)s | "
            "%(message)s"
        ),
    },

    "storage": {
        "output_directory": "outputs",
        "data_directory": "data",

        "save_json_output": True,
        "save_text_output": True,
    },
}


# ─────────────────────────────────────────────────────────────
# Config Manager
# ─────────────────────────────────────────────────────────────

class ConfigManager:
    """
    Production-grade configuration manager.

    Merges defaults → config.json → encrypted secrets → env overrides.
    """

    def __init__(
        self,
        config_path: Optional[Path] = None,
    ):
        self.config_path = (
            config_path or CONFIG_PATH
        )

        self._config: Dict[str, Any] = {}
        self._secrets: Dict[str, Any] = {}
        self.runtime_cache: Dict[str, Any] = {}

        self.reload()

    # ─────────────────────────────────────────────────────
    # Internal Loaders
    # ─────────────────────────────────────────────────────

    def _read_config_file(
        self,
    ) -> Dict[str, Any]:

        if not self.config_path.exists():
            logger.warning(
                "Config file not found | using defaults | %s",
                self.config_path,
            )
            return deepcopy(DEFAULT_CONFIG)

        try:
            with open(
                self.config_path,
                "r",
                encoding="utf-8",
            ) as file:
                loaded_config = json.load(file)

            if not isinstance(
                loaded_config,
                dict,
            ):
                raise ValueError(
                    "Configuration root must be a JSON object"
                )

            merged_config = deep_merge_dicts(
                deepcopy(DEFAULT_CONFIG),
                loaded_config,
            )

            logger.info(
                "Configuration loaded successfully | %s",
                self.config_path,
            )

            return merged_config

        except Exception:
            logger.exception(
                "Failed loading configuration"
            )
            logger.warning(
                "Falling back to default configuration"
            )
            return deepcopy(DEFAULT_CONFIG)

    def _load_secrets(self) -> Dict[str, Any]:
        """
        Load secrets from encrypted file or environment variables.

        Priority:
        1. Encrypted config file (if CONFIG_FILE_PATH exists)
        2. Environment variables (fallback)
        """
        try:
            from config.encrypted_config import (
                load_encrypted_config,
                load_secrets_from_env,
            )

            # Try encrypted config first
            secrets = load_encrypted_config()
            if secrets:
                logger.info("Secrets loaded from encrypted configuration")
                return secrets

            # Fall back to environment variables
            secrets = load_secrets_from_env()
            if secrets:
                logger.info("Secrets loaded from environment variables")
            else:
                logger.warning("No secrets found in encrypted config or environment")

            return secrets

        except Exception:
            logger.exception("Failed to load secrets — continuing without them")
            return {}

    # ─────────────────────────────────────────────────────
    # Public Methods
    # ─────────────────────────────────────────────────────

    def reload(self) -> None:
        self._config = self._read_config_file()
        self._secrets = self._load_secrets()

        # Apply environment overrides
        env = os.getenv("APP_ENV", "").strip()
        if env:
            self._config.setdefault("app", {})["environment"] = env

        debug_env = os.getenv("DEBUG", "").strip().lower()
        if debug_env in ("true", "1", "yes"):
            self._config.setdefault("app", {})["debug"] = True
        elif debug_env in ("false", "0", "no"):
            self._config.setdefault("app", {})["debug"] = False

        logger.info("Configuration reloaded")

    def get(
        self,
        key_path: str,
        default: Any = None,
    ) -> Any:
        """
        Get configuration value using dot notation.

        Example:
            config.get("model.name")
        """
        if not key_path:
            return default

        value: Any = self._config

        try:
            for key in key_path.split("."):
                if not isinstance(value, dict):
                    return default
                value = value.get(key, default)
                if value is None:
                    return default
            return value
        except Exception:
            return default

    def get_secret(
        self,
        key: str,
        default: str = "",
    ) -> str:
        """
        Get a secret value from the loaded secrets.

        Example:
            config.get_secret("groq_api_key")
        """
        return self._secrets.get(key, default)

    def set(
        self,
        key_path: str,
        value: Any,
    ) -> None:
        """Dynamically update config value in memory."""
        keys = key_path.split(".")
        current = self._config

        for key in keys[:-1]:
            if (
                key not in current
                or not isinstance(current[key], dict)
            ):
                current[key] = {}
            current = current[key]

        current[keys[-1]] = value

    def get_all(self) -> Dict[str, Any]:
        return deepcopy(self._config)

    def save(self) -> None:
        """Persist config to disk."""
        try:
            with open(
                self.config_path,
                "w",
                encoding="utf-8",
            ) as file:
                json.dump(
                    self._config,
                    file,
                    indent=2,
                    ensure_ascii=False,
                )
            logger.info("Configuration saved successfully")
        except Exception:
            logger.exception("Failed saving configuration")

    # ─────────────────────────────────────────────────────
    # Runtime Cache
    # ─────────────────────────────────────────────────────

    def set_runtime_cache(
        self,
        key: str,
        value: Any,
    ) -> None:
        self.runtime_cache[key] = value

    def get_runtime_cache(
        self,
        key: str,
        default: Any = None,
    ) -> Any:
        return self.runtime_cache.get(key, default)

    # ─────────────────────────────────────────────────────
    # Dynamic Pattern Helpers
    # ─────────────────────────────────────────────────────

    def get_pattern_excel_path(self) -> Path:
        return PATTERN_EXCEL_PATH

    def get_dynamic_patterns(self) -> list:
        return self.get_runtime_cache("dynamic_patterns", [])

    # ─────────────────────────────────────────────────────
    # Section Helpers
    # ─────────────────────────────────────────────────────

    def get_model_config(self) -> Dict[str, Any]:
        return self.get("model", {})

    def get_generation_config(self) -> Dict[str, Any]:
        return self.get("generation", {})

    def get_rag_config(self) -> Dict[str, Any]:
        return self.get("rag", {})

    def get_validation_config(self) -> Dict[str, Any]:
        return self.get("validation", {})

    def get_prompt_config(self) -> Dict[str, Any]:
        return self.get("prompt", {})

    def get_templates(self) -> Dict[str, Any]:
        return self.get("prompt.step_patterns", {})

    def get_actions(self) -> Dict[str, Any]:
        return self.get("actions", {})

    def get_forbidden_words(self) -> list:
        return self.get("prompting.forbidden_words", [])

    # ─────────────────────────────────────────────────────
    # Properties
    # ─────────────────────────────────────────────────────

    @property
    def path(self) -> Path:
        return self.config_path

    @property
    def exists(self) -> bool:
        return self.config_path.exists()

    @property
    def is_debug(self) -> bool:
        return self.get("app.debug", False)

    @property
    def environment(self) -> str:
        return self.get("app.environment", "production")


# ─────────────────────────────────────────────────────────────
# Singleton Access
# ─────────────────────────────────────────────────────────────

@lru_cache(maxsize=1)
def get_config() -> ConfigManager:
    """Get singleton config manager."""
    return ConfigManager()


def reload_config() -> ConfigManager:
    """Force reload config."""
    get_config.cache_clear()
    return get_config()


# ─────────────────────────────────────────────────────────────
# Exports
# ─────────────────────────────────────────────────────────────

__all__ = [
    "ConfigManager",
    "DEFAULT_CONFIG",
    "get_config",
    "reload_config",
]