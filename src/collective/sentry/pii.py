"""Whether identifying request data may be sent. Works on sentry-sdk 2 and 3.

sentry-sdk 3.0 removed ``sentry_sdk.scope.should_send_default_pii`` *and* the
``send_default_pii`` option it read -- ``init(send_default_pii=False)`` now
raises ``TypeError: Unknown option``. They are replaced by the granular
``data_collection`` option, and per Sentry's migration guide its defaults
collect **more** than ``send_default_pii=False`` did: user identity, HTTP
request and response bodies, file paths, and more headers and cookies.

That matters here. ``send_default_pii`` defaulted to False, so every install
today filters the headers in ``extraction.SENSITIVE_HEADERS`` -- Authorization,
Cookie, Set-Cookie, X-Api-Key and the forwarded-IP pair. A port that simply
read 3.x's wider defaults would start shipping credentials and session cookies
off sites that never asked for it, without anybody changing a setting.

3.x also offers no public boolean to ask "may I send PII" (only a private
``sentry_sdk.utils._apply_key_value_collection_filtering``), and this package
builds its own ``extra["request"]["headers"]`` rather than letting the SDK
collect them -- so the SDK's own ``data_collection.http_headers`` filtering
would not cover these headers on 3.x even if it were consulted.

So: absence of configuration keeps meaning "filter", on both majors.
``SENTRY_SEND_DEFAULT_PII`` is the explicit opt-in, and behaves identically on
2.x and 3.x so a deployer can set it once without caring which SDK resolves.
On 2.x, with the variable unset, a deployer's own ``init(send_default_pii=...)``
is still honoured, so nothing regresses.
"""

import os

try:  # sentry-sdk < 3
    from sentry_sdk.scope import should_send_default_pii as _sdk_should_send_default_pii
except ImportError:  # sentry-sdk >= 3 removed it with the option it read
    _sdk_should_send_default_pii = None

SEND_DEFAULT_PII = "SENTRY_SEND_DEFAULT_PII"

TRUTHY = frozenset({"1", "true", "yes", "on"})
FALSEY = frozenset({"0", "false", "no", "off"})


def should_send_pii(environ=None):
    """True when identifying request data may be included in events."""
    environ = os.environ if environ is None else environ
    raw = environ.get(SEND_DEFAULT_PII)
    if raw is not None and raw.strip():
        value = raw.strip().lower()
        if value in TRUTHY:
            return True
        if value in FALSEY:
            return False
        # Do not guess. Picking either default would silently leak
        # credentials or silently drop headers an operator asked for, and
        # both are the kind of thing nobody notices for months.
        raise RuntimeError(
            f"Environment variable {SEND_DEFAULT_PII} is malformed "
            f"(expected one of {sorted(TRUTHY | FALSEY)}): {raw}"
        )
    if _sdk_should_send_default_pii is not None:
        return _sdk_should_send_default_pii()
    # sentry-sdk 3.x: no equivalent option, so stay closed.
    return False
