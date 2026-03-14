# app/storage/file_store.py
#
# DEPRECATED — Phase 1C replaces this with app/db/repositories.py (PostgreSQL).
#
# Kept for backward compatibility with:
#   - aggregator.py fallback path (when get_session is None, e.g. tests)
#   - load_profiles() fallback in context_builder.py
#
# Do NOT add new callers.  Will be deleted in a future cleanup PR.
#
# Original purpose (Phase 1B):
#   JSON file storage helpers. Logic before infrastructure.
#
# Files managed:
#   data/fact_log.json       — append-only list of every extracted fact
#   data/active_profile.json — current best-known facts per pet (Aggregator, Phase 1C)
#
# Atomic write pattern:
#   Write to <file>.tmp first, then os.replace() to the real path.
#   os.replace() is atomic on Linux, macOS, and Windows — if the process
#   dies mid-write the original file is untouched. No corrupt JSON.

import json
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

# ── Paths ──────────────────────────────────────────────────────────────────────
#
# __file__ is  backend/app/storage/file_store.py
# .parent       → backend/app/storage/
# .parent.parent → backend/app/
# .parent.parent.parent → backend/
# / "data"      → backend/data/

_BACKEND_DIR: Path = Path(__file__).parent.parent.parent
_DATA_DIR: Path = _BACKEND_DIR / "data"
_FACT_LOG_PATH: Path = _DATA_DIR / "fact_log.json"
_PET_PROFILE_PATH: Path = _DATA_DIR / "pet_profile.json"
_ACTIVE_PROFILE_PATH: Path = _DATA_DIR / "active_profile.json"
_USER_PROFILE_PATH: Path = _DATA_DIR / "user_profile.json"


# ── _ensure_data_dir ───────────────────────────────────────────────────────────

def _ensure_data_dir() -> None:
    """Create the data/ directory if it does not exist. Safe to call repeatedly."""
    _DATA_DIR.mkdir(parents=True, exist_ok=True)


# ── append_fact_log ────────────────────────────────────────────────────────────

def append_fact_log(facts: list[dict]) -> None:
    """
    Append a list of fact dicts to data/fact_log.json.

    Creates data/ and fact_log.json if they do not exist.
    Uses an atomic write (write to .tmp → os.replace) to prevent corrupt JSON
    if the process is interrupted mid-write.

    Args:
        facts: List of fact dicts to append. Each dict must be JSON-serialisable.
               Typically produced by dataclasses.asdict(ExtractedFact(...)).
    """
    if not facts:
        return

    _ensure_data_dir()

    # ── Read existing log ──────────────────────────────────────────────────────
    existing: list[dict] = []
    if _FACT_LOG_PATH.exists():
        try:
            with open(_FACT_LOG_PATH, "r", encoding="utf-8") as fh:
                existing = json.load(fh)
            if not isinstance(existing, list):
                logger.warning("fact_log.json is not a list — resetting to []")
                existing = []
        except (json.JSONDecodeError, OSError) as exc:
            logger.error("Could not read fact_log.json: %s — resetting to []", exc)
            existing = []

    # ── Append and write atomically ────────────────────────────────────────────
    updated = existing + facts
    tmp_path = _FACT_LOG_PATH.with_suffix(".json.tmp")

    try:
        with open(tmp_path, "w", encoding="utf-8") as fh:
            json.dump(updated, fh, ensure_ascii=False, indent=2)
        os.replace(tmp_path, _FACT_LOG_PATH)
        logger.debug("fact_log.json updated — total=%d entries", len(updated))
    except OSError as exc:
        logger.error("Could not write fact_log.json: %s", exc)
        # Clean up orphaned .tmp file if it was created
        if tmp_path.exists():
            tmp_path.unlink(missing_ok=True)
        raise


# ── read_fact_log ──────────────────────────────────────────────────────────────

def read_fact_log() -> list[dict]:
    """
    Read data/fact_log.json and return the list of fact dicts.

    Returns [] if the file does not exist or cannot be parsed.
    Never raises — callers can always safely iterate over the result.
    """
    if not _FACT_LOG_PATH.exists():
        return []

    try:
        with open(_FACT_LOG_PATH, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError) as exc:
        logger.error("Could not read fact_log.json: %s", exc)
        return []


# ── Generic JSON read/write helpers ──────────────────────────────────────────
#
# Used by pet_profile, active_profile, and user_profile.
# Same atomic write pattern as append_fact_log.

def _read_json(path: Path) -> dict | None:
    """
    Read a JSON file and return the parsed dict.

    Returns None if the file does not exist.
    Returns None on parse errors (logs warning).
    """
    if not path.exists():
        return None

    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if not isinstance(data, dict):
            logger.warning("%s is not a dict — returning None", path.name)
            return None
        return data
    except (json.JSONDecodeError, OSError) as exc:
        logger.error("Could not read %s: %s", path.name, exc)
        return None


def _write_json(path: Path, data: dict) -> None:
    """
    Write a dict to a JSON file atomically.

    Creates the data/ directory if it does not exist.
    Uses .tmp -> os.replace() to prevent corrupt JSON on crash.
    """
    _ensure_data_dir()

    tmp_path = path.with_suffix(".json.tmp")
    try:
        with open(tmp_path, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
        os.replace(tmp_path, path)
        logger.debug("%s written successfully.", path.name)
    except OSError as exc:
        logger.error("Could not write %s: %s", path.name, exc)
        if tmp_path.exists():
            tmp_path.unlink(missing_ok=True)
        raise


# ── Pet Profile ──────────────────────────────────────────────────────────────

def read_pet_profile() -> dict | None:
    """Read data/pet_profile.json. Returns None if file does not exist."""
    return _read_json(_PET_PROFILE_PATH)


def write_pet_profile(data: dict) -> None:
    """Write pet profile dict to data/pet_profile.json atomically."""
    _write_json(_PET_PROFILE_PATH, data)


# ── Active Profile ───────────────────────────────────────────────────────────

def read_active_profile() -> dict | None:
    """Read data/active_profile.json. Returns None if file does not exist."""
    return _read_json(_ACTIVE_PROFILE_PATH)


def write_active_profile(data: dict) -> None:
    """Write active profile dict to data/active_profile.json atomically."""
    _write_json(_ACTIVE_PROFILE_PATH, data)


# ── User Profile ─────────────────────────────────────────────────────────────

def read_user_profile() -> dict | None:
    """Read data/user_profile.json. Returns None if file does not exist."""
    return _read_json(_USER_PROFILE_PATH)


def write_user_profile(data: dict) -> None:
    """Write user profile dict to data/user_profile.json atomically."""
    _write_json(_USER_PROFILE_PATH, data)
