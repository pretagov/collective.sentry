from collective.sentry.tests.utils import CapturingTransport
from collective.sentry.tests.utils import FakeRequest
from collective.sentry.tests.utils import reset_sentry
from zope.component import getGlobalSiteManager
from ZPublisher import WSGIPublisher
from ZPublisher.interfaces import IPubFailure

import contextlib
import sentry_sdk
import unittest


class TestSetupOnce(unittest.TestCase):
    def setUp(self):
        reset_sentry()
        self._orig_pubevents = WSGIPublisher.transaction_pubevents
        self.calls = []
        self.swallow = False

        @contextlib.contextmanager
        def stub_pubevents(request, response, tm=None):
            self.calls.append(sentry_sdk.get_isolation_scope())
            try:
                yield
            except ValueError:
                if not self.swallow:
                    raise

        WSGIPublisher.transaction_pubevents = stub_pubevents

    def tearDown(self):
        WSGIPublisher.transaction_pubevents = self._orig_pubevents
        from collective.sentry.capture import on_pub_failure

        getGlobalSiteManager().unregisterHandler(on_pub_failure, (IPubFailure,))
        reset_sentry()

    def _init(self):
        from collective.sentry.integration import ZopeIntegration

        sentry_sdk.init(
            dsn="https://x@example.com/1",
            transport=CapturingTransport(),
            default_integrations=False,
            integrations=[ZopeIntegration()],
        )

    def _request(self, path="/plone/some-view"):
        return FakeRequest(environ={"PATH_INFO": path, "REQUEST_METHOD": "GET"})

    def test_wraps_transaction_pubevents_once(self):
        self._init()
        wrapped = WSGIPublisher.transaction_pubevents
        self.assertTrue(getattr(wrapped, "_sentry_wrapped", False))
        reset_sentry()  # allows setup_once to run again
        self._init()
        self.assertIs(WSGIPublisher.transaction_pubevents, wrapped)

    def test_registers_pub_failure_handler(self):
        from collective.sentry.capture import on_pub_failure

        self._init()
        handlers = list(getGlobalSiteManager().registeredHandlers())
        self.assertIn(on_pub_failure, [h.handler for h in handlers])

    def test_request_gets_isolated_scope_and_transaction(self):
        self._init()
        outer = sentry_sdk.get_isolation_scope()
        with WSGIPublisher.transaction_pubevents(self._request(), None):
            inside = sentry_sdk.get_isolation_scope()
        inner = self.calls[0]
        self.assertIsNot(inner, outer)
        self.assertIs(inside, inner)
        self.assertEqual(inner._transaction, "/plone/some-view")
        self.assertIs(sentry_sdk.get_isolation_scope(), outer)

    def test_each_attempt_gets_its_own_scope(self):
        self._init()
        for path in ("/first", "/second"):
            with WSGIPublisher.transaction_pubevents(self._request(path), None):
                pass
        first, second = self.calls
        self.assertIsNot(first, second)
        self.assertEqual(first._transaction, "/first")
        self.assertEqual(second._transaction, "/second")

    def test_reraised_exception_propagates(self):
        self._init()
        outer = sentry_sdk.get_isolation_scope()
        with self.assertRaises(ValueError):
            with WSGIPublisher.transaction_pubevents(self._request(), None):
                raise ValueError("boom")
        self.assertIs(sentry_sdk.get_isolation_scope(), outer)

    def test_handled_exception_stays_swallowed(self):
        # transaction_pubevents does not re-raise when an exception view
        # rendered the response; the wrapper must not change that.
        self.swallow = True
        self._init()
        outer = sentry_sdk.get_isolation_scope()
        with WSGIPublisher.transaction_pubevents(self._request(), None):
            raise ValueError("handled")
        self.assertIs(sentry_sdk.get_isolation_scope(), outer)


class TestSiteErrorLogIsNotReportedTwice(unittest.TestCase):
    """The publisher logs every failure to Zope.SiteErrorLog at ERROR, and
    this package captures the same failure from IPubFailure. Without the
    logger being ignored, each error reaches Sentry twice -- doubling what a
    fault costs against the quota, in exactly the situation where the quota
    matters.

    setup_once() has always ignored that logger, but nothing tested it, and
    the call is not portable: sentry-sdk 3 renamed the function and gave the
    old name a different job (it silences the Sentry Logs stream, not
    events), so the 2.x call keeps importing and keeps running on 3.x while
    quietly doing nothing.
    """

    def setUp(self):
        reset_sentry()

    def tearDown(self):
        from collective.sentry.capture import on_pub_failure

        getGlobalSiteManager().unregisterHandler(on_pub_failure, (IPubFailure,))
        reset_sentry()

    def test_a_site_error_log_record_makes_no_event(self):
        from collective.sentry.bootstrap import logging_integration
        from collective.sentry.integration import ZopeIntegration
        from collective.sentry.tests.utils import captured_events

        import logging

        transport = CapturingTransport()
        sentry_sdk.init(
            dsn="https://x@example.com/1",
            transport=transport,
            default_integrations=False,
            integrations=[ZopeIntegration(), logging_integration()],
        )
        logging.getLogger("Zope.SiteErrorLog").error(
            "Exception while rendering /plone/boom"
        )
        sentry_sdk.flush()
        # Compare loggers, not whole events: a captured event here is 100KB
        # of frames and the assertion output is unreadable.
        self.assertEqual(
            [event.get("logger") for event in captured_events(transport)], []
        )

    def test_another_logger_at_error_still_makes_an_event(self):
        # The guard above must not be a blanket "logged errors are dropped":
        # that is the regression the 3.x event_level default would cause, and
        # it would make the test above pass for the wrong reason.
        from collective.sentry.bootstrap import logging_integration
        from collective.sentry.integration import ZopeIntegration
        from collective.sentry.tests.utils import captured_events

        import logging

        transport = CapturingTransport()
        sentry_sdk.init(
            dsn="https://x@example.com/1",
            transport=transport,
            default_integrations=False,
            integrations=[ZopeIntegration(), logging_integration()],
        )
        logging.getLogger("collective.sentry.tests.elsewhere").error("a real error")
        sentry_sdk.flush()
        self.assertEqual(len(captured_events(transport)), 1)
