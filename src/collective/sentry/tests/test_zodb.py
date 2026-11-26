from collective.sentry.tests.utils import captured_events
from collective.sentry.tests.utils import CapturingTransport
from collective.sentry.tests.utils import reset_sentry
from sentry_sdk.integrations.logging import LoggingIntegration

import logging
import sentry_sdk
import unittest

DSN = "https://x@example.com/1"


class TestZODBConnectionLog(unittest.TestCase):
    def setUp(self):
        from collective.sentry import zodb

        reset_sentry()
        zodb.ignore_connection_log()
        self.transport = CapturingTransport()
        sentry_sdk.init(
            dsn=DSN,
            transport=self.transport,
            integrations=[LoggingIntegration(event_level=logging.ERROR)],
        )

    def tearDown(self):
        reset_sentry()

    def _log_error(self, name):
        try:
            raise RuntimeError("Shouldn't load state when the connection is closed")
        except RuntimeError:
            logging.getLogger(name).exception("Shouldn't load state")

    def test_connection_log_is_not_sent(self):
        self._log_error("ZODB.Connection")
        self.assertEqual(captured_events(self.transport), [])

    def test_other_logs_are_still_sent(self):
        self._log_error("collective.sentry.tests")
        self.assertEqual(len(captured_events(self.transport)), 1)
