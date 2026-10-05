"""Emergency messages: low-bandwidth broadcast messages with hop tracking."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pymongo.errors import DuplicateKeyError

from app.auth import optional_current_user
from app.database import (
    get_db,
    make_id,
    now_utc,
    serialize,
    serialize_many,
    to_db,
)
from app.schemas import CurrentUser, MessageCreate

router = APIRouter(prefix="/messages", tags=["Messages"])


@router.post("", status_code=201, summary="Create an emergency message")
def create_message(payload: MessageCreate, user: Optional[CurrentUser] = Depends(optional_current_user)):
    """Duplicate messageId -> 409."""
    db = get_db()
    doc = to_db(payload)
    doc["messageId"] = doc.get("messageId") or make_id("msg")
    now = now_utc()
    doc["createdAt"] = now
    if user is not None:
        doc["createdBy"] = user.userId
        doc["sourceRole"] = user.role.value
        doc["sourceDevice"] = user.deviceId or doc.get("sourceDevice")
    else:
        doc["createdBy"] = doc.get("createdBy") or "anonymous"
    try:
        db.messages.insert_one(doc)
    except DuplicateKeyError:
        raise HTTPException(status_code=409, detail="Duplicate messageId: message already exists")
    doc.pop("_id", None)
    return serialize(doc)


@router.get("", summary="List emergency messages")
def list_messages(
    messageType: Optional[str] = None,
    includeExpired: bool = Query(default=False),
):
    db = get_db()
    query: dict = {}
    if messageType:
        query["messageType"] = messageType
    if not includeExpired:
        query["$or"] = [{"expiresAt": None}, {"expiresAt": {"$gt": now_utc()}}]
    return serialize_many(db.messages.find(query).sort("createdAt", -1))


@router.get("/{message_id}", summary="Get one message")
def get_message(message_id: str):
    db = get_db()
    doc = db.messages.find_one({"messageId": message_id})
    if not doc:
        raise HTTPException(status_code=404, detail="Message not found")
    return serialize(doc)
