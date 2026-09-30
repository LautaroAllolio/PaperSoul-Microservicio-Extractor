# Tasks — BigPickle (Orquestador PaperSoul)

Task list operativo de la implementación de BigPickle. Detalle técnico en `tasks/plan.md`; contrato y especificación en `SPEC-bigpickle.md`. SDD Fase 1 y 2 completas — pendiente de aprobación humana antes de implementar.

---

## Fase A: Fundación

### Task 1: Scaffold del proyecto con uv y esqueleto en 3 capas

**Description:** Crear el proyecto Python 3.12 con `uv` (`pyproject.toml`, `.python-version`, `.venv`), el paquete `src/bigpickle/` con los directorios `presentation/`, `application/`, `infrastructure/` vacíos, `main.py` con app factory mínima, y `infrastructure/config/settings.py` (pydantic-settings, prefijo `BIGPICKLE_`) con todas las variables de la sección 7 del plan.

**Acceptance criteria:**
- [x] `uv sync` instala sin errores; `bigpickle` es importable desde `src/` (editable).
- [x] `main.py` levanta una FastAPI app vacía con `GET /` de prueba respondiendo `200`.
- [x] `Settings` carga desde entorno con defaults correctos (url del Extractor, límites de tamaño/timeout, pool).
- [x] `uv run mypy -p bigpickle` y `uv run ruff check .` pasan en limpio. *(mypy 2.3.1 no resuelve el nombre bare con layout `src/`; `-p` es la forma verificada)*

**Verification:**
- [x] Tests pass: `uv run pytest` (test básico de arranque de app/settings).
- [x] Build succeeds: `uv run uvicorn bigpickle.main:app --app-dir src` inicia y responde.
- [x] Manual check: `python -c "from bigpickle.main import create_app"` sin errores.

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
- [x] Schemas validan/duplican exactamente los contratos JSON de la SPEC §6-§7 (tests de serialización).
- [x] `RequestValidationError` → `422 validation-error` con array `errors` (loc/msg/type).
- [x] `HTTPException` y excepción genérica → `problem+json` con `status` correcto.
- [x] Handlers mapean cada excepción de dominio (aún sin implementar) desde `status`+`problem_type` que expone la excepción.

**Verification:**
- [x] Tests pass: `uv run pytest tests/unit/test_schemas.py tests/unit/test_problems.py`.
- [x] Manual check: POST inválido a la app devuelve body RFC 9457 completo. *(verificado: `HTTPException` vivo → `application/problem+json`; `RequestValidationError`/dominio/genérico cubiertos por tests; body endpoint completo a partir de Task 6)*
- [x] `uv run mypy -p bigpickle` y `uv run ruff check .` en limpio. *(mypy 2.3.1 no resuelve el nombre bare con layout `src/`; `-p` es la forma verificada)*

**Dependencies:** Task 1

**Files likely touched:**
- `src/bigpickle/presentation/schemas/{document,health,problems}.py`
- `src/bigpickle/presentation/errors/handlers.py`
- `src/bigpickle/application/errors.py` (definir jerarquía de excepciones de dominio, base)
- `tests/unit/test_schemas.py`, `tests/unit/test_problems.py`

**Estimated scope:** Medium (4-5 archivos).

### Checkpoint A (tras Tasks 1-2)
- [x] `uv sync` limpio; schemas compilan y serializan según SPEC.
- [x] Mapa RFC 9457 cubierto por tests de handler.
- [x] Review con humano antes de seguir.

---

## Fase B: Núcleo de streaming y cliente downstream

### Task 3: Streaming sin disco (`SourceForwardingStream` + guard de tamaño)

**Description:** Implementar `infrastructure/http/streaming.py`: el adapter `SourceForwardingStream(httpx.AsyncByteStream)` que itera una `AsyncByteSource` en chunks de 64 KB, expone `get_content_length()` (devuelto desde el `Content-Length` entrante o `None` → chunked), y aborta con `PayloadTooLargeError` al superar `BIGPICKLE_MAX_UPLOAD_BYTES`, sin buffering del archivo. Escribir primero la prueba de humo R1: verificar de forma empírica que httpx envía async byte streams con length correcto.

**Acceptance criteria:**
- [x] El iterador emite exactamente los bytes recibidos, en orden, sin duplicaciones ni pérdidas (test byte-identical unitario).
- [x] `get_content_length()` devuelve el length entrante o `None`.
- [x] Guard: al exceder el límite se eleva `PayloadTooLargeError` durante la iteración (test con fuente de > límite).
- [x] No se escribe a disco en ningún punto (test de monkeypatch de ruta de escritura).

**Verification:**
- [x] Tests pass: `uv run pytest tests/unit/test_streaming.py` (15 passed; suite completa 45 passed).
- [x] Manual/R1: prueba de humo con `httpx.AsyncClient` contra `ASGITransport` verificando un request body streamed con y sin `Content-Length`.
- [x] `uv run mypy -p bigpickle` y `uv run ruff check .` en limpio. *(mypy 2.3.1 no resuelve el nombre bare con layout `src/`; `-p` es la forma verificada. `ruff format --check` limpio en `src/` y `tests/`; los 2 `.md` de `docs/` con diferencias de format son preexistentes y se limpian en T9)*

**Hallazgo R1 (resuelto empíricamente con httpx 0.28.1):** httpx **no** consulta `get_content_length()` sobre `AsyncByteStream`; para cualquier async iterable fuerza `Transfer-Encoding: chunked`. El `Content-Length` solo llega al cable si el **caller** setea el header — y entonces httpx descarta su propio `Transfer-Encoding` (`Request._prepare`). Consecuencia para D5/T4: `HttpExtractorClient` debe pasar `Content-Length: str(stream.get_content_length())` explícitamente cuando no sea `None`; `get_content_length()` queda como API del adapter, no como hook de httpx. Ambos caminos (con length y chunked) quedaron verificados byte a byte.

**Dependencies:** Task 1

**Files likely touched:**
- `src/bigpickle/infrastructure/http/streaming.py` → `SourceForwardingStream` + guard de tamaño (`_SizeGuard`)
- `src/bigpickle/infrastructure/http/__init__.py` (nuevo, paquete)
- `src/bigpickle/application/interfaces.py` (`AsyncByteSource`, `ExtractionResult`, `ExtractionService`)
- `src/bigpickle/application/errors.py` (`PayloadTooLargeError` — ya existía de T2, sin cambios)
- `tests/unit/test_streaming.py`

**Estimated scope:** Medium (2-3 archivos). Alto riesgo → temprano (R1/R2).

---

### Task 4: `HttpExtractorClient` (httpx) y traducción de errores downstream

**Description:** Implementar `infrastructure/http/downstream/`: `base.py` (ABC `ExtractorClient` con `forward()` y `ping()`), `models.py` (records crudos `ExtractorSuccess`, `ExtractorError` con `error: str`), y `http_client.py` con la implementación real: `AsyncClient` con pool y timeouts por fase desde settings; `forward()` emite `POST {BASE}/api/v1/extract` con `content=SourceForwardingStream` y headers `Content-Type`, `Content-Length` (si hay), `X-Request-Id`; traduce respuestas del Extractor a excepciones de dominio (422→`ExtractionFailedError`, 5xx→`UpstreamError`, timeout→`UpstreamTimeoutError`, refused/DNS→`UpstreamUnavailableError`, respuestas con shape inesperado→`UpstreamError`). `ping()` para readiness.

**Acceptance criteria:**
- [x] `forward()` envía exactamente el source dado y devuelve `ExtractorSuccess` en `200`.
- [x] Cada error downstream (422/500/timeout/refused/shape inválido) produce la excepción de dominio correcta (tabla §9.3 SPEC).
- [x] El `detail` propagado usa solo el mensaje `{error}` del Extractor; nunca loguea ni reenvía el cuerpo completo a cliente.
- [x] `ping()` lanza `UpstreamUnavailableError` solo en fallas de red; cualquier status HTTP = reachable.

**Verification:**
- [x] Tests pass: `uv run pytest tests/unit` (incl. tests nuevos del cliente con `respx`). *(GREEN: 78 tests en `tests/unit/test_http_client.py`; suite completa del proyecto 123 passed)*
- [x] `uv run mypy -p bigpickle` y `uv run ruff check .` en limpio. *(sobre el paquete y la suite; `ruff format --check src tests` limpio)*

**Estado:** 🟢 GREEN. `downstream/{__init__,base,models,http_client}.py` implementados. `errors.py` y `settings.py` no requirieron cambios: la jerarquía de dominio (T2) y los timeouts/pool (T1) ya cubrían el contrato.

**Contrato fijado por los tests (decisiones que la descripción de la Task no explicaba):**
- **Firma:** `HttpExtractorClient(settings)` + `await aclose()`; `forward(source, *, content_type, content_length, request_id) -> ExtractorSuccess` y `ping() -> None` (plan §5.2). El cliente construye internamente el `SourceForwardingStream` con `max_upload_bytes` de settings (de ahí que `forward` reciba la `source`, no el stream).
- **`Content-Length`:** el cliente debe setear el header explícitamente cuando `content_length` no sea `None`, y omitirlo cuando sea `None` (httpx cae a `Transfer-Encoding: chunked`). Consecuencia directa del hallazgo R1 de T3. Aislado en `_build_headers()`.
- **`base_url` con slash final:** normalizado con `rstrip("/")`; sin eso, `BIGPICKLE_EXTRACTOR_BASE_URL=http://extractor:8000/` produce `//api/v1/extract` y httpx **no** normaliza (cubierto por `test_forward_does_not_duplicate_the_path_separator`, añadido en ciclo red→green durante el GREEN).
- **Traducción:** `httpx.TimeoutException` (cualquier subtype) → `UpstreamTimeoutError`; el resto de `httpx.RequestError` de red → `UpstreamUnavailableError`. `4xx` → `ExtractionFailedError` (cualquier `4xx`, no solo `422`: lo exige D4 y es la única excepción de dominio 4xx) y `5xx` → `UpstreamError`. `PayloadTooLargeError` del guard se propaga sin re-clasificar (no es `httpx.RequestError`, así que las cláusulas `except` no lo tocan).
- **Status inesperados:** cualquier status que no sea `200` (201/202/204/302…) → `UpstreamError`; solo `200` es éxito.
- **`detail`:** es exactamente el string de `{"error": …}` del Extractor. Si el body no lo trae, se usa un mensaje controlado — nunca el body crudo (verificado con un canario en el payload).
- **Tolerancia a evolución:** `ExtractorSuccess.from_payload` exige los 3 campos del SPEC §9.2 con su tipo, pero **ignora** campos extra (el Extractor puede evolucionar sin romper el contrato, coherente con D2).
- **Modelos:** `ExtractorSuccess`/`ExtractorError` son dataclasses frozen con `from_payload()`classmethod que valida el shape crudo y levanta `ValueError`; la traducción a excepción de dominio es responsabilidad exclusiva del cliente (SRP).
- **D6:** exactamente 1 llamada downstream por `forward()`, incluso ante `5xx` (sin reintentos).
- **`ping()`:** sondea `GET {BASE}/health` y colapsa **toda** falla de red —incluido timeout— a `UpstreamUnavailableError` (SPEC §6.3), a diferencia de `forward()`, que distingue timeout vs. inalcanzable.

**Desviación menor respecto del plan:** el diagrama §6 muestra `httpx.stream("POST", …)`; la implementación usa `client.post(…)`. Con `content=<AsyncByteStream>` httpx streamea el body igual en ambos casos, y `post()` evita el context manager extra. El body de *respuesta* sí se bufferiza, que es lo permitido explícitamente por §6.1 ("el cuerpo de respuesta (JSON del Extractor) es pequeño y se bufferiza en RAM").

**Correcciones aplicadas a los propios tests RED (2 bugs encontrados al ejecutar GREEN):**
1. `test_forward_never_leaks_the_raw_downstream_body` usaba el mismo canario en `error` (que SPEC §9.3 **sí** propaga) y en los campos que no, así que la aserción contradecía el contrato. Separado: canario solo en posiciones prohibidas + 2 casos extra.
2. `test_forward_sends_the_source_bytes_byte_identical` usaba un payload 3× mayor que `max_upload_bytes` (disparaba el guard que el propio test exige) y, al estar por debajo de 64 KB, nunca cruzaba una frontera de chunk. Reescrito con un cliente de `max_upload_bytes` propio y un payload de `2*64KB+1`, que sí ejercita el reassamblado multi-chunk.

**Medición para T6/T8 (coste real, no un defecto de diseño):** construir un `httpx.AsyncClient()` cuesta ~163 ms y `aclose()` ~58 ms en este entorno (carga del CA store por instancia). Son ~220 ms de overhead por test → la suite unit aria de 78 tests tarda ~11 s. En producción es un coste único de arranque (el lifespan debe crear **un** cliente y reutilizarlo — que es justamente el objetivo de D7), pero conviene tenerlo presente al añadir suites en T8.

**Dependencies:** Task 1, Task 3

**Files likely touched:**
- `src/bigpickle/infrastructure/http/downstream/{__init__,base,models,http_client}.py`
- `src/bigpickle/application/errors.py` (excepciones de dominio — ya completas de T2, sin cambios)
- `src/bigpickle/infrastructure/config/settings.py` (timeouts/pool — ya completos de T1, sin cambios)
- `tests/unit/test_http_client.py`

**Estimated scope:** Medium (4-5 archivos).

---

### Task 5: `ExtractionOrchestrator` (interfaz + implementación)

**Description:** Definir `ExtractionService` (Protocol) en `application/interfaces.py` e implementar `ExtractionOrchestrator` en `application/services/orchestrator.py`: generar `request_id`, medir `duration_ms`, construir `SourceForwardingStream` sobre la fuente recibida, llamar al `ExtractorClient.forward()` inyectado, y devolver `ExtractionResult` (`extracted_text`, `page_count`, `extraction_method`, `duration_ms`). No conoce HTTP ni DTOs de presentación.

> ⚠️ **Corrección al RED (no seguir el paso 3 a ciegas).** "Construir `SourceForwardingStream` sobre la fuente recibida" es imposible y está desmentido por ejecución real: `forward()` recibe una `AsyncByteSource` y `SourceForwardingStream` no implementa `read()`/`close()`. El orquestador reenvía la fuente intacta y el stream lo construye el cliente. Ver *Desviación respecto del plan* abajo.

**Acceptance criteria:**
- [x] Orquesta correctamente usando el cliente inyectado (fake en tests); produce `ExtractionResult` completo.
- [x] Propaga las excepciones de dominio sin transformarlas (las traduce la presentación).
- [x] Registra `duration_ms` ≥ 0; `request_id` único por llamada.
- [x] Inyectable: el mismo código funciona con fake y con `HttpExtractorClient` real.

**Cobertura de cada AC por los tests (21 tests en `tests/unit/test_orchestrator.py`):**
- **AC1** → `test_orchestrator_returns_a_complete_extraction_result` (set exacto de 4 claves), `..._calls_the_injected_client_exactly_once`, `..._relays_the_source_untouched`, `..._relays_the_request_metadata`, `..._accepts_an_unknown_filename`.
- **AC2** → `test_orchestrator_propagates_domain_errors_unchanged`, parametrizado sobre las **7** excepciones de `errors.py`, afirmando identidad (`caught.value is error`), no solo el tipo.
- **AC3** → `..._measures_duration_ms` (fake con `delay=0.05` ⇒ `>= 50 ms`, prueba que se *mide* y no se hardcodea), `..._reports_a_non_negative_integer_duration` (descarta `bool`), `..._generates_a_fresh_uuid4_request_id_per_call`, `..._delegates_request_id_generation_to_the_injected_factory`.
- **AC4** → `..._composes_with_the_real_http_client` y `..._surfaces_real_downstream_failures_as_domain_errors`, ambos con `HttpExtractorClient` real sobre `respx` (no fake).
- **Inyección obligatoria** → `..._requires_a_request_id_factory`.
- **Capa** → `..._never_imports_http_or_concrete_infrastructure`.

**Verification:**
- [x] Tests pass: `uv run pytest tests/unit/test_orchestrator.py`. *(GREEN: 21 passed; suite completa del proyecto **144 passed**)*
- [x] `uv run mypy bigpickle` y `uv run ruff check .` en limpio. *(`ruff check .` → All checks passed; `ruff format --check src tests` → 34 files already formatted; `mypy -p bigpickle` → Success, no issues in 24 source files)*

**Estado:** 🟢 GREEN. Implementados `infrastructure/tracing.py`, `application/services/__init__.py` y `application/services/orchestrator.py`; `interfaces.py` solo recibió `@runtime_checkable` sobre el Protocol que ya fijó T3. **Los 21 tests del RED pasaron sin modificación alguna**: cero cambios en `test_orchestrator.py` entre RED y GREEN, que es la prueba de que el RED describía el contrato y no la implementación.

**Cómo se sostiene cada principio SOLID (no como teoría, con dónde se verifica):**
- **SRP** — `tracing.py` solo genera ids; `orchestrator.py` solo secuencia el caso de uso; el mapeo a HTTP y a errores RFC 9457 sigue en presentación (T2/T6). Tres archivos, una razón para cambiar cada uno.
- **OCP** — Las dos únicas cosas que varían (cliente y fábrica de ids) son parámetros del constructor. Agregar otro cliente downstream no obliga a tocar el orquestador.
- **LSP** — `FakeExtractorClient` y `HttpExtractorClient` se inyectan en **el mismo** `ExtractionOrchestrator`. Lo prueban `..._composes_with_the_real_http_client` y `..._surfaces_real_downstream_failures_as_domain_errors` (cliente real sobre `respx`, no fake): es el AC4 con evidencia, no con una afirmación.
- **ISP** — El orquestador depende de `ExtractorClient` (2 métodos) y solo usa `forward()`. No toca `ping()`, que es del caso de uso de readiness (T7). Ningún cliente implementa métodos que no se usan.
- **DIP** — No construye nada: `grep` sobre el módulo solo encuentra `self._new_request_id = new_request_id` (asignación de referencia). Depende de la ABC `ExtractorClient` y de un `Callable[[], str]`, ambos abstracciones.

**Auditoría de capas ejecutada sobre `src/bigpickle/application/`:** el **único** import de infraestructura es `infrastructure.http.downstream.base` (el puerto). Cero `httpx`, cero `fastapi`, cero `presentation`, cero `config`. Coincide con la tabla de plan §3 y con el test `..._never_imports_http_or_concrete_infrastructure`, que lo verifica por AST en cada corrida en vez de dejarlo escrito en un docstring.

**graphify como apoyo al GREEN:** `graphify path "ExtractionOrchestrator" "HttpExtractorClient"` responde **`No directed path found between 'ExtractionOrchestrator' and 'HttpExtractorClient'`**, y `graphify explain "Dependency Inversion"` muestra que el nodo conecta solo con `ExtractorClient Interface` y `ExtractionService Interface`. El grafo ya representa la regla de capas y la implementación la respeta sin atajos. Nota honesta: el grafo está en commit `c4196ba`, anterior a T3/T4 — cubre `SourceForwardingStream`, `HttpExtractorClient` y `ExtractionOrchestrator` (este último vía `plan.md`), pero **`tracing`/`new_request_id` no aparecen**, correcto porque los archivos no existían. Conviene `/graphify --update` antes del Checkpoint B.

**Contrato fijado por los tests (decisiones que la descripción de la Task no explicaba):**
- **Firma:** `ExtractionOrchestrator(client=…, new_request_id=…)`, ambos **inyectados y obligatorios**; `async extract(source, *, filename, content_type, content_length) -> ExtractionResult`. Coincide con el Protocol `ExtractionService` que ya fijó T3, así que el GREEN solo necesita añadirle `@runtime_checkable` para poder verificar `isinstance` (test `..._satisfies_the_extraction_service_port`).
- **`new_request_id` inyectado, no importado:** la DIP manda sobre la conveniencia. La *interfaz* de cliente (`ExtractorClient`, ABC) sí está permitido importarla desde infraestructura —el propio plan §3 autoriza a la capa de aplicación conocer "infraestructura (interfaces de cliente)"—, pero `tracing.new_request_id` es una función concreta, y el orquestador no debe resolver por su cuenta ni el reloj ni el generador de ids. El cableado lo hará `deps.py` en T6. Beneficio comprobable: el test verifica que la fábrica inyectada se invoca **una vez por extracción** y que su valor llega al cliente.
- **La fuente se reenvía intacta; el orquestador NO construye el `SourceForwardingStream`.** La descripción de la Task pide lo contrario, pero es imposible: `forward()` (fijado en T4) recibe una `AsyncByteSource`, y `SourceForwardingStream` **no tiene `read()`/`close()`**, luego no satisface el port (verificado en runtime). Construir el stream es tarea del cliente, que es quien posee `max_upload_bytes`.
- **`filename` se acepta y no se usa:** `ExtractorService` lo expone (SPEC §6.1) pero §9.1 no manda ningún header de filename, así que no hay contrato de reenvío. Se cubre el fallback documentado `filename=""` (nota al pie de plan §6.1) sin afirmar un reenvío que el SPEC no define.
- **`ExtractionResult` es un `TypedDict`**, así que los tests acceden con `result["…"]`, nunca con atributo.
- **Test de arquitectura por AST:** `imported_modules()` parsea el módulo del orquestador y afirma que no importa `httpx`, `fastapi`, `…http.streaming` ni `…downstream.http_client`, y que **sí** importa `…downstream.base`. Cumplimiento ejecutable del "Nunca conoce de: FastAPI, HTTP, transporte" de plan §3, no una aspiración del docstring.

**Desviación respecto del plan (justificada empíricamente, no por gusto):**
El paso "construir `SourceForwardingStream` sobre la fuente recibida" de la descripción de T5 es incompatible con el contrato ya implementado y testeado en T4. La prueba `test_orchestrator_composes_with_the_real_http_client` lo demuestra: con el orquestador envolviendo la fuente, la ejecución real revienta con `AttributeError: 'SourceForwardingStream' object has no attribute 'read'` / `… has no attribute 'close'. Did you mean: 'aclose'?`. Por eso el contrato fija reenvío directo de la fuente. Además, envolver en la capa de aplicación filtraría un detalle de transporte (httpx `AsyncByteStream`) hacia arriba, violando la §3.

**Validación de la calidad de los tests: mutation testing.** Un RED que solo verifica "falla" no demuestra que los tests sirvan. Con un stub naive temporal se comprobó que cada test muerde:

| Mutante | Tests que lo matan |
|---|---|
| `duration_ms: 0` hardcodeado | 1 (`..._measures_duration_ms`) |
| `request_id="req-fijo"` en vez de uuid4 por llamada | 3 (incluye el end-to-end real, que detecta el header `x-request-id`) |
| Envuelve la fuente en `SourceForwardingStream` (lo que pide la Task al pie de la letra) | 5 (incluye el end-to-end real) |
| Envuelve los errores de dominio en `RuntimeError` | 8 (los 7 casos parametrizados + el end-to-end real) |

Los 4 mutantes mueren; los stubs se borraron después y el árbol quedó en RED real. Sin esta tabla, el GREEN podría haber "paseado" con una implementación que miente sobre la medición o sobre la propagación de errores.

**Refactor DRY incluido en la fase RED:** `ByteSource` (el doble de `AsyncByteSource`) estaba duplicado en `test_streaming.py` y `test_http_client.py` y esta task habría creado la **tercera** copia. Se extrajo a `tests/fakes.py` (con `read_sizes` y `drain()` para verificar que un cuerpo reenviado sigue siendo byte-idéntico) y se añadieron `tests/__init__.py` + `pythonpath` para poder importarlo como `from tests.fakes import ByteSource`. Suite completa verificada tras el refactor: 123 passed.

**Dependencies:** Task 3, Task 4

**Files likely touched:**
- `src/bigpickle/application/interfaces.py` (añadir `@runtime_checkable` al Protocol)
- `src/bigpickle/application/services/orchestrator.py` (+ `__init__.py`) — **aún no creado**
- `src/bigpickle/infrastructure/tracing.py` — **aún no creado**
- `tests/unit/test_orchestrator.py` — creado (RED)
- `tests/fakes.py`, `tests/__init__.py` — refactor DRY

**Estimated scope:** Medium (3 archivos).

### Checkpoint B (tras Tasks 3-5)
- [x] Streaming con guard verificado por tests; Prueba de humo R1 resuelta. *(T3)*
- [x] Cliente traduce todos los errores del Extractor a excepciones de dominio. *(T4)*
- [x] Orquestador produce `ExtractionResult` end-to-end con fake. *(T5)*
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