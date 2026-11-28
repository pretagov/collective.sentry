"""plone.app.testing layer with a capturing Sentry client."""

from collective.sentry.bootstrap import logging_integration
from collective.sentry.integration import ZopeIntegration
from collective.sentry.tests.utils import CapturingTransport
from collective.sentry.tests.utils import reset_sentry
from plone.app.testing import FunctionalTesting
from plone.app.testing import PLONE_FIXTURE
from plone.app.testing import PloneSandboxLayer

import sentry_sdk


class SentryLayer(PloneSandboxLayer):
    defaultBases = (PLONE_FIXTURE,)

    def setUpZope(self, app, configurationContext):
        import collective.sentry.tests

        self.loadZCML(package=collective.sentry.tests, name="testing.zcml")
        reset_sentry()
        self["sentry_transport"] = CapturingTransport()
        sentry_sdk.init(
            dsn="https://x@example.com/1",
            transport=self["sentry_transport"],
            # The same integrations bootstrap installs. With the SDK's
            # default LoggingIntegration instead, sentry-sdk 3's
            # event_level=None default means a logged error produces no
            # event, so the logging-path tests would measure an empty
            # pipeline rather than the behaviour this package promises.
            integrations=[ZopeIntegration(), logging_integration()],
        )

    def tearDownZope(self, app):
        reset_sentry()


SENTRY_FIXTURE = SentryLayer()
SENTRY_FUNCTIONAL_TESTING = FunctionalTesting(
    bases=(SENTRY_FIXTURE,), name="CollectiveSentry:Functional"
)
