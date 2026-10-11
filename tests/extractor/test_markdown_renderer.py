"""TASK-25: the in-house Markdown renderer (fast, dependency-free).

``pymupdf4llm`` was measured at ~40-180x the cost of ``get_text`` (1.9-18 s for a
20-page document) and dragged onnxruntime + a GNN layout model into the image —
incompatible with the ``p95 < 500 ms`` contract. This renderer builds lightweight
GitHub Markdown straight from ``page.get_text("dict")``: headings by font size,
``**bold**``/``*italic*`` by span flags, bullet/numbered lists, and blank-line
paragraph/page separation. No tables and no OCR.
"""

import pymupdf

from pdfextractor.infrastructure.extraction.markdown import document_to_markdown

_BODY = 11.0
_TITLE = 24.0


def _document(pages: list[list[list[tuple[str, float, str]]]]) -> pymupdf.Document:
    """Build an in-memory PDF: pages → lines → spans ``(text, size, fontname)``."""
    source = pymupdf.open()
    for lines in pages:
        page = source.new_page()
        y = 72.0
        for spans in lines:
            x = 72.0
            for text, size, fontname in spans:
                page.insert_text((x, y), text, fontsize=size, fontname=fontname)
                x += pymupdf.get_text_length(text, fontname=fontname, fontsize=size)
            y += 24.0
    data = source.tobytes()
    source.close()
    return pymupdf.open(stream=data, filetype="pdf")


def _line(text: str, size: float = _BODY, fontname: str = "helv") -> list[tuple[str, float, str]]:
    return [(text, size, fontname)]


def test_a_large_title_becomes_a_level_1_heading() -> None:
    with _document(
        [[_line("Document Title", _TITLE, "hebo"), _line("plain body text here")]]
    ) as doc:
        markdown = document_to_markdown(doc)

    assert markdown.startswith("# Document Title")
    assert "plain body text here" in markdown


def test_heading_levels_scale_with_font_size() -> None:
    with _document(
        [
            [
                _line("Level one", 24.0, "hebo"),
                _line("Level two", 18.0, "hebo"),
                _line("Level three", 14.0, "hebo"),
                _line("Just body text that is long enough to set the base size"),
            ]
        ]
    ) as doc:
        markdown = document_to_markdown(doc)

    assert "# Level one" in markdown
    assert "## Level two" in markdown
    assert "### Level three" in markdown


def test_bold_spans_are_wrapped_in_double_asterisks() -> None:
    with _document([[_line("bold statement", _BODY, "hebo")]]) as doc:
        markdown = document_to_markdown(doc)

    assert "**bold statement**" in markdown


def test_italic_spans_are_wrapped_in_single_asterisks() -> None:
    with _document([[_line("italic aside", _BODY, "heit")]]) as doc:
        markdown = document_to_markdown(doc)

    assert "*italic aside*" in markdown


def test_mixed_spans_in_one_line_are_combined_and_styled() -> None:
    spans = [("plain and ", _BODY, "helv"), ("bold", _BODY, "hebo"), (" tail", _BODY, "helv")]
    with _document([[spans]]) as doc:
        markdown = document_to_markdown(doc)

    assert "plain and **bold** tail" in markdown


def test_bullet_lines_become_markdown_list_items() -> None:
    with _document([[_line("- alpha"), _line("· beta"), _line("1. gamma")]]) as doc:
        markdown = document_to_markdown(doc)

    assert "- alpha" in markdown
    assert "- beta" in markdown
    assert "1. gamma" in markdown


def test_pages_are_separated_by_a_blank_line() -> None:
    with _document([[_line("first page")], [_line("second page")]]) as doc:
        markdown = document_to_markdown(doc)

    assert markdown == "first page\n\nsecond page"


def test_empty_pages_contribute_nothing() -> None:
    with _document([[], [_line("only text")]]) as doc:
        markdown = document_to_markdown(doc)

    assert markdown == "only text"


def test_a_document_without_text_renders_an_empty_string() -> None:
    with _document([[]]) as doc:
        markdown = document_to_markdown(doc)

    assert markdown == ""


def test_no_markdown_markers_leak_for_plain_body_text() -> None:
    with _document([[_line("just a sentence.")]]) as doc:
        markdown = document_to_markdown(doc)

    assert markdown == "just a sentence."


def test_headings_do_not_carry_inline_markers() -> None:
    with _document(
        [[_line("Heading Text", _TITLE, "hebo"), _line("body word long enough")]]
    ) as doc:
        markdown = document_to_markdown(doc)

    assert "**" not in markdown.splitlines()[0]
