"""collective.sentry: sentry_sdk integration for Zope/Plone."""

from collective.sentry import ratelimit
from collective.sentry.bootstrap import initialize_from_environ
from collective.sentry.integration import ZopeIntegration  # noqa: F401

ratelimit.install_from_environ()
initialize_from_environ()
