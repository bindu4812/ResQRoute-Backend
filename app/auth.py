"""JWT authentication, password hashing and role-based access for ResQRoute.

Password hashing: PBKDF2-HMAC-SHA256 with a per-user random salt (stdlib only,
no plain-text passwords ever stored). JWT tokens are stateless: logout simply
means the frontend drops the token.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

import jwt
from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pymongo.errors import DuplicateKeyError

from app.database import (
    BASE_DIR,
    get_db,
    make_id,
    now_utc,
    serialize,
)
from app.schemas import CurrentUser, LoginRequest, RegisterRequest, UserRole

from dotenv import load_dotenv

load_dotenv(BASE_DIR / ".env")

JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY", "change-this-secret-key")
JWT_ALGORITHM = os.getenv("JWT_ALGORITHM", "HS256")
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "60"))

bearer_scheme = HTTPBearer(scheme_name="BearerAuth", auto_error=False)

# --------------------------------------------------------------------------
# Password hashing (PBKDF2-HMAC-SHA256)
# --------------------------------------------------------------------------
_PBKDF2_ITERATIONS = 120_000
_PBKDF2_SCHEME = "pbkdf2_sha256"


def hash_password(password: str) -> str:
    """Return ``pbkdf2_sha256$iterations$salt$hexdigest`` for a password."""
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt.encode("utf-8"), _PBKDF2_ITERATIONS
    )
    return f"{_PBKDF2_SCHEME}${_PBKDF2_ITERATIONS}${salt}${digest.hex()}"


def verify_password(password: str, password_hash: Optional[str]) -> bool:
    """Constant-time verification of a password against its stored hash."""
    if not password_hash:
        return False
    try:
        scheme, iterations, salt, expected = password_hash.split("$")
        if scheme != _PBKDF2_SCHEME:
            return False
        digest = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            salt.encode("utf-8"),
            int(iterations),
        )
        return hmac.compare_digest(digest.hex(), expected)
    except (ValueError, TypeError, AttributeError):
        return False


# --------------------------------------------------------------------------
# JWT helpers - payload only contains userId, email, role (+ iat/exp)
# --------------------------------------------------------------------------
def create_access_token(user_id: str, email: str, role: str) -> str:
    issued_at = datetime.now(timezone.utc)
    payload = {
        "userId": user_id,
        "email": email,
        "role": role,
        "iat": issued_at,
        "exp": issued_at + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES),
    }
    return jwt.encode(payload, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)


def decode_access_token(token: str) -> dict:
    try:
        return jwt.decode(
            token,
            JWT_SECRET_KEY,
            algorithms=[JWT_ALGORITHM],
            options={"require": ["exp"]},
        )
    except jwt.PyJWTError as exc:
        raise HTTPException(
            status_code=401, detail="Could not validate credentials"
        ) from exc


# --------------------------------------------------------------------------
# Dependencies
# --------------------------------------------------------------------------
def _user_from_token(token: str) -> CurrentUser:
    """Decode the JWT, load the user from Mongo and return the identity."""
    claims = decode_access_token(token)
    db = get_db()
    user = db.users.find_one(
        {"$or": [{"userId": claims.get("userId")}, {"email": claims.get("email")}]}
    )
    if not user:
        raise HTTPException(status_code=401, detail="User no longer exists")
    if not user.get("isActive", True):
        raise HTTPException(status_code=401, detail="Account is disabled")
    return CurrentUser(
        userId=user["userId"],
        name=user.get("name", ""),
        email=user.get("email", ""),
        role=UserRole(user.get("role", UserRole.VOLUNTEER.value)),
        deviceId=user.get("deviceId"),
        isActive=user.get("isActive", True),
    )


def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
) -> CurrentUser:
    """Reusable dependency: the authenticated user behind a Bearer JWT."""
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=401,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return _user_from_token(credentials.credentials)


def optional_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
) -> Optional[CurrentUser]:
    """Same as get_current_user but returns None when no token is sent."""
    if credentials is None or credentials.scheme.lower() != "bearer":
        return None
    return _user_from_token(credentials.credentials)


def require_roles(*roles: UserRole):
    """Role-checking dependency factory -> 403 when the role is not allowed."""
    allowed = set(roles)

    def dependency(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
        if user.role not in allowed:
            raise HTTPException(
                status_code=403,
                detail=f"Requires role: {', '.join(r.value for r in allowed)}",
            )
        return user

    return dependency


def public_user(user: dict) -> dict:
    """Strip sensitive fields before returning a user document."""
    data = serialize(user) or {}
    data.pop("passwordHash", None)
    return data


# --------------------------------------------------------------------------
# Endpoints
# --------------------------------------------------------------------------
router = APIRouter(prefix="/auth", tags=["Authentication"])


@router.post("/register", status_code=201, summary="Register a new user")
def register(payload: RegisterRequest):
    """Create an account. Duplicate email -> 409, weak input -> 422."""
    db = get_db()
    email = str(payload.email).strip().lower()
    if db.users.find_one({"email": email}, {"_id": 1}):
        raise HTTPException(status_code=409, detail="Email already registered")

    user = {
        "userId": make_id("user"),
        "name": payload.name.strip(),
        "email": email,
        "passwordHash": hash_password(payload.password),
        "role": payload.role.value,
        "deviceId": payload.deviceId,
        "isActive": True,
        "createdAt": now_utc(),
    }
    try:
        db.users.insert_one(user)
    except DuplicateKeyError:
        raise HTTPException(status_code=409, detail="Email already registered")
    return public_user(user)


@router.post("/login", response_model=None, summary="Login and get a JWT")
def login(payload: LoginRequest):
    """Verify credentials -> 200 with access token, wrong ones -> 401."""
    db = get_db()
    email = str(payload.email).strip().lower()
    user = db.users.find_one({"email": email})
    if not user or not verify_password(payload.password, user.get("passwordHash")):
        raise HTTPException(status_code=401, detail="Invalid email or password")
    if not user.get("isActive", True):
        raise HTTPException(status_code=401, detail="Account is disabled")

    token = create_access_token(user["userId"], user["email"], user["role"])
    return {
        "access_token": token,
        "token_type": "bearer",
        "user": {
            "userId": user["userId"],
            "name": user.get("name", ""),
            "email": user["email"],
            "role": user["role"],
            "deviceId": user.get("deviceId"),
        },
    }


@router.get("/me", summary="Current authenticated user")
def me(user: CurrentUser = Depends(get_current_user)):
    """Return the identity behind the Bearer token (never passwordHash)."""
    db = get_db()
    doc = db.users.find_one({"userId": user.userId}) or {}
    data = public_user(doc)
    if not data:
        data = {
            "userId": user.userId,
            "name": user.name,
            "email": user.email,
            "role": user.role.value,
            "deviceId": user.deviceId,
            "isActive": user.isActive,
        }
    return data


@router.post("/logout", summary="Logout (stateless JWT)")
def logout(user: CurrentUser = Depends(get_current_user)):
    """JWT stays stateless - the client simply drops the token."""
    return {"message": "Logged out. Remove the token on the client.", "userId": user.userId}
