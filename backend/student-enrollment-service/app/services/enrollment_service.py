from sqlalchemy.orm import Session

from app.repositories.enrollment_repository import get_student_enrollments


def get_my_enrollments(
    db: Session,
    student_id: str,
):
    rows = get_student_enrollments(
        db=db,
        student_id=student_id,
    )

    enrollments = {}

    for enrollment, provider, batch in rows:
        if enrollment.enrollment_id not in enrollments:
            enrollments[enrollment.enrollment_id] = {
                "enrollment_id": enrollment.enrollment_id,
                "provider": {
                    "provider_id": str(provider.provider_id),
                    "name": provider.name,
                },
                "status": enrollment.status,
                "enrolled_at": enrollment.enrolled_at.date(),
                "batches": [],
            }

        if batch:
            enrollments[enrollment.enrollment_id]["batches"].append(
                {
                    "batch_id": str(batch.batch_id),
                    "name": batch.name,
                }
            )

    return list(enrollments.values())