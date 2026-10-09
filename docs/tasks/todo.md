# Todo — Unificación del repositorio en un único microservicio `pdfextractor`

Plan: `docs/tasks/plan.md`. Flujo: TDD estricto (rojo → verde) + pausa obligatoria tras cada tarea.

## Paso 0: Recuperación (completado)

- [x] Restaurar la épica desde `64fc7d7` (reflog `HEAD@{1}`); pytest valida 324 tests
- [x] Archivar plan previo de endurecimiento §6.x en `docs/tasks/archive/`

## Paso 1: Auditoría y Estructura

- [x] **TASK-01: Test de identidad estructural/funcional (ROJO)**
      — `tests/extractor/test_repository_identity.py`: solo existe `src/pdfextractor`
      (sin `src/paperextractor`, sin `tests/unit`, `tests/integration`, `tests/contract`,
      `harness.py`, `fakes.py`, `tests/extractor/contract`, sin `docs/SPEC-paperextractor.md`);
      `pyproject` con `name = pdfextractor` y wheel `["src/pdfextractor"]`;
      `/health` responde `service: pdfextractor`
- [x] **TASK-02: Eliminar el orquestador** — `src/paperextractor/`, `tests/unit/`,
      `tests/integration/`, `tests/harness.py`, `tests/fakes.py`, `tests/contract/`,
      `tests/extractor/contract/`, `docs/SPEC-paperextractor.md` eliminados (49 ficheros).
      Test estructural y funcional de identidad en verde; suite 97 passed/2 skipped.
      ⚠ El test de metadata (`pyproject name = pdfextractor`) queda rojo hasta TASK-03.

### Checkpoint CP-1: Estructura
- [x] Suite completa + ruff + mypy verdes; test de identidad verde
- [x] Revisión con humano (TASK-01..02 aprobadas y commiteadas)

## Paso 2: Identidad del proyecto

- [x] **TASK-03: `pyproject.toml` → `pdfextractor`** (name, description, packages,
      known-first-party) + `.env.example` con bloque `PDFEXTRACTOR_*`. Además: pymupdf y
      prometheus-client promovidos a dependencias runtime, `httpx` movido a dev, extra
      `extractor` y `respx` eliminados (huérfanos). Test de identidad **3/3 verde**;
      suite 98 passed/2 skipped; ruff + mypy limpios. `uv.lock` regenerado.
- [x] **TASK-04: CI** `.github/workflows/ci.yml` solo `pdfextractor` (mypy -p pdfextractor):
      quitado `--extra extractor`, el doble `mypy -p paperextractor`, el input
      `run_contract_tests`, la secret `EXTRACTOR_BASE_URL` y el paso `-m contract`
      (huérfanos tras eliminar el orquestador). YAML válido; pasos CI verificados localmente.

### Checkpoint CP-2: Identidad
- [x] `uv`/build sano (`pdfextractor-0.1.0`); `grep -r paperextractor` vacío en src/config/README/.github
- [x] Revisión con humano (TASK-03..04 aprobadas y commiteadas)

## Paso 3: Documentación, contrato y cierre

- [x] **TASK-05: README/docs + contrato REST** documentado (PDF → JSON estricto):
      `README.md` reescrito para el extractor (zero-disk, memoria acotada, endpoints,
      env `PDFEXTRACTOR_*`, errores); nuevo `docs/api-contract.md` (contrato estricto
      con códigos y garantías); marcador `contract` huérfano removido de `tests/conftest.py`.
- [x] **TASK-06: Verificación integral** — suite + lint + types + `-m memory`. Al
      ejecutar el memory gate quedó rojo `test_...without_copying` (endurecimiento
      zero-copy perdido en el reset): re-aplicado el ancho `bytes | bytearray` en
      puerto/servicio/extractor/pool y la ruta entrega el buffer del pool sin copia.
      Resultado final: **98 passed/2 skipped**, `-m memory` **2 passed**, ruff + mypy
      limpios, `uv build` produce `dist/pdfextractor-0.1.0-*.whl`.

### Checkpoint: Complete
- [x] Todas las acceptance criteria cumplidas; listo para PR único

## Paso 4: Integración con el Orquestador (`EXTRACTOR_URL` + contrato real)

Plan: `docs/tasks/plan.md` (§ Paso 4). Contrato autoritativo leído del repo del
Orquestador (`internal/client/extractor.go`, `internal/domain/document.go`,
`internal/client/multipart.go`) — sin modificarlo.

- [x] **TASK-07: Endpoint alineado al orquestador** — la ruta pasa de
      `POST /api/v1/extract` a `POST /api/v1/extractions` (el path que invoca el
      orquestador). El `response_model=ExtractResponse` y la respuesta JSON
      **no cambian**: `extracted_text`/`extraction_method`/`page_count` ya
      calzan con `domain.ExtractResponse`.
- [x] **TASK-08: Correlación y headers del orquestador** —
      `X-Correlation-Id` se honra (fallback `X-Request-Id`) y se ecoa bajo el
      mismo header (`middlewares.py`, `deps.py`). El multipart real del
      orquestador (campo `checksum` primero + `file`, header
      `X-Document-Checksum`) se cubre con test de regresión en
      `test_extract_api.py`.
- [x] **TASK-09: Tests/doc/load actualizados al nuevo path** — referencias a
      `/api/v1/extractions` en integración, middlewares, telemetría, memory,
      concurrency, load-tests y k6; suite verde.
- [x] **TASK-10: Dockerfile del extractor** — build reproducible en
      `ghcr.io/astral-sh/uv:python3.12-bookworm-slim`, no-root, `EXPOSE 9000`,
      `PDFEXTRACTOR_PORT=9000` y `PDFEXTRACTOR_MIN_TEXT_LENGTH=0` (texto vacío
      válido para el orquestador). `.dockerignore` y `.env.example` alineados.
- [x] **TASK-11: Guía de validación E2E** — `docs/integration.md`: valor
      `EXTRACTOR_URL=http://extractor:9000`, servicio `extractor`, revisión de
      logs y cURL a `POST /api/v1/documents/process`.

### Checkpoint CP-4: Integración
- [x] Suite completa + `-m memory` + ruff + mypy verdes
- [x] Imagen `pdfextractor:test` construida; contenedor responde `/health` en
      `:9000`; extracción real (multipart checksum+file) → `200` con las 3 claves
      y `x-correlation-id` ecoado; PDF sin texto → `200` con texto vacío
- [x] Revisión con humano (TASK-07..11 aprobadas)