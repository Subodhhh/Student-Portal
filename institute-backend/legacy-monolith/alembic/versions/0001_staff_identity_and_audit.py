"""staff identity, role assignments, tokens, audit log (additive)

Requires the enrollment chain (providers, branches) to be applied first.

Revision ID: 0001_staff_identity
Revises:
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001_staff_identity"
down_revision: Union[str, Sequence[str], None] = None
branch_labels = None
depends_on = None

TS = sa.DateTime(timezone=True)
NOW = sa.text("CURRENT_TIMESTAMP")
INET = postgresql.INET().with_variant(sa.String(45), "sqlite")
JSONT = postgresql.JSONB().with_variant(sa.JSON(), "sqlite")


def upgrade() -> None:
    op.create_table(
        "staff",
        sa.Column("staff_id", sa.Uuid(), primary_key=True),
        sa.Column("staff_code", sa.String(20), nullable=False),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("email_verified_at", TS),
        sa.Column("full_name", sa.String(255), nullable=False),
        sa.Column("phone", sa.String(20)),
        sa.Column("password_hash", sa.String(255)),
        sa.Column("password_changed_at", TS),
        sa.Column("status", sa.String(20), nullable=False, server_default="ACTIVE"),
        sa.Column("created_by", sa.Uuid()),
        sa.Column("created_ip_address", INET),
        sa.Column("updated_by", sa.Uuid()),
        sa.Column("updated_ip_address", INET),
        sa.Column("deactivated_at", TS),
        sa.Column("created_at", TS, nullable=False, server_default=NOW),
        sa.Column("updated_at", TS, nullable=False, server_default=NOW),
        sa.UniqueConstraint("staff_code"),
        sa.UniqueConstraint("email"),
        sa.CheckConstraint("status IN ('ACTIVE', 'INACTIVE')", name="staff_status_check"),
    )
    op.create_table(
        "staff_tokens",
        sa.Column("token_id", sa.Uuid(), primary_key=True),
        sa.Column("staff_id", sa.Uuid(), sa.ForeignKey("staff.staff_id", ondelete="CASCADE"), nullable=False),
        sa.Column("purpose", sa.String(20), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("expires_at", TS, nullable=False),
        sa.Column("used_at", TS),
        sa.Column("created_at", TS, nullable=False, server_default=NOW),
        sa.UniqueConstraint("token_hash"),
        sa.CheckConstraint("purpose IN ('EMAIL_VERIFY', 'PASSWORD_RESET')", name="staff_tokens_purpose_check"),
    )
    op.create_index("ix_staff_tokens_staff_id", "staff_tokens", ["staff_id"])
    op.create_table(
        "staff_role_assignments",
        sa.Column("assignment_id", sa.Uuid(), primary_key=True),
        sa.Column("staff_id", sa.Uuid(), sa.ForeignKey("staff.staff_id", ondelete="CASCADE"), nullable=False),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("provider_id", sa.Uuid(), sa.ForeignKey("providers.provider_id")),
        sa.Column("branch_id", sa.Uuid(), sa.ForeignKey("branches.branch_id")),
        sa.Column("status", sa.String(20), nullable=False, server_default="ACTIVE"),
        sa.Column("created_by", sa.Uuid()),
        sa.Column("created_at", TS, nullable=False, server_default=NOW),
        sa.Column("updated_at", TS, nullable=False, server_default=NOW),
        sa.CheckConstraint(
            "role IN ('SUPER_ADMIN','ADMIN','PROVIDER_ADMIN','BRANCH_ADMIN','TEACHER')",
            name="staff_role_assignments_role_check"),
        sa.CheckConstraint("status IN ('ACTIVE', 'INACTIVE')", name="staff_role_assignments_status_check"),
        sa.CheckConstraint(
            "(role IN ('SUPER_ADMIN','ADMIN') AND provider_id IS NULL AND branch_id IS NULL) "
            "OR (role IN ('PROVIDER_ADMIN','TEACHER') AND provider_id IS NOT NULL AND branch_id IS NULL) "
            "OR (role = 'BRANCH_ADMIN' AND provider_id IS NOT NULL AND branch_id IS NOT NULL)",
            name="staff_role_assignments_scope_check"),
    )
    op.create_index("ix_staff_role_assignments_staff_id", "staff_role_assignments", ["staff_id"])
    for name, cols, where in (
        ("uq_sra_global", ["staff_id", "role"], "provider_id IS NULL"),
        ("uq_sra_provider", ["staff_id", "role", "provider_id"], "provider_id IS NOT NULL AND branch_id IS NULL"),
        ("uq_sra_branch", ["staff_id", "role", "provider_id", "branch_id"], "branch_id IS NOT NULL"),
    ):
        op.create_index(name, "staff_role_assignments", cols, unique=True,
                        postgresql_where=sa.text(where), sqlite_where=sa.text(where))
    op.create_table(
        "institute_audit_logs",
        sa.Column("audit_id", sa.Uuid(), primary_key=True),
        sa.Column("occurred_at", TS, nullable=False, server_default=NOW),
        sa.Column("actor_staff_id", sa.Uuid()),
        sa.Column("actor_service", sa.String(64)),
        sa.Column("action", sa.String(80), nullable=False),
        sa.Column("target_type", sa.String(60)),
        sa.Column("target_id", sa.String(64)),
        sa.Column("provider_id", sa.Uuid()),
        sa.Column("branch_id", sa.Uuid()),
        sa.Column("outcome", sa.String(20), nullable=False),
        sa.Column("reason", sa.String(1000)),
        sa.Column("ip_address", INET),
        sa.Column("details", JSONT),
    )
    for col in ("occurred_at", "actor_staff_id", "action", "provider_id"):
        op.create_index(f"ix_institute_audit_logs_{col}", "institute_audit_logs", [col])
    if op.get_bind().dialect.name == "postgresql":
        # Append-only: ordinary application roles cannot rewrite history.
        op.execute("""
            CREATE FUNCTION institute_audit_immutable() RETURNS trigger AS $$
            BEGIN RAISE EXCEPTION 'institute_audit_logs is append-only'; END;
            $$ LANGUAGE plpgsql;
        """)
        op.execute("""
            CREATE TRIGGER institute_audit_logs_immutable
            BEFORE UPDATE OR DELETE ON institute_audit_logs
            FOR EACH ROW EXECUTE FUNCTION institute_audit_immutable();
        """)


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute("DROP TRIGGER IF EXISTS institute_audit_logs_immutable ON institute_audit_logs")
        op.execute("DROP FUNCTION IF EXISTS institute_audit_immutable()")
    op.drop_table("institute_audit_logs")
    op.drop_table("staff_role_assignments")
    op.drop_table("staff_tokens")
    op.drop_table("staff")
