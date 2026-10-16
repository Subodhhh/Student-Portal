"""baseline: adopt the tables this service owns (create the missing ones, keep existing data), install append-only triggers

Revision ID: 0001_organization_baseline
Revises:
"""
from alembic import op

from institute_common.migrations import baseline

revision = "0001_organization_baseline"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    baseline(op.get_bind(), "organization")


def downgrade() -> None:
    # Forward-only by policy: a baseline that adopted existing tables must never drop them.
    pass
