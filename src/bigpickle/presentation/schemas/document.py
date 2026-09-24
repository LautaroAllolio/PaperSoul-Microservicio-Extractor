"""Output schemas for the document extraction envelope (SPEC §6.1 and §7)."""

from typing import Literal

from pydantic import BaseModel, ConfigDict


class ExtractedDocument(BaseModel):
    """Document metadata returned by the downstream Extractor."""

    model_config = ConfigDict(frozen=True)

    extracted_text: str
    page_count: int
    extraction_method: str


class OrchestrationMetadata(BaseModel):
    """BigPickle-side orchestration metadata, decoupled from the Extractor payload."""

    model_config = ConfigDict(frozen=True)

    downstream_service: Literal["extractor"] = "extractor"
    duration_ms: int


class DocumentExtractResponse(BaseModel):
    """Stable user-facing envelope for ``POST /api/v1/extract``."""

    model_config = ConfigDict(frozen=True)

    request_id: str
    document: ExtractedDocument
    orchestration: OrchestrationMetadata
