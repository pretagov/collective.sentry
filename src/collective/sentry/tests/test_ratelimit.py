from collective.sentry.tests.utils import captured_events
from collective.sentry.tests.utils import CapturingTransport
from collective.sentry.tests.utils import reset_sentry

import logging
import sentry_sdk
import unittest

DSN = "https://x@example.com/1"


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


def _error(type_="ValueError", lineno=10, message=None):
    if message is not None:
        return {"logger": "app", "logentry": {"message": message}}
    return {
        "exception": {
            "values": [
                {
                    "type": type_,
                    "stacktrace": {
                        "frames": [
                            {"module": "app", "function": "view", "lineno": lineno}
                        ]
                    },
                }
            ]
        }
    }


class TestEventRateLimiter(unittest.TestCase):
    def _limiter(self, **kw):
        from collective.sentry.ratelimit import EventRateLimiter

        self.clock = FakeClock()
        return EventRateLimiter(clock=self.clock, **kw)

    def _sent(self, limiter, events):
        return sum(1 for event in events if limiter(event, {}) is not None)

    def test_burst_then_hourly_rate(self):
        limiter = self._limiter(max_per_hour=60, burst=5, max_per_error=0)
        events = [_error(lineno=i) for i in range(10)]
        self.assertEqual(self._sent(limiter, events), 5)
        # 60 an hour is one a minute.
        self.clock.advance(60)
        self.assertEqual(self._sent(limiter, events), 1)
        self.clock.advance(3600)
        self.assertEqual(self._sent(limiter, events), 5)

    def test_same_error_is_capped_separately(self):
        limiter = self._limiter(max_per_hour=60, burst=20, max_per_error=3)
        self.assertEqual(self._sent(limiter, [_error()] * 10), 3)
        # A different error still gets through.
        self.assertEqual(self._sent(limiter, [_error(type_="KeyError")] * 10), 3)
        self.assertEqual(limiter.dropped, 14)

    def test_log_messages_are_keyed_by_template(self):
        limiter = self._limiter(max_per_hour=0, burst=0, max_per_error=2)
        same = [_error(message="Failed %s") for _ in range(5)]
        self.assertEqual(self._sent(limiter, same), 2)
        self.assertEqual(self._sent(limiter, [_error(message="Other %s")]), 1)

    def test_dropped_event_uses_no_process_token(self):
        limiter = self._limiter(max_per_hour=60, burst=5, max_per_error=1)
        self._sent(limiter, [_error()] * 10)
        # Only the one sent ValueError used a process token.
        events = [_error(lineno=i) for i in range(100, 110)]
        self.assertEqual(self._sent(limiter, events), 4)

    def test_transactions_are_not_limited(self):
        limiter = self._limiter(max_per_hour=60, burst=1, max_per_error=1)
        transaction = {"type": "transaction"}
        self.assertEqual(self._sent(limiter, [transaction] * 10), 10)

    def test_tracked_errors_stay_bounded(self):
        from collective.sentry import ratelimit

        limiter = self._limiter(max_per_hour=0, burst=0, max_per_error=1)
        self._sent(limiter, [_error(lineno=i) for i in range(3000)])
        self.assertLessEqual(len(limiter.errors), ratelimit.MAX_TRACKED_ERRORS)

    def test_warns_at_most_every_ten_minutes(self):
        limiter = self._limiter(max_per_hour=0, burst=0, max_per_error=1)
        with self.assertLogs("collective.sentry.ratelimit", "WARNING") as logs:
            self._sent(limiter, [_error()] * 50)
            self.clock.advance(601)
            self._sent(limiter, [_error(type_="KeyError")] * 2)
            self._sent(limiter, [_error()] * 2)
        self.assertEqual(len(logs.records), 2)


class TestInstallFromEnviron(unittest.TestCase):
    def setUp(self):
        reset_sentry()

    def tearDown(self):
        from collective.sentry import ratelimit

        ratelimit.install_from_environ({})
        reset_sentry()

    def _install(self, **env):
        from collective.sentry import ratelimit

        return ratelimit.install_from_environ(env)

    def test_defaults(self):
        from collective.sentry import ratelimit

        limiter = self._install()
        self.assertEqual(limiter.total.capacity, ratelimit.DEFAULT_MAX_EVENTS_BURST)
        self.assertEqual(limiter.max_per_error, ratelimit.DEFAULT_MAX_EVENTS_PER_ERROR)

    def test_zero_turns_limits_off(self):
        self.assertIsNone(
            self._install(
                SENTRY_MAX_EVENTS_PER_HOUR="0", SENTRY_MAX_EVENTS_PER_ERROR="0"
            )
        )

    def test_malformed_value_raises_clear_error(self):
        for value in ("many", "-1"):
            with self.assertRaises(RuntimeError) as ctx:
                self._install(SENTRY_MAX_EVENTS_PER_ERROR=value)
            self.assertIn("SENTRY_MAX_EVENTS_PER_ERROR", str(ctx.exception))

    def test_burst_zero_with_hourly_limit_raises(self):
        with self.assertRaises(RuntimeError):
            self._install(SENTRY_MAX_EVENTS_BURST="0")

    def test_installs_once(self):
        from collective.sentry import ratelimit
        from sentry_sdk.scope import global_event_processors

        self._install()
        self._install(SENTRY_MAX_EVENTS_PER_ERROR="2")
        self.assertEqual(global_event_processors.count(ratelimit._limit), 1)
        self.assertEqual(ratelimit._limiter.max_per_error, 2)

    def test_caps_a_logging_storm(self):
        # One error logged in a loop, as a fault on every request would.
        transport = CapturingTransport()
        self._install(SENTRY_MAX_EVENTS_PER_ERROR="5")
        sentry_sdk.init(dsn=DSN, transport=transport)
        log = logging.getLogger("collective.sentry.tests.storm")
        for _ in range(200):
            try:
                raise ValueError("database went away")
            except ValueError:
                log.exception("request failed")
        self.assertEqual(len(captured_events(transport)), 5)

    def test_applies_to_a_deployers_own_init(self):
        transport = CapturingTransport()
        sentry_sdk.init(dsn=DSN, transport=transport, default_integrations=False)
        self._install(SENTRY_MAX_EVENTS_PER_ERROR="1")
        for _ in range(3):
            sentry_sdk.capture_message("same message")
        self.assertEqual(len(captured_events(transport)), 1)
