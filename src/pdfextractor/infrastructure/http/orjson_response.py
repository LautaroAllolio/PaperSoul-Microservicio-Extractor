"""orjson-backed JSON response for the Extractor's (potentially large) payloads.

FastAPI 0.141 routes with a ``response_model`` and no custom response class
serialize through Pydantic's ``model_dump_json``. For the Extractor's payload —
``extracted_text`` is the bulk of the document and routinely tens of KB to a few
MB — that is measurably slower than ``jsonable_encoder`` + ``orjson.dumps``
(~18x on a 1 MB body), because orjson's SIMD string escaping beats Pydantic's
serializer on large strings. ``fastapi.responses.ORJSONResponse`` would give the
same speed but is deprecated and emits ``FastAPIDeprecationWarning`` on every
instantiation, so we own a tiny, warning-free equivalent instead. Switching the
app's ``default_response_class`` away from the ``DefaultPlaceholder`` also
disables Pydantic's slower ``dump_json`` fast path, which is exactly the point.
"""

from typing import Any

import orjson
from starlette.responses import Response

__all__ = ["OrjsonResponse"]


class OrjsonResponse(Response):
    """``application/json`` rendered by orjson, without deprecated FastAPI shims."""

    media_type = "application/json"

    def render(self, content: Any) -> bytes:
        return orjson.dumps(content, option=orjson.OPT_NON_STR_KEYS)
