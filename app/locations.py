"""Emergency locations: hospitals, shelters, rescue points, danger zones..."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pymongo.errors import DuplicateKeyError

from app.auth import optional_current_user
from app.database import get_db, make_id, now_utc, serialize, serialize_many, to_db
from app.schemas import CurrentUser, LocationCreate, LocationType, LocationUpdate

router = APIRouter(prefix="/locations", tags=["Locations"])


@router.post("", status_code=201, summary="Create an emergency location")
def create_location(payload: LocationCreate, user: Optional[CurrentUser] = Depends(optional_current_user)):
    db = get_db()
    doc = to_db(payload)
    doc["locationId"] = doc.get("locationId") or make_id("loc")
    if db.locations.find_one({"locationId": doc["locationId"]}, {"_id": 1}):
        raise HTTPException(status_code=409, detail="locationId already exists")
    now = now_utc()
    doc["createdBy"] = user.userId if user else (doc.get("createdBy") or "anonymous")
    doc["createdAt"] = now
    doc["updatedAt"] = now
    try:
        db.locations.insert_one(doc)
    except DuplicateKeyError:
        raise HTTPException(status_code=409, detail="locationId already exists")
    doc.pop("_id", None)
    return serialize(doc)


@router.get("", summary="List emergency locations")
def list_locations(type: Optional[LocationType] = None):
    db = get_db()
    query = {"type": type.value} if type else {}
    return serialize_many(db.locations.find(query).sort("createdAt", -1))


@router.get("/{location_id}", summary="Get one location")
def get_location(location_id: str):
    db = get_db()
    doc = db.locations.find_one({"locationId": location_id})
    if not doc:
        raise HTTPException(status_code=404, detail="Location not found")
    return serialize(doc)


@router.put("/{location_id}", summary="Update a location")
def update_location(
    location_id: str,
    payload: LocationUpdate,
    user: Optional[CurrentUser] = Depends(optional_current_user),
):
    db = get_db()
    if not db.locations.find_one({"locationId": location_id}, {"_id": 1}):
        raise HTTPException(status_code=404, detail="Location not found")
    changes = {key: value for key, value in to_db(payload).items() if value is not None}
    if not changes:
        raise HTTPException(status_code=400, detail="No updatable fields supplied")
    changes["updatedAt"] = now_utc()
    db.locations.update_one({"locationId": location_id}, {"$set": changes})
    return serialize(db.locations.find_one({"locationId": location_id}))
