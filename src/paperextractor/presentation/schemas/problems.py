"""RFC 9457 problem detail schemas (SPEC §7)."""

from pydantic import BaseModel, ConfigDict


class ProblemDetailError(BaseModel):
    """A single field error inside a ``ProblemDetails.errors`` array."""

    model_config = ConfigDict(frozen=True)

    loc: list[str]
    msg: str
    type: str


class ProblemDetails(BaseModel):
    """RFC 9457 machine-readable error format shared by every handler."""

    model_config = ConfigDict(frozen=True)

    type: str = "about:blank"
    title: str
    status: int
    detail: str
    instance: str | None = None
    errors: list[ProblemDetailError] | None = None
