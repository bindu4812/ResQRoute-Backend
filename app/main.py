"""ResQRoute API - FastAPI application entry point."""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from datetime import timedelta
from typing import Dict, List

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import alerts, auth, dashboard, locations, messages, routes, shelters, sync
from app.auth import hash_password, require_roles
from app.database import (
    BASE_DIR,
    COLLECTIONS,
    ensure_indexes,
    get_db,
    now_utc,
)
from app.schemas import UserRole

TAGS_METADATA = [
    {"name": "Authentication", "description": "Registration, login, JWT and current user."},
    {"name": "Alerts", "description": "Emergency alerts with duplicate and freshness detection."},
    {"name": "Routes", "description": "Evacuation routes with version history and conflicts."},
    {"name": "Shelters", "description": "Shelter registry with capacity tracking."},
    {"name": "Locations", "description": "Hospitals, rescue points, danger zones and more."},
    {"name": "Messages", "description": "Low-bandwidth emergency messages with hop tracking."},
    {"name": "Sync", "description": "Store-and-forward synchronization between offline devices."},
    {"name": "Dashboard", "description": "Aggregated situation overview (authenticated)."},
    {"name": "Demo", "description": "Hackathon demo helpers (protected)."},
    {"name": "System", "description": "Health and service info."},
]


@asynccontextmanager
async def lifespan(_: FastAPI):
    ensure_indexes()
    yield


app = FastAPI(
    title="ResQRoute API",
    description=(
        "Low-Bandwidth Emergency Evacuation Route Exchange. "
        "JWT protected endpoints: click **Authorize** and paste a Bearer token."
    ),
    version="1.0.0",
    openapi_tags=TAGS_METADATA,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(alerts.router)
app.include_router(routes.router)
app.include_router(shelters.router)
app.include_router(locations.router)
app.include_router(messages.router)
app.include_router(sync.router)
app.include_router(dashboard.router)


@app.get("/health", tags=["System"], summary="Service health check")
def health():
    return {"status": "ok"}


# --------------------------------------------------------------------------
# Demo reset (hackathon helper) - protected: authenticated RESPONDER only
# --------------------------------------------------------------------------
DEMO_FILE = BASE_DIR / "data" / "demo_data.json"
_TIME_KEYS = (
    "createdAt",
    "updatedAt",
    "lastUpdated",
    "lastVerified",
    "reportedAt",
    "receivedAt",
)


def _apply_time_hints(doc: dict, now) -> None:
    """Turn relative time markers in demo_data.json into real timestamps.

    ``minutesAgo``      -> createdAt / updatedAt / lastUpdated / lastVerified
    ``expiresInMinutes`` -> expiresAt
    Nested history / conflict entries are back-dated so freshness stays real
    every time the demo data is reloaded.
    """
    minutes = doc.pop("minutesAgo", None)
    expires_in = doc.pop("expiresInMinutes", None)
    if minutes is not None:
        stamp = now - timedelta(minutes=minutes)
        for key in _TIME_KEYS:
            if key in doc or key == "createdAt":
                doc[key] = stamp
        history = doc.get("history")
        if isinstance(history, list):
            for index, entry in enumerate(history):
                if isinstance(entry, dict):
                    entry["updatedAt"] = stamp - timedelta(
                        minutes=5 * (len(history) - index)
                    )
        conflicts = doc.get("conflicts")
        if isinstance(conflicts, list):
            for index, entry in enumerate(conflicts):
                if isinstance(entry, dict):
                    ts = stamp - timedelta(minutes=5 * (len(conflicts) - index))
                    entry["reportedAt"] = ts
                    entry["receivedAt"] = ts
    if expires_in is not None:
        doc["expiresAt"] = now + timedelta(minutes=expires_in)


def reset_demo_data() -> Dict[str, int]:
    """Wipe every collection and reload data/demo_data.json."""
    payload = json.loads(DEMO_FILE.read_text(encoding="utf-8"))
    db = get_db()
    for name in COLLECTIONS:
        db[name].delete_many({})
    ensure_indexes()

    now = now_utc()
    counts: Dict[str, int] = {name: 0 for name in COLLECTIONS}

    for name in ("users", "alerts", "routes", "route_updates", "shelters", "locations", "messages"):
        docs: List[dict] = payload.get(name, [])
        if not docs:
            continue
        for doc in docs:
            _apply_time_hints(doc, now)
            if name == "users":
                doc["passwordHash"] = hash_password(doc.pop("password"))
        db[name].insert_many(docs)
        counts[name] = len(docs)
    return counts


@app.post("/demo/reset", tags=["Demo"], summary="Reset database to demo data")
def demo_reset(user=Depends(require_roles(UserRole.RESPONDER))):
    """Hackathon helper: reloads data/demo_data.json.

    Protected by JWT + RESPONDER role (401 without token, 403 for other roles).
    """
    counts = reset_demo_data()
    return {
        "status": "ok",
        "message": "Demo data restored",
        "resetBy": user.userId,
        "counts": counts,
    }

