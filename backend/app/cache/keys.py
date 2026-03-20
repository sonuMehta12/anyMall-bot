# app/cache/keys.py
#
# Single source of truth for every Valkey key pattern used in AnyMall-chan.
#
# Why centralise keys here?
#   - Typo in a key name = silent cache miss, very hard to debug.
#   - Changing the format of a key (e.g., adding a namespace) requires touching
#     every place that builds the string.  With this class, it's one line.
#
# Key format: am:{type}:{identifier}
#   am   = AnyMall namespace (prevents collisions if Valkey is shared)
#   type = what the key stores (session, profile, meta, …)
#   id   = which instance (thread_id, pet_id, user_code, …)
#
# TTLs are documented here but APPLIED at the call site (SETEX / EXPIRE).
# Keeping them here as constants gives the caller a sensible default to use.

import random

# ── TTL constants (in seconds) ────────────────────────────────────────────────
# Document the reasoning next to each value.  Use jittered_ttl() on all of
# these except rate limit counters (those MUST expire on exact windows).

TTL_SESSION = 7200      # 2h inactivity timeout — refreshed on every message
TTL_META = 7200         # same as session — meta dies when session dies
TTL_PENDING = 7200      # same as session — pending clari dies when session dies
TTL_PROFILE = 3600      # 1h — refreshed by Aggregator on every fact merge
TTL_AALDA = 300         # 5min — matches current PetFetcher CACHE_TTL_SECONDS
TTL_USER = 7200         # 2h — matches session lifetime, refreshed on upsert
TTL_HEALTH = 60         # 60s — checks Azure at most once per minute
TTL_COMPACTING = 300    # 5min safety net — auto-cleanup if compaction task dies


def jittered_ttl(base: int) -> int:
    """
    Return base ± 10 % to prevent synchronised expiration (stampede prevention).

    Example: jittered_ttl(3600) → somewhere between 3240 and 3960.

    When many keys are cached in the same batch (e.g. 50 profiles cached at
    startup), they would all expire at the exact same second without jitter —
    triggering 50 simultaneous DB queries.  Jitter spreads the expiration over
    a ±6-minute window, turning a spike into a trickle.

    Exception: rate-limit counters (TTL_RATE_LIMIT) MUST NOT be jittered —
    they need to expire on a precise sliding window boundary.
    """
    offset = base // 10
    return base + random.randint(-offset, offset)


# ── Lua script: atomic GET-append-SET for session messages ───────────────────
# Why Lua?
#   GET → modify in Python → SET is NOT atomic.  Another instance can SET
#   between our GET and SET, losing one of the appends.  Lua scripts run
#   atomically inside Valkey's single-threaded event loop — no other command
#   can execute while the script runs.
#
# Arguments:
#   KEYS[1]  = session key (e.g. "am:session:abc-123")
#   ARGV[1]  = JSON array of new messages to append (e.g. '[{...},{...}]')
#   ARGV[2]  = TTL in seconds as a string (e.g. "7200")
#
# Returns: total number of messages in the session after append.
#
# NOTE: In Lua, redis.call('GET', key) returns false (not nil) when the key
# does not exist.  `current or '[]'` handles this: false or '[]' = '[]'.
LUA_APPEND_MESSAGES = """
local current = redis.call('GET', KEYS[1])
local messages = cjson.decode(current or '[]')
local new_msgs = cjson.decode(ARGV[1])
for _, msg in ipairs(new_msgs) do
    table.insert(messages, msg)
end
redis.call('SETEX', KEYS[1], tonumber(ARGV[2]), cjson.encode(messages))
return #messages
"""

# ── Lua script: safe lock release (check token before delete) ────────────────
# Why not just DEL?
#   Lock TTL = 10s.  If our work takes longer than 10s, the TTL expires and
#   another instance acquires the lock.  A bare DEL would then delete THEIR
#   lock.  This script checks that the lock still holds our token before
#   deleting — making the check+delete atomic and safe.
#
# Arguments:
#   KEYS[1] = lock key
#   ARGV[1] = our unique token (uuid)
LUA_RELEASE_LOCK = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
    return redis.call('DEL', KEYS[1])
else
    return 0
end
"""


class CacheKeys:
    """
    All Valkey key patterns for AnyMall-chan, defined as static methods.

    Usage:
        from app.cache.keys import CacheKeys
        key = CacheKeys.session("thread-abc-123")   # → "am:session:thread-abc-123"
    """

    PREFIX = "am"

    @staticmethod
    def session(thread_id: str) -> str:
        """Thread message list — JSON array of {role, content, timestamp} dicts."""
        return f"am:session:{thread_id}"

    @staticmethod
    def meta(thread_id: str) -> str:
        """Per-thread metadata — gap questions asked, redirect turn tracker."""
        return f"am:meta:{thread_id}"

    @staticmethod
    def pending(thread_id: str) -> str:
        """Pending clarifications for this thread — low-confidence facts awaiting confirmation."""
        return f"am:pending:{thread_id}"

    @staticmethod
    def profile(pet_id: int) -> str:
        """Active profile for a pet — all known facts with confidence scores."""
        return f"am:profile:{pet_id}"

    @staticmethod
    def aalda(user_code: str, pet_id: int) -> str:
        """Fresh AALDA API result for this user+pet (5-min TTL)."""
        return f"am:aalda:{user_code}:{pet_id}"

    @staticmethod
    def user(user_code: str) -> str:
        """User record — relationship summary and session metadata."""
        return f"am:user:{user_code}"

    @staticmethod
    def health_llm() -> str:
        """Cached /health LLM check result — refreshed every 60 seconds."""
        return "am:health:llm"

    @staticmethod
    def compacting(thread_id: str) -> str:
        """
        Distributed compaction lock for this thread.

        SETNX pattern: SET if Not eXists.  Prevents two instances from
        compacting the same thread simultaneously.  5-min TTL is a safety
        net — if the compaction task crashes without calling DEL, the lock
        auto-expires so future compactions can proceed.
        """
        return f"am:compacting:{thread_id}"

