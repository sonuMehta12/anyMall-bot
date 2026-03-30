"""add_pet_id_to_suggested_questions

Revision ID: b1c2d3e4f5a6
Revises: a7b8c9d0e1f2
Create Date: 2026-03-29 00:00:00.000000

Per-pet redesign: add pet_id column to anymall_chan_suggested_questions.
Changes UNIQUE from (user_code, language) → (user_code, language, pet_id)
so each pet has its own 10-question row.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b1c2d3e4f5a6'
down_revision: Union[str, Sequence[str], None] = 'a7b8c9d0e1f2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add pet_id column and update UNIQUE constraint."""
    # Add pet_id with default 0 so existing rows (if any) don't violate NOT NULL
    op.add_column(
        'anymall_chan_suggested_questions',
        sa.Column('pet_id', sa.Integer(), nullable=False, server_default='0'),
    )

    # Drop old unique constraint on (user_code, language)
    op.drop_constraint(
        'uq_suggested_questions',
        'anymall_chan_suggested_questions',
        type_='unique',
    )

    # Create new unique constraint on (user_code, language, pet_id)
    op.create_unique_constraint(
        'uq_suggested_questions',
        'anymall_chan_suggested_questions',
        ['user_code', 'language', 'pet_id'],
    )

    # Add index on pet_id for nightly-job lookups
    op.create_index(
        'idx_sq_pet_id',
        'anymall_chan_suggested_questions',
        ['pet_id'],
    )


def downgrade() -> None:
    """Reverse: remove pet_id column, restore original UNIQUE constraint."""
    op.drop_index('idx_sq_pet_id', table_name='anymall_chan_suggested_questions')
    op.drop_constraint(
        'uq_suggested_questions',
        'anymall_chan_suggested_questions',
        type_='unique',
    )
    op.create_unique_constraint(
        'uq_suggested_questions',
        'anymall_chan_suggested_questions',
        ['user_code', 'language'],
    )
    op.drop_column('anymall_chan_suggested_questions', 'pet_id')
