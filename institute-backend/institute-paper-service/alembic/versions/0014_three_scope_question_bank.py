"""Three-scope question bank: Platform (GLOBAL), Provider-wide and Branch-specific (relaxing and additive)

  * bank_questions_scope_check: a PROVIDER question needs its provider but no longer a branch. branch_id NULL = a provider-wide
    question; otherwise branch_id is the ORIGINATING branch. Every PROVIDER question is shared with all branches of its provider
    (decided in code by provider_id), so nothing is copied per branch. GLOBAL stays "no provider, no branch".
  * ix_bank_questions_provider_subject (provider_id, subject_id, status): provider-wide reads and duplicate checks.
  * question_image_assets.provider_id becomes nullable: NULL = a platform-owned picture (GLOBAL imports), unique per checksum
    (uq_question_image_assets_platform_checksum). Provider pictures keep their (provider_id, checksum) uniqueness.
  * bulk_import_jobs.scope ('GLOBAL' | 'PROVIDER', default 'PROVIDER'); provider_id and branch_id become nullable, kept consistent by
    bulk_import_jobs_scope_check: GLOBAL jobs have neither, PROVIDER jobs have a provider and, for a branch import, the branch.

No row is changed: every existing row already satisfies the new rules (validated by PostgreSQL when the CHECKs are added). The old
code runs unchanged on the upgraded schema, so the schema can be upgraded before the new code is deployed.

Downgrade refuses while a row uses one of the new shapes (a provider-wide question, a platform picture, a GLOBAL or provider-wide
job): they cannot be represented before this revision.

Idempotent for a database whose baseline already created these shapes from today's models. PostgreSQL only, like the other
constraint changes of this chain.

Revision ID: 0014_three_scope_question_bank
Revises: 0013_bulk_imports
"""
import sqlalchemy as sa
from alembic import op

from institute_common.migrations import existing_columns

revision = "0014_three_scope_question_bank"
down_revision = "0013_bulk_imports"
branch_labels = None
depends_on = None

Q_CHECK, JOB_CHECK = "bank_questions_scope_check", "bulk_import_jobs_scope_check"
Q_NEW = "(scope = 'GLOBAL' AND provider_id IS NULL AND branch_id IS NULL) OR (scope = 'PROVIDER' AND provider_id IS NOT NULL)"
Q_OLD = ("(scope = 'GLOBAL' AND provider_id IS NULL AND branch_id IS NULL) OR "
         "(scope = 'PROVIDER' AND provider_id IS NOT NULL AND branch_id IS NOT NULL)")
JOB_RULE = Q_NEW


def upgrade() -> None:
    bind = op.get_bind()
    op.execute(f'ALTER TABLE bank_questions DROP CONSTRAINT IF EXISTS "{Q_CHECK}"')
    op.execute(f'ALTER TABLE bank_questions ADD CONSTRAINT "{Q_CHECK}" CHECK ({Q_NEW})')
    op.execute("CREATE INDEX IF NOT EXISTS ix_bank_questions_provider_subject ON bank_questions (provider_id, subject_id, status)")

    op.execute("ALTER TABLE question_image_assets ALTER COLUMN provider_id DROP NOT NULL")
    op.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_question_image_assets_platform_checksum ON question_image_assets (checksum) "
               "WHERE provider_id IS NULL")

    if "scope" not in existing_columns(bind, "bulk_import_jobs"):
        op.add_column("bulk_import_jobs", sa.Column("scope", sa.String(10), nullable=False, server_default="PROVIDER"))
    op.execute("ALTER TABLE bulk_import_jobs ALTER COLUMN provider_id DROP NOT NULL")
    op.execute("ALTER TABLE bulk_import_jobs ALTER COLUMN branch_id DROP NOT NULL")
    op.execute(f'ALTER TABLE bulk_import_jobs DROP CONSTRAINT IF EXISTS "{JOB_CHECK}"')
    op.execute(f'ALTER TABLE bulk_import_jobs ADD CONSTRAINT "{JOB_CHECK}" CHECK ({JOB_RULE})')
    op.execute("CREATE INDEX IF NOT EXISTS ix_bulk_import_jobs_owner_created ON bulk_import_jobs (scope, provider_id, created_at)")


def downgrade() -> None:
    bind = op.get_bind()
    count = lambda sql: bind.execute(sa.text(sql)).scalar()      # noqa: E731
    wide = count("SELECT count(*) FROM bank_questions WHERE scope = 'PROVIDER' AND branch_id IS NULL")
    platform = count("SELECT count(*) FROM question_image_assets WHERE provider_id IS NULL")
    jobs = count("SELECT count(*) FROM bulk_import_jobs WHERE scope = 'GLOBAL' OR branch_id IS NULL")
    if wide or platform or jobs:
        raise RuntimeError(f"{wide} provider-wide question(s), {platform} platform picture(s) and {jobs} GLOBAL or provider-wide import "
                           "job(s) exist; they cannot be represented before this revision. Downgrade refused.")
    op.execute("DROP INDEX IF EXISTS ix_bulk_import_jobs_owner_created")
    op.execute(f'ALTER TABLE bulk_import_jobs DROP CONSTRAINT IF EXISTS "{JOB_CHECK}"')
    op.execute("ALTER TABLE bulk_import_jobs ALTER COLUMN branch_id SET NOT NULL")
    op.execute("ALTER TABLE bulk_import_jobs ALTER COLUMN provider_id SET NOT NULL")
    op.drop_column("bulk_import_jobs", "scope")
    op.execute("DROP INDEX IF EXISTS uq_question_image_assets_platform_checksum")
    op.execute("ALTER TABLE question_image_assets ALTER COLUMN provider_id SET NOT NULL")
    op.execute("DROP INDEX IF EXISTS ix_bank_questions_provider_subject")
    op.execute(f'ALTER TABLE bank_questions DROP CONSTRAINT IF EXISTS "{Q_CHECK}"')
    op.execute(f'ALTER TABLE bank_questions ADD CONSTRAINT "{Q_CHECK}" CHECK ({Q_OLD})')
