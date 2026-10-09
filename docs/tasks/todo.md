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

## Paso 5: Auditoría de deuda técnica, robustez y cumplimiento 12-Factor

Auditoría read-only sobre el estado commiteado en `199ef43`. Decisiones de alcance
acordadas con el humano: **Fases A + B + C completas**; se **mantiene `.env`** como
fuente de configuración (no se elimina); se autorizan cambios de comportamiento
visibles que aumenten la seguridad (fast-fail 503 antes de leer body, timeout único,
process pool con `spawn`). Flujo TDD estricto (rojo → verde) + pausa obligatoria tras
cada tarea.

Artefactos de referencia: `docs/report.md` (cifras 2026-10-09 sobre `:9000`),
`docs/api-contract.md`, `docs/integration.md`, `load-tests/run-9000/`.

### Fase A — Correctitud y anti-DoS

- [x] **TASK-12: Control de admisión ANTES de leer el body (fast-fail 503)**
      — Hoy el gate vive en `ProcessPoolTextExtractor.extract`
      (`infrastructure/concurrency/pool.py:119`) y solo se alcanza **después** de
      `read_multipart_file`. Cuando todos los buffers del pool están prestados,
      `BufferPool.acquire()` devuelve `None` y `multipart_reader.py:59` crea un
      `bytearray()` ad-hoc **sin cota** que nunca vuelve al pool: bajo overload cada
      request encolado bufferea igual su upload completo (causa raíz de los 504s del
      run Vegeta B y del crecimiento de memoria). Mover la admisión/pool-acquire al
      inicio de la ruta, antes de consumir `request.stream()`.
  - **AC-12.1:** con el pool saturado, un upload completo se rechaza `503` **antes**
    de bufferear el cuerpo (no se consumen bytes del stream).
  - **AC-12.2:** la ruta nunca usa un buffer no-pooled: `grep -n "bytearray()"` en el
    camino de request es vacío (el fallback ad-hoc de `read_multipart_file` deja de
    utilizarse desde la ruta).
  - **AC-12.3:** test nuevo: N+1 uploads concurrentes con capacidad de pool N →
    `inflight <= N`, respuestas `503` acotadas, sin crecimiento del número de
    buffers vivos.
  - **AC-12.4:** contrato (`docs/api-contract.md`) sigue: `503 {"error":"overloaded"}`.
  - **Completado:** nuevo `infrastructure/concurrency/admission.py` (`AdmissionGate`,
    `asyncio.Semaphore` + cola con timeout + contadores `inflight`/`waiting`/
    `saturated`/`idle`); el lifespan lo instala en `app.state.admission` y la ruta
    `extract.py` lo reserva **antes** de `read_multipart_file`, marca
    `ready_state.mark_overloaded()` al saturarse/agotar la cola y `mark_ready()` al
    quedar `idle`. El buffer pooled se adquiere solo tras la admisión (nunca `None`
    hacia el reader). Tests: `test_admission_gate.py` (unidad) + `integration/
    test_admission.py` (sin framing del body rechazado; capacidad nunca excedida).
    Verificación: **105 passed / 2 skipped**, `-m memory` **2 passed**, ruff +
    ruff format + mypy strict verdes. Nota: el `_Gate` interno del pool permanece
    como guardia secundario; su consolidación es TASK-13/14.

- [x] **TASK-13: Timeout único; liberar buffer/permiso solo cuando el trabajo termina**
      — `extract.py:49-65` envuelve `asyncio.to_thread(service.extract, buffer)` en
      `asyncio.wait_for`; al vencer, `wait_for` cancela solo el wrapper asyncio pero el
      thread de `to_thread` **sigue corriendo** y el `finally` devuelve el `sink` al
      pool y lo limpia → otra request reutiliza el mismo `bytearray` mientras el worker
      lo lee (corrupción cruzada). Igual patrón en `pool.py:127,133`: `future.cancel()`
      no detiene al proceso y `_gate.release()` corre en `finally` (permiso liberado
      con trabajo en vuelo). Además hay **doble timeout** compitiendo (outer 30 s vs
      `future.result(timeout=...)` 30 s, `pool.py:125`).
  - **AC-13.1:** un timeout de extracción **nunca** permite que otra request observe
    el mismo buffer pooled (test de regresión con extractor fake lento).
  - **AC-13.2:** un único presupuesto de timeout gobierna la extracción (se elimina el
    `wait_for` externo; el pool es la única autoridad).
  - **AC-13.3:** el permiso del gate se libera solo cuando el trabajo realmente
    terminó; `inflight()` refleja trabajo vivo, no solo requests esperando.
  - **AC-13.4:** timeout sigue mapeando a `504 {"error":"timeout"}`.
  - **Completado:** la ruta `extract.py` ya no envuelve la extracción en
    `asyncio.wait_for` (se elimina el import de `ExtractionTimeoutError`): solo
    `await asyncio.to_thread(service.extract, buffer)`, así el pool es la única
    autoridad del timeout y el buffer no se libera con trabajo en vuelo. En
    `pool.py` el `except TimeoutError` marca `release_deferred`, cancela el future y
    difiere la liberación vía `add_done_callback(self._release_when_done)`; el
    `finally` libera solo si no se difirió y se extraen los helpers `_release_slot`
    (permiso + `mark_ready()` cuando `inflight()==0`) y `_release_when_done`.
    Tests: `test_concurrency.py::test_a_timed_out_extraction_keeps_its_slot_until_the_worker_finishes`
    (tras el timeout `inflight()==1` y una segunda llamada da `OverloadError`; al
    terminar el job se recupera) y nuevo `integration/test_extraction_timeout.py`
    (el buffer pooled se libera solo al terminar la extracción). Se reescribió el
    504 de `test_middlewares.py` para usar el pool real con un job lento de módulo.
    Verificación: **107 passed / 2 skipped**, `-m memory` **2 passed**, ruff +
    ruff format + mypy strict verdes.

- [x] **TASK-14: Reemplazar `_Gate` por `threading.BoundedSemaphore`**
      — `_Gate.acquire` (`pool.py:60-74`) despierta a un waiter y hace `_in_use += 1`
      **sin re-validar el predicado**: un caller nuevo puede tomar el fast-path
      (`in_use: 0→1`) entre `release()` y el re-lock del waiter, y el waiter luego deja
      `in_use = 2` con `permits = 1` → más extracciones en vuelo que el tope, cola
      ilimitada del `ProcessPoolExecutor`, backpressure anulada.
  - **AC-14.1:** bajo stress de contención (fast-path + waiter despertado), la
    concurrencia **nunca** supera `permits` (test determinista con barreras).
  - **AC-14.2:** se preservan `inflight()`, `queue_depth()`, `mark_overloaded/ready` y
    el comportamiento de `/ready` (`200`/`503`).
  - **AC-14.3:** se mantiene `on_wait` para marcar overload al entrar en cola.
  - **Completado:** `_Gate` se reimplementa sobre `threading.BoundedSemaphore`
    (la única autoridad del tope: el waiter debe re-adquirir el semáforo, así el
    fast-path no puede sobrepasar `permits`). Se conservan los contadores
    `_in_use`/`_waiting` (para `inflight()`/`queue_depth()`) y `on_wait` (se dispara
    solo cuando un caller tiene que encolar). Test determinista nuevo
    `tests/extractor/test_gate.py`: reproduce el interleave release→fast-path→waiter
    (rojo con `overshoot=2`, verde con el semáforo) y cubre `on_wait`/reset a idle.
    Verificación: **110 passed / 2 skipped**, `-m memory` **2 passed**, ruff +
    ruff format + mypy strict verdes.

- [x] **TASK-15: Acotar el buffer de preámbulo/headers del multipart reader**
      — `_read_headers` (`multipart_reader.py:139-150`) no poda ni tiene tope: un bloque
      de headers de parte que nunca termina (`\r\n\r\n` ausente) hace crecer `_buf` sin
      límite (DoS). (`_seek_boundary` y `_skip_part` sí podan; `_read_file` está
      acotado por `max_bytes`.)
  - **AC-15.1:** un bloque de headers sin terminador lanza `MalformedMultipartError`
    al superar un tope fijo, en vez de crecer en memoria.
  - **AC-15.2:** test que alimenta un preámbulo/headers ilimitado y verifica que
    `len(_buf)` queda acotado y que se lanza el error esperado.
  - **AC-15.3:** lectura legítima (checksum + file) intacta; suite verde.
  - **Completado:** nuevo tope `_MAX_PART_HEADER_BYTES = 16 KiB` en
    `_read_headers`: si no hay terminador y `_buf` lo supera, o el terminador cae
    más allá del tope, lanza `MalformedMultipartError` (422). Tests:
    `test_endless_part_headers_are_rejected_without_unbounded_growth` (unidad, verifica
    `len(_buf) < 64 KiB` y el error) y
    `test_a_stream_with_never_ending_headers_is_aborted_early` (API pública: aborta
    tras pocos bloques, `consumed < 5000`). Verificación: **112 passed / 2 skipped**,
    `-m memory` **2 passed**, ruff + ruff format + mypy strict verdes.

### Fase B — Cumplimiento 12-Factor y dialecto de errores

- [x] **TASK-16: Logs a stdout + traceback estructurado + `PYTHONUNBUFFERED`**
      — Los logs JSON van a **stderr** (`logging_.py:57`); el handler catch-all
      (`handlers.py:24-27`) devuelve `{"error":"internal"}` y registra solo el *tipo*
      de excepción, sin `exc_info` por el logger estructurado (los tracebacks quedan
      en el logger plano de uvicorn). El `Dockerfile` no define `PYTHONUNBUFFERED`.
  - **AC-16.1:** `configure_logging` emite a **stdout** por defecto.
  - **AC-16.2:** un error no esperado produce **una** línea JSON en stdout con el
    traceback (`exc_info`), sin filtrar detalles al cliente (el cuerpo sigue
    `500 {"error":"internal"}`).
  - **AC-16.3:** `Dockerfile` incluye `PYTHONUNBUFFERED=1`.
  - **AC-16.4:** test captura la línea estructurada del 500.
  - **Completado:** `configure_logging` usa `sys.stdout` por defecto; `JsonFormatter`
    añade el campo `exception` cuando hay `record.exc_info`; el handler catch-all
    (`handlers.py`) ahora emite `_LOGGER.error("unhandled_exception", exc_info=(...),
    extra={request_id, error_type})` (una sola línea Error con el traceback, el cliente
    sigue viendo `500 {"error":"internal"}`); `Dockerfile` agrega
    `PYTHONUNBUFFERED=1`. Tests nuevos `integration/test_error_logging.py`
    (línea estructurada del 500 con `Traceback` y sin filtrar `secret`; `stdout`
    por defecto). Verificación: **114 passed / 2 skipped**, `-m memory` **2 passed**,
    ruff + ruff format + mypy strict verdes.

- [x] **TASK-17: Endurecer configuración (manteniendo `.env`)**
      — `Settings` (`settings.py:17-33`) carga `.env`, pero: no tiene validadores;
      `host`/`port` están definidos y **nunca se usan** (uvicorn CLI fija `--port`);
      `.env.example` trae `PDFEXTRACTOR_WORKERS=` y
      `PDFEXTRACTOR_MAX_CONCURRENT_EXTRACTIONS=` vacíos sobre campos `int | None`
      (probable `ValidationError` al copiar el ejemplo). Añadir `env_ignore_empty=True`,
      validadores de rango y hacer que `PDFEXTRACTOR_HOST`/`PDFEXTRACTOR_PORT` gobiernen
      el arranque (entrypoint/CMD) o eliminarlos si no aplican.
  - **AC-17.1:** copiar `.env.example` → `.env` arranca sin error (vacíos → `None`).
  - **AC-17.2:** valores inválidos (`max_upload_bytes <= 0`, timeout `<= 0`,
    `workers < 1`, `log_level` inválido) fallan con mensaje claro.
  - **AC-17.3:** `PDFEXTRACTOR_HOST`/`PDFEXTRACTOR_PORT` tienen efecto real en el
    arranque (o se eliminan y se actualiza `.env.example`/`api-contract.md`).
  - **AC-17.4:** tests de settings (válidos e inválidos) verdes.
  - **Completado:** `Settings` con `env_ignore_empty=True` y validadores
    (`port` 1–65535, `max_upload_bytes>0`, `min_text_length>=0`,
    `workers`/`max_concurrent`>=1 si están set, timeouts>0, `log_level` en el set y
    normalizado a mayúsculas). Nuevo entrypoint `src/pdfextractor/__main__.py`
    (`uvicorn.run("pdfextractor.main:app", host=settings.host, port=settings.port)`)
    y `Dockerfile` `CMD ["python","-m","pdfextractor"]`, así `PDFEXTRACTOR_HOST/PORT`
    gobiernan el arranque. `.env.example` movió los comentarios de las vars vacías a
    su propia línea (dotenv tomaba el comentario como valor). Tests nuevos en
    `test_extractor_settings.py` (carga de `.env.example`, 9 casos inválidos, binding
    host/port del entrypoint). Verificación: **125 passed / 2 skipped**, `-m memory`
    **2 passed**, ruff + ruff format + mypy strict verdes.

- [x] **TASK-18: Unificar el dialecto de error a `{"error": ...}`**
      — Solo `PdfExtractorError` mapea a `{"error"}`. FastAPI/Starlette siguen
      respondiendo 404/405/422 con `{"detail": ...}`, contradiciendo
      `api-contract.md:8,88-100`.
  - **AC-18.1:** 404, 405 y 422 responden con una sola clave `{"error": ...}`.
  - **AC-18.2:** se añaden handlers para `RequestValidationError` y
    `StarletteHTTPException` (sin romper `PdfExtractorError` ni el 500 genérico).
  - **AC-18.3:** test de contrato ampliado a 404/405/422; tabla de
    `docs/api-contract.md` coherente.
  - **Completado:** nuevos handlers `RequestValidationError` (422
    `{"error":"solicitud inválida"}`) y `StarletteHTTPException` (mapa 404→`no
    encontrado`, 405→`método no permitido`, 422→`solicitud inválida`, fallback al
    `detail`) en `handlers.py`; `PdfExtractorError` y el catch-all 500 intactos.
    Test nuevo `integration/test_error_dialect.py` cubre 404/405/422 con una única
    clave. `docs/api-contract.md` amplía la tabla de errores. Verificación:
    **128 passed / 2 skipped**, `-m memory` **2 passed**, ruff + ruff format + mypy
    strict verdes.

### Fase C — Robustez, límites, observabilidad y limpieza

- [x] **TASK-19: Process pool con `spawn`/`forkserver`**
      — `ProcessPoolExecutor` (`pool.py:109,150`) usa **fork** por defecto en Linux
      con un servidor multihilo → riesgo de fork no seguro.
  - **AC-19.1:** el pool crea workers con `mp_context=get_context("spawn")` (o
    `forkserver`), con guard de importación adecuado.
  - **AC-19.2:** extracción real y `_restart_pool()` siguen funcionando; suite verde
    con el nuevo start method.
  - **Nota:** se adoptó `forkserver` (más barato que `spawn` en hosts lentos). Los
    jobs de workers de las tests se movieron a `tests/extractor/integration/_workers.py`
    (módulo sin dependencias) porque re-importar los módulos de test en el worker
    costaba ~12s en WSL2; `test_concurrency.py` añadió `test_the_pool_starts_workers_in_a_safe_context_not_fork`
    (asserts `method == "forkserver"` también tras `_restart_pool()`). Gates:
    129 passed/2 skipped, memory 2, ruff/format/mypy verdes.

- [x] **TASK-20: Cota de tamaño de salida y de páginas (anti decompression-bomb)**
      — Hoy no hay tope al `extracted_text` ni al `page_count`: un PDF malicioso puede
      expandir memoria. Añadir settings (`max_extracted_chars`, `max_pages`) y
      aplicarlos en el servicio de aplicación.
  - **AC-20.1:** salida/páginas por encima del tope → error de dominio acotado
    (422), sin OOM.
  - **AC-20.2:** tests con salida/páginas sintéticas grandes verifican el corte.
  - **AC-20.3:** settings documentados en `.env.example` y `api-contract.md`.
  - **Nota:** `Settings` ganó `max_pages=1000` y `max_extracted_chars=5_000_000`
    (validados `> 0`); `ExtractionService` recibe caps opcionales (None = sin tope)
    y verifica `page_count` primero y luego `len(text)` en bruto **antes** de
    normalizar (cota de memoria real). Nuevos errores 422 `ExcessivePagesError`
    («demasiadas páginas») y `ExcessiveTextError` («texto excesivo»). Tests:
    7 en `test_extraction_service.py`, 2 de settings, 3 de integración en
    `integration/test_output_limits.py`. Gates: 141 passed/2 skipped, memory 2,
    ruff/format/mypy verdes.

- [x] **TASK-21: Métricas — collectors de proceso/GC y render no bloqueante**
      — El registry privado (`metrics.py:74`) omite collectors de proceso/GC;
    `render()` corre síncrono dentro de un handler async (`metrics.py:61-69`).
  - **AC-21.1:** `/metrics` expone series de proceso/GC (Process/Platform/GC
    collectors ligados al registry privado).
  - **AC-21.2:** el handler de `/metrics` no bloquea el event loop (render vía
    `to_thread`/threadpool).
  - **AC-21.3:** tests de métricas existentes siguen verdes.
  - **Nota:** `create_metrics()` registra `PROCESS_COLLECTOR`, `PLATFORM_COLLECTOR`
    y `GC_COLLECTOR` en el registry privado (cada registro en un registry nuevo,
    sin duplicados); el handler `/metrics` resuelve `content` vía
    `fastapi.concurrency.run_in_threadpool(bundle.render, extractor)`. Dos tests
    nuevos en `integration/test_telemetry.py` (`process_cpu_seconds_total` /
    `python_gc_objects_collected_total` presentes; render ejecutado por
    `run_in_threadpool`). Gates: 143 passed/2 skipped, memory 2, ruff/format/mypy
    verdes.

- [x] **TASK-22: Eliminar código muerto y referencias obsoletas**
      — Un símbolo muerto en el adaptador de extracción, docstrings de código con
    rutas de plan ya renombradas y una referencia a un informe de rendimiento
    inexistente.
  - **AC-22.1:** `grep` del símbolo muerto y de las refs obsoletas es vacío.
  - **Nota:** eliminado el símbolo muerto del adaptador (y el import de la
    interface que dejaba huérfano); docstrings de `settings.py`, `logging_.py`,
    `metrics.py`, `errors.py`, `memory/pool.py`, `presentation/api/v1/metrics.py`
    y el comentario de `tests/extractor/load/loadgen.js` re-apuntan a
    `docs/tasks/plan.md` (plan vigente). `src/` y `tests/` quedan sin referencias
    a símbolos muertos ni rutas de docs inexistentes; los registros históricos
    (audit `docs/optimization-report.md` y `docs/tasks/archive/`) retienen su
    narración original. Gates: 143 passed/2 skipped, memory 2, ruff/format/mypy
    verdes.
  - **AC-22.2:** ruff + mypy verdes; suite verde.

- [x] **TASK-23: Gates de CI (memory, vulnerabilidades, build de imagen)**
      — `.github/workflows/ci.yml` no ejecuta `-m memory`, ni escaneo de
    vulnerabilidades, ni build de imagen.
  - **AC-23.1:** CI corre `pytest -m memory`.
  - **AC-23.2:** CI ejecuta escaneo de dependencias (p. ej. `uvx pip-audit`).
  - **AC-23.3:** CI construye la imagen Docker.
  - **AC-23.4:** YAML válido; pasos reproducibles localmente y documentados.
  - **Nota:** `.github/workflows/ci.yml` gana tres pasos tras «Test»: `pytest -m
    memory -q`, `uv run --with pip-audit pip-audit` (escanea el entorno del
    proyecto, no un venv aislado) y `docker build -t pdfextractor:ci .`. YAML
    validado con PyYAML; todos los pasos reproducidos localmente (pip-audit sin
    vulnerabilidades conocidas, build OK) y documentados en README bajo «CI
    (reproducible local)».

- [x] **TASK-24: Documentar oversubscription de `uvicorn --workers N`**
      — Con `--workers N`, cada worker crea su propio process pool de `workers` = CPU
      → `N × CPU` procesos (footgun de rendimiento/memoria).
  - **AC-24.1:** README/notas de operador documentan el efecto y una configuración
    recomendada (p. ej. `workers` del pool acotado en despliegues multi-worker).
  - **AC-24.2:** sin cambios de código silenciosos; la decisión queda explícita.
  - **Nota:** README (Despliegue) documenta el efecto (`N × workers_del_pool`
    procesos de extracción) y dos configuraciones recomendadas: un solo worker
    uvicorn con pool = CPUs, o `--workers N` con `PDFEXTRACTOR_WORKERS=1` y
    escalado por réplicas. Sin cambios de código.

### Checkpoint CP-5: Cierre de auditoría
- [x] Suite completa + `-m memory` + ruff + mypy verdes tras TASK-12..24
      (143 passed/2 skipped + 2 memory; ruff/format/mypy verdes)
- [ ] `docs/report.md` actualizado con el impacto de los cambios de comportamiento
      (fast-fail antes de leer body, timeout único, pool con `spawn`) y nueva corrida
      de carga sobre `:9000`
      - Impacto documentado en `docs/report.md` (§ 9).
      - Pendiente de humano: re-ejecutar la carga de § 7 sobre `:9000`.
- [ ] Revisión con humano (TASK-12..24 aprobadas una a una)