"""add compacted_before_id to threads

Revision ID: c5d9e3f7a8b2
Revises: b4c8d2e6f7a1
Create Date: 2026-03-16 18:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "c5d9e3f7a8b2"
down_revision: Union[str, None] = "b4c8d2e6f7a1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "threads",
        sa.Column("compacted_before_id", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("threads", "compacted_before_id")
