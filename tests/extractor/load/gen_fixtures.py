"""Deterministic fixture generator for the pdfextractor load harness (Task 12).

Fixtures are regenerated on demand so no binary blob is committed to the
repository.  Expected outcomes live next to the bytes that produce them and are
exported to ``fixtures/manifest.json`` for the k6 suite to consume.
"""

import argparse
import hashlib
import json
import random
from pathlib import Path

import pymupdf

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"
MANIFEST_PATH = FIXTURES_DIR / "manifest.json"

_WORDS = (
    "alpha bravo charlie delta echo foxtrot golf hotel india juliet kilo lima mike "
    "november oscar papa quebec romeo sierra tango uniform victor whiskey xray yankee zulu"
).split()

_PAGE_W, _PAGE_H = 595, 842
_MARGIN = 48
_FONT_SIZE = 9
_LINE_H = 11
_COLS = 92
_CREATION_DATE = "D:20260101000000Z"


def _prose(seed: int, chars: int) -> str:
    """Build pseudo-random prose so the PDF payload does not compress to nothing."""
    rng = random.Random(seed)
    parts: list[str] = []
    written = 0
    while written < chars:
        word = rng.choice(_WORDS)
        parts.append(word)
        written += len(word) + 1
    return " ".join(parts)


def _fill_page(page: pymupdf.Page, text: str) -> None:
    y = _MARGIN
    for i in range(0, len(text), _COLS):
        if y > _PAGE_H - _MARGIN:
            break
        page.insert_text((_MARGIN, y), text[i : i + _COLS], fontsize=_FONT_SIZE)
        y += _LINE_H


def _new_doc() -> pymupdf.Document:
    doc = pymupdf.open()
    doc.set_metadata(
        {
            "title": "pdfextractor load fixture",
            "author": "gen_fixtures.py",
            "creationDate": _CREATION_DATE,
            "modDate": _CREATION_DATE,
        }
    )
    return doc


def _text_pdf(pages: int, chars_per_page: int, seed: int) -> bytes:
    doc = _new_doc()
    for index in range(pages):
        page = doc.new_page(width=_PAGE_W, height=_PAGE_H)
        _fill_page(page, _prose(seed + index, chars_per_page))
    blob = doc.tobytes(garbage=4, deflate=True)
    doc.close()
    return blob


def _no_text_pdf() -> bytes:
    doc = _new_doc()
    page = doc.new_page(width=_PAGE_W, height=_PAGE_H)
    page.draw_rect(
        pymupdf.Rect(_MARGIN, _MARGIN, _PAGE_W - _MARGIN, _PAGE_H - _MARGIN),
        color=(0.6, 0.6, 0.6),
        fill=(0.92, 0.92, 0.92),
        width=1,
    )
    blob = doc.tobytes(garbage=4, deflate=True)
    doc.close()
    return blob


def _encrypted_pdf() -> bytes:
    doc = _new_doc()
    page = doc.new_page(width=_PAGE_W, height=_PAGE_H)
    _fill_page(page, _prose(7, 600))
    blob = doc.tobytes(
        garbage=4,
        deflate=True,
        encryption=pymupdf.PDF_ENCRYPT_AES_256,
        owner_pw="pdfextractor-owner",
        user_pw="pdfextractor-secret",
        permissions=0,
    )
    doc.close()
    return blob


def _corrupt_pdf(valid: bytes) -> bytes:
    """A structurally broken file that still advertises itself as a PDF."""
    pivot = max(1, len(valid) // 3)
    damaged = bytearray(valid[:pivot])
    damaged[len(damaged) // 2 : len(damaged) // 2 + 16] = b"X" * 16
    return bytes(damaged)


def _zero_pages_pdf() -> bytes:
    return (
        b"%PDF-1.4\n"
        b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n"
        b"2 0 obj\n<< /Type /Pages /Kids [] /Count 0 >>\nendobj\n"
        b"trailer\n<< /Root 1 0 R /Size 3 >>\n%%EOF\n"
    )


def _record(
    name: str,
    payload: bytes,
    *,
    kind: str,
    expected: dict[str, object],
    description: str,
) -> dict[str, object]:
    (FIXTURES_DIR / name).write_bytes(payload)
    return {
        "file": name,
        "kind": kind,
        "bytes": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
        "expected": expected,
        "description": description,
    }


def build(*, max_upload_bytes: int) -> dict[str, object]:
    """Materialise every fixture and return the manifest describing them."""
    FIXTURES_DIR.mkdir(parents=True, exist_ok=True)

    happy = [
        _record(
            "valid_1p.pdf",
            _text_pdf(pages=1, chars_per_page=1_200, seed=11),
            kind="happy",
            expected={"status": 200},
            description="Single page, short text: the baseline happy path.",
        ),
        _record(
            "valid_5p.pdf",
            _text_pdf(pages=5, chars_per_page=4_000, seed=23),
            kind="happy",
            expected={"status": 200},
            description="Five pages, mid-size payload representative of a normal upload.",
        ),
        _record(
            "valid_20p.pdf",
            _text_pdf(pages=20, chars_per_page=6_000, seed=37),
            kind="happy",
            expected={"status": 200},
            description="Twenty pages: the upper end of a standard-size document.",
        ),
    ]

    edge = [
        _record(
            "no_text.pdf",
            _no_text_pdf(),
            kind="edge",
            expected={"status": 422, "error_contains": "texto"},
            description="Vector artwork only, zero extractable text.",
        ),
        _record(
            "encrypted.pdf",
            _encrypted_pdf(),
            kind="edge",
            expected={"status": 422, "error_contains": "cifrado"},
            description="AES-256 encrypted document.",
        ),
        _record(
            "corrupt.pdf",
            _corrupt_pdf(_text_pdf(pages=3, chars_per_page=2_000, seed=41)),
            kind="edge",
            expected={"status": 422, "error_contains": "leer"},
            description="Truncated PDF with a mangled body: unreadable but named .pdf.",
        ),
        _record(
            "not_pdf.bin",
            b"this file is plain text pretending to be a document\n" * 20,
            kind="edge",
            expected={"status": 422, "error_contains": "leer"},
            description="Not a PDF at all.",
        ),
        _record(
            "zero_pages.pdf",
            _zero_pages_pdf(),
            kind="edge",
            expected={"status": 422, "error_contains": "leer"},
            description="Structurally valid PDF declaring zero pages.",
        ),
        _record(
            "empty.pdf",
            b"",
            kind="edge",
            expected={"status_class": "4xx"},
            description="Zero-byte upload; spec does not pin an exact status.",
        ),
    ]

    manifest: dict[str, object] = {
        "generator": "tests/extractor/load/gen_fixtures.py",
        "max_upload_bytes": max_upload_bytes,
        "synthetic": [
            {
                "file": "__oversized__",
                "kind": "edge",
                "bytes": max_upload_bytes + 262_144,
                "expected": {"status": 413, "error_contains": "grande"},
                "description": (
                    "Synthetic multipart body larger than max_upload_bytes; built in the "
                    "k6 process so no oversized blob is written to disk."
                ),
            },
            {
                "file": "__missing_field__",
                "kind": "edge",
                "expected": {"status": 422, "error_contains": "campo"},
                "description": "Multipart body whose part is not named `file`.",
            },
        ],
        "happy": happy,
        "edge": edge,
    }

    MANIFEST_PATH.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--max-upload-bytes",
        type=int,
        default=1_048_576,
        help="Recorded in the manifest so k6 can size the synthetic oversized body.",
    )
    args = parser.parse_args()

    manifest = build(max_upload_bytes=args.max_upload_bytes)
    happy = manifest["happy"]
    edge = manifest["edge"]
    assert isinstance(happy, list) and isinstance(edge, list)
    total = sum(int(item["bytes"]) for item in [*happy, *edge])
    print(f"fixtures: {len(happy)} happy + {len(edge)} edge = {total} bytes -> {FIXTURES_DIR}")


if __name__ == "__main__":
    main()
