"""Response bodies of the Extractor contract (plan § 4)."""

from pydantic import BaseModel

__all__ = ["ExtractResponse"]


class ExtractResponse(BaseModel):
    """The exact ``200`` payload: three keys, nothing else."""

    extracted_text: str
    extraction_method: str
    page_count: int
