"""Paso 1 — ROJO: el repositorio contiene únicamente el microservicio extractor.

Objetivo de la reestructuración (plan § AD1-AD3): un único paquete `pdfextractor`
(FastAPI, PDF → JSON, cero disco, sin bases de datos ni lógica ajena). Hoy este
test falla: el orquestador `src/paperextractor/` y sus tests/docs siguen presentes
y el proyecto aún se identifica como `paperextractor`. Vira a verde en TASK-02/03.
"""

import tomllib
from pathlib import Path

from pdfextractor import __version__

ROOT = Path(__file__).resolve().parents[2]


def test_repository_contains_only_the_pdfextractor_microservice() -> None:
    assert (ROOT / "src" / "pdfextractor").is_dir()
    assert not (ROOT / "src" / "paperextractor").exists()
    assert not (ROOT / "tests" / "unit").exists()
    assert not (ROOT / "tests" / "integration").exists()
    assert not (ROOT / "tests" / "contract").exists()
    assert not (ROOT / "tests" / "harness.py").exists()
    assert not (ROOT / "tests" / "fakes.py").exists()
    assert not (ROOT / "tests" / "extractor" / "contract").exists()
    assert not (ROOT / "docs" / "SPEC-paperextractor.md").exists()


def test_project_metadata_identifies_the_extractor() -> None:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    assert pyproject["project"]["name"] == "pdfextractor"
    assert pyproject["tool"]["hatch"]["build"]["targets"]["wheel"]["packages"] == [
        "src/pdfextractor"
    ]
    assert pyproject["tool"]["ruff"]["lint"]["isort"]["known-first-party"] == ["pdfextractor"]


async def test_the_api_answers_the_basic_contract_with_the_extractor_identity(client) -> None:
    response = await client.get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "service": "pdfextractor",
        "version": __version__,
    }
