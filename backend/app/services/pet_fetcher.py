# app/services/pet_fetcher.py
#
# AALDA API client — fetches real pet data from the external pet platform.
#
# Two endpoints:
#   GET /api/v1/pet-profile/{pet_id}  — full profile with nutrition, diet, vaccinations
#   GET /api/v1/pet                   — list all pets for a user
#
# Auth: X-User-Code header (same value Flutter sends to us).
#
# Caching: in-memory dict with 5-minute TTL.  One entry per (user_code, pet_id).
# On cache miss: call AALDA API.  On AALDA failure: raise PetFetchError.
#
# Returns TWO things per pet:
#   pet_profile — static identity (pet_id, name, species, breed, date_of_birth, sex)
#   aalda_facts — dynamic facts for active_profile seeding (neutered, diet, vaccinations)

import logging
import time
from datetime import date
from email.utils import parsedate_to_datetime
from typing import Any

import httpx

logger = logging.getLogger(__name__)

CACHE_TTL_SECONDS: int = 300  # 5 minutes


class PetFetchError(Exception):
    """Raised when the AALDA API call fails or returns an error."""
    pass


class PetFetcher:
    """
    Async client for the AALDA pet data API with in-memory caching.

    Created once at startup, stored on app.state.pet_fetcher.
    Closed at shutdown via close().
    """

    def __init__(self, base_url: str) -> None:
        self._base_url = base_url.rstrip("/")
        self._cache: dict[tuple[str, int], tuple[dict, float]] = {}
        self._client = httpx.AsyncClient(timeout=10.0)
        logger.info("PetFetcher initialised — base_url=%s", self._base_url)

    # ── Public API ────────────────────────────────────────────────────────────

    async def fetch_pet_profile(
        self, user_code: str, pet_id: int,
    ) -> tuple[dict, dict]:
        """
        Fetch a single pet profile from AALDA.

        Returns:
            (pet_profile, aalda_facts) tuple.
            pet_profile: static identity dict for build_context().
            aalda_facts: dynamic facts dict to seed active_profile.

        Raises:
            PetFetchError: if AALDA is unreachable or returns an error.
        """
        cache_key = (user_code, pet_id)
        cached = self._cache.get(cache_key)
        if cached:
            result, ts = cached
            if time.monotonic() - ts < CACHE_TTL_SECONDS:
                logger.debug("PetFetcher cache hit — pet_id=%d", pet_id)
                return result["pet_profile"], result["aalda_facts"]
            # Expired — fall through to fetch

        url = f"{self._base_url}/pet-profile/{pet_id}"
        headers = {"X-User-Code": user_code}

        try:
            resp = await self._client.get(url, headers=headers)
        except httpx.HTTPError as exc:
            raise PetFetchError(
                f"AALDA API unreachable: {exc}"
            ) from exc

        if resp.status_code != 200:
            raise PetFetchError(
                f"AALDA API returned {resp.status_code}: {resp.text[:200]}"
            )

        body = resp.json()
        if not body.get("success"):
            raise PetFetchError(
                f"AALDA API error: {body.get('message', 'unknown error')}"
            )

        data = body["data"]
        pet_profile = self._extract_pet_profile(data)
        aalda_facts = self._extract_aalda_facts(data)

        # Cache the result
        self._cache[cache_key] = (
            {"pet_profile": pet_profile, "aalda_facts": aalda_facts},
            time.monotonic(),
        )

        logger.info(
            "PetFetcher fetched pet_id=%d name=%s from AALDA",
            pet_id, pet_profile.get("name"),
        )
        return pet_profile, aalda_facts

    async def fetch_user_pets(self, user_code: str) -> list[dict]:
        """
        Fetch all pets for a user from AALDA.

        Returns list of pet summary dicts:
          [{"pet_id": 143, "name": "Node", "species": "dog", "breed": "Toy Poodle", ...}, ...]

        Raises:
            PetFetchError: if AALDA is unreachable or returns an error.
        """
        url = f"{self._base_url}/pet"
        headers = {"X-User-Code": user_code}

        try:
            resp = await self._client.get(url, headers=headers)
        except httpx.HTTPError as exc:
            raise PetFetchError(
                f"AALDA API unreachable: {exc}"
            ) from exc

        if resp.status_code != 200:
            raise PetFetchError(
                f"AALDA API returned {resp.status_code}: {resp.text[:200]}"
            )

        body = resp.json()
        if not body.get("success"):
            raise PetFetchError(
                f"AALDA API error: {body.get('message', 'unknown error')}"
            )

        data = body.get("data", {})
        if isinstance(data, dict):
            pets_raw = data.get("pets", [])
        elif isinstance(data, list):
            pets_raw = data
        else:
            pets_raw = []

        return [self._extract_pet_profile(p) for p in pets_raw]

    async def close(self) -> None:
        """Close the HTTP client. Called during app shutdown."""
        await self._client.aclose()
        logger.info("PetFetcher closed.")

    # ── Internal mapping ──────────────────────────────────────────────────────

    @staticmethod
    def _extract_pet_profile(data: dict) -> dict:
        """
        Map AALDA response to our internal pet_profile shape.

        AALDA uses "gender" and "birthday" (RFC 2822).
        We use "sex" and "date_of_birth" (ISO 8601).
        """
        dob_iso = _parse_rfc2822_to_iso(data.get("birthday", ""))

        return {
            "pet_id": data["pet_id"],
            "name": data.get("name", ""),
            "species": data.get("species", ""),
            "breed": data.get("breed", ""),
            "date_of_birth": dob_iso,
            "sex": data.get("gender", "unknown"),
            "life_stage": _compute_life_stage(dob_iso, data.get("species", "")),
        }

    @staticmethod
    def _extract_aalda_facts(data: dict) -> dict:
        """
        Extract dynamic facts from AALDA data for active_profile seeding.

        These are facts the user didn't tell us in chat — they come from
        the AALDA platform (onboarding, vet records, etc.).  We inject them
        into active_profile so Agent 1 knows about them.
        """
        facts: dict[str, dict[str, Any]] = {}

        # Nutrition block
        nutrition = data.get("nutrition") or {}

        if "is_neutered" in nutrition:
            facts["neutered_spayed"] = {
                "value": "yes" if nutrition["is_neutered"] else "no",
                "confidence": 0.95,
                "source_rank": "vet_record",
                "time_scope": "current",
            }

        if "activity_level" in nutrition and nutrition["activity_level"] is not None:
            level = nutrition["activity_level"]
            labels = {1: "very low", 2: "low", 3: "moderate", 4: "high", 5: "very high"}
            facts["activity_level"] = {
                "value": f"{level} ({labels.get(level, 'unknown')})",
                "confidence": 0.90,
                "source_rank": "vet_record",
                "time_scope": "current",
            }

        if "body_condition_score" in nutrition and nutrition["body_condition_score"] is not None:
            bcs = nutrition["body_condition_score"]
            facts["body_condition_score"] = {
                "value": str(bcs),
                "confidence": 0.90,
                "source_rank": "vet_record",
                "time_scope": "current",
            }

        # Diet array
        diet_list = data.get("diet") or []
        if diet_list:
            diet_parts = []
            brands = set()
            for item in diet_list:
                brand = item.get("food_brand", "")
                name = item.get("food_name", "")
                qty = item.get("quantity", "")
                unit = item.get("unit", "")
                if brand:
                    brands.add(brand)
                part = name or brand
                if qty and unit:
                    part += f" ({qty}{unit})"
                if part:
                    diet_parts.append(part)

            if diet_parts:
                facts["diet_type"] = {
                    "value": "; ".join(diet_parts),
                    "confidence": 0.90,
                    "source_rank": "vet_record",
                    "time_scope": "current",
                }
            if brands:
                facts["food_brand"] = {
                    "value": ", ".join(brands),
                    "confidence": 0.90,
                    "source_rank": "vet_record",
                    "time_scope": "current",
                }

        # Vaccinations array
        vacc_list = data.get("vaccinations") or []
        if vacc_list:
            vacc_parts = []
            for v in vacc_list:
                name = v.get("vaccine_name", "")
                administered = v.get("date_administered", "")
                expiry = v.get("expiry_date", "")
                part = name
                if administered:
                    adm_iso = _parse_rfc2822_to_iso(administered)
                    part += f" (given {adm_iso})"
                if expiry:
                    exp_iso = _parse_rfc2822_to_iso(expiry)
                    part += f" expires {exp_iso}"
                if part:
                    vacc_parts.append(part)

            if vacc_parts:
                facts["vaccinations"] = {
                    "value": "; ".join(vacc_parts),
                    "confidence": 0.90,
                    "source_rank": "vet_record",
                    "time_scope": "current",
                }

        return facts


# ── Helper functions ──────────────────────────────────────────────────────────

def _parse_rfc2822_to_iso(rfc2822_str: str) -> str:
    """
    Parse an RFC 2822 date string to ISO 8601 date (YYYY-MM-DD).

    AALDA returns dates like: "Fri, 06 Mar 2026 17:50:20 GMT"
    We store dates as:        "2026-03-06"

    Returns "unknown" if parsing fails.
    """
    if not rfc2822_str:
        return "unknown"
    try:
        dt = parsedate_to_datetime(rfc2822_str)
        return dt.date().isoformat()
    except (ValueError, TypeError):
        return "unknown"


def compute_current_age(date_of_birth: str) -> str:
    """
    Compute age in "X years Y months" format for the v0.3 prompt.

    Returns "unknown age" if date_of_birth is unparseable.
    Returns "N weeks old" if under 2 months.
    Returns "N months old" if under 1 year.
    Returns "X years Y months" otherwise.
    """
    if not date_of_birth or date_of_birth == "unknown":
        return "unknown age"

    try:
        dob = date.fromisoformat(date_of_birth)
    except (ValueError, TypeError):
        return "unknown age"

    today = date.today()
    age_days = (today - dob).days
    if age_days < 0:
        return "unknown age"

    if age_days < 60:
        weeks = max(age_days // 7, 1)
        return f"{weeks} weeks old"

    # Calculate years and months
    years = today.year - dob.year
    months = today.month - dob.month
    if today.day < dob.day:
        months -= 1
    if months < 0:
        years -= 1
        months += 12

    if years == 0:
        return f"{months} months old"
    if months == 0:
        return f"{years} years 0 months"
    return f"{years} years {months} months"


def _compute_life_stage(date_of_birth: str, species: str) -> str:
    """Derive life_stage from age and species."""
    if not date_of_birth or date_of_birth == "unknown":
        return "adult"

    try:
        dob = date.fromisoformat(date_of_birth)
    except (ValueError, TypeError):
        return "adult"

    age_years = (date.today() - dob).days / 365.25

    # Rough life stage thresholds (dogs mature faster than cats for large breeds)
    if species == "cat":
        if age_years < 1:
            return "kitten"
        if age_years < 7:
            return "adult"
        return "senior"
    else:  # dog or other
        if age_years < 1:
            return "puppy"
        if age_years < 7:
            return "adult"
        return "senior"
