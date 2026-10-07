#!/usr/bin/env bash
#
# Task 12 — load / soak runner for the pdfextractor microservice.
#
# Generates the PDF fixtures, boots the service with a deterministic concurrency
# configuration, samples its RSS while k6 drives the load, and exits with k6's
# status so CI can gate on the thresholds defined in loadgen.js.
#
#   ./tests/extractor/load/run.sh                       # full profile (local)
#   ./tests/extractor/load/run.sh --profile ci          # bounded run for CI
#   ./tests/extractor/load/run.sh --profile soak        # sustained load, RSS watch
#   ./tests/extractor/load/run.sh --serve-only          # boot + fixtures, then wait
#
# Every knob is an environment variable so the runner can be reproduced without
# this script; run `k6 run tests/extractor/load/loadgen.js` directly if preferred.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"
FIXTURES_DIR="${SCRIPT_DIR}/fixtures"
RESULTS_DIR="${SCRIPT_DIR}/results"

PROFILE="${LOAD_PROFILE:-full}"
SCENARIO=""
SERVE_ONLY=0
USE_EXTERNAL=0

LOAD_MAX_UPLOAD_BYTES="${LOAD_MAX_UPLOAD_BYTES:-1048576}"
LOAD_CONCURRENCY="${LOAD_CONCURRENCY:-4}"
LOAD_QUEUE_TIMEOUT_SECONDS="${LOAD_QUEUE_TIMEOUT_SECONDS:-2}"
LOAD_QUEUE_TIMEOUT_MS="$((LOAD_QUEUE_TIMEOUT_SECONDS * 1000))"
LOAD_SATURATION_RPS="${LOAD_SATURATION_RPS:-200}"
LOAD_HAPPY_RPS="${LOAD_HAPPY_RPS:-20}"
LOAD_P95_SLO_MS="${LOAD_P95_SLO_MS:-500}"
LOAD_P99_SLO_MS="${LOAD_P99_SLO_MS:-750}"
LOAD_BACKPRESSURE_MARGIN_MS="${LOAD_BACKPRESSURE_MARGIN_MS:-1000}"
PDFEXTRACTOR_PORT="${PDFEXTRACTOR_PORT:-18011}"
if [[ -n "${TARGET_BASE_URL:-}" ]]; then
  TARGET_URL_WAS_SET=1
else
  TARGET_BASE_URL="http://127.0.0.1:${PDFEXTRACTOR_PORT}"
  TARGET_URL_WAS_SET=0
fi
RSS_INTERVAL_S="${RSS_INTERVAL_S:-1}"

SERVER_PID=""
SAMPLER_PID=""
RSS_CSV=""

usage() {
  sed -n '2,16p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --profile) PROFILE="$2"; shift 2 ;;
    --scenario) SCENARIO="$2"; shift 2 ;;
    --serve-only) SERVE_ONLY=1; shift ;;
    --external) USE_EXTERNAL=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown argument: $1" >&2; usage; exit 2 ;;
  esac
done

if [[ "${USE_EXTERNAL}" -eq 1 && "${TARGET_URL_WAS_SET}" -eq 0 ]]; then
  echo "--external requires TARGET_BASE_URL=<url> so the runner knows where to point k6" >&2
  exit 2
fi

log() { printf '[loadgen] %s\n' "$*"; }

cleanup() {
  if [[ -n "${SAMPLER_PID}" ]] && kill -0 "${SAMPLER_PID}" 2>/dev/null; then
    kill "${SAMPLER_PID}" 2>/dev/null || true
    wait "${SAMPLER_PID}" 2>/dev/null || true
  fi
  if [[ -n "${SERVER_PID}" ]] && kill -0 "${SERVER_PID}" 2>/dev/null; then
    kill "${SERVER_PID}" 2>/dev/null || true
    for _ in $(seq 1 20); do
      kill -0 "${SERVER_PID}" 2>/dev/null || break
      sleep 0.25
    done
    kill -9 "${SERVER_PID}" 2>/dev/null || true
  fi
}
trap cleanup EXIT INT TERM

# Largest RSS among the processes serving the port: `uv run` adds a small parent
# process, and the uvicorn worker is the one whose memory we actually care about.
server_rss_kb() {
  local best=0 pid rss
  while read -r pid; do
    [[ -z "${pid}" ]] && continue
    rss="$(awk '/^VmRSS:/ {print $2}' "/proc/${pid}/status" 2>/dev/null || echo 0)"
    if [[ "${rss}" -gt "${best}" ]]; then best="${rss}"; fi
  done < <(pgrep -f "uvicorn.*pdfextractor\.main" 2>/dev/null || true)
  echo "${best}"
}

wait_for_health() {
  local url="$1" deadline=$((SECONDS + 60))
  while (( SECONDS < deadline )); do
    if curl -fsS -m 2 "${url}/health" >/dev/null 2>&1; then return 0; fi
    sleep 0.5
  done
  echo "service did not become healthy at ${url}/health within 60s" >&2
  return 1
}

# Writes a CSV sample file and leaves its path in ${RSS_CSV}.  A global is used
# on purpose: capturing the path with command substitution would make the shell
# wait for the background sampler's stdout to close.
start_sampler() {
  mkdir -p "${RESULTS_DIR}"
  RSS_CSV="${RESULTS_DIR}/rss_$(date +%Y%m%d_%H%M%S).csv"
  echo "epoch_s,rss_kb" > "${RSS_CSV}"
  (
    while :; do
      rss="$(server_rss_kb)"
      if [[ "${rss}" -gt 0 ]]; then echo "$(date +%s),${rss}" >> "${RSS_CSV}"; fi
      sleep "${RSS_INTERVAL_S}"
    done
  ) 1>&2 &
  SAMPLER_PID=$!
}

summarise_rss() {
  local csv="$1"
  [[ -f "${csv}" ]] || return 0
  awk -F, 'NR > 1 {
      v[n++] = $2
      if (n == 1 || $2 < min) min = $2
      if ($2 > max) max = $2
      last = $2
    }
    END {
      if (n == 0) { print "[loadgen] rss: no samples"; exit }
      printf "[loadgen] rss: samples=%d first=%.1fMB last=%.1fMB min=%.1fMB max=%.1fMB delta=%.1fMB\n", \
        n, v[0] / 1024, last / 1024, min / 1024, max / 1024, (last - v[0]) / 1024
    }' "${csv}"
}

log "generating fixtures (max_upload_bytes=${LOAD_MAX_UPLOAD_BYTES})"
(cd "${REPO_ROOT}" && uv run python tests/extractor/load/gen_fixtures.py \
  --max-upload-bytes "${LOAD_MAX_UPLOAD_BYTES}")

if [[ "${USE_EXTERNAL}" -eq 0 ]]; then
  log "starting pdfextractor on port ${PDFEXTRACTOR_PORT}"
  (
    cd "${REPO_ROOT}"
    PDFEXTRACTOR_PORT="${PDFEXTRACTOR_PORT}" \
    PDFEXTRACTOR_MAX_UPLOAD_BYTES="${LOAD_MAX_UPLOAD_BYTES}" \
    PDFEXTRACTOR_MAX_CONCURRENT_EXTRACTIONS="${LOAD_CONCURRENCY}" \
    PDFEXTRACTOR_QUEUE_TIMEOUT_SECONDS="${LOAD_QUEUE_TIMEOUT_SECONDS}" \
      uv run uvicorn --factory pdfextractor.main:create_app \
        --host 127.0.0.1 --port "${PDFEXTRACTOR_PORT}" \
        --log-level warning
  ) &
  SERVER_PID=$!
  wait_for_health "${TARGET_BASE_URL}"
  log "service healthy at ${TARGET_BASE_URL}"
fi

if [[ "${SERVE_ONLY}" -eq 1 ]]; then
  log "--serve-only: press Ctrl-C to stop the service"
  if [[ -n "${SERVER_PID}" ]]; then wait "${SERVER_PID}"; fi
  exit 0
fi

start_sampler

K6_ARGS=(
  --summary-trend-stats "avg,min,med,max,p(90),p(95),p(99)"
  --summary-export "${RESULTS_DIR}/summary_$(date +%Y%m%d_%H%M%S).json"
  -e "TARGET_BASE_URL=${TARGET_BASE_URL}"
  -e "LOAD_PROFILE=${PROFILE}"
  -e "LOAD_MAX_UPLOAD_BYTES=${LOAD_MAX_UPLOAD_BYTES}"
  -e "LOAD_QUEUE_TIMEOUT_MS=${LOAD_QUEUE_TIMEOUT_MS}"
  -e "LOAD_SATURATION_RPS=${LOAD_SATURATION_RPS}"
  -e "LOAD_HAPPY_RPS=${LOAD_HAPPY_RPS}"
  -e "LOAD_P95_SLO_MS=${LOAD_P95_SLO_MS}"
  -e "LOAD_P99_SLO_MS=${LOAD_P99_SLO_MS}"
  -e "LOAD_BACKPRESSURE_MARGIN_MS=${LOAD_BACKPRESSURE_MARGIN_MS}"
)
if [[ -n "${SCENARIO}" ]]; then K6_ARGS+=(-e "LOAD_SCENARIO=${SCENARIO}"); fi

log "running k6 (profile=${PROFILE}${SCENARIO:+, scenario=${SCENARIO}})"
set +e
(cd "${REPO_ROOT}" && k6 run "${K6_ARGS[@]}" tests/extractor/load/loadgen.js)
STATUS=$?
set -e

sleep 1
kill "${SAMPLER_PID}" 2>/dev/null || true
wait "${SAMPLER_PID}" 2>/dev/null || true
SAMPLER_PID=""
summarise_rss "${RSS_CSV}"

if [[ "${STATUS}" -eq 0 ]]; then
  log "PASS — all thresholds met (results in ${RESULTS_DIR})"
else
  log "FAIL — k6 exited with ${STATUS} (results in ${RESULTS_DIR})"
fi
exit "${STATUS}"
