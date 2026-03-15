"""add threads and thread_messages tables

Revision ID: a3b7c2d4e5f6
Revises: 1e14c749ed04
Create Date: 2026-03-14 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a3b7c2d4e5f6'
down_revision: Union[str, Sequence[str], None] = '1e14c749ed04'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create threads and thread_messages tables."""
    op.create_table('threads',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('thread_id', sa.String(length=128), nullable=False),
        sa.Column('pet_id', sa.String(length=64), nullable=False),
        sa.Column('user_id', sa.String(length=64), nullable=False),
        sa.Column('started_at', sa.String(length=64), nullable=False),
        sa.Column('expires_at', sa.String(length=64), nullable=False),
        sa.Column('status', sa.String(length=16), nullable=False),
        sa.Column('compaction_summary', sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('thread_id'),
    )
    op.create_index('ix_threads_pet_id_status', 'threads',
                     ['pet_id', 'status'], unique=False)

    op.create_table('thread_messages',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('thread_id', sa.String(length=128), nullable=False),
        sa.Column('role', sa.String(length=16), nullable=False),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('timestamp', sa.String(length=64), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_thread_messages_thread_id', 'thread_messages',
                     ['thread_id'], unique=False)


def downgrade() -> None:
    """Drop threads and thread_messages tables."""
    op.drop_index('ix_thread_messages_thread_id', table_name='thread_messages')
    op.drop_table('thread_messages')
    op.drop_index('ix_threads_pet_id_status', table_name='threads')
    op.drop_table('threads')
