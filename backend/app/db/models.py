# app/db/models.py
#
# SQLAlchemy ORM models for AnyMall-chan backend.
#
# Tables:
#   Pet            → static pet identity (pet_id is Integer from AALDA)
#   User           → owner relationship data (user_id is x-user-code string)
#   ActiveProfile  → current best-known facts per field
#   FactLog        → append-only audit trail
#   Thread         → 24-hour conversation windows
#   ThreadMessage  → individual messages within a thread
#
# pet_id is Integer everywhere — matches AALDA API (e.g. 143, 149).
# user_id is String(64) — stores x-user-code (e.g. "3AOU9K1PWH").
#
# Each model has a to_dict() or to_dict_entry() method that returns the
# EXACT same dict shape the rest of the code expects.
#
# The _pet_history special key:
#   Stored as a row in active_profile with field_key="_pet_history".
#   Only the `value` column is used (holds the narrative string).
#   All metadata columns (confidence, source_rank, etc.) are NULL.
#   to_dict_entry() returns the raw string for this key.

from sqlalchemy import (
    Boolean,
    Float,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


# ── Base class ───────────────────────────────────────────────────────────────
# All ORM models inherit from this.  Alembic uses Base.metadata to discover
# tables when generating migrations (autogenerate).

class Base(DeclarativeBase):
    """Base class for all ORM models."""
    pass


# ── Pet ──────────────────────────────────────────────────────────────────────

class Pet(Base):
    """
    Static pet identity — set at onboarding, rarely changes.

    pet_id is the integer ID from the AALDA API (e.g. 143).
    One row per pet.  Multi-pet supported via AALDA integration.
    """
    __tablename__ = "pets"

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True)
    pet_id: Mapped[int] = mapped_column(
        Integer, unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    species: Mapped[str] = mapped_column(
        String(32), nullable=False, default="dog")
    breed: Mapped[str] = mapped_column(
        String(128), nullable=False, default="unknown")
    date_of_birth: Mapped[str] = mapped_column(
        String(16), nullable=False, default="unknown")
    sex: Mapped[str] = mapped_column(
        String(16), nullable=False, default="unknown")
    life_stage: Mapped[str] = mapped_column(
        String(16), nullable=False, default="adult")

    def __repr__(self) -> str:
        return f"<Pet pet_id={self.pet_id!r} name={self.name!r}>"

    def to_dict(self) -> dict:
        """Return the same dict shape as pet_profile.json."""
        return {
            "pet_id": self.pet_id,
            "name": self.name,
            "species": self.species,
            "breed": self.breed,
            "date_of_birth": self.date_of_birth,
            "sex": self.sex,
            "life_stage": self.life_stage,
        }


# ── User ─────────────────────────────────────────────────────────────────────

class User(Base):
    """
    Owner relationship data.

    user_id stores the x-user-code string (e.g. "3AOU9K1PWH").
    pet_id is the integer ID from AALDA (e.g. 143).
    """
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(
        String(64), unique=True, nullable=False)
    pet_id: Mapped[int] = mapped_column(Integer, nullable=False)
    session_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0)
    relationship_summary: Mapped[str] = mapped_column(
        Text, nullable=False, default="")
    updated_at: Mapped[str] = mapped_column(
        String(64), nullable=False, default="")

    def __repr__(self) -> str:
        return f"<User user_id={self.user_id!r}>"

    def to_dict(self) -> dict:
        """Return the same dict shape as user_profile.json."""
        return {
            "user_id": self.user_id,
            "pet_id": self.pet_id,
            "session_count": self.session_count,
            "relationship_summary": self.relationship_summary,
            "updated_at": self.updated_at,
        }


# ── ActiveProfile ────────────────────────────────────────────────────────────

class ActiveProfile(Base):
    """
    Current best-known value for each fact field about a pet.

    Maps to data/active_profile.json (a dict keyed by field_key).
    One row per (pet_id, field_key) pair — enforced by UNIQUE constraint.

    Special handling for _pet_history:
      field_key = "_pet_history", value = the narrative string,
      all metadata columns (confidence, source_rank, etc.) = NULL.
      to_dict_entry() returns the raw string for this key.
    """
    __tablename__ = "active_profile"

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True)
    pet_id: Mapped[int] = mapped_column(Integer, nullable=False)
    field_key: Mapped[str] = mapped_column(String(128), nullable=False)

    # The fact value — always a string (even for _pet_history).
    value: Mapped[str] = mapped_column(Text, nullable=False)

    # Metadata columns — NULL for _pet_history rows.
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    source_rank: Mapped[str | None] = mapped_column(String(32), nullable=True)
    time_scope: Mapped[str | None] = mapped_column(String(16), nullable=True)
    source_quote: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[str | None] = mapped_column(String(64), nullable=True)
    session_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    status: Mapped[str | None] = mapped_column(String(16), nullable=True)
    change_detected: Mapped[str | None] = mapped_column(Text, nullable=True)
    trend_flag: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # One row per pet+field combination.
    __table_args__ = (
        UniqueConstraint("pet_id", "field_key",
                         name="uq_active_profile_pet_field"),
    )

    def __repr__(self) -> str:
        return f"<ActiveProfile pet_id={self.pet_id!r} field_key={self.field_key!r}>"

    def to_dict_entry(self) -> dict | str:
        """
        Return the dict shape for a single entry in active_profile.

        For _pet_history: returns just the string value (not a dict).
        For regular fields: returns the full metadata dict matching
        what the Aggregator writes to active_profile.json.
        """
        if self.field_key == "_pet_history":
            return self.value

        return {
            "value": self.value,
            "confidence": self.confidence,
            "source_rank": self.source_rank or "",
            "time_scope": self.time_scope or "",
            "source_quote": self.source_quote or "",
            "updated_at": self.updated_at or "",
            "session_id": self.session_id or "",
            "status": self.status or "",
            "change_detected": self.change_detected or "",
            "trend_flag": self.trend_flag or "",
        }


# ── FactLog ──────────────────────────────────────────────────────────────────

class FactLog(Base):
    """
    Append-only audit trail of every extracted fact.

    One row per extracted fact per conversation. No UNIQUE constraint —
    the same field can appear many times (every conversation may extract
    the same fact).
    """
    __tablename__ = "fact_log"

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True)
    pet_id: Mapped[int] = mapped_column(Integer, nullable=False)
    session_id: Mapped[str] = mapped_column(String(128), nullable=False)
    field_key: Mapped[str] = mapped_column(String(128), nullable=False)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    source_rank: Mapped[str] = mapped_column(
        String(32), nullable=False, default="explicit_owner")
    time_scope: Mapped[str] = mapped_column(
        String(16), nullable=False, default="current")
    uncertainty: Mapped[str] = mapped_column(Text, nullable=False, default="")
    source_quote: Mapped[str] = mapped_column(Text, nullable=False, default="")
    timestamp: Mapped[str | None] = mapped_column(String(64), nullable=True)
    needs_clarification: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False)
    extracted_at: Mapped[str] = mapped_column(String(64), nullable=False)

    __table_args__ = (
        Index("ix_fact_log_pet_id", "pet_id"),
        Index("ix_fact_log_session_id", "session_id"),
    )

    def __repr__(self) -> str:
        return f"<FactLog id={self.id} pet_id={self.pet_id!r} field_key={self.field_key!r}>"

    def to_dict(self) -> dict:
        """Return the same dict shape as one entry in fact_log.json."""
        return {
            "key": self.field_key,
            "value": self.value,
            "confidence": self.confidence,
            "source_rank": self.source_rank,
            "time_scope": self.time_scope,
            "uncertainty": self.uncertainty,
            "source_quote": self.source_quote,
            "timestamp": self.timestamp,
            "needs_clarification": self.needs_clarification,
            "extracted_at": self.extracted_at,
            "session_id": self.session_id,
        }


# ── Thread ────────────────────────────────────────────────────────────────────

class Thread(Base):
    """
    A 24-hour conversation window.

    Each thread has a hard expiry (started_at + 24h). Pet facts persist
    forever in active_profile; only conversation text resets at expiry.
    The compaction_summary column stores an LLM-generated summary of
    older messages after compaction runs.
    """
    __tablename__ = "threads"

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True)
    thread_id: Mapped[str] = mapped_column(
        String(128), unique=True, nullable=False)
    pet_id: Mapped[int] = mapped_column(Integer, nullable=False)
    user_id: Mapped[str] = mapped_column(String(64), nullable=False)
    started_at: Mapped[str] = mapped_column(String(64), nullable=False)
    expires_at: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="active")
    compaction_summary: Mapped[str | None] = mapped_column(
        Text, nullable=True)

    __table_args__ = (
        Index("ix_threads_pet_id_status", "pet_id", "status"),
    )

    def __repr__(self) -> str:
        return f"<Thread thread_id={self.thread_id!r} status={self.status!r}>"

    def to_dict(self) -> dict:
        """Return all thread fields as a plain dict."""
        return {
            "thread_id": self.thread_id,
            "pet_id": self.pet_id,
            "user_id": self.user_id,
            "started_at": self.started_at,
            "expires_at": self.expires_at,
            "status": self.status,
            "compaction_summary": self.compaction_summary,
        }


# ── ThreadMessage ─────────────────────────────────────────────────────────────

class ThreadMessage(Base):
    """
    A single message within a thread.

    Append-only — one row per user or assistant message.
    Write-through pattern: appended to app.state.sessions (in-memory)
    synchronously, then INSERT'd to PostgreSQL in _run_background().
    """
    __tablename__ = "thread_messages"

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True)
    thread_id: Mapped[str] = mapped_column(String(128), nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    timestamp: Mapped[str] = mapped_column(String(64), nullable=False)

    __table_args__ = (
        Index("ix_thread_messages_thread_id", "thread_id"),
    )

    def __repr__(self) -> str:
        return f"<ThreadMessage id={self.id} thread_id={self.thread_id!r} role={self.role!r}>"

    def to_dict(self) -> dict:
        """Return the message dict used by app.state.sessions lists."""
        return {
            "role": self.role,
            "content": self.content,
            "timestamp": self.timestamp,
        }
