"""pet_id string to integer

Revision ID: b4c8d2e6f7a1
Revises: a3b7c2d4e5f6
Create Date: 2026-03-16 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "b4c8d2e6f7a1"
down_revision: Union[str, None] = "a3b7c2d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Drop all tables and recreate with pet_id as Integer.

    No production data exists — only dev seed data for Luna/Shara.
    A clean drop+create is simpler than ALTER COLUMN on 5 tables
    with index and constraint rebuilds.
    """
    # Drop in reverse dependency order
    op.drop_table("thread_messages")
    op.drop_table("threads")
    op.drop_table("fact_log")
    op.drop_table("active_profile")
    op.drop_table("users")
    op.drop_table("pets")

    # Recreate: pets
    op.create_table(
        "pets",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("pet_id", sa.Integer, unique=True, nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("species", sa.String(32), nullable=False, server_default="dog"),
        sa.Column("breed", sa.String(128), nullable=False, server_default="unknown"),
        sa.Column("date_of_birth", sa.String(16), nullable=False, server_default="unknown"),
        sa.Column("sex", sa.String(16), nullable=False, server_default="unknown"),
        sa.Column("life_stage", sa.String(16), nullable=False, server_default="adult"),
    )

    # Recreate: users
    op.create_table(
        "users",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.String(64), unique=True, nullable=False),
        sa.Column("pet_id", sa.Integer, nullable=False),
        sa.Column("session_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("relationship_summary", sa.Text, nullable=False, server_default=""),
        sa.Column("updated_at", sa.String(64), nullable=False, server_default=""),
    )

    # Recreate: active_profile
    op.create_table(
        "active_profile",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("pet_id", sa.Integer, nullable=False),
        sa.Column("field_key", sa.String(128), nullable=False),
        sa.Column("value", sa.Text, nullable=False),
        sa.Column("confidence", sa.Float, nullable=True),
        sa.Column("source_rank", sa.String(32), nullable=True),
        sa.Column("time_scope", sa.String(16), nullable=True),
        sa.Column("source_quote", sa.Text, nullable=True),
        sa.Column("updated_at", sa.String(64), nullable=True),
        sa.Column("session_id", sa.String(128), nullable=True),
        sa.Column("status", sa.String(16), nullable=True),
        sa.Column("change_detected", sa.Text, nullable=True),
        sa.Column("trend_flag", sa.String(64), nullable=True),
        sa.UniqueConstraint("pet_id", "field_key", name="uq_active_profile_pet_field"),
    )

    # Recreate: fact_log
    op.create_table(
        "fact_log",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("pet_id", sa.Integer, nullable=False),
        sa.Column("session_id", sa.String(128), nullable=False),
        sa.Column("field_key", sa.String(128), nullable=False),
        sa.Column("value", sa.Text, nullable=False),
        sa.Column("confidence", sa.Float, nullable=False),
        sa.Column("source_rank", sa.String(32), nullable=False, server_default="explicit_owner"),
        sa.Column("time_scope", sa.String(16), nullable=False, server_default="current"),
        sa.Column("uncertainty", sa.Text, nullable=False, server_default=""),
        sa.Column("source_quote", sa.Text, nullable=False, server_default=""),
        sa.Column("timestamp", sa.String(64), nullable=True),
        sa.Column("needs_clarification", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("extracted_at", sa.String(64), nullable=False),
    )
    op.create_index("ix_fact_log_pet_id", "fact_log", ["pet_id"])
    op.create_index("ix_fact_log_session_id", "fact_log", ["session_id"])

    # Recreate: threads
    op.create_table(
        "threads",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("thread_id", sa.String(128), unique=True, nullable=False),
        sa.Column("pet_id", sa.Integer, nullable=False),
        sa.Column("user_id", sa.String(64), nullable=False),
        sa.Column("started_at", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.String(64), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
        sa.Column("compaction_summary", sa.Text, nullable=True),
    )
    op.create_index("ix_threads_pet_id_status", "threads", ["pet_id", "status"])

    # Recreate: thread_messages
    op.create_table(
        "thread_messages",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("thread_id", sa.String(128), nullable=False),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("timestamp", sa.String(64), nullable=False),
    )
    op.create_index("ix_thread_messages_thread_id", "thread_messages", ["thread_id"])


def downgrade() -> None:
    """Revert to string pet_id — drop and recreate with String(64)."""
    op.drop_table("thread_messages")
    op.drop_table("threads")
    op.drop_table("fact_log")
    op.drop_table("active_profile")
    op.drop_table("users")
    op.drop_table("pets")

    # Recreate with original String(64) pet_id
    op.create_table(
        "pets",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("pet_id", sa.String(64), unique=True, nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("species", sa.String(32), nullable=False, server_default="dog"),
        sa.Column("breed", sa.String(128), nullable=False, server_default="unknown"),
        sa.Column("date_of_birth", sa.String(16), nullable=False, server_default="unknown"),
        sa.Column("sex", sa.String(16), nullable=False, server_default="unknown"),
        sa.Column("life_stage", sa.String(16), nullable=False, server_default="adult"),
    )

    op.create_table(
        "users",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.String(64), unique=True, nullable=False),
        sa.Column("pet_id", sa.String(64), nullable=False),
        sa.Column("session_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("relationship_summary", sa.Text, nullable=False, server_default=""),
        sa.Column("updated_at", sa.String(64), nullable=False, server_default=""),
    )

    op.create_table(
        "active_profile",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("pet_id", sa.String(64), nullable=False),
        sa.Column("field_key", sa.String(128), nullable=False),
        sa.Column("value", sa.Text, nullable=False),
        sa.Column("confidence", sa.Float, nullable=True),
        sa.Column("source_rank", sa.String(32), nullable=True),
        sa.Column("time_scope", sa.String(16), nullable=True),
        sa.Column("source_quote", sa.Text, nullable=True),
        sa.Column("updated_at", sa.String(64), nullable=True),
        sa.Column("session_id", sa.String(128), nullable=True),
        sa.Column("status", sa.String(16), nullable=True),
        sa.Column("change_detected", sa.Text, nullable=True),
        sa.Column("trend_flag", sa.String(64), nullable=True),
        sa.UniqueConstraint("pet_id", "field_key", name="uq_active_profile_pet_field"),
    )

    op.create_table(
        "fact_log",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("pet_id", sa.String(64), nullable=False),
        sa.Column("session_id", sa.String(128), nullable=False),
        sa.Column("field_key", sa.String(128), nullable=False),
        sa.Column("value", sa.Text, nullable=False),
        sa.Column("confidence", sa.Float, nullable=False),
        sa.Column("source_rank", sa.String(32), nullable=False, server_default="explicit_owner"),
        sa.Column("time_scope", sa.String(16), nullable=False, server_default="current"),
        sa.Column("uncertainty", sa.Text, nullable=False, server_default=""),
        sa.Column("source_quote", sa.Text, nullable=False, server_default=""),
        sa.Column("timestamp", sa.String(64), nullable=True),
        sa.Column("needs_clarification", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("extracted_at", sa.String(64), nullable=False),
    )
    op.create_index("ix_fact_log_pet_id", "fact_log", ["pet_id"])
    op.create_index("ix_fact_log_session_id", "fact_log", ["session_id"])

    op.create_table(
        "threads",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("thread_id", sa.String(128), unique=True, nullable=False),
        sa.Column("pet_id", sa.String(64), nullable=False),
        sa.Column("user_id", sa.String(64), nullable=False),
        sa.Column("started_at", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.String(64), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
        sa.Column("compaction_summary", sa.Text, nullable=True),
    )
    op.create_index("ix_threads_pet_id_status", "threads", ["pet_id", "status"])

    op.create_table(
        "thread_messages",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("thread_id", sa.String(128), nullable=False),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("timestamp", sa.String(64), nullable=False),
    )
    op.create_index("ix_thread_messages_thread_id", "thread_messages", ["thread_id"])
