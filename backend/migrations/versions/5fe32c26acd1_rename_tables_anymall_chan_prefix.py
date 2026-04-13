"""rename_tables_anymall_chan_prefix

Rename all tables to use anymall_chan_ prefix for shared database deployment.
Also renames indexes, constraints, and FK references.

Revision ID: 5fe32c26acd1
Revises: ca002cd94239
Create Date: 2026-03-20 14:58:49.773022

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = '5fe32c26acd1'
down_revision: Union[str, Sequence[str], None] = 'ca002cd94239'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# ── Table renames ────────────────────────────────────────────────────────────
TABLE_RENAMES = [
    ("thread_messages", "anymall_chan_thread_messages"),
    ("threads", "anymall_chan_threads"),
    ("active_profile", "anymall_chan_active_profile"),
    ("fact_log", "anymall_chan_fact_log"),
    ("users", "anymall_chan_users"),
]

# ── Index renames (old_name, new_name) ───────────────────────────────────────
INDEX_RENAMES = [
    # thread_messages
    ("ix_thread_messages_thread_id", "ix_anymall_chan_thread_messages_thread_id"),
    # threads
    ("ix_threads_pet_id_status", "ix_anymall_chan_threads_pet_id_status"),
    ("ix_threads_secondary_pet_id", "ix_anymall_chan_threads_secondary_pet_id"),
    ("ix_threads_user_id", "ix_anymall_chan_threads_user_id"),
    ("ix_threads_expires_at", "ix_anymall_chan_threads_expires_at"),
    ("ix_threads_one_active_per_pet", "ix_anymall_chan_threads_one_active_per_pet"),
    # active_profile
    ("ix_active_profile_user_code", "ix_anymall_chan_active_profile_user_code"),
    # fact_log
    ("ix_fact_log_pet_id", "ix_anymall_chan_fact_log_pet_id"),
    ("ix_fact_log_session_id", "ix_anymall_chan_fact_log_session_id"),
    ("ix_fact_log_pet_id_extracted_at", "ix_anymall_chan_fact_log_pet_id_extracted_at"),
    ("ix_fact_log_user_code", "ix_anymall_chan_fact_log_user_code"),
]

# ── Constraint renames (old_name, new_name) ──────────────────────────────────
CONSTRAINT_RENAMES = [
    ("uq_active_profile_pet_field", "uq_anymall_chan_active_profile_pet_field"),
]


def upgrade() -> None:
    """Rename all tables, indexes, and constraints to anymall_chan_ prefix."""

    # 1. Drop FK from thread_messages → threads (references old table name)
    op.drop_constraint(
        "fk_thread_messages_thread_id",
        "thread_messages",
        type_="foreignkey",
    )

    # 2. Rename tables
    for old, new in TABLE_RENAMES:
        op.rename_table(old, new)

    # 3. Rename indexes (PostgreSQL: ALTER INDEX old_name RENAME TO new_name)
    for old, new in INDEX_RENAMES:
        op.execute(f'ALTER INDEX IF EXISTS "{old}" RENAME TO "{new}"')

    # 4. Rename constraints
    for old, new in CONSTRAINT_RENAMES:
        op.execute(
            f'ALTER TABLE anymall_chan_active_profile '
            f'RENAME CONSTRAINT "{old}" TO "{new}"'
        )

    # 5. Recreate FK with new table name
    op.create_foreign_key(
        "anymall_chan_thread_messages_thread_id_fkey",
        "anymall_chan_thread_messages",
        "anymall_chan_threads",
        ["thread_id"],
        ["thread_id"],
        ondelete="CASCADE",
    )


def downgrade() -> None:
    """Revert all renames back to original names."""

    # 1. Drop FK
    op.drop_constraint(
        "anymall_chan_thread_messages_thread_id_fkey",
        "anymall_chan_thread_messages",
        type_="foreignkey",
    )

    # 2. Rename tables back
    for old, new in TABLE_RENAMES:
        op.rename_table(new, old)

    # 3. Rename indexes back
    for old, new in INDEX_RENAMES:
        op.execute(f'ALTER INDEX IF EXISTS "{new}" RENAME TO "{old}"')

    # 4. Rename constraints back
    for old, new in CONSTRAINT_RENAMES:
        op.execute(
            f'ALTER TABLE active_profile '
            f'RENAME CONSTRAINT "{new}" TO "{old}"'
        )

    # 5. Recreate FK with original table name
    op.create_foreign_key(
        "fk_thread_messages_thread_id",
        "thread_messages",
        "threads",
        ["thread_id"],
        ["thread_id"],
        ondelete="CASCADE",
    )
