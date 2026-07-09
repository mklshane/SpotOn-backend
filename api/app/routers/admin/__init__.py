"""Admin directory-management endpoints. Every route requires users.is_admin
(see core.security.require_admin — checked per request against the DB).

Write semantics the routers follow (documented once here):
- get_session never auto-commits → every write endpoint commits explicitly.
- updated_at is set explicitly on doctors/facilities writes so the mobile
  /sync feed (updated_at cursor) picks changes up regardless of DB triggers.
- Deletes: facility soft-deletes to status='excluded' (propagates via /sync);
  doctor delete is hard and removes booking_links + doctor_facility children
  explicitly (no reliance on FK cascade); platform soft-deletes to
  is_active=false. Hard deletes do NOT propagate to the mobile app's offline
  cache until a full refresh — /sync has no delete tombstones.
"""
from fastapi import APIRouter, Depends

from app.core.security import require_admin
from app.core.vocab import SERVICES, SPECIALTIES
from app.schemas.admin import (
    FACILITY_KINDS,
    FACILITY_STATUSES,
    FACILITY_TYPES,
    AdminMetaOut,
)

from app.routers.admin import doctors, facilities, platforms  # noqa: E402

router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(require_admin)])


@router.get("/meta", response_model=AdminMetaOut)
async def admin_meta() -> AdminMetaOut:
    return AdminMetaOut(
        services=sorted(SERVICES),
        specialties=sorted(SPECIALTIES),
        facility_statuses=FACILITY_STATUSES,
        facility_types=FACILITY_TYPES,
        types=FACILITY_KINDS,
    )


router.include_router(facilities.router)
router.include_router(doctors.router)
router.include_router(platforms.router)
