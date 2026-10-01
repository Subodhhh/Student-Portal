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

    for enrollment, provider, branch, batch in rows:
        if enrollment.enrollment_id not in enrollments:
            enrollments[enrollment.enrollment_id] = {
                "enrollment_id": enrollment.enrollment_id,
                "provider": {
                    "provider_id": str(provider.provider_id),
                    "name": provider.name,
                    "email": provider.email,
                    "phone": provider.phone,
                    "address": provider.address,
                },
                "branch": {
                    "branch_id": str(branch.branch_id),
                    "name": branch.name,
                    "city": branch.city,
                    "state": branch.state,
                    "address": branch.address,
                    "pincode": branch.pincode,
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
                    "start_date": batch.start_date,
                    "end_date": batch.end_date,
                }
            )

    return list(enrollments.values())