# Integración Extractor ↔ Orquestador — Guía de validación E2E

Este documento describe cómo validar el flujo de punta a punta: el **Orquestador**
(`PaperSoul-Microservicio-Orquestador`, Go) recibe un PDF por
`POST /api/v1/documents/process`, lo valida y reenvía el binario al **Extractor**
(este repositorio, Python/FastAPI) por `POST /api/v1/extractions` (multipart),
que devuelve el texto extraído.

## Contrato de integración

| Aspecto | Valor |
|---|---|
| Variable en el orquestador | `EXTRACTOR_URL` (requerida al arrancar) |
| Base URL de despliegue | `http://extractor:9000` (nombre de servicio `extractor`, puerto `9000`) |
| Endpoint del extractor | `POST /api/v1/extractions` |
| Tipo de request | `multipart/form-data`: campos `checksum` (primer), `file`; header `X-Document-Checksum` |
| Correlación | Se honra `X-Correlation-Id` (fallback `X-Request-Id`) y se ecoa |
| Respuesta `200` | `{"extracted_text": "...", "extraction_method": "pymupdf", "page_count": N}` (3 claves exactas) |

> El endpoint del orquestador es `POST /api/v1/documents/process`. La respuesta
> **no** incluye el texto: devuelve `documentId`, `status`, `checksum` y
> `metadata`. El texto queda en Persistencia.

## Imagen Docker del extractor

Construir desde la raíz de este repositorio:

```bash
docker build -t pdfextractor:latest .
```

La imagen escucha en `:9000` (`PDFEXTRACTOR_PORT=9000`) y trae configurado
`PDFEXTRACTOR_MIN_TEXT_LENGTH=0` para que un PDF escaneado sin capa de texto
sea válido (200 con texto vacío), como espera el orquestador.

## Levantar el stack (orquestador + extractor)

El orquestador exige sus downstream al arrancar (`EXTRACTOR_URL` y
`PERSISTENCE_URL`). Para que la red del compose resuelva el nombre `extractor`,
el servicio debe llamarse `extractor` y estar en la misma red que el orquestador.
Fragmento de referencia para el compose del stack (NO modifica el repo del
orquestador):

```yaml
services:
  extractor:
    image: pdfextractor:latest
    restart: unless-stopped
    environment:
      PDFEXTRACTOR_PORT: 9000
      PDFEXTRACTOR_MIN_TEXT_LENGTH: "0"
    networks:
      - mired

  orchestrator:
    # ... imagen del orquestador ...
    environment:
      EXTRACTOR_URL: http://extractor:9000
      PERSISTENCE_URL: http://<persistencia>:<puerto>
    networks:
      - mired
```

```
docker compose up --build
```

## 1. Revisar logs de ambos contenedores

Descarta `connection refused`, timeouts y códigos 4xx/5xx del downstream:

```bash
docker compose logs -f orchestrator extractor
```

Señales de fallo de conexión y qué significan:

| Log | Causa |
|---|---|
| `connection refused` a `extractor:9000` | El nombre/puerto del servicio no coincide con `EXTRACTOR_URL` |
| `502 …:extractor-unavailable` | El extractor no responde `2xx` (comprobar que arrancó en `:9000`) |
| `504 …:downstream-timeout` | La extracción superó `EXTRACTOR_TIMEOUT` (default 30s) |
| Logs del extractor con `path: /api/v1/extractions` | Confirma que el orquestador llegó a la ruta correcta |

## 2. Prueba E2E con cURL

```bash
curl -X POST http://localhost:8080/api/v1/documents/process \
  -H "X-Correlation-Id: 3f2504e0-4f89-41d3-9a0c-0305e82c3301" \
  -F "file=@/ruta/a/tu/archivo.pdf"
```

### Respuesta esperada (200)

```json
{
  "documentId": "...",
  "status": "PROCESSED",
  "checksum": "<sha256 hex del binario>",
  "metadata": { "fileName": "...", "sizeBytes": 12345, "pageCount": 3, "encrypted": false }
}
```

Repetir con el mismo archivo devuelve `200` con `status: REUSED` (dedup por
checksum; no vuelve a llamar al extractor).

### Errores relevantes

| Status | `type` | Qué revisar |
|---|---|---|
| 502 | `urn:papersoul:orchestrator:extractor-unavailable` | Extractor caído o respondiendo no-2xx (path/URL mal alineados) |
| 504 | `urn:papersoul:orchestrator:downstream-timeout` | Extracción lenta; evaluar `EXTRACTOR_TIMEOUT` |
| 422 | `urn:papersoul:orchestrator:invalid-pdf*` | Es el orquestador rechazando (magic bytes/estructura/cifrado); no es fallo del extractor |

## Verificación directa del extractor (sin orquestador)

```bash
curl -i -X POST http://localhost:9000/api/v1/extractions \
  -H "Content-Type: multipart/form-data; boundary=bnd" \
  -H "X-Correlation-Id: 3f2504e0-4f89-41d3-9a0c-0305e82c3301" \
  -F "checksum=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa" \
  -F "file=@/ruta/a/tu/archivo.pdf;type=application/pdf"
```

Esperado: `HTTP/1.1 200`, header `x-correlation-id` con el id enviado y body con
exactamente `extracted_text`, `extraction_method`, `page_count`.

> En despliegues detrás de proxy inverso mantener `proxy_request_buffering off`
> para preservar el streaming de subida.