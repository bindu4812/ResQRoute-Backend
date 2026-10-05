"""Routes: CRUD, version-aware updates, history and conflict detection.

Update rules (see sections 16 and 22 of the spec):
1. Every report is stored in ``route_updates``.
2. A newer version becomes the current state.
3. An older version is ignored (stale).
4. A different status reported at the SAME version (by a different source)
   never silently overwrites - the route is marked CONFLICTED and both
   reports are retained.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pymongo.errors import DuplicateKeyError

from app.auth import get_current_user, optional_current_user
from app.database import (
    freshness_of,
    get_db,
    make_id,
    now_utc,
    serialize,
    serialize_many,
    to_db,
)
from app.schemas import (
    CurrentUser,
    RoadStatus,
    RouteCreate,
    RouteUpdate,
    VerificationStatus,
)

router = APIRouter(prefix="/routes", tags=["Routes"])

_HISTORY = "history"
_CONFLICTS = "conflicts"


def _record_report(db, report: dict, outcome: str, previous: Optional[dict] = None) -> None:
    """Store one incoming report in route_updates (all reports are retained)."""
    db.route_updates.insert_one(
        {
            "updateId": make_id("upd"),
            "routeId": report.get("routeId"),
            "status": report.get("status"),
            "version": report.get("version"),
            "previousStatus": (previous or {}).get("status"),
            "previousVersion": (previous or {}).get("version"),
            "sourceDevice": report.get("sourceDevice"),
            "reason": report.get("reason"),
            "reportedAt": report.get("reportedAt") or now_utc(),
            "receivedAt": now_utc(),
            "freshness": freshness_of(report.get("reportedAt") or now_utc()),
            "outcome": outcome,
        }
    )


def _with_meta(doc: dict) -> dict:
    data = serialize(doc) or {}
    data["freshness"] = freshness_of(doc.get("lastUpdated"))
    data["conflictCount"] = len(doc.get(_CONFLICTS) or [])
    return data


def apply_route_report(db, report: dict, create_if_missing: bool = False) -> dict:
    """Apply a version-aware route status report.

    Returns a dict with ``outcome`` in
    ``created | applied | conflict | stale | unchanged | missing``.
    Used both by PUT /routes/{id} and by POST /sync.
    """
    route_id = report.get("routeId")
    now = now_utc()
    route = db.routes.find_one({"routeId": route_id})

    if route is None:
        if not create_if_missing:
            return {"outcome": "missing"}
        status = report.get("status") or RoadStatus.UNKNOWN.value
        doc = {
            "routeId": route_id,
            "name": report.get("name") or route_id,
            "from": report.get("from") or "unknown",
            "to": report.get("to") or "unknown",
            "status": status,
            "version": report.get("version") or 1,
            "lastUpdated": now,
            "lastVerified": now,
            "createdAt": now,
            "verificationStatus": VerificationStatus.UNVERIFIED.value,
            "lastSourceDevice": report.get("sourceDevice"),
            _HISTORY: [
                {
                    "version": report.get("version") or 1,
                    "status": status,
                    "updatedAt": now,
                    "sourceDevice": report.get("sourceDevice"),
                    "reason": report.get("reason"),
                }
            ],
            _CONFLICTS: [],
        }
        db.routes.insert_one(doc)
        doc.pop("_id", None)
        _record_report(db, {**report, "routeId": route_id}, "created")
        return {"outcome": "created", "route": doc}

    incoming_status = report.get("status")
    incoming_version = report.get("version")
    current_version = route.get("version") or 1
    source_device = report.get("sourceDevice")
    same_source = bool(source_device) and source_device == route.get("lastSourceDevice")

    def _history_entry(version: int) -> dict:
        return {
            "version": version,
            "status": incoming_status,
            "updatedAt": now,
            "sourceDevice": source_device,
            "reason": report.get("reason"),
        }

    # --- metadata-only edit (no status reported) -------------------------
    if incoming_status is None:
        changes = {key: report[key] for key in ("name", "from", "to") if report.get(key)}
        if not changes:
            _record_report(db, {**report, "routeId": route_id}, "ignored_no_change", route)
            return {"outcome": "unchanged", "route": route}
        changes["lastUpdated"] = now
        db.routes.update_one({"routeId": route_id}, {"$set": changes})
        _record_report(db, {**report, "routeId": route_id}, "applied", route)
        return {"outcome": "applied", "route": db.routes.find_one({"routeId": route_id})}

    # --- 1. older version is ignored (stale) -----------------------------
    if incoming_version is not None and incoming_version < current_version:
        _record_report(db, {**report, "routeId": route_id}, "ignored_stale", route)
        return {"outcome": "stale", "reason": "stale_version", "route": route}

    status_differs = incoming_status != route.get("status")
    current_unknown = route.get("status") == RoadStatus.UNKNOWN.value

    # --- 2. equal version + different status -> CONFLICT ----------------
    if (
        status_differs
        and not current_unknown
        and not same_source
        and (incoming_version is None or incoming_version == current_version)
    ):
        conflict_entry = {
            "status": incoming_status,
            "version": incoming_version or current_version,
            "sourceDevice": source_device,
            "reason": report.get("reason"),
            "reportedAt": report.get("reportedAt") or now,
            "receivedAt": now,
        }
        conflicts = list(route.get(_CONFLICTS) or []) + [conflict_entry]
        db.routes.update_one(
            {"routeId": route_id},
            {
                "$set": {
                    "verificationStatus": VerificationStatus.CONFLICTED.value,
                    _CONFLICTS: conflicts,
                }
            },
        )
        _record_report(db, {**report, "routeId": route_id}, "conflict", route)
        route = db.routes.find_one({"routeId": route_id})
        return {
            "outcome": "conflict",
            "reason": "conflicting_status_report",
            "route": route,
            "conflict": conflict_entry,
        }

    # --- 3. consistent confirmation of the current state -----------------
    if not status_differs and (incoming_version is None or incoming_version == current_version):
        db.routes.update_one(
            {"routeId": route_id},
            {"$set": {"lastUpdated": now, "lastVerified": now, "lastSourceDevice": source_device}},
        )
        _record_report(db, {**report, "routeId": route_id}, "confirmed", route)
        return {"outcome": "unchanged", "route": db.routes.find_one({"routeId": route_id})}

    # --- 4. newer version (or superseding report) becomes the state ------
    new_version = current_version
    if incoming_version is not None and incoming_version > current_version:
        new_version = incoming_version
    update = {
        "status": incoming_status,
        "version": new_version,
        "lastUpdated": now,
        "lastVerified": now,
        "lastSourceDevice": source_device,
        "verificationStatus": (
            VerificationStatus.VERIFIED.value
            if report.get("verified")
            else VerificationStatus.UNVERIFIED.value
        ),
    }
    for key in ("name", "from", "to"):
        if report.get(key):
            update[key] = report[key]
    db.routes.update_one(
        {"routeId": route_id},
        {"$set": update, "$push": {_HISTORY: _history_entry(new_version)}},
    )
    _record_report(db, {**report, "routeId": route_id}, "applied", route)
    return {"outcome": "applied", "route": db.routes.find_one({"routeId": route_id})}


# --------------------------------------------------------------------------
# Endpoints
# --------------------------------------------------------------------------


@router.post("", status_code=201, summary="Create an evacuation route")
def create_route(payload: RouteCreate):
    db = get_db()
    doc = to_db(payload)
    doc["routeId"] = doc.get("routeId") or make_id("route")
    if db.routes.find_one({"routeId": doc["routeId"]}, {"_id": 1}):
        raise HTTPException(status_code=409, detail="routeId already exists")
    now = now_utc()
    doc["version"] = doc.get("version") or 1
    doc["lastUpdated"] = now
    if doc.get("lastVerified") is None:
        doc["lastVerified"] = now
    doc.setdefault("createdAt", now)
    doc["verificationStatus"] = (
        VerificationStatus.UNVERIFIED.value
        if doc["status"] == RoadStatus.UNKNOWN.value
        else VerificationStatus.VERIFIED.value
    )
    doc.setdefault(_HISTORY, [])
    doc[_HISTORY] = list(doc.get(_HISTORY) or []) + [
        {
            "version": doc["version"],
            "status": doc["status"],
            "updatedAt": now,
            "sourceDevice": None,
            "reason": "route created",
        }
    ]
    doc.setdefault(_CONFLICTS, [])
    try:
        db.routes.insert_one(doc)
    except DuplicateKeyError:
        raise HTTPException(status_code=409, detail="routeId already exists")
    doc.pop("_id", None)
    return _with_meta(doc)


@router.get("", summary="List evacuation routes")
def list_routes(
    status: Optional[RoadStatus] = None,
    verificationStatus: Optional[VerificationStatus] = None,
    q: Optional[str] = None,
):
    db = get_db()
    query: dict = {}
    if status is not None:
        query["status"] = status.value
    if verificationStatus is not None:
        query["verificationStatus"] = verificationStatus.value
    if q:
        query["name"] = {"$regex": q, "$options": "i"}
    return [_with_meta(doc) for doc in db.routes.find(query).sort("lastUpdated", -1)]


@router.get("/{route_id}", summary="Get one route")
def get_route(route_id: str):
    db = get_db()
    doc = db.routes.find_one({"routeId": route_id})
    if not doc:
        raise HTTPException(status_code=404, detail="Route not found")
    return _with_meta(doc)


@router.put("/{route_id}", summary="Report / update a route (version-aware)")
def update_route(
    route_id: str,
    payload: RouteUpdate,
    user: Optional[CurrentUser] = Depends(optional_current_user),
):
    """Applies version rules: newer wins, older ignored, same-version
    mismatch -> CONFLICT with conflict information returned."""
    db = get_db()
    if not db.routes.find_one({"routeId": route_id}, {"_id": 1}):
        raise HTTPException(status_code=404, detail="Route not found")

    report = to_db(payload)
    report["routeId"] = route_id
    report["reportedAt"] = report.get("reportedAt") or now_utc()
    if user is not None and not report.get("sourceDevice"):
        report["sourceDevice"] = user.deviceId

    outcome = apply_route_report(db, report, create_if_missing=False)
    route = _with_meta(outcome.get("route") or {})

    if outcome["outcome"] == "conflict":
        return {
            "outcome": "conflict",
            "conflict": True,
            "reason": outcome["reason"],
            "message": "Conflicting reports retained; route marked CONFLICTED",
            "route": route,
            "incoming": serialize({k: v for k, v in report.items() if k != "routeId"}),
            "conflicts": route.get(_CONFLICTS, []),
        }
    if outcome["outcome"] == "stale":
        return {
            "outcome": "ignored",
            "conflict": False,
            "reason": outcome["reason"],
            "message": "Older version ignored; current state kept",
            "route": route,
        }
    return {
        "outcome": outcome["outcome"],
        "conflict": False,
        "reason": outcome.get("reason"),
        "route": route,
    }


@router.get("/{route_id}/history", summary="Route version history and reports")
def route_history(route_id: str):
    db = get_db()
    route = db.routes.find_one({"routeId": route_id})
    if not route:
        raise HTTPException(status_code=404, detail="Route not found")
    reports = list(db.route_updates.find({"routeId": route_id}).sort("receivedAt", 1))
    return {
        "route": _with_meta(route),
        "history": route.get(_HISTORY) or [],
        "conflicts": route.get(_CONFLICTS) or [],
        "reports": serialize_many(reports),
    }


