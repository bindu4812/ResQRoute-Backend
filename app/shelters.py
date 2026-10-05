"""Shelters: CRUD with capacity validation."""

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
    to_db,
)
from app.schemas import CurrentUser, ShelterCreate, ShelterStatus, ShelterUpdate

router = APIRouter(prefix="/shelters", tags=["Shelters"])


def _validate_capacity(capacity: int, occupied: int) -> None:
    if capacity < 1:
        raise HTTPException(status_code=400, detail="capacity must be at least 1")
    if occupied < 0:
        raise HTTPException(status_code=400, detail="occupied cannot be negative")
    if occupied > capacity:
        raise HTTPException(status_code=400, detail="occupied cannot exceed capacity")


def _with_meta(doc: dict) -> dict:
    data = serialize(doc) or {}
    data["freshness"] = freshness_of(doc.get("lastUpdated"))
    free = max((doc.get("capacity") or 0) - (doc.get("occupied") or 0), 0)
    data["freeSlots"] = free
    return data


@router.post("", status_code=201, summary="Create a shelter")
def create_shelter(payload: ShelterCreate, user: Optional[CurrentUser] = Depends(optional_current_user)):
    db = get_db()
    doc = to_db(payload)
    _validate_capacity(doc.get("capacity", 0), doc.get("occupied", 0))
    doc["shelterId"] = doc.get("shelterId") or make_id("shelter")
    if db.shelters.find_one({"shelterId": doc["shelterId"]}, {"_id": 1}):
        raise HTTPException(status_code=409, detail="shelterId already exists")
    doc["lastUpdated"] = doc.get("lastUpdated") or now_utc()
    doc.setdefault("createdAt", now_utc())
    try:
        db.shelters.insert_one(doc)
    except DuplicateKeyError:
        raise HTTPException(status_code=409, detail="shelterId already exists")
    doc.pop("_id", None)
    return _with_meta(doc)


@router.get("", summary="List shelters")
def list_shelters(status: Optional[ShelterStatus] = None):
    db = get_db()
    query = {"status": status.value} if status else {}
    return [_with_meta(doc) for doc in db.shelters.find(query).sort("lastUpdated", -1)]


@router.get("/{shelter_id}", summary="Get one shelter")
def get_shelter(shelter_id: str):
    db = get_db()
    doc = db.shelters.find_one({"shelterId": shelter_id})
    if not doc:
        raise HTTPException(status_code=404, detail="Shelter not found")
    return _with_meta(doc)


@router.put("/{shelter_id}", summary="Update a shelter")
def update_shelter(
    shelter_id: str,
    payload: ShelterUpdate,
    user: Optional[CurrentUser] = Depends(get_current_user),
):
    db = get_db()
    doc = db.shelters.find_one({"shelterId": shelter_id})
    if not doc:
        raise HTTPException(status_code=404, detail="Shelter not found")

    changes = {key: value for key, value in to_db(payload).items() if value is not None}
    if not changes:
        raise HTTPException(status_code=400, detail="No updatable fields supplied")
    capacity = changes.get("capacity", doc.get("capacity", 0))
    occupied = changes.get("occupied", doc.get("occupied", 0))
    _validate_capacity(capacity, occupied)
    changes["lastUpdated"] = now_utc()
    db.shelters.update_one({"shelterId": shelter_id}, {"$set": changes})
    return _with_meta(db.shelters.find_one({"shelterId": shelter_id}))
