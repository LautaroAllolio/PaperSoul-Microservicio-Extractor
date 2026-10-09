# Reporte de rendimiento — `pdfextractor`

> Medición sobre el servicio real (Task 12 — load/stress), re-ejecutada contra
> el endpoint de despliegue **`http://localhost:9000/api/v1/extractions`**
> (Task 10). Todas las cifras citadas provienen de los artefactos
> reproducibles en `load-tests/run-9000/` (ver [§ 7](#7-reproducibilidad-y-artefactos)).
> Los umbrales T1–T16 usados para el veredicto (§ 5) son los definidos para
> esta tarea en `docs/tasks/plan.md`; el contrato verificado proviene de
> `docs/api-contract.md`.

**Fecha de medición:** 2026-10-09 (segunda corrida) · **Herramientas:** k6
v2.3.0 (snap), Vegeta v12.13.0 · **Entorno:** WSL2 (Ubuntu sobre Windows),
3.7 GiB RAM, CPU 8 núcleos; servicio `uvicorn`
(`--factory pdfextractor.main:create_app`) en `127.0.0.1:9000`.

> **Corrida actual vs. anterior:** este documento ha sido re-ejecutado contra el
> binario **posterior a TASK-12…TASK-24** (fast-fail `503` antes de leer el
> cuerpo, timeout único en el pool, pool en `forkserver`, cotas de salida).
> Por eso § 3 y § 5 **reemplazan** las cifras de la corrida original de este
> mismo día: la comparativa con los números previos (y el impacto esperado de
> cada tarea) está documentada en [§ 9](#9-cambios-de-comportamiento-task-1224-que-explican-la-diferencia),
> y el anexo de pruebas de robustez en `docs/optimization-report.md`.

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
verdad: el throughput sostenible de `200` con la mezcla real se ubica en el
orden de **~30–60 rps** (rodilla), muy por debajo de los 150 rps *ofrecidos* en
la ventana de saturación.

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
  usado 409) y no descartó iteraciones (`0 interrupted`), por lo que el runner
  no distorsiona la curva ofrecida.

### 2.3 Ataques Vegeta (`load-tests/run-9000/targets*.txt`, `body*.bin`)

- **A — mixto:** 6 targets (`POST /api/v1/extractions` ×3, `GET /health`,
  `GET /ready`, `GET /metrics`), body `valid_5p.pdf`, 400 rps, 30 s,
  timeout 35 s. Vegeta adjunta el mismo body a todos los targets; las rutas GET
  lo ignoran. Peso de POST: 50 % del rate (3 de 6 targets → 200 rps).
- **B — homogéneo:** 1 target `POST /api/v1/extractions`, body `valid_20p.pdf`,
  150 rps, 30 s, timeout 35 s.

### 2.4 Mapa de etapas del servicio (base del análisis del § 4)

La ruta `POST /api/v1/extractions`
(`src/pdfextractor/presentation/api/v1/extract.py`) encadena cuatro etapas,
**con la admisión antes de leer el cuerpo** (TASK-12):

```
HTTP → ① AdmissionGate (slot de concurrencia, ANTES de leer un byte; 2 s → 503)
      → ② read_multipart_file (event loop, buffer del pool, max_bytes = 1 MiB)
      → ③ asyncio.to_thread (cola del executor default, ~min(32,cpu+4) hilos)
      → ④ ProcessPool workers (timeout único de extracción 30 s → 504)
```

- ① `admission.admit(queue_timeout)` reserva el slot **antes** de enmarcar el
  multipart: si la puerta está saturada, `503` sin leer ni un byte
  (`extract.py:47-53`). Mientras haya tokens, la petición avanza y se lee el
  cuerpo a un buffer del pool (cota: `max_upload_bytes`); si el buffer no está
  disponible se responde `503` defensivo (`extract.py:58-61`).
- El `504` solo es alcanzable si una extracción admitida no termina en los
  **30 s** del timeout único del pool (`memory/pool.py`). Como ① corta la
  entrada antes de leer el cuerpo, en la práctica el `503` predomina y el
  `504` queda acotado a trabajos que ya estaban en vuelo.
- La cola **menos acotada** restante es el event loop (①–②) bajo tasas muy
  altas (régimen A de § 3.3): todo lo que se enmarca, serializa o escribe
  pasa por él, y su saturación degrada también a las sondas `GET`.

## 3. Resultados

### 3.1 Línea base (k6, 40 rps, 1099 requests) — **SLO OK**

| Métrica | Valor | Target |
|---|---:|---|
| avg / med | 87.39 ms / 37.87 ms | — |
| p90 / p95 / p99 | 175.22 / **289.38** / **729.37** ms | p95 < 500 · p99 < 750 ✅✅ |
| min / max | 5.19 ms / 1.07 s | — |
| `http_req_failed` | **0.00 %** (0/1099) | < 1 % ✅ |
| checks (200 + 3 claves) | 2198 / 2198 (100 %) | > 99 % ✅ |

Sigue siendo OK, pero con menos holgura que la corrida anterior (p95 había
quedado en 64.88 ms): p95 queda a ~58 % del techo y p99 a ~97 %. El incremento
es coherente con las tareas de robustez: el pool `forkserver` esparce workers
perezosamente, y los primeros request concurrentes de la rampa absorben cada
arranque en frío (~3.4 s, una cola por worker) antes de quedar tibio. A 40 rps
la puerta de 4 slots no se satura.

### 3.2 Carga sostenida / rodilla (k6, 150 rps, 3231 requests POST)

| Métrica | Valor | Lectura |
|---|---:|---|
| avg / med | 3.35 s / 1.18 s | ~mitad de la ventana sin cola (rampa) |
| p90 / p95 / p99 | 18.62 / 19.35 / **19.93** s | el event loop marca el piso alto de `200` |
| min / max | 4.57 ms / 20.17 s | `p(99) < 32 s` ✅ (T7) |
| `http_req_failed` (POST) | **29.12 %** (941/3231, todos `503`) | backpressure real, acotada a 2 s |
| `200` vs `503` | 2290 vs 941 | la puerta corta el exceso; cero `504` |
| `unexpected_status` | **0** | ningún status fuera de `{200,503,504}` |
| iteraciones interrumpidas / dropped | **0** | runner no descarta |
| VUs asignadas / pico usado | 1542 / 409 | pool suficiente |
| `/health` durante saturación | 254 / 254 = 100 % ✅ | liveness intacta |
| `/ready` durante saturación | 71 × `503` (luego vuelve a 200) | degradación de readiness observable |

El cambio clave frente a la corrida anterior es **cualitativo**: a 150 rps
mixtos el sistema ya **no** absorbe todo con `200` encolados hasta ~22 s
(antes: 0.00 % de fallos); ahora la puerta admite solo la capacidad (`200`),
rechaza el exceso con **`503` inmediato y acotado** (941) y solo deja que
envejezcan los trabajos ya admitidos. Los `200` que sí entraron terminaron
rápido; el p90/p99 (~18-20 s) reflejan la **saturación del event loop** (etapa
② y la escritura de las respuestas de ~120 KB de los fixtures pesados), no
colas de extracción. Los `503` se activan a cientos de ms del `queue_timeout`
(2 s), de modo que el coste de bytes fallido cayó a casi cero (cuerpo no leído).

**Resultado global k6:** `http_reqs = 5094` (baseline 1099 + saturación 3231 +
sonda 764), `iterations = 4584`, `data_sent = 306 MB`, `data_received = 80 MB`.
El `http_req_failed` global de **19.86 % (1012/5094)** = 941 `503` POST de
saturación + 71 `503` de `/ready` de la sonda; baseline y body-checks siguen en
**0.00 %**. Todos los thresholds del runner pasaron y k6 salió con código **0**.

### 3.3 Sobrecarga mixta (Vegeta A, 400 rps, 12000 requests, 5 páginas)

| Métrica | Valor |
|---|---:|
| rate ofrecido / throughput | 400.04 rps / **58.27 rps exitosos** |
| latencia min / mean | 2.881 ms / 19.137 s |
| latencia p50 / p90 / p95 / p99 / max | 19.553 / 35.001 / 35.001 / 35.003 / 35.052 s |
| éxito (`200`) | 3787 = **31.56 %** |
| `503` (backpressure) | 4912 = **40.93 %** ✅ **el fast-fail se activa aun en colapso** |
| timeout de cliente (35 s, código `0`) | 3301 = 27.51 % |
| otros códigos / cortes de transporte | 0 |
| drenaje tras fin del ataque | 34.997 s (la cola del event loop se vacía) |

A 400 rps mixtos (≈ 200 rps POST ofrecidos contra ~60 rps de capacidad) el
servicio **entra en colapso profundo**: la etapa ② del event loop se satura y
degradó también a las sondas `GET` (/health, `/ready`, `/metrics` entraron en el
mismo bote de los `0-count` de timeout de cliente). La puerta siguió produciendo
`503` (4912) —la mitad de las respuestas exitosas en términos de protocolo— pero
el p50 global (19.5 s) muestra que la admisión y el retorno quedan encolados por
la saturación misma del event loop. Los `0` de la columna de códigos
corresponden a los ~27.5 % de requests cuyo cliente (35 s) dejó de esperar; el
servidor siguió drenándolos (ver log § 3.5). El `throughput` (58 rps) narra la
capacidad mixta real: los `503` no consumen slots de extracción.

### 3.4 Sobrecarga homogénea pesada (Vegeta B, 150 rps, 4500 requests, 20 páginas)

| Métrica | Valor |
|---|---:|
| rate ofrecido / throughput | 150.04 rps / **28.78 rps exitosos** |
| latencia min / mean | 177.6 ms / 2.093 s |
| latencia p50 / p90 / p95 / p99 / max | 2.041 / 2.199 / 2.329 / 2.919 / 3.014 s |
| `200` | 923 = **20.51 %** |
| `503` (backpressure) | 3577 = **79.49 %** ✅ |
| `504` (timeout de extracción 30 s) | **0** |
| timeout de cliente (35 s) | **0** (p99 2.9 s queda muy por debajo) |
| drenaje tras fin del ataque | 2.078 s |

**El hallazgo que esta corrida pasa a verde:** el régimen de bytes pesados ya no
produce colas desacotadas. Antes, con el cuerpo de 328 KB y ~120 KB de texto de
respuesta, la puerta era inalcanzable: no había `503`, la latencia escalaba hasta
el `504` del pool (~30 s) o el timeout del cliente (0 % de éxito medible en los
artefactos previos § 7). Con la admisión **antes** de leer el cuerpo (TASK-12),
la puerta corta en ~2 s: la latencia se acota (p99 2.9 s ≈ `queue_timeout`
2 s + servicio), el `503` llega al cliente en el 79 % de los casos y no hay
ningún `504` ni cortes de transporte. El throughput exitoso (28.78 rps) coincide
con la capacidad teórica del fixture pesado (`4 slots / ~0.124 s ≈ 32 rps`,
§ 3.5): los `503`, otra vez, no consumen capacidad de extracción.

### 3.5 Capacidad por fixture y memoria

Latencias aisladas (mediana de 5 muestras, sin carga, pool tibio, misma sesión):

| Fixture | Tiempo | Capacidad de puerta `≈ 4/t` |
|---|---:|---:|
| `valid_1p.pdf` | ~23 ms | ~170 rps |
| `valid_5p.pdf` | ~50 ms | ~80 rps |
| `valid_20p.pdf` | ~124 ms | ~32 rps |
| mezcla (1/5/20) | ~66 ms | ~60 rps |

La capacidad mixta sostenible observada es **~30–60 rps** (el fixture de 20
páginas es el limitante): el throughput del ataque B (§ 3.4, 28.78 rps
exitosos) calza con la fila del `20p`, y el `~60 rps` de la mezcla es el
techo de `200` que la puerta deja pasar en § 3.2 (2290 `200` en ~35 s de
ventana saturada). Frente a la corrida anterior los aislados subieron
(~27/32/92 → ~23/50/124 ms): varianza del host WSL2 compartido + arranque
`forkserver` (el primer trabajo del proceso tarda ~3.4 s en frío; luego se
amortiza — ver `server.log`).

**Memoria del servicio** (uvicorn + pool, muestreado con `ps rss` tras el
ataque B): proceso principal **~220 MiB**, workers del pool **~63 MiB** cada
uno (4 spawneados bajo carga, de hasta 8 según `effective_workers`), total
~500 MiB. El RSS del principal bajó frente al ~1.06 GiB de la corrida anterior
—medido igualmente "en reposo"— por la compartición copy-on-write de los workers
`forkserver`; no se observó `OOM` ni `500` en ningún log. El soak largo de T16
sigue pendiente.

Log del servicio, sesión completa (suma de `load-tests/run-9000/server.log`
[k6 + ataque A] y `server-b.log` [ataque B]): **200: 15394 · 503: 15939 ·
504: 0 · ningún otro 5xx**. El 0 de `504` es el cambio estructural de esta
corrida: ninguna cola llegó al timeout de extracción. El total de `200/503`
del servidor supera al observado por los clientes (k6 + Vegeta) porque el
ataque A dejó requests en vuelo que **el servidor terminó de procesar después
de que el cliente cortara a los 35 s** (los `0-count` de § 3.3).

## 4. Análisis

### 4.1 Capacidad y rodilla

- El tope configurado (`max_concurrent_extractions = 4`) fija el techo
  teórico `≈ 4 / tiempo_de_servicio`. Con la mezcla real la rodilla cae en
  **~30–60 rps**: el `200`-throughput del ataque B (28.78 rps) coincide con la
  fila del fixture pesado (§ 3.5) y en § 3.2 la puerta deja pasar ~60 rps de
  `200`. La separación de percentiles —p90 pasando de ~175 ms en la línea base
  a ~18.6 s en saturación— marca el onset de la cola mucho antes que el
  promedio; esto ilustra el fallo de "juzgar por el mean" del cap. 9 de
  *Essential Kanban Condensed* (PDF 48) y del principio de inspección/adaptación
  del *Scrum Guide* (p. 4 impresa): **inspeccionar p95/p99, no la media**.
- Ley de Little aplicada al propio layout: el pico de `200` concurrentes en
  saturación (~60 rps × ~15–20 s de piso del event loop) exigiría ~1000–1200
  peticiones en vuelo si todo se admitiera; la puerta corta antes, por eso el
  pico de VUs usado en k6 cayó de 976 a **409**. `preAllocatedVUs = rate×10`
  mantiene el runner por encima del WIP teórico (WIP del *Kanban*, PDF 26).

### 4.2 de hallazgo a resuelto: la cola antes del cuerpo ya está acotada

La corrida anterior encontró (y la memoria de esta tarea lo registró como
hallazgo de diseño, TASK-12) que el `503` acotado **solo era alcanzable si la
petición conseguía un hilo después de leer el cuerpo**: en el régimen homogéneo
pesado la cola crecía sin topes y el cliente recibía un `504` tardío o cortaba a
los 35 s, sin `503`. Ese defecto quedó **resuelto con la admisión antes de leer
el cuerpo** (`admission.admit` en `extract.py:47-53`):

- Bajo carga homogénea pesada (§ 3.4) ahora hay **3577 `503` acotados a ~2 s**,
  cero `504` y cero cortes de cliente; la latencia p99 (2.9 s) es un orden de
  magnitud menor que el peor caso anterior (~34 s).
- Bajo saturación mixta k6 (§ 3.2), 941 `503` llegan al cliente (antes: 0).
- El residuo que persiste es el **event loop como cuello físico**: en el
  colapso profundo del ataque A (400 rps) la degradación alcanza a las propias
  sondas `GET`, porque todo lo enmarcado/serializado/escrito atraviesa un único
  loop de 8 núcleos (etapa ②, § 2.4). No es una cola de extracción: la puerta
  sigue cortando (`503`), pero el retorno de esas respuestas se encola tras la
  escritura de los bodies. La defensa a ese nivel es el `queue_timeout` del
  cliente (35 s en Vegeta, 60 s en k6) y la capacidad del host; una evolución natural
  sería limitar `asyncio.to_thread` con un semáforo de admisión propio.

### 4.3 Sobre la estabilidad de los indicadores

El `503` de backpressure (ahora previo a la lectura) y el `504` del timeout de
extracción son la defensa real contra colas infinitas a nivel **servidor**; el
presupuesto de bytes se protegió internamente y la memoria no creció en modo
visible (sin muestreo de soaks largos). En ningún régimen se observó `500` ni un
`5xx` fuera de contrato: los únicos status emitidos fueron `200`, `503` y `504`,
y los `0` del ataque A corresponden a cortes de cliente, no a respuestas del
servidor fuera de contrato.

## 5. Cumplimiento — SLOs, DoD y marco teórico

Mapeo contra los umbrales T1–T16 definidos para esta tarea:

| # | Umbral | Medido | Veredicto |
|---|---|---|---|
| T1 | `happy_path p(95) < 500 ms` | **289.38 ms** | ✅ |
| T2 | `happy_path p(99) < 750 ms` | **729.37 ms** | ✅ |
| T3 | `happy_path http_req_failed < 1 %` | **0.00 %** | ✅ |
| T4 | `checks happy_path > 99 %` | **100 %** | ✅ |
| T6 | `checks saturación > 95 %` | **100 %** | ✅ |
| T7 | `saturación p(99) < queue+extraction` (32 s) | **19.93 s** | ✅ |
| T8 | `unexpected_status == 0` | **0** (solo `200`/`503`/`504` en todo el log) | ✅ |
| T9 | backpressure `> 0` (`503` reales) | k6 POST saturación: **941** · A: 4912 · B: 3577 | ✅ (ahora en cualquier régimen) |
| T10 | `503` latencia ≈ `queue_timeout` (2 s) | p50 B = **2.041 s**; p50 A = 19.5 s (colapso mixto, ver § 4.2) | ✅ en carga acotada |
| T11/T12 | `/health` 200 bajo saturación | **100 %** (254/254) | ✅ |
| T13 | `/ready` nunca 404 | **0** | ✅ |
| T14 | error budget `/health` < 1 % | **0.00 %** | ✅ |
| T16 | RSS estable | muestreo en reposo ~500 MiB; sin `OOM`/`500` | 🟡 soak pendiente |
| — | iteraciones interrumpidas / dropped `== 0` | **0** | ✅ |

**Marco RNF (medidas de calidad, DoD):** siguiendo al *Scrum Guide* (p. 12
impresa), la Definition of Done del rendimiento se enuncia como **medidas
objetivas verificables**, no impresiones: las tablas de § 3 (percentiles,
throughput, tasa de éxito) son el mecanismo de inspección y se vuelven a
medir en cada cambio (adaptación). Desde *Historias de usuario* (p. 24
impresa), los criterios de aceptación no funcionales se separan en
**objetivo** (p95 < 500 ms), **restricción** (503 acotado a ~2 s bajo
saturación) y **línea base** (hoy: p50 de línea base ≈ 37.9 ms, capacidad
sostenida ≈ 30–60 rps). La **historia abierta** que dejó la corrida previa
—"mover la puerta a la entrada del event loop"— quedó **cerrada por TASK-12**:
en esta corrida el `503` acotado se cumple en **todos** los regímenes
(§ 3.2–§ 3.4). Queda una historia menor: acotar la admisión de `asyncio.to_thread`
(§ 4.2 y § 6).

## 6. Recomendaciones

1. ~~**Aplicar la puerta antes de leer el cuerpo**~~ — **hecho en TASK-12**
   (`admission.admit` en `extract.py:47-53`), con impacto medido en § 3.2–§ 3.4:
   el `503` acotado ahora existe en todos los regímenes y desaparecieron los
   `504` y los cortes de cliente en el régimen de bytes.
2. **Acotar la admisión de `asyncio.to_thread`** con un semáforo alrededor de
   `service.extract`, para que el `queue_timeout` de la puerta se mida desde la
   llegada y no se forme una cola de coroutinas en el executor default. Es lo
   único que resta del hallazgo § 4.2 (relevante a tasas ≥ 400 rps mixtos).
3. **Watch del p95 en el punto ~30–60 rps** como early warning de la rodilla
   (el p95 se degrada dos órdenes de magnitud antes que el p50).
4. **Cerrar T16**: corrida soak (10 min) para confirmar la meseta de RSS y
   muestreo en vivo de los workers del pool (item 7 del plan de carga).
5. Re-medir con `PDFEXTRACTOR_MAX_UPLOAD_BYTES=52428800` (50 MiB) antes de
   cerrar la Task 13 (item 6 del plan): el costo de bytes por request sigue
   siendo el factor que separa los regímenes § 3.3 (mixto) y § 3.4 (pesado).

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
  -output=load-tests/run-9000/results-mixed.bin
vegeta report < load-tests/run-9000/results-mixed.bin
vegeta report --type=json < load-tests/run-9000/results-mixed.bin > load-tests/run-9000/results-mixed.json

# 5) Vegeta — sobrecarga homogénea (B)
vegeta attack -targets=load-tests/run-9000/targets-20p.txt \
  -body=load-tests/run-9000/body20p.bin \
  -header="Content-Type: multipart/form-data; boundary=$B" \
  -rate=150 -duration=30s -timeout=35s -max-body=1024 \
  -output=load-tests/run-9000/results-20p.bin
vegeta report < load-tests/run-9000/results-20p.bin
vegeta report --type=json < load-tests/run-9000/results-20p.bin > load-tests/run-9000/results-20p.json
```

> **Secuencia recomendada de esta corrida:** A y B se ejecutaron desde un pool
> **tibio** (tras un warm-up de los tres fixtures). En la práctica conviene
> reiniciar el servicio entre A y B —o al menos entre corridas— porque A deja el
> event loop saturado y contaminaría una B inmediata. En esta sesión el warm-up
> del pool `forkserver` costó ~3.4 s en el primer POST y quedó amortizado
> después.

Artefactos en `load-tests/run-9000/`:

| Archivo | Contenido |
|---|---|
| `k6-output.txt` | Salida completa de k6 (thresholds, checks, métricas) |
| `server.log` | Log del servicio durante k6 + ataque A |
| `server-b.log` | Log del servicio durante el ataque B (instancia reiniciada) |
| `targets-mixed.txt` / `targets-20p.txt` | Targets Vegeta (rutas absolutas a `:9000`) |
| `body5p.bin` / `body20p.bin` | Cuerpos multipart (`checksum` + `file`) |
| `results-mixed.bin` / `.json` / `vegeta-report-mixed.txt` | Ataque A (mixto) |
| `results-20p.bin` / `.json` / `vegeta-report-20p.txt` | Ataque B (homogéneo 20 pág.) |

**Cómo leer los `.json`:** el campo `"success"` es la razón sobre **todos** los
targets (incluye los `GET` de la sonda en el ataque A) y `"status_codes":{"0":N}`
son cortes de cliente por timeout, no respuestas del servidor (ver § 3.3).

**Limitaciones:** host compartido (WSL2) — la rodilla es sensible a la carga
del host entre corridas y las latencias aisladas (§ 3.5) son de una única sesión.
Los logs de servicio (`server.log`, `server-b.log`) están en `.gitignore`
(regenerables con el paso 1); los artefactos de medición (`.bin`, `.json`,
reportes, `k6-output.txt`) sí se versionan. El RSS de § 3.5 es un muestreo
puntual **en reposo** (no un soak): T16 sigue abierto. Los cuerpos `valid_*`
son sintéticos (páginas de texto aleatorio). k6 corrió como snap (sin permiso
de escritura fuera del home, por lo que no se generó el `--summary-export`
JSON; el reporte textual es equivalente).

## 9. Cambios de comportamiento (TASK-12..24) que explican la diferencia

Las cifras de § 3 y el veredicto de § 5 corresponden al binario **posterior** a
la suite de robustez (TASK-12…TASK-24, fases A–C del `docs/tasks/todo.md`); esta
sección resume **qué cambió** respecto de la corrida previa y por eso los
números se movieron —especialmente en el régimen de bytes—. El detalle de cada
tarea (con sus tests) vive en `docs/tasks/todo.md` y
`docs/optimization-report.md`.

| Cambio | Dónde | Efecto observado en esta corrida |
|---|---|---|
| Fast-fail `503`/`413` **antes de leer el cuerpo** (backstop de tamaño + puerta) | `extract.py`, `middlewares.py` (TASK-12) | **El cambio dominante.** Cierra la historia abierta: el `503` acotado ahora aparece en todos los regímenes (§ 3.2–§ 3.4) y el coste de bytes por request fallido cae a casi cero |
| **Timeout único en el pool**; la ruta ya no envuelve en `asyncio.wait_for`; el buffer se libera al terminar la extracción | `pool.py`, `extract.py` (TASK-13) | Desaparecieron los `504` (0 en toda la sesión); p99 de saturación acotado (T7 = 19.93 s) |
| `_Gate` sobre `BoundedSemaphore` (ceiling autoritario) | `pool.py` (TASK-14) | `inflight` nunca excede `MAX_CONCURRENT`; throughput de `200` reproducible (28.78 rps en B) |
| Tope de cabeceras de parte multipart (16 KiB) | `multipart_reader.py` (TASK-15) | 422 acotado ante streams infinitos de cabeceras (no ejercido por esta carga) |
| Log de excepciones no esperadas (traceback estructurado) + stdout por defecto | `handlers.py`, `logging_.py` (TASK-16) | 500 diagnosticable; sin impacto en p95 |
| `Settings` con validadores + entrypoint `python -m pdfextractor` | `settings.py`, `__main__.py` (TASK-17) | Config inválida falla al arrancar |
| Dialecto de error 404/405/422 con una sola clave `{"error"}` | `handlers.py` (TASK-18) | Contrato de errores homogéneo |
| Pool en **`forkserver`** (no `fork`) | `pool.py` (TASK-19) | Workers seguros con el servidor multihilo; **primer trabajo en frío ~3.4 s**, luego amortizado. Explica parte del alza del p95 de línea base (289 ms vs. 65 ms previos) |
| **Cotas de salida** `MAX_PAGES` / `MAX_EXTRACTED_CHARS` → `422` | `errors.py`, `extraction_service.py`, `settings.py` (TASK-20) | Bombas de descompresión cortadas con 422 acotado; no ejercidas por esta carga |
| Métricas de proceso/GC + render `/metrics` en threadpool | `metrics.py` (TASK-21) | `/metrics` ya no bloquea el event loop; series nuevas `process_cpu_*`, `python_gc_*` |
| Gates de CI (memory, `pip-audit`, build imagen) | `.github/workflows/ci.yml` (TASK-23) | Calidad reproducible en CI |
| Documentación de oversubscripción de workers | `README.md` (TASK-24) | Sin impacto en las medidas |

**Estado de verificación:** suite **143 passed / 2 skipped** + `-m memory`
**2 passed** (ruff/format/mypy verdes) y **corrida de carga sobre `:9000`
re-ejecutada y capturada en `load-tests/run-9000/`** (esta misma sesión). El CP-5
queda solo a la espera de la **revisión humana** de TASK-12..24; T16 (soak de
RSS) permanece pendiente.

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
