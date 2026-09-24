# Implementation Plan: BigPickle (Orquestador PaperSoul)

**Fase:** 2 de SDD (Planeación y Diseño de Capas)
**Especificación:** `SPEC-bigpickle.md`
**Task list operativo:** `tasks/todo.md`
**Fecha:** 2026-09-22

---

## 1. Overview

BigPickle orquesta la extracción de documentos del ecosistema PaperSoul. Bajo FastAPI, recibe un `multipart/form-data`, **reeenvía el cuerpo crudo en streaming** al microservicio Extractor (`POST /api/v1/extract`) sin materializarlo en disco, y responde al cliente con un envelope propio. Todos los errores son RFC 9457. Este documento define la arquitectura en 3 capas, las interfaces, el diseño del streaming y el orden de implementación.

---

## 2. Decisiones de Arquitectura

### D1. Streaming raw passthrough (sin parsear multipart) — *la decisión central*

**Elección:** BigPickle **no** usa `UploadFile` ni `python-multipart`. Expone el endpoint con `request: Request` y reenvía `request.stream()` byte a byte vía un adapter `httpx.AsyncByteStream`.

**Por qué:**
- **Cero disco garantizado.** Starlette/`python-multipart` spoola a disco archivos > 1 MB (`SpooledTemporaryFile`). El requisito "sin almacenamiento intermedio" se vuelve imposible de garantizar con `UploadFile`. Con streaming crudo, la única escritura posible es la del propio runtime (ninguna).
- **Memoria constante O(chunk)**, sin buffering del archivo completo.
- **Sin recodificación:** se reenvía el boundary original del cliente → cero riesgo de romper el contrato multipart.
- El Extractor es quien debe validar contenido; BigPickle solo valida transporte (content-type, tamaño).

**Trade-offs:** BigPickle no puede inspeccionar el archivo (no lo necesita — es orquestador); requiere un servidor que acepte request body en streaming/chunked (Uvicorn/h11 lo hace). Si en el futuro se necesita inspección, se migra a `UploadFile` con threshold de memoria (Ask first).

**Fallback (si httpx no cooperara con length):** enviar con `Transfer-Encoding: chunked` (sin `Content-Length`) — ver R1/R2.

### D2. Envelope de respuesta propio

BigPickle define `DocumentExtractResponse` (`request_id` + `document` + `orchestration`), desacoplado del `{extracted_text, extraction_method, page_count}` crudo del Extractor. Permite evolucionar bigote: agregar otros downstreams, tracing y metadatos sin romper clientes.

### D3. RFC 9457 como único idioma de error

Un único modelo `ProblemDetails` (`presentation/schemas/problems.py`) + 4 handlers globales (validation, HTTPException, dominio, excepción genérica). Los clientes downstream ven solo `title`/`detail` controlados; jamás stacktraces ni datos internos.

### D4. Regla de traducción de status

`4xx` del Extractor → `4xx` de BigPickle (culpa del cliente). `5xx`/red/timeout → `502`/`504` de BigPickle (culpa upstream). 413 por tamaño se corta en BigPickle (guard).

### D5. `Content-Length` forwards cuando se conoce

El adapter `SourceForwardingStream.get_content_length()` devuelve el `Content-Length` entrante si el cliente lo envió → el downstream recibe body con length (máxima compatibilidad con proxies). Sin length de entrada → chunked. (Verified en T3/T4.)

### D6. Sin reintentos automáticos en POST

Un POST reenviado puede duplicar el trabajo downstream. `retries=0` por defecto en el `AsyncClient`. El nivel de reintento (si acaso) es decisión del orquestador de más arriba, no de BigPickle.

### D7. Pool y timeouts configurables

`httpx.AsyncClient(limits=..., timeout=...)` con timeouts por fase (connect/read/write/pool) desde settings, para que un downstream lento no acapare workers de Uvicorn.

---

## 3. Arquitectura en 3 Capas (mapeo a entregables)

| Capa | Directorio | Responsabilidad | Conoce de | Nunca conoce de |
|---|---|---|---|---|
| **Presentación / Routers** | `src/bigpickle/presentation/` | Endpoints HTTP, DTOs de entrada/salida, handlers RFC 9457, inyección de dependencias | dominio (excepciones + interfaces) | httpx, config |
| **Lógica de Negocio / Servicios** | `src/bigpickle/application/` | Interfaz `ExtractionService`, orquestación, excepciones de dominio | infraestructura (interfaces de cliente) | FastAPI, HTTP, transporte |
| **Infraestructura / Clientes HTTP** | `src/bigpickle/infrastructure/` | Config (settings), `ExtractorClient` (httpx), adapter de streaming, tracing | nada hacia arriba | — |

**Regla de dependencia:** las flechas apuntan de Presentación → Aplicación → Infraestructura. La Aplicación depende de **interfaces** (Protocol/ABC), nunca de clases concretas de infraestructura. La Infraestructura implementa esas interfaces. Inversión de dependencia vía inyección en `presentation/api/deps.py`.

---

## 4. Árbol de Directorios del Proyecto

```
PaperSoul-Microservicio-Extractor/          # repo que aloja a BigPickle (ver SPEC §13.1)
├── SPEC-bigpickle.md
├── tasks/
│   ├── plan.md
│   └── todo.md
├── pyproject.toml                          # uv + [tool.pytest.ini_options] pythonpath=["src"]
├── .python-version                         # 3.12
├── .env.example
├── README.md
├── .github/workflows/ci.yml                # pytest + ruff + mypy (T8)
├── src/
│   └── bigpickle/
│       ├── __init__.py
│       ├── main.py                         # app factory; monta router v1 + handlers de error
│       ├── presentation/                   # CAPA 1: Presentación
│       │   ├── __init__.py
│       │   ├── api/
│       │   │   ├── __init__.py
│       │   │   ├── deps.py                 # create_extraction_service, get_settings (DI)
│       │   │   └── v1/
│       │   │       ├── __init__.py
│       │   │       ├── extract.py          # POST /api/v1/extract
│       │   │       └── health.py           # GET /health, GET /ready
│       │   ├── errors/
│       │   │   ├── __init__.py
│       │   │   └── handlers.py             # 4 handlers → RFC 9457
│       │   └── schemas/
│       │       ├── __init__.py
│       │       ├── document.py             # DocumentExtractResponse, ExtractedDocument, ...
│       │       ├── health.py               # HealthResponse, ReadinessResponse
│       │       └── problems.py             # ProblemDetails, ProblemDetailError
│       ├── application/                    # CAPA 2: Lógica de Negocio
│       │   ├── __init__.py
│       │   ├── interfaces.py               # AsyncByteSource (Protocol), ExtractionResult,
│       │   │                               # ExtractionService (Protocol)
│       │   ├── errors.py                   # BigPickleError, PayloadTooLargeError, InvalidRequestError,
│       │   │                               # ExtractionFailedError, UpstreamError,
│       │   │                               # UpstreamTimeoutError, UpstreamUnavailableError,
│       │   │                               # ConfigurationError
│       │   └── services/
│       │       ├── __init__.py
│       │       └── orchestrator.py         # ExtractionOrchestrator (implementa ExtractionService)
│       └── infrastructure/                 # CAPA 3: Infraestructura
│           ├── __init__.py
│           ├── config/
│           │   ├── __init__.py
│           │   └── settings.py             # Settings (pydantic-settings, prefijo BIGPICKLE_)
│           ├── http/
│           │   ├── __init__.py
│           │   ├── downstream/
│           │   │   ├── __init__.py
│           │   │   ├── base.py             # ExtractorClient (ABC): forward(), ping()
│           │   │   ├── models.py           # ExtractorSuccess(raw), ExtractorError(raw)
│           │   │   └── http_client.py      # HttpExtractorClient (implementación httpx)
│           │   └── streaming.py            # SourceForwardingStream(httpx.AsyncByteStream)
│           │                               #   + guard de tamaño (PayloadTooLargeError)
│           └── tracing.py                  # new_request_id()
├── tests/
│   ├── conftest.py                         # fixtures: app, AsyncClient(ASGITransport), respx mock
│   ├── unit/
│   │   ├── test_schemas.py
│   │   ├── test_problems.py                # mapa RFC 9457 (handlers → body esperado)
│   │   ├── test_settings.py
│   │   ├── test_streaming.py               # guard de tamaño, chunking, caps
│   │   └── test_orchestrator.py            # servicio con cliente fake
│   ├── integration/
│   │   ├── test_extract_flow.py            # happy path byte-identical
│   │   ├── test_error_mapping.py           # cada caso del mapa §8.1
│   │   ├── test_no_disk.py                 # verificación de cero escritura
│   │   └── test_health.py
│   └── contract/
│       └── test_extractor_contract.py      # marcador `-m contract`, contra Extractor real
└── .venv/                                  # (omitió)
```

---

## 5. Interfaces y Contratos Internos

### 5.1 `application/interfaces.py`

```python
from typing import Protocol, TypedDict

class AsyncByteSource(Protocol):
    """Fuente de bytes consumible en chunks (abstrae request.stream(), BytesIO, tests)."""
    async def read(self, size: int = -1) -> bytes: ...
    async def close(self) -> None: ...

class ExtractionResult(TypedDict):
    extracted_text: str
    page_count: int
    extraction_method: str
    duration_ms: int

class ExtractionService(Protocol):
    async def extract(
        self,
        source: AsyncByteSource,
        *,
        filename: str,
        content_type: str,
        content_length: int | None,
    ) -> ExtractionResult: ...
```

### 5.2 `infrastructure/http/downstream/base.py` — cliente abstracto

```python
class ExtractorClient(ABC):
    """Abstracción del downstream Extractor. Implementación real: HttpExtractorClient."""

    @abstractmethod
    async def forward(
        self,
        source: AsyncByteSource,
        *,
        content_type: str,
        content_length: int | None,
        request_id: str,
    ) -> ExtractorSuccess: ...

    @abstractmethod
    async def ping(self) -> None:
        """Lanza UpstreamUnavailableError si el Extractor es inalcanzable."""
```

### 5.3 `application/errors.py` — excepciones de dominio

Jerarquía: `BigPickleError(Exception)` → cada tipo del mapa §8.1 de la SPEC. Cada excepción expone `status`, `problem_type`, `title`, `detail` para que el handler los use. El `source` (chunk de red) nunca entra en mensajes.

### 5.4 `infrastructure/http/streaming.py` — primitiva de streaming

```python
class SourceForwardingStream(httpx.AsyncByteStream):
    def __init__(self, source: AsyncByteSource, *, max_bytes: int,
                 chunk_size: int = 64 * 1024) -> None:
        ...

    def get_content_length(self) -> int | None:
        # contenido provisto por el cliente (Content-Length entrante) o None → chunked

    async def __aiter__(self) -> AsyncIterator[bytes]:
        # itera source.read(chunk_size) contando bytes acumulados
        # si acumulado > max_bytes: levantEn PayloadTooLargeError (corta el stream)
```

Invariante de diseño: el cable de streaming **solo** produce bytes; el guard de tamaño vive dentro del iterador para abortar lo antes posible sin buffering.

---

## 6. Diseño del Flujo de Streaming (sin persistencia en disco, sin bloqueos de I/O)

```
Cliente ── POST /api/v1/extract (multipart, campo "file") ──────────►  Uvicorn
   ▲                                                                     │ h11 parsea SOLO el frame HTTP
   │                                                                     ▼
   │        Content-Type: multipart/form-data; boundary=…            router.extract (presentation)
   │        Content-Length (opcional)                                 request: Request (sin UploadFile)
   │                                                                     │  metadatos: ct, len, filename (header Content-Disposition)¹
   │                                                                     ▼
   │                                                     deps.create_extraction_service()  (DI)
   │                                                                     ▼
   │                                                     ExtractionOrchestrator.extract()  (application)
   │                                                                     │  1. new_request_id() → X-Request-Id
   │                                                                     │  2. mide duration_ms
   │                                                                     │  3. construye SourceForwardingStream sobre source
   │                                                                     └───────────────────────────────►
   │                                                                         adapter (chunks de 64 KB)     │
   │                                                                                                        ▼
   │                                                  HttpExtractorClient.forward() (infrastructure)
   │                                                    httpx.stream("POST", "{BASE}/api/v1/extract",
   │                                                       content=SourceForwardingStream,
   │                                                       headers={Content-Type: ct original,
   │                                                                Content-Length: len entrante (si hay),
   │                                                                X-Request-Id})
   │                                                     AsyncClient(limits=pool, timeout=por fase)
   │                                                                                                        ▼
   │                                                              {EXTRACTOR_BASE_URL}/api/v1/extract
   │                                                              parsea multipart → pymupdf
   │                                                                    200: {extracted_text, method, page_count}
   │                                                                    422/500: {"error": …}
   │                                                                                        │  JSON pequeño (buffer en RAM)
   │                                                        ┌───────────────────────────────┤
   │                                                        ▼                               ▼
   │                                             éxito → ExtractionResult         error → excepción de dominio
   │                                                        │                               │
   │                                                        ▼                               ▼
   │                              envelope + duration_ms 200         handlers RFC 9457 (422/502/504) → problem+json
   │                                                                         │
   └───────────────────────────────── respuesta al cliente ◄─────────────────┘
```

### 6.1 Pasos clave y garantías

1. **Recepción (Uvicorn):** el frame HTTP se lee por partes; **nunca** se spoola multipart a disco porque nunca se invoca el parser multipart de Starlette. El router usa `Request` y construye un `AsyncByteSource` sobre `request.stream()`.
2. **Guard de tamaño:** el iterador del adapter cuenta octetos. Al superar `BIGPICKLE_MAX_UPLOAD_BYTES` eleva `PayloadTooLargeError` → corta el envío → `413 problem+json`. El corte ocurre *durante* el streaming, sin haber leído todo.
3. **Forwarding 1:1:** mismos bytes, mismo `Content-Type` (se reenvía el `multipart/form-data; boundary=…` del cliente), `Content-Length` igual si el cliente lo mandó. `X-Request-Id` para trazabilidad.
4. **Memoria/IO:** cola de lectura de 64 KB; el `AsyncClient` de httpx con pool acotado evita agotar descriptores; el cuerpo de respuesta (JSON del Extractor) es pequeño y se bufferiza en RAM. Nunca hay *blocking I/O*: todo es `async read/write`.
5. **Traducción:** la respuesta del Extractor se mapea según la regla D4 → `200` envelope o RFC 9457.

¹ *filename*: se lee del primer chunk de cabecera multipart sin parsearlo completo (preanálisis ligero); si el preanálisis no puede extraerlo, se envía `""` (el downstream es la autoridad).

### 6.2 Qué NO hace BigPickle (límites del ftujo)

- No materializa `UploadFile`, no toca `tempfile`, no escribe nada.
- No re-ensambla el multipart (conserva el boundary original).
- No bufferiza el archivo en memoria; solo el JSON de respuesta.
- No reintenta POST (D6).

---

## 7. Configuración (variables de entorno, prefix `BIGPICKLE_`)

`.env.example`:

```dotenv
BIGPICKLE_HOST=0.0.0.0
BIGPICKLE_PORT=8000
BIGPICKLE_EXTRACTOR_BASE_URL=http://extractor:8000
BIGPICKLE_MAX_UPLOAD_BYTES=52428800        # 50 MB
BIGPICKLE_HTTP_TIMEOUT_CONNECT_SECONDS=5
BIGPICKLE_HTTP_TIMEOUT_READ_SECONDS=120
BIGPICKLE_HTTP_TIMEOUT_WRITE_SECONDS=120
BIGPICKLE_HTTP_TIMEOUT_POOL_SECONDS=5
BIGPICKLE_HTTP_MAX_CONNECTIONS=100
BIGPICKLE_LOG_LEVEL=INFO
```

---

## 8. Dependencias Técnicas y Versiones

```
fastapi, pydantic>=2, pydantic-settings, httpx>=0.27, uvicorn[standard]
dev: pytest, pytest-asyncio, respx, ruff, mypy
```

> `python-multipart`: **excluido** (D1). Si Uvicorn/FastAPI lo exigiera por el simple hecho de declarar el router, configurar `RouterDefaults` para no parsear forms y verificar con `uv run pytest`.

---

## 9. Hoja de Ruta de Implementación

Orden de construcción (dependencias hacia abajo; alto riesgo primero):

| # | Tarea | Depende de | Archivos ~ | Támaño |
|---|---|---|---|---|
| 1 | Scaffold `uv` + esqueleto de capas + settings | — | 5-6 | M |
| 2 | Schemas Pydantic + RFC 9457 (handlers) | T1 | 4-5 | M |
| 3 | Streaming sin disco (`SourceForwardingStream` + guard) | T1 | 2-3 | M |
| 4 | `HttpExtractorClient` (httpx) + mapeo de errores | T1, T3 | 4-5 | M |
| 5 | `ExtractionOrchestrator` (interfaz + impl) | T3, T4 | 3 | M |
| 6 | `POST /api/v1/extract` end-to-end + test de integración | T2, T5 | 4-5 | M |
| 7 | `GET /health` + `GET /ready` | T4 | 3 | S |
| 8 | Suite completa + CI (no-disk, byte-identical, RFC 9457) | T6, T7 | 5-8 | L→se divide |
| 9 | README + `.env.example` + limpieza lint/mypy | T8 | 2-3 | S |

> T8 es L: se implementa en dos sub-entregas (a: CI + tests de error/no-disk; b: tests de contract + cobertura final).

**Paralelizable (agentes/sesiones separadas):** T2 ∥ T3 (ambas dependen solo de T1). T8b puede correr en paralelo tras T8a.

---

## 10. Puntos de Control (checkpoints)

- **Checkpoint A (tras T1–T2):** `uv sync` limpio; schemas compilan; mapa RFC 9457 unit-test.
- **Checkpoint B (tras T3–T5):** adapter de streaming con guard verificado por unit; cliente mapea todos los errores downstream a excepciones de dominio; orquestador produce `ExtractionResult`. Revisión humana del streaming antes de exponer endpoints.
- **Checkpoint C (tras T6–T7):** extracción end-to-end con Extractor mockeado (byte-identical), `/health` y `/ready` funcionan. Revisión humana.
- **Checkpoint D (tras T8–T9):** CI verde completo; fechase de éxito del SPEC §12 cumplida; lista para revisión final.

---

## 11. Riesgos y Mitigaciones

| # | Riesgo | Impacto | Mitigación |
|---|---|---|---|
| R1 | httpx stream + `Content-Length` con body async no se comporta como se espera (`AsyncByteStream.get_content_length`) | Med | T3/T4 escriben primero una prueba de humo; si falla, fallback chunked (sin length) — comportamiento equivalente para el Extractor. |
| R2 | Proxies/balancers intermedios bufferizan request chunked (nginx `proxy_request_buffering on`) | Med | Documentación de despliegue: `proxy_request_buffering off`; `Content-Length` forwards cuando existe (D5) evita chunked en la mayoría de casos. |
| R3 | Errores a mitad de stream (cliente corta / guard de tamaño) dejan estados parciales downstream | Med | El guard aborta el iterador → httpx cierra la conexión; el Extractor rechaza cuerpos truncados (sigue siendo su cobertura). Tests de corte a mitad de flujo. |
| R4 | Timeout de lectura demasiado corto con PDFs lentos | Med | Read timeout configurable (120 s default, sección 7); `duration_ms` y logging permiten calibrar. |
| R5 | Mensajes del Extractor contienen ruido / datos sensibles | Bajo | BigPickle solo propaga `detail` acotado del `{error}`; nunca headers ni cuerpo completo. |
| R6 | `python-multipart` exigido de forma no intencional por FastAPI | Bajo | Agnostic; si se requiere parseo futuro, revisar límite de 1 MB de Starlette (spool a disco) — decisión consciente. |
| R7 | Puerto/url del Extractor mal configurado en despliegue | Alto | `/ready` + logs de arranque avisan; `ConfigurationError` si faltó `EXTRACTOR_BASE_URL`. |
| R8 | Workers de Uvicorn agotados por upstream lento | Med | Pool de conexiones y timeouts (D7); readiness para retirar tráfico; docs de escalado. |

---

## 12. Preguntas Abiertas (heredadas del SPEC §13)

1. Renombrar repo / README (actualmente dice "Extractor").
2. MIME whitelist: ¿solo `application/pdf` o cualquier contenido?
3. Confirmar comportamiento exacto de httpx/Uvicorn con chunked (se resuelve en T3/T4, riesgos R1/R2).
4. Auth y rate limiting en fase posterior.
5. Métricas Prometheus: ¿ahora o después?