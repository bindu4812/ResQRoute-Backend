"""Pydantic schemas and enums for the ResQRoute API."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, EmailStr, Field

# --------------------------------------------------------------------------
# Enums
# --------------------------------------------------------------------------
class UserRole(str, Enum):
    RESPONDER = "RESPONDER"
    VOLUNTEER = "VOLUNTEER"
    SHELTER = "SHELTER"


class AlertType(str, Enum):
    ROAD_UPDATE = "ROAD_UPDATE"
    DANGER_ZONE = "DANGER_ZONE"
    SHELTER_UPDATE = "SHELTER_UPDATE"
    SOS = "SOS"
    GENERAL_EMERGENCY = "GENERAL_EMERGENCY"


class RoadStatus(str, Enum):
    SAFE = "SAFE"
    CAUTION = "CAUTION"
    BLOCKED = "BLOCKED"
    UNKNOWN = "UNKNOWN"


class Priority(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class VerificationStatus(str, Enum):
    VERIFIED = "VERIFIED"
    UNVERIFIED = "UNVERIFIED"
    CORROBORATED = "CORROBORATED"
    OUTDATED = "OUTDATED"
    CONFLICTED = "CONFLICTED"


class ShelterStatus(str, Enum):
    OPEN = "OPEN"
    NEAR_FULL = "NEAR_FULL"
    FULL = "FULL"
    CLOSED = "CLOSED"


class LocationType(str, Enum):
    HOSPITAL = "HOSPITAL"
    SHELTER = "SHELTER"
    RESCUE_POINT = "RESCUE_POINT"
    DANGER_ZONE = "DANGER_ZONE"
    WATER_POINT = "WATER_POINT"
    EVACUATION_CENTRE = "EVACUATION_CENTRE"


class Freshness(str, Enum):
    FRESH = "Fresh"
    AGING = "Aging"
    OUTDATED = "Outdated"


# --------------------------------------------------------------------------
# Authentication schemas
# --------------------------------------------------------------------------
class RegisterRequest(BaseModel):
    name: str = Field(min_length=2, max_length=80, examples=["Rahul"])
    email: EmailStr
    password: str = Field(min_length=6, max_length=128)
    role: UserRole
    deviceId: Optional[str] = Field(default=None, max_length=64)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: dict


class CurrentUser(BaseModel):
    """Authenticated identity available to every protected dependency."""

    userId: str
    name: str = ""
    email: str
    role: UserRole
    deviceId: Optional[str] = None
    isActive: bool = True


# --------------------------------------------------------------------------
# Alert schemas
# --------------------------------------------------------------------------
class AlertBase(BaseModel):
    type: AlertType
    roadId: Optional[str] = Field(default=None, max_length=64)
    status: Optional[RoadStatus] = None
    reason: Optional[str] = Field(default=None, max_length=500)
    lat: float = Field(ge=-90, le=90, examples=[13.0827])
    lon: float = Field(ge=-180, le=180, examples=[80.2707])
    priority: Priority = Priority.MEDIUM
    expiresAt: Optional[datetime] = None
    sourceDevice: Optional[str] = Field(default=None, max_length=64)
    sourceRole: Optional[UserRole] = None
    createdBy: Optional[str] = Field(default=None, max_length=64)
    verified: bool = False
    verificationStatus: VerificationStatus = VerificationStatus.UNVERIFIED
    hopCount: int = Field(default=0, ge=0)
    maxHops: int = Field(default=5, ge=0)


class AlertCreate(AlertBase):
    messageId: Optional[str] = Field(default=None, max_length=64)


class AlertUpdate(BaseModel):
    type: Optional[AlertType] = None
    roadId: Optional[str] = Field(default=None, max_length=64)
    status: Optional[RoadStatus] = None
    reason: Optional[str] = Field(default=None, max_length=500)
    lat: Optional[float] = Field(default=None, ge=-90, le=90)
    lon: Optional[float] = Field(default=None, ge=-180, le=180)
    priority: Optional[Priority] = None
    expiresAt: Optional[datetime] = None
    verified: Optional[bool] = None
    verificationStatus: Optional[VerificationStatus] = None
    hopCount: Optional[int] = Field(default=None, ge=0)
    maxHops: Optional[int] = Field(default=None, ge=0)



# --------------------------------------------------------------------------
# Route schemas ("from" / "to" are served with their exact JSON names)
# --------------------------------------------------------------------------
class RouteBase(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    name: str = Field(min_length=1, max_length=120, examples=["NH-44 Highway"])
    from_location: str = Field(alias="from", min_length=1, max_length=120, examples=["Chennai"])
    to_location: str = Field(alias="to", min_length=1, max_length=120, examples=["Tiruvallur"])
    status: RoadStatus = RoadStatus.UNKNOWN


class RouteCreate(RouteBase):
    routeId: Optional[str] = Field(default=None, max_length=64)
    version: int = Field(default=1, ge=1)
    lastVerified: Optional[datetime] = None


class RouteUpdate(BaseModel):
    """Status report / edit for an existing route (version-aware)."""

    model_config = ConfigDict(populate_by_name=True)

    name: Optional[str] = Field(default=None, min_length=1, max_length=120)
    from_location: Optional[str] = Field(default=None, alias="from", min_length=1, max_length=120)
    to_location: Optional[str] = Field(default=None, alias="to", min_length=1, max_length=120)
    status: Optional[RoadStatus] = None
    version: Optional[int] = Field(default=None, ge=1)
    reportedAt: Optional[datetime] = None
    sourceDevice: Optional[str] = Field(default=None, max_length=64)
    reason: Optional[str] = Field(default=None, max_length=500)
    verified: bool = False


class RouteReport(RouteUpdate):
    """Route report accepted through store-and-forward sync."""

    routeId: str = Field(max_length=64)
    messageId: Optional[str] = Field(default=None, max_length=64)
    expiresAt: Optional[datetime] = None
    hopCount: int = Field(default=0, ge=0)
    maxHops: int = Field(default=5, ge=0)


# --------------------------------------------------------------------------
# Shelter schemas
# --------------------------------------------------------------------------
class ShelterCreate(BaseModel):
    shelterId: Optional[str] = Field(default=None, max_length=64)
    name: str = Field(min_length=1, max_length=120)
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    capacity: int = Field(ge=0)
    occupied: int = Field(default=0, ge=0)
    status: ShelterStatus = ShelterStatus.OPEN
    lastUpdated: Optional[datetime] = None


class ShelterUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=120)
    latitude: Optional[float] = Field(default=None, ge=-90, le=90)
    longitude: Optional[float] = Field(default=None, ge=-180, le=180)
    capacity: Optional[int] = Field(default=None, ge=0)
    occupied: Optional[int] = Field(default=None, ge=0)
    status: Optional[ShelterStatus] = None
    lastUpdated: Optional[datetime] = None


# --------------------------------------------------------------------------
# Location schemas
# --------------------------------------------------------------------------
class LocationCreate(BaseModel):
    locationId: Optional[str] = Field(default=None, max_length=64)
    name: str = Field(min_length=1, max_length=120)
    type: LocationType
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    description: Optional[str] = Field(default=None, max_length=500)
    createdBy: Optional[str] = Field(default=None, max_length=64)


class LocationUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=120)
    type: Optional[LocationType] = None
    latitude: Optional[float] = Field(default=None, ge=-90, le=90)
    longitude: Optional[float] = Field(default=None, ge=-180, le=180)
    description: Optional[str] = Field(default=None, max_length=500)


# --------------------------------------------------------------------------
# Message schemas
# --------------------------------------------------------------------------
class MessageCreate(BaseModel):
    messageId: Optional[str] = Field(default=None, max_length=64)
    message: str = Field(min_length=1, max_length=1000)
    messageType: str = Field(default="GENERAL", max_length=40)
    createdBy: Optional[str] = Field(default=None, max_length=64)
    sourceRole: Optional[UserRole] = None
    sourceDevice: Optional[str] = Field(default=None, max_length=64)
    expiresAt: Optional[datetime] = None
    hopCount: int = Field(default=0, ge=0)
    maxHops: int = Field(default=5, ge=0)


# --------------------------------------------------------------------------
# Sync schemas
# --------------------------------------------------------------------------
class SyncRequest(BaseModel):
    """A store-and-forward batch uploaded by a device."""

    deviceId: Optional[str] = Field(default=None, max_length=64)
    alerts: List[AlertCreate] = Field(default_factory=list)
    routes: List[RouteReport] = Field(default_factory=list)
    shelters: List[ShelterCreate] = Field(default_factory=list)
    messages: List[MessageCreate] = Field(default_factory=list)
