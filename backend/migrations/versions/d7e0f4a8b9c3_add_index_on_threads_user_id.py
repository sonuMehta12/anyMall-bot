"""add index on threads.user_id

Revision ID: d7e0f4a8b9c3
Revises: c5d9e3f7a8b2
Create Date: 2026-03-16 22:00:00.000000

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "d7e0f4a8b9c3"
down_revision: Union[str, None] = "c5d9e3f7a8b2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index("ix_threads_user_id", "threads", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_threads_user_id", table_name="threads")
