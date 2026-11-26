"""collective.sentry: sentry_sdk integration for Zope/Plone."""

from collective.sentry import zodb
from collective.sentry.bootstrap import initialize_from_environ
from collective.sentry.integration import ZopeIntegration  # noqa: F401

zodb.ignore_connection_log()
initialize_from_environ()
