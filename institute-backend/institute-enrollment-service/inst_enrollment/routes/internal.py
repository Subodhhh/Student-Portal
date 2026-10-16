"""Internal API of the enrollment service (service key only, never proxied by the gateway)."""
from fastapi import APIRouter, Depends

from institute_common.internal_http import require_internal_key
from inst_enrollment.services import reference_sync

router = APIRouter(prefix="/internal/v1", tags=["Internal"], dependencies=[Depends(require_internal_key)])


@router.post("/reference-sync")
def sync_now():
    """Push changed providers/branches/batches/enrollments to the Student database now (used before exam delivery)."""
    return reference_sync.run_once()


@router.get("/reference-sync")
def sync_status():
    return reference_sync.status()
