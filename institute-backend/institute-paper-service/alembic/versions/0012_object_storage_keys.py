"""Object-stored question pictures (additive)

  * question_image_assets.storage_key (VARCHAR 300, nullable): where an object-stored picture lives in the object store
    (institute_common.objectstore). Never a URL.
  * question_image_assets_storage_check now allows 'db', 'local' and 's3', and the new question_image_assets_storage_key_check keeps
    the two in step: 'db' pictures have no key, object-stored pictures always have one.

Existing pictures are NOT moved: they stay 'db' (bytes in image_data) with a NULL key, which both checks accept. Moving them is a
separate, approved migration plan. Adding a nullable column without a default does not rewrite the table in PostgreSQL.

PostgreSQL only (DROP CONSTRAINT IF EXISTS), like the other constraint changes of this chain.

Revision ID: 0012_object_storage_keys
Revises: 0011_question_taxonomy
"""
import sqlalchemy as sa
from alembic import op

from institute_common.migrations import existing_columns

revision = "0012_object_storage_keys"
down_revision = "0011_question_taxonomy"
branch_labels = None
depends_on = None

TABLE = "question_image_assets"
STORAGE_CHECK = "question_image_assets_storage_check"
KEY_CHECK = "question_image_assets_storage_key_check"


def upgrade() -> None:
    if "storage_key" not in existing_columns(op.get_bind(), TABLE):
        op.add_column(TABLE, sa.Column("storage_key", sa.String(300), nullable=True))
    op.execute(f'ALTER TABLE {TABLE} DROP CONSTRAINT IF EXISTS "{STORAGE_CHECK}"')
    op.execute(f"""ALTER TABLE {TABLE} ADD CONSTRAINT "{STORAGE_CHECK}" CHECK (storage IN ('db','local','s3'))""")
    op.execute(f'ALTER TABLE {TABLE} DROP CONSTRAINT IF EXISTS "{KEY_CHECK}"')
    op.execute(f"""ALTER TABLE {TABLE} ADD CONSTRAINT "{KEY_CHECK}" CHECK ((storage = 'db') = (storage_key IS NULL))""")


def downgrade() -> None:
    # Refuse rather than lose track of pictures whose bytes are only in the object store.
    in_store = op.get_bind().execute(sa.text(f"SELECT count(*) FROM {TABLE} WHERE storage <> 'db'")).scalar()
    if in_store:
        raise RuntimeError(f"{in_store} picture(s) are kept in object storage; they cannot be represented before this revision.")
    op.execute(f'ALTER TABLE {TABLE} DROP CONSTRAINT IF EXISTS "{KEY_CHECK}"')
    op.execute(f'ALTER TABLE {TABLE} DROP CONSTRAINT IF EXISTS "{STORAGE_CHECK}"')
    op.execute(f"""ALTER TABLE {TABLE} ADD CONSTRAINT "{STORAGE_CHECK}" CHECK (storage IN ('db'))""")
    op.drop_column(TABLE, "storage_key")
