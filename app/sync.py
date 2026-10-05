"""Store-and-forward synchronization.

POST /sync  accepts an offline batch from a device and:
  - validates incoming data (Pydantic on the request body)
  - ignores duplicates  (same messageId already stored)
  - ignores expired items (expiresAt in the past)
  - limits hops (hopCount > maxHops -> packet dropped)
  - accepts newer route versions / keeps older ones ignored (stale)
  - updates route state (conflicts are flagged, never silently overwritten)
  - updates shelter state when the incoming report is newer
  - records a sync log and returns statistics

GET /sync/pending returns batches that still need attention
(unresolved conflicts or hop-limited packets).
"""

from __future__ import annotations

from typing import List

from fastapi import APIRouter

from app.database import get_db, make_id, now_utc, serialize, serialize_many, to_db
from app.routes import apply_route_report
from app.schemas import SyncRequest

router = APIRouter(prefix="/sync", tags=["Sync"])

_HOP = "hopLimited"


def _expired(expires_at) -> bool:
    return expires_at is not None and expires_at <= now_utc()


def _hop_limited(item: dict) -> bool:
    return (item.get("hopCount") or 0) > (item.get("maxHops") or 5)


@router.post("", summary="Upload an offline batch (store-and-forward)")
def sync_batch(payload: SyncRequest):
    db = get_db()
    now = now_utc()
    items_log: List[dict] = []

    stats = {
        "received": len(payload.alerts)
        + len(payload.routes)
        + len(payload.shelters)
        + len(payload.messages),
        "accepted": 0,
        "duplicates": 0,
        "expired": 0,
        _HOP: 0,
        "stale": 0,
        "updatedRoutes": 0,
        "updatedShelters": 0,
        "conflicts": 0,
    }
    conflict_route_ids: List[str] = []

    def note(kind: str, key: str, outcome: str) -> None:
        items_log.append({"kind": kind, "key": key, "outcome": outcome})

    # ---------------- alerts -------------------------------------------
    for alert in payload.alerts:
        doc = to_db(alert)
        key = doc.get("messageId") or make_id("alert")
        if _expired(doc.get("expiresAt")):
            stats["expired"] += 1
            note("alert", key, "expired")
            continue
        if _hop_limited(doc):
            stats[_HOP] += 1
            note("alert", key, "hop_limited")
            continue
        if db.alerts.find_one({"messageId": key}, {"_id": 1}):
            stats["duplicates"] += 1
            note("alert", key, "duplicate")
            continue
        doc["messageId"] = key
        doc["createdAt"] = doc.get("createdAt") or now
        doc["updatedAt"] = now
        if payload.deviceId:
            doc.setdefault("sourceDevice", payload.deviceId)
            doc.setdefault("createdBy", "sync")
        db.alerts.insert_one(doc)
        stats["accepted"] += 1
        note("alert", key, "accepted")

    # ---------------- messages -----------------------------------------
    for message in payload.messages:
        doc = to_db(message)
        key = doc.get("messageId") or make_id("msg")
        if _expired(doc.get("expiresAt")):
            stats["expired"] += 1
            note("message", key, "expired")
            continue
        if _hop_limited(doc):
            stats[_HOP] += 1
            note("message", key, "hop_limited")
            continue
        if db.messages.find_one({"messageId": key}, {"_id": 1}):
            stats["duplicates"] += 1
            note("message", key, "duplicate")
            continue
        doc["messageId"] = key
        doc["createdAt"] = doc.get("createdAt") or now
        if payload.deviceId:
            doc.setdefault("sourceDevice", payload.deviceId)
            doc.setdefault("createdBy", "sync")
        db.messages.insert_one(doc)
        stats["accepted"] += 1
        note("message", key, "accepted")

    # ---------------- route reports ------------------------------------
    for report in payload.routes:
        doc = to_db(report)
        key = doc.get("routeId") or "unknown-route"
        if _expired(doc.get("expiresAt")):
            stats["expired"] += 1
            note("route", key, "expired")
            continue
        if _hop_limited(doc):
            stats[_HOP] += 1
            note("route", key, "hop_limited")
            continue
        doc.setdefault("sourceDevice", payload.deviceId)
        result = apply_route_report(db, doc, create_if_missing=True)
        outcome = result["outcome"]
        if outcome == "stale":
            stats["stale"] += 1
            note("route", key, "stale")
            continue
        if outcome == "conflict":
            stats["conflicts"] += 1
            stats["accepted"] += 1
            conflict_route_ids.append(key)
            note("route", key, "conflict")
            continue
        # created / applied / unchanged
        stats["accepted"] += 1
        if outcome in ("created", "applied"):
            stats["updatedRoutes"] += 1
        note("route", key, outcome)

    # ---------------- shelters -----------------------------------------
    for shelter in payload.shelters:
        doc = to_db(shelter)
        key = doc.get("shelterId") or make_id("shelter")
        existing = db.shelters.find_one({"shelterId": key})
        incoming_at = doc.get("lastUpdated") or now
        if existing is not None and (existing.get("lastUpdated") or now) > incoming_at:
            stats["stale"] += 1
            note("shelter", key, "stale")
            continue
        if existing is None:
            doc["shelterId"] = key
            doc["lastUpdated"] = incoming_at
            doc.setdefault("createdAt", now)
            db.shelters.insert_one(doc)
            stats["accepted"] += 1
            stats["updatedShelters"] += 1
            note("shelter", key, "created")
        else:
            changes = {k: v for k, v in doc.items() if k != "shelterId"}
            changes["lastUpdated"] = incoming_at
            db.shelters.update_one({"shelterId": key}, {"$set": changes})
            stats["accepted"] += 1
            stats["updatedShelters"] += 1
            note("shelter", key, "updated")

    # ---------------- sync log -----------------------------------------
    status = "pending" if (stats["conflicts"] > 0 or stats[_HOP] > 0) else "completed"
    log = {
        "syncId": make_id("sync"),
        "deviceId": payload.deviceId,
        "received": stats["received"],
        "accepted": stats["accepted"],
        "duplicates": stats["duplicates"],
        "expired": stats["expired"],
        _HOP: stats[_HOP],
        "stale": stats["stale"],
        "updatedRoutes": stats["updatedRoutes"],
        "updatedShelters": stats["updatedShelters"],
        "conflicts": stats["conflicts"],
        "conflictRouteIds": conflict_route_ids,
        "status": status,
        "createdAt": now,
        "items": items_log[:100],
    }
    db.sync_logs.insert_one(log)

    return {
        "syncId": log["syncId"],
        "deviceId": payload.deviceId,
        "status": status,
        **{k: v for k, v in stats.items()},
    }


def get_pending_sync(db) -> List[dict]:
    """Sync batches that still need attention.

    A pending batch is one flagged as pending whose conflicts are not all
    resolved yet (route back to a non-CONFLICTED state) or that contains
    hop-limited packets. Once conflicts are resolved it stops being pending.
    """
    pending: List[dict] = []
    for log in db.sync_logs.find({"status": "pending"}).sort("createdAt", -1):
        unresolved = [
            route_id
            for route_id in log.get("conflictRouteIds") or []
            if db.routes.find_one(
                {"routeId": route_id, "verificationStatus": "CONFLICTED"}, {"_id": 1}
            )
        ]
        hop_limited = log.get(_HOP, 0)
        if unresolved or hop_limited:
            entry = serialize(log) or {}
            entry["unresolvedConflicts"] = unresolved
            pending.append(entry)
    return pending


@router.get("/pending", summary="Batches still awaiting resolution")
def sync_pending():
    db = get_db()
    pending = get_pending_sync(db)
    conflicted = serialize_many(
        db.routes.find({"verificationStatus": "CONFLICTED"}).sort("lastUpdated", -1)
    )
    return {
        "count": len(pending),
        "pending": pending,
        "conflictedRoutes": conflicted,
    }

