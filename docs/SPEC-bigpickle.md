# SPEC: BigPickle — Microservicio Orquestador de Extracción (PaperSoul)

**Módulo:** `bigpickle`
**Estado:** Propuesta — pendiente revisión humana
**Fecha:** 2026-09-22
**Fase:** 1 de SDD (Especificación y Contratos). El plan de implementación vive en `tasks/plan.md` y `tasks/todo.md`.

---

## 1. Objetivo

BigPickle es el microservicio **orquestador** del ecosistema PaperSoul. Recibe un documento (PDF) por `multipart/form-data`, lo reenvía en **streaming de bytes** al microservicio downstream *Extractor* (sin almacenamiento intermedio en disco ni en memoria completa) y responde al cliente con un **envelope propio, estable y desacoplado** del payload interno del Extractor.

Todos los errores se exponen cumpliendo estrictamente **RFC 9457 (Problem Details for HTTP APIs)** con `Content-Type: application/problem+json`.

**Usuarios:** frontends y clientes corporativos que necesitan extraer texto plano de PDFs dentro del ecosistema PaperSoul.

**Éxito:** un PDF subido produce documento extraído en < N segundos, con memoria constante (O(chunk)) y cero escritura en disco del contenido del archivo, y toda condición de fallo (ilegible, upstream caído, timeout, archivo enorme) emite un Problem Details correctamente tipificado.

**Fuera de alcance de esta fase:** catálogo de servicios, historial de orquestaciones, autenticación/autorización, rate limiting, métricas Prometheus, otros downstreams además de Extractor. (Se deja el diseño abierto para incorporarlos.)

---

## 2. Supuestos

**Confirmados con el humano:**
1. Los artefactos (SPEC, plan, código) vivirán en este repositorio (`PaperSoul-Microservicio-Extractor`, ver Preguntas Abiertas #1).
2. Gestor de dependencias **`uv`** y **Python 3.12**.
3. BigPickle **wrapea** la respuesta del Extractor en su propio DTO (no passthrough directo).
4. Endpoints públicos en esta fase: **solo** extracción (`POST /api/v1/extract`) + **`GET /health`** y **`GET /ready`**.

**Asumidos técnicamente (corrígeme si no):**
5. El Extractor corre en `http://extractor:8000` (dirección externa a BigPickle, configurable por entorno).
6. El contrato downstream del Extractor es el provisto: `POST /api/v1/extract`, campo `file`, payloads de éxito y error indicados.
7. BigPickle **no parsea** el `multipart/form-data` del cliente con `python-multipart`: reenvía el flujo de bytes crudo (ver `tasks/plan.md`, decisión de arquitectura). Por eso el campo `file` nunca se materializa como objeto, solo se valida a nivel de transporte.
8. El runtime de despliegue es Uvicorn, que acepta *request bodies chunked* (`Transfer-Encoding: chunked`).
9. No se persiste nada del contenido del archivo en disco (requisito duro del negocio).

---

## 3. Tech Stack

| Componente | Elección | Versión objetivo |
|---|---|---|
| Lenguaje | Python | 3.12 |
| Framework HTTP | FastAPI | última estable (~0.115+) |
| Validación | Pydantic | v2.x |
| Configuración | pydantic-settings | 2.x |
| Cliente HTTP asíncrono | httpx (`AsyncClient`) | 0.27+ |
| Servidor | uvicorn[standard] | última estable |
| Gestor de paquetes | uv | última estable |
| Tests | pytest + pytest-asyncio | — |
| Mock HTTP | respx (mock de httpx) | — |
| Lint / formato | ruff | — |
| Tipado estático | mypy (estricto) | — |

> `python-multipart` NO se declara como dependencia: BigPickle no parsea multipart (requisito de streaming sin disco). Si en el futuro se necesita parsear, se agrega (Ask first).

---

## 4. Comandos

```bash
# Instalar dependencias
uv sync

# Servidor de desarrollo (reload)
uv run uvicorn bigpickle.main:app --reload --host 0.0.0.0 --port 8000

# Servidor de producción (n workers)
uv run uvicorn bigpickle.main:app --host 0.0.0.0 --port 8000 --workers 2

# Tests (unit + integration)
uv run pytest

# Tests incluyendo contrato contra Extractor real (requiere downstream levantado)
uv run pytest -m contract

# Lint
uv run ruff check .

# Formato
uv run ruff format --check .

# Tipado
uv run mypy bigpickle
```

---

## 5. Estructura del proyecto

```
src/bigpickle/            → código fuente (3 capas: presentation/application/infrastructure)
tests/                    → unit, integration, contract
tasks/                    → plan.md y todo.md (planificación SDD)
SPEC-bigpickle.md         → este documento
```

El árbol completo con responsabilidad por módulo está en `tasks/plan.md` § 5.

---

## 6. Contratos Públicos (API externa de BigPickle)

### 6.1 `POST /api/v1/extract` — Orquestar extracción de documento

**Request**
- `Content-Type: multipart/form-data; boundary=<boundary>`
- Campo único relevante: `file` (archivo, p. ej. PDF).
- Límite de tamaño configurable `BIGPICKLE_MAX_UPLOAD_BYTES` (default `52_428_800` ≈ 50 MB). Exceso → `413` Problem Details.
- BigPickle **no spoola a disco**: reenvía el cuerpo crudo aguas abajo (ver § 9 y `tasks/plan.md`).

**Response `200 OK` — `application/json`**

Envelope propio (desacoplado del payload del Extractor):

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

| Campo | Tipo | Descripción |
|---|---|---|
| `request_id` | `string(UUID)` | Trazabilidad; se propaga como `X-Request-Id` al downstream |
| `document.extracted_text` | `string` | Texto plano extraído |
| `document.page_count` | `int` | Nº de páginas |
| `document.extraction_method` | `string` | Método usado por el Extractor (`pymupdf`) |
| `orchestration.downstream_service` | `string` | `"extractor"` fijo |
| `orchestration.duration_ms` | `int` | Tiempo de orquestación medido en BigPickle |

**Errors** → siempre RFC 9457 (§ 8).

### 6.2 `GET /health` — Liveness

Sin dependencias externas. Responde siempre que el proceso está vivo.

```json
{
  "status": "ok",
  "service": "bigpickle",
  "version": "0.1.0",
  "timestamp": "2026-09-22T12:00:00Z"
}
```

### 6.3 `GET /ready` — Readiness

Comprueba alcanzabilidad del Extractor (probe HTTP corto sobre `{EXTRACTOR_BASE_URL}/health`).

```json
{
  "status": "ready",
  "downstream": { "extractor": "reachable" },
  "timestamp": "2026-09-22T12:00:00Z"
}
```

- `200` cuando el Extractor responde a nivel de red/HTTP.
- `503` + Problem Details (`type: upstream-unavailable`) cuando el probe falla por timeout/refused/DNS. Cualquier *status HTTP* (incluso 4xx/5xx) se considera "reachable" — solo falla de red = no listo.

---

## 7. Esquemas Pydantic (contrato de datos — Fase 1)

> Estos fragmentos son la **especificación** del contrato. Se materializarán en `presentation/schemas/` durante la implementación (fase posterior), no ahora.

```python
# presentation/schemas/problems.py — RFC 9457
from pydantic import BaseModel, ConfigDict

class ProblemDetailError(BaseModel):
    model_config = ConfigDict(frozen=True)
    loc: list[str]
    msg: str
    type: str

class ProblemDetails(BaseModel):
    model_config = ConfigDict(frozen=True)
    type: str = "about:blank"
    title: str
    status: int
    detail: str
    instance: str | None = None
    errors: list[ProblemDetailError] | None = None
```

```python
# presentation/schemas/document.py
from typing import Literal
from pydantic import BaseModel, ConfigDict

class ExtractedDocument(BaseModel):
    model_config = ConfigDict(frozen=True)
    extracted_text: str
    page_count: int
    extraction_method: str

class OrchestrationMetadata(BaseModel):
    model_config = ConfigDict(frozen=True)
    downstream_service: Literal["extractor"] = "extractor"
    duration_ms: int

class DocumentExtractResponse(BaseModel):
    model_config = ConfigDict(frozen=True)
    request_id: str
    document: ExtractedDocument
    orchestration: OrchestrationMetadata
```

```python
# presentation/schemas/health.py
from datetime import datetime
from typing import Literal
from pydantic import BaseModel

class HealthResponse(BaseModel):
    status: Literal["ok"]
    service: str
    version: str
    timestamp: datetime

class ReadinessResponse(BaseModel):
    status: Literal["ready"]
    downstream: dict[str, str]
    timestamp: datetime
```

**Entrada:** no hay DTO JSON de entrada. La entrada es `multipart`; la capa de presentación extrae solo metadatos (`filename`, `content_type`, `content_length`) y el *source* de bytes.

---

## 8. Contrato de Errores — RFC 9457

**Formato base (todas las respuestas de error):**

```json
{
  "type": "https://papersoul.dev/problems/<tipo>",
  "title": "…",
  "status": 422,
  "detail": "…",
  "instance": "/api/v1/extract"
}
```

`errors` se añade **solo** para errores de validación de esquema (array con `loc`, `msg`, `type`).
`instance` = ruta del endpoint que falló.

### 8.1 Mapa de errores interno

| # | Condición | `status` | `type` (suffix) | `title` |
|---|---|---|---|---|
| 1 | Carece de `multipart/form-data`, sin `boundary`, o campo requerido ausente | 422 | `invalid-request` | Invalid Request |
| 2 | Error de validación de esquema (Pydantic) | 422 | `validation-error` | Validation Error |
| 3 | Archivo excede `MAX_UPLOAD_BYTES` (guard de streaming) | 413 | `payload-too-large` | Payload Too Large |
| 4 | Extractor responde `422 {"error": …}` (archivo ilegible) | 422 | `extraction-failed` | Extraction Failed |
| 5 | Extractor responde 5xx (`500 {"error": …}`) | 502 | `upstream-error` | Upstream Error |
| 6 | Timeout de conexión/lectura hacia el Extractor | 504 | `upstream-timeout` | Upstream Timeout |
| 7 | Extractor inalcanzable (refused/DNS) | 502 | `upstream-unavailable` | Upstream Unavailable |
| 8 | URL de Extractor sin configurar | 500 | `configuration-error` | Configuration Error |
| 9 | Cualquier excepción no controlada | 500 | `internal-error` | Internal Error |

### 8.2 Handlers requeridos

1. `RequestValidationError` (Pydantic/FastAPI) → `validation-error`.
2. `HTTPException` (Starlette) → Problema HTTP genérico con su `status`.
3. Excepciones de dominio (`application/errors.py`) → casos 3–8.
4. Excepción no controlada (`Exception`) → `internal-error`.

Ejemplo de respuesta del caso 4:

```json
{
  "type": "https://papersoul.dev/problems/extraction-failed",
  "title": "Extraction Failed",
  "status": 422,
  "detail": "El documento no pudo ser procesado: archivo ilegible o sin texto extraíble",
  "instance": "/api/v1/extract"
}
```

---

## 9. Contrato de Comunicación con el Microservicio Extractor (downstream)

### 9.1 Llamada

```
POST {EXTRACTOR_BASE_URL}/api/v1/extract
Content-Type: multipart/form-data; boundary=<boundary del cliente>
Content-Length: <igual a la recibida, si el cliente la mandó>
X-Request-Id: <request_id de BigPickle>

<body: mismísimos bytes recibidos del cliente, sin re-codificar>
```

- El body es el **flujo de bytes inalterado**. No se re-ensambla multipart; el propio Extractor lo parsea.
- Si BigPickle no conoce el `Content-Length` de entrada (no venía), se envía con *transfer chunked* (soportado por Uvicorn/h11).

### 9.2 Payloads del Extractor

**Éxito `200 OK — application/json`**

```json
{
  "extracted_text": "Texto plano extraído del PDF...",
  "extraction_method": "pymupdf",
  "page_count": 4
}
```

**Error `422 / 500 — application/json`**

```json
{
  "error": "No se pudo extraer texto del PDF o archivo ilegible"
}
```

### 9.3 Traducción downstream → BigPickle (RFC 9457)

| Downstream devuelve | BigPickle responde al cliente |
|---|---|
| `200` + payload éxito validado | `200` + envelope propio (§ 6.1) |
| `422` + `{error}` | `422` `extraction-failed`; `detail` = mensaje del Extractor (caso 4) |
| `500` + `{error}` | `502` `upstream-error`; `detail` = mensaje del Extractor (caso 5) |
| timeout / DNS / connection refused | `504` `upstream-timeout` o `502` `upstream-unavailable` (casos 6–7) |

**Regla:** los `4xx` del Extractor se traducen a `4xx` (responsabilidad del cliente); los `5xx`/fallas de red a `5xx` de BigPickle (502/504). BigPickle nunca filtra detalles internos de red al cliente; acota a `title`/`detail` controlados.

---

## 10. Estrategia de Pruebas

**Framework:** pytest + pytest-asyncio; `respx` para mockear httpx; `ASGITransport` de httpx para pruebas end-to-end sin servidor real.

| Nivel | Dónde | Qué cubre |
|---|---|---|
| Unit | `tests/unit/` | Schemas, mapa RFC 9457, guard de tamaño, settings, mapeo de excepciones |
| Service | `tests/unit/test_orchestrator.py` | Orquestación con cliente fake: envelope, `duration_ms`, propagación de errores |
| Integration | `tests/integration/` | Flujo completo con Extractor mockeado (`respx`): happy path, cada caso de error, límite de tamaño |
| No-disk | `tests/integration/test_no_disk.py` | Verifica que el contenido del archivo nunca se escribe a disco durante una operación |
| Contract (opcional) | `tests/contract/` | End-to-end contra Extractor real (marcador `-m contract`) |

**Pruebas que NO pueden faltar (criterio de fondo):**
- **Byte-identical:** el body que recibe el Extractor mock son exactamente los bytes recibidos por BigPickle.
- **No-disk:** durante el flujo no se crea/abre ningún archivo temporal con contenido del documento (`tempfile`/tmp dir libres).
- **RFC 9457:** cada escenario de error responde `Content-Type: application/problem+json` con campos `type/title/status/detail/instance`.
- **Envelope:** el cliente nunca ve el payload crudo del Extractor.

---

## 11. Límites (Boundaries)

**Always:**
- Correr `uv run ruff check .`, `uv run mypy bigpickle` y `uv run pytest` antes de commitear.
- Un test nuevo por cada ruta de error nueva.
- `Content-Type: application/problem+json` en toda respuesta de error.
- Timestamps en UTC (`Z`).
- Nunca loguear contenido del documento ni secrets.

**Ask first:**
- Cambiar el contrato público o downstream (agregar/quitar endpoints o campos).
- Agregar dependencias (especialmente `python-multipart`).
- Cambiar config de CI, versiones de Python, o umbrales de tamaño/timeout.
- Cambiar la topología de despliegue (proxy con buffering, múltiples workers).

**Never:**
- Escribir el contenido del archivo en disco (cualquier directorio) — es el requisito duro.
- Commitear secretos / `.env` real.
- Eliminar o desactivar tests sin aprobación.

---

## 12. Criterios de Éxito (Fase 1 completa)

Cumplidos y verificables:

- [ ] `POST /api/v1/extract` extrae un PDF real end-to-end contra un Extractor (real o mockeado) y responde el envelope `200` definido.
- [ ] Los bytes enviados al downstream son byte-identical a los recibidos (test de integración).
- [ ] Ningún contenido de archivo se persiste en disco (test dedicado).
- [ ] Todos los escenarios del mapa § 8.1 emiten `application/problem+json` RFC 9457.
- [ ] `GET /health` responde sin dependencias; `GET /ready` responde `200` con Extractor arriba y `503` con Extractor caído.
- [ ] Memoria constante durante el streaming (sin buffering del archivo completo) — verificado por diseño del adapter y revisión.
- [ ] CI verde: ruff + mypy estrictos + pytest (unit, service, integration, no-disk).
- [ ] No se depende de `python-multipart`.

---

## 13. Preguntas Abiertas

1. **Nombre del repo:** este repo se llama `PaperSoul-Microservicio-Extractor` y el README aún dice eso, pero alojará a BigPickle. ¿Lo renombramos a `PaperSoul-Microservicio-Orquestador` / `PaperSoul-BigPickle` y actualizamos el README?
2. **MIME whitelist:** ¿forzar `application/pdf` en el campo `file`, o aceptar cualquier tipo que el Extractor pueda leer (p. ej. `image/tiff`)? (Default propuesto: validar solo que exista el campo y el límite de tamaño; la decisión de "¿es legible?" la toma el Extractor.)
3. **Compatibilidad de httpx:** confirmar en la implementación (tareas T3/T4) el comportamiento exacto de `httpx.AsyncByteStream` + `Content-Length` con body streaming, y el soporte de *chunked request* de Uvicorn. Mitigación documentada en `tasks/plan.md` (R1/R2).
4. **Auth / rate limiting:** ¿entran en una fase posterior?
5. **Métricas:** ¿se requiere `prometheus-client`/`/metrics` en esta iteración? (No incluido por defecto.)