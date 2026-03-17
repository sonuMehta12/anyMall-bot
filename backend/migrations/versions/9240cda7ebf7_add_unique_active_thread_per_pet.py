"""add_unique_active_thread_per_pet

Revision ID: 9240cda7ebf7
Revises: d65ce8144b53
Create Date: 2026-03-17 17:42:26.226481

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '9240cda7ebf7'
down_revision: Union[str, Sequence[str], None] = 'd65ce8144b53'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add partial unique index: only one active thread per pet_id."""
    op.create_index(
        'ix_threads_one_active_per_pet',
        'threads',
        ['pet_id'],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
    )


def downgrade() -> None:
    """Remove partial unique index."""
    op.drop_index('ix_threads_one_active_per_pet', table_name='threads')
