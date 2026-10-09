# Reporte de rendimiento — `pdfextractor`

> Medición sobre el servicio real (Task 12 — load/stress), re-ejecutada contra
> el endpoint de despliegue **`http://localhost:9000/api/v1/extractions`**
> (Task 10). Todas las cifras citadas provienen de los artefactos
> reproducibles en `load-tests/run-9000/` (ver [§ 7](#7-reproducibilidad-y-artefactos)).
> Los umbrales T1–T16 usados para el veredicto (§ 5) son los definidos para
> esta tarea en `docs/tasks/plan.md`; el contrato verificado proviene de
> `docs/api-contract.md`.

**Fecha de medición:** 2026-10-09 · **Herramientas:** k6 v2.3.0 (snap),
Vegeta v12.13.0 · **Entorno:** WSL2 (Ubuntu sobre Windows), 3.7 GiB RAM,
CPU 8 núcleos; servicio `uvicorn` (`--factory pdfextractor.main:create_app`) en
`127.0.0.1:9000`.

## 1. Alcance

Se midió el microservicio `pdfextractor` bajo tres regímenes de carga, todos
apuntando a la ruta pública `POST /api/v1/extractions`:

| Régimen | Herramienta | Perfil | Objetivo |
|---|---|---:|---|
| Línea base (SLO) | k6 | 40 rps, 3 fixtures mezclados | Validar contrato de latencia `p95 < 500 ms`, `p99 < 750 ms` |
| Rodilla / carga sostenida | k6 | 150 rps, 3 fixtures mezclados | Ubicar la capacidad y medir degradación gradual |
| Sobrecarga mixta | Vegeta | 400 rps (50 % POST, 50 % probes) | Forzar `503` de backpressure y medir throughput |
| Sobrecarga homogénea pesada | Vegeta | 150 rps, solo fixture 20 páginas | Caracterizar el régimen de colapso por bytes |

Configuración del servicio bajo prueba (determinista, `load-tests/run-9000/server.log`):

| Variable | Valor |
|---|---|
| `PDFEXTRACTOR_PORT` | `9000` |
| `PDFEXTRACTOR_MAX_CONCURRENT_EXTRACTIONS` | `4` (capacidad de la puerta) |
| `PDFEXTRACTOR_QUEUE_TIMEOUT_SECONDS` | `2` (espera máxima en la puerta → `503`) |
| `PDFEXTRACTOR_MAX_UPLOAD_BYTES` | `1048576` (1 MiB) |
| `PDFEXTRACTOR_METRICS_ENABLED` | `true` |

> Nota: es la misma configuración determinista de la corrida anterior, no la
> del despliegue Docker (`.env.example`: 50 MiB, `queue_timeout` 5 s,
> `MIN_TEXT_LENGTH=0`). Se fija 1 MiB para que la fase de bytes (§ 3.4) sea
> comparable entre corridas.

Fixtures (`tests/extractor/load/fixtures/`, generados por `gen_fixtures.py`):
`valid_1p.pdf` (4.1 KB), `valid_5p.pdf` (53.9 KB), `valid_20p.pdf` (320.8 KB).
Todos con expectativa verificada en `manifest.json`. Cuerpos multipart Vegeta
construidos con los campos `checksum` (SHA-256) + `file`, boundary
`----pdfextractorVegetaB0UNDARY` (`load-tests/run-9000/body5p.bin` = 55 549 B,
`body20p.bin` = 328 850 B).

## 2. Método

### 2.1 Nota de herramientas

k6 mide con un pool de VUs que ocupan memoria por petición en vuelo
(`rate × latencia`): bajo colas profundas se necesitan miles de VUs (~2 GiB), lo
que fuerza decisiones de preasignación (ver § 2.2). Vegeta es *goroutine-per-
request* y admite saturaciones profundas con costo de memoria fijo, por eso se
usa para el régimen de sobrecarga. Ambas herramientas capturan la misma
verdad: el umbral de saturación se ubica en el orden de ~150 rps mixtos.

### 2.2 Harness k6 (`load-tests/load-test.js`)

Escenarios secuenciales (la saturación no contamina la línea base) + sonda
paralela:

| Escenario | Executor | Ventana | Carga |
|---|---|---|---|
| `probes` | `constant-vus` × 2 | 0–90 s | `GET /health`, `/ready`, `/metrics` cada 0.5 s |
| `extract_baseline` | `ramping-arrival-rate` | 0–35 s | 0→40 rps (10 s) → 40 rps (20 s) → 0 |
| `extract_stress` | `ramping-arrival-rate` | 40–75 s | 0→150 rps (10 s) → 150 rps (20 s) → 0 |

- Multipart armado a mano (boundary `----pdfextractorLoadTestB0UNDARY`, campo
  `file`), matched con `docs/api-contract.md`.
- Checks: baseline → `200` + cuerpo con las 3 claves
  `extracted_text`/`extraction_method`/`page_count`; saturación → status en
  `{200, 503, 504}` y cuerpo coherente con su status; sonda → `/health` nunca
  sale de `200`, `/ready` en `{200, 503}` (nunca `404`), `/metrics` en `200`.
- Umbrales (todos verificados por el exit code del runner):
  - baseline: `p(95)<500`, `p(99)<750`, `http_req_failed<1 %`, `checks>99 %`
  - saturación: `p(99)<extraction_timeout+2000` (32 s), `http_req_failed<35 %`,
    `checks>95 %`
  - sonda: `health_ok_rate==1`, `ready_route_missing==0`, `ready_overloaded>0`,
    `unexpected_status==0`
- Preasignación del pool: `preAllocatedVUs = rate×10`, `maxVUs = rate×16`
  (= 1500/2400 a 150 rps). En esta corrida k6 asignó hasta **1542 VUs** (pico
  usado 976) y no descartó iteraciones (`0 interrupted`), por lo que el runner
  no distorsiona la curva ofrecida.

### 2.3 Ataques Vegeta (`load-tests/run-9000/targets*.txt`, `body*.bin`)

- **A — mixto:** 6 targets (`POST /api/v1/extractions` ×3, `GET /health`,
  `GET /ready`, `GET /metrics`), body `valid_5p.pdf`, 400 rps, 30 s,
  timeout 35 s. Vegeta adjunta el mismo body a todos los targets; las rutas GET
  lo ignoran. Peso de POST: 50 % del rate (3 de 6 targets → 200 rps).
- **B — homogéneo:** 1 target `POST /api/v1/extractions`, body `valid_20p.pdf`,
  150 rps, 30 s, timeout 35 s.

### 2.4 Mapa de colas del servicio (base del análisis del § 4)

La ruta `POST /api/v1/extractions`
(`src/pdfextractor/presentation/api/v1/extract.py`) encadena cuatro etapas tras
la lectura:

```
HTTP → ① read_multipart_file (event loop, bytearray por request)
      → ② asyncio.to_thread (cola del executor default, ~min(32,cpu+4) hilos)
      → ③ ProcessPoolTextExtractor._Gate  (4 permisos, 2 s → 503)
      → ④ ProcessPoolExecutor workers     (30 s → 504)
```

- `BufferPool.acquire()` (memoria/pool.py:21) es **no bloqueante**: con los 4
  buffers en uso devuelve `None`, y `read_multipart_file(sink=None)`
  (multipart_reader.py:59) lee igual el cuerpo completo a un `bytearray`
  nuevo. Resultado: la ① (y su cola de coroutinas pendientes de hilo en ②)
  es la **cola realmente sin tope** del sistema.
- El `503` en ③ solo se dispara si la petición consigue un hilo de ② y agota
  los 2 s esperando un permiso de la puerta. `504` = timeout de extracción
  (30 s) del pool en ④.

## 3. Resultados

### 3.1 Línea base (k6, 40 rps, 1099 requests) — **SLO OK**

| Métrica | Valor | Target |
|---|---:|---|
| avg / med | 24.53 ms / 11.47 ms | — |
| p90 / p95 / p99 | 58.33 / **64.88** / **93.72** ms | p95 < 500 · p99 < 750 ✅✅ |
| min / max | 3.2 / 121.4 ms | — |
| `http_req_failed` | **0.00 %** (0/1099) | < 1 % ✅ |
| checks (200 + 3 claves) | 2198 / 2198 (100 %) | > 99 % ✅ |

El SLO queda holgado: p95 a ~13 % del techo y p99 a ~12 % del suyo. El body
mixto a 40 rps nunca alcanza la puerta de 4 permisos.

### 3.2 Carga sostenida / rodilla (k6, 150 rps, 4124 requests)

| Métrica | Valor | Lectura |
|---|---:|---|
| avg / med | 3.85 s / 15.62 ms | ~mitad de la ventana sin cola (rampa) |
| p90 / p95 / p99 | 17.47 / 20.10 / **22.32** s | la cola domina el piso alto |
| min / max | 2.89 ms / 22.69 s | `p(99) < 32 s` ✅ (T7) |
| `http_req_failed` (POST) | **0.00 %** (4124/4124 `200`) | sin degradar el contrato |
| `unexpected_status` | **0** | ningún status fuera de `{200,503,504}` |
| iteraciones interrumpidas / dropped | **0** | runner no descarta |
| VUs asignadas / pico usado | 1542 / 976 | pool suficiente |
| `/health` durante saturación | 337 / 337 = 100 % ✅ | liveness intacta |
| `/ready` durante saturación | 107 × `503` (luego vuelve a 200) | degradación de readiness observable |

A 150 rps mixtos el sistema **absorbió toda la carga sin un solo error**: las
peticiones esperaron en cola hasta ~22 s (por debajo del timeout de extracción
de 30 s) y todas terminaron en `200`. La rodilla —el point donde la latencia
p99 se separa tres órdenes de magnitud del p50— está en el entorno de los
**~150 rps mixtos**, pero en esta corrida el servicio aún no colapsa, solo
acumula cola. El promedio (3.85 s) es engañoso: mezcla el tramo de rampa sin
cola con el tramo saturado.

**Resultado global k6:** `http_reqs = 6236` (67.25 rps agregados, incluyendo
sonda), `iterations = 5560`, `data_sent = 601 MB`, `data_received = 225 MB`. El
`http_req_failed` global de **1.71 % (107/6236)** corresponde **exactamente** a
los `503` de `/ready` de la sonda: los escenarios POST (baseline y stress)
reportan **0.00 %** de fallos. Todos los thresholds del runner pasaron y k6
salió con código **0**.

### 3.3 Sobrecarga mixta (Vegeta A, 400 rps, 12000 requests, 5 páginas)

| Métrica | Valor |
|---|---:|
| rate ofrecido / throughput | 400.04 rps / **244.47 rps exitosos** |
| latencia min / mean | 5.96 ms / 5.086 s |
| latencia p50 / p90 / p95 / p99 / max | 1.992 / 19.529 / 22.762 / 26.931 / 27.998 s |
| éxito (`200`) | 10005 = **83.38 %** |
| `503` (backpressure) | 1995 = 16.62 % ✅ **el fast-fail se activa bajo carga mixta** |
| otros códigos / cortes de transporte | 0 |
| drenaje tras fin del ataque | 10.93 s (la cola se vacía por sí sola) |

Con POST ≈ 200 rps ofrecidos sobre una capacidad mixta de ~100–150, la puerta ③
satura y genera `503` acotados: el p50 global (1.992 s) está en el orden del
`queue_timeout` (2 s) porque la mitad de las muestras son rechazos rápidos de
la puerta. El throughput exitoso (244 rps) refleja que los `503` **no consumen
capacidad de extracción**.

### 3.4 Sobrecarga homogénea pesada (Vegeta B, 150 rps, 4500 requests, 20 páginas)

| Métrica | Valor |
|---|---:|
| rate ofrecido / throughput | 150.04 rps / **26.83 rps exitosos** |
| latencia min / mean | 79.57 ms / 25.957 s |
| latencia p50 / p90 / p95 / p99 / max | 30.162 / 33.461 / 33.532 / 33.645 / 33.736 s |
| `200` | 1676 = **37.24 %** |
| `504` (timeout de extracción 30 s) | 2824 = **62.76 %** |
| timeout de cliente (35 s) | 0 (el p99 interno 33.7 s queda por debajo) |
| **`503`** | **0** — el fast-fail **no** llega al cliente |
| drenaje tras fin del ataque | 32.48 s |

Con un solo fixture pesado (body 328 KB, respuesta de ~120 KB de texto), la
etapa ① (parseo + serialización JSON) satura el event loop y la cola ② aguas
arriba de la puerta. Las peticiones entran en la cola **sin contador de
expiración propio** y la puerta de 2 s nunca se alcanza: no hay `503`, la
latencia escala hasta el `504` del pool ④ (~30 s) y el p50/p90/p95 se pegan a
~30–34 s. Es la manifestación prevista en § 4.2 ("si la latencia del 503 crece
con la carga, la cola no está acotada"). A diferencia de la corrida anterior,
el cliente (timeout 35 s) ya no corta antes que el servidor: el `504` interno
llega primero y queda contabilizado como respuesta.

### 3.5 Capacidad por fixture y memoria

Latencias aisladas (mediana de 5 muestras, sin carga, misma sesión):

| Fixture | Tiempo | Capacidad de puerta `≈ 4/t` |
|---|---:|---:|
| `valid_1p.pdf` | ~27 ms | ~150 rps |
| `valid_5p.pdf` | ~32 ms | ~125 rps |
| `valid_20p.pdf` | ~92 ms | ~44 rps |
| mezcla (1/5/20) | ~50 ms | ~80 rps |

Capacidad mixta observada sostenible: **~100–150 rps** (el 20 páginas es el
limitante). Los valores aislados del fixture pesado subieron frente a la
corrida anterior (~54 → ~92 ms), consistente con la varianza de un host WSL2
compartido.

**Memoria del servicio** (uvicorn + process pool): RSS del proceso principal
~1.06 GiB al cierre de las pruebas, con los workers del pool en ~40 MiB cada
uno. **No se realizó muestreo de RSS durante la carga** en esta corrida (el
soak de T16 sigue pendiente); no se observó ningún `OOM` ni `500` en el log de
servicio.

Log del servicio a lo largo de toda la sesión (`load-tests/run-9000/server.log`):
**200: 17817 · 503: 2102 · 504: 2824 · ningún otro 5xx**. La totalidad de los
`503` se descompone en 1995 del ataque mixto (A) + 107 del `/ready` de la sonda
k6; los 2824 `504` provienen del ataque homogéneo (B).

## 4. Análisis

### 4.1 Capacidad y rodilla

- El tope configurado (`max_concurrent_extractions = 4`) fija el techo
  teórico `≈ 4 / tiempo_de_servicio`. Con la mezcla real la rodilla cae en
  **~100–150 rps**, y los percentiles altos (`p90` pasando de ~58 ms en la
  línea base a ~17 s en saturación) marcan el onset de la cola mucho antes que
  el promedio; esto ilustra el fallo de "juzgar por el mean" del cap. 9 de
  *Essential Kanban Condensed* (PDF 48) y del principio de inspección/adaptación
  del *Scrum Guide* (p. 4 impresa): **inspeccionar p95/p99, no la media**.
- Ley de Little aplicada al propio layout: con ~150 rps ofrecidos y latencia
  ~15–22 s en saturación, las peticiones en vuelo alcanzan ~1500–2000; el
  harness k6 debió preasignar `rate × latencia_max` VUs para no descartar (WIP
  del *Kanban*, PDF 26) — de ahí `preAllocatedVUs = rate×10` y `vus_max = 1542`.

### 4.2 Hallazgo de diseño: la cola no acotada vive en el event loop, no en la puerta

El `503` acotado en 2 s **solo es alcanzable** si la petición consigue un hilo
de ②. Cuando el cuello está en ① (bytes/event loop, caso homogéneo pesado), la
cola crece sin topes y el cliente recibe un `504` tardío en vez de un `503`
rápido (§ 3.4). Cuando la mezcla es variada y el body es chico, ① se mantiene
al día y la puerta ③ sí produce `503` acotados (§ 3.3).

Implicación para la RNF de backpressure: la restricción "saturación responde
503 rápido" se cumple **parcialmente** — solo cuando el event loop no es el
cuello. La causa raíz es que `BufferPool.acquire()` devuelve `None` en lugar
de rechazar (memoria/pool.py:21) y el reader acepta `sink=None` creando un
buffer por petición, y que `asyncio.to_thread` no tiene límite de admisión
(extract.py:49-51).

### 4.3 Sobre la estabilidad de los indicadores

El `503` del riesgo de bytes y el `504` del timeout de extracción son la
defensa real contra colas infinitas a nivel **servidor**; el límite de
memoria se protegió por los timeouts de cliente más que por la política
interna. En ningún régimen se observó `500` ni un `5xx` fuera de contrato.

## 5. Cumplimiento — SLOs, DoD y marco teórico

Mapeo contra los umbrales T1–T16 definidos para esta tarea:

| # | Umbral | Medido | Veredicto |
|---|---|---|---|
| T1 | `happy_path p(95) < 500 ms` | **64.88 ms** | ✅ |
| T2 | `happy_path p(99) < 750 ms` | **93.72 ms** | ✅ |
| T3 | `happy_path http_req_failed < 1 %` | **0.00 %** | ✅ |
| T4 | `checks happy_path > 99 %` | **100 %** | ✅ |
| T6 | `checks saturación > 95 %` | **100 %** | ✅ |
| T7 | `saturación p(99) < queue+extraction` (32 s) | **22.32 s** | ✅ |
| T8 | `unexpected_status == 0` | **0** | ✅ |
| T9 | backpressure `> 0` (`503` reales) | k6 POST: 0 · **Vegeta A: 1995** | ⚠️ según régimen (ver § 4.2) |
| T10 | `503` latencia ≈ queue_timeout | p50 global A = **1.992 s** (≈ 2 s) | ✅ (solo mixto) |
| T11/T12 | `/health` 200 bajo saturación | **100 %** (337/337) | ✅ |
| T13 | `/ready` nunca 404 | **0** | ✅ |
| T14 | error budget `/health` < 1 % | **0.00 %** | ✅ |
| T16 | RSS estable | no re-muestreado esta corrida | 🟡 soak pendiente |
| — | iteraciones interrumpidas / dropped `== 0` | **0** | ✅ |

**Marco RNF (medidas de calidad, DoD):** siguiendo al *Scrum Guide* (p. 12
impresa), la Definition of Done del rendimiento se enuncia como **medidas
objetivas verificables**, no impresiones: las tablas de § 3 (percentiles,
throughput, tasa de éxito) son el mecanismo de inspección y se vuelven a
medir en cada cambio (adaptación). Desde *Historias de usuario* (p. 24
impresa), los criterios de aceptación no funcionales se separan en
**objetivo** (p95 < 500 ms), **restricción** (503 acotado a ~2 s bajo
saturación) y **línea base** (hoy: p50 ≈ 12 ms, capacidad ≈ 100–150 rps) —
de ningún modo mezclándose con el rendimiento funcional, aunque esta corrida
reveló que la restricción 503 no se cumple en el régimen de bytes (§ 4.2), lo
que deja una **historia abierta** ("nunca dar una historia por cerrada", HU
p. 29 impresa): hay que mover la puerta a la entrada del event loop.

## 6. Recomendaciones

1. **Aplicar la puerta antes de leer el cuerpo** (semáforo `asyncio` o
   `acquire()` bloqueante con timeout en `extract.py:39`, en vez de
   `sink=None`): el fast-fail `503` quedaría disponible en el régimen en que
   hoy no existe. Es el cambio con mayor relación valor/coste.
2. **Acotar admisión de `to_thread`** o reemplazarlo por un pool con cola
   limitada (`Semaphore` alrededor de `service.extract`), para que el
   `queue_timeout` de la puerta sea medido desde la llegada, no desde el hilo.
3. **Watch del p95 en el punto ~100–150 rps** como early warning de la rodilla
   (el p95 se degrada 2 órdenes de magnitud antes que el p50).
4. **Cerrado de T16**: corrida soak (10 min) para confirmar la meseta de RSS
   y medición por separado de los workers del pool (item 7 del plan de carga).
5. Re-medir con `PDFEXTRACTOR_MAX_UPLOAD_BYTES=52428800` (50 MiB) antes de
   cerrar la Task 13 (item 6 del plan): el costo de bytes por request es el
   factor que hoy descubre el régimen del § 3.4.

## 7. Reproducibilidad y artefactos

```bash
# 1) Servidor con configuración determinista (puerto de despliegue 9000)
PDFEXTRACTOR_PORT=9000 PDFEXTRACTOR_MAX_CONCURRENT_EXTRACTIONS=4 \
PDFEXTRACTOR_MAX_UPLOAD_BYTES=1048576 PDFEXTRACTOR_QUEUE_TIMEOUT_SECONDS=2 \
PDFEXTRACTOR_METRICS_ENABLED=true \
uv run uvicorn --factory pdfextractor.main:create_app --host 127.0.0.1 --port 9000

# 2) k6 (línea base + rodilla + sonda) contra /api/v1/extractions
k6 run --summary-trend-stats "avg,min,med,max,p(90),p(95),p(99)" \
  -e TARGET_BASE_URL=http://localhost:9000 load-tests/load-test.js

# 3) Cuerpos multipart (checksum + file) para Vegeta
B=----pdfextractorVegetaB0UNDARY
# (ver load-tests/run-9000/body5p.bin y body20p.bin; checksum = sha256sum del PDF)

# 4) Vegeta — sobrecarga mixta (A)
vegeta attack -targets=load-tests/run-9000/targets-mixed.txt \
  -body=load-tests/run-9000/body5p.bin \
  -header="Content-Type: multipart/form-data; boundary=$B" \
  -rate=400 -duration=30s -timeout=35s -max-body=1024 \
  | tee load-tests/run-9000/results-mixed.bin
vegeta report < load-tests/run-9000/results-mixed.bin
vegeta report --type=json < load-tests/run-9000/results-mixed.bin > load-tests/run-9000/results-mixed.json

# 5) Vegeta — sobrecarga homogénea (B)
vegeta attack -targets=load-tests/run-9000/targets-20p.txt \
  -body=load-tests/run-9000/body20p.bin \
  -header="Content-Type: multipart/form-data; boundary=$B" \
  -rate=150 -duration=30s -timeout=35s -max-body=1024 \
  | tee load-tests/run-9000/results-20p.bin
vegeta report < load-tests/run-9000/results-20p.bin
vegeta report --type=json < load-tests/run-9000/results-20p.bin > load-tests/run-9000/results-20p.json
```

Artefactos en `load-tests/run-9000/`:

| Archivo | Contenido |
|---|---|
| `k6-output.txt` | Salida completa de k6 (thresholds, checks, métricas) |
| `server.log` | Log completo del servicio durante las pruebas |
| `targets-mixed.txt` / `targets-20p.txt` | Targets Vegeta (rutas absolutas a `:9000`) |
| `body5p.bin` / `body20p.bin` | Cuerpos multipart (`checksum` + `file`) |
| `results-mixed.bin` / `.json` / `vegeta-report-mixed.txt` | Ataque A (mixto) |
| `results-20p.bin` / `.json` / `vegeta-report-20p.txt` | Ataque B (homogéneo 20 pág.) |

**Limitaciones:** host compartido (WSL2) — la rodilla es sensible a la carga
del host entre corridas; las latencias aisladas (§ 3.5) son de una única
sesión. No se realizó soak largo ni muestreo de RSS en vivo. Los cuerpos
`valid_*` son sintéticos (páginas de texto aleatorio). k6 corrió como snap
(sin permiso de escritura fuera del home, por lo que no se generó el
`--summary-export` JSON; el reporte textual es equivalente).

## 8. Referencias

- `docs/tasks/plan.md` — alcance, ruta `POST /api/v1/extractions` (AD6) y
  tareas de integración (TASK-07…TASK-11).
- `docs/api-contract.md` — contrato de endpoints y cuerpos verificados.
- `docs/optimization-report.md` — auditoría de PyMuPDF y optimizaciones.
- `src/pdfextractor/presentation/api/v1/extract.py`,
  `src/pdfextractor/infrastructure/concurrency/pool.py`,
  `src/pdfextractor/infrastructure/http/multipart_reader.py`,
  `src/pdfextractor/infrastructure/memory/pool.py` — mapa de etapas y
  sensibilidad al tiempo (Ley de Little, WIP).
- `docs/pdfs/2020-Scrum-Guide-Spanish-Latin-South-American.pdf` — DoD como
  medidas de calidad (p. 12), inspección/adaptación (p. 4).
- `docs/pdfs/scrum_manager_historias_usuario.pdf` — RNF objetivo/restricción/
  línea base (p. 24), historias abiertas (p. 29).
- `docs/pdfs/Essential-Kanban-Condensed-Spanish.pdf` — no confiar en el
  promedio (PDF 48), Ley de Little (PDF 26).
