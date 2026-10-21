# pdfextractor

Microservicio FastAPI de extracción **PDF → JSON**: recibe un PDF por `multipart/form-data`, lo procesa en memoria (pymupdf) y devuelve el texto plano extraído. Sigue arquitectura hexagonal (Presentation → Application → Infrastructure), **no escribe nada en disco**, mantiene un consumo de memoria acotado y habla un dialecto de errores compacto (`{"error": "..."}`).

## Características

- **Zero-disk, constante en memoria**: el body se encuadra en memoria (stdlib, sin `python-multipart`, sin `UploadFile`); los buffers se reutilizan vía pool acotado.
- **Cómputo aislado**: extracción en process pool con tope de concurrencia y cola; bajo overload responde `503 {"error": "overloaded"}`.
- **Límites**: tamaño máximo de upload, timeout de cola y timeout de extracción configurables.
- **Correlación**: header `X-Request-Id` (se hereda o se genera) y logs JSON estructurados.
- **Observabilidad**: `GET /metrics` (Prometheus, opcional) y `GET /health` + `GET /ready`.
- **Seguridad**: mensajes de error acotados; nunca se propagan detalles internos ni bytes del documento.

## Arquitectura

- **Hexagonal**: routers solo conocen puertos (`interfaces.py`); el servicio de aplicación define la frontera; la infraestructura (pymupdf, process pool, reader multipart, middleware) queda detrás.
- **Flujo**: `Request.stream()` → `read_multipart_file` (framing en `bytearray` del pool) → `ExtractionService` → `ProcessPoolTextExtractor` (pymupdf en worker) → respuesta `ExtractResponse`.
- **Gestión de recursos**: todo recurso (settings, pool, extractor, métricas) se crea en el *lifespan* de FastAPI y se cierra al terminar.
- **Errores**: handlers globales mapean excepciones de dominio a status + `{"error"}`; cualquier excepción no esperada degrada a `500 {"error": "internal"}`.

## Requisitos

- Python 3.12
- [uv](https://github.com/astral-sh/uv)

## Instalación

```bash
uv sync --dev
```

## Configuración

Variables de entorno (ver `.env.example`), todas con prefijo `PDFEXTRACTOR_`:

| Variable | Valor por defecto | Descripción |
|---|---|---|
| `PDFEXTRACTOR_HOST` | `0.0.0.0` | Host del servidor |
| `PDFEXTRACTOR_PORT` | `8001` | Puerto del servidor |
| `PDFEXTRACTOR_MAX_UPLOAD_BYTES` | `52428800` | Límite del `file` (50 MB) |
| `PDFEXTRACTOR_MIN_TEXT_LENGTH` | `10` | Mínimo de caracteres extraídos para considerar éxito |
| `PDFEXTRACTOR_WORKERS` | *(uno por CPU)* | Workers del process pool |
| `PDFEXTRACTOR_MAX_CONCURRENT_EXTRACTIONS` | *(= workers)* | Tope de extracciones concurrentes (pool + cola). `7` es el tuning de referencia **medido** para una instancia (≤200 rps); con réplicas co-ubicadas en un host usar `2` (ver *Escalado*) |
| `PDFEXTRACTOR_QUEUE_TIMEOUT_SECONDS` | `5.0` | Espera máxima en cola antes de `503` |
| `PDFEXTRACTOR_EXTRACTION_TIMEOUT_SECONDS` | `30.0` | Tope por extracción antes de `504` |
| `PDFEXTRACTOR_MAX_PAGES` | `1000` | Tope de páginas (anti decompression-bomb) → `422` |
| `PDFEXTRACTOR_MAX_EXTRACTED_CHARS` | `5000000` | Tope del texto extraído (anti decompression-bomb) → `422` |
| `PDFEXTRACTOR_WARMUP` | `true` | Precalienta `min(workers, max_concurrent)` workers al arrancar (evita el cold-start de `forkserver`) |
| `PDFEXTRACTOR_METRICS_ENABLED` | `true` | Monta `GET /metrics` |
| `PDFEXTRACTOR_LOG_LEVEL` | `INFO` | Nivel de logs |

## Ejecución

Modo desarrollo:

```bash
uv run uvicorn pdfextractor.main:app --host 0.0.0.0 --port 8001 --reload --no-access-log
```

Modo producción (sin reload):

```bash
uv run uvicorn pdfextractor.main:app --host 0.0.0.0 --port 8001 --no-access-log --http httptools
```

El servicio emite su propio log JSON estructurado por request; `--no-access-log`
evita la línea duplicada del access log de uvicorn (la entrada de contenedor
`python -m pdfextractor` ya lo desactiva).

Se recomienda `--http httptools` (parser HTTP en C/Cython, `httptools` es
dependencia de uvicorn) en lugar del parser `h11` por defecto escrito en Python
puro: reduce la CPU dedicada al parsing/framing de los cuerpos multipart y deja
más ciclos al event loop para el despacho de extracciones bajo carga alta. La
entrada de contenedor `python -m pdfextractor` ya lo activa.

## Contrato público

Documentación completa y estricta: [`docs/api-contract.md`](docs/api-contract.md).

| Endpoint | Método | Descripción |
|---|---|---|
| `/health` | GET | Liveness: `200 {"status":"ok","service":"pdfextractor","version":"..."}` |
| `/ready` | GET | Readiness: `200 {"status":"ready"}` o `503 {"status":"not ready"}` |
| `/api/v1/extractions` | POST | `multipart/form-data`, campo `file` → `200` con texto/duración/páginas |
| `/metrics` | GET | Prometheus text (solo si `PDFEXTRACTOR_METRICS_ENABLED=true`) |

> El path coincide con el del orquestador (`POST /api/v1/extractions`): acepta el
> multipart con los campos `checksum` + `file` y honra `X-Correlation-Id`.

Ejemplo `curl`:

```bash
curl -X POST http://localhost:8001/api/v1/extractions \
  -F "file=@/ruta/contrato.pdf;type=application/pdf"
```

> En despliegues detrás de proxy inverso (Nginx/Ingress), mantener `proxy_request_buffering off` para preservar el streaming de subida (el body nunca debe spoolearse a disco).

## Map de errores

Respuestas de error (siempre `Content-Type: application/json`):

| Condición | Status | `{"error"}` |
|---|---|---|
| `file` excede `PDFEXTRACTOR_MAX_UPLOAD_BYTES` | 413 | `archivo demasiado grande` |
| Falta boundary o body multipart inválido | 422 | `multipart inválido` / `multipart sin boundary` |
| Falta el campo `file` | 422 | `campo file ausente` |
| `file` presente pero vacío | 422 | `archivo vacío` |
| PDF cifrado con contraseña | 422 | `no se pudo leer: cifrado` |
| Texto extraído < `PDFEXTRACTOR_MIN_TEXT_LENGTH` | 422 | `sin texto extraíble` |
| PDF corrupto / no legible / 0 páginas | 422 | `no se pudo leer` |
| Más páginas que `PDFEXTRACTOR_MAX_PAGES` | 422 | `demasiadas páginas` |
| Texto extraído > `PDFEXTRACTOR_MAX_EXTRACTED_CHARS` | 422 | `texto excesivo` |
| Cola llena (overload) | 503 | `overloaded` |
| Tiempo de extracción excedido | 504 | `timeout` |
| Error interno no esperado | 500 | `internal` |

## Tests

```bash
# Suite completa (unit + integración del extractor)
uv run pytest -q

# Pruebas de techo de memoria (RSS acotado)
uv run pytest -m memory -q
```

## Calidad

```bash
uv run ruff check .
uv run ruff format --check src tests
uv run mypy -p pdfextractor
```

## CI (reproducible local)

`.github/workflows/ci.yml` corre exactamente estas gates; ejecutarlas a mano
localmente equivale a la validación del CI:

```bash
uv sync --dev                        # instalar dependencias (dev incluidas)
uv run ruff check .
uv run ruff format --check src tests
uv run mypy -p pdfextractor
uv run pytest -q
uv run pytest -m memory -q           # techo de memoria (RSS)
uv run --with pip-audit pip-audit    # escaneo de vulnerabilidades
docker build -t pdfextractor:ci .    # build de la imagen
```

## Despliegue

- **Proxy inverso (Nginx/Ingress):** `proxy_request_buffering off` para no spoolear el upload.
- **Health checks:** `/health` para liveness, `/ready` para readiness (refleja overload).
- **Escalado:** el límite de memoria lo fija `PDFEXTRACTOR_MAX_UPLOAD_BYTES` × `PDFEXTRACTOR_MAX_CONCURRENT_EXTRACTIONS`, ambos configurables.
- **Oversubscription de `uvicorn --workers N`:** cada worker de uvicorn es un proceso independiente y crea **su propio** process pool de `PDFEXTRACTOR_WORKERS` workers (por defecto, uno por CPU). Con `--workers N` el total de procesos de extracción es `N × workers_del_pool` — un footgun de memoria/rendimiento si ambos se sobredimensionan. Configuración recomendada: un solo worker uvicorn con `PDFEXTRACTOR_WORKERS` = CPUs, **o** varios workers uvicorn (`N`) con `PDFEXTRACTOR_WORKERS = 1` y escalado horizontal por réplicas de contenedor (el tope de concurrencia por proceso lo sigue fijando `PDFEXTRACTOR_MAX_CONCURRENT_EXTRACTIONS`). No hay código que compense esto: queda explícito en la configuración del operador.

### Escalado horizontal (múltiples réplicas)

El cuello de una instancia **no es la extracción** sino su *event loop* monohilo (framing HTTP +
parser multipart): medido con el arnés mixto a 400 rps, subir `PDFEXTRACTOR_MAX_CONCURRENT_EXTRACTIONS`
de 4 a 8 solo mueve el éxito de **39 % a ~46–51 %** (el `p90/p95/p99` queda clavado en el timeout de
cliente). El servicio sí es estable hasta su rodilla: **≤200 rps por instancia ≈ 99 % de éxito**
(250 rps → ~93 %; a 300 rps colapsa). Ver [`docs/report.md`](docs/report.md) §10.4–§10.5.

Por eso el escalado es **horizontal**: varias réplicas del contenedor detrás de un balanceador, no
`uvicorn --workers`. El extractor es **stateless** (cero disco, sin sesión), así que el balanceo es
round-robin y **no** necesita sticky sessions.

```
demanda_agregada (rps)  ──Traefik (round-robin)──▶  réplica 1 ─┐
                                                     réplica 2 ─┼─ cada una ≤200 rps
                                                     réplica k ─┘
```

**Fórmula de dimensionamiento:** `k = ceil(demanda_agregada_rps / 200)`.

| Demanda agregada | Réplicas (`k`) | RPS por réplica | Nota |
|---|---:|---:|---|
| 200 rps | 1 | 200 | una instancia basta (≈99 %) |
| 400 rps | 2 (mínimo) / **3 (recomendado)** | 200 / 133 | `k=3` tolera la caída de una réplica |
| 600 rps | 3 (mínimo) / 4 | 200 / 150 | `k=3` ya está a ~99 % por réplica; `k=4` da margen |

Medición de referencia (400 rps agregados, arnés mixto, réplicas co-ubicadas en un mismo host =
cota inferior): mono-instancia **39–51 %** de éxito con `p99 = 35 s` y miles de timeouts de
cliente; `k=2` **~78 %** (media de 4 corridas) y `k=3` **78.13 %**, ambas con `p99 ≈ 2 s` y
**cero** timeouts de cliente.

**Trade-off de las palancas de tuning:**

| Palanca | Efecto | Costo |
|---|---|---|
| `MAX_CONCURRENT_EXTRACTIONS` (`N`) ↑ | no sube el éxito bajo saturación (≤51 % a 400 rps); solo acorta la cola de extracción | +RSS por réplica (≈62 MiB por worker tibio) y CPU |
| réplicas `k` ↑ | **sí** sube el éxito agregado (39 % → ~78 %) y **corta el `p99` a ~2 s** | +RSS total (≈486 MiB por instancia) y +núcleos; una réplica menos = `demanda/k` menos de capacidad |
| pool pequeño por réplica (`WORKERS=2`, `MAX_CONCURRENT_EXTRACTIONS=2`) | deja CPUs libres para los event loops co-ubicados | menor throughput por réplica |

> Cuando las réplicas comparten host (`docker compose` con varias réplicas en la misma máquina),
> conviene un **pool pequeño por réplica** (`PDFEXTRACTOR_WORKERS=2`,
> `PDFEXTRACTOR_MAX_CONCURRENT_EXTRACTIONS=2`) y repartir las réplicas en hosts/núcleos
> distintos; con núcleos dedicados por réplica, `N=7` (el valor de `.env.example`) es el
> tuning de referencia.

### Integración con Traefik

- **Router**: un solo router por servicio, compartido por todas las réplicas (por eso el despliegue **no** fija `container_name` ni publica `ports:`; las réplicas se descubren por el Docker provider de Traefik en la red externa `mired`).
- **Balanceo**: round-robin, **sin sticky sessions** (el servicio es stateless). Regla de host típica: `Host(\`extractor.universidad.localhost\`)`; el servicio se expone en el puerto interno `9000`.
- **Healthcheck del balanceador**: `GET /health` (liveness; aislado del downstream), con `interval=10s` y `timeout=3s`.
- **Readiness**: `GET /ready` devuelve `200`/`503` según el estado de la instancia; un `503` debe sacar la réplica del balanceo (no solo del healthcheck).
- **Retry**: middleware de Traefik con `attempts=2` e `initialInterval=500ms`. Ojo: Traefik v3.5 **no** reintenta respuestas `5xx` ni `POST` con cuerpo (`retryNonIdempotentMethod`), así que el retry **no** es garantía de reejecución en otra réplica.
- **Red**: `mired` es **externa** (`external: true`); no la crea ni la destruye el despliegue del extractor.

Guía de validación extremo a extremo con el Orquestador: [`docs/integration.md`](docs/integration.md).
