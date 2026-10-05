"""MongoDB connection, indexes and shared helpers for ResQRoute.

This module is the single source of truth for:
- the PyMongo client / database handle (reads MONGO_URI + DATABASE_NAME from .env)
- index creation
- centralized freshness rules (Fresh / Aging / Outdated)
- id generation and Mongo <-> JSON serialization helpers
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from dotenv import load_dotenv
from pydantic import BaseModel
from pymongo import ASCENDING, MongoClient
from pymongo.database import Database

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017")
DATABASE_NAME = os.getenv("DATABASE_NAME", "resqroute_db")

COLLECTIONS: List[str] = [
    "users",
    "alerts",
    "routes",
    "route_updates",
    "shelters",
    "locations",
    "messages",
    "sync_logs",
]

# --------------------------------------------------------------------------
# Freshness rules (centralized - used by alerts, routes, sync and dashboard)
# --------------------------------------------------------------------------
FRESH_MINUTES = 30       # < 30 minutes            -> Fresh
AGING_MINUTES = 120      # 30-120 minutes -> Aging, > 120 -> Outdated

FRESH = "Fresh"
AGING = "Aging"
OUTDATED = "Outdated"

_client: Optional[MongoClient] = None


# --------------------------------------------------------------------------
# Connection
# --------------------------------------------------------------------------
def get_client() -> MongoClient:
    """Return a process-wide MongoClient (created lazily)."""
    global _client
    if _client is None:
        _client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
    return _client


def get_db() -> Database:
    """Return the configured database handle."""
    return get_client()[DATABASE_NAME]


def ensure_indexes() -> None:
    """Create the useful indexes required by the API."""
    db = get_db()
    index_specs = (
        ("users", [("email", ASCENDING)], True, "users.email"),
        ("users", [("userId", ASCENDING)], True, "users.userId"),
        ("alerts", [("messageId", ASCENDING)], True, "alerts.messageId"),
        ("alerts", [("roadId", ASCENDING)], False, "alerts.roadId"),
        ("alerts", [("createdAt", ASCENDING)], False, "alerts.createdAt"),
        ("routes", [("routeId", ASCENDING)], True, "routes.routeId"),
        ("route_updates", [("routeId", ASCENDING)], False, "route_updates.routeId"),
        ("messages", [("messageId", ASCENDING)], True, "messages.messageId"),
        ("messages", [("createdAt", ASCENDING)], False, "messages.createdAt"),
        ("shelters", [("shelterId", ASCENDING)], True, "shelters.shelterId"),
        ("locations", [("locationId", ASCENDING)], True, "locations.locationId"),
        ("sync_logs", [("createdAt", ASCENDING)], False, "sync_logs.createdAt"),
    )
    for collection, keys, unique, name in index_specs:
        try:
            db[collection].create_index(keys, unique=unique, name=name)
        except Exception:
            # A conflicting index (same keys, different name) may exist from a
            # previous run - drop it and retry once with the canonical name.
            try:
                for idx in db[collection].list_indexes():
                    if idx["name"] != "_id_" and dict(idx["key"]) == dict(keys):
                        db[collection].drop_index(idx["name"])
                db[collection].create_index(keys, unique=unique, name=name)
            except Exception as exc:  # pragma: no cover - never block startup
                print(f"[database] could not create index {name}: {exc}")


# --------------------------------------------------------------------------
# Time / freshness helpers
# --------------------------------------------------------------------------
def now_utc() -> datetime:
    """Current UTC time as a naive datetime (BSON friendly)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def ensure_naive_utc(value: Optional[datetime]) -> Optional[datetime]:
    """Convert an aware datetime to naive UTC; pass naive datetimes through."""
    if value is None:
        return None
    if value.tzinfo is not None:
        return value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


def minutes_since(value: datetime) -> float:
    """Minutes elapsed since ``value`` (UTC)."""
    value = ensure_naive_utc(value) or now_utc()
    return (now_utc() - value).total_seconds() / 60.0


def freshness_of(value: Optional[datetime]) -> str:
    """Centralized freshness classification: Fresh / Aging / Outdated."""
    if value is None:
        return OUTDATED
    age = minutes_since(value)
    if age < FRESH_MINUTES:
        return FRESH
    if age <= AGING_MINUTES:
        return AGING
    return OUTDATED


def is_outdated(value: Optional[datetime]) -> bool:
    return freshness_of(value) == OUTDATED


def freshness_window(freshness: str) -> Dict[str, Any]:
    """Build a MongoDB query fragment for a freshness bucket (createdAt based)."""
    now = now_utc()
    fresh_since = now - timedelta(minutes=FRESH_MINUTES)
    aging_since = now - timedelta(minutes=AGING_MINUTES)
    if freshness == FRESH:
        return {"createdAt": {"$gt": fresh_since}}
    if freshness == AGING:
        return {"createdAt": {"$gt": aging_since, "$lte": fresh_since}}
    if freshness == OUTDATED:
        return {"createdAt": {"$lte": aging_since}}
    raise ValueError(f"Unknown freshness bucket: {freshness}")


# --------------------------------------------------------------------------
# Id / serialization helpers
# --------------------------------------------------------------------------
def make_id(prefix: str) -> str:
    """Generate a short unique id such as ``user-8f3a91c2``."""
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def _normalize(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        return ensure_naive_utc(value)
    if isinstance(value, dict):
        return {key: _normalize(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_normalize(item) for item in value]
    return value


def to_db(value: Any) -> Dict[str, Any]:
    """Convert a Pydantic model (or dict) into a Mongo-ready document."""
    if isinstance(value, BaseModel):
        data = value.model_dump(by_alias=True)
    else:
        data = dict(value)
    return _normalize(data)


def _serialize_value(value: Any) -> Any:
    if isinstance(value, datetime):
        naive = ensure_naive_utc(value) or now_utc()
        return naive.isoformat() + "Z"
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return serialize(value)
    if isinstance(value, (list, tuple)):
        return [_serialize_value(item) for item in value]
    return value


def serialize(doc: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Convert a Mongo document to a JSON-friendly dict (drops ``_id``)."""
    if doc is None:
        return None
    return {key: _serialize_value(value) for key, value in doc.items() if key != "_id"}


def serialize_many(docs: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [serialize(doc) for doc in docs]
