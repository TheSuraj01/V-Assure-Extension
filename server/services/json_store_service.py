"""
JSON Store Service
~~~~~~~~~~~~~~~~~~

Replaces MongoDB as the persistence layer for parsed step-pattern templates.

Responsibilities:
- load_patterns()  → read patterns_cache.json, return dict (empty on first run)
- save_patterns()  → atomically write dict via .tmp-then-rename (crash-safe)

Design decisions:
- Plain synchronous file I/O — local disk latency is microseconds, async adds
  complexity with zero benefit here.
- Atomic write: we write to <file>.tmp first, then os.replace() which is an
  atomic operation on all POSIX/Windows NTFS filesystems.  A crash mid-write
  never corrupts the existing cache.
- Pretty-printed JSON for human inspectability / easy manual editing.
- File location is resolved from the PATTERNS_CACHE_PATH env var, defaulting
  to  <this file's directory>/patterns_cache.json  (i.e. server/config/).
"""

import json
import os
from pathlib import Path
from typing import Any, Dict

from utils import setup_logger

logger = setup_logger(__name__)

def _get_cache_path() -> Path:
    """
    Resolve the path to patterns_cache.json.

    Priority:
    1. PATTERNS_CACHE_PATH env var (absolute or relative to cwd)
    2. Default: <server>/config/patterns_cache.json
    """
    env_path = os.getenv("PATTERNS_CACHE_PATH", "").strip()
    if env_path:
        return Path(env_path)
    # Default: same directory as this file → server/services/  → go up one
    # level to server/, then into config/
    return Path(__file__).parent.parent / "config" / "patterns_cache.json"


def load_patterns() -> Dict[str, Dict[str, Any]]:
    """
    Load all step-pattern templates from the JSON cache file.

    Returns an empty dict if the file does not exist (first run) or if the
    file is corrupt (logs a warning — never raises).
    """
    cache_path = _get_cache_path()

    if not cache_path.exists():
        logger.info(
            "[JSON-STORE] No patterns cache found at %s — first run or cache cleared",
            cache_path,
        )
        return {}

    try:
        text = cache_path.read_text(encoding="utf-8")
        data = json.loads(text)

        if not isinstance(data, dict):
            logger.warning(
                "[JSON-STORE] Cache file is not a JSON object — ignoring | path=%s",
                cache_path,
            )
            return {}

        logger.info(
            "[JSON-STORE] Loaded %d patterns from %s",
            len(data),
            cache_path,
        )
        return data

    except json.JSONDecodeError as exc:
        logger.warning(
            "[JSON-STORE] Cache file is corrupt (JSON parse error) — ignoring | %s | path=%s",
            exc,
            cache_path,
        )
        return {}

    except OSError as exc:
        logger.warning(
            "[JSON-STORE] Cannot read cache file | %s | path=%s",
            exc,
            cache_path,
        )
        return {}


def save_patterns(patterns: Dict[str, Dict[str, Any]]) -> int:
    """
    Atomically persist all step-pattern templates to the JSON cache file.

    Write strategy:
    1. Serialise to JSON in memory.
    2. Write to <path>.tmp  (so a crash here leaves the original intact).
    3. os.replace(<tmp>, <path>)  — atomic rename on POSIX and NTFS.

    Returns the number of patterns written (0 on failure).
    """
    if not patterns:
        logger.warning("[JSON-STORE] save_patterns called with empty dict — nothing written")
        return 0

    cache_path = _get_cache_path()
    tmp_path = cache_path.with_suffix(".tmp")

    try:
        # Ensure parent directory exists
        cache_path.parent.mkdir(parents=True, exist_ok=True)

        serialised = json.dumps(patterns, ensure_ascii=False, indent=2)

        tmp_path.write_text(serialised, encoding="utf-8")
        os.replace(tmp_path, cache_path)

        logger.info(
            "[JSON-STORE] Saved %d patterns to %s",
            len(patterns),
            cache_path,
        )
        return len(patterns)

    except OSError as exc:
        logger.warning(
            "[JSON-STORE] Failed to write cache file | %s | path=%s",
            exc,
            cache_path,
        )
        # Clean up the orphaned .tmp file if it exists
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        return 0

    except (TypeError, ValueError) as exc:
        logger.warning(
            "[JSON-STORE] Failed to serialise patterns to JSON | %s",
            exc,
        )
        return 0


def clear_cache() -> None:
    """
    Delete the patterns cache file (useful for testing / forced re-sync).
    Silently does nothing if the file does not exist.
    """
    cache_path = _get_cache_path()
    try:
        cache_path.unlink(missing_ok=True)
        logger.info("[JSON-STORE] Cache cleared | path=%s", cache_path)
    except OSError as exc:
        logger.warning("[JSON-STORE] Failed to clear cache | %s", exc)


def get_cache_path() -> Path:
    """Return the resolved cache file path (read-only helper for health endpoint)."""
    return _get_cache_path()
