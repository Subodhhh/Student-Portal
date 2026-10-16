from sqlalchemy.orm import Session

from app.models.batch import Batch
from app.models.branch import Branch
from app.models.enrollment import Enrollment
from app.models.enrollment_batch import EnrollmentBatch
from app.models.provider import Provider


def get_student_enrollments(
    db: Session,
    student_id: str,
):
    rows = (
        db.query(
            Enrollment,
            Provider,
            Branch,
            Batch,
        )
        .join(
            Provider,
            Enrollment.provider_id == Provider.provider_id,
        )
        .join(
            Branch,
            Enrollment.branch_id == Branch.branch_id,
        )
        .outerjoin(
            EnrollmentBatch,
            Enrollment.enrollment_id == EnrollmentBatch.enrollment_id,
        )
        .outerjoin(
            Batch,
            EnrollmentBatch.batch_id == Batch.batch_id,
        )
        .filter(
            Enrollment.student_id == student_id,
        )
        .order_by(
            Enrollment.enrolled_at.desc(),
        )
        .all()
    )

    return rows