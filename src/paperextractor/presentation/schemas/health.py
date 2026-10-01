"""Health and readiness response schemas (SPEC §6.2 and §6.3)."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict


class HealthResponse(BaseModel):
    """Liveness payload: reports only that the process is alive."""

    model_config = ConfigDict(frozen=True)

    status: Literal["ok"]
    service: str
    version: str
    timestamp: datetime


class ReadinessResponse(BaseModel):
    """Readiness payload plus downstream reachability report."""

    model_config = ConfigDict(frozen=True)

    status: Literal["ready"]
    downstream: dict[str, str]
    timestamp: datetime
