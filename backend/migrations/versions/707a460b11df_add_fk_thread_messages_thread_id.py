"""add_fk_thread_messages_thread_id

Revision ID: 707a460b11df
Revises: 9240cda7ebf7
Create Date: 2026-03-17 17:51:47.349974

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '707a460b11df'
down_revision: Union[str, Sequence[str], None] = '9240cda7ebf7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add FK constraint: thread_messages.thread_id -> threads.thread_id."""
    # Clean orphaned messages first (defensive — shouldn't exist in normal operation)
    op.execute("""
        DELETE FROM thread_messages
        WHERE thread_id NOT IN (SELECT thread_id FROM threads)
    """)
    op.create_foreign_key(
        'fk_thread_messages_thread_id',
        'thread_messages', 'threads',
        ['thread_id'], ['thread_id'],
        ondelete='CASCADE',
    )


def downgrade() -> None:
    """Remove FK constraint."""
    op.drop_constraint('fk_thread_messages_thread_id', 'thread_messages', type_='foreignkey')
