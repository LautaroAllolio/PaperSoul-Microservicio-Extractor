"""Micro-benchmark: in-house Markdown renderer vs. plain ``get_text`` (TASK-25).

Not part of CI — run it manually to inspect the cost of rendering Markdown:

    .venv/bin/python tests/extractor/load/bench_markdown.py

It reports, per fixture, the median wall-clock of
``document_to_markdown`` against a plain ``page.get_text()`` baseline, plus the
output-size ratio. The acceptance anchor is the load SLO (``docs/report.md``,
p95 < 500 ms), not a hard threshold here; this script only makes the cost
visible so regressions in the renderer are easy to spot.
"""

import argparse
import statistics
import time
from collections.abc import Callable
from functools import partial
from pathlib import Path

import pymupdf

from pdfextractor.infrastructure.extraction.markdown import document_to_markdown

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"
_DEFAULT_FIXTURES = ("valid_1p.pdf", "valid_5p.pdf", "valid_20p.pdf")


def _plain_text(path: Path) -> tuple[str, int]:
    with pymupdf.open(path) as document:
        text = "".join(document[index].get_text() for index in range(document.page_count))
        return text, document.page_count


def _markdown(path: Path) -> tuple[str, int]:
    with pymupdf.open(path) as document:
        return document_to_markdown(document), document.page_count


def _median_seconds(callable_once: Callable[[], object], repeats: int) -> float:
    samples = []
    for _ in range(repeats):
        start = time.perf_counter()
        callable_once()
        samples.append(time.perf_counter() - start)
    return statistics.median(samples)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repeats", type=int, default=15, help="samples per fixture")
    parser.add_argument("fixtures", nargs="*", default=list(_DEFAULT_FIXTURES))
    args = parser.parse_args()

    print(f"{'fixture':<16}{'pages':>6}{'text ms':>10}{'md ms':>10}{'ratio':>8}{'chars x':>10}")
    for name in args.fixtures:
        path = FIXTURES_DIR / name
        if not path.exists():
            print(f"{name:<16}  (missing — run gen_fixtures.py)")
            continue
        text, page_count = _plain_text(path)
        markdown, _ = _markdown(path)
        text_ms = _median_seconds(partial(_plain_text, path), args.repeats) * 1_000
        markdown_ms = _median_seconds(partial(_markdown, path), args.repeats) * 1_000
        size_ratio = len(markdown) / len(text) if text else float("nan")
        print(
            f"{name:<16}{page_count:>6}{text_ms:>10.1f}{markdown_ms:>10.1f}"
            f"{markdown_ms / text_ms:>8.2f}{size_ratio:>10.2f}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
