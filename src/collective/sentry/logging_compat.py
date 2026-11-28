"""Keep a logger out of Sentry *events*, on either sentry-sdk major.

sentry-sdk 3 renamed ``ignore_logger`` to ``ignore_logger_for_events`` and
gave the old name a different job: it now silences only the Sentry Logs
stream. Both names exist on 3.x, so a 2.x call still imports and still runs
-- it just stops suppressing events, with no error, and no test failure
unless a test asserts on the events themselves.

This package has two loggers it must keep out of events, for two different
reasons (``Zope.SiteErrorLog`` would duplicate every publisher failure;
``ZODB.Connection`` can feed itself), so the choice lives here once rather
than being made correctly in one module and not the other.
"""

try:  # sentry-sdk >= 3
    from sentry_sdk.integrations.logging import ignore_logger_for_events
except ImportError:  # sentry-sdk 2
    from sentry_sdk.integrations.logging import (
        ignore_logger as ignore_logger_for_events,
    )

__all__ = ["ignore_logger_for_events"]
