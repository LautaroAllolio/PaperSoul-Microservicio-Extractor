"""Correlation identifiers for the request lifecycle (SPEC § 6.1, § 9.1).

Kept in infrastructure because it is a cross-cutting concern shared by every
layer, but the orchestration logic never imports it directly: the factory is
injected, so swapping the id scheme (ULID, W3C trace id) never touches the
application layer.
"""

import uuid


def new_request_id() -> str:
    """Return a fresh request correlation id.

    A random UUID v4 (SPEC § 6.1). Uniqueness is what matters — it is the value
    the orchestrator stamps on the request, the client puts on the wire as
    ``X-Request-Id`` and the handlers echo back, so operators can correlate a
    log line, a downstream call and a response without a shared registry.
    """
    return str(uuid.uuid4())
