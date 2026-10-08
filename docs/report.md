# Reporte de rendimiento — `pdfextractor`

> Medición sobre el servicio real (Task 12 — load/stress). Complementa y
> actualiza `docs/perf-report.md`, cuya sección 6.1 estaba pendiente de valores.
> Todas las cifras citadas provienen de los artefactos reproducibles en
> `load-tests/` (ver [§ 7](#7-reproducibilidad-y-artefactos)).

**Fecha de medición:** 2026-10-08 · **Herramientas:** k6 v2.3.0 (snap),
Vegeta v12.13.0 · **Entorno:** WSL2 (Ubuntu sobre Windows), 3.8 GiB RAM,
CPU 8 núcleos; servicio `uvicorn` (`--factory pdfextractor.main:create_app`).

## 1. Alcance

Se midió el microservicio `pdfextractor` bajo tres regímenes de carga:

| Régimen | Herramienta | Perfil | Objetivo |
|---|---|---:|---|
| Línea base (SLO) | k6 | 40 rps, 3 fixtures mezclados | Validar contrato de latencia `p95 < 500 ms`, `p99 < 750 ms` |
| Rodilla / carga sostenida | k6 | 150 rps, 3 fixtures mezclados | Ubicar la capacidad y medir degradación gradual |
| Sobrecarga mixta | Vegeta | 400 rps (50 % POST, 50 % probes) | Forzar `503` de backpressure y medir throughput |
| Sobrecarga homogénea pesada | Vegeta | 150 rps, solo fixture 20 páginas | Caracterizar el régimen de colapso por bytes |

Configuración del servicio bajo prueba (determinista, `load-tests/server.log`):

| Variable | Valor |
|---|---|
| `PDFEXTRACTOR_MAX_CONCURRENT_EXTRACTIONS` | `4` (capacidad de la puerta) |
| `PDFEXTRACTOR_QUEUE_TIMEOUT_SECONDS` | `2` (espera máxima en la puerta → `503`) |
| `PDFEXTRACTOR_MAX_UPLOAD_BYTES` | `1048576` (1 MiB) |
| `PDFEXTRACTOR_METRICS_ENABLED` | `true` |

Fixtures (`tests/extractor/load/fixtures/`, generados por
`gen_fixtures.py`): `valid_1p.pdf` (4.1 KB), `valid_5p.pdf` (55 KB),
`valid_20p.pdf` (328 KB). Todos con expectativa verificada en `manifest.json`.

## 2. Método

### 2.1 Nota de herramientas

k6 mide con un pool de VUs que ocupan memoria por petición en vuelo
(`rate × latencia`): bajo colas profundas se necesitan ~2000 VUs (~2 GiB), lo
que fuerza decisiones de preasignación (ver § 2.2). Vegeta es *goroutine-per-
request* y admite saturaciones profundas con costo de memoria fijo, por eso se
usa para el régimen de sobrecarga. Ambas herramientas capturan la misma
verdad: el umbral de saturación en ~100–150 rps mixtos.

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
  (= 1500/2400 a 150 rps). En la corrida de referencia esto dio
  `dropped_iterations = 0` (sin descartes el runner no distorsiona la curva
  ofrecida). Peak RSS de k6: 2.16 GiB, con 145 MiB disponibles de holgura.

### 2.3 Ataques Vegeta (`load-tests/targets*.txt`, `body*.bin`)

- **A — mixto:** 6 targets (3× `POST /api/v1/extract`, `GET /health`,
  `GET /ready`, `GET /metrics`), body `valid_5p.pdf`, 400 rps, 30 s,
  timeout 35 s. Vegeta adjunta el mismo body a todos los targets; las rutas GET
  lo ignoran. Peso de POST: 50 % del rate (3 de 6 targets → 200 rps).
- **B — homogéneo:** 1 target `POST /api/v1/extract`, body `valid_20p.pdf`,
  150 rps, 30 s, timeout 35 s.

### 2.4 Mapa de colas del servicio (base del análisis del § 4)

La ruta `POST /api/v1/extract` (`src/pdfextractor/presentation/api/v1/extract.py`)
encadena cuatro etapas tras la lectura:

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
| avg / med | 36.9 ms / 13.9 ms | — |
| p90 / p95 / p99 | 86.0 / 104.8 / **255.6** ms | p95 < 500 · p99 < 750 ✅✅ |
| max | 345.1 ms | — |
| `http_req_failed` | **0.00 %** | < 1 % ✅ |
| checks (200 + 3 claves) | 2198 / 2198 (100 %) | > 99 % ✅ |

### 3.2 Carga sostenida / rodilla (k6, 150 rps, 3774 requests)

| Métrica | Valor | Lectura |
|---|---:|---|
| avg / med | 2.81 s / 122 ms | ~mitad de la ventana sin cola (rampa) |
| p90 / p95 / p99 | 14.8 / 15.7 / **16.3** s | la cola domina el piso alto |
| max | 16.67 s | `p(99) < 32 s` ✅ (T7) |
| `http_req_failed{POST}` | **0.00 %** (3774/3774 `200`) | sin degradar el contrato |
| `unexpected_status` | **0** | ningún status fuera de `{200,503,504}` |
| `dropped_iterations` | **0** | runner no descarta |
| `/health` durante saturación | 314 / 314 = 100 % ✅ | liveness intacta |
| `/ready` durante saturación | 99 × `503` (luego vuelve a 200) | degradación de readiness observable |

La rodilla está en **~150 rps mixtos**: es bimodal — una corrida idéntica
funcionó sin saturar (`p50 25 ms`, `p95 526 ms`, 0 fallos) y otra saturó con
120 cortes de conexión de cliente y cero `5xx` de servidor
(`k6-run3-saturado.txt`). El host (WSL2 compartido con Windows) desplaza el
punto de quiebre ±30 % entre corridas; la corrida aquí reportada es la
observación definitiva por tener `dropped_iterations = 0`.

### 3.3 Sobrecarga mixta (Vegeta A, 400 rps, 11999 requests, 5 páginas)

| Métrica | Valor |
|---|---:|
| rate ofrecido / throughput | 399.98 rps / **254.4 rps exitosos** |
| latencia min / mean | 0.8 ms / 4.23 s |
| latencia p50 / p90 / p95 / p99 / max | 1.11 / 16.31 / 19.96 / 21.46 / 22.71 s |
| éxito (`200`) | 10243 = **85.4 %** |
| `503` (backpressure) | 1756 = 14.6 % ✅ **el fast-fail se activa bajo carga mixta** |
| otros códigos | 0 (ni `5xx` inesperados ni cortes de transporte) |
| drenaje tras fin de ataque | 10.3 s (la cola se vacía por sí sola) |

Con POST ≈ 200 rps ofrecidos (60 % de 400) sobre una capacidad de ~100–150,
la puerta ③ satura y genera `503` acotados en `≈ queue_timeout` (p50 global
1.11 s incluye el 14.6 % de rechazos rápidos). El throughput exitoso (254 rps)
refleja que los `503` no consumen capacidad de extracción.

### 3.4 Sobrecarga homogénea pesada (Vegeta B, 150 rps, 4500 requests, 20 páginas)

| Métrica | Valor |
|---|---:|
| rate ofrecido / throughput | 150.02 rps / **29.7 rps exitosos** |
| latencia mean / p50 | 27.7 s / **35.0 s (= timeout de cliente)** |
| p90 / p95 / p99 / max | 35.0 / 35.0 / 35.0 / 35.03 s |
| `200` | 1933 = 43.0 % |
| `504` (timeout de extracción 30 s) | 168 = 3.7 % |
| timeout de cliente sin respuesta | 2399 = 53.3 % |
| **`503`** | **0** — el fast-fail **no** llega al cliente |

Con un solo fixture pesado (body 328 KB), la etapa ① (parseo + serialización
JSON de ~120 KB de texto por respuesta) satura el event loop y la cola ②
aguas arriba de la puerta. Las peticiones entran en la cola **sin contador de
expiración propio** y la puerta de 2 s nunca se alcanza: no hay `503`, la
latencia escala hasta el timeout del cliente y recién a los 30 s el pool ④
emite `504`. Es la manifestación prevista en `docs/perf-report.md` § 8.4
("si la latencia del 503 crece con la carga, la cola no está acotada").

### 3.5 Capacidad por fixture y memoria

Latencias aisladas (mediana de 5 muestras, sin carga, misma sesión):

| Fixture | Tiempo | Capacidad de puerta `≈ 4/t` |
|---|---:|---:|
| `valid_1p.pdf` | ~32 ms | ~125 rps |
| `valid_5p.pdf` | ~40 ms | ~100 rps |
| `valid_20p.pdf` | ~54 ms | ~74 rps |
| mezcla (1/5/20) | ~41 ms | ~97 rps |

Capacidad mixta observada sostenible: **~100–150 rps** (el 20 páginas es el
limitante). Un burst extra de `valid_5p.pdf` a 150 rps (20 s, 3000 requests,
`/tmp/opencode/rss-burst.bin`) completo **100 % `200`** con `p95 284 ms`,
`p99 832 ms` — consistente con una capacidad 5p ≈ 150 rps.

**Memoria del servicio** (uvicorn + process pool): RSS ~0.6 GiB en reposo y
~0.97 GiB en pico durante la saturación; **sin crecimiento** a lo largo del
burst (`RSS pico == RSS tras drenaje`), sin peticiones `OOM` — no se observa
fuga en el horizonte probado (pendiente el soak largo de T16).

## 4. Análisis

### 4.1 Capacidad y rodilla

- El tope configurado (`max_concurrent_extractions = 4`) fija el techo
  teórico `≈ 4 / tiempo_de_servicio`. Con la mezcla real la rodilla cae en
  **~100–150 rps**, y los percentiles altos (`p90` +14 rps sobre la línea
  base) marcan el onset de la cola antes que el promedio; esto ilustra el
  fallo de "juzgar por el mean" del cap. 9 de *Essential Kanban Condensed*
  (PDF 48) y del principio de inspección/adaptación del *Scrum Guide*
  (p. 4 impresa): **inspeccionar p95/p99, no la media**.
- Ley de Little aplicada al propio layout: con ~150 rps ofrecidos y latencia
  ~15 s en saturación, las peticiones en vuelo alcanzan ~2250; el harness k6
  debió preasignar `rate × latencia_max` VUs para no descartar (WIP del
  *Kanban*, PDF 26) — de ahí `preAllocatedVUs = rate×10`.

### 4.2 Hallazgo de diseño: la cola no acotada vive en el event loop, no en la puerta

El `503` acotado en 2 s **solo es alcanzable** si la petición consigue un hilo
de ②. Cuando el cuello está en ① (bytes/event loop, caso homogéneo pesado), la
cola crece sin topes y el cliente corta por timeout en vez de recibir un `503`
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
memoria se protegió por los timeouts de cliente (35 s) más que por la política
interna. En ningún régimen se observó `500` ni un `5xx` fuera de contrato.

## 5. Cumplimiento — SLOs, DoD y marco teórico

Mapeo contra los umbrales de `docs/perf-report.md`:

| # | Umbral | Medido | Veredicto |
|---|---|---|---|
| T1 | `happy_path p(95) < 500 ms` | **104.8 ms** | ✅ |
| T2 | `happy_path p(99) < 750 ms` | **255.6 ms** | ✅ |
| T3 | `happy_path http_req_failed < 1 %` | **0.00 %** | ✅ |
| T4 | `checks happy_path > 99 %` | **100 %** | ✅ |
| T6 | `checks saturación > 95 %` | **100 %** | ✅ |
| T7 | `saturación p(99) < queue+extraction` (32 s) | **16.3 s** | ✅ |
| T8 | `unexpected_status == 0` | **0** | ✅ |
| T9 | backpressure `> 0` (`503` reales) | k6: 0 POST · **Vegeta A: 1756** | ⚠️ según régimen (ver § 4.2) |
| T10 | `503` latencia ≈ queue_timeout | p50 global 1.11 s en A | ⚠️ parcial (solo mixto) |
| T11/T12 | `/health` 200 bajo saturación | **100 %** (314/314) | ✅ |
| T13 | `/ready` nunca 404 | **0** | ✅ |
| T14 | error budget `/health` < 1 % | **0.00 %** | ✅ |
| T16 | RSS estable | sin pendiente en 20 s (~0.97 GiB) | 🟡 soak pendiente |
| — | `dropped_iterations == 0` | **0** | ✅ |

**Marco RNF (medidas de calidad, DoD):** siguiendo al *Scrum Guide* (p. 12
impresa), la Definition of Done del rendimiento se enuncia como **medidas
objetivas verificables**, no impresiones: las tablas de § 3 (percentiles,
throughput, tasa de éxito) son el mecanismo de inspección y se vuelven a
medir en cada cambio (adaptación). Desde *Historias de usuario* (p. 24
impresa), los criterios de aceptación no funcionales se separan en
**objetivo** (p95 < 500 ms), **restricción** (503 acotado a ~2 s bajo
saturación) y **línea base** (hoy: p50 ≈ 14 ms, capacidad ≈ 100–150 rps) —
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
   y medición por separado de los workers del pool (item 7 de
   `docs/perf-report.md` § 8).
5. Re-medir con `PDFEXTRACTOR_MAX_UPLOAD_BYTES=52428800` (50 MiB) antes de
   cerrar la Task 13 (item 6 de `perf-report.md` § 8): el costo de bytes por
   request es el factor que hoy descubre el régimen del § 3.4.

## 7. Reproducibilidad y artefactos

```bash
# 1) Servidor con configuración determinista
PDFEXTRACTOR_PORT=18011 PDFEXTRACTOR_MAX_CONCURRENT_EXTRACTIONS=4 \
PDFEXTRACTOR_MAX_UPLOAD_BYTES=1048576 PDFEXTRACTOR_QUEUE_TIMEOUT_SECONDS=2 \
uv run uvicorn --factory pdfextractor.main:create_app --host 127.0.0.1 --port 18011

# 2) k6 (línea base + rodilla + sonda)
k6 run --summary-trend-stats "avg,min,med,max,p(90),p(95),p(99)" \
  --summary-export load-tests/k6-summary.json \
  -e TARGET_BASE_URL=http://127.0.0.1:18011 load-tests/load-test.js

# 3) Vegeta — sobrecarga mixta (A) y su reporte
vegeta attack -targets=load-tests/targets.txt -body=load-tests/body5p.bin \
  -header='Content-Type: multipart/form-data; boundary=----pdfextractorVegetaB0UNDARY' \
  -rate=400 -duration=30s -timeout=35s -max-body=1024 | tee load-tests/results.bin
vegeta report < load-tests/results.bin | tee load-tests/vegeta-report.txt
vegeta report --type=json < load-tests/results.bin > load-tests/results.json
vegeta plot < load-tests/results.bin > load-tests/results.html

# 4) Vegeta — sobrecarga homogénea (B) y su reporte
vegeta attack -targets=load-tests/targets-saturacion.txt -body=load-tests/body20p.bin \
  -header='Content-Type: multipart/form-data; boundary=----pdfextractorVegetaB0UNDARY' \
  -rate=150 -duration=30s -timeout=35s -max-body=1024 | tee load-tests/results-saturacion.bin
vegeta report < load-tests/results-saturacion.bin | tee load-tests/vegeta-report-saturacion.txt
vegeta report --type=json < load-tests/results-saturacion.bin > load-tests/results-saturacion.json
vegeta plot < load-tests/results-saturacion.bin > load-tests/results-saturacion.html
```

Artefactos en `load-tests/`:

| Archivo | Contenido |
|---|---|
| `load-test.js` | Harness k6 completo (escenarios, checks, thresholds) |
| `k6-output.txt` / `k6-summary.json` | Corrida definitiva (exit 0, `dropped_iterations=0`) |
| `k6-run3-saturado.txt` / `k6-run3-summary.json` | Corrida 3 (saturación con cortes de transporte; referencia) |
| `server.log` | Log completo del servicio durante las pruebas |
| `targets.txt` / `targets-saturacion.txt` | Targets Vegeta |
| `body5p.bin` / `body20p.bin` | Cuerpos multipart (boundary `----pdfextractorVegetaB0UNDARY`) |
| `results.bin` / `results.json` / `results.html` / `vegeta-report.txt` | Ataque A (mixto) |
| `results-saturacion.*` / `vegeta-report-saturacion.txt` | Ataque B (homogéneo 20 pág.) |

**Limitaciones:** host compartido (WSL2) — la rodilla es bimodal entre
corridas (±30 %); las latencias aisladas (§ 3.5) son de una única sesión. No
se realizó soak largo. Los cuerpos `valid_*` son sintéticos (páginas de texto
aleatorio).

## 8. Referencias

- `docs/perf-report.md` — definiciones T1–T16, plan de carga y curva esperada.
- `docs/api-contract.md` — contrato de endpoints y cuerpos verificados.
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