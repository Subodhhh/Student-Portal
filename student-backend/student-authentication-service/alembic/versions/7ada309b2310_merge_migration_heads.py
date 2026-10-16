"""merge migration heads

Revision ID: 7ada309b2310
Revises: 0001_create_students, 9ffccd3f23f5
Create Date: 2026-09-28 08:57:59.923449
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '7ada309b2310'
down_revision: Union[str, Sequence[str], None] = ('0001_create_students', '9ffccd3f23f5')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
