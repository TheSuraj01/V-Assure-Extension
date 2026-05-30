"""
MongoDB Service
~~~~~~~~~~~~~~~

Async MongoDB integration using motor.

Responsibilities:
- Connect to MongoDB Atlas (lazy, single shared client).
- Upsert all parsed step-pattern templates into the `step_patterns` collection.
- Load all templates from MongoDB into the runtime-cache dict format.
- Provide a status/count helper for the health endpoint.

Collection schema (one document per pattern):
    {
        "_id": "<pattern_id>",   # same as pattern_id field
        "pattern_id": str,
        "template_key": str,
        "action": str,
        "template": str,
        "description": str,
        "examples": [str, ...],
        "priority": int,
        "synced_at": datetime (UTC)
    }
"""

import os
from datetime import datetime, timezone
from typing import Any, Dict

import motor.motor_asyncio as motor_asyncio
from pymongo import UpdateOne

from utils import setup_logger

logger = setup_logger(__name__)

# ─────────────────────────────────────────────────────────────
# Lazy client holder
# ─────────────────────────────────────────────────────────────

_client = None
_db = None
_collection = None

DB_NAME = "vassure"
COLLECTION_NAME = "step_patterns"


def _get_collection():
    """Return the motor collection, initialising on first call."""
    global _client, _db, _collection

    if _collection is not None:
        return _collection

    uri = os.getenv("MONGODB_URI", "").strip()
    if not uri:
        logger.warning("MONGODB_URI not set — MongoDB service disabled")
        return None

    try:
        _client = motor_asyncio.AsyncIOMotorClient(
            uri,
            serverSelectionTimeoutMS=5000,
            connectTimeoutMS=5000,
        )
        _db = _client[DB_NAME]
        _collection = _db[COLLECTION_NAME]

        logger.info(
            "MongoDB client initialised | db=%s | collection=%s",
            DB_NAME,
            COLLECTION_NAME,
        )

    except Exception:
        logger.exception("Failed to initialise MongoDB client")
        _collection = None

    return _collection


# ─────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────


async def upsert_patterns(
    patterns: Dict[str, Dict[str, Any]],
) -> int:
    """
    Bulk-upsert all parsed patterns into MongoDB.

    Each pattern is stored with ``_id = pattern_id`` so repeated syncs are
    idempotent (update, not duplicate).

    Returns the number of patterns processed.
    """
    coll = _get_collection()
    if coll is None:
        return 0

    if not patterns:
        logger.warning("upsert_patterns called with empty dict — nothing to write")
        return 0

    now = datetime.now(timezone.utc)
    ops = []

    try:
        

        for pattern_id, data in patterns.items():
            doc = {**data, "synced_at": now}
            ops.append(
                UpdateOne(
                    {"_id": pattern_id},
                    {"$set": doc},
                    upsert=True,
                )
            )

        result = await coll.bulk_write(ops, ordered=False)

        logger.info(
            "MongoDB upsert complete | upserted=%s | modified=%s | total=%s",
            result.upserted_count,
            result.modified_count,
            len(ops),
        )

        return len(ops)

    except Exception:
        logger.exception("MongoDB upsert_patterns failed")
        return 0


async def load_patterns() -> Dict[str, Dict[str, Any]]:
    """
    Load all patterns from MongoDB and return them in the same dict format
    that ``DynamicPatternLoader._parse_sheet()`` produces:

        { "<pattern_id>": { "pattern_id": ..., "template_key": ..., ... } }

    Returns an empty dict if MongoDB is unreachable or the collection is empty.
    """
    coll = _get_collection()
    if coll is None:
        return {}

    try:
        patterns: Dict[str, Dict[str, Any]] = {}

        async for doc in coll.find({}, {"_id": 0, "synced_at": 0}):
            pid = doc.get("pattern_id")
            if pid:
                patterns[pid] = doc

        if patterns:
            logger.info(
                "Loaded %s patterns from MongoDB",
                len(patterns),
            )
        else:
            logger.info("MongoDB collection is empty — no patterns loaded")

        return patterns

    except Exception:
        logger.exception("MongoDB load_patterns failed")
        return {}


async def get_pattern_count() -> int:
    """Return total number of patterns stored in MongoDB."""
    coll = _get_collection()
    if coll is None:
        return 0
    try:
        return await coll.count_documents({})
    except Exception:
        logger.exception("MongoDB count failed")
        return 0


async def close() -> None:
    """Gracefully close the MongoDB client (called on app shutdown)."""
    global _client, _db, _collection
    if _client is not None:
        _client.close()
        _client = None
        _db = None
        _collection = None
        logger.info("MongoDB client closed")
