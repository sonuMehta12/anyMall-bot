"""add_suggested_questions_table

Revision ID: f1a2b3c4d5e6
Revises: 5fe32c26acd1
Create Date: 2026-03-28 00:01:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'f1a2b3c4d5e6'
down_revision: Union[str, Sequence[str], None] = '5fe32c26acd1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create anymall_chan_suggested_questions table with indexes."""
    op.create_table(
        'anymall_chan_suggested_questions',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('user_code', sa.String(length=64), nullable=False),
        sa.Column('language', sa.String(length=10), nullable=False),
        sa.Column('questions', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            'generated_at',
            sa.DateTime(timezone=True),
            server_default=sa.text('NOW()'),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('user_code', 'language', name='uq_suggested_questions'),
    )
    op.create_index(
        'idx_sq_user_code',
        'anymall_chan_suggested_questions',
        ['user_code'],
    )
    op.create_index(
        'idx_sq_generated_at',
        'anymall_chan_suggested_questions',
        ['generated_at'],
    )


def downgrade() -> None:
    """Drop anymall_chan_suggested_questions table."""
    op.drop_index('idx_sq_generated_at', table_name='anymall_chan_suggested_questions')
    op.drop_index('idx_sq_user_code', table_name='anymall_chan_suggested_questions')
    op.drop_table('anymall_chan_suggested_questions')
