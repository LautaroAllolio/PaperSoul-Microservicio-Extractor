# Performance report — `pdfextractor`

> **Estado (Task 12 — Load y soak testing):** el harness está implementado y
> verificado. **Las mediciones del servicio están PENDIENTES**: ver
> [§ 2.1 Bloqueo](#21-bloqueo-dependencias).
>
> Este archivo es compartido por la Task 11 (microbenchmarks), la Task 12 (load
> y soak, esta sección) y la Task 13 (profiling y tuning).

**Índice**

1. [Alcance](#1-alcance)
2. [Método](#2-método)
3. [SLOs y umbrales](#3-slos-y-umbrales)
4. [Configuración bajo prueba](#4-configuración-bajo-prueba)
5. [Curvas de rendimiento esperadas](#5-curvas-de-rendimiento-esperadas)
6. [Resultados](#6-resultados)
7. [Cómo reproducir](#7-cómo-reproducir)
8. [Ítems abiertos y calibración](#8-ítems-abiertos-y-calibración)
9. [Task 11 — Microbenchmarks (pendiente)](#9-task-11--microbenchmarks-pendiente)

---

## 1. Alcance

Task 12 del `docs/tasks/todo-extractor.md`: harness de carga que ejercita el
contrato downstream de `pdfextractor` (`docs/tasks/plan-extractor.md` § 4 y § 8)
y mide RPS, percentiles de latencia, comportamiento de backpressure y
estabilidad de RSS.

### 2.1 Bloqueo (dependencias)

La Task 12 depende de la **Task 6** (endpoint `POST /api/v1/extract`, `GET
/ready`) y de la **Task 9** (concurrencia, backpressure → `503`). Ninguna está
implementada. Sondeo del servicio levantado en el momento de escribir este
informe:

| Ruta | Status | Habilitada por |
|---|---|---|
| `GET /health` | **200** ✅ | Task 1 |
| `GET /ready` | **404** ❌ | Task 6 |
| `POST /api/v1/extract` | **404** ❌ | Task 6 |
| `GET /metrics` | **404** ❌ | Task 10 |
| `openapi.json` → `paths` | `["/health"]` | — |

`uv run pytest tests/extractor` → `7 passed` (solo Fase 0). Por lo tanto:

- Los escenarios *happy path* y *edge cases* no tienen ruta que ejercer.
- El escenario de saturación no puede validar `503`: no existe `asyncio.Semaphore`
  ni `OverloadError` que los produzca.

**Decisión:** se implementa y verifica el harness contra el contrato cerrado, y
la ejecución sobre el servicio real queda pendiente. No se reportan números de
latencia del servicio porque no se han medido.

---

## 2. Método

### 2.1 Herramienta

**k6 v2.3.0** (Go, scripting ES). Justificación frente a un cliente `httpx`
async en Python:

- Umrales declarativos (`thresholds`) evaluados por el propio runner → el exit
  code es directamente el gate de CI, sin código adicional.
- Percentiles p95/p99 nativos sobre métricas builtin y custom.
- Escenarios con `startTime`, que permite **secuenciar** las fases para que la
  saturación no contamine la medición del baseline.
- Curvas por tag (`http_req_duration{scenario:...}`) sin agregación manual.

### 2.2 Fases (secuenciales, no concurrentes entre sí)

| # | Escenario (`scenario`) | Executor | Objetivo |
|---|---|---|---|
| 1 | `happy_path` | `ramping-arrival-rate` | Baseline: RPS y percentiles bajo carga nominal. |
| 2 | `edge_cases` | `shared-iterations` | Oversized, cifrado, corrupto, sin texto, 0 páginas, vacío, campo ausente. |
| 3 | `saturation_extract` | `ramping-arrival-rate` | Rampa hasta `LOAD_SATURATION_RPS` (≫ capacidad) para forzar backpressure. |
| 4 | `health_probe` | `constant-vus` | Corre **en paralelo con la fase 3**: `/health` debe seguir en `200` y `/ready` debe degradar a `503`. |

Las fases 3 y 4 comparten `startTime` a propósito: es la forma de probar
"503 ante saturación **sin degradar otros requests**".

### 2.3 Fixtures

Generados en cada corrida por `tests/extractor/load/gen_fixtures.py` (nada
binario se commitea). Cada fixture lleva su expectativa en
`fixtures/manifest.json`, que es la fuente de verdad que consume k6 — el
harness no hardcodea estados esperados.

El comportamiento de cada fixture fue verificado contra PyMuPDF 1.28.2 antes de
asertarlo:

| Fixture | Tamaño | Comportamiento verificado | Status esperado |
|---|---|---|---|
| `valid_1p.pdf` | 4.2 KB | 1 página, 1214 chars | 200 |
| `valid_5p.pdf` | 55 KB | 5 páginas, 20226 chars | 200 |
| `valid_20p.pdf` | 328 KB | 20 páginas, 121361 chars | 200 |
| `no_text.pdf` | 836 B | 0 chars extraídos | 422 |
| `encrypted.pdf` | 3.5 KB | `needs_pass=1` | 422 |
| `corrupt.pdf` | 5.7 KB | `FileDataError` | 422 |
| `not_pdf.bin` | 1.0 KB | `FileDataError` | 422 |
| `zero_pages.pdf` | 150 B | `page_count=0` | 422 |
| `empty.pdf` | 0 B | `EmptyFileError` | 4xx (la spec no fija status) |
| `__oversized__` | límite + 256 KB | sintético, multipart válido | 413 |
| `__missing_field__` | sintético | part llamado `document` | 422 |

El body oversized se construye **dentro de k6**, no se escribe a disco. El
multipart se arma a mano (`FormData` no existe en k6) y el framing fue
verificado byte a byte: SHA-256 del part `file` idéntico al archivo fuente.

### 2.4 Instrumentación

| Métrica custom | Tipo | Qué mide |
|---|---|---|
| `backpressure_rejections` | Counter | Veces que el servicio respondió `503` bajo carga. Debe ser `> 0`. |
| `backpressure_fail_ms` | Trend | Latencia percibida por el cliente hasta el `503`. Debe ser ≈ queue timeout, no creciente. |
| `ready_overloaded` | Counter | Veces que `/ready` respondió `503` durante la saturación. |
| `ready_route_missing` | Counter | Veces que `/ready` respondió `404`. Debe ser `0`. |
| `health_ok_during_saturation` | Rate | `/health` = 200 durante la tormenta. Debe ser `1.0`. |
| `unexpected_status` | Counter | Status fuera del conjunto legal por escenario. Debe ser `0`. |

`unexpected_status` es el detector de regresiones duras: durante saturación el
único conjunto legal es `{200, 503}` — un `500`, `504` o un corte de conexión
lo incrementa y rompe el threshold.

---

## 3. SLOs y umbrales

Definidos en `tests/extractor/load/loadgen.js`. Todos son sobreescribibles por
variable de entorno para calibrar contra hardware real.

| # | Métrica | Umbral | Origen |
|---|---|---|---|
| T1 | `http_req_duration{scenario:happy_path}` | `p(95) < 500 ms` | Target (`LOAD_P95_SLO_MS`) |
| T2 | `http_req_duration{scenario:happy_path}` | `p(99) < 750 ms` | Target (`LOAD_P99_SLO_MS`) |
| T3 | `http_req_failed{scenario:happy_path}` | `< 1 %` | Requerido por Task 12 |
| T4 | `checks{scenario:happy_path}` | `> 99 %` | Exactitud del contrato § 4 |
| T5 | `checks{scenario:edge_cases}` | `> 99 %` | Exactitud de la matriz § 8 |
| T6 | `checks{scenario:saturation_extract}` | `> 95 %` | Solo `{200,503}` con body correcto |
| T7 | `http_req_duration{scenario:saturation_extract}` | `p(99) < queue_timeout + extraction_timeout` | Corte de colas indefinidas |
| T8 | `unexpected_status` | `count == 0` | Ningún status fuera de contrato |
| T9 | `backpressure_rejections` | `count > 0` | La backpressure **se activa** |
| T10 | `backpressure_fail_ms` | `p(95) < queue_timeout + 1000 ms`<br>`p(99) < queue_timeout + 2000 ms` | Plan § 9: "saturación responde 503 rápido" |
| T11 | `checks{scenario:health_probe}` | `> 99 %` | `/health` y `/ready` sobreviven |
| T12 | `health_ok_during_saturation` | `rate == 1` | Liveness intacta bajo carga |
| T13 | `ready_route_missing` | `count == 0` | `/ready` existe |
| T14 | `http_req_failed{scenario:health_probe,should_succeed:true}` | `< 1 %` | Error budget de `/health` |
| T15 | `http_req_failed{scenario:edge_cases}` | *(sin threshold)* | Los 4xx/413/503 son **deseados**; se validan por checks |
| T16 | RSS durante soak | ver § 6 | Estabilidad de buffers/workers |

**Nota sobre T15:** `http_req_failed` cuenta cualquier status ≥ 400. En los
escenarios *edge* y *saturation* el fallo es el resultado correcto, así que el
presupuesto de errores se mide solo sobre las requests que **deben** tener
éxito (`should_succeed:true` y los tags `scenario:happy_path` /
`scenario:health_probe`). Es la distinción que evita que la tasa de error real
quede oculta por los rechazos esperados.

---

## 4. Configuración bajo prueba

`tests/extractor/load/run.sh` arranca el servicio con valores deterministas
para que el punto de saturación sea reproducible:

| Variable | Default | Por qué |
|---|---|---|
| `PDFEXTRACTOR_MAX_CONCURRENT_EXTRACTIONS` | `4` | Fija la capacidad: `capacidad ≈ 4 / latencia_extraer`. |
| `PDFEXTRACTOR_QUEUE_TIMEOUT_SECONDS` | `2` | Reduce el default (5 s) para que la saturación se manifieste rápido. |
| `PDFEXTRACTOR_MAX_UPLOAD_BYTES` | `1048576` (1 MiB) | El guard de tamaño es independiente del valor: probarlo con 1 MiB en vez de 50 MiB mantiene al generador en ~1.3 MB/VU en vez de ~53 MB/VU. **Mismo camino de código, misma aserción.** |
| `LOAD_SATURATION_RPS` | `200` | Debe ser ≫ capacidad. Regla: `target_rps ≈ 3 × capacidad`. |
| `LOAD_HAPPY_RPS` | `20` | Por debajo de capacidad: el baseline no debe saturar. |

**Punto de saturación esperado:** con `max_concurrent_extractions = 4` y una
extracción de ~100 ms, la capacidad es ~40 req/s. A 200 req/s ofrecidos el
semáforo satura, el `asyncio.wait_for` expira a los `queue_timeout` y el
servicio responde `503`.

> ⚠️ El VU de k6 queda ocupado durante **todo** el queue timeout mientras
> espera, así que `maxVUs` del escenario de saturación se dimensiona como
> `rps × queue_timeout × 2`. Dimensionarlo mal hace que k6 descarte iteraciones
> (`dropped_iterations`) y la curva quede **subestimada**.

---

## 5. Curvas de rendimiento esperadas

> Este es el **modelo** contra el que se interpretará la primera medición real.
> No son datos medidos.

### 5.1 Latencia vs. RPS ofrecido

```
latencia
  (ms)
   │                              ╱  cola crece:
   │                            ╱     todo el mundo espera
   │ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─╱─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─
   │ p95 ──────────────── ╱      │
   │ p50 ───────────── ╱         │ ∅ en el pool:
   │                 ╱  │        │ sin espera
   │ ──────────────╱─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─
   │            ╱  │            │
   │         ╱     │            │
   └─────────┴─────────┴─────────┴──────────── rps ofrecido
              0      capacidad   >capacidad
                        ▲
                        rodilla (knee)
```

- **Región 1 (0 → capacidad):** latencia plana en ~p50 de parseo; el semáforo
  nunca espera. RPS servido ≈ RPS ofrecido.
- **Región 2 (en la rodilla):** el semáforo empieza a esperar; la cola crece
  hasta `queue_timeout`.
- **Región 3 (> capacidad):** el `503` se vuelve dominante. **La latencia de
  los `503` debe quedarse plana en ≈ `queue_timeout`** — si crece con el load,
  la cola no está acotada (justo lo que T10 vigila).

### 5.2 Composición de respuestas vs. RPS ofrecido

```
 %
100│█████████████████░░░░░░░░░░░░░░░░░░░░░
   │████████████████████░░░░░░░░░░░░░░░░░░   █ 200
   │███████████████████████░░░░░░░░░░░░░░░   ░ 503
   │█████████████████████████░░░░░░░░░░░░░
   │████████████████████████████░░░░░░░░░░
   └──────────┴────────────┴────────────── rps
              0         capacidad
```

- Antes de la rodilla: ~100 % `200`.
- Después: share de `503` → ~100 %, **sin** aparición de `5xx` ni cortes.
- Durante toda la fase, `/health` debe permanecer en `200` (T12) y `/ready`
  debe pasar a `503` y recuperarse a `200` al bajar la carga (T9/`ready_overloaded`).

### 5.3 RSS vs. tiempo (soak)

```
RSS
  │        ┌─────────────────────────  techo teórico:
  │       ╱                            max_concurrent × (worker_rss + MAX_UPLOAD_BYTES)
  │     ╱  rampa de calentamiento
  │   ╱    (pool + BufferPool llenándose)
  │ ╱────────────────────────────────  meseta: sin pendiente ⇒ sin fuga
  └─────────────────────────────────── tiempo
```

Criterio de aceptación (T16): tras el calentamiento, `RSS` no tiene pendiente
creciente; `RSS_final − RSS_calentamiento < 5 %` de `RSS_calentamiento`.

---

## 6. Resultados

### 6.1 Mediciones del servicio real — **PENDIENTE**

Bloqueado por las Tasks 6 y 9 (ver § 2.1). Rellenar al primer run verde sobre
el servicio:

| Fase | RPS servido | p50 | p95 | p99 | % 200 | % 503 | % error |
|---|---|---|---|---|---|---|---|
| `happy_path` @ 20 rps | — | — | — | — | — | — | — |
| `happy_path` @ 50 % capacidad | — | — | — | — | — | — | — |
| `edge_cases` | — | — | — | — | — | — | — |
| `saturation_extract` @ 5 rps | — | — | — | — | — | — | — |
| `saturation_extract` @ capacidad | — | — | — | — | — | — | — |
| `saturation_extract` @ 5× capacidad | — | — | — | — | — | — | — |

| Indicador | Objetivo | Medido |
|---|---|---|
| `backpressure_rejections` | `> 0` | — |
| `backpressure_fail_ms` p95 | `< queue_timeout + 1000 ms` | — |
| `/health` durante saturación | `100 %` en 200 | — |
| `/ready` durante saturación | observa ≥ 1 `503` | — |
| `unexpected_status` | `0` | — |
| `dropped_iterations` | `0` | — |
| RSS calentamiento → final | `< 5 %` de crecimiento | — |

### 6.2 Auto-verificación del harness (no son mediciones del servicio)

Corrida de verificación del propio harness contra un **doble de contrato
descartable** (`fastapi` + `asyncio.Semaphore` que implementa § 4/§ 8, fuera
del repositorio). Sirve para probar que escenarios, checks y thresholds
producen datos reales; **no** dice nada del rendimiento de `pdfextractor`.

Perfil `ci`, `LOAD_SATURATION_RPS=60`, 4 escenarios, ~26 s:

| Verificación | Resultado |
|---|---|
| Exit code k6 | **0** |
| Thresholds | **16 / 16 en verde** |
| Checks | **1046 / 1046 (100 %)** — los 8 tipos de aserción |
| `backpressure_rejections` | 58 (la backpressure se activó) |
| `ready_overloaded` | 21 (`/ready` degradó a `503`) |
| `health_ok_during_saturation` | 38 / 38 = **100 %** |
| `unexpected_status` | **0** |
| `dropped_iterations` | 28 (1.09/s) — ver § 8 |
| `http_req_failed{happy_path}` | **0.00 %** |
| `http_req_failed{health_probe, should_succeed}` | **0.00 %** |

Bugs encontrados y corregidos durante la verificación del harness (todos del
propio harness, no del servicio):

1. `exec` no se asignaba a los escenarios → todos caían sobre `default`.
2. `exec: 'happy_path'` no coincidía con el nombre exportado `happyPath`.
3. `__ITER` no existe en `setup()` → `ReferenceError` en el probe de contrato.
4. El probe de `/health` y `/ready` corría sin `sleep` → **1400 req/s** de
   sonda, que se convertía en la principal fuente de carga y invalidaba la
   curva que medía.
5. `http_req_failed{scenario:health_probe}` contaba los `503` **esperados** de
   `/ready` (26.65 %) → acotado con el tag `should_succeed:true`.
6. `maxVUs` del escenario de saturación estaba capado en 200 →
   `dropped_iterations: 708` y curva subestimada.
7. Margen del threshold de fast-fail demasiado agresivo (`+250 ms`) → ahora
   configurable (`LOAD_BACKPRESSURE_MARGIN_MS`, default `1000 ms`).

---

## 7. Cómo reproducir

```bash
# Fixtures + servicio + carga completa (local)
./tests/extractor/load/run.sh

# Corrida acotada para CI
./tests/extractor/load/run.sh --profile ci

# Soak para la curva de RSS (10 min por defecto)
./tests/extractor/load/run.sh --profile soak

# Solo levantar el servicio con la config de carga
./tests/extractor/load/run.sh --serve-only

# Contra un servicio ya corriendo
TARGET_BASE_URL=http://127.0.0.1:8001 ./tests/extractor/load/run.sh --external

# Una sola fase
./tests/extractor/load/run.sh --scenario saturation_extract
```

Salidas en `tests/extractor/load/results/` (ignorado por git):
`summary_*.json` (export k6) y `rss_*.csv` (muestreo de RSS).

Variables: `LOAD_PROFILE`, `LOAD_SCENARIO`, `LOAD_SATURATION_RPS`,
`LOAD_HAPPY_RPS`, `LOAD_P95_SLO_MS`, `LOAD_P99_SLO_MS`,
`LOAD_QUEUE_TIMEOUT_SECONDS`, `LOAD_BACKPRESSURE_MARGIN_MS`,
`LOAD_MAX_UPLOAD_BYTES`, `LOAD_CONCURRENCY`, `TARGET_BASE_URL`.

**CI:** `--profile ci` es la corrida acotada (12 iteraciones de edge, ~26 s).
Cualquier threshold rojo hace fallar el job. La corrida manual de alta carga es
`--profile full` (default) o `--profile soak`.

**Fallo esperado hoy:** el harness falla en `setup()` con
`POST /api/v1/extract returned 404 — the route does not exist yet. Task 12
depends on Task 6 ... and Task 9 ...`. Es intencional: falla rápido con un
mensaje accionable en vez de emitir minutos de `404` y un reporte engañoso.
Para inspeccionar el harness sin el servicio: `LOAD_SKIP_CONTRACT_PROBE=1`.

---

## 8. Ítems abiertos y calibración

| # | Ítem | Acción |
|---|---|---|
| 1 | **Bloqueo Tasks 6 y 9** | Implementar endpoint + backpressure; luego correr `--profile full` y rellenar § 6.1. |
| 2 | **SLOs de latencia (T1/T2) son targets sin medir** | 500/750 ms son propuestos. Calibrar con la primera corrida real sobre hardware de referencia y fijarlos. |
| 3 | **`dropped_iterations > 0`** | Aparece al inicio de la rampa mientras k6 aún allocates VUs. Subir `preAllocatedVUs` si persiste; debe ser `0` en el reporte final. |
| 4 | **Margen de fast-fail (T10)** | Si el servicio real devuelve `503` con p95 > `queue_timeout + 1000 ms`, es un hallazgo de diseño (la cola no falla rápido), no un problema del umbral. No subir el margen sin revisar § 9 del plan. |
| 5 | **`/ready` bajo carga** | Requiere Task 9. Verificar que vuelve a `200` al bajar la carga, no solo que observa `503`. |
| 6 | **Oversized vs. límite de producción** | La aserción de `413` se corre con límite de 1 MiB. Añadir una corrida con `LOAD_MAX_UPLOAD_BYTES=52428800` antes de cerrar la Task 13. |
| 7 | **RSS con servicio real** | El muestreo mide el proceso `uvicorn`. Con `ProcessPoolExecutor` (Task 9) hay que muestrear también los workers: hoy `run.sh` toma el máximo RSS de los procesos que matchean, no la suma. |

---

## 9. Task 11 — Microbenchmarks (pendiente)

Sección reservada para `tests/extractor/benchmarks/test_overhead.py`
(overhead no-parse vs. tiempo de parseo PyMuPDF, presupuesto `p99 < 1 ms`).
