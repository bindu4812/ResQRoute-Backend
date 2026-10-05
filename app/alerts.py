"""Emergency alerts: CRUD, filters, duplicate and freshness detection."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pymongo.errors import DuplicateKeyError

from app.auth import get_current_user, optional_current_user
from app.database import (
    freshness_of,
    freshness_window,
    get_db,
    make_id,
    now_utc,
    serialize,
    to_db,
)
from app.schemas import (
    AlertCreate,
    AlertUpdate,
    AlertType,
    CurrentUser,
    Freshness,
    Priority,
    RoadStatus,
    UserRole,
    VerificationStatus,
)

router = APIRouter(prefix="/alerts", tags=["Alerts"])


def _prepare_alert(payload: AlertCreate, user: Optional[CurrentUser]) -> dict:
    """Build the alert document.

    Identity fields are NEVER trusted from the frontend when a JWT is present -
    they come from the authenticated user (createdBy / sourceRole / sourceDevice).
    """
    doc = to_db(payload)
    now = now_utc()
    doc["messageId"] = doc.get("messageId") or make_id("alert")
    doc["createdAt"] = now
    doc["updatedAt"] = now
    if user is not None:
        doc["createdBy"] = user.userId
        doc["sourceRole"] = user.role.value
        doc["sourceDevice"] = user.deviceId or doc.get("sourceDevice")
    else:
        doc["createdBy"] = doc.get("createdBy") or "anonymous"
        if doc.get("sourceRole"):
            doc["sourceRole"] = (
                doc["sourceRole"].value if isinstance(doc["sourceRole"], UserRole) else doc["sourceRole"]
            )
    return doc


def _with_freshness(doc: dict) -> dict:
    data = serialize(doc) or {}
    data["freshness"] = freshness_of(doc.get("createdAt"))
    data["active"] = doc.get("expiresAt") is None or doc["expiresAt"] > now_utc()
    return data


@router.post("", status_code=201, summary="Create an emergency alert")
def create_alert(
    payload: AlertCreate,
    user: Optional[CurrentUser] = Depends(optional_current_user),
):
    """Duplicate messageId -> 409. Works with or without a JWT."""
    db = get_db()
    doc = _prepare_alert(payload, user)
    try:
        db.alerts.insert_one(doc)
    except DuplicateKeyError:
        raise HTTPException(status_code=409, detail="Duplicate messageId: alert already exists")
    doc.pop("_id", None)
    return _with_freshness(doc)


@router.get("", summary="List alerts with filters")
def list_alerts(
    type: Optional[AlertType] = None,
    status: Optional[RoadStatus] = None,
    priority: Optional[Priority] = None,
    sourceRole: Optional[UserRole] = None,
    verificationStatus: Optional[VerificationStatus] = None,
    roadId: Optional[str] = None,
    freshness: Optional[Freshness] = None,
    includeExpired: bool = Query(default=False),
):
    db = get_db()
    query: dict = {}
    if type is not None:
        query["type"] = type.value
    if status is not None:
        query["status"] = status.value
    if priority is not None:
        query["priority"] = priority.value
    if sourceRole is not None:
        query["sourceRole"] = sourceRole.value
    if verificationStatus is not None:
        query["verificationStatus"] = verificationStatus.value
    if roadId is not None:
        query["roadId"] = roadId
    if freshness is not None:
        query.update(freshness_window(freshness.value))
    if not includeExpired:
        query["$or"] = [{"expiresAt": None}, {"expiresAt": {"$gt": now_utc()}}]

    cursor = db.alerts.find(query).sort("createdAt", -1)
    return [_with_freshness(doc) for doc in cursor]


@router.get("/{message_id}", summary="Get one alert by messageId")
def get_alert(message_id: str):
    db = get_db()
    doc = db.alerts.find_one({"messageId": message_id})
    if not doc:
        raise HTTPException(status_code=404, detail="Alert not found")
    return _with_freshness(doc)


@router.put("/{message_id}", summary="Update an alert")
def update_alert(message_id: str, payload: AlertUpdate, user: CurrentUser = Depends(get_current_user)):
    """Partial update. Unknown messageId -> 404."""
    db = get_db()
    doc = db.alerts.find_one({"messageId": message_id})
    if not doc:
        raise HTTPException(status_code=404, detail="Alert not found")

    changes = {
        key: value
        for key, value in to_db(payload).items()
        if value is not None
    }
    if not changes:
        raise HTTPException(status_code=400, detail="No updatable fields supplied")
    changes["updatedAt"] = now_utc()
    db.alerts.update_one({"messageId": message_id}, {"$set": changes})
    return _with_freshness(db.alerts.find_one({"messageId": message_id}))


@router.delete("/{message_id}", summary="Delete an alert")
def delete_alert(message_id: str, user: CurrentUser = Depends(get_current_user)):
    db = get_db()
    result = db.alerts.delete_one({"messageId": message_id})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Alert not found")
    return {"message": "Alert deleted", "messageId": message_id}

