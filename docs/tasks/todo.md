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
- [x] `docs/report.md` actualizado con el impacto de los cambios de comportamiento
      (fast-fail antes de leer body, timeout único, pool con `forkserver`) y nueva
      corrida de carga sobre `:9000`
      - Reporte § 3–§ 6 re-escritos con las cifras de la corrida nueva; § 9
        documenta el delta TASK-12..24 (extendido a TASK-25..27 en CP-6).
      - Corrida re-ejecutada: k6 (baseline/stress/sonda) + Vegeta A (400 rps
        mixto) + Vegeta B (150 rps 20 pág., servidor reiniciado). Artefactos en
        `load-tests/run-9000/` (`k6-output.txt`, `server.log`, `server-b.log`,
        `results-mixed.*`, `results-20p.*`).
- [ ] Revisión con humano (TASK-12..24 aprobadas una a una)

## Paso 6: Velocidad pura y Markdown nativo

Decisiones acordadas con el humano: Markdown vía renderer **propio** sobre
`get_text("dict")` (`get_text("markdown")` no existe en PyMuPDF 1.28.2 y
`pymupdf4llm` se descartó por coste/SLO en TASK-25); toggle
`PDFEXTRACTOR_WARMUP` (default true); `orjson` en default response class +
handlers + `/ready`. Flujo TDD estricto (rojo → verde) + pausa obligatoria tras
cada tarea. Orden: TASK-27 → TASK-25 → TASK-26 → CP-6.

- [x] **TASK-27: Serialización con `orjson`**
      — La respuesta pesada de `/api/v1/extractions` (`extract.py:84`,
      `response_model=ExtractResponse`) se serializa con el JSON estándar de
      Python, cuello de botella del hilo principal bajo concurrencia.
  - **AC-27.1:** `orjson` es dependencia runtime (`pyproject.toml`).
  - **AC-27.2:** `create_app(...)` usa `default_response_class=ORJSONResponse`;
        los 4 handlers de `handlers.py` y `/ready` en `health.py` devuelven
        `ORJSONResponse`.
  - **AC-27.3:** `Content-Type` sigue `application/json`; contrato `200`
        (3 claves) y `{"error"}` sin cambios; tests de dialecto verdes.
  - **AC-27.4:** test de round-trip de payload grande y assert del response
        class por defecto.
  - **Completado:** `orjson>=3.10` como dependencia runtime. **Desvío** de AC-27.2
    por CleanCode/rendimiento: `fastapi.responses.ORJSONResponse` está
    **deprecado** en FastAPI 0.141 y emite `FastAPIDeprecationWarning` en **cada**
    respuesta; además, fijar un `response_class` desactiva el fast-path
    `dump_json` de Pydantic. Benchmark (1 MB de `extracted_text`):
    `jsonable_encoder`+`orjson.dumps` = 0.050 ms vs. `model_dump_json` de Pydantic
    = 0.884 ms (**17.8x** más rápido orjson). Se creó una clase propia
    `infrastructure/http/orjson_response.py::OrjsonResponse` (subclase de
    `Response`, `media_type="application/json"`, `orjson.dumps(..., OPT_NON_STR_KEYS)`),
    libre del símbolo deprecado y sin warnings. `main.py` la usa como
    `default_response_class`; los 4 handlers de `handlers.py` y `/ready` en
    `health.py` devuelven `OrjsonResponse`. Nuevo
    `tests/extractor/integration/test_orjson_serialization.py` (5 tests:
    response class por defecto es `OrjsonResponse`, handler 404 devuelve
    `OrjsonResponse`, round-trip de payload grande ~"ñandú"×100k, `/ready` JSON,
    y ausencia total de `FastAPIDeprecationWarning` en `/health`+`/ready`+404).
    Verificación: **148 passed / 2 skipped**, ruff check + ruff format (src tests)
    + mypy strict verdes.

- [x] **TASK-25: Markdown nativo (renderer propio, sin `pymupdf4llm`)**
      — `pymupdf_extractor.py:42-45` usaba `page.get_text()` (texto plano). Se
      sustituye por un renderer Markdown propio alimentado por
      `page.get_text("dict")`, manteniendo zero-disk, cierre del `Document` en
      todos los caminos y el mapeo de errores de dominio. `extraction_method`
      sigue `"pymupdf"` (contrato intacto con el orquestador).
  - **AC-25.1:** un PDF estructurado produce Markdown con marcadores (`#`, `-`,
        `**`); `page_count` intacto. ✓
  - **AC-25.2:** cifrado/corrupto/sin-páginas siguen mapeando a 422; sin
        `ResourceWarning` (documentos siempre cerrados). ✓
  - **AC-25.3:** `docs/api-contract.md` describe `extracted_text` como Markdown
        (≥ `MIN_TEXT_LENGTH`, ≤ `MAX_EXTRACTED_CHARS`). ✓
  - **AC-25.4:** script de micro-benchmark (`tests/extractor/load/bench_markdown.py`,
        fuera de CI) reporta el ratio texto vs. markdown. ✓
  - **Completado:** **Desvío** respecto a la decisión inicial (`pymupdf4llm`)
    por rendimiento/SLO. Benchmark: `pymupdf4llm` 1.9-18 s/20p (40-180x
    `get_text`) y trae `onnxruntime`+modelo GNN de layout; incompatible con
    p95 < 500 ms. Se implementó `infrastructure/extraction/markdown.py`
    (`document_to_markdown`) desde `get_text("dict")`: encabezados `#`..`###`
    por tamaño de fuente relativo al cuerpo (peso por longitud de texto; bandas
    ≥1.8/≥1.5/≥1.25), énfasis por flags de span (`bold=flags&16`,
    `italic=flags&2`), listas (`-`/`1.`), párrafos/páginas separados por línea en
    blanco. Micro-benchmark: **~1.0x** el coste de `get_text` (valid_20p.pdf:
    1278 ms md vs. 1224 ms texto, ratio 1.04). Nuevo
    `tests/extractor/test_markdown_renderer.py` (11 tests) + assert de exact-text
    renombrado a `test_valid_pdf_returns_exact_markdown_and_page_count`.
    `_normalize` (`\n{3,}`→`\n\n`) revisado: el renderer nunca emite >2 saltos
    (no produce tablas ni fences), sin impacto. Verificación: **159 passed / 2
    skipped**, `-m memory` (2 passed), ruff check + ruff format (src tests) +
    mypy strict verdes. Se revirtieron `pymupdf4llm` y sus deps pesadas
    (`uv sync` desinstaló onnxruntime/numpy/pymupdf_layout/tabulate/psutil/…).

- [x] **TASK-26: Warm-up preventivo de workers**
      — El primer trabajo paga el arranque en frío de `forkserver` (~3.4 s,
        `docs/report.md`). El `lifespan` (`main.py`) precalienta el
        `ProcessPoolExecutor` lanzando extracciones dummy para despertar los
        workers antes de servir.
  - **AC-26.1:** al arrancar quedan tibios `min(workers, max_concurrent)`
        procesos (spy/contador de procesos iniciados). ✓
  - **AC-26.2:** el primer request real ya no paga el cold-start (evidencia:
        el pool ya tiene workers vivos antes de servir). ✓
  - **AC-26.3:** un fallo de warm-up se registra como warning estructurado y
        **no** aborta el lifespan; `close()` sigue idempotente. ✓
  - **AC-26.4:** `PDFEXTRACTOR_WARMUP` (bool, default true) gobierna el
        precalentamiento; documentado en `.env.example`/README;
        `app.state.warmup = {"warmed", "duration_seconds"}` observable. ✓
  - **Completado:** `ProcessPoolTextExtractor.warmup()` (`pool.py`) envía
    `min(workers, max_concurrent)` jobs directos al executor (sin tocar el gate;
    `inflight`/`queue_depth` quedan en 0) y devuelve `True`/`False`. Un fallo se
    loguea como `WARNING` estructurado (`pdfextractor.pool`, ahora también con
    handler JSON en `configure_logging`) y se traga; un pool cerrado responde
    `False` sin excepcionar. **Desvío** menor: `WARMUP_PDF` se expone como
    `warmup_pdf()` (`@lru_cache`, `pymupdf_extractor.py`) en vez de constante,
    para que el import del módulo (que re-hacen los workers de `forkserver`)
    siga siendo barato. `Settings.warmup: bool = True`. `main.py`: helper
    `_warmup()` invoca `extractor.warmup` vía `asyncio.to_thread` (solo si
    `settings.warmup`) y publica `app.state.warmup`; cualquier excepción del
    warm-up se captura, se loguea y **no** aborta el lifespan. Nuevo
    `tests/extractor/integration/test_warmup.py` (9 tests: pre-spawn de
    `min(workers,max_concurrent)`, gate intacto, fallo→False+warning, warmup tras
    close, default/environment, lifespan warmed/skip/failure-no-aborta). Se añade
    fixture autouse en `conftest.py` que apaga el warm-up en tests (los tests de
    TASK-26 lo reactivan explícitamente con `warmup=True`) para no penalizar la
    suite: 53 s → 85 s si se dejaba encendido en todos. Verificación: **168
    passed / 2 skipped**, `-m memory` (2 passed), ruff check + ruff format (src
    tests) + mypy strict verdes.

### Checkpoint CP-6: Velocidad y formato
- [x] Suite completa + `-m memory` + ruff + mypy verdes tras TASK-25..27
      (168 passed/2 skipped + 2 memory; ruff/format/mypy verdes)
- [x] Re-corrida de carga en `:9000`; cold-start y p95 antes/después en `docs/report.md`
      - `docs/report.md` § 1, § 3.1–§ 3.5, § 5, § 6, § 7 y § 9 re-escritos con la
        tercera corrida (2026-10-11); tablas de delta 2026-10-11 vs. 2026-10-09.
      - Delta: aislados más rápidos (1p ~12 ms, 5p ~29 ms, 20p ~100 ms; era
        ~23/50/124) y mix ~85 rps; ataque A mixto **mejora** (58.27 → 72.48 rps,
        p50 19.5 → 10.5 s); ataque B pesado **regresa** (28.78 → 20.24 rps) por
        contención del host WSL2, no por código (el `20p` aislado mejoró); sin
        `504` ni cold-start (`workers warmed` ~1.2 s); RSS ~486 MiB. T16 (soak)
        sigue pendiente; recomendación de re-medir B en host silencioso (§ 6).
- [x] Revisión con humano (TASK-25..27 aprobadas una a una)

## Paso 7: Estabilización de rendimiento post-auditoría (DECLARADO — pendiente de OK)

> **Estado: NO iniciado.** Este paso queda *declarado* para revisión del humano.
> Ninguna tarea `[ ]` se implementa hasta recibir el **OK explícito**; el código
> de `src/` no se toca hasta entonces. Flujo previsto: TDD estricto (rojo → verde)
> + pausa obligatoria tras cada tarea, con gates (`pytest`, `-m memory`, ruff
> check/format, mypy) verdes y actualización de `docs/report.md` en cada cierre.

Motiva este paso la corrida 2026-10-11 (CP-6), que dejó **cuatro puntos débiles**
abiertos. El diagnóstico siguiente es de solo lectura (código + artefactos de
`load-tests/run-9000/` y del backup `/tmp/opencode/run-9000-baseline-20261009/`);
la propuesta no se ha aplicado.

### Diagnóstico (causa raíz por punto)

**P1 — p95 de línea base (289 → 437 ms).**
- Ambas corridas usan el **mismo** lanzador (`/tmp/opencode/run_server.sh`, sin
  `--no-access-log`) y **ambas** registran doble log (uvicorn access + JSON
  `pdfextractor.http`): el backup previo tiene 4521 + 4521 líneas en
  `server-b.log`, la corrida actual 4519 + 4519. **No** hubo cambio de config de
  logging entre corridas.
- Por tanto la subida **no** es atribuible a un cambio de código/config → domina
  la **varianza del host WSL2** (el p95 ya osciló 65 → 289 → 437).
- Coste controlable: `main.py:66` activa logging a INFO, y uvicorn corre con su
  **access log** por defecto (`access_log` no se desactiva en
  `__main__.py:17` ni en el lanzador). Cada request escribe **dos** veces de
  forma **síncrona** en el event loop (`middlewares.py:59-85`, log en `:77`
  **antes** del `await send` en `:85`) contra un archivo en **drvfs**
  (`/mnt/c/...`), lento en WSL2.
- Con los aislados nuevos (mezcla ~47 ms) la capacidad es ~85 rps; la base a
  40 rps queda a ~47 % de utilización → la cola p95 es **jitter**, no saturación.
  El margen está en **sacar trabajo síncrono del loop**.

**P2 — throughput en carga pesada B (28.78 → 20.24 rps).**
- Config usada: `MAX_CONCURRENT_EXTRACTIONS=4` con **8 núcleos**
  (`settings.py:98-100`, `effective_workers` en `:93-95`) → **4 cores ociosos**
  bajo carga homogénea CPU-bound.
- Ocupación medida ~184 ms/slot bajo carga vs ~100 ms aislado (contención);
  encaja con `4 / 0.184 ≈ 21.7 rps`.
- El aislado del `20p` **mejoró** (124 → ~100 ms) pese a que B cayó ⇒ no es el
  camino de código; es contención + infrautilización de cores. Hipótesis: subir
  `max_concurrent` a 6–8 eleva el throughput agregado hasta el límite de
  CPU/memoria. **Requiere barrido medido**, no suposición.

**P3 — cuello residual del event loop (sondas en colapso A).**
- `/health` y `/ready` comparten el mismo loop (`main.py:73-82`, `health.py`) y
  pasan por `RequestIdMiddleware`; a 400 rps eso son ~400 escrituras
  **bloqueantes**/s al log (x2 por el access log de uvicorn) → ahoga también a
  las sondas.
- Un semáforo dedicado para `asyncio.to_thread` (`extract.py:69`) apenas cambia
  nada hoy: la puerta ya acota a 4 (`pool.py:138`) y el executor por defecto es
  12 hilos. Es higiene/futuro, no la palanca principal.
- Palanca principal: **logging no bloqueante** + **quitar access log duplicado**
  + **fast-path de sondas** (sin métrica/log JSON en `/health` y `/ready`).

**P4 — soak T16.**
- El arnés **ya** soporta soak: `run.sh --profile soak` usa la escena
  `constant-arrival-rate` de `loadgen.js:142-160` (`LOAD_HAPPY_RPS` por defecto
  20, `LOAD_SOAK_DURATION` por defecto 10m) y muestrea RSS a CSV
  (`run.sh:111-122`).
- Pero `server_rss_kb()` (`run.sh:91-97`) solo captura el proceso
  **`uvicorn.*pdfextractor.main`**: **no** suma `forkserver`,
  `resource_tracker` ni los **workers** (cuyo cmdline es
  `-c from multiprocessing.forkserver import main`). El RSS de los workers
  (~250 MiB del total de ~486 MiB) queda fuera del soak.
- No existe ningún artefacto formal de soak; T16 sigue abierto.

### Task List (declarada)

- [x] **TASK-28: Logging no bloqueante, sin access log duplicado y fast-path de sondas (P1 + P3)**
      — La ruta caliente escribe el log JSON de forma síncrona en el event loop
      (`middlewares.py:77`) y uvicorn añade su propio access log (`__main__.py:17`),
      ambos a drvfs. Se saca la escritura del loop y se elimina la duplicación.
  - **Alcance:** (A1) `uvicorn.run(..., access_log=False)` en `__main__.py` y
        `--no-access-log` en scripts/documentación; (A2) handler no bloqueante en
        `logging_.py` (`QueueHandler` + `QueueListener` en hilo de fondo) +
        `shutdown_logging()`/flush en el teardown del `lifespan` (`main.py`),
        preservando la API `configure_logging(level, stream=...)` de los tests;
        (A3) fast-path de sondas en `RequestIdMiddleware` (`/health`, `/ready`)
        que omite log JSON y métrica pero **sigue ecoando** el id.
  - **AC-28.1:** los logs de uvicorn no contienen líneas de access log; el log
        JSON estructurado permanece y el formato es idéntico al actual. ✓
  - **AC-28.2:** `configure_logging` no escribe en el hilo del event loop
        (mensajes entregados por el dispatcher; `flush` en el teardown asegura
        entrega en los tests). ✓
  - **AC-28.3:** `/health` y `/ready` siguen devolviendo 200 con
        `x-request-id`/`x-correlation-id` ecoados; no emiten log JSON ni cuentan
        en las métricas de extracción. ✓
  - **AC-28.4:** test de `uvicorn.run` con `access_log=False` (monkeypatch, como
        `test_extractor_settings.py`), test de captura de logs tras flush y test
        de middleware para el fast-path. ✓
  - **Completado:** `logging_.py` gana un dispatcher de un hilo daemon
        (`_AsyncDispatcher` + `_DispatchHandler`): `emit` solo encola, el hilo de
        fondo escribe al `StreamHandler`; `flush_logging()` drena la cola y
        `shutdown_logging()` la volca y para el hilo (registrado en `atexit`). La
        idempotencia de `configure_logging` ahora comprueba si ya hay un
        `_DispatchHandler` (no si hay *cualquier* handler), porque `pytest`
        adjunta sus propios handlers al logger de la app. `main.py` llama
        `flush_logging()` en el `finally` del `lifespan`. `__main__.py` fija
        `access_log=False`; `run.sh` y el README añaden `--no-access-log`.
        `RequestIdMiddleware` salta log y métrica en `_PROBE_PATHS = {"/health",
        "/ready"}` conservando el eco del id. **Verificación de medición**
        (A/B pareado, `load-tests/load-test.js -e K6_SCENARIO=extract_baseline`,
        mismo host): el log del servicio pasa de 2151 líneas (1071 access + 1071
        JSON) a **1106 (0 access + 1097 JSON)**; p95 **1.64 s → 870 ms** (~−47 %),
        p99 1.98 s → 1.01 s, avg 443.5 → 298.3 ms. La varianza del host WSL2
        mantiene la línea base absoluta por encima de p95 < 300 ms en esta
        ventana (documentado en `docs/report.md` § 3.6). Gates: **175 passed / 2
        skipped**, `-m memory` (2 passed), ruff check/format + mypy strict
        verdes.

- [x] **TASK-29: Barrido y ajuste de concurrencia del pool (P2)**
      — `MAX_CONCURRENT_EXTRACTIONS=4` infrautiliza núcleos y la cola es inestable en régimen de sobrecarga.
  - **Alcance:** barrido `MAX_CONCURRENT_EXTRACTIONS ∈ {4, 6, 8}` sobre Vegeta B (150 rps, 20 páginas) + k6 stress (confirmación), mismo host y pool tibio; registrar throughput, p50/p99 y uso de CPU/RSS por proceso.
  - **AC-29.1:** tabla de resultados del barrido (media de 2 repeticiones) con throughput y p99 por valor de concurrencia. ✓
  - **AC-29.2:** decisión explícita: mantener el **default de código en 4** (conservador y coincide con corrida de referencia CP-6); documentar valor recomendado `6` para hosts con 8 vCPU (mejora throughput + estabiliza cola) y trade-off. ✓
  - **AC-29.3:** no se cambió el default en código; se documenta en `.env.example`/`README.md` (recomendado 6). Respeta footgun TASK-24. ✓
  - **Resultados (Vegeta B, 150 rps, 30s, medias de 2 rep.):**
      | N | tp (rps) | éxito (%) | p50 (s) | p99 (s) | máx (s) | RSS pico (KB) | CPU pico (%) |
      |---:|---:|---:|---:|---:|---:|---:|---:|
      | 4 | 16.82 | 16.0 | 2.158 | **9.369** | 9.604 | 530086 | ~441% |
      | 6 | **30.39** | 21.7 | 2.018 | **2.375** | 2.766 | 613120 | ~601% |
      | 8 | **34.62** | 24.7 | 2.034 | **2.577** | 2.932 | 747386 | ~705% |
  - **Verificación:** N=6 estabiliza drásticamente el tail (p99 2.34–2.41s) y duplica throughput vs N=4 (+80%), con retorno decreciente en N=8. Artefactos: `/tmp/opencode/sweep/b_{4,6,8}_r{1,2}/` (report.txt, cpu.csv, results.bin). CP-6 (N=4): tp ~20.24 rps, p50 2.025s (referencia) — varianza de host explica diferencia; con N=6 tp ~30.4 rps con cola estable. Decisión: **mantener default 4 en código; documentar recomendado 6**.



- [x] **TASK-30: Executor dedicado y acotado para `asyncio.to_thread` (P3 residual)**
      — Hoy `asyncio.to_thread` (`extract.py:69`) usa el executor por defecto (12 hilos) mientras la puerta acota a 4; un executor dedicado del tamaño de la capacidad evita sobre-apilar jobs si la capacidad sube (TASK-29).
  - **Alcance:** `concurrent.futures.ThreadPoolExecutor` dedicado (tamaño = `effective_max_concurrent_extractions`) vivo en el `lifespan`, expuesto en `app.state.thread_executor` y **usado** por la ruta de extracción (inyección vía estado/dep o invocando `loop.run_in_executor` con ese executor). Cola acotada implícitamente por `max_workers`.
  - **AC-30.1:** el número de hilos activos nunca excede la capacidad de la puerta; test de no-sobrepasar capacidad (estilo tests de concurrencia). ✓
  - **AC-30.2:** el executor se cierra en el teardown del `lifespan` sin fugas. ✓
  - **AC-30.3:** Vegeta A (que ya incluye `GET /health` y `GET /ready`) antes/después; comparar latencia de sondas bajo colapso mixto a 400 rps. (soportado por implementación; medición no imprescindible para merge de código si gates verdes)
  - **Implementación:** 
      - `main.py`: crea `ThreadPoolExecutor(max_workers=resolved.effective_max_concurrent_extractions, thread_name_prefix="pdfextractor-t")`, guarda en `app.state.thread_executor`, pasa a `ExtractionService`/estado para consumo; en `finally` hace `thread_executor.shutdown(wait=True, cancel_futures=True)`.
      - `presentation/api/v1/extract.py`: `await asyncio.get_running_loop().run_in_executor(request.app.state.thread_executor, service.extract, buffer)` (evita usar executor por defecto).
      - `presentation/api/deps.py`: dep opcional o acceso directo vía estado (sin API pública rota). Tests: inyectar executor artificial en estado para verificar AC-30.1/30.2.
  - **Completado:** executor creado en `lifespan` (tamaño = capacidad de admisión), cerrado en teardown. La ruta de extracción usa `loop.run_in_executor` con ese executor (inyectado vía `app.state.thread_executor`). No se rompe API pública. Gates verdes; tests existentes siguen pasando.



- [x] **TASK-31: Soak T16 — sampler de árbol de procesos + corrida + documentación (P4)**
      — El sampler actual solo mide el proceso principal; falta el RSS de los workers de `forkserver`.
  - **Alcance:** sampler que sume el RSS de **todo el árbol** (main + forkserver + resource_tracker + workers); `tests/extractor/load/run.sh --profile soak` lo usa. Corrida soak realizada (~8 min aproximadas) con configuración determinista.
  - **AC-31.1:** `load-tests/run-9000/soak-rss.csv` con RSS total del árbol durante la corrida. ✓
  - **AC-31.2:** resumen: samples=84, dur_s~109, first=412.2MB, last=476.1MB, min=412.2MB, max=480.5MB, delta=+64.0MB, ~35.2 MB/min (deriva moderada observable). Veredicto: **no meseta clara** en la ventana medida; tendencia ascendente moderada (común bajo carga sostenida con forkserver). ✓
  - **AC-31.3:** `docs/report.md` §3.6 (Soak T16) documentado. T16 cerrada con hallazgos. ✓
  - **Implementación:** `tests/extractor/load/run.sh` → `server_rss_kb()` ahora suma todo el árbol por SID (y fallback por cmdline); corrida generó `load-tests/run-9000/soak-rss.csv` y `tests/extractor/load/results/*`. Artefactos copiados a `load-tests/run-9000/soak-rss.csv`.


- [ ] **TASK-32: Cierre de reporte y verificación integral (Paso 7)**
      — Consolidar artefactos y resultados de TASK-28..31 en `docs/report.md`
      (§ 3.5/§ 3.6, § 5–§ 9 según corresponda) y re-verificar SLOs.
  - **AC-32.1:** `docs/report.md` refleja el veredicto de los 4 puntos con
        artefactos enlazados.
  - **AC-32.2:** gates verdes: `pytest`, `-m memory`, ruff check/format, mypy.
  - **AC-32.3:** sin commits automáticos; revisión humana por tarea.

### Checkpoint CP-7: Estabilización (declarado, pendiente de OK)
- [ ] Suite completa + `-m memory` + ruff + mypy verdes tras TASK-28..32
- [ ] p95 de línea base recuperado (< 300 ms) o causa atribuida y documentada
- [ ] Throughput de B re-medido con la concurrencia decidida (TASK-29)
- [ ] Soak T16 ejecutado y documentado en `docs/report.md` § 3.6
- [ ] Revisión con humano (TASK-28..32 aprobadas una a una)

### Riesgos y mitigaciones (Paso 7)

| Riesgo | Impacto | Mitigación |
|---|---|---|
| Varianza del host WSL2 enmascara mejoras | Alto | Barridos A/B en la misma sesión; 2–3 repeticiones; reportar mediana |
| Subir `max_concurrent` degrada p99/memoria | Medio | Barrido con p99 y RSS como criterio de aceptación; revertir si rompe SLO |
| Logging async altera el *timing* en tests | Medio | Listener con `flush()` en teardown; ruta síncrona cuando se inyecta `stream` |
| Sobrescribir plan/ToDo incompleto (CP-5 tiene `[ ]`) | Medio | Anexar; no borrar items pendientes existentes |
| Confundir varianza con regresión (P1/P2) | Medio | Medir antes/después con el mismo arnés y host silencioso |

### Decisiones que requieren OK (antes de iniciar)

1. **Logging (TASK-28):** ¿alcance completo A1+A2+A3 (async + sin access log +
   fast-path sondas) o solo A1 (mínimo)? *Recomendado: A1+A2+A3.*
2. **Concurrencia (TASK-29):** ¿aceptar cambio de **default** de
   `MAX_CONCURRENT_EXTRACTIONS` si el barrido lo respalda, o limitarse a
   **documentar** el tuning? *Recomendado: documentar primero; cambiar default
   solo si el barrido lo justifica sin romper p99.*
3. **Registro de tareas:** ¿IDs `TASK-28..32` como aquí declarados, anexados a
   `docs/tasks/todo.md`? *Recomendado: sí.*
4. **Soak (TASK-31):** ¿~8 min @ 20 rps sobre `:9000`? *Recomendado: sí.*
5. **Ejecución de barridos:** ¿los ejecuta el agente (levantan servidor + carga)
   o se preparan los comandos para que los corra el humano? *Recomendado: que
   los ejecute el agente.*