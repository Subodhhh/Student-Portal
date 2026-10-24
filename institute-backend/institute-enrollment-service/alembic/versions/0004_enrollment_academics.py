"""Class / Academic Year per provider enrollment: one academic profile per enrollment

Additive only: creates `enrollment_academic_profiles`; no existing table, column or row is changed. Enrollments that existed before have no
profile (reported as incomplete in the portal; Class-specific exams do not reach them until a profile is set).

Revision ID: 0004_enrollment_academics
Revises: 0003_enrollment_batch_subjects
"""
import sqlalchemy as sa
from alembic import op

revision = "0004_enrollment_academics"
down_revision = "0003_enrollment_batch_subjects"
branch_labels = None
depends_on = None

CLASSES = "'6TH', '7TH', '8TH', '9TH', '10TH', '11TH', '12TH', 'DROPPER', 'OTHER'"
YEAR = ("length(academic_year) = 9 AND substr(academic_year, 5, 1) = '-' "
        "AND CAST(substr(academic_year, 6, 4) AS INTEGER) = CAST(substr(academic_year, 1, 4) AS INTEGER) + 1")


def _public_search_path() -> None:
    # Transaction-local: a pooled connection may carry an empty session search_path (see alembic/env.py).
    if op.get_bind().dialect.name == "postgresql":
        op.execute("SET LOCAL search_path TO public")


def upgrade() -> None:
    _public_search_path()
    if "enrollment_academic_profiles" in sa.inspect(op.get_bind()).get_table_names():
        return
    op.create_table(
        "enrollment_academic_profiles",
        sa.Column("enrollment_id", sa.String(36), sa.ForeignKey("enrollments.enrollment_id", ondelete="CASCADE"), primary_key=True),
        sa.Column("class_level", sa.String(12), nullable=False),
        sa.Column("academic_year", sa.String(9), nullable=False),
        sa.Column("other_class_description", sa.String(120)),
        sa.Column("updated_by", sa.Uuid()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.CheckConstraint(f"class_level IN ({CLASSES})", name="eap_class_level_check"),
        sa.CheckConstraint(f"academic_year IS NULL OR ({YEAR}) AND academic_year IS NOT NULL", name="eap_academic_year_check"),
        sa.CheckConstraint("(class_level = 'OTHER' AND other_class_description IS NOT NULL) OR (class_level <> 'OTHER' AND other_class_description IS NULL)",
                           name="eap_other_description_check"),
    )


def downgrade() -> None:
    _public_search_path()
    op.drop_table("enrollment_academic_profiles")
