Support sentry-sdk 3, and lift the ``<3`` cap. 3.0 removed
``sentry_sdk.scope.should_send_default_pii`` and the ``send_default_pii``
option it read, replacing them with ``data_collection``, whose defaults
collect more data. Rather than inherit that, the PII decision moved to
``collective.sentry.pii.should_send_pii`` and stays closed unless
``SENTRY_SEND_DEFAULT_PII`` is set, so sensitive request headers are still
filtered by default on both majors. On sentry-sdk 2 a deployer's own
``send_default_pii`` is still honoured when the variable is unset.
[instification]
