"""student identity boundary: enrollments reference the global Student ID without a foreign key

The Institute database (institute_portal_test) and the Student database (subodh_dev) are separate. Student identities are created
and owned by the Student authentication service, so `enrollments.student_id` cannot reference a `students` table here (the copy in
this database is stale and would reject every newly created student). Drops only that constraint and adds a lookup index; no row
is changed.

Revision ID: 0002_enrollment_student_identity
Revises: 0001_enrollment_baseline
"""
import sqlalchemy as sa
from alembic import op

revision = "0002_enrollment_student_identity"
down_revision = "0001_enrollment_baseline"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    for fk in insp.get_foreign_keys("enrollments"):
        if fk.get("referred_table") == "students" and fk.get("name"):
            op.drop_constraint(fk["name"], "enrollments", type_="foreignkey")
    if "ix_enrollments_student_id" not in {i["name"] for i in insp.get_indexes("enrollments")}:
        op.create_index("ix_enrollments_student_id", "enrollments", ["student_id"])


def downgrade() -> None:
    # Forward-only by policy: re-adding the constraint would reject enrollments of students created after this revision.
    pass
