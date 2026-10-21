#!/usr/bin/env bash
#
# TASK-37 — concurrency door sweep for the isolated-instance overload regime.
#
# Drives Vegeta A (400 rps mixed, 30s) against the deployment port :9000 for
# MAX_INFLIGHT_REQUESTS in {N, 2N, 4N} with MAX_CONCURRENT_EXTRACTIONS fixed,
# restarting the service between runs so a saturated event loop never leaks into
# the next measurement. While the attack runs, a poller hits /health, /ready and
# /metrics every 200 ms and records the status code and the latency of each
# probe: the instance must keep answering them while it sheds load.
#
# Requires: vegeta on PATH, uv, curl. Deterministic config matches docs/report.md.
#
#   ./load-tests/sweep-door.sh                              # full sweep (K=7,14,28)
#   SWEEP_KS="7" SWEEP_REPS=2 ./load-tests/sweep-door.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
RUN_DIR="${REPO_ROOT}/load-tests/run-9000"
OUT_DIR="${RUN_DIR}/sweep-door-K7-K14-K28"

SWEEP_N="${SWEEP_N:-7}"
SWEEP_KS="${SWEEP_KS:-7 14 28}"
SWEEP_REPS="${SWEEP_REPS:-1}"
BOUNDARY="----pdfextractorVegetaB0UNDARY"
PORT="${PDFEXTRACTOR_PORT:-9000}"
RATE="${SWEEP_RATE:-400}"
DURATION="${SWEEP_DURATION:-30s}"
TIMEOUT="${SWEEP_TIMEOUT:-35s}"
MAX_BODY="${SWEEP_MAX_BODY:-1024}"

SERVER_PGID=""
PROBE_PID=""

log() { printf '[door] %s\n' "$*"; }

stop_probes() {
  if [[ -n "${PROBE_PID}" ]] && kill -0 "${PROBE_PID}" 2>/dev/null; then
    kill -TERM "${PROBE_PID}" 2>/dev/null || true
    wait "${PROBE_PID}" 2>/dev/null || true
  fi
  PROBE_PID=""
}

stop_server() {
  stop_probes
  if [[ -n "${SERVER_PGID}" ]] && kill -0 "${SERVER_PGID}" 2>/dev/null; then
    kill -TERM -- "-${SERVER_PGID}" 2>/dev/null || true
    for _ in $(seq 1 40); do
      kill -0 "${SERVER_PGID}" 2>/dev/null || break
      sleep 0.25
    done
    kill -KILL -- "-${SERVER_PGID}" 2>/dev/null || true
  fi
  SERVER_PGID=""
  for _ in $(seq 1 40); do
    curl -fsS -m 1 "http://127.0.0.1:${PORT}/health" >/dev/null 2>&1 || break
    sleep 0.25
  done
}
trap stop_server EXIT INT TERM

start_server() {
  local n="$1" k="$2" logfile="$3"
  log "starting service on :${PORT} (MAX_CONCURRENT_EXTRACTIONS=${n} MAX_INFLIGHT_REQUESTS=${k})"
  setsid bash -c "
    cd '${REPO_ROOT}'
    PDFEXTRACTOR_PORT=${PORT} \
    PDFEXTRACTOR_MAX_CONCURRENT_EXTRACTIONS=${n} \
    PDFEXTRACTOR_MAX_INFLIGHT_REQUESTS=${k} \
    PDFEXTRACTOR_MAX_UPLOAD_BYTES=1048576 \
    PDFEXTRACTOR_QUEUE_TIMEOUT_SECONDS=2 \
    PDFEXTRACTOR_METRICS_ENABLED=true \
    PDFEXTRACTOR_WARMUP=true \
      exec uv run uvicorn --factory pdfextractor.main:create_app \
        --host 127.0.0.1 --port ${PORT} \
        --log-level warning --no-access-log --http httptools
  " >"${logfile}" 2>&1 &
  SERVER_PGID=$!
}

wait_health() {
  for _ in $(seq 1 120); do
    curl -fsS -m 2 "http://127.0.0.1:${PORT}/health" >/dev/null 2>&1 && return 0
    sleep 0.5
  done
  return 1
}

start_probes() {
  local out="$1"
  : >"${out}"
  (
    while true; do
      for path in /health /ready /metrics; do
        code=$(curl -s -o /dev/null -m 2 -w '%{http_code} %{time_total}' \
          "http://127.0.0.1:${PORT}${path}" 2>/dev/null || echo "000 0")
        printf '%s %s\n' "${path}" "${code}" >>"${out}"
      done
      sleep 0.2
    done
  ) &
  PROBE_PID=$!
}

run_one() {
  local n="$1" k="$2" rep="$3"
  local dir="${OUT_DIR}/K${k}_r${rep}"
  mkdir -p "${dir}"
  start_server "${n}" "${k}" "${dir}/server.log"
  if ! wait_health; then
    log "K=${k} rep=${rep}: service did not become healthy"
    stop_server
    return 1
  fi
  log "K=${k} rep=${rep}: attacking ${RATE} rps for ${DURATION}"
  start_probes "${dir}/probes.txt"
  vegeta attack \
    -targets="${RUN_DIR}/targets-mixed.txt" \
    -body="${RUN_DIR}/body5p.bin" \
    -header="Content-Type: multipart/form-data; boundary=${BOUNDARY}" \
    -rate="${RATE}" -duration="${DURATION}" -timeout="${TIMEOUT}" \
    -max-body="${MAX_BODY}" \
    -output="${dir}/results-mixed.bin"
  stop_probes
  vegeta report <"${dir}/results-mixed.bin" >"${dir}/vegeta-report-mixed.txt"
  vegeta report --type=json <"${dir}/results-mixed.bin" >"${dir}/results-mixed.json"
  log "K=${k} rep=${rep}: done"
  grep -E "Requests|Success|Status Codes" "${dir}/vegeta-report-mixed.txt" || true
  echo "--- probe status codes (path count) ---"
  awk '{print $1, $2}' "${dir}/probes.txt" | sort | uniq -c | sort -rn || true
  stop_server
}

mkdir -p "${OUT_DIR}"

for k in ${SWEEP_KS}; do
  for rep in $(seq 1 "${SWEEP_REPS}"); do
    echo "==================== K=${k} rep=${rep} ===================="
    run_one "${SWEEP_N}" "${k}" "${rep}"
  done
done

log "sweep complete; artifacts in ${OUT_DIR}"
