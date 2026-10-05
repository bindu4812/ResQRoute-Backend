"""Dashboard summary for the React frontend (auth required)."""

from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Depends

from app.auth import get_current_user
from app.database import AGING_MINUTES, get_db, now_utc, serialize
from app.schemas import CurrentUser

router = APIRouter(prefix="/dashboard", tags=["Dashboard"])


@router.get("/summary", summary="Aggregated situation overview")
def dashboard_summary(user: CurrentUser = Depends(get_current_user)):
    db = get_db()
    now = now_utc()
    outdated_cutoff = now - timedelta(minutes=AGING_MINUTES)

    active_alerts = db.alerts.count_documents(
        {"$or": [{"expiresAt": None}, {"expiresAt": {"$gt": now}}]}
    )
    outdated_alerts = db.alerts.count_documents({"createdAt": {"$lte": outdated_cutoff}})

    def count_routes(status: str) -> int:
        return db.routes.count_documents({"status": status})

    open_shelters = db.shelters.count_documents({"status": "OPEN"})
    near_full_shelters = db.shelters.count_documents({"status": "NEAR_FULL"})
    full_shelters = db.shelters.count_documents({"status": "FULL"})

    from app.sync import get_pending_sync

    pending_sync = len(get_pending_sync(db))

    return {
        "activeAlerts": active_alerts,
        "safeRoutes": count_routes("SAFE"),
        "blockedRoutes": count_routes("BLOCKED"),
        "cautionRoutes": count_routes("CAUTION"),
        "outdatedAlerts": outdated_alerts,
        "conflictedRoutes": db.routes.count_documents(
            {"verificationStatus": "CONFLICTED"}
        ),
        "openShelters": open_shelters,
        "nearFullShelters": near_full_shelters,
        "fullShelters": full_shelters,
        "pendingSync": pending_sync,
        "generatedAt": serialize({"t": now})["t"],
        "generatedBy": user.userId,
    }
