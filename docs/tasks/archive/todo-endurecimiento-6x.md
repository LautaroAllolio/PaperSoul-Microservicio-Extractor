# Todo — Endurecimiento de confiabilidad del extractor y entrada del orquestador

Plan: `docs/tasks/plan.md`. Fuente de requisitos: análisis exhaustivo (secciones §6.1–§6.4, §6.7, §6.13).

## Phase 0: Foundation

- [ ] **TASK-01: Baseline verde** — `uv run pytest -q`, `uv run ruff check .`, `uv run ruff format --check src tests`, `uv run mypy -p paperextractor`, `uv run mypy -p pdfextractor` sin errores
- [ ] **TASK-02: Test de techo de memoria (`-m memory`)** — falla hoy, skip por defecto

### Checkpoint CP-1: Foundation
- [ ] Suite completa + ruff + mypy verdes
- [ ] Test de memoria rojo con el comportamiento actual (verde en TASK-06)
- [ ] Revisión con humano

## Phase 1: Timeout determinístico

- [ ] **TASK-03: Unicidad del timeout (§6.1)** — margen `queue_timeout + extraction_timeout + 2s` en `wait_for`; pool como reloj autoritativo; `tests/extractor/integration/test_timeout.py`

### Checkpoint CP-2: Timeout
- [ ] 504 único y determinístico; job en borde → 200; suite verde

## Phase 2: Memoria

- [ ] **TASK-04: Eliminar copia `bytes(buffer)` (§6.2)** — puerto `TextExtractor`, `ExtractionService` y `PyMuPDFExtractor` aceptan `bytes | bytearray`
- [ ] **TASK-05: `BufferPool` gate bloqueante (§6.3)** — `acquire(timeout)` + `503 OverloadError` en ruta cuando `None`
- [ ] **TASK-06: Cierre techo de memoria + invariantes concurrencia** — `-m memory` verde, sin deadlock

### Checkpoint CP-3: Memoria
- [ ] Test de memoria verde (pico RSS acotado, un solo `BytesIO`)
- [ ] `test_concurrency.py` intacto y verde
- [ ] Revisión con humano

## Phase 3: Endurecimiento de entrada

- [ ] **TASK-07: `Content-Length` malformado → 422 (§6.4)** — `InvalidRequestError` en `declared_length` de `paperextractor`
- [ ] **TASK-08: Validadores `Settings` pdfextractor (§6.7)** — `workers≥1`, `max_concurrent≥1`, timeouts>0, `max_upload_bytes>0`, `min_text_length≥0`
- [ ] **TASK-09: `.env.example` completo (§6.13)** — bloque `PDFEXTRACTOR_*`

### Checkpoint CP-4: Entrada
- [ ] Header basura → 422 (nunca 500); `workers=0` falla en arranque con mensaje claro

## Phase 4: Verificación final

- [ ] **TASK-10: Verificación integral** — suite completa + lint + types + `-m memory` verdes

### Checkpoint: Complete
- [ ] Todas las acceptance criteria cumplidas
- [ ] Listo para PR único (resuelve 6.1, 6.2, 6.3, 6.4, 6.7, 6.13)
- [ ] Decisión 6.1–6.4 documentada en `docs/perf-report.md` o ADR