# Contrato REST — pdfextractor (PDF → JSON)

Contrato estricto del microservicio `pdfextractor`. Todo requester debe atenerse a este documento; cualquier divergencia es un fallo de compatibilidad.

## Convenciones

- Base path: raíz (`/`). Host/puerto por defecto: `0.0.0.0:8001` (ver `PDFEXTRACTOR_HOST`/`PDFEXTRACTOR_PORT`).
- Errores: cuerpo `application/json` con la forma `{"error": "<mensaje acotado>"}` (ver [Errores](#errores)).
- Correlación: el header `X-Request-Id` se respeta si llega; si no, el servidor lo genera y lo devuelve.
- El servidor **jamás** escribe bytes del documento en disco y nunca muestra fragmentos del documento en logs.

## Endpoints

### `GET /health` — liveness

`200 OK` (sin dependencias):

```json
{
  "status": "ok",
  "service": "pdfextractor",
  "version": "0.1.0"
}
```

Contrato respaldado por el test de identidad (`tests/extractor/test_repository_identity.py`).

### `GET /ready` — readiness

- `200 OK`:
  ```json
  { "status": "ready" }
  ```
- `503 Service Unavailable`:
  ```json
  { "status": "not ready" }
  ```
  Se degrada a `503` bajo overload (todos los slots de concurrencia ocupados).

### `POST /api/v1/extractions` — extracción PDF → JSON

`multipart/form-data` con un campo `file` (el PDF). El orquestador antepone un
campo `checksum` (SHA-256 del binario); se ignora y el reader salta a `file`. No
se aceptan otros campos; el conteo de partes se resuelve con el primer `file`.

**Request**
- `Content-Type: multipart/form-data; boundary=...` (STDLIB/determinista).
- Correlación: se honra `X-Correlation-Id` (lo que reenvía el orquestador), con
  `X-Request-Id` como fallback; se ecoa bajo el mismo header.
- `X-Document-Checksum` (opcional): no es obligatorio; se tolera.
- Límite de tamaño: `PDFEXTRACTOR_MAX_UPLOAD_BYTES` (por defecto 50 MB), verificado mid-stream y como backstop por `Content-Length`.

**`200 OK`** — exactamente tres claves:

```json
{
  "extracted_text": "Texto plano extraído del PDF...",
  "extraction_method": "pymupdf",
  "page_count": 4
}
```

- `extracted_text`: texto plano del documento (≥ `PDFEXTRACTOR_MIN_TEXT_LENGTH` caracteres).
- `extraction_method`: motor de extracción (hoy siempre `pymupdf`).
- `page_count`: páginas del PDF.

**Semántica de concurrencia**
- Las extracciones corren en un process pool con tope `PDFEXTRACTOR_MAX_CONCURRENT_EXTRACTIONS`.
- Si todos los slots están ocupados y la cola agota `PDFEXTRACTOR_QUEUE_TIMEOUT_SECONDS` → `503`.

**Referencia `curl`**

```bash
curl -i -X POST http://localhost:8001/api/v1/extractions \
  -F "file=@/ruta/contrato.pdf;type=application/pdf"
```

> Este path (`/api/v1/extractions` plural) es el que invoca el orquestador
> (`internal/client/extractor.go`). Respuesta y campos multipart (`file` +
> `checksum`) calzan con su contrato tipado.

### `GET /metrics` — Prometheus (opcional)

`text/plain; version=0.0.4` con métricas del registry privado. Solo se monta si `PDFEXTRACTOR_METRICS_ENABLED=true` (por defecto).

## Errores

| HTTP | `{"error"}` | Condición |
|---|---|---|
| 413 | `archivo demasiado grande` | el `file` excede `PDFEXTRACTOR_MAX_UPLOAD_BYTES` (mid-stream o por `Content-Length`) |
| 422 | `multipart sin boundary` | falta `boundary` en `Content-Type` |
| 422 | `multipart inválido` | body no frameable / truncado |
| 422 | `campo file ausente` | no hay parte llamada `file` |
| 422 | `archivo vacío` | el `file` tiene 0 bytes |
| 422 | `no se pudo leer: cifrado` | PDF protegido por contraseña |
| 422 | `sin texto extraíble` | texto extraído < `PDFEXTRACTOR_MIN_TEXT_LENGTH` |
| 422 | `no se pudo leer` | PDF corrupto, no-PDF o sin páginas |
| 503 | `overloaded` | cola llena y timeout de cola agotado |
| 504 | `timeout` | extracción > `PDFEXTRACTOR_EXTRACTION_TIMEOUT_SECONDS` |
| 500 | `internal` | error interno no esperado (nunca con detalle interno) |

## Garantías de implementación

- **Zero-disk**: el body se encuadra en memoria (reader stdlib); no se importa `python-multipart` ni se usa `UploadFile`.
- **Memoria acotada**: `file` → `bytearray` de un pool acotado (capacidad = concurrencia); cota ≈ tamaño máx. del archivo concurrente.
- **Streaming**: el parseo se detiene al cerrar el `file`; no se drena el resto del body.
- **Respuesta compacta**: tres claves en `200`, una clave en errores — dialecto downstream (D5); RFC 9457 es responsabilidad del consumidor/orquestador aguas arriba si lo requiere.

## Referencias

- `docs/tasks/plan.md` — plan de unificación del repositorio.
- `tests/extractor/` — suite que respalda este contrato.