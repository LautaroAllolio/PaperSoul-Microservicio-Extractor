# PaperExtractor

PaperExtractor es un microservicio HTTP que recibe documentos en formato `multipart/form-data` y los retransmite al *Extractor* downstream para su procesamiento. Sigue arquitectura hexagonal (Presentation → Application → Infrastructure), no almacena bytes del documento en disco, reléa el body por streaming byte a byte y devuelve respuestas con RFC 9457 para errores.

## Arquitectura

- **Streaming sin buffer**: el upload se reléa chunk a chunk usando `AsyncByteSource` (`RequestByteSource`), sin parsear el multipart completo en memoria.
- **Lifespan**: un único `HttpExtractorClient` (pool HTTP) por aplicación, creado al iniciar y cerrado al finalizar.
- **DI**: dependencias resueltas en `presentation/api/deps.py` (DIP). Routers solo conocen puertos.
- **Errores**: 4 handlers globales mapean errores de dominio a RFC 9457 (`application/problem+json`).

## Requisitos

- Python 3.12
- [uv](https://github.com/astral-sh/uv)

## Instalación

```bash
uv sync --dev
```

## Configuración

Variables de entorno (ver `.env.example`):

| Variable | Valor por defecto | Descripción |
|---|---|---|
| `PAPEREXTRACTOR_HOST` | `0.0.0.0` | Host del servidor |
| `PAPEREXTRACTOR_PORT` | `8000` | Puerto del servidor |
| `PAPEREXTRACTOR_EXTRACTOR_BASE_URL` | `http://extractor:8000` | Base URL del Extractor downstream |
| `PAPEREXTRACTOR_MAX_UPLOAD_BYTES` | `52428800` | Límite de subida (50 MB) |
| `PAPEREXTRACTOR_HTTP_TIMEOUT_CONNECT_SECONDS` | `5` | Timeout de conexión |
| `PAPEREXTRACTOR_HTTP_TIMEOUT_READ_SECONDS` | `120` | Timeout de lectura |
| `PAPEREXTRACTOR_HTTP_TIMEOUT_WRITE_SECONDS` | `120` | Timeout de escritura |
| `PAPEREXTRACTOR_HTTP_TIMEOUT_POOL_SECONDS` | `5` | Timeout de pool |
| `PAPEREXTRACTOR_HTTP_MAX_CONNECTIONS` | `100` | Conexiones máximas |
| `PAPEREXTRACTOR_LOG_LEVEL` | `INFO` | Nivel de logs |

## Ejecución

Modo desarrollo (Uvicorn):

```bash
uv run uvicorn paperextractor.main:app --host 0.0.0.0 --port 8000 --reload
```

Modo producción (sin reload):

```bash
uv run uvicorn paperextractor.main:app --host 0.0.0.0 --port 8000
```

## Contrato público

### `GET /health` (liveness)
Sin dependencias externas. Retorna 200:

```json
{
  "status": "ok",
  "service": "paperextractor",
  "version": "0.1.0",
  "timestamp": "2026-09-22T12:00:00Z"
}
```

### `GET /ready` (readiness)
Sondea al Extractor via `GET {EXTRACTOR_BASE_URL}/health` (usando `ExtractorClient.ping()`).

- 200: Extractor alcanzable
```json
{
  "status": "ready",
  "downstream": {"extractor": "reachable"},
  "timestamp": "2026-09-22T12:00:00Z"
}
```
- 503 + `application/problem+json`: Extractor no alcanzable (DNS/refused/timeout de red)
```json
{
  "type": "https://papersoul.dev/problems/upstream-unavailable",
  "title": "Upstream Unavailable",
  "status": 503,
  "detail": "The Extractor is unreachable",
  "instance": "/ready"
}
```

### `POST /api/v1/extract` (extracción)
Recibe `multipart/form-data` con un archivo. Responde 200 con envelope de PaperExtractor (nunca payload crudo del Extractor):

```json
{
  "request_id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
  "document": {
    "extracted_text": "Texto plano extraído del PDF...",
    "page_count": 4,
    "extraction_method": "pymupdf"
  },
  "orchestration": {
    "downstream_service": "extractor",
    "duration_ms": 142
  }
}
```

#### Ejemplo `curl`

```bash
curl -X POST http://localhost:8000/api/v1/extract \
  -H "Content-Type: multipart/form-data" \
  -F "file=@/ruta/contrato.pdf;type=application/pdf"
```

> Importante: el servidor reléa bytes tal cual llegan. En despliegues detrás de proxy inverso (Nginx/Ingress), debe desactivarse el buffering de request para preservar streaming: `proxy_request_buffering off`.

## Flujo de streaming

1. FastAPI recibe `Request.stream()` (no `UploadFile`). 
2. `RequestByteSource` adapta a `AsyncByteSource` (chunk por chunk).
3. Preanálisis: lee ventana mínima para detectar filename (`sniff_multipart_filename`) sin almacenar todo el body.
4. `HttpExtractorClient.forward()` envía los bytes al Extractor usando streaming (nunca se reconstruye el multipart). 
5. Solo la respuesta JSON pequeña del Extractor se lee en memoria.
6. Respuesta final es el envelope propio de PaperExtractor.

## Mapa de errores (RFC 9457)

| # | Condición | Status | `type` | `title` |
|---|---|---|---|---|
| 1 | Falta `multipart/form-data`/`boundary` o campo requerido | 422 | `https://papersoul.dev/problems/invalid-request` | Invalid Request |
| 2 | Error validación esquema | 422 | `https://papersoul.dev/problems/validation-error` | Validation Error |
| 3 | Archivo excede `MAX_UPLOAD_BYTES` | 413 | `https://papersoul.dev/problems/payload-too-large` | Payload Too Large |
| 4 | Extractor 422 | 422 | `https://papersoul.dev/problems/extraction-failed` | Extraction Failed |
| 5 | Extractor 5xx | 502 | `https://papersoul.dev/problems/upstream-error` | Upstream Error |
| 6 | Timeout (conexión/lectura) | 504 | `https://papersoul.dev/problems/upstream-timeout` | Upstream Timeout |
| 7 | Extractor inalcanzable (refused/DNS) | 502 | `https://papersoul.dev/problems/upstream-unavailable` | Upstream Unavailable |
| 8 | URL Extractor sin configurar | 500 | `https://papersoul.dev/problems/configuration-error` | Configuration Error |
| 9 | Excepción no controlada | 500 | `https://papersoul.dev/problems/internal-error` | Internal Error |

Todas las respuestas de error usan `Content-Type: application/problem+json` y `instance` = ruta del endpoint.

## Tests

```bash
# Unit + integration (sin contract)
uv run pytest -q

# Con contract tests (requiere Extractor real y EXTRACTOR_BASE_URL)
EXTRACTOR_BASE_URL=http://extractor:8000 uv run pytest -m contract -q
```

## Calidad

```bash
uv run ruff check .
uv run ruff format --check src tests
uv run mypy -p paperextractor
```

## Despliegue

- **Proxy inverso (Nginx/Ingress):** establecer `proxy_request_buffering off` para no interrumpir el streaming del upload.
- **Límites:** ajustar `PAPEREXTRACTOR_MAX_UPLOAD_BYTES` acorde a recursos.
- **Readiness/Liveness:** usar `/ready` para health checks de dependencias y `/health` para liveness.
