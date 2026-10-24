"""Student ID allocation: STU + at least six digits (STU000013), from the durable counter `student_id_allocator`.

* allocate() increments the counter with one UPDATE ... RETURNING inside the caller's transaction. PostgreSQL row-locks the counter until
  that transaction ends, so concurrent creations get distinct numbers. If the transaction rolls back, the number was never committed or
  returned to anyone (never issued) and the next creation may use it.
* The counter never decreases and is never computed from the students table: deleting students (even all of them) cannot make an issued
  ID available again. A missing counter row is an error (no silent fallback to MAX+1).
* skip_past() moves the counter beyond an ID found to exist already (e.g. a row inserted by hand), committed on its own."""
from sqlalchemy import case, update
from sqlalchemy.orm import Session

from app.models.student_id_allocator import StudentIdAllocator

PREFIX = "STU"


class AllocatorMissing(RuntimeError):
    pass


def format_id(n: int) -> str:
    return f"{PREFIX}{n:06d}"


def allocate(db: Session) -> str:
    n = db.execute(update(StudentIdAllocator).where(StudentIdAllocator.name == PREFIX)
                   .values(last_value=StudentIdAllocator.last_value + 1).returning(StudentIdAllocator.last_value)).scalar_one_or_none()
    if n is None:
        raise AllocatorMissing("the student_id_allocator row is missing: run the authentication service migrations")
    return format_id(n)


def skip_past(db: Session, student_id: str) -> None:
    """Never issue `student_id` (already taken) again: raise the counter to at least its number. Commits."""
    digits = student_id[len(PREFIX):]
    if student_id.startswith(PREFIX) and digits.isdigit():
        n = int(digits)
        db.execute(update(StudentIdAllocator).where(StudentIdAllocator.name == PREFIX)
                   .values(last_value=case((StudentIdAllocator.last_value < n, n), else_=StudentIdAllocator.last_value)))
        db.commit()
