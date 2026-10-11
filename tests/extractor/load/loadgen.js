/**
 * Task 12 — load / soak harness for the `pdfextractor` microservice.
 *
 * Contract under test: `docs/tasks/plan-extractor.md` § 4 (routes and success
 * body) and § 8 (failure-mode matrix).  Nothing here is guessed: every expected
 * status comes from `fixtures/manifest.json`, produced by `gen_fixtures.py`.
 *
 * Profiles (`LOAD_PROFILE`):
 *   ci    bounded run for CI — a handful of requests per scenario.
 *   full  default: baseline, edge cases, then saturation with a liveness probe.
 *   soak  sustained moderate load, used to watch RSS stability.
 *
 * Phases are sequenced with `startTime` so saturation never contaminates the
 * baseline measurements.  Select a single phase with `LOAD_SCENARIO=<name>`.
 */

import http from 'k6/http';
import { check, sleep } from 'k6';
import { Counter, Rate, Trend } from 'k6/metrics';

const MANIFEST = JSON.parse(open('./fixtures/manifest.json'));

const BOUNDARY = '----pdfextractorLoadT12B0UNDARY';
const ENC = new TextEncoder();

function envInt(name, fallback) {
  const raw = __ENV[name];
  if (raw === undefined || raw === '') return fallback;
  const value = parseInt(raw, 10);
  return Number.isNaN(value) ? fallback : value;
}

function envFloat(name, fallback) {
  const raw = __ENV[name];
  if (raw === undefined || raw === '') return fallback;
  const value = parseFloat(raw);
  return Number.isNaN(value) ? fallback : value;
}

const CFG = {
  baseUrl: String(__ENV.TARGET_BASE_URL || 'http://127.0.0.1:8001').replace(/\/+$/, ''),
  maxUploadBytes: envInt('LOAD_MAX_UPLOAD_BYTES', Number(MANIFEST.max_upload_bytes)),
  happyRps: envInt('LOAD_HAPPY_RPS', 20),
  saturationRps: envInt('LOAD_SATURATION_RPS', 200),
  queueTimeoutMs: envInt('LOAD_QUEUE_TIMEOUT_MS', 2000),
  extractionTimeoutMs: envInt('LOAD_EXTRACTION_TIMEOUT_MS', 30000),
  p95SloMs: envFloat('LOAD_P95_SLO_MS', 500),
  p99SloMs: envFloat('LOAD_P99_SLO_MS', 750),
  requestTimeout: __ENV.LOAD_REQUEST_TIMEOUT || '60s',
  backpressureMarginMs: envInt('LOAD_BACKPRESSURE_MARGIN_MS', 1000),
  skipContractProbe: String(__ENV.LOAD_SKIP_CONTRACT_PROBE || '') === '1',
  profile: String(__ENV.LOAD_PROFILE || 'full').toLowerCase(),
  selected: __ENV.LOAD_SCENARIO
    ? String(__ENV.LOAD_SCENARIO)
        .split(',')
        .map((s) => s.trim())
        .filter((s) => s.length > 0)
    : null,
  soakDuration: __ENV.LOAD_SOAK_DURATION || '10m',
};

const HARD_LATENCY_CAP_MS = CFG.queueTimeoutMs + CFG.extractionTimeoutMs;

const happyFixtures = MANIFEST.happy;
const edgeFixtures = MANIFEST.edge.concat(MANIFEST.synthetic);

const unexpectedStatus = new Counter('unexpected_status');
const backpressureRejections = new Counter('backpressure_rejections');
const backpressureFailMs = new Trend('backpressure_fail_ms', true);
const readyOverloaded = new Counter('ready_overloaded');
const readyRouteMissing = new Counter('ready_route_missing');
const healthOkDuringSaturation = new Rate('health_ok_during_saturation');

/** Thresholds are pruned when `LOAD_SCENARIO` narrows the run to one phase. */
const METRIC_PHASE = {
  backpressure_rejections: 'saturation_extract',
  backpressure_fail_ms: 'saturation_extract',
  ready_overloaded: 'health_probe',
  ready_route_missing: 'health_probe',
  health_ok_during_saturation: 'health_probe',
};

const THRESHOLDS = {
  // Baseline: p95/p99 latency SLO and a sub-1% error budget.
  'http_req_duration{scenario:happy_path}': [
    `p(95)<${CFG.p95SloMs}`,
    `p(99)<${CFG.p99SloMs}`,
  ],
  'http_req_failed{scenario:happy_path}': ['rate<0.01'],
  'checks{scenario:happy_path}': ['rate>0.99'],

  // Edge cases: correctness only — latency is guarded by a loose hang cap.
  'checks{scenario:edge_cases}': ['rate>0.99'],
  'http_req_duration{scenario:edge_cases}': [`p(99)<${HARD_LATENCY_CAP_MS}`],

  // Saturation: legal statuses are exactly 200 and 503, and latency stays bounded.
  'checks{scenario:saturation_extract}': ['rate>0.95'],
  'http_req_duration{scenario:saturation_extract}': [`p(99)<${HARD_LATENCY_CAP_MS}`],
  'unexpected_status': ['count==0'],

  // Backpressure must actually engage, and it must fail fast: a rejection can
  // never legitimately take longer than the queue timeout plus transport slack.
  'backpressure_rejections': ['count>0'],
  'backpressure_fail_ms': [
    `p(95)<${CFG.queueTimeoutMs + CFG.backpressureMarginMs}`,
    `p(99)<${CFG.queueTimeoutMs + 2 * CFG.backpressureMarginMs}`,
  ],

  // Liveness and readiness must survive the storm.  Readiness is *expected* to
  // answer 503 while saturated, so the error budget only covers /health.
  'checks{scenario:health_probe}': ['rate>0.99'],
  'health_ok_during_saturation': ['rate==1'],
  'ready_route_missing': ['count==0'],
  'http_req_failed{scenario:health_probe,should_succeed:true}': ['rate<0.01'],
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

function saturationMaxVUs() {
  const seconds = CFG.queueTimeoutMs / 1000;
  return Math.max(50, Math.ceil(CFG.saturationRps * seconds * 2) + 50);
}

function buildScenarios() {
  const all = {};

  if (CFG.profile === 'soak') {
    if (wanted('happy_path')) {
      all.happy_path = {
        exec: 'happyPath',
        executor: 'constant-arrival-rate',
        rate: CFG.happyRps,
        timeUnit: '1s',
        duration: CFG.soakDuration,
        maxVUs: Math.max(20, CFG.happyRps * 2),
        preAllocatedVUs: Math.max(5, Math.ceil(CFG.happyRps / 2)),
        startTime: '0s',
      };
    }
    if (wanted('health_probe')) {
      all.health_probe = {
        exec: 'healthProbe',
        executor: 'constant-vus',
        vus: 1,
        duration: CFG.soakDuration,
        startTime: '0s',
      };
    }
    return all;
  }

  const isCi = CFG.profile === 'ci';

  if (wanted('happy_path')) {
    const stages = isCi
      ? [
          { target: Math.min(CFG.happyRps, 5), duration: '2s' },
          { target: Math.min(CFG.happyRps, 5), duration: '3s' },
          { target: 0, duration: '1s' },
        ]
      : [
          { target: CFG.happyRps, duration: '10s' },
          { target: CFG.happyRps, duration: '20s' },
          { target: 0, duration: '5s' },
        ];
    all.happy_path = {
      exec: 'happyPath',
      executor: 'ramping-arrival-rate',
      startRate: 0,
      timeUnit: '1s',
      stages,
      maxVUs: isCi ? 8 : Math.max(50, CFG.happyRps * 3),
      preAllocatedVUs: isCi ? 4 : 10,
      startTime: '0s',
    };
  }

  if (wanted('edge_cases')) {
    all.edge_cases = {
      exec: 'edgeCases',
      executor: 'shared-iterations',
      vus: isCi ? 2 : 4,
      iterations: isCi ? 12 : edgeFixtures.length * 4,
      maxDuration: '60s',
      startTime: isCi ? '7s' : '40s',
    };
  }

  const saturationStart = isCi ? '14s' : '55s';
  if (wanted('saturation_extract')) {
    const stages = isCi
      ? [
          { target: CFG.saturationRps, duration: '4s' },
          { target: CFG.saturationRps, duration: '5s' },
          { target: 0, duration: '2s' },
        ]
      : [
          { target: CFG.saturationRps, duration: '15s' },
          { target: CFG.saturationRps, duration: '30s' },
          { target: 0, duration: '10s' },
        ];
    all.saturation_extract = {
      exec: 'saturationExtract',
      executor: 'ramping-arrival-rate',
      startRate: 0,
      timeUnit: '1s',
      stages,
      // A VU stays occupied for up to the whole queue timeout while it waits,
      // so the pool must be sized rate x queue_timeout (x2 headroom) or k6
      // silently drops iterations and the saturation curve is understated.
      maxVUs: saturationMaxVUs(),
      preAllocatedVUs: Math.min(saturationMaxVUs(), isCi ? 100 : 200),
      startTime: saturationStart,
    };
  }

  if (wanted('health_probe')) {
    all.health_probe = {
      exec: 'healthProbe',
      executor: 'constant-vus',
      vus: 2,
      duration: isCi ? '11s' : '55s',
      startTime: saturationStart,
    };
  }

  return all;
}

export const options = {
  scenarios: buildScenarios(),
  thresholds: prunedThresholds(),
  noConnectionReuse: false,
  userAgent: 'pdfextractor-loadgen/Task12',
};

// ---------------------------------------------------------------- fixtures --

const FIXTURES = {};
for (const item of happyFixtures.concat(MANIFEST.edge)) {
  FIXTURES[item.file] = {
    meta: item,
    bytes: item.bytes === 0 ? new Uint8Array(0) : open(`./fixtures/${item.file}`, 'b'),
  };
}

let happyCursor = 0;
let edgeCursor = 0;
let oversizedBodyCache = null;

function nextHappy() {
  const item = happyFixtures[happyCursor % happyFixtures.length];
  happyCursor += 1;
  return item;
}

function nextEdge() {
  const item = edgeFixtures[edgeCursor % edgeFixtures.length];
  edgeCursor += 1;
  return item;
}

function multipartBody(filename, contentType, payload, fieldName) {
  const pay = payload instanceof Uint8Array ? payload : new Uint8Array(payload);
  const field = fieldName || 'file';
  const head = ENC.encode(
    `--${BOUNDARY}\r\n` +
      `Content-Disposition: form-data; name="${field}"; filename="${filename}"\r\n` +
      `Content-Type: ${contentType}\r\n\r\n`,
  );
  const tail = ENC.encode(`\r\n--${BOUNDARY}--\r\n`);
  const out = new Uint8Array(head.length + pay.length + tail.length);
  out.set(head, 0);
  out.set(pay, head.length);
  out.set(tail, head.length + pay.length);
  return out;
}

function oversizedBody() {
  if (oversizedBodyCache !== null) return oversizedBodyCache;
  // Valid multipart framing with a file part that exceeds the upload guard, so
  // the rejection is driven by size alone and never by a parse error.
  const payload = new Uint8Array(CFG.maxUploadBytes + 262_144);
  for (let i = 0; i < payload.length; i += 1) payload[i] = 65 + (i % 26);
  oversizedBodyCache = multipartBody('oversized.pdf', 'application/pdf', payload);
  return oversizedBodyCache;
}

function missingFieldBody() {
  return multipartBody(
    'orphan.pdf',
    'application/pdf',
    FIXTURES['valid_1p.pdf'].bytes,
    'document',
  );
}

function requestId() {
  // `__ITER` only exists inside a VU iteration, never during setup().
  const vu = typeof __VU === 'number' ? __VU : 0;
  const iteration = typeof __ITER === 'number' ? __ITER : 0;
  return `loadgen-${vu}-${iteration}-${Date.now()}`;
}

function post(body, tags) {
  return http.post(`${CFG.baseUrl}/api/v1/extractions`, body, {
    headers: {
      'Content-Type': `multipart/form-data; boundary=${BOUNDARY}`,
      'X-Request-Id': requestId(),
    },
    tags,
    timeout: CFG.requestTimeout,
  });
}

function postFixture(item, extraTags) {
  const tags = Object.assign({ case: item.file }, extraTags || {});
  return post(multipartBody(item.file, 'application/pdf', FIXTURES[item.file].bytes), tags);
}

// ------------------------------------------------------------- assertions ---

function allowedStatuses(expected) {
  if (typeof expected.status === 'number') return [expected.status];
  const base = parseInt(String(expected.status_class).charAt(0), 10) * 100;
  const out = [];
  for (let s = base; s < base + 100; s += 1) out.push(s);
  return out;
}

function expectStatus(res, allowed, ctx) {
  const ok = allowed.indexOf(res.status) !== -1;
  if (!ok) unexpectedStatus.add(1, { ctx: ctx, status: String(res.status) });
  return ok;
}

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

function hasErrorContract(res, expected) {
  const body = jsonOrNull(res);
  if (!body || typeof body.error !== 'string' || body.error.length === 0) return false;
  if (expected.error_contains && body.error.indexOf(expected.error_contains) === -1) {
    return false;
  }
  return true;
}

function isOverloadBody(res) {
  const body = jsonOrNull(res);
  return !!body && typeof body.error === 'string' && body.error.indexOf('overload') !== -1;
}

// --------------------------------------------------------------- scenarios --

export function setup() {
  const health = http.get(`${CFG.baseUrl}/health`, {
    tags: { name: 'setup_health' },
    timeout: '10s',
  });
  if (health.status !== 200) {
    throw new Error(
      `pdfextractor is not reachable at ${CFG.baseUrl}/health (status ${health.status}). ` +
        'Start it first, e.g. `./tests/extractor/load/run.sh --serve-only`.',
    );
  }
  if (CFG.skipContractProbe) return { probed: false };

  const probe = postFixture(happyFixtures[0], { name: 'setup_contract' });
  if (probe.status === 404) {
    throw new Error(
      'POST /api/v1/extractions returned 404 — the route does not exist yet. ' +
        'Task 12 depends on Task 6 (extract endpoint) and Task 9 (backpressure/503). ' +
        'Implement those first, or rerun with LOAD_SKIP_CONTRACT_PROBE=1 to inspect the harness.',
    );
  }
  return { probed: true, status: probe.status };
}

export function happyPath() {
  const item = nextHappy();
  const res = postFixture(item, { should_succeed: 'true' });
  const statusOk = expectStatus(res, [200], 'happy_path');
  const bodyOk = isExtractSuccess(res);
  check(res, {
    'happy path returns 200': () => statusOk,
    'happy path body is the exact § 4 contract': () => bodyOk,
  });
}

export function edgeCases() {
  const item = nextEdge();
  let res;
  if (item.file === '__oversized__') {
    res = post(oversizedBody(), { case: 'oversized' });
  } else if (item.file === '__missing_field__') {
    res = post(missingFieldBody(), { case: 'missing_field' });
  } else {
    res = postFixture(item);
  }

  const allowed = allowedStatuses(item.expected);
  const statusOk = expectStatus(res, allowed, `edge:${item.file}`);
  const contractOk = hasErrorContract(res, item.expected);
  check(res, {
    'edge case status matches the failure matrix': () => statusOk,
    'edge case body is {"error": str}': () => contractOk,
  });
  sleep(0.05);
}

export function saturationExtract() {
  const item = nextHappy();
  const res = postFixture(item, { case: 'saturation' });

  const legal = res.status === 200 || res.status === 503;
  if (!legal) unexpectedStatus.add(1, { ctx: 'saturation', status: String(res.status) });
  if (res.status === 503) {
    backpressureRejections.add(1);
    backpressureFailMs.add(res.timings.duration);
  }

  const bodyOk =
    res.status === 200
      ? isExtractSuccess(res)
      : res.status === 503
        ? isOverloadBody(res)
        : false;

  check(res, {
    'saturation answers only 200 or 503': () => legal,
    'saturation body matches its status contract': () => bodyOk,
  });
}

export function healthProbe() {
  const health = http.get(`${CFG.baseUrl}/health`, {
    tags: { name: 'health', should_succeed: 'true' },
  });
  const healthOk = health.status === 200;
  healthOkDuringSaturation.add(healthOk);
  if (!healthOk) unexpectedStatus.add(1, { ctx: 'health', status: String(health.status) });
  check(health, {
    'liveness stays 200 while the extractor is saturated': () => healthOk,
  });

  const ready = http.get(`${CFG.baseUrl}/ready`, { tags: { name: 'ready' } });
  if (ready.status === 404) {
    readyRouteMissing.add(1);
  } else if (ready.status === 503) {
    readyOverloaded.add(1);
  } else if (ready.status !== 200) {
    unexpectedStatus.add(1, { ctx: 'ready', status: String(ready.status) });
  }
  check(ready, {
    'readiness answers 200 or 503, never 404/5xx': () =>
      ready.status === 200 || ready.status === 503,
  });

  // Without a delay the probe degenerates into a tight loop that becomes the
  // dominant source of load and invalidates the saturation curve it measures.
  sleep(0.5);
}

export default function () {
  happyPath();
}
