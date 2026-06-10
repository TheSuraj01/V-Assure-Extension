"""
Encrypted Configuration Loader
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Production-grade configuration loader that reads encrypted configuration
from the server filesystem, decrypts it, validates required fields, and
makes it available to the application.

Encryption: AES-256-GCM via the `cryptography` library.

Flow:
  1. Read encrypted file from CONFIG_FILE_PATH (default: /etc/vassure/config.enc)
  2. Read encryption key from CONFIG_ENCRYPTION_KEY environment variable
  3. Decrypt using AES-256-GCM (nonce stored as first 12 bytes of ciphertext)
  4. Parse JSON and validate required fields
  5. Return as a plain dict to be merged into the config system

CLI Usage (encrypt a config file):
    python -m config.encrypted_config encrypt config_plain.json config.enc
    python -m config.encrypted_config decrypt config.enc config_plain.json
"""

import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, Optional

from utils import setup_logger

logger = setup_logger(__name__)

# ─────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────

DEFAULT_CONFIG_PATH = "/etc/vassure/config.enc"

REQUIRED_SECRETS = {
    "groq_api_key",
    "admin_sync_code",
}

S3_REQUIRED_FIELDS = {
    "s3_bucket",
    "s3_key",
    "aws_region",
}


# ─────────────────────────────────────────────────────────────
# Encryption / Decryption
# ─────────────────────────────────────────────────────────────

def _get_encryption_key() -> bytes:
    """
    Read the 32-byte AES key from CONFIG_ENCRYPTION_KEY env var.
    The key must be hex-encoded (64 hex characters = 32 bytes).
    """
    key_hex = os.getenv("CONFIG_ENCRYPTION_KEY", "").strip()
    if not key_hex:
        raise EnvironmentError(
            "CONFIG_ENCRYPTION_KEY environment variable is not set. "
            "This is required for loading encrypted configuration."
        )

    try:
        key_bytes = bytes.fromhex(key_hex)
    except ValueError as exc:
        raise ValueError(
            "CONFIG_ENCRYPTION_KEY must be a valid hex string (64 characters for AES-256)"
        ) from exc

    if len(key_bytes) != 32:
        raise ValueError(
            f"CONFIG_ENCRYPTION_KEY must be exactly 32 bytes (64 hex chars), "
            f"got {len(key_bytes)} bytes"
        )

    return key_bytes


def encrypt_config(plaintext: str, key: bytes) -> bytes:
    """
    Encrypt a plaintext JSON string using AES-256-GCM.

    Returns: nonce (12 bytes) + tag (16 bytes) + ciphertext
    """
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    aesgcm = AESGCM(key)
    nonce = os.urandom(12)
    ciphertext = aesgcm.encrypt(nonce, plaintext.encode("utf-8"), None)

    return nonce + ciphertext


def decrypt_config(encrypted_data: bytes, key: bytes) -> str:
    """
    Decrypt AES-256-GCM encrypted data.

    Input format: nonce (12 bytes) + ciphertext (includes 16-byte GCM tag)
    """
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    if len(encrypted_data) < 28:  # 12 (nonce) + 16 (tag minimum)
        raise ValueError("Encrypted data is too short to be valid")

    nonce = encrypted_data[:12]
    ciphertext = encrypted_data[12:]

    aesgcm = AESGCM(key)
    plaintext = aesgcm.decrypt(nonce, ciphertext, None)

    return plaintext.decode("utf-8")


# ─────────────────────────────────────────────────────────────
# Config Loading
# ─────────────────────────────────────────────────────────────

def load_encrypted_config(
    config_path: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Load and decrypt configuration from the filesystem.

    Parameters
    ----------
    config_path : str, optional
        Path to the encrypted config file. Defaults to CONFIG_FILE_PATH
        env var or /etc/vassure/config.enc.

    Returns
    -------
    dict
        Decrypted and validated configuration dictionary.

    Raises
    ------
    FileNotFoundError
        If the encrypted config file does not exist.
    EnvironmentError
        If the encryption key is not set.
    ValueError
        If the decrypted content is not valid JSON or fails validation.
    """
    path = config_path or os.getenv("CONFIG_FILE_PATH", DEFAULT_CONFIG_PATH)
    config_file = Path(path)

    if not config_file.exists():
        logger.warning(
            "Encrypted config file not found at %s — skipping encrypted config",
            config_file,
        )
        return {}

    logger.info("Loading encrypted configuration from %s", config_file)

    key = _get_encryption_key()

    encrypted_data = config_file.read_bytes()

    try:
        plaintext = decrypt_config(encrypted_data, key)
    except Exception as exc:
        raise ValueError(
            f"Failed to decrypt configuration file: {config_file}"
        ) from exc

    try:
        config = json.loads(plaintext)
    except json.JSONDecodeError as exc:
        raise ValueError(
            "Decrypted configuration is not valid JSON"
        ) from exc

    if not isinstance(config, dict):
        raise ValueError("Decrypted configuration must be a JSON object")

    logger.info("Encrypted configuration loaded and decrypted successfully")

    return config


def validate_secrets(config: Dict[str, Any]) -> None:
    """
    Validate that all required secret fields are present and non-empty.

    Raises ValueError if any required field is missing.
    """
    missing = []

    for field in REQUIRED_SECRETS:
        value = config.get(field, "")
        if not value or (isinstance(value, str) and not value.strip()):
            missing.append(field)

    if missing:
        raise ValueError(
            f"Missing required configuration secrets: {sorted(missing)}"
        )


def validate_s3_config(config: Dict[str, Any]) -> None:
    """
    Validate S3 configuration if S3 fields are present.
    Only validates if at least one S3 field is set.
    """
    s3_values = {
        field: config.get(field, "")
        for field in S3_REQUIRED_FIELDS
    }

    has_any = any(v for v in s3_values.values())
    if not has_any:
        logger.info("No S3 configuration found — S3 features disabled")
        return

    missing = [
        field for field, value in s3_values.items()
        if not value or (isinstance(value, str) and not value.strip())
    ]

    if missing:
        raise ValueError(
            f"Partial S3 configuration detected. Missing fields: {sorted(missing)}"
        )


# ─────────────────────────────────────────────────────────────
# Environment-to-Config Mapping
# ─────────────────────────────────────────────────────────────

def load_secrets_from_env() -> Dict[str, Any]:
    """
    Fallback: load secrets from environment variables.

    This is used when no encrypted config file is available
    (e.g., in development or simple Docker deployments).
    """
    secrets = {}

    env_mapping = {
        "groq_api_key":        "GROQ_API_KEY",
        "local_api_key":       "LOCAL_API_KEY",
        "local_api_base":      "LOCAL_API_BASE",
        "bedrock_credentials": "BEDROCK_CREDENTIALS",
        "admin_sync_code":     "ADMIN_SYNC_CODE",
        "aws_access_key_id":     "AWS_ACCESS_KEY_ID",
        "aws_secret_access_key": "AWS_SECRET_ACCESS_KEY",
        "aws_region":            "AWS_REGION",
        "s3_bucket":             "S3_BUCKET",
        "s3_key":                "S3_KEY",
        "s3_endpoint_url":       "S3_ENDPOINT_URL",
    }

    for config_key, env_var in env_mapping.items():
        value = os.getenv(env_var, "").strip()
        if value:
            secrets[config_key] = value

    return secrets


# ─────────────────────────────────────────────────────────────
# CLI for encrypt/decrypt
# ─────────────────────────────────────────────────────────────

def _cli():
    """CLI entry point for encrypting/decrypting config files."""
    if len(sys.argv) < 4:
        print("Usage:")
        print("  python -m config.encrypted_config encrypt <input.json> <output.enc>")
        print("  python -m config.encrypted_config decrypt <input.enc> <output.json>")
        print()
        print("  CONFIG_ENCRYPTION_KEY env var must be set (64 hex chars = 32 bytes AES key)")
        print()
        print("Generate a new key:")
        print(f"  python -c \"import os; print(os.urandom(32).hex())\"")
        sys.exit(1)

    action = sys.argv[1]
    input_path = Path(sys.argv[2])
    output_path = Path(sys.argv[3])

    key = _get_encryption_key()

    if action == "encrypt":
        plaintext = input_path.read_text(encoding="utf-8")
        # Validate it's valid JSON
        json.loads(plaintext)
        encrypted = encrypt_config(plaintext, key)
        output_path.write_bytes(encrypted)
        print(f"Encrypted {input_path} -> {output_path} ({len(encrypted)} bytes)")

    elif action == "decrypt":
        encrypted_data = input_path.read_bytes()
        plaintext = decrypt_config(encrypted_data, key)
        # Pretty-print the output
        parsed = json.loads(plaintext)
        output_path.write_text(
            json.dumps(parsed, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        print(f"Decrypted {input_path} -> {output_path}")

    else:
        print(f"Unknown action: {action}. Use 'encrypt' or 'decrypt'.")
        sys.exit(1)


if __name__ == "__main__":
    _cli()
