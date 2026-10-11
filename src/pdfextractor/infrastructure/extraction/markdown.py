"""Lightweight, dependency-free Markdown renderer for extracted PDF text.

``pymupdf4llm`` was ruled out: it costs ~40-180x a plain ``get_text`` (1.9-18 s
for 20 pages) and drags onnxruntime plus a GNN layout model into the image, which
is incompatible with the service's ``p95 < 500 ms`` contract. This renderer reads
``page.get_text("dict")`` — one C-side pass, ~1.3x the cost of plain text — and
emits GitHub-flavoured Markdown from the structure already present in the spans:

- headings by font size relative to the document's body size (``#``..``###``);
- ``**bold**`` / ``*italic*`` by span flags;
- bullet and numbered lists;
- blank lines between paragraphs and pages.

Out of scope (documented): tables, multi-column reading order and OCR.
"""

import re
from collections import defaultdict
from typing import Any

import pymupdf

__all__ = ["document_to_markdown"]

_BOLD = 1 << 4
_ITALIC = 1 << 1
_BULLET_MARKERS = ("•", "·", "‣", "▪", "◦", "●", "○", "-", "*")
_ORDERED_ITEM = re.compile(r"^\d{1,3}[.)]\s+")
_MIN_HEADING_RATIO = 1.25
_HEADING_RATIOS = ((1.8, 1), (1.5, 2), (_MIN_HEADING_RATIO, 3))


def document_to_markdown(document: pymupdf.Document) -> str:
    """Render every page of ``document`` as one Markdown string."""
    pages = (page_to_markdown(document[index]) for index in range(document.page_count))
    return "\n\n".join(page for page in pages if page)


def page_to_markdown(page: pymupdf.Page) -> str:
    """Render a single page as Markdown (empty string when it holds no text)."""
    blocks = page.get_text("dict")["blocks"]  # type: ignore[no-untyped-call]
    body_size = _body_font_size(blocks)
    paragraphs: list[str] = []
    for block in blocks:
        if block.get("type", 0) != 0:
            continue
        lines = [rendered for line in block["lines"] if (rendered := _render_line(line, body_size))]
        if lines:
            paragraphs.append("\n".join(lines))
    return "\n\n".join(paragraphs)


def _render_line(line: dict[str, Any], body_size: float) -> str:
    """Render one line: a heading, a list item, or a styled paragraph line."""
    spans = line["spans"]
    plain = "".join(span["text"] for span in spans)
    if not plain.strip():
        return ""
    level = _heading_level(max((span["size"] for span in spans), default=body_size), body_size)
    if level:
        return f"{'#' * level} {plain.strip()}"
    styled = "".join(_render_span(span) for span in spans).strip()
    return _as_list_item(styled) or styled


def _render_span(span: dict[str, Any]) -> str:
    """Wrap a span's non-blank core in ``**``/``*`` per its flags, keeping edges."""
    text: str = span["text"]
    core = text.strip()
    if not core:
        return text
    flags: int = span["flags"]
    if flags & _BOLD:
        core = f"**{core}**"
    if flags & _ITALIC:
        core = f"*{core}*"
    return text[: len(text) - len(text.lstrip())] + core + text[len(text.rstrip()) :]


def _as_list_item(text: str) -> str | None:
    """Normalize a leading bullet to ``- `` (ordered items pass through)."""
    if _ORDERED_ITEM.match(text):
        return text
    for marker in _BULLET_MARKERS:
        if text.startswith(f"{marker} "):
            return f"- {text[len(marker) + 1 :].lstrip()}"
    return None


def _heading_level(size: float, body_size: float) -> int:
    """Return ``1..3`` for a heading, ``0`` for body text."""
    if body_size <= 0:
        return 0
    ratio = size / body_size
    for threshold, level in _HEADING_RATIOS:
        if ratio >= threshold:
            return level
    return 0


def _body_font_size(blocks: list[dict[str, Any]]) -> float:
    """Pick the body font size: the span size carrying the most characters."""
    weights: dict[float, int] = defaultdict(int)
    for block in blocks:
        if block.get("type", 0) != 0:
            continue
        for line in block["lines"]:
            for span in line["spans"]:
                text = span["text"].strip()
                if text:
                    weights[round(span["size"], 1)] += len(text)
    if not weights:
        return 0.0
    return max(weights, key=lambda size: weights[size])
