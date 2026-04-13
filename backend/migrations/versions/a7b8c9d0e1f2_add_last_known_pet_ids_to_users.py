"""add_last_known_pet_ids_to_users

Revision ID: a7b8c9d0e1f2
Revises: f1a2b3c4d5e6
Create Date: 2026-03-28 00:02:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'a7b8c9d0e1f2'
down_revision: Union[str, Sequence[str], None] = 'f1a2b3c4d5e6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add last_known_pet_ids JSONB column to anymall_chan_users."""
    op.add_column(
        'anymall_chan_users',
        sa.Column(
            'last_known_pet_ids',
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=True,
        ),
    )


def downgrade() -> None:
    """Drop last_known_pet_ids from anymall_chan_users."""
    op.drop_column('anymall_chan_users', 'last_known_pet_ids')
