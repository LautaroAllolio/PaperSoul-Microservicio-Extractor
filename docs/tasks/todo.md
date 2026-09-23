# Tasks — BigPickle (Orquestador PaperSoul)

Task list operativo de la implementación de BigPickle. Detalle técnico en `tasks/plan.md`; contrato y especificación en `SPEC-bigpickle.md`. SDD Fase 1 y 2 completas — pendiente de aprobación humana antes de implementar.

---

## Fase A: Fundación

### Task 1: Scaffold del proyecto con uv y esqueleto en 3 capas

**Description:** Crear el proyecto Python 3.12 con `uv` (`pyproject.toml`, `.python-version`, `.venv`), el paquete `src/bigpickle/` con los directorios `presentation/`, `application/`, `infrastructure/` vacíos, `main.py` con app factory mínima, y `infrastructure/config/settings.py` (pydantic-settings, prefijo `BIGPICKLE_`) con todas las variables de la sección 7 del plan.

**Acceptance criteria:**
- [ ] `uv sync` instala sin errores; `bigpickle` es importable desde `src/` (editable).
- [ ] `main.py` levanta una FastAPI app vacía con `GET /` de prueba respondiendo `200`.
- [ ] `Settings` carga desde entorno con defaults correctos (url del Extractor, límites de tamaño/timeout, pool).
- [ ] `uv run mypy bigpickle` y `uv run ruff check .` pasan en limpio.

**Verification:**
- [ ] Tests pass: `uv run pytest` (test básico de arranque de app/settings).
- [ ] Build succeeds: `uv run uvicorn bigpickle.main:app --app-dir src` inicia y responde.
- [ ] Manual check: `python -c "from bigpickle.main import create_app"` sin errores.

**Dependencies:** None

**Files likely touched:**
- `pyproject.toml`, `.python-version`, `.env.example`
- `src/bigpickle/__init__.py`, `src/bigpickle/main.py`
- `src/bigpickle/config/settings.py` (+ `__init__`s de capas)
- `tests/unit/test_settings.py`, `tests/conftest.py`

**Estimated scope:** Medium (5-6 archivos).

---

### Task 2: Schemas Pydantic y contrato RFC 9457

**Description:** Implementar los DTOs de salida (`ExtractedDocument`, `OrchestrationMetadata`, `DocumentExtractResponse`, `HealthResponse`, `ReadinessResponse`) en `presentation/schemas/` y el modelo `ProblemDetails`/`ProblemDetailError`. Implementar los 4 handlers globales de error en `presentation/errors/handlers.py` que traducen a `application/problem+json`: validation (Pydantic), `HTTPException`, excepciones de dominio, y excepción genérica — todos con el sufijo `type` del mapa §8.1.

**Acceptance criteria:**
- [ ] Schemas validan/duplican exactamente los contratos JSON de la SPEC §6-§7 (tests de serialización).
- [ ] `RequestValidationError` → `422 validation-error` con array `errors` (loc/msg/type).
- [ ] `HTTPException` y excepción genérica → `problem+json` con `status` correcto.
- [ ] Handlers mapean cada excepción de dominio (aún sin implementar) desde `status`+`problem_type` que expone la excepción.

**Verification:**
- [ ] Tests pass: `uv run pytest tests/unit/test_schemas.py tests/unit/test_problems.py`.
- [ ] Manual check: POST inválido a la app devuelve body RFC 9457 completo.
- [ ] `uv run mypy bigpickle` y `uv run ruff check .` en limpio.

**Dependencies:** Task 1

**Files likely touched:**
- `src/bigpickle/presentation/schemas/{document,health,problems}.py`
- `src/bigpickle/presentation/errors/handlers.py`
- `src/bigpickle/application/errors.py` (definir jerarquía de excepciones de dominio, base)
- `tests/unit/test_schemas.py`, `tests/unit/test_problems.py`

**Estimated scope:** Medium (4-5 archivos).

### Checkpoint A (tras Tasks 1-2)
- [ ] `uv sync` limpio; schemas compilan y serializan según SPEC.
- [ ] Mapa RFC 9457 cubierto por tests de handler.
- [ ] Review con humano antes de seguir.

---

## Fase B: Núcleo de streaming y cliente downstream

### Task 3: Streaming sin disco (`SourceForwardingStream` + guard de tamaño)

**Description:** Implementar `infrastructure/http/streaming.py`: el adapter `SourceForwardingStream(httpx.AsyncByteStream)` que itera una `AsyncByteSource` en chunks de 64 KB, expone `get_content_length()` (devuelto desde el `Content-Length` entrante o `None` → chunked), y aborta con `PayloadTooLargeError` al superar `BIGPICKLE_MAX_UPLOAD_BYTES`, sin buffering del archivo. Escribir primero la prueba de humo R1: verificar de forma empírica que httpx envía async byte streams con length correcto.

**Acceptance criteria:**
- [ ] El iterador emite exactamente los bytes recibidos, en orden, sin duplicaciones ni pérdidas (test byte-identical unitario).
- [ ] `get_content_length()` devuelve el length entrante o `None`.
- [ ] Guard: al exceder el límite se eleva `PayloadTooLargeError` durante la iteración (test con fuente de > límite).
- [ ] No se escribe a disco en ningún punto (test de monkeypatch de ruta de escritura).

**Verification:**
- [ ] Tests pass: `uv run pytest tests/unit/test_streaming.py`.
- [ ] Manual/R1: prueba de humo con `httpx.AsyncClient` contra `ASGITransport` verificando un request body streamed con y sin `Content-Length`.
- [ ] `uv run mypy bigpickle` y `uv run ruff check .` en limpio.

**Dependencies:** Task 1

**Files likely touched:**
- `src/bigpickle/infrastructure/http/streaming.py`
- `src/bigpickle/application/interfaces.py` (`AsyncByteSource`)
- `src/bigpickle/application/errors.py` (`PayloadTooLargeError`)
- `tests/unit/test_streaming.py`

**Estimated scope:** Medium (2-3 archivos). Alto riesgo → temprano (R1/R2).

---

### Task 4: `HttpExtractorClient` (httpx) y traducción de errores downstream

**Description:** Implementar `infrastructure/http/downstream/`: `base.py` (ABC `ExtractorClient` con `forward()` y `ping()`), `models.py` (records crudos `ExtractorSuccess`, `ExtractorError` con `error: str`), y `http_client.py` con la implementación real: `AsyncClient` con pool y timeouts por fase desde settings; `forward()` emite `POST {BASE}/api/v1/extract` con `content=SourceForwardingStream` y headers `Content-Type`, `Content-Length` (si hay), `X-Request-Id`; traduce respuestas del Extractor a excepciones de dominio (422→`ExtractionFailedError`, 5xx→`UpstreamError`, timeout→`UpstreamTimeoutError`, refused/DNS→`UpstreamUnavailableError`, respuestas con shape inesperado→`UpstreamError`). `ping()` para readiness.

**Acceptance criteria:**
- [ ] `forward()` envía exactamente el source dado y devuelve `ExtractorSuccess` en `200`.
- [ ] Cada error downstream (422/500/timeout/refused/shape inválido) produce la excepción de dominio correcta (tabla §9.3 SPEC).
- [ ] El `detail` propagado usa solo el mensaje `{error}` del Extractor; nunca loguea ni reenvía el cuerpo completo a cliente.
- [ ] `ping()` lanza `UpstreamUnavailableError` solo en fallas de red; cualquier status HTTP = reachable.

**Verification:**
- [ ] Tests pass: `uv run pytest tests/unit` (incl. tests nuevos del cliente con `respx`).
- [ ] `uv run mypy bigpickle` y `uv run ruff check .` en limpio.

**Dependencies:** Task 1, Task 3

**Files likely touched:**
- `src/bigpickle/infrastructure/http/downstream/{base,models,http_client}.py`
- `src/bigpickle/application/errors.py` (excepciones de dominio completas)
- `src/bigpickle/infrastructure/config/settings.py` (timeouts/pool)
- `tests/unit/test_http_client.py`

**Estimated scope:** Medium (4-5 archivos).

---

### Task 5: `ExtractionOrchestrator` (interfaz + implementación)

**Description:** Definir `ExtractionService` (Protocol) en `application/interfaces.py` e implementar `ExtractionOrchestrator` en `application/services/orchestrator.py`: generar `request_id`, medir `duration_ms`, construir `SourceForwardingStream` sobre la fuente recibida, llamar al `ExtractorClient.forward()` inyectado, y devolver `ExtractionResult` (`extracted_text`, `page_count`, `extraction_method`, `duration_ms`). No conoce HTTP ni DTOs de presentación.

**Acceptance criteria:**
- [ ] Orquesta correctamente usando el cliente inyectado (fake en tests); produce `ExtractionResult` completo.
- [ ] Propaga las excepciones de dominio sin transformarlas (las traduce la presentación).
- [ ] Registra `duration_ms` ≥ 0; `request_id` único por llamada.
- [ ] Inyectable: el mismo código funciona con fake y con `HttpExtractorClient` real.

**Verification:**
- [ ] Tests pass: `uv run pytest tests/unit/test_orchestrator.py`.
- [ ] `uv run mypy bigpickle` y `uv run ruff check .` en limpio.

**Dependencies:** Task 3, Task 4

**Files likely touched:**
- `src/bigpickle/application/interfaces.py`
- `src/bigpickle/application/services/orchestrator.py`
- `src/bigpickle/infrastructure/tracing.py`
- `tests/unit/test_orchestrator.py`

**Estimated scope:** Medium (3 archivos).

### Checkpoint B (tras Tasks 3-5)
- [ ] Streaming con guard verificado por tests; Prueba de humo R1 resuelta.
- [ ] Cliente traduce todos los errores del Extractor a excepciones de dominio.
- [ ] Orquestador produce `ExtractionResult` end-to-end con fake.
- [ ] Review con humano del diseño de streaming antes de exponer endpoints.

---

## Fase C: Endpoints

### Task 6: Endpoint `POST /api/v1/extract` end-to-end

**Description:** Implementar `presentation/api/v1/extract.py` (router con `Request` directo, sin `UploadFile` — D1), `presentation/api/deps.py` (DI de `ExtractionService`+settings), montar el router en `main.py`, construir el `AsyncByteSource` sobre `request.stream()`, extraer metadatos del primer chunk multipart (preamanálisis ligero; fallback `""`), y cablear la respuesta envelope `200` o los handlers RFC 9457. Test de integración completo con `ASGITransport` + `respx` mock del Extractor en `tests/integration/test_extract_flow.py`: happy path byte-identical + caso de corte a mitad de stream (R3).

**Acceptance criteria:**
- [ ] POST multipart real (campo `file`) → `200` con envelope exacto de la SPEC §6.1.
- [ ] El body que recibe el Extractor mock es byte-identical al enviado (test).
- [ ] Corte a mitad de stream → httpx cierra la conexión; respuesta de error controlada (no 200 parcial).
- [ ] Request inválido (sin multipart/sin boundary) → `422 invalid-request`; exceso de tamaño → `413 payload-too-large`.
- [ ] Loguear `request_id` en entrada y salida.

**Verification:**
- [ ] Tests pass: `uv run pytest tests/integration/test_extract_flow.py`.
- [ ] Manual check: `curl -F "file=@sample.pdf" localhost:8000/api/v1/extract` → envelope.
- [ ] `uv run mypy bigpickle` y `uv run ruff check .` en limpio.

**Dependencies:** Task 2, Task 5

**Files likely touched:**
- `src/bigpickle/presentation/api/v1/extract.py`, `src/bigpickle/presentation/api/deps.py`
- `src/bigpickle/main.py`
- `src/bigpickle/infrastructure/http/multipart.py` (preamanálisis ligero de metadatos)
- `tests/integration/test_extract_flow.py`

**Estimated scope:** Medium (4-5 archivos).

---

### Task 7: Endpoints `GET /health` y `GET /ready`

**Description:** Implementar `presentation/api/v1/health.py`: `/health` (liveness, sin dependencias) y `/ready` (readiness, llama a `ExtractorClient.ping()` inyectado). `/ready` responde `200` con `downstream: {extractor: reachable}` o `503` + Problem Details `upstream-unavailable`.

**Acceptance criteria:**
- [ ] `/health` responde `200` con `status:"ok"`, service, version y timestamp UTC (sin tocar red).
- [ ] `/ready` con Extractor mockeado reachable → `200`; caído (respx desconexión) → `503` problem+json.
- [ ] Tests en `tests/integration/test_health.py`.

**Verification:**
- [ ] Tests pass: `uv run pytest tests/integration/test_health.py`.
- [ ] `uv run mypy bigpickle` y `uv run ruff check .` en limpio.

**Dependencies:** Task 4

**Files likely touched:**
- `src/bigpickle/presentation/api/v1/health.py`, `src/bigpickle/presentation/api/deps.py`
- `src/bigpickle/main.py`
- `tests/integration/test_health.py`

**Estimated scope:** Small (3 archivos).

### Checkpoint C (tras Tasks 6-7)
- [ ] Extracción end-to-end con Extractor mockeado funciona (byte-identical).
- [ ] `/health` y `/ready` verificados; `/ready` degrada correctamente.
- [ ] Review con humano antes de pulir.

---

## Fase D: Suite completa, CI y documentación

### Task 8: Suite completa de tests + CI

**Description:** Completar la cobertura: `tests/integration/test_error_mapping.py` (los 9 casos del mapa §8.1 con body `problem+json` exacto), `tests/integration/test_no_disk.py` (monkeypatch de `tempfile`/rutas temporales verificando que no se escribe contenido del archivo), y `tests/contract/test_extractor_contract.py` (marcador `-m contract`, requiere downstream real). Agregar `.github/workflows/ci.yml` (ruff, mypy, pytest, pytest -m contract opcional por input).

**Acceptance criteria:**
- [ ] Los 9 casos de error producen el `type`/`status`/`title` esperado (tests de mapping).
- [ ] `test_no_disk` pasa: cero escritura de contenido de documento durante una operación real.
- [ ] CI corre ruff + mypy + pytest en cada push (workflow verde).
- [ ] Contract test corre de forma opcional y documentada.

**Verification:**
- [ ] Tests pass: `uv run pytest` completo (sin `-m contract` por defecto).
- [ ] Manual check: ci.yml validado (runde localmente con `act` si disponible, o push de prueba).
- [ ] `uv run mypy bigpickle` y `uv run ruff check .` en limpio.

**Dependencies:** Task 6, Task 7

**Files likely touched:**
- `tests/integration/test_error_mapping.py`, `tests/integration/test_no_disk.py`
- `tests/contract/test_extractor_contract.py`, `tests/conftest.py`
- `.github/workflows/ci.yml`, `pyproject.toml` (pytest options/markers)

**Estimated scope:** Large (5-8) → se ejecuta en dos sub-entregas (a: CI + tests de error/no-disk; b: contract + ajustes finales).

---

### Task 9: README, `.env.example` y limpieza final

**Description:** Reescribir `README.md` como guía de BigPickle (qué es, cómo correr, contrato, curl de ejemplo, tabla de errores RFC 9457), finalizar `.env.example`, aplicar `ruff format`, y verificación final de mypy estricto y del checklist de éxito del SPEC §12.

**Acceptance criteria:**
- [ ] README documenta comandos, contrato público, flujo de streaming y despliegue (proxy: `proxy_request_buffering off`).
- [ ] `.env.example` refleja todas las variables de la sección 7 del plan.
- [ ] Todo el criterio de éxito del SPEC §12 está cumplido (checklist marcado).
- [ ] Mimetic: NPI de la documentación es coherente con lo implementado.

**Verification:**
- [ ] Tests pass: `uv run pytest` (verde completo, incl. `-m contract` si hay Extractor real).
- [ ] `uv run ruff check .` + `uv run ruff format --check .` + `uv run mypy bigpickle` en limpio.
- [ ] Manual check: flujo curl completo desde README funciona.

**Dependencies:** Task 8

**Files likely touched:**
- `README.md`, `.env.example`
- `tasks/plan.md` / `tasks/todo.md` (marcar tareas cumplidas)

**Estimated scope:** Small (2-3 archivos).

### Checkpoint D (final, tras Tasks 8-9)
- [ ] CI verde completo (ruff, mypy, pytest).
- [ ] Todos los criterios del SPEC §12 cumplidos.
- [ ] Revisión final con humano; lista para aprobar implementación de otras fases (auth, catálogo, métricas).

---

## Notas de paralelización

- **Paralelo:** T2 ∥ T3 (tras T1). T8b puede correr tras T8a sin bloquear extras.
- **Secuencial (no tocar):** dependencias de streaming (T3→T4→T5) y el orden de checkpoints.