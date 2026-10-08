# Implementation Plan: Endurecimiento de confiabilidad del extractor (`pdfextractor`) y entrada del orquestador (`paperextractor`)

## Overview

Épica de pago de deuda técnica identificada en el análisis exhaustivo del repositorio. Cuatro frentes, todos con **pruebas que cambian de rojo a verde** como criterio de aceptación:

1. **§6.1 Timeout duplicado y no coordinado** — la ruta (`src/pdfextractor/presentation/api/v1/extract.py:49-51`) y el pool (`src/pdfextractor/infrastructure/concurrency/pool.py:125`) imponen dos relojes con el mismo valor y compiten en carrera. Resultado: 504 con origen no determinista, y trabajo que sigue corriendo en un worker tras cancelar.
2. **§6.2 Copias de memoria** — `bytes(buffer)` en la ruta + `io.BytesIO` en pymupdf + copia interna de la librería ≈ 2-3× el tamaño del upload.
3. **§6.3 Brecha del BufferPool** — `acquire() → None` cuando hay más streams que buffers ⇒ se cae a `bytearray()` ilimitado por petición.
4. **§6.4/§6.7 Endurecimiento de entrada** — `Content-Length` malformado → 500 en el orquestador; settings del extractor sin validación (`workers=0` rompe en arranque); `.env.example` incompleto (§6.13).

## Architecture Decisions

- **D1 — Fuente única de verdad para el timeout (§6.1).** El pool de procesos es quien *mide* el tiempo de extracción (`future.result(timeout)`). El `asyncio.wait_for` de la ruta queda como **red de seguridad puramente defensiva** con margen constante `queue_timeout + extraction_timeout + 2s`, de modo que el pool siempre dispara primero (504 `ExtractionTimeoutError` determinístico). No se elimina `wait_for`: protege el event loop si el pool fallara en cumplir su contrato.
- **D2 — El `BufferPool` se convierte en el gate de concurrency de TODO el request (§6.3).** La causa raíz es que los buffers se adquieren *antes* que el gate: `acquire()` pasa a ser **bloqueante con timeout** (vía `threading.Condition`, ejecutado en `to_thread`) y devuelve `None` solo al vencer → ruta responde `503 OverloadError`. Invariante resultante: **máximo `max_concurrent` buffers en existencia = máximo streams concurrentes**, por construcción. Memoria total acotada por `capacity × max_upload_bytes`. El gate actual de `ProcessPoolTextExtractor` se mantiene como segunda barrera (mismo número); sincronización sin deadlock garantizada (los buffers se liberan en `finally` tras extracción; las extracciones nunca adquieren buffers).
- **D3 — Semántica de `/ready` sin cambios.** El estado "overloaded" sigue ligado al gate de extracción (cola de trabajo), no al buffer-wait. El `503` por buffer-wait es la respuesta por petición, no una señal de readiness.
- **D4 — Cero copias explícitas de ruta (§6.2).** Se amplía `ExtractionService.extract` y `PyMuPDFExtractor.extract` a `bytes | bytearray`; la ruta pasa el `bytearray` del pool **directamente** (ya no `bytes(buffer)`). El `io.BytesIO` interno se conserva (una sola construcción). Seguridad de concurrencia: el buffer no se muta durante la extracción síncrona y solo se libera en `finally`.
- **D5 — Validación en el borde de config (§6.7).** `Settings` del extractor gana validadores Pydantic (`workers ≥ 1` cuando se fijan, `max_concurrent ≥ 1`, timeouts `> 0`, `max_upload_bytes > 0`, `min_text_length ≥ 0`) → falla **al arrancar**, no a mitad de tráfico. `Content-Length` no numérico en el orquestador → `422 InvalidRequestError`.

## Task List

### Phase 0: Foundation y gate de medición

- [ ] **TASK-01: Baseline verde antes de tocar nada**
- [ ] **TASK-02: Test de techo de memoria (`-m memory`)** — falla hoy, documenta §6.2/§6.3

### Checkpoint: Foundation (CP-1)
- [ ] Suite completa + ruff + mypy verdes
- [ ] El test de memoria falla con el comportamiento actual (vuelve verde en TASK-06)
- [ ] Revisión con humano antes de seguir

### Phase 1: Timeout determinístico (§6.1)

- [ ] **TASK-03: Unicidad del timeout + red de seguridad en la ruta**

### Checkpoint: Timeout (CP-2)
- [ ] 504 único y determinístico; job en el borde responde 200; suite verde

### Phase 2: Memoria (§6.2 + §6.3, el núcleo del epic)

- [ ] **TASK-04: Eliminar la copia `bytes(buffer)` de la ruta**
- [ ] **TASK-05: `BufferPool` como gate bloqueante con timeout + `503 OverloadError`**
- [ ] **TASK-06: Cierre del techo de memoria (gate tests verdes) e invariantes de concurrencia**

### Checkpoint: Memoria (CP-3)
- [ ] Test de memoria pasa (pico RSS acotado); tests de concurrencia existentes verdes
- [ ] Revisión con humano antes de seguir

### Phase 3: Endurecimiento de entrada (§6.4, §6.7, §6.13)

- [ ] **TASK-07: `Content-Length` malformado → 422 en `paperextractor`**
- [ ] **TASK-08: Validadores de `Settings` en `pdfextractor`**
- [ ] **TASK-09: `.env.example` completo con variables `PDFEXTRACTOR_*`**

### Checkpoint: Entrada (CP-4)
- [ ] Un `Content-Length` basura da 422 (no 500); `workers=0` falla al arrancar con mensaje claro

### Phase 4: Verificación final

- [ ] **TASK-10: Verificación integral (suite + lint + types + memoria) y cierre**

### Checkpoint: Complete
- [ ] Todas las acceptance criteria cumplidas
- [ ] Suficiente para PR single que resuelve 6.1, 6.2, 6.3, 6.4, 6.7, 6.13

---

## Detalle de Tareas

### TASK-01: Baseline verde antes de tocar nada
**Descripción:** Ejecutar la suite completa y los chequeos estáticos para fijar el estado cero. Registrar el recuento de tests (≈110) como referencia para las fases.

**Criterios de aceptación:**
- [ ] `uv run pytest -q` pasa completo
- [ ] `uv run ruff check .` y `uv run ruff format --check src tests` pasan
- [ ] `uv run mypy -p paperextractor` y `uv run mypy -p pdfextractor` pasan

**Verificación:** los 4 comandos sin error.

**Dependencias:** None
**Files likely touched:** ninguno
**Sizing:** XS

### TASK-02: Test de techo de memoria (`-m memory`)
**Descripción:** Crear `tests/extractor/performance/test_memory.py` con marcador `memory` (registrado en el `conftest.py` raíz) que:
- Mide `resource.getrusage(RUSAGE_SELF).ru_maxrss` alrededor de una extracción real de un PDF de tamaño fijo (p.ej. 8MB).
- Aserta `pico < base + K × tamaño_upload` (constante K conservadora).
- Aserta que se construye **exactamente un `io.BytesIO`** por extracción (espía en `pymupdf_extractor`).
Debe **fallar hoy** (documenta la deuda §6.2) y quedarse fuera del run por defecto de pytest (como la marca `contract`).

**Criterios de aceptación:**
- [ ] Existe el marcador `memory` y el test está skip por defecto
- [ ] El test falla con el código actual (baseline)
- [ ] `uv run pytest -m memory` lo ejecuta explícitamente

**Verificación:** `uv run pytest -m memory -q tests/extractor/performance/test_memory.py`

**Dependencias:** TASK-01
**Files likely touched:** `tests/extractor/performance/test_memory.py`, `tests/conftest.py`, `tests/extractor/conftest.py`
**Sizing:** S

### TASK-03: Timeout determinístico en la ruta (§6.1)
**Descripción:** En `src/pdfextractor/presentation/api/v1/extract.py`:
- Añadir constante `FUTURE_WAIT_SAFETY_MARGIN_SECONDS = 2.0`.
- `wait_for(..., timeout=settings.extraction_timeout_seconds + settings.queue_timeout_seconds + margen)`.
El pool (`pool.py:125`) sigue siendo el reloj autoritativo; el `wait_for` solo es red de seguridad. El pool lanza `ExtractionTimeoutError` de dominio (propaga igual que hoy).

Tests (`tests/extractor/integration/test_timeout.py`):
- Falso extractor que bloquea `extraction_timeout + ε`: la ruta responde **un** 504 con body `{"error":"timeout"}`.
- Job que completa justo en `extraction_timeout`: responde 200 (sin falsos 504 del `wait_for`).
- Test unitario que aserta `wait_for_timeout > pool_budget` (el margen hace imposible la carrera).

**Criterios de aceptación:**
- [ ] El reloj autoritativo es el pool; la ruta nunca dispara 504 antes que el pool
- [ ] Tests nuevos verdes; `test_middlewares.py::test_a_job_that_exceeds_the_timeout_answers_504_timeout` sigue verde

**Verificación:** `uv run pytest tests/extractor/integration/test_timeout.py tests/extractor/integration/test_middlewares.py -q`

**Dependencias:** TASK-01
**Files likely touched:** `src/pdfextractor/presentation/api/v1/extract.py`, `tests/extractor/integration/test_timeout.py`
**Sizing:** S

### TASK-04: Eliminar la copia `bytes(buffer)` (§6.2)
**Descripción:** Ampliar tipos para recibir el `bytearray` sin copia intermedia:
- `ExtractionService.extract(data: bytes | bytearray)` (`src/pdfextractor/application/services/extraction_service.py:44`)
- `PyMuPDFExtractor.extract(data: bytes | bytearray)` (`src/pdfextractor/infrastructure/extraction/pymupdf_extractor.py:24`) que construye un único `io.BytesIO(buffer)`.
- Ruta (`src/pdfextractor/presentation/api/v1/extract.py:50`): pasar `buffer` directo, eliminar `bytes(buffer)`.
- Ampliar el puerto `TextExtractor` (`application/interfaces.py`) a `bytes | bytearray`.

Tests: espía que aserta una única construcción de `BytesIO`; tests existentes del servicio/extractor siguen verdes (aceptan ambos tipos).

**Criterios de aceptación:**
- [ ] La ruta ya no llama a `bytes(buffer)`
- [ ] App-service y extractor aceptan `bytearray` sin copia previa
- [ ] Tests unitarios verdes (identidad de bytes mantenida)

**Verificación:** `uv run pytest tests/extractor/test_pymupdf_extractor.py tests/extractor/test_extraction_service.py tests/extractor/integration/test_extract_api.py -q`

**Dependencias:** TASK-03
**Files likely touched:** `src/pdfextractor/presentation/api/v1/extract.py`, `src/pdfextractor/application/services/extraction_service.py`, `src/pdfextractor/application/interfaces.py`, `src/pdfextractor/infrastructure/extraction/pymupdf_extractor.py`, tests
**Sizing:** M

### TASK-05: `BufferPool` como gate bloqueante con timeout (§6.3)
**Descripción:** En `src/pdfextractor/infrastructure/memory/pool.py`:
- Añadir `threading.Condition` + contador de espera.
- `acquire(timeout: float) -> bytearray | None`: si hay libre o capacidad → presta; si no → espera en la condition hasta el timeout; timeout → `None`.
- `release(buffer)`: reintegra y `notify()` (sin liberar más allá del tope, como hoy).

Ruta (`src/pdfextractor/presentation/api/v1/extract.py:37-45`):
- `sink = await asyncio.to_thread(pool.acquire, settings.queue_timeout_seconds)`
- Si `sink is None` → **`raise OverloadError()`** (nunca más `bytearray()` sin origen).
- El `finally` con `pool.release` existente pasa a notificar a los waiters.

El gate de `ProcessPoolTextExtractor` queda como segunda barrera de igual tamaño (sin cambios de comportamiento).

Tests (`tests/extractor/test_buffer_pool.py` + nuevo `test_buffer_gate.py`):
- `acquire` concurrente nunca supera `capacity` de leases; `None` solo tras timeout; `release` despierta a un waiter.
- Con `max_concurrent=1`: 2 uploads concurrentes ⇒ el segundo responde `503 {"error":"overloaded"}`; nunca se crea un `bytearray` fuera del pool.

**Criterios de aceptación:**
- [ ] `acquire` es bloqueante con timeout y nunca devuelve buffer por encima del tope
- [ ] La ruta traduce timeout de buffer en `503 OverloadError`
- [ ] Test de concurrencia de la API (2 uploads, cap=1) pasa

**Verificación:** `uv run pytest tests/extractor/test_buffer_pool.py tests/extractor/integration/test_concurrency.py tests/extractor/integration/test_extract_api.py -q`

**Dependencias:** TASK-04
**Files likely touched:** `src/pdfextractor/infrastructure/memory/pool.py`, `src/pdfextractor/presentation/api/v1/extract.py`, tests
**Sizing:** M

### TASK-06: Cierre del techo de memoria e invariantes de concurrencia
**Descripción:** Verificación end-to-end del objetivo de memoria:
- El test `-m memory` (TASK-02) debe **pasar ahora** (pico acotado + un único `BytesIO`).
- Reforzar invariantes en `tests/extractor/integration/test_concurrency.py`: con `capacity = max_concurrent`, N>capacity uploads simultáneos producen éxitos + `503` acotados (sin buffering ilimitado).
- Invariante de no-deadlock: test con dos tandas de requests (`cap=1`, el segundo comienza mientras el primero extrae) resuelve sin bloqueo global.

**Criterios de aceptación:**
- [ ] `uv run pytest -m memory` verde
- [ ] Tests de concurrencia actualizados verdes; sin deadlock documentado por test

**Verificación:** `uv run pytest -m memory tests/extractor/performance/test_memory.py -q && uv run pytest tests/extractor/integration/test_concurrency.py -q`

**Dependencias:** TASK-05
**Files likely touched:** `tests/extractor/performance/test_memory.py`, `tests/extractor/integration/test_concurrency.py`
**Sizing:** S

### TASK-07: `Content-Length` malformado → 422 en `paperextractor` (§6.4)
**Descripción:** En `src/paperextractor/presentation/api/v1/extract.py::declared_length` (`:61-68`): envolver `int(announced)`; ante `ValueError` → `raise InvalidRequestError("Content-Length no es un entero válido")`. El handler global lo convierte en `422 application/problem+json` (tipo `invalid-request`).

Tests: nuevo caso en `tests/integration/test_error_mapping.py` con header basura → 422 con `detail` controlado; el body nunca llega al downstream.

**Criterios de aceptación:**
- [ ] `Content-Length: abc` → `422 invalid-request` (no 500)
- [ ] Sin cambios en el flujo feliz (length válido se reenvía igual)

**Verificación:** `uv run pytest tests/integration/test_error_mapping.py tests/integration/test_extract_flow.py -q`

**Dependencias:** TASK-06
**Files likely touched:** `src/paperextractor/presentation/api/v1/extract.py`, tests
**Sizing:** S

### TASK-08: Validadores de `Settings` en `pdfextractor` (§6.7)
**Descripción:** En `src/pdfextractor/infrastructure/config/settings.py`, añadir `@field_validator` para:
- `workers`: si no `None`, `>= 1`
- `max_concurrent_extractions`: si no `None`, `>= 1`
- `max_upload_bytes`, `min_text_length`, `queue_timeout_seconds`, `extraction_timeout_seconds`: `> 0` (y `min_text_length >= 0`)

Invalidez → `pydantic.ValidationError` al instanciar `Settings()` (fallo en arranque/lifespan, mensaje claro).

Tests (`tests/extractor/test_extractor_settings.py`): cada campo inválido lanza con mensaje que identifica la variable (p.ej. `PDFEXTRACTOR_WORKERS=0` → error; `extraction_timeout_seconds=0` → error; default sigue sano).

**Criterios de aceptación:**
- [ ] `workers=0` y `max_concurrent=0` rechazados
- [ ] Timeouts `0`/negativos rechazados
- [ ] Defaults y envs válidos sin cambios

**Verificación:** `uv run pytest tests/extractor/test_extractor_settings.py -q`

**Dependencias:** None (paralelizable con TASK-07)
**Files likely touched:** `src/pdfextractor/infrastructure/config/settings.py`, `tests/extractor/test_extractor_settings.py`
**Sizing:** S

### TASK-09: `.env.example` completo (§6.13)
**Descripción:** Añadir al `.env.example` de la raíz el bloque `# pdfextractor (downstream extractor)` con las 10 variables `PDFEXTRACTOR_*` y comentarios de cada una (misma estructura que las del orquestador).

**Criterios de aceptación:**
- [ ] Todas las variables que lee `Settings` de ambos servicios están documentadas
- [ ] Sin cambiar ningún default de código

**Verificación:** revisión manual del fichero.

**Dependencias:** None
**Files likely touched:** `.env.example`
**Sizing:** XS

### TASK-10: Verificación integral y cierre
**Descripción:** Ejecutar la batería completa; actualizar `docs/perf-report.md` (si aplica) con la nueva cota de memoria y el comportamiento de timeout; revisión final del diff de todo el epic como un único entregable.

**Criterios de aceptación:**
- [ ] `uv run pytest -q` verde (suite completa)
- [ ] `uv run ruff check . && uv run ruff format --check src tests` verde
- [ ] `uv run mypy -p paperextractor && uv run mypy -p pdfextractor` verde
- [ ] `uv run pytest -m memory -q` verde
- [ ] Decisión documentada sobre 6.1/6.2/6.3/6.4 (en `docs/perf-report.md` o ADR corto)

**Verificación:** los comandos anteriores.

**Dependencias:** TASK-07, TASK-08, TASK-09
**Files likely touched:** `docs/perf-report.md` (o nuevo ADR)
**Sizing:** S

---

## Fase 3: Plan de Pruebas y Validación

| Tipo | Qué prueba | Comando |
|---|---|---|
| Unit (extractor) | pool de buffers, service, pymupdf, multipart | `uv run pytest tests/extractor -q` |
| Unit (orquestador) | streaming, ingress, http_client, orchestrator, handlers | `uv run pytest tests/unit -q` |
| Integración | flujo completo, errores, concurrency, telemetría | `uv run pytest tests/integration tests/extractor/integration -q` |
| Memoria (nuevo) | techo RSS + una sola construcción de `BytesIO` | `uv run pytest -m memory -q` |
| Estática | lint + format + types | `uv run ruff check . && uv run ruff format --check src tests && uv run mypy -p paperextractor && uv run mypy -p pdfextractor` |
| Total | todo el epic | `uv run pytest -q` |

## Riesgos y Mitigaciones

| Riesgo | Impacto | Mitigación |
|---|---|---|
| Cambio en `ProcessPoolTextExtractor` rompe invariantes de concurrencia existentes | Alto | TASK-05 mantiene intacto el gate actual como 2ª barrera; los tests de `test_concurrency.py` se corren en cada checkpoint |
| El test de techo de memoria resulta frágil (thrashing de CI) | Medio | Umbral conservador (K×upload) + marcador `memory` fuera del run por defecto; medición sobre `ru_maxrss` |
| `to_thread(pool.acquire)` satura el ThreadPoolExecutor por defecto bajo avalancha de uploads | Medio | Límite de threads del default executor es el techo real de esperas; documentar en TASK-05 y dejar el `503` como respuesta a tiempo |
| Cambio de tipos `bytes → bytes|bytearray` rompe el puerto tipado | Bajo | Ampliar el puerto `TextExtractor` en el mismo commit; mypy estricto valida |
| `wait_for` como red de seguridad conserva parte de la complejidad | Bajo | Margen documentado y testeado; eliminar solo si un task posterior fija proceso killer (fuera de alcance) |

## Fuera de Alcance (deuda restante)

Documentada para futuras épicas: §6.5 (spin de `_fill` en source vacíos), §6.6 (código muerto `_text_extractor_port`), §6.8 (dialectos/logs/request-id inconsistentes), §6.9 (mensajes en español hardcode), §6.10 (ventana `_read_file`), §6.11 (futures huérfanos tras restart de pool), §6.12 (ausencia de límites de concurrencia en el orquestador), §6.14 (`assert isinstance` en handlers).

## Open Questions

1. ¿La constante de margen `FUTURE_WAIT_SAFETY_MARGIN_SECONDS` queda hardcodeada (2s) o se mueve a settings `PDFEXTRACTOR_*`? → Resuelta: hardcodeada como constante de módulo (TASK-03).
2. ¿Se puede dedicar un run del `workflow_dispatch` de CI para ejecutar `-m memory`? → Pendiente; por defecto solo local.