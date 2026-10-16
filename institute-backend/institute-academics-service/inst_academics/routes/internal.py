"""Internal API of the academics service."""
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from institute_common.database import get_db
from institute_common.internal_http import require_internal_key
from institute_common.models.academics import TeacherSubjectAssignment

router = APIRouter(prefix="/internal/v1", tags=["Internal"], dependencies=[Depends(require_internal_key)])


class Retire(BaseModel):
    staff_id: UUID
    provider_id: UUID


@router.post("/teaching/retire")
def retire_teaching(body: Retire, db: Session = Depends(get_db)):
    """Called by the identity service when a staff member loses their TEACHER role of a provider: retire the dependent teaching rows."""
    rows = db.scalars(select(TeacherSubjectAssignment).where(TeacherSubjectAssignment.staff_id == body.staff_id,
                                                             TeacherSubjectAssignment.provider_id == body.provider_id,
                                                             TeacherSubjectAssignment.status == "ACTIVE")).all()
    for t in rows:
        t.status = "INACTIVE"
    db.commit()
    return {"retired": len(rows)}
