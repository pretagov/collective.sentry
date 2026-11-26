"""Keep ZODB's connection log out of Sentry events.

ZODB logs "Shouldn't load state for ... when the connection is closed" at
ERROR, and then raises ConnectionStateError. When Sentry reports an error
whose stack frames hold persistent objects from a connection that has since
closed, serialising the frames' local variables loads those objects: ZODB
logs the error again, and the logging integration reports that log with the
same objects in its frames. One request can loop like this for hours.

ZODB raises after everything it logs on this logger, so the error is still
reported once, by the publisher.
"""

try:
    # sentry-sdk 3: ignore_logger only covers Sentry Logs.
    from sentry_sdk.integrations.logging import ignore_logger_for_events
except ImportError:  # sentry-sdk 2
    from sentry_sdk.integrations.logging import (
        ignore_logger as ignore_logger_for_events,
    )

LOGGER = "ZODB.Connection"


def ignore_connection_log():
    ignore_logger_for_events(LOGGER)
