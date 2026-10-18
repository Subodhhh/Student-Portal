"""Test -> Create Test: multi-branch audiences and idempotent publication (additive)

  * exam_plan_branches: whole branches targeted by a TARGETED-audience plan (new table).
  * exam_plans.audience_type: adds TARGETED (union of whole branches, batches of any branch of the provider, listed students).
  * exam_plans.client_request_id: the Create Test request's idempotency key (unique, nullable): a retried publication finds its
    plan instead of creating a second one.
Existing plans are untouched: the new column is nullable and the other audience types keep their meaning.

Revision ID: 0005_exam_targeted_tests
Revises: 0004_exam_tests_audience
"""
import sqlalchemy as sa
from alembic import op

from institute_common.migrations import existing_checks, existing_columns, existing_indexes

revision = "0005_exam_targeted_tests"
down_revision = "0004_exam_tests_audience"
branch_labels = None
depends_on = None

AUDIENCE = "audience_type IN ('BATCHES', 'BRANCH', 'INSTITUTE', 'STUDENTS', 'TARGETED')"


def upgrade() -> None:
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())      # a fresh database already has everything from the baseline
    if "exam_plan_branches" not in tables:
        op.create_table(
            "exam_plan_branches",
            sa.Column("plan_branch_id", sa.Uuid(), primary_key=True),
            sa.Column("plan_id", sa.Uuid(), sa.ForeignKey("exam_plans.plan_id"), nullable=False),
            sa.Column("branch_id", sa.Uuid(), sa.ForeignKey("branches.branch_id"), nullable=False),
            sa.UniqueConstraint("plan_id", "branch_id", name="uq_exam_plan_branch"),
        )
        op.create_index("ix_exam_plan_branches_plan_id", "exam_plan_branches", ["plan_id"])
    if "client_request_id" not in existing_columns(bind, "exam_plans"):
        op.add_column("exam_plans", sa.Column("client_request_id", sa.String(100)))
    uniques = {u["name"] for u in sa.inspect(bind).get_unique_constraints("exam_plans")} | existing_indexes(bind, "exam_plans")
    if "exam_plans_client_request_id_key" not in uniques:
        op.create_unique_constraint("exam_plans_client_request_id_key", "exam_plans", ["client_request_id"])
    checks = existing_checks(bind, "exam_plans")
    with op.batch_alter_table("exam_plans") as b:
        if "exam_plans_audience_check" in checks:
            b.drop_constraint("exam_plans_audience_check", type_="check")
        b.create_check_constraint("exam_plans_audience_check", AUDIENCE)


def downgrade() -> None:
    # Forward-only by policy (exam history).
    pass
