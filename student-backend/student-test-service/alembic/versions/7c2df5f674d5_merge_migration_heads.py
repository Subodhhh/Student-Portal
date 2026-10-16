"""merge migration heads

Revision ID: 7c2df5f674d5
Revises: 761d293bf7cb, add_question_subject
Create Date: 2026-10-05 14:20:01.957317
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa



revision: str = '7c2df5f674d5'
down_revision: Union[str, Sequence[str], None] = ('761d293bf7cb', 'add_question_subject')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass