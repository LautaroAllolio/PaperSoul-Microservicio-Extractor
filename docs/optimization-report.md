# Auditoría de código y optimización — `pdfextractor`

> **Fecha:** 08/10/2026 · **Alcance:** auditoría de solo lectura del árbol fuente completo; **no se aplicó ningún cambio de código** — los cambios propuestos se entregan en §3 como diffs listos para aplicar por decisión del equipo.
> **Base de mediciones:** `docs/report.md` (carga k6 + Vegeta). **Dependencia verificada:** `pymupdf 1.28.2` instalada.
> **Gates de calidad ejecutados como parte de esta auditoría:** `ruff check`, `ruff format --check`, `mypy -p pdfextractor`, `pytest -q` y `pytest -m memory` — todos verdes (§4.3).

---

## 1. Auditoría de la librería de extracción (PyMuPDF)

### 1.1 Estado actual (verificado en `src/pdfextractor/infrastructure/extraction/pymupdf_extractor.py`)

Flujo del adaptador (`pymupdf_extractor.py:32-49`):

1. `io.BytesIO(data)` — **una única** envoltura en memoria del payload (hay un test que lo pinza: `test_memory.py:155-188`).
2. `pymupdf.open(stream=buffer, filetype="pdf")` — el documento no toca disco (`filetype` explícito evita sniffing por extensión).
3. `document.needs_pass` → `EncryptionError` (422 «no se pudo leer: cifrado»).
4. `document.page_count == 0` → `UnreadableError` (422).
5. Texto = `"".join(page.get_text() for page in range(page_count))` — extracción de texto plano por página.
6. `with document:` garantiza el cierre del handle del documento en **todos** los caminos (éxito, cifrado, corrupción).

El trabajo vive en un `ProcessPoolExecutor` (`concurrency/pool.py:109`) porque MuPDF retiene el GIL; `pymupdf_extract` es la entrada de worker (`pool.py:23-25`).

### 1.2 Comparativa con alternativas

| Librería | Motor | Texto plano | Memoria | Layout/tablas | Licencia | Streaming in-memory | Mantenimiento | Nota |
|---|---|---|---|---|---|---|---|---|
| **PyMuPDF (actual)** | MuPDF (C++) | **El más rápido** | **Baja** (buffer + doc en heap) | Básico | **AGPL-3.0** | Sí (`stream=`) | Activo, 1.28.x | La opción por defecto para PDF→texto puro |
| `pdfplumber` | pdfminer.six | 10–50× más lento | Media (retiene objetos por página) | **Excelente** (tablas/curvas) | MIT | Parcial | Activo | Solo si el producto algún día necesita tablas |
| `pypdf` | Python puro | Lento | Baja | Ninguno | BSD-3 | Sí | Activo | Sin parsing de layout real |
| `pdfminer.six` | Python puro | Lento | Alta (LAParams) | Bueno | MIT | Parcial | Activo | Base de `pdfplumber` |
| `pypdfium2` | PDFium (Chrome) | Muy rápido | Baja–media | Básico | **BSD-3** | Sí (`FPDF_LoadMemDocument`) | Activo | La mejor alternativa *comercially friendly* |

**Decisión:** PyMuPDF es la elección correcta para el caso de uso de este microservicio (texto plano, cero disco, memoria acotada, latencia de cola sensible). No se recomienda migrar por rendimiento ni memoria.

### 1.3 Veredicto y micro-optimizaciones del adaptador

1. **Licencia — consideración de negocio, no de código.** PyMuPDF es **AGPL-3.0**. Sirviéndolo como servicio interno/privado no hay obligación de divulgación; en un despliegue SaaS hacia terceros o en código redistribuido la AGPL impone publicar el servicio. Si eso es un bloqueo, `pypdfium2` (BSD-3) es el sustituto natural; requiere validar calidad de texto en el corpus real.
2. **`convert_to_text()` descartado.** Verificado: **no existe** en PyMuPDF 1.28.2 (`hasattr(pymupdf.Document, "convert_to_text") == False`). No aplicar ese supuesto.
3. **Mantener `get_text()` por página con el modo por defecto** (`"text"`). No agregar `sort=True`: ordenar por coordenadas cuesta CPU extra y no hace falta para el contrato «texto plano».
4. **`page_count` se evalúa 3 veces** (`pymupdf_extractor.py:40,45,49`) — es una propiedad barata de MuPDF; costo despreciable, sin cambio.
5. **No hay doble normalización del texto**: la única normalización vive en la capa de aplicación (`extraction_service.py:32-34`, NFC + colapso de `\n{3,}`); el adaptador devuelve el texto crudo. Correcto según el diseño (D6).
6. **Doble materialización en la frontera de servicio ya eliminada** (cubierta por `test_memory.py:91-112`): el service recibe el `bytearray` del pool, nunca `bytes(buffer)`.

---

## 2. Código muerto y residuos (junk)

### 2.1 Hallazgos confirmados

| # | Ítem | Ubicación | Tipo | Recomendación |
|---|---|---|---|---|
| J1 | `_text_extractor_port: TextExtractor = PyMuPDFExtractor()` | `pymupdf_extractor.py:52` | **Símbolo muerto** | Eliminar la línea (y el import de `TextExtractor` que queda huérfano). El único uso real de la clase es `pymupdf_extract` en `pool.py:25`. No lo marca ruff porque es una variable con valor, no un import. |
| J2 | PYC huérfano `test_timeout.cpython-312-pytest-9.1.1.pyc` | `tests/extractor/integration/__pycache__/` | Residuo de test eliminado | La fuente `test_timeout.py` ya no existe (fue reemplazada por `test_concurrency.py`). El `.pyc` es `__pycache__` (ya gitignored); eliminar junto con el resto de caches. |
| J3 | Caches de tooling | `src/.mypy_cache/`, `.ruff_cache/` | Residuos de desarrollo | Gitignored (`src/.mypy_cache` cae bajo la regla `.mypy_cache/` de `.gitignore:171`). Limpieza opcional pre-push o en CI. |
| J4 | `docs/perf-report.md` **obsoleto** | `docs/perf-report.md` | Documentación duplicada con referencias rotas | Es el tracker Task 11-13 con secciones aún «PENDIENTES» y cita archivos que **ya no existen** (`docs/tasks/todo-extractor.md`, `docs/tasks/plan-extractor.md` → hoy `docs/tasks/todo.md` y `docs/tasks/plan.md`). La medición real de rendimiento quedó en `docs/report.md`. **Mover a `docs/tasks/archive/`** (fuera del índice de doc viva) o mendigar las referencias; no borrar: contiene la definición de SLOs y método de las Tasks 11-13. |
| J5 | Artefactos de carga grandes (untracked) | `load-tests/` | Evidencia de trabajo | Conservar los que referencia `docs/report.md` (resultados JSON/txt, `load-test.js`, bodies). `server.log` (13 MB), `results.bin` (11 MB) y `results-saturacion.bin` (3 MB) aportan poco una vez resumidos en tablas; comprimir o eliminar si no se van a publicar. No borrar sin confirmación del dueño de la tarea. |

### 2.2 Dependencias — ninguna muerta

Revisado import por import: `fastapi` (app), `prometheus-client` (`metrics.py`), `pydantic`/`pydantic-settings` (`settings.py`, schemas), `pymupdf` (adaptador), `uvicorn` (server). Dev: `httpx`, `mypy`, `pytest`, `pytest-asyncio`, `ruff` (CI + tests). Sin dependencias superfluas ni `python-multipart` (deliberadamente ausente; ver `multipart_reader.py:7-8`).

### 2.3 Evidencia: CI en verde

- `ruff check .` → sin hallazgos.
- `ruff format --check src tests` → 47 archivos ya formateados.
- `mypy -p pdfextractor` (strict) → sin hallazgos.
- `pytest -q` → **98 passed**; `pytest -m memory` → **2 passed** (100/100 total verdes).

---

## 3. Optimización de rendimiento

### 3.1 Diagnóstico: la cola real no está en la puerta (confirmado en código + mediciones)

Cadena actual del endpoint (`presentation/api/v1/extract.py:38-52`):

```
pool.acquire()                          # extract.py:39  → None si el pool (4 buffers) está lleno
    ↓ si None → read_multipart_file(sink=None)
multipart_reader.py:59                  # target = bytearray() si sink es None → NUEVO buffer por request
    ↓ body leído COMPLETO y retenido
asyncio.to_thread(service.extract, ...) # extract.py:50 → executor por defecto = min(32, cpu+4) hilos
    ↓ dentro: gate.acquire(queue_timeout=2s)  (pool.py:119) → 503 si tarda
```

Consecuencias (coinciden con `docs/report.md` §4.2):

1. **La memoria NO está acotada bajo overload.** `BufferPool` **es** el techo ideal (`memory/pool.py:21-30` devuelve `None` en lugar de crecer; `main.py:57` lo dimensiona a `max_concurrent`), pero `sink=None` anula el techo: cada request extra mientras el pool está lleno asigna un `bytearray` propio y retiene el cuerpo completo. En la prueba Vegeta A (400 rps mixtos) eso fueron **1 756 cuerpos** leídos y descartados.
2. **La cola de atrás no es la puerta.** La puerta mide 4 permits/2 s y genera el 503, pero la cola *real* vive en dos lugares no controlados: (a) corrutinas del event loop bloqueadas leyendo el body y (b) hasta 32 hilos del executor por defecto haciendo submit+espera sobre la puerta. `queue_depth` (`pool.py:156-157`) solo observa la puerta, **no** esta cola.
3. **I/O descartado bajo saturación.** El 503 llega *después* de leer el body entero; en Vegeta B (homogéneo, 150 rps) el fast-fail **nunca se activó** (0×503): el collapso fue 29,7 rps efectivos, p50 = timeout de cliente (35 s) y 168×504. El 503 solo aparece cuando el *workload* mezcla ráfagas (fue el caso de Vegeta A, 14,6 %).

El cuello de throughput serio (p90 14,8 s a 150 rps) lo pone la cola de `to_thread` + los cuerpos leídos, no el cómputo MuPDF (el extractor normal hace 1p≈32 ms, 5p≈40 ms).

### 3.2 Cambios propuestos (diffs listos para aplicar)

#### A. Fast‑fail **antes** de leer el cuerpo — `presentation/api/v1/extract.py` + `main.py` *(recomendado, alto impacto)*

Adquirir un slot **asíncrono** con `asyncio.Semaphore` (qty = `max_concurrent`) **antes** de leer el multipart: el 503 llega a los ~2 s de cola (o inmediato si la cola es 0) **sin haber leído un solo byte del body**, y el número de buffers vivos queda hard-bound al pool.

`main.py` (lifespan):

```diff
         app.state.settings = resolved
         app.state.ready_state = ReadyState()
+        app.state.extraction_slots = asyncio.Semaphore(resolved.effective_max_concurrent_extractions)
```

`extract.py`:

```diff
     settings = request.app.state.settings
     boundary = boundary_from_content_type(request.headers.get("content-type", ""))
-    pool = request.app.state.pool
-    sink = pool.acquire()
+    slots = request.app.state.extraction_slots
+    pool = request.app.state.pool
+    try:
+        await asyncio.wait_for(
+            slots.acquire(), timeout=settings.queue_timeout_seconds
+        )
+    except TimeoutError:
+        raise OverloadError()
+    sink = pool.acquire()  # slots == pool.capacity ⇒ nunca None
+    assert sink is not None, "slot adquirido sin buffer disponible"
     try:
         buffer = await read_multipart_file(
             request.stream(),
             boundary=boundary,
             max_bytes=settings.max_upload_bytes,
             sink=sink,
         )
         started = time.perf_counter()
-        try:
-            result = await asyncio.wait_for(
-                asyncio.to_thread(service.extract, buffer),
-                timeout=settings.extraction_timeout_seconds,
-            )
-        except TimeoutError as exc:
-            raise ExtractionTimeoutError() from exc
+        result = await asyncio.wait_for(
+            asyncio.to_thread(service.extract, buffer),
+            timeout=settings.extraction_timeout_seconds,
+        )
         duration = time.perf_counter() - started
         ...
     finally:
         if sink is not None:
             pool.release(sink)
+        slots.release()
```

Semántica: se preserva el `503` tras `queue_timeout_seconds` de espera (la cola ahora es el semáforo, real y acotada), y el `extraction_timeout`→`504` queda igual. El gate interno (`pool.py:119-121`) se vuelve trivial (nunca lleno) pero se deja como red de seguridad; su `ready_state` sigue siendo la fuente de `/ready`.

#### B. Executor acotado en vez de `asyncio.to_thread` *(opcional, medio impacto — baja prioridad si A entra)*

Con A, a lo sumo `permits` corrutinas llegan al `to_thread`, así que ya no se acumulan 32 hilos. De persistir el objetivo de aislar el hilo de submit del executor por defecto (compartido con otras libs):

```python
import threading
from concurrent.futures import ThreadPoolExecutor

EXTRACT_THREADS = threading.local()

def _executor() -> ThreadPoolExecutor:
    if not hasattr(EXTRACT_THREADS, "loop"):
        EXTRACT_THREADS.loop = ThreadPoolExecutor(max_workers=...)  # granularidad de app
    return EXTRACT_THREADS.loop
```

y en la ruta `loop.run_in_executor(_executor(), service.extract, buffer)` con el mismo `wait_for`. La prioridad es **A**, que ya elimina el pileup.

#### C. Techo de concurrencia conservador por defecto — `config/settings.py` *(bajo, rápido)*

Hoy `effective_max_concurrent_extractions` = workers (`settings.py:37-43`): en una máquina de 32 CPUs el aforo por defecto es `32 × 50 MB = 1,6 GB` RAE solo de buffers, más los procesos. Acotar por presupuesto de memoria:

```python
_MAX_TOTAL_BUFFER_BYTES = 512 * 1024 * 1024  # 512 MB de aforo por instancia

@property
def effective_max_concurrent_extractions(self) -> int:
    if self.max_concurrent_extractions is not None:
        return self.max_concurrent_extractions
    by_memory = max(1, _MAX_TOTAL_BUFFER_BYTES // self.max_upload_bytes)
    return min(self.effective_workers, by_memory)
```

Esto mantiene el invariante «memoria = `max_upload × concurrent`» (README, sección Escalado) explícito y escala horizontalmente en orquestación. Batch: para 50 MB default → 10 concurrentes; para 5 MB → 32 (o workers).

#### D. Observabilidad de la cola real *(menor)*

`queue_depth` hoy refleja el gate, no la espera de `to_thread`. Con A, añadir al route (o a `Metrics.observe_request`) una gauge del semáforo (`permits - slots._value`) como `extractor_queue_depth` para que Prometheus muestre la cola que realmente importa.

### 3.3 Impacto esperado (con la evidencia de `docs/report.md`)

| Escenario medido | Hoy | Con A |
|---|---|---|
| Vegeta A — 1 756 pedidos 503 (14,6 %) | leen su body completo y esperan ≤2 s en la puerta | **503 inmediato, 0 bytes leídos, 0 memoria nueva** |
| Saturación 150 rps (p90 14,8 s) | cola en event loop + 32 hilos `to_thread` | cola acotada = semáforo; trabajos ≥ inmediato |
| Régimen mixto 1p/5p (T1–T6, SLO verde) | sin cambio | sin cambio (A no toca el camino sin overload) |
| Memoria RAM | sin tope efectivo durante overload | **tope duro = pool × `max_upload`** |

El p95/%ile bajo régimen normal **no** mejora (está en el cómputo MuPDF serial y en el pool de 4 procesos): más throughput de saturación requiere más workers por instancia (C permite dimensionarlo) o más instancias.

### 3.4 Priorización

| Cambio | Impacto | Esfuerzo | Riesgo | Estado |
|---|---|---|---|---|
| **A. Fast-fail antes del body** | Alto (memoria + saturación) | Bajo (1 archivo + ruta) | Medio (semántica de cola; cambios de tests) | Propuesto |
| **C. Techo de concurrencia** | Medio | Trivial | Bajo | Propuesto |
| **B. Executor dedicado** | Medio (aislamiento) | Bajo | Bajo | Opcional, subsumido por A |
| **D. Gauge de cola real** | Bajo | Trivial | Bajo | Propuesto |

`test_concurrency.py` (Task 9: gate, overload → 503, `/ready` degradado, crash/restart) sigue siendo la rejilla de validación: A debe conservar los 4 comportamientos (la puerta permanece; solo cambia dónde se espera).

---

## 4. Buenas prácticas y gestión de memoria

### 4.1 Cierre de recursos (revisión línea por línea) — **todo correcto hoy**

- **Documento PyMuPDF:** `with document:` (`pymupdf_extractor.py:37`) cubre éxito, `EncryptionError` y `UnreadableError`; no hay handle que sobreviva a la llamada.
- **`io.BytesIO`:** dentro del `with` (`:32`).
- **Lifespan de FastAPI:** `finally: extractor.close()` (`main.py:71`); `close` es idempotente y drena el pool (`concurrency/pool.py:137-143`).
- **Pool de buffers:** free-list aforada; `release` no puede exceder `capacity` (`memory/pool.py:32-35`).
- **Backstop de tamaño:** `SizeBackstopMiddleware` rechaza por `Content-Length` antes de encuadrar (`middlewares.py:81-101`); el reader corta el stream en el guard mid-stream (`multipart_reader.py:61-66`, `_append` `:176-179`).
- **Errores:** el catch-all degrada a `500 {"error":"internal"}` sin filtrar bytes ni detalles (`handlers.py:16-33`); los logs jamás reciben contenido de documento (`middlewares.py:60-75`, `logging_.py:18-28`).
- **Prometheus:** registro privado por app para que apps/tests en un mismo proceso no contaminen contadores (`metrics.py:44-49`, `render` refresca gauges en scrape).

### 4.2 Riesgos y observaciones de memoria/concurrencia

1. **RSS medido sin fuga:** idle ≈ 0,6 GiB (import MuPDF + pool de 4 procesos), pico de carga ≈ 0,97 GiB, estable durante bursts (perf report). El salto idle→pico es caché de mmap del allocador, no crecimiento de fuga.
2. **Único punto de memoria no acotada:** el `sink=None` de §3.1 ramp; cerrado por el cambio A.
3. **Forward-compat fork:** pytest mostró `DeprecationWarning: process is multi-threaded, use of fork()` (Python 3.12, Popen de `ProcessPoolExecutor`). Python 3.14 cambia el método por defecto; al lanzar el pool desde un app multi-hilo conviene testear con `multiprocessing` contexto `forkserver`/`spawn` (bajo esfuerzo, antes de subir de runtime).
4. **Prometheus sync en el loop:** `observe_extraction`/`observe_request` son bloqueantes pero triviales (histogramas en memoria); aceptable al volumen del contrato. No usar `generate_latest` en la ruta caliente.

### 4.3 Verificación final ejecutada (08/10/2026)

```text
uv run ruff check .                 → sin hallazgos
uv run ruff format --check src tests → 47 files already formatted
uv run mypy -p pdfextractor          → success: no issues found
uv run pytest -q                     → 98 passed, 2 skipped (memory deseleccionados)
uv run pytest -m memory -q           → 2 passed
```

---

## 5. Resumen ejecutivo

1. **Librería:** PyMuPDF es la decisión correcta (rendimiento y memoria) y está bien usada (un solo `BytesIO`, cierre garantizado, cero disco). Único asunto no técnico: **licencia AGPL-3.0** → evaluar contra política de despliegue; `pypdfium2` (BSD-3) como plan B.
2. **Junk:** un símbolo muerto seguro (`_text_extractor_port`), un `.pyc` huérfano, caches de tooling, un doc obsoleto (`docs/perf-report.md`, mover a archive) y artefactos de carga de 13-11 MB a decidir. Cero dependencias muertas; 100/100 tests verdes.
3. **Rendimiento — un solo cambio gana casi todo:** adquirir el slot con un `asyncio.Semaphore` **antes** de leer el body (§3.2-A). Mata la cola no acotada, el `bytearray` nuevo bajo overload y el I/O descartado de los 503 (1 756 cuerpos en Vegeta A), y devuelve a la memoria su techo determinista `max_upload × concurrent`. Secundarios: techo de concurrencia conservador (C), executor dedicado (B, opcional si entra A), gauge de cola real (D).
4. **Memoria/recursos:** manejo de cierres ya correcto en todos los puntos; sin fugas observadas (0,97 GiB pico estable). Cerrar el hueco de `sink=None` (A) y vigilar el cambio de `fork` en Python 3.14.

---

## Anexo — Reproducción de referencia

- Método y comandos completos de carga: `docs/report.md` (baseline 40 rps p95 104,8 ms; saturación 150 rps; Vegeta A/B para régimen mixto/homogéneo).
- Costs de medición del techo de memoria: `tests/extractor/performance/test_memory.py` (`pytest -m memory`).
- Invariante de arquitectura que respalda el cambio A: «memoria = `MAX_UPLOAD_BYTES × MAX_CONCURRENT_EXTRACTIONS`» (README §Despliegue/Escalado).