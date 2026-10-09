/**
 * Prueba de rendimiento del microservicio `pdfextractor` (docs/report.md).
 *
 * Endpoints ejercidos (docs/api-contract.md):
 *   POST /api/v1/extractions — extracción multipart, los tres fixtures happy path
 *   GET  /health           — liveness, debe mantenerse 200 bajo saturación
 *   GET  /ready            — readiness, puede degradar a 503 con la puerta llena
 *   GET  /metrics          — Prometheus text, no debe caer
 *
 * Fases (secuenciales, la saturación nunca contamina la línea base):
 *   1. extract_baseline — rampa 0 → BASELINE_RPS (bajo capacidad), SLO p95/p99.
 *   2. extract_stress   — rampa 0 → STRESS_RPS (≈ capacidad mixta), mide la
 *      rodilla (knee): latencia que crece, /ready degrada, sin colas 5xx.
 *      El colapso profundo (> capacidad) lo ejerce Vegeta (docs/report.md):
 *      k6 dimensiona VUs como rate × latencia, inviable en este contenedor.
 *   3. probes           — sonda paralela de /health + /ready + /metrics (0–90 s).
 *
 * Selección de fase para calibración: -e K6_SCENARIO=extract_stress
 */

import http from 'k6/http';
import { check, sleep } from 'k6';
import { Counter, Rate } from 'k6/metrics';

const BOUNDARY = '----pdfextractorLoadTestB0UNDARY';
const ENC = new TextEncoder();

function envInt(name, fallback) {
  const raw = __ENV[name];
  if (raw === undefined || raw === '') return fallback;
  const value = parseInt(raw, 10);
  return Number.isNaN(value) ? fallback : value;
}

const CFG = {
  baseUrl: String(__ENV.TARGET_BASE_URL || 'http://127.0.0.1:18011').replace(/\/+$/, ''),
  baselineRps: envInt('BASELINE_RPS', 40),
  stressRps: envInt('STRESS_RPS', 150),
  queueTimeoutMs: envInt('QUEUE_TIMEOUT_MS', 2000),
  extractionTimeoutMs: envInt('EXTRACTION_TIMEOUT_MS', 30000),
  requestTimeout: __ENV.REQUEST_TIMEOUT || '60s',
  selected: __ENV.K6_SCENARIO
    ? String(__ENV.K6_SCENARIO).split(',').map((s) => s.trim()).filter((s) => s.length > 0)
    : null,
};

const unexpectedStatus = new Counter('unexpected_status');
const healthOkDuringRun = new Rate('health_ok_during_run');
const readyOverloaded = new Counter('ready_overloaded');
const readyRouteMissing = new Counter('ready_route_missing');
const backpressureRejections = new Counter('backpressure_rejections');

const HARD_CAP_MS = CFG.extractionTimeoutMs;

const THRESHOLDS = {
  // Línea base: SLO de latencia y presupuesto de errores (RNF rendimiento).
  'http_req_duration{scenario:extract_baseline}': ['p(95)<500', 'p(99)<750'],
  'http_req_failed{scenario:extract_baseline}': ['rate<0.01'],
  'checks{scenario:extract_baseline}': ['rate>0.99'],

  // Saturación: contrato duro ({200,503,504}), corte de colas indefinidas
  // (timeout de extracción + margen de transporte) y presupuesto de error que
  // admite rechazos esperados sin esconder un colapso.
  'checks{scenario:extract_stress}': ['rate>0.95'],
  'http_req_failed{scenario:extract_stress}': ['rate<0.35'],
  'http_req_duration{scenario:extract_stress}': [
    `p(99)<${HARD_CAP_MS + 2000}`,
  ],

  // La sonda debe sobrevivir: liveness intacto y readiness nunca 404.
  'checks{scenario:probes}': ['rate>0.99'],
  'health_ok_during_run': ['rate==1'],
  'ready_route_missing': ['count==0'],
  'ready_overloaded': ['count>0'],
  'unexpected_status': ['count==0'],
};

// `backpressure_rejections` queda como métrica informativa: la calibración
// mostró que el 503 de la puerta no llega al cliente (ver docs/report.md), así
// que exigirlo en un threshold haría fallar una corrida correcta.
const METRIC_PHASE = {
  ready_overloaded: 'extract_stress',
  health_ok_during_run: 'probes',
  ready_route_missing: 'probes',
};

function prunedThresholds() {
  if (!CFG.selected) return THRESHOLDS;
  const out = {};
  for (const key of Object.keys(THRESHOLDS)) {
    const tagged = key.match(/scenario:([a-z_]+)/);
    if (tagged && CFG.selected.indexOf(tagged[1]) === -1) continue;
    const phase = METRIC_PHASE[key];
    if (phase && CFG.selected.indexOf(phase) === -1) continue;
    out[key] = THRESHOLDS[key];
  }
  return out;
}

function wanted(name) {
  return !CFG.selected || CFG.selected.indexOf(name) !== -1;
}

function buildScenarios() {
  const all = {};
  if (wanted('probes')) {
    all.probes = {
      exec: 'probes',
      executor: 'constant-vus',
      vus: 2,
      duration: '90s',
      startTime: '0s',
    };
  }
  if (wanted('extract_baseline')) {
    all.extract_baseline = {
      exec: 'extractBaseline',
      executor: 'ramping-arrival-rate',
      startRate: 0,
      timeUnit: '1s',
      stages: [
        { target: CFG.baselineRps, duration: '10s' },
        { target: CFG.baselineRps, duration: '20s' },
        { target: 0, duration: '5s' },
      ],
      maxVUs: Math.max(30, CFG.baselineRps * 3),
      preAllocatedVUs: Math.max(10, CFG.baselineRps),
      startTime: '0s',
    };
  }
  if (wanted('extract_stress')) {
    // El pool debe cubrir rate × latencia máxima: con latencias de hasta ~12 s
    // a 150 rps se necesitan ~1900 VUs. Preasignar 1500 y dejar crecer hasta
    // 2400 alcanzó `dropped_iterations=0` en la corrida de referencia. Costo
    // ≈850 KB/VU (fixtures + buffers → ~2 GB en pico), viable con 3.8 GB.
    const maxVUs = Math.max(400, Math.ceil(CFG.stressRps * 16));
    const preAllocated = Math.min(maxVUs, Math.ceil(CFG.stressRps * 10));
    all.extract_stress = {
      exec: 'extractStress',
      executor: 'ramping-arrival-rate',
      startRate: 0,
      timeUnit: '1s',
      stages: [
        { target: CFG.stressRps, duration: '10s' },
        { target: CFG.stressRps, duration: '20s' },
        { target: 0, duration: '5s' },
      ],
      maxVUs,
      preAllocatedVUs: preAllocated,
      startTime: '40s',
    };
  }
  return all;
}

export const options = {
  scenarios: buildScenarios(),
  thresholds: prunedThresholds(),
  userAgent: 'pdfextractor-load-test/docs-report',
};

// --------------------------------------------------------------- fixtures --

const FIXTURE_FILES = ['valid_1p.pdf', 'valid_5p.pdf', 'valid_20p.pdf'];
const FIXTURES = FIXTURE_FILES.map((name) => ({
  name,
  bytes: open(`../tests/extractor/load/fixtures/${name}`, 'b'),
}));

let fixtureCursor = 0;
function nextFixture() {
  const item = FIXTURES[fixtureCursor % FIXTURES.length];
  fixtureCursor += 1;
  return item;
}

function multipartBody(filename, payload) {
  // open(path, 'b') devuelve un ArrayBuffer: se envuelve en Uint8Array (vista,
  // no copia) antes de medir o concatenar.
  const pay = payload instanceof Uint8Array ? payload : new Uint8Array(payload);
  const head = ENC.encode(
    `--${BOUNDARY}\r\n` +
      `Content-Disposition: form-data; name="file"; filename="${filename}"\r\n` +
      'Content-Type: application/pdf\r\n\r\n',
  );
  const tail = ENC.encode(`\r\n--${BOUNDARY}--\r\n`);
  const out = new Uint8Array(head.length + pay.length + tail.length);
  out.set(head, 0);
  out.set(pay, head.length);
  out.set(tail, head.length + pay.length);
  return out;
}

function requestId() {
  const vu = typeof __VU === 'number' ? __VU : 0;
  const iteration = typeof __ITER === 'number' ? __ITER : 0;
  return `loadtest-${vu}-${iteration}-${Date.now()}`;
}

function postFixture(item, tags) {
  return http.post(`${CFG.baseUrl}/api/v1/extractions`, multipartBody(item.name, item.bytes), {
    headers: {
      'Content-Type': `multipart/form-data; boundary=${BOUNDARY}`,
      'X-Request-Id': requestId(),
    },
    tags: Object.assign({ case: item.name }, tags || {}),
    timeout: CFG.requestTimeout,
  });
}

// ------------------------------------------------------------ assertidades --

function jsonOrNull(res) {
  try {
    return res.json();
  } catch (e) {
    return null;
  }
}

function isExtractSuccess(res) {
  if (res.status !== 200) return false;
  const body = jsonOrNull(res);
  if (!body || typeof body !== 'object') return false;
  const keys = Object.keys(body).sort().join(',');
  return (
    keys === 'extracted_text,extraction_method,page_count' &&
    body.extraction_method === 'pymupdf' &&
    typeof body.page_count === 'number' &&
    body.page_count > 0 &&
    typeof body.extracted_text === 'string' &&
    body.extracted_text.length >= 10
  );
}

function isErrorContract(res, needle) {
  const body = jsonOrNull(res);
  if (!body || typeof body.error !== 'string' || body.error.length === 0) return false;
  return needle ? body.error.indexOf(needle) !== -1 : true;
}

function noteUnexpected(res, ctx) {
  unexpectedStatus.add(1, { ctx: ctx, status: String(res.status) });
}

// ------------------------------------------------------------- escenarios ---

export function setup() {
  const health = http.get(`${CFG.baseUrl}/health`, { timeout: '10s' });
  if (health.status !== 200) {
    throw new Error(
      `pdfextractor no responde en ${CFG.baseUrl}/health (status ${health.status}). ` +
        'Levantar el servicio antes de correr la prueba.',
    );
  }
  const probe = postFixture(FIXTURES[0], { name: 'setup_probe' });
  return { probeStatus: probe.status };
}

export function extractBaseline() {
  const item = nextFixture();
  const res = postFixture(item, { should_succeed: 'true' });
  if (res.status !== 200) noteUnexpected(res, 'baseline');
  check(res, {
    'baseline devuelve 200': () => res.status === 200,
    'baseline respeta el contrato de 3 claves': () => isExtractSuccess(res),
  });
}

export function extractStress() {
  const item = nextFixture();
  const res = postFixture(item);
  const legal = res.status === 200 || res.status === 503 || res.status === 504;
  if (!legal) noteUnexpected(res, 'stress');
  if (res.status === 503) backpressureRejections.add(1);

  const bodyOk =
    res.status === 200
      ? isExtractSuccess(res)
      : res.status === 503
        ? isErrorContract(res, 'overload')
        : isErrorContract(res, 'timeout');

  check(res, {
    'saturación responde 200, 503 o 504': () => legal,
    'saturación respeta el cuerpo de su status': () => bodyOk,
  });
}

export function probes() {
  const health = http.get(`${CFG.baseUrl}/health`, { tags: { name: 'health', should_succeed: 'true' } });
  healthOkDuringRun.add(health.status === 200);
  if (health.status !== 200) noteUnexpected(health, 'health');
  check(health, {
    'liveness sigue en 200 bajo carga': () => health.status === 200,
  });

  const ready = http.get(`${CFG.baseUrl}/ready`, { tags: { name: 'ready' } });
  if (ready.status === 404) readyRouteMissing.add(1);
  else if (ready.status === 503) readyOverloaded.add(1);
  else if (ready.status !== 200) noteUnexpected(ready, 'ready');
  check(ready, {
    'readiness contesta 200 o 503, nunca 404/5xx': () => ready.status === 200 || ready.status === 503,
  });

  const metrics = http.get(`${CFG.baseUrl}/metrics`, { tags: { name: 'metrics' } });
  if (metrics.status !== 200) noteUnexpected(metrics, 'metrics');
  check(metrics, {
    'metrics sigue en 200': () => metrics.status === 200,
  });

  // Sin pausa la sonda degenera en un tight loop y se vuelve la principal
  // fuente de carga, invalidando la curva que mide.
  sleep(0.5);
}

export default function () {
  extractBaseline();
}
