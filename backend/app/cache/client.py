# app/cache/client.py
#
# ValkeyClient — thin wrapper around the valkey-py async client.
#
# What this adds on top of the raw client:
#
#   1. Graceful degradation
#      Every method catches ConnectionError and TimeoutError.  On failure it
#      returns None / False instead of raising, so the caller can fall back to
#      PostgreSQL without crashing.  Valkey must be a soft dependency.
#
#   2. Circuit breaker
#      Without a circuit breaker, a Valkey outage adds socket_timeout (5s) to
#      EVERY request before it falls back to DB.  With 100 concurrent requests,
#      that's 100 × 5s = 500s of wasted wait.
#
#      The circuit breaker tracks consecutive failures:
#        - After _failure_threshold failures → "open" the circuit
#        - While open → skip Valkey entirely (0ms overhead), fall back to DB
#        - After _recovery_seconds → allow one probe through ("half-open")
#        - If probe succeeds → close circuit, normal operation
#        - If probe fails → stay open for another _recovery_seconds
#
#   3. Consistent interface
#      All methods return Python types (str | None, bool, int | None), not
#      raw bytes, so callers don't need to .decode() anything.
#      decode_responses=True is set on the raw client at construction time.
#
# Usage (from lifespan in main.py):
#   raw_vk = valkey_lib.Valkey.from_url(settings.valkey_url, decode_responses=True, ...)
#   app.state.valkey = ValkeyClient(raw_vk)
#
# Usage in a route:
#   vk: ValkeyClient = request.app.state.valkey
#   raw = await vk.get(CacheKeys.session(thread_id))

import logging
import time
from typing import TYPE_CHECKING

import valkey as valkey_errors  # import the sync package for exception types

if TYPE_CHECKING:
    import valkey.asyncio as valkey_lib

logger = logging.getLogger(__name__)

# Exceptions we treat as "Valkey is down, fall back to DB".
# NOTE: ResponseError is intentionally excluded — it signals a bad command sent
# by our code (WRONGTYPE, NOSCRIPT, wrong arg count), not a connection failure.
# Swallowing ResponseError would hide logic bugs as silent cache misses.
_VALKEY_ERRORS = (
    valkey_errors.ConnectionError,
    valkey_errors.TimeoutError,
)


class ValkeyClient:
    """
    Circuit-breaking wrapper around a valkey.asyncio.Valkey connection pool.

    All public methods are coroutines that match the raw client's API but
    return safe Python types and never raise on connection failures.

    The raw client is still accessible via .raw for operations not covered
    here (pipelines, SCAN iterators, etc.).
    """

    def __init__(
        self,
        client: "valkey_lib.Valkey",
        failure_threshold: int = 5,
        recovery_seconds: float = 30.0,
    ) -> None:
        self._client = client
        self._failure_threshold = failure_threshold
        self._recovery_seconds = recovery_seconds

        # Circuit breaker state (not thread-safe, but asyncio is single-threaded)
        self._consecutive_failures: int = 0
        self._last_failure_time: float = 0.0

    # ── Circuit breaker logic ─────────────────────────────────────────────────

    def _should_allow_request(self) -> bool:
        """
        Return True if the request should proceed to Valkey.

        Closed circuit  → always True.
        Open circuit    → False (skip Valkey, go straight to DB fallback).
        Half-open probe → True for exactly ONE coroutine. By resetting
                          _last_failure_time here (a synchronous, non-yielding
                          operation), subsequent coroutines that check before the
                          probe result is recorded will see elapsed < recovery_seconds
                          and be blocked. This guarantees a single probe.
        """
        if self._consecutive_failures < self._failure_threshold:
            return True  # circuit closed
        elapsed = time.monotonic() - self._last_failure_time
        if elapsed >= self._recovery_seconds:
            # Half-open: claim the probe slot by resetting the timer NOW (atomic).
            self._last_failure_time = time.monotonic()
            return True
        return False  # circuit open

    def _record_success(self) -> None:
        self._consecutive_failures = 0

    def _record_failure(self) -> None:
        self._consecutive_failures += 1
        self._last_failure_time = time.monotonic()

    # ── Public API ────────────────────────────────────────────────────────────

    @property
    def raw(self) -> "valkey_lib.Valkey":
        """
        Direct access to the underlying valkey-py client.

        Use this for operations not wrapped here: pipelines, SCAN, INFO, etc.
        Errors from raw calls are NOT caught — caller must handle them.
        """
        return self._client

    async def get(self, key: str) -> str | None:
        """
        GET key → str value, or None on miss / connection failure.

        Callers must treat None as a cache miss and fall back to DB.
        Do NOT call json.loads(None) — always check for None first.
        """
        if not self._should_allow_request():
            return None
        try:
            result = await self._client.get(key)
            self._record_success()
            return result
        except _VALKEY_ERRORS as exc:
            self._record_failure()
            logger.warning("Valkey GET failed — key=%s error=%s", key, exc)
            return None

    async def setex(self, key: str, ttl: int, value: str) -> bool:
        """
        SETEX key ttl value — set a key with an expiry time in seconds.

        Returns True on success, False on connection failure.
        Callers can ignore the return value when the DB is the source of truth.
        """
        if not self._should_allow_request():
            return False
        try:
            await self._client.setex(key, ttl, value)
            self._record_success()
            return True
        except _VALKEY_ERRORS as exc:
            self._record_failure()
            logger.warning("Valkey SETEX failed — key=%s error=%s", key, exc)
            return False

    async def set(
        self,
        key: str,
        value: str,
        *,
        nx: bool = False,
        ex: int | None = None,
    ) -> bool:
        """
        SET key value [NX] [EX seconds].

        Used for SETNX-style distributed locks:
            acquired = await vk.set(lock_key, token, nx=True, ex=300)
            if not acquired: return  # another instance holds the lock

        Returns True if the key was set, False if NX prevented the set or on error.
        """
        if not self._should_allow_request():
            return False
        try:
            result = await self._client.set(key, value, nx=nx, ex=ex)
            self._record_success()
            # valkey-py returns True on success, None when NX prevents set
            return result is True
        except _VALKEY_ERRORS as exc:
            self._record_failure()
            logger.warning("Valkey SET failed — key=%s error=%s", key, exc)
            return False

    async def delete(self, *keys: str) -> int:
        """
        DEL key [key ...] — delete one or more keys.

        Returns the number of keys deleted, or 0 on connection failure.
        """
        if not self._should_allow_request():
            return 0
        try:
            count = await self._client.delete(*keys)
            self._record_success()
            return count
        except _VALKEY_ERRORS as exc:
            self._record_failure()
            logger.warning("Valkey DEL failed — keys=%s error=%s", keys, exc)
            return 0

    async def expire(self, key: str, ttl: int) -> bool:
        """
        EXPIRE key seconds — reset the TTL on an existing key.

        Used to refresh the inactivity timeout on a session after each message.
        Returns True if the timeout was set, False if key doesn't exist or on error.
        """
        if not self._should_allow_request():
            return False
        try:
            result = await self._client.expire(key, ttl)
            self._record_success()
            return bool(result)
        except _VALKEY_ERRORS as exc:
            self._record_failure()
            logger.warning("Valkey EXPIRE failed — key=%s error=%s", key, exc)
            return False

    async def eval(self, script: str, numkeys: int, *args: str) -> object:
        """
        EVAL script numkeys [key ...] [arg ...] — run a Lua script atomically.

        Used for:
          - LUA_APPEND_MESSAGES: atomic GET-append-SET for session messages
          - LUA_RELEASE_LOCK: safe token-checked lock release

        Returns the script's return value, or None on connection failure.
        The caller must treat None as a failure and fall back to the safe path.
        """
        if not self._should_allow_request():
            return None
        try:
            result = await self._client.eval(script, numkeys, *args)
            self._record_success()
            return result
        except _VALKEY_ERRORS as exc:
            self._record_failure()
            logger.warning("Valkey EVAL failed — error=%s", exc)
            return None

    async def ping(self) -> bool:
        """
        PING — check if Valkey is reachable.

        Returns True if connected, False otherwise.
        Does NOT update circuit breaker state (used for health reporting only).
        """
        try:
            await self._client.ping()
            return True
        except _VALKEY_ERRORS:
            return False

    async def aclose(self) -> None:
        """Close the underlying connection pool gracefully on shutdown."""
        await self._client.aclose()
