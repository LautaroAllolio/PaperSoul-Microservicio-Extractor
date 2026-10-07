# Tasks — pdfextractor (Microservicio de Extracción / downstream)

Task list operativo de la implementación del **Extractor** de documentos. Detalle técnico en `docs/tasks/plan-extractor.md`; contrato downstream en `SPEC-paperextractor.md` § 9. El orquestador (`src/paperextractor`) **no se toca**.

---

## Fase 0: Fundación

### Task 1: Scaffold del paquete `pdfextractor`, settings, app factory y wiring de packaging/CI

**Description:** Crear `src/pdfextractor/` con capas `presentation/`, `application/`, `infrastructure/` y un `main.py` con app factory + lifespan mínimo. Implementar `infrastructure/config/settings.py` (pydantic-settings, prefijo `PDFEXTRACTOR_`, todas las variables de plan § 7). Agregar las dependencias `pymupdf` y `prometheus-client` al extra opcional `extractor` en `pyproject.toml` (**sin `python-multipart`**: el test de invariante del orquestador `test_the_extract_route_needs_no_multipart_parser_dependency` exige que no esté importable), registrar `src/pdfextractor` en hatch, y extender `.github/workflows/ci.yml` para instalar el extra, correr la suite del Extractor (`uv run pytest tests/extractor`) y `mypy -p pdfextractor`.

**Acceptance criteria:**
- [x] `uv sync --extra extractor` instala sin errores; `pdfextractor` es importable.
- [x] `create_app()` levanta una app FastAPI con `GET /health` respondiendo `200`.
- [x] `Settings` carga defaults correctos y overrides de entorno (prefijo `PDFEXTRACTOR_`).
- [x] `uv run mypy -p pdfextractor` y `uv run ruff check .` pasan en limpio.
- [x] El orquestador sigue pasando su suite completa (203 tests) intacta.

**Verification:**
- [x] Tests pass: `uv run pytest tests/extractor` (test básico de arranque/settings).
- [x] Build succeeds: `uv run ruff format --check src tests`.
- [x] Type check: `uv run mypy -p pdfextractor`.
- [x] CI: el job incluye la suite del Extractor; no rompe el job del orquestador.

**Dependencies:** None

**Files likely touched:**
- `src/pdfextractor/__init__.py`, `src/pdfextractor/main.py`
- `src/pdfextractor/infrastructure/config/settings.py` (+ `__init__`s de capas)
- `pyproject.toml`, `.github/workflows/ci.yml`
- `tests/extractor/test_app.py`, `tests/extractor/test_settings.py`

**Estimated scope:** Medium (6-7 archivos).

---

## Fase 1: Motor de parseo y optimización de memoria

### Task 2: Lector multipart en memoria (zero-disk) + guard de tamaño

**Description:** Implementar `infrastructure/http/multipart_reader.py`: un parser de `multipart/form-data` alimentado por `request.stream()` (asíncrono, chunk a chunk) cuyo sink es un `bytearray` en memoria — nunca `UploadFile`, nunca `SpooledTemporaryFile`, nunca `tempfile` — y **solo stdlib** (`python-multipart` excluido por la invariante del orquestador). Debe aceptar bodies con `Content-Length` y chunked, manejar límites CRLF/LF, extraer solo el part `file` y abortar con `OversizedError` (413) al superar `PDFEXTRACTOR_MAX_UPLOAD_BYTES` durante la lectura.

**Acceptance criteria:**
- [x] El part `file` extraído es byte-identical al enviado (varios tamaños, cruce de frontera de chunk).
- [x] Se aborta al exceder el tamaño durante el streaming (sin leer completo).
- [x] Multipart sin `file`, trunkado o con boundary inválido produce el error de dominio correcto.
- [x] Nunca se invoca `SpooledTemporaryFile`/`tempfile` (monkeypatch/rastreo de imports).

**Verification:**
- [x] Tests pass: `uv run pytest tests/extractor/test_multipart_reader.py`.
- [x] `uv run ruff check .` y `uv run mypy -p pdfextractor` en limpio.

**Dependencies:** Task 1

**Files likely touched:**
- `src/pdfextractor/infrastructure/http/multipart_reader.py`
- `src/pdfextractor/application/errors.py` (parcial: `OversizedError`, `EmptyFileError`, `MissingFieldError`)
- `tests/extractor/test_multipart_reader.py`

**Estimated scope:** Medium (2-3 archivos).

---

### Task 3: Pool acotado de buffers (`BufferPool`)

**Description:** Implementar `infrastructure/memory/pool.py`: un pool acotado de `bytearray` reusados entre requests con `memoryview` para slicing, reset de longitud en cada préstamo (sin sangrado de datos entre requests) y tamaño máximo = concurrencia configurada. Uso: sink del `multipart_reader`.

**Acceptance criteria:**
- [x] Los buffers se reusan (test de identidad del objeto) dentro del tope del pool.
- [x] No hay sangrado de contenido entre préstamos consecutivos.
- [x] El pool nunca excede su capacidad y devuelve `None`/genera nuevo solo dentro del tope.

**Verification:**
- [x] Tests pass: `uv run pytest tests/extractor/test_buffer_pool.py`.

**Dependencies:** Task 1 (paralelo a Task 2)

**Files likely touched:**
- `src/pdfextractor/infrastructure/memory/pool.py`
- `tests/extractor/test_buffer_pool.py`

**Estimated scope:** Small (1-2 archivos).

---

### Task 4: Adaptador de extracción PyMuPDF + errores de dominio

**Description:** Implementar `infrastructure/extraction/pymupdf_extractor.py` que implementa el puerto `TextExtractor` (Protocol): abre el PDF desde `io.BytesIO(buf)` (sin disco), une el texto de todas las páginas en memoria, devuelve `(text, page_count)` y siempre cierra el documento. Detectar PDF cifrado, corrupto y con 0 páginas y traducirlos a las excepciones de dominio correctas (`EncryptionError`, `UnreadableError`). Cerrar la jerarquía de `PdfExtractorError` en `application/errors.py` con `status` + mensaje acotado.

**Acceptance criteria:**
- [x] PDF válido devuelve texto exacto + `page_count` correcto.
- [x] PDF cifrado → `EncryptionError` (status 422); corrupto/no-PDF → `UnreadableError`; 0 páginas → `UnreadableError`.
- [x] No se escapan handles de archivo abiertos (sin `ResourceWarning`).
- [x] Si un sample real devuelve texto, `MIN_TEXT_LENGTH` decide texto vs "sin texto" como retorno, no como excepción en esta capa.

**Verification:**
- [x] Tests pass: `uv run pytest tests/extractor/test_pymupdf_extractor.py` (fixtures PDF: válido, cifrado, corrupto, vacío).
- [x] `uv run ruff check .` / `uv run mypy -p pdfextractor` en limpio.

**Dependencies:** Task 1

**Files likely touched:**
- `src/pdfextractor/infrastructure/extraction/pymupdf_extractor.py`
- `src/pdfextractor/application/errors.py`
- `src/pdfextractor/application/interfaces.py` (`TextExtractor`, `ByteSink`)
- `tests/extractor/test_pymupdf_extractor.py`

**Estimated scope:** Medium (2-3 archivos).

---

### Task 5: Servicio de extracción (capa aplicación)

**Description:** Implementar `application/services/extraction_service.py`: secuencia read → validate → extract (PyMuPDF) → normalizar (NFC, colapsar `\n{3,}`) → `ExtractionResult {extracted_text, extraction_method, page_count}`. Recibe el buffer ya extraído o la fuente; depende SOLO de puertos (`TextExtractor`); no importa FastAPI/httpx (test por AST). Propaga errores de dominio sin transformarlos.

**Acceptance criteria:**
- [x] Con un `TextExtractor` fake produce `ExtractionResult` completo y correcto.
- [x] Propaga excepciones de dominio intactas.
- [x] Normaliza el texto (NFC y colapso de líneas en blanco).
- [x] Test de capas por AST: `application/` no importa infraestructura concreta ni HTTP.

**Verification:**
- [x] Tests pass: `uv run pytest tests/extractor/test_extraction_service.py`.
- [x] `uv run ruff check .` / `uv run mypy -p pdfextractor` en limpio.

**Dependencies:** Tasks 2, 4

**Files likely touched:**
- `src/pdfextractor/application/services/extraction_service.py` (+ `__init__.py`)
- `src/pdfextractor/application/interfaces.py`
- `tests/extractor/test_extraction_service.py`

**Estimated scope:** Medium (2-3 archivos).

---

### Checkpoint A (tras Tasks 2-5)
- [x] `uv run pytest tests/extractor` verde.
- [x] Zero-disk verificado en el reader; buffers reusados; pool acotado.
- [x] Capas verificadas por AST (application no ve HTTP).
- [ ] Review con humano del diseño de parsing antes de exponer endpoints.

---

## Fase 2: Contrato API e integración con el cliente del orquestador

### Task 6: Endpoints `POST /api/v1/extract`, `GET /health`, `GET /ready`

**Description:** Implementar `presentation/api/v1/extract.py` (router con `Request` directo, **sin** `UploadFile` — D2), `presentation/api/deps.py` (DI: `get_extraction_service`, `get_request_id`), montar el router en `main.py`, handlers globales que devuelven `{"error": ...}` con el status correcto, y `GET /health` (200 siempre) + `GET /ready` (200 / 503). Emite exactamente `{"extracted_text","extraction_method","page_count"}` en `200`.

**Acceptance criteria:**
- [x] `POST` multipart real (campo `file`) → `200` con el body exacto del contrato (§ 4).
- [x] Cada fallo (cifrado, corrupto, sin texto, sin campo, demasiado grande, saturado) responde `{"error": str}` con el status correcto.
- [x] `/health` responde `200` sin tocar dependencias; `/ready` refleja el estado del pool.
- [x] El endpoint nunca dispara `MultiPartParser` de Starlette (no usa `File`/`Form`).

**Verification:**
- [x] Tests pass: `uv run pytest tests/extractor/integration/test_extract_api.py`.
- [x] `uv run ruff check .` / `uv run mypy -p pdfextractor` en limpio.

**Dependencies:** Task 5

**Files likely touched:**
- `src/pdfextractor/presentation/api/v1/extract.py`, `src/pdfextractor/presentation/api/v1/health.py`
- `src/pdfextractor/presentation/api/deps.py`, `src/pdfextractor/presentation/schemas/document.py`
- `src/pdfextractor/presentation/errors/handlers.py`, `src/pdfextractor/main.py`
- `tests/extractor/integration/test_extract_api.py`

**Estimated scope:** Medium (4-5 archivos).

---

### Task 7: Test de contrato cross-service con el cliente real del orquestador

**Description:** Fixture de pytest que levanta `pdfextractor` con `uvicorn.Server` en un puerto efímero (thread de fondo) y lo conduce con el `HttpExtractorClient` + `ExtractionOrchestrator` **reales** de `src/paperextractor` (sin mocks en el camino crítico). Verificar: `200` con `ExtractorSuccess`, relay byte-identical, y el mapeo completo (`4xx`→422 `extraction-failed`, `5xx`→502 `upstream-error`, refused→502, timeout→504). No se edita código ni tests del orquestador.

**Acceptance criteria:**
- [x] El `HttpExtractorClient.forward()` real devuelve `ExtractorSuccess` válido contra `pdfextractor`.
- [x] Los bytes recibidos por el extractor son byte-identical a los enviados.
- [x] Cada fallo downstream mapeado produce el RFC 9457 correcto del orquestador.
- [x] Cero modificaciones en `src/paperextractor/` y `tests/` del orquestador.

**Verification:**
- [x] Tests pass: `uv run pytest tests/extractor/contract/test_orchestrator_contract.py`.
- [x] Suite completa del orquestador sigue verde (`uv run pytest tests/unit tests/integration`).

**Dependencies:** Task 6

**Files likely touched:**
- `tests/extractor/contract/test_orchestrator_contract.py`
- `tests/extractor/conftest.py` (fixture de servidor uvicorn)

**Estimated scope:** Medium (2 archivos, mayormente tests).

---

### Checkpoint B (tras Tasks 6-7)
- [x] El cliente real del orquestador habla con `pdfextractor` end-to-end.
- [x] Contrato § 4 satisfecho byte a byte.
- [ ] Revisión humana antes del trabajo de resiliencia.

---

## Fase 3: Resiliencia, manejo de errores y middlewares

### Task 8: Middlewares (request-id, backstop de tamaño, timeout, errores globales, shutdown)

**Description:** Implementar `infrastructure/middleware/middlewares.py`: (a) `X-Request-Id` inbound→log→outbound; (b) backstop de tamaño de request (por si el pool no cortó); (c) timeout por job (`wait_for` + terminación del worker); (d) handler global de `PdfExtractorError` y de excepciones no controladas → `{"error": ...}` con status correcto; (e) shutdown del lifespan que drena y cierra el process pool sin leaks.

**Acceptance criteria:**
- [x] `X-Request-Id` presente → se loguea y se devuelve en el header; ausente → se genera.
- [x] Un job que excede `EXTRACTION_TIMEOUT_SECONDS` termina con el status/timeout configurado.
- [x] Cualquier excepción no mapeada → `500 {"error": "internal"}` (canario: el mensaje interno no llega).
- [x] Shutdown no deja workers colgados ni warnings de loop. *(pool real + `finally: extractor.close()`, Task 9)*

**Verification:**
- [x] Tests pass: `uv run pytest tests/extractor/integration/test_middlewares.py`.
- [x] `uv run ruff check .` / `uv run mypy -p pdfextractor` en limpio.

**Dependencies:** Task 6

**Files likely touched:**
- `src/pdfextractor/infrastructure/middleware/middlewares.py`
- `src/pdfextractor/main.py` (registro de middlewares)
- `src/pdfextractor/application/errors.py` (si falta `ExtractionTimeoutError`)
- `tests/extractor/integration/test_middlewares.py`

**Estimated scope:** Medium (3-4 archivos).

---

### Task 9: Concurrencia, backpressure y process pool

**Description:** Implementar `infrastructure/concurrency/pool.py`: `ProcessPoolExecutor(workers)` creado en lifespan, `asyncio.Semaphore(max_concurrent_extractions)`, `asyncio.wait_for(queue_timeout)`; saturación → `OverloadError` (503). Recuperación ante crash de worker (recrear/purgar futuras fallidas) y `ReadyState` que degrada `/ready` a 503 bajo sobrecarga. Exponer `extractor_inflight`, `extractor_queue_depth`, `extractor_worker_restarts_total`.

**Acceptance criteria:**
- [x] Carga concurrente nunca supera `max_concurrent_extractions`.
- [x] Saturación responde `503` rápido (no acumula colas infinitas).
- [x] Un worker que muere se detecta; las futuras afectadas fallan con `{"error"}` controlado y el pool sigue sirviendo.
- [x] `/ready` pasa a 503 bajo sobrecarga y vuelve a 200 al recuperarse.

**Verification:**
- [x] Tests pass: `uv run pytest tests/extractor/integration/test_concurrency.py`.
- [x] `uv run ruff check .` / `uv run mypy -p pdfextractor` en limpio.

**Dependencies:** Task 8

**Files likely touched:**
- `src/pdfextractor/infrastructure/concurrency/pool.py`
- `src/pdfextractor/main.py` (lifespan)
- `src/pdfextractor/infrastructure/telemetry/metrics.py` (parcial)
- `tests/extractor/integration/test_concurrency.py`

**Estimated scope:** Medium (3-4 archivos).

---

### Task 10: Logs JSON estructurados + métricas Prometheus (`/metrics`)

**Description:** Implementar `infrastructure/telemetry/logging_.py` (formatter JSON de stdlib con contexto enlazado: `request_id`, `duration_ms`, `bytes`, `pages`, `method`, `outcome`, `error_type`) y `metrics.py` (registro de todas las métricas de plan § 6). Montar `/metrics` (ASGI) cuando `PDFEXTRACTOR_METRICS_ENABLED`. Cardinalidad de labels baja.

**Acceptance criteria:**
- [x] Los logs de request son single-line JSON con `request_id` y campos del contrato.
- [x] Test canario: el contenido del documento jamás aparece en logs (filename solo como longitud).
- [x] Tras una request, `/metrics` expone los valores esperados de cada métrica definida.

**Verification:**
- [x] Tests pass: `uv run pytest tests/extractor/integration/test_telemetry.py`.
- [x] `uv run ruff check .` / `uv run mypy -p pdfextractor` en limpio.

**Dependencies:** Task 8

**Files likely touched:**
- `src/pdfextractor/infrastructure/telemetry/logging_.py`, `src/pdfextractor/infrastructure/telemetry/metrics.py`
- `src/pdfextractor/main.py`
- `tests/extractor/integration/test_telemetry.py`

**Estimated scope:** Medium (3-4 archivos).

---

### Checkpoint C (tras Tasks 8-10)
- [ ] Resiliencia verificada bajo sobrecarga inducida, timeout y crash de worker.
- [ ] Métricas y logs inspeccionados (valores correctos, sin contenido del documento).
- [ ] Revisión humana.

---

## Fase 4: Benchmarking, load testing y profiling

### Task 11: Microbenchmarks — overhead no-parse vs tiempo de parseo

**Description:** Harness con `pytest-benchmark` (o benchmark a medida) que separa el overhead no-parse (transport + dispatch) del tiempo de parseo PyMuPDF. Definir y asertar el presupuesto de overhead (p99 < 1 ms en hardware de referencia). Guardar el reporte en `docs/perf-report.md`.

**Acceptance criteria:**
- [ ] El reporte distingue overhead vs parseo y los SLO asociados.
- [ ] Existe un test de presupuesto de overhead estable en CI (con holgura para no flakear).

**Verification:**
- [ ] `uv run pytest tests/extractor/benchmarks` produce el reporte y pasa.
- [ ] `uv run ruff check .` en limpio.

**Dependencies:** Task 10

**Files likely touched:**
- `tests/extractor/benchmarks/test_overhead.py`
- `docs/perf-report.md` (parcial)

**Estimated scope:** Small (2 archivos).

---

### Task 12: Load y soak testing

**Description:** Harness de carga asíncrona (locust o httpx async) que ejercita happy path, oversized, cifrado y saturación; mide RPS, percentiles de latencia, comportamiento de backpressure y estabilidad de RSS en un soak corrido (o un número acotado de requests configurable para CI). Documentar curvas en `docs/perf-report.md`.

**Acceptance criteria:**
- [ ] Curvas RPS/latencia documentadas con los percentiles pedidos.
- [ ] La backpressure se sostiene (503 ante saturación, sin degradar otros requests).
- [ ] RSS acotado durante el soak (no hay fuga de buffers/workers).

**Verification:**
- [ ] El harness corre en CI de forma acotada (pequeño) y manualmente con carga alta.
- [ ] `uv run ruff check .` en limpio.

**Dependencies:** Task 9

**Files likely touched:**
- `tests/extractor/load/loadgen.py`, `tests/extractor/load/test_soak.py` (opcional en CI)
- `docs/perf-report.md`

**Estimated scope:** Medium (2-3 archivos).

---

### Task 13: Profiling y tuning final

**Description:** CPU profiling (`py-spy`/`scalene`/`cProfile`) y memoria (`tracemalloc`); comparación empírica process-pool vs threads vs multi-worker uvicorn (resuelve Pregunta Abierta 2 del plan); tuning de `workers`, tamaño de buffer, chunk y concurrencia. Dejar config final en el código y sumario + decisiones en `docs/perf-report.md`.

**Acceptance criteria:**
- [ ] Reporte con mediciones y la decisión documentada sobre el modelo de workers.
- [ ] Config final calibrada (con justificación numérica).
- [ ] Checklist de éxito del plan § 1-9 completo y marcado.

**Verification:**
- [ ] `docs/perf-report.md` completo.
- [ ] `uv run pytest` (todo el repo) verde; `uv run ruff check .` / `uv run mypy -p pdfextractor` en limpio.

**Dependencies:** Tasks 11, 12

**Files likely touched:**
- `src/pdfextractor/infrastructure/concurrency/pool.py` (tuning)
- `documents/docs/perf-report.md`

**Estimated scope:** Medium (2-3 archivos + reporte).

---

### Checkpoint D (final)
- [ ] SLOs definidos y cumplidos (overhead no-parse p99 < 1 ms en HW de referencia).
- [ ] Reporte de profiling y configuración final.
- [ ] Suite completa del repo verde (orquestador + extractor).
- [ ] Revisión final con humano.

---

## Notas de paralelización

- **Paralelo:** T3 ∥ T2 (tras T1). T10 puede comenzar tras T8 sin esperar T9.
- **Secuencial (no reordenar):** cadena de contrato T2→T4→T5→T6→T7 (parseo → servicio → API → integración) y el orden de checkpoints.
- **No tocar:** `src/paperextractor/**`, `tests/**` del orquestador, `docs/tasks/plan.md` y `docs/tasks/todo.md` (plan del orquestador, completo).

## Nuevos directorios de tests

- `tests/extractor/test_*.py` — unit (reader, pool, extractor, servicio).
- `tests/extractor/integration/test_*.py` — endpoints, middlewares, concurrency, telemetry.
- `tests/extractor/contract/test_orchestrator_contract.py` — cross-service con cliente real.
- `tests/extractor/benchmarks/` y `tests/extractor/load/` — Fase 4.