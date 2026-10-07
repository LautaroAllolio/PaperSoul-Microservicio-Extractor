# Implementation Plan: pdfextractor — Microservicio de Extracción (downstream)

**Estado:** Aprobado — pendiente de implementación (fase de ejecución).
**Especificación de referencia:** `SPEC-paperextractor.md` § 9 (contrato downstream) y el contrato fijado por los tests del orquestador (`tests/contract/`, `tests/unit/test_http_client.py`).
**Task list operativo:** `docs/tasks/todo-extractor.md`.
**Fecha:** 2026-10-07

> Este plan NO toca el plan del orquestador (`docs/tasks/plan.md` / `docs/tasks/todo.md`, completos). Describe el segundo microservicio del ecosistema PaperSoul: el **Extractor** real de PDFs, que el orquestador ya sabe llamar.

---

## 1. Overview

El repositorio alojará dos aplicaciones:

1. `src/paperextractor` — el **orquestador** (existente, completo, 203 tests verdes). Inalterado.
2. `src/pdfextractor` — **nuevo**: el **Extractor** de documentos, stateless, zero-disk, que recibe `multipart/form-data`, extrae el texto de un PDF con PyMuPDF y responde con el payload exacto que el orquestador valida (`extracted_text`, `extraction_method`, `page_count`).

El nuevo servicio se construye **en este repositorio**, en Python 3.12 / FastAPI / PyMuPDF, con concurrencia acotada, backpressure, métricas Prometheus y logs JSON estructurados. Es **stateless** (sin base de datos ni deduplicación) y conforma estrictamente el contrato downstream que el orquestador ya fija.

**Restricciones duras:**
- **Zero-disk:** el contenido del documento nunca se escribe a disco (el parser multipart de Starlette spoola >1 MB a `SpooledTemporaryFile`; por eso nunca se usa `UploadFile`).
- **Memoria acotada:** O(tamaño del documento), tope `MAX_UPLOAD_BYTES` (50 MB). "Memoria constante" no es posible para parsear un PDF; zero-disk + tope acotado sí.
- **SLO honesto:** el overhead *no-parse* (transporte + dispatch) apunta a p99 < 1 ms; el tiempo de parseo es otra métrica, separada y dominante.
- **No tocar el orquestador:** su código fuente y su suite de tests permanecen intactos.

---

## 2. Flujo de datos (Orquestador → Extractor → JSON)

```
Cliente ──multipart/form-data──▶ Orquestador (paperextractor:8000)      [sin cambios]
                                   │  valida transporte + guard 50 MB
                                   │  reenvía bytes crudos, Content-Type+boundary,
                                   │  Content-Length | chunked, X-Request-Id
                                   ▼
        Extractor (pdfextractor:8001)  POST /api/v1/extract
          1. request_id := X-Request-Id | uuid4
          2. multipart reader en memoria (bytearray acotado) ── 0 disco
             ├─ enforce MAX_UPLOAD_BYTES (backstop)        → 413 {"error"}
             └─ extrae solo el part "file"
          3. submit(bytes) → worker pool acotado (ProcessPoolExecutor)
             └─ asyncio.Semaphore(max_concurrent_extractions) (backpressure)
                ├─ saturado + queue timeout                → 503 {"error"}
          4. PyMuPDF: open(BytesIO) → join(page.get_text()) → page_count
             ├─ cifrado / corrupto / 0 páginas             → 422 {"error"}
             └─ sin texto extraíble                        → 422 {"error"}
          5. normalizar texto (NFC, colapsar \n{3,})
          6. 200 {"extracted_text","extraction_method":"pymupdf","page_count"}
             │  métricas + log JSON(request_id, duration, bytes, pages, outcome)
             ▼
Orquestador envuelve en su envelope propio ──▶ Cliente
```

---

## 3. Decisiones de Arquitectura

### D1. Segunda aplicación `src/pdfextractor`, misma distribución, extra opcional

`src/pdfextractor` es un paquete nuevo dentro de la misma distribución (`hatch` incluye ambos paquetes). Dependencias exclusivas del Extractor (`pymupdf`, `prometheus-client`, `python-multipart`) viven en `[project.optional-dependencies] extractor`, de modo que la imagen del orquestador no las arrastra.

**Por qué:** mínimo impacto sobre el orquestador verde; un solo repo, un solo CI. *(Alternativa a futuro: uv workspace con dos paquetes — se descarta ahora para no remover la base instalada del orquestador.)*

### D2. Parsing multipart en memoria (zero-disk) — **solo stdlib**

El router declara `request: Request` y **nunca** `File(...)`/`Form(...)`, así Starlette no invoca su `MultiPartParser` (que spoola a disco). El `multipart_reader` (Task 2) parsea el cuerpo en memoria **sin dependencias externas** (`python-multipart` queda **excluido**: el orquestador tiene un test de invariante, `test_the_extract_route_needs_no_multipart_parser_dependency`, que exige `find_spec("python_multipart") is None` y no puede romperse). Controlando ambos extremos (un único campo `file`), un parser mínimo en memoria con `bytes`/`boundary` de stdlib es viable y conserva la garantía zero-disk.

### D3. Memoria O(documento), acotada

PyMuPDF necesita los bytes completos y seekable del PDF. Pico de RSS ≈ tamaño del archivo (≤ `MAX_UPLOAD_BYTES`). Un pool acotado de `bytearray` (tamaño = concurrencia máxima) reusa buffers y reduce presión del allocator. **No se reclama memoria constante para el parseo**, solo acotada y sin disco.

### D4. Aislamiento CPU: process pool + semáforo acotado

La extracción corre en `ProcessPoolExecutor(workers)` (default `workers = cpu_count`), con `asyncio.Semaphore(max_concurrent_extractions)`. Saturado → fast-fail `503 {"error"}` (el orquestador lo traduce a `502 upstream-error`). Evita el GIL (MuPDF no lo libera de forma confiable). *Hilos y multi-worker uvicorn quedan como alternativas a validar en Fase 4.*

### D5. El idioma de error del Extractor es `{"error": str}`, no RFC 9457

El orquestador es dueño de RFC 9457. El Extractor habla el dialecto compacto downstream (§ 4) y deja la traducción al orquestador. Un único handler global mapea errores de dominio a `status` + `{"error": ...}`.

### D6. Presentación delgada

El endpoint solo adapta HTTP ↔ aplicación. Parsing en infraestructura; extracción detrás del puerto de aplicación; capas equivalentes a las del orquestador (presentation / application / infrastructure) con tests de capas por AST.

### D7. El test de contrato reusa el cliente real del orquestador

La prueba más fuerte de conformidad: levantar `pdfextractor` con `uvicorn.Server` en un puerto efímero dentro del test y conducirlo con el `HttpExtractorClient` + `ExtractionOrchestrator` **reales** del orquestador. Cero mocks en el camino crítico.

### D8. Idempotente por construcción

La extracción es función pura de los bytes; no hay estado. `X-Request-Id` es correlación (log + echo de header). Reintentos seguros; no hay "circuit breaking" en servidor (el cliente decide reintentar — el orquestador, por D6 de su plan, no reintenta POST).

---

## 4. Contrato del Extractor (exacto)

| Aspecto | Valor |
|---|---|
| Ruta | `POST /api/v1/extract` |
| Request | `multipart/form-data`, campo `file`, relicenciado verbatim (boundary original del cliente), header `X-Request-Id` |
| Encodado | `Content-Length` cuando venga; si no, `Transfer-Encoding: chunked` (soportar ambos) |
| `200` | `{"extracted_text": str, "extraction_method": "pymupdf", "page_count": int}` |
| `4xx` | `{"error": "<mensaje>"}` → orquestador `422 extraction-failed` |
| `5xx` | `{"error": "<mensaje>"}` → orquestador `502 upstream-error` |
| Liveness | `GET /health` → `200` siempre (para `ping()` del orquestador: cualquier status HTTP = reachable) |
| Readiness | `GET /ready` → `200` listo / `503` sobrecargado |
| Métricas | `GET /metrics` (exposición Prometheus text) |

Reglas de conformidad heredadas del cliente (`http_client.py`):
- Solo `200` es éxito. Cualquier otro status (201/204/302…) → `UpstreamError`. El Extractor debe devolver exactamente `200` en éxito.
- El `detail` que propaga el orquestador es el string de `{"error": ...}`. Si el body no trae `error` válido, el orquestador cae a un mensaje controlado — pero el Extractor siempre entrega `{"error": str}` legible.

---

## 5. Estrategia de Memoria y Concurrencia

- **Worker pool:** `ProcessPoolExecutor(workers)` creado en el lifespan del app factory, cerrado en shutdown con drenaje acotado.
- **Backpressure:** `asyncio.Semaphore(max_concurrent_extractions)` + `asyncio.wait_for(queue_timeout)`; overflow → `503`.
- **Techo de memoria:** `max_concurrent_extractions × (worker_rss + MAX_UPLOAD_BYTES)`.
  - Defaults recomendados: `workers = cpu_count`, `max_concurrent_extractions = workers`, `MAX_UPLOAD_BYTES = 52428800`.
- **Reuso de buffers:** `BufferPool` con `bytearray` + `memoryview`; reset de longitud por préstamo; sin sangrado entre requests (test dedicado).
- **Timeouts:** per-job `EXTRACTION_TIMEOUT_SECONDS` estrictamente por debajo del read-timeout del orquestador (120 s); al exceder → terminar worker + `504`-class `{"error"}`.

---

## 6. Observabilidad

**Métricas (`/metrics`):**
- `extractor_requests_total{status,method}` (contador)
- `extractor_extraction_seconds` (histograma, buckets de latencia de extracción)
- `extractor_upload_bytes` (histograma)
- `extractor_pages` (histograma)
- `extractor_inflight` (gauge)
- `extractor_queue_depth` (gauge)
- `extractor_worker_restarts_total` (contador)

**Logs JSON estructurados:** campos clave `request_id`, `duration_ms`, `bytes`, `pages`, `method`, `outcome`, `error_type`. Nunca se loguea el contenido del documento; el filename se loguea solo como longitud.

---

## 7. Configuración (prefijo `PDFEXTRACTOR_`)

| Variable | Default | Descripción |
|---|---|---|
| `PDFEXTRACTOR_HOST` | `0.0.0.0` | Host del servidor |
| `PDFEXTRACTOR_PORT` | `8001` | Puerto (local; en contenedor puede ser el de la red interna) |
| `PDFEXTRACTOR_MAX_UPLOAD_BYTES` | `52428800` | Tope de subida (50 MB, alineado con el guard del orquestador) |
| `PDFEXTRACTOR_MIN_TEXT_LENGTH` | `10` | Mínimo de caracteres para considerar extracción exitosa |
| `PDFEXTRACTOR_WORKERS` | `= cpu_count` | Tamaño del process pool |
| `PDFEXTRACTOR_MAX_CONCURRENT_EXTRACTIONS` | `= WORKERS` | Concurrencia máxima (semáforo) |
| `PDFEXTRACTOR_QUEUE_TIMEOUT_SECONDS` | `5` | Tiempo máximo en espera antes de `503` |
| `PDFEXTRACTOR_EXTRACTION_TIMEOUT_SECONDS` | `30` | Tope por job (< 120 s del orquestador) |
| `PDFEXTRACTOR_METRICS_ENABLED` | `true` | Monta `/metrics` |
| `PDFEXTRACTOR_LOG_LEVEL` | `INFO` | Nivel de logs |

---

## 8. Matriz de modos de fallo

| Condición | Responde el Extractor | Traduce el orquestador |
|---|---|---|
| PDF cifrado | `422 {"error":"no se pudo leer: cifrado"}` | `422 extraction-failed` |
| No-PDF / corrupto | `422 {"error":"no se pudo leer"}` | `422 extraction-failed` |
| Sin texto extraíble | `422 {"error":"sin texto extraíble"}` | `422 extraction-failed` |
| Falta el part `file` | `422 {"error":"campo file ausente"}` | `422 extraction-failed` |
| Excede tamaño (backstop) | `413 {"error":"archivo demasiado grande"}` | `422 extraction-failed` (normalmente dispara primero el guard del orquestador) |
| Pool saturado / queue timeout | `503 {"error":"overloaded"}` | `502 upstream-error` |
| Timeout por job | `504 {"error":"timeout"}` | `502 upstream-error` |
| Crash de worker | `500 {"error":"internal"}` | `502 upstream-error` |
| Cliente corta el stream | multipart truncado → `4xx` | `422` |
| Extractor caído | connection refused | `502 upstream-unavailable` |
| Extractor lento | no responde | `504 upstream-timeout` |

---

## 9. Riesgos y Mitigaciones

| # | Riesgo | Impacto | Mitigación |
|---|---|---|---|
| R1 | MuPDF retiene el GIL → thread pool no paraleliza | Med | Process pool (D4); validación empírica en Fase 4 |
| R2 | Overhead de copia de bytes hacia el worker del process pool | Med | Tope de upload; comparar threads/shared-memory tras benchmark |
| R3 | `python-multipart` como dependencia nueva | Bajo | Extra `extractor`; el orquestador no la importa |
| R4 | Dos apps en una misma distribución confunden packaging | Med | Extra opcional + entrypoint/dockerfile separados; workspace a futuro |
| R5 | "Sub-ms" malinterpretado como end-to-end | Med | SLO definido: overhead no-parse p99 < 1 ms; SLO de parseo separado |
| R6 | Scanner PDFs sin texto → OCR necesario | Bajo | Explícitamente fuera de alcance; puerto `TextExtractor` listo para agregarlo |
| R7 | Worker muere a mitad de extracción | Med | `Future` anotada, restart + métrica, `{"error"}` controlado |
| R8 | Sobre-suscripción de workers torpedea el host | Med | Semáforo + techos configurables; default = cpu_count |

---

## 10. Preguntas Abiertas (resueltas por defecto recomendado en esta versión)

1. **OCR (Tesseract) en alcance?** → **Fuera de alcance.** Puerto `TextExtractor` pluggable listo para una fase posterior (requiere deps de sistema).
2. **Modelo de workers** (process pool vs threads vs multi-worker uvicorn) → **Process pool por defecto**; decisión final con datos de la Fase 4.
3. **Packaging** (misma distribución + extra vs uv workspace) → **Misma distribución + extra** ahora; workspace a futuro.
4. **Puerto de métricas** → **Mismo puerto** (`/metrics`).
5. **`EXTRACTION_TIMEOUT_SECONDS`** → **30 s** por defecto, calibrable; < 120 s del orquestador.
6. **Dependencias nuevas en este repo** → **Sí**: `pymupdf`, `prometheus-client` en el extra `extractor`. `python-multipart` **queda excluido** (invariante del orquestador: su suite exige que no esté importable). *(Aceptado por el humano.)*

---

## 11. Estructura prevista de `src/pdfextractor`

```
src/pdfextractor/
├── __init__.py
├── main.py                            # app factory: create_app(settings|None); lifespan con pool
├── presentation/
│   ├── __init__.py
│   ├── schemas/
│   │   ├── __init__.py
│   │   └── document.py                # ExtractedDocument, ExtractionResponse, Health/Readiness
│   └── api/
│       ├── __init__.py
│       ├── deps.py                    # DI (get_extraction_service, get_request_id)
│       └── v1/
│           ├── __init__.py
│           └── extract.py             # POST /api/v1/extract (Request, sin UploadFile)
├── application/
│   ├── __init__.py
│   ├── interfaces.py                  # TextExtractor (Protocol), ByteSink, ExtractionService
│   ├── errors.py                      # PdfExtractorError → EmptyFileError, OversizedError,
│   │                                  # EncryptionError, UnreadableError, NoTextError,
│   │                                  # ExtractionTimeoutError, OverloadError, ...
│   └── services/
│       ├── __init__.py
│       └── extraction_service.py      # orquesta read→validate→extract→normalize
└── infrastructure/
    ├── __init__.py
    ├── config/
    │   ├── __init__.py
    │   └── settings.py                # Settings (pydantic-settings, prefijo PDFEXTRACTOR_)
    ├── http/
    │   ├── __init__.py
    │   └── multipart_reader.py        # parser multipart en memoria + guard de tamaño
    ├── memory/
    │   ├── __init__.py
    │   └── pool.py                    # BufferPool (bytearray + memoryview)
    ├── extraction/
    │   ├── __init__.py
    │   └── pymupdf_extractor.py       # implementa TextExtractor
    ├── concurrency/
    │   ├── __init__.py
    │   └── pool.py                    # ProcessPoolExecutor + semáforo + queue timeout
    ├── telemetry/
    │   ├── __init__.py
    │   ├── metrics.py                 # métricas Prometheus
    │   └── logging_.py                # formatter JSON
    └── middleware/
        ├── __init__.py
        └── middlewares.py             # request-id, size backstop, timeout, errores globales
```

---

## 12. Entregables

- `plan.extractor` → este documento.
- `todo.extractor` → hoja de ruta operativa (`docs/tasks/todo-extractor.md`).
- Código en `src/pdfextractor/` + tests en `tests/extractor/` + CI extendido + `docs/perf-report.md` (Fase 4).