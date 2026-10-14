#!/usr/bin/env bash
#
# TASK-35 — controlled concurrency sweep for the mixed overload regime.
#
# Drives Vegeta A (400 rps, 50% POST / 50% probes, 30s) against the deployment
# port :9000 for MAX_CONCURRENT_EXTRACTIONS in {6,7,8}, two repetitions each,
# restarting the service between runs so a saturated event loop never leaks into
# the next measurement. Artifacts land in load-tests/run-9000/sweep-N6-N7-N8/.
#
# Requires: vegeta on PATH, uv, curl. Deterministic config matches docs/report.md.
#
#   ./load-tests/sweep-mixed.sh            # full sweep (N=6,7,8 × 2 reps)
#   SWEEP_NS="7" SWEEP_REPS=1 ./load-tests/sweep-mixed.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
RUN_DIR="${REPO_ROOT}/load-tests/run-9000"
OUT_DIR="${RUN_DIR}/sweep-N6-N7-N8"

SWEEP_NS="${SWEEP_NS:-6 7 8}"
SWEEP_REPS="${SWEEP_REPS:-2}"
BOUNDARY="----pdfextractorVegetaB0UNDARY"
PORT="${PDFEXTRACTOR_PORT:-9000}"
RATE="${SWEEP_RATE:-400}"
DURATION="${SWEEP_DURATION:-30s}"
TIMEOUT="${SWEEP_TIMEOUT:-35s}"
MAX_BODY="${SWEEP_MAX_BODY:-1024}"

SERVER_PGID=""

log() { printf '[sweep] %s\n' "$*"; }

stop_server() {
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
  local n="$1" logfile="$2"
  log "starting service on :${PORT} (MAX_CONCURRENT_EXTRACTIONS=${n})"
  setsid bash -c "
    cd '${REPO_ROOT}'
    PDFEXTRACTOR_PORT=${PORT} \
    PDFEXTRACTOR_MAX_CONCURRENT_EXTRACTIONS=${n} \
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

run_one() {
  local n="$1" rep="$2"
  local dir="${OUT_DIR}/N${n}_r${rep}"
  mkdir -p "${dir}"
  start_server "${n}" "${dir}/server.log"
  if ! wait_health; then
    log "N=${n} rep=${rep}: service did not become healthy"
    stop_server
    return 1
  fi
  log "N=${n} rep=${rep}: attacking ${RATE} rps for ${DURATION}"
  vegeta attack \
    -targets="${RUN_DIR}/targets-mixed.txt" \
    -body="${RUN_DIR}/body5p.bin" \
    -header="Content-Type: multipart/form-data; boundary=${BOUNDARY}" \
    -rate="${RATE}" -duration="${DURATION}" -timeout="${TIMEOUT}" \
    -max-body="${MAX_BODY}" \
    -output="${dir}/results-mixed.bin"
  vegeta report <"${dir}/results-mixed.bin" >"${dir}/vegeta-report-mixed.txt"
  vegeta report --type=json <"${dir}/results-mixed.bin" >"${dir}/results-mixed.json"
  log "N=${n} rep=${rep}: done"
  grep -E "Requests|Success|Status Codes" "${dir}/vegeta-report-mixed.txt" || true
  stop_server
}

mkdir -p "${OUT_DIR}"

for n in ${SWEEP_NS}; do
  for rep in $(seq 1 "${SWEEP_REPS}"); do
    echo "==================== N=${n} rep=${rep} ===================="
    run_one "${n}" "${rep}"
  done
done

log "sweep complete; artifacts in ${OUT_DIR}"
