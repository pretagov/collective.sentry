"""Zero-config initialization from environment variables. Zope-only."""

from App.config import getConfiguration
from collective.sentry.integration import ZopeIntegration
from sentry_sdk.integrations.logging import LoggingIntegration

import importlib
import inspect
import logging
import os
import sentry_sdk
import warnings

logger = logging.getLogger(__name__)


def _load_integration(dotted):
    module_path, _, name = dotted.rpartition(".")
    try:
        module = importlib.import_module(module_path)
        return getattr(module, name)
    except (ImportError, AttributeError, ValueError) as exc:
        raise RuntimeError(
            f"SENTRY_INTEGRATIONS entry {dotted!r} cannot be loaded: {exc}"
        ) from exc


def _rate(variable, raw):
    try:
        rate = float(raw)
    except ValueError:
        rate = None
    if rate is None or not 0.0 <= rate <= 1.0:
        raise RuntimeError(
            f"Environment variable {variable} is malformed "
            f"(expected a number between 0 and 1): {raw}"
        )
    return rate


# sentry-sdk 3 renamed the breadcrumb option to `breadcrumb_level` and reused
# `level` for the Sentry-logs stream, so the parameter's presence identifies
# the major without parsing a version string.
_LOGGING_PARAMS = inspect.signature(LoggingIntegration.__init__).parameters
_SDK3_LOGGING = "breadcrumb_level" in _LOGGING_PARAMS


def logging_integration():
    """LoggingIntegration that behaves the same on sentry-sdk 2 and 3.

    sentry-sdk 3 changed two defaults, and inheriting either by accident is a
    silent regression:

    - ``event_level`` went from ``ERROR`` to ``None``, so a logged error no
      longer becomes an event. Every Zope/Plone deployment relies on logged
      errors reaching Sentry -- Products.SiteErrorLog and any add-on that
      logs rather than raises -- so losing that is losing error reporting
      without a single failing test to say so. Restored explicitly.
    - The Sentry-logs stream was gated off in 2.x by
      ``capture_sentry_logs=False``. In 3.x that option is gone and ``level``
      (default ``INFO``) controls it, so it is ON: upgrading would start
      streaming every INFO line a Plone site emits into a separately metered
      data category nobody opted into. Turned off, leaving 2.x behaviour.

    Note ``level`` is NOT the same option across the two majors -- it is the
    breadcrumb level on 2.x and the logs level on 3.x -- so it can only be
    passed on 3.x. Breadcrumbs keep their default on both.
    """
    kwargs = {"event_level": logging.ERROR}
    if _SDK3_LOGGING:
        kwargs["level"] = None
    return LoggingIntegration(**kwargs)


def initialize_from_environ(environ=None):
    """Init the SDK from env vars. Returns False when skipped."""
    environ = os.environ if environ is None else environ

    if "SENTRY_OPTIONAL" in environ:
        warnings.warn(
            "SENTRY_OPTIONAL is deprecated and ignored: a missing "
            "SENTRY_DSN never raises anymore.",
            DeprecationWarning,
            stacklevel=2,
        )

    if environ.get("SENTRY_DISABLE"):
        logger.info("Sentry disabled via SENTRY_DISABLE")
        return False
    if sentry_sdk.get_client().is_active():
        logger.info("Sentry already initialized, bootstrap skipped")
        return False
    dsn = environ.get("SENTRY_DSN")
    if not dsn:
        logger.info("SENTRY_DSN not set, Sentry reporting disabled")
        return False

    # Ours first, so an explicit SENTRY_INTEGRATIONS entry can replace it.
    integrations = [ZopeIntegration(), logging_integration()]
    raw = environ.get("SENTRY_INTEGRATIONS", "")
    for dotted in filter(None, (part.strip() for part in raw.split(","))):
        integrations.append(_load_integration(dotted)())

    extra_options = {}
    if "SENTRY_MAX_LENGTH" in environ:
        raw_max_length = environ["SENTRY_MAX_LENGTH"]
        try:
            extra_options["max_value_length"] = int(raw_max_length)
        except ValueError as exc:
            raise RuntimeError(
                "Environment variable SENTRY_MAX_LENGTH is malformed "
                f"(expected an integer): {raw_max_length}"
            ) from exc

    for variable, option in (
        ("SENTRY_SAMPLE_RATE", "sample_rate"),
        ("SENTRY_TRACES_SAMPLE_RATE", "traces_sample_rate"),
    ):
        if variable in environ:
            extra_options[option] = _rate(variable, environ[variable])

    sentry_sdk.init(
        dsn=dsn,
        environment=environ.get("SENTRY_ENVIRONMENT"),
        integrations=integrations,
        attach_stacktrace=True,
        max_breadcrumbs=50,
        **extra_options,
    )

    scope = sentry_sdk.get_global_scope()
    instancehome = getattr(getConfiguration(), "instancehome", "") or ""
    if instancehome:
        scope.set_tag("instance_name", instancehome.rsplit(os.path.sep, 1)[-1])
    if environ.get("SENTRY_PROJECT"):
        scope.set_tag("project", environ["SENTRY_PROJECT"])

    logger.info("Sentry integration enabled")
    return True
