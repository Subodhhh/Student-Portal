"""Internal API of the paper service: the only way another service may change paper sharing."""
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from institute_common.core import audit
from institute_common.core.security import api_error
from institute_common.database import get_db
from institute_common.internal_http import require_internal_key
from institute_common.models.papers import Paper, PaperShare, PaperVersion

router = APIRouter(prefix="/internal/v1", tags=["Internal"], dependencies=[Depends(require_internal_key)])


class ShareGrant(BaseModel):
    paper_id: UUID
    owner_provider_id: UUID
    shared_with_provider_id: UUID
    granted_by: UUID


@router.post("/paper-shares")
def grant_share(body: ShareGrant, db: Session = Depends(get_db)):
    """Idempotent: grants `shared_with_provider_id` USE of a published paper (never access to its content). The caller (exam service)
    has already established that the owner's decision maker agreed; ownership and publication are re-checked here."""
    paper = db.get(Paper, body.paper_id)
    if not paper or paper.provider_id != body.owner_provider_id:
        raise api_error(404, "NOT_FOUND", "Paper not found for that owner.")
    if body.shared_with_provider_id == paper.provider_id:
        raise api_error(422, "INVALID_SHARE", "Choose a different provider.")
    if not db.scalar(select(PaperVersion).where(PaperVersion.paper_id == paper.paper_id, PaperVersion.status == "PUBLISHED")):
        raise api_error(409, "NOT_PUBLISHED", "Only papers with a published version can be shared.")
    share = db.scalar(select(PaperShare).where(PaperShare.paper_id == paper.paper_id,
                                               PaperShare.shared_with_provider_id == body.shared_with_provider_id))
    created = share is None or share.status != "ACTIVE"
    if share:
        share.status = "ACTIVE"
    else:
        db.add(PaperShare(paper_id=paper.paper_id, owner_provider_id=paper.provider_id,
                          shared_with_provider_id=body.shared_with_provider_id, granted_by=body.granted_by))
    db.commit()
    if created:
        audit.record(db, "paper.share", "SUCCESS", actor=body.granted_by, target_type="paper", target_id=paper.paper_id,
                     provider_id=paper.provider_id, details={"with": str(body.shared_with_provider_id), "via": "composite-request"})
    return {"created": created, "status": "ACTIVE"}
