"""Cap how many error events one process sends to Sentry.

A fault that repeats on every request, or in a loop, can send thousands of
events a minute and use up a month's Sentry quota in one go. The limiter is
a global event processor: it runs before the SDK serialises an event,
so a dropped event costs neither quota nor serialisation, and the SDK
reports it to Sentry as an ``event_processor`` client discard.

Two token buckets apply, both refilled continuously over an hour:

- one for the whole process: ``SENTRY_MAX_EVENTS_PER_HOUR`` events an hour,
  with up to ``SENTRY_MAX_EVENTS_BURST`` at once;
- one per error (exception type and the line that raised it, or the log
  message): ``SENTRY_MAX_EVENTS_PER_ERROR`` events an hour, all of which
  may come at once.

An event is sent only if both buckets have a token. A value of 0 turns that
bucket off. Transactions and other non-error events are never limited.
"""

from sentry_sdk.scope import add_global_event_processor
from sentry_sdk.scope import global_event_processors

import logging
import os
import threading
import time

logger = logging.getLogger(__name__)

DEFAULT_MAX_EVENTS_PER_HOUR = 60
DEFAULT_MAX_EVENTS_BURST = 20
DEFAULT_MAX_EVENTS_PER_ERROR = 10

# Error buckets kept before idle (full) ones are pruned.
MAX_TRACKED_ERRORS = 1000
# How often, at most, the limiter logs that it is dropping events.
WARN_INTERVAL = 600


class TokenBucket:
    def __init__(self, capacity, per_hour, now):
        self.capacity = capacity
        self.rate = per_hour / 3600.0
        self.tokens = float(capacity)
        self.updated = now

    def refill(self, now):
        elapsed = max(0.0, now - self.updated)
        self.tokens = min(self.capacity, self.tokens + elapsed * self.rate)
        self.updated = now

    @property
    def full(self):
        return self.tokens >= self.capacity


def error_key(event):
    """What counts as "the same error" for the per-error cap."""
    values = (event.get("exception") or {}).get("values") or []
    if values:
        # Sentry lists chained exceptions oldest first: the last one is the
        # exception that was raised.
        exc = values[-1]
        frames = (exc.get("stacktrace") or {}).get("frames") or []
        frame = frames[-1] if frames else {}
        return (
            exc.get("type"),
            frame.get("module") or frame.get("filename"),
            frame.get("function"),
            frame.get("lineno"),
        )
    logentry = event.get("logentry") or {}
    return (
        "message",
        event.get("logger"),
        logentry.get("message") or event.get("message"),
    )


class EventRateLimiter:
    def __init__(
        self,
        max_per_hour=DEFAULT_MAX_EVENTS_PER_HOUR,
        burst=DEFAULT_MAX_EVENTS_BURST,
        max_per_error=DEFAULT_MAX_EVENTS_PER_ERROR,
        clock=time.monotonic,
    ):
        self.clock = clock
        now = clock()
        self.total = TokenBucket(burst, max_per_hour, now) if max_per_hour else None
        self.max_per_error = max_per_error
        self.errors = {}
        self.dropped = 0
        self._last_warning = None
        self._lock = threading.Lock()

    def __call__(self, event, hint):
        if event.get("type") not in (None, "error", "default"):
            return event
        with self._lock:
            if self._allow(event):
                return event
            self.dropped += 1
            dropped = self.dropped
            warn = self._should_warn()
        if warn:
            # Logged outside the lock: if a deployer sends warnings to
            # Sentry as events, this one comes back through the limiter.
            logger.warning(
                "Sentry event limit reached: %d events dropped so far "
                "(SENTRY_MAX_EVENTS_PER_HOUR, SENTRY_MAX_EVENTS_BURST, "
                "SENTRY_MAX_EVENTS_PER_ERROR)",
                dropped,
            )
        return None

    def _allow(self, event):
        now = self.clock()
        buckets = []
        if self.total is not None:
            self.total.refill(now)
            buckets.append(self.total)
        if self.max_per_error:
            key = error_key(event)
            bucket = self.errors.get(key)
            if bucket is None:
                if len(self.errors) >= MAX_TRACKED_ERRORS:
                    self._prune(now)
                bucket = TokenBucket(self.max_per_error, self.max_per_error, now)
                self.errors[key] = bucket
            else:
                bucket.refill(now)
            buckets.append(bucket)
        if all(bucket.tokens >= 1 for bucket in buckets):
            for bucket in buckets:
                bucket.tokens -= 1
            return True
        return False

    def _prune(self, now):
        for key, bucket in list(self.errors.items()):
            bucket.refill(now)
            if bucket.full:
                del self.errors[key]
        if len(self.errors) >= MAX_TRACKED_ERRORS:
            # Everything is busy: start the per-error counts afresh rather
            # than grow without bound.
            self.errors.clear()

    def _should_warn(self):
        now = self.clock()
        if self._last_warning is not None and now - self._last_warning < WARN_INTERVAL:
            return False
        self._last_warning = now
        return True


def _read_count(environ, name, default):
    raw = environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise RuntimeError(
            f"Environment variable {name} is malformed "
            f"(expected a whole number, 0 to turn it off): {raw}"
        ) from exc
    if value < 0:
        raise RuntimeError(f"Environment variable {name} must not be negative: {raw}")
    return value


_limiter = None


def _limit(event, hint):
    limiter = _limiter
    return event if limiter is None else limiter(event, hint)


def install_from_environ(environ=None):
    """Turn the limits on from env vars. Returns the limiter, or None if off.

    The limiter is a global event processor, so it applies to whichever
    client is active, including a deployer's own ``sentry_sdk.init()``.
    Calling this again replaces the limits.
    """
    global _limiter
    environ = os.environ if environ is None else environ
    max_per_hour = _read_count(
        environ, "SENTRY_MAX_EVENTS_PER_HOUR", DEFAULT_MAX_EVENTS_PER_HOUR
    )
    burst = _read_count(environ, "SENTRY_MAX_EVENTS_BURST", DEFAULT_MAX_EVENTS_BURST)
    max_per_error = _read_count(
        environ, "SENTRY_MAX_EVENTS_PER_ERROR", DEFAULT_MAX_EVENTS_PER_ERROR
    )
    if max_per_hour and not burst:
        raise RuntimeError(
            "SENTRY_MAX_EVENTS_BURST must be at least 1 when "
            "SENTRY_MAX_EVENTS_PER_HOUR is set: with 0 nothing could be sent"
        )

    if not max_per_hour and not max_per_error:
        _limiter = None
        logger.info("Sentry event limits turned off")
        return None
    _limiter = EventRateLimiter(max_per_hour, burst, max_per_error)
    if _limit not in global_event_processors:
        add_global_event_processor(_limit)
    return _limiter
