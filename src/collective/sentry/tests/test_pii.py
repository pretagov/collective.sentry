"""Tests for the PII decision, across both sentry-sdk majors."""

from collective.sentry.tests.utils import reset_sentry

import sentry_sdk
import unittest

DSN = "https://x@example.com/1"

# sentry-sdk 2.x has the helper and the option; 3.x removed both.
SDK_HAS_SEND_DEFAULT_PII = hasattr(
    __import__("sentry_sdk.scope", fromlist=["scope"]), "should_send_default_pii"
)


class TestShouldSendPii(unittest.TestCase):
    def setUp(self):
        reset_sentry()

    def tearDown(self):
        reset_sentry()

    def _call(self, **environ):
        from collective.sentry.pii import should_send_pii

        return should_send_pii(environ)

    def test_closed_by_default(self):
        # The whole point of this module. sentry-sdk 2.x defaulted
        # send_default_pii to False, so every install today filters the
        # headers in extraction.SENSITIVE_HEADERS. sentry-sdk 3.x replaced
        # that option with `data_collection`, whose defaults collect MORE
        # (user identity, HTTP bodies, more headers and cookies). Reading the
        # new defaults would start shipping Authorization and Cookie headers
        # from sites that never asked for it, so absence of configuration must
        # keep meaning "filter".
        self.assertFalse(self._call())

    def test_opt_in_is_explicit(self):
        for raw in ("1", "true", "TRUE", "yes", "on", " 1 "):
            self.assertTrue(self._call(SENTRY_SEND_DEFAULT_PII=raw), raw)

    def test_opt_out_is_explicit(self):
        for raw in ("0", "false", "FALSE", "no", "off"):
            self.assertFalse(self._call(SENTRY_SEND_DEFAULT_PII=raw), raw)

    def test_empty_counts_as_unset(self):
        # An unset variable in a shell script often arrives as "".
        self.assertFalse(self._call(SENTRY_SEND_DEFAULT_PII=""))
        self.assertFalse(self._call(SENTRY_SEND_DEFAULT_PII="   "))

    def test_malformed_fails_loudly(self):
        # Never guess with this one: silently choosing a default would either
        # leak credentials or quietly stop reporting the headers an operator
        # asked for. The same shape as bootstrap._rate.
        for raw in ("maybe", "2", "-1", "truthy"):
            with self.assertRaises(RuntimeError) as caught:
                self._call(SENTRY_SEND_DEFAULT_PII=raw)
            self.assertIn("SENTRY_SEND_DEFAULT_PII", str(caught.exception))
            self.assertIn(raw, str(caught.exception))

    @unittest.skipUnless(
        SDK_HAS_SEND_DEFAULT_PII, "sentry-sdk 3.x removed send_default_pii"
    )
    def test_delegates_to_the_sdk_on_sentry_sdk_2(self):
        # On 2.x a deployer's own init(send_default_pii=True) must keep
        # working with no env var set, exactly as before this module existed.
        sentry_sdk.init(dsn=DSN, send_default_pii=True, default_integrations=False)
        self.assertTrue(self._call())
        sentry_sdk.init(dsn=DSN, send_default_pii=False, default_integrations=False)
        self.assertFalse(self._call())

    @unittest.skipUnless(
        SDK_HAS_SEND_DEFAULT_PII, "sentry-sdk 3.x removed send_default_pii"
    )
    def test_the_env_var_overrides_the_sdk_on_sentry_sdk_2(self):
        # One setting that behaves the same on both majors, so a deployer can
        # set it once without caring which SDK resolves.
        sentry_sdk.init(dsn=DSN, send_default_pii=False, default_integrations=False)
        self.assertTrue(self._call(SENTRY_SEND_DEFAULT_PII="1"))
        sentry_sdk.init(dsn=DSN, send_default_pii=True, default_integrations=False)
        self.assertFalse(self._call(SENTRY_SEND_DEFAULT_PII="0"))

    @unittest.skipIf(SDK_HAS_SEND_DEFAULT_PII, "only sentry-sdk 3.x lacks the option")
    def test_an_active_client_does_not_open_pii_on_sentry_sdk_3(self):
        # 3.x's data_collection defaults are wide. An active client must not
        # be read as consent.
        sentry_sdk.init(dsn=DSN, default_integrations=False)
        self.assertFalse(self._call())


class TestSendDefaultPiiIsNotPassedToInit(unittest.TestCase):
    """`init(send_default_pii=...)` raises TypeError on sentry-sdk 3.x."""

    def setUp(self):
        reset_sentry()

    def tearDown(self):
        reset_sentry()

    def test_bootstrap_does_not_pass_send_default_pii(self):
        # Guards the other half of the 3.x break: bootstrap builds the option
        # dict itself, and send_default_pii is no longer a valid option, so
        # adding it would make init() raise "Unknown option" and leave Zope
        # unable to start -- the same outage shape as the removed import.
        from collective.sentry import bootstrap

        import inspect

        source = inspect.getsource(bootstrap)
        self.assertNotIn("send_default_pii", source)


class TestLoggingIntegrationDefaults(unittest.TestCase):
    """sentry-sdk 3 changed two LoggingIntegration defaults. Both regress
    silently, so both are pinned here rather than inherited."""

    def setUp(self):
        reset_sentry()

    def tearDown(self):
        reset_sentry()

    def test_a_logged_error_still_becomes_an_event(self):
        # sentry-sdk 3 defaults event_level to None. Inheriting that loses
        # every error that Zope/Plone LOGS rather than raises -- including
        # Products.SiteErrorLog -- and nothing would have failed to say so.
        from collective.sentry.bootstrap import logging_integration
        from collective.sentry.tests.utils import captured_events
        from collective.sentry.tests.utils import CapturingTransport

        import logging as logging_module

        transport = CapturingTransport()
        sentry_sdk.init(
            dsn=DSN,
            transport=transport,
            integrations=[logging_integration()],
            default_integrations=False,
        )
        log = logging_module.getLogger("collective.sentry.tests.logged")
        try:
            raise ValueError("logged, not raised")
        except ValueError:
            log.exception("something failed")
        sentry_sdk.flush()
        self.assertEqual(len(captured_events(transport)), 1)

    @unittest.skipIf(
        SDK_HAS_SEND_DEFAULT_PII, "the logs stream only exists on sentry-sdk 3"
    )
    def test_the_sentry_logs_stream_stays_off(self):
        # 2.x gated this off with capture_sentry_logs=False. On 3.x `level`
        # (default INFO) turns it ON, which would stream every INFO line a
        # Plone site emits into a separately metered data category.
        from collective.sentry.bootstrap import logging_integration

        import logging as logging_module

        seen = []

        class Collecting(sentry_sdk.transport.Transport):
            def __init__(self, options=None):
                super().__init__(options)

            def capture_envelope(self, envelope):
                seen.extend(item.type for item in envelope.items)

        sentry_sdk.init(
            dsn=DSN,
            transport=Collecting(),
            integrations=[logging_integration()],
            default_integrations=False,
        )
        log = logging_module.getLogger("collective.sentry.tests.logs")
        log.info("an info line")
        log.warning("a warning line")
        sentry_sdk.flush()
        self.assertEqual(
            [t for t in seen if "log" in t],
            [],
            f"sentry logs were sent: {seen}",
        )
