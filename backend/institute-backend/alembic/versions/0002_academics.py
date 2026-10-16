"""subjects, provider/batch subject mapping, teaching assignments (additive)

Revision ID: 0002_academics
Revises: 0001_staff_identity
"""
import sqlalchemy as sa
from alembic import op

revision = "0002_academics"
down_revision = "0001_staff_identity"
branch_labels = None
depends_on = None

TS = sa.DateTime(timezone=True)
NOW = sa.text("CURRENT_TIMESTAMP")


def upgrade() -> None:
    op.create_table(
        "subjects",
        sa.Column("subject_id", sa.Uuid(), primary_key=True),
        sa.Column("code", sa.String(30), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="ACTIVE"),
        sa.Column("created_by", sa.Uuid()),
        sa.Column("created_at", TS, nullable=False, server_default=NOW),
        sa.Column("updated_at", TS, nullable=False, server_default=NOW),
        sa.UniqueConstraint("code"),
        sa.CheckConstraint("status IN ('ACTIVE','INACTIVE')", name="subjects_status_check"),
    )
    op.create_table(
        "provider_subjects",
        sa.Column("provider_subject_id", sa.Uuid(), primary_key=True),
        sa.Column("provider_id", sa.Uuid(), sa.ForeignKey("providers.provider_id"), nullable=False),
        sa.Column("subject_id", sa.Uuid(), sa.ForeignKey("subjects.subject_id"), nullable=False),
        sa.Column("created_by", sa.Uuid()),
        sa.Column("created_at", TS, nullable=False, server_default=NOW),
        sa.UniqueConstraint("provider_id", "subject_id", name="uq_provider_subject"),
    )
    op.create_index("ix_provider_subjects_provider_id", "provider_subjects", ["provider_id"])
    op.create_table(
        "batch_subjects",
        sa.Column("batch_subject_id", sa.Uuid(), primary_key=True),
        sa.Column("batch_id", sa.Uuid(), sa.ForeignKey("batches.batch_id"), nullable=False),
        sa.Column("subject_id", sa.Uuid(), sa.ForeignKey("subjects.subject_id"), nullable=False),
        sa.Column("created_by", sa.Uuid()),
        sa.Column("created_at", TS, nullable=False, server_default=NOW),
        sa.UniqueConstraint("batch_id", "subject_id", name="uq_batch_subject"),
    )
    op.create_index("ix_batch_subjects_batch_id", "batch_subjects", ["batch_id"])
    op.create_table(
        "teacher_subject_assignments",
        sa.Column("teaching_id", sa.Uuid(), primary_key=True),
        sa.Column("staff_id", sa.Uuid(), sa.ForeignKey("staff.staff_id"), nullable=False),
        sa.Column("provider_id", sa.Uuid(), sa.ForeignKey("providers.provider_id"), nullable=False),
        sa.Column("branch_id", sa.Uuid(), sa.ForeignKey("branches.branch_id"), nullable=False),
        sa.Column("subject_id", sa.Uuid(), sa.ForeignKey("subjects.subject_id"), nullable=False),
        sa.Column("batch_id", sa.Uuid(), sa.ForeignKey("batches.batch_id")),
        sa.Column("status", sa.String(20), nullable=False, server_default="ACTIVE"),
        sa.Column("created_by", sa.Uuid()),
        sa.Column("created_at", TS, nullable=False, server_default=NOW),
        sa.Column("updated_at", TS, nullable=False, server_default=NOW),
        sa.CheckConstraint("status IN ('ACTIVE','INACTIVE')", name="tsa_status_check"),
    )
    op.create_index("ix_teacher_subject_assignments_staff_id", "teacher_subject_assignments", ["staff_id"])
    for name, cols, where in (
        ("uq_tsa_all_batches", ["staff_id", "branch_id", "subject_id"], "batch_id IS NULL"),
        ("uq_tsa_batch", ["staff_id", "branch_id", "subject_id", "batch_id"], "batch_id IS NOT NULL"),
    ):
        op.create_index(name, "teacher_subject_assignments", cols, unique=True,
                        postgresql_where=sa.text(where), sqlite_where=sa.text(where))


def downgrade() -> None:
    op.drop_table("teacher_subject_assignments")
    op.drop_table("batch_subjects")
    op.drop_table("provider_subjects")
    op.drop_table("subjects")
