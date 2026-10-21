#!/usr/bin/env bash
#
# TASK-37 — multi-replica validation with the concurrency door in place.
#
# Starts K isolated instances (the same configuration the single-instance
# sweep uses: N=7 workers, K=N in-flight requests, 2 s queue timeout) on
# consecutive ports and drives the mixed Vegeta A workload (400 rps) against
# them round-robin, the way Traefik would. Each instance answers its own
# probe poller, so the evidence shows the probes survive the saturation of
# the whole fleet, not just of one instance.
#
# Requires: vegeta on PATH, uv, curl.
#
#   ./load-tests/run-replicas.sh                # k=2 and k=3, 1 rep each
#   REPLICAS="2" ./load-tests/run-replicas.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
RUN_DIR="${REPO_ROOT}/load-tests/run-9000"
OUT_DIR="${RUN_DIR}/replicas-door"

REPLICAS_LIST="${REPLICAS:-2 3}"
N_WORKERS="${N_WORKERS:-7}"
BOUNDARY="----pdfextractorVegetaB0UNDARY"
BASE_PORT="${BASE_PORT:-9010}"
RATE="${SWEEP_RATE:-400}"
DURATION="${SWEEP_DURATION:-30s}"
TIMEOUT="${SWEEP_TIMEOUT:-35s}"
MAX_BODY="${SWEEP_MAX_BODY:-1024}"

SERVER_PGIDS=()
PROBE_PIDS=()

log() { printf '[replicas] %s\n' "$*"; }

stop_probes() {
  for pid in ${PROBE_PIDS[@]+"${PROBE_PIDS[@]}"}; do
    kill -TERM "${pid}" 2>/dev/null || true
  done
  for pid in ${PROBE_PIDS[@]+"${PROBE_PIDS[@]}"}; do
    wait "${pid}" 2>/dev/null || true
  done
  PROBE_PIDS=()
}

stop_servers() {
  stop_probes
  for pgid in ${SERVER_PGIDS[@]+"${SERVER_PGIDS[@]}"}; do
    if kill -0 "${pgid}" 2>/dev/null; then
      kill -TERM -- "-${pgid}" 2>/dev/null || true
      for _ in $(seq 1 40); do
        kill -0 "${pgid}" 2>/dev/null || break
        sleep 0.25
      done
      kill -KILL -- "-${pgid}" 2>/dev/null || true
    fi
  done
  SERVER_PGIDS=()
  for port in $(seq "${BASE_PORT}" $((BASE_PORT + 8))); do
    curl -fsS -m 1 "http://127.0.0.1:${port}/health" >/dev/null 2>&1 || break
    sleep 0.25
  done
}
trap stop_servers EXIT INT TERM

start_server() {
  local port="$1" logfile="$2"
  setsid bash -c "
    cd '${REPO_ROOT}'
    PDFEXTRACTOR_PORT=${port} \
    PDFEXTRACTOR_MAX_CONCURRENT_EXTRACTIONS=${N_WORKERS} \
    PDFEXTRACTOR_MAX_INFLIGHT_REQUESTS=${N_WORKERS} \
    PDFEXTRACTOR_MAX_UPLOAD_BYTES=1048576 \
    PDFEXTRACTOR_QUEUE_TIMEOUT_SECONDS=2 \
    PDFEXTRACTOR_METRICS_ENABLED=true \
    PDFEXTRACTOR_WARMUP=true \
      exec uv run uvicorn --factory pdfextractor.main:create_app \
        --host 127.0.0.1 --port ${port} \
        --log-level warning --no-access-log --http httptools
  " >"${logfile}" 2>&1 &
  SERVER_PGIDS+=("$!")
}

wait_health() {
  local port="$1"
  for _ in $(seq 1 120); do
    curl -fsS -m 2 "http://127.0.0.1:${port}/health" >/dev/null 2>&1 && return 0
    sleep 0.5
  done
  return 1
}

start_probes() {
  local port="$1" out="$2"
  : >"${out}"
  (
    while true; do
      for path in /health /ready; do
        code=$(curl -s -o /dev/null -m 5 -w '%{http_code} %{time_total}' \
          "http://127.0.0.1:${port}${path}" 2>/dev/null || echo "000 0")
        printf '%s %s\n' "${path}" "${code}" >>"${out}"
      done
      sleep 0.2
    done
  ) &
  PROBE_PIDS+=("$!")
}

build_targets() {
  local k="$1" out="$2"
  : >"${out}"
  for _ in $(seq 1 6); do
    for port in $(seq "${BASE_PORT}" $((BASE_PORT + k - 1))); do
      printf 'GET http://127.0.0.1:%s/health\n' "${port}" >>"${out}"
      printf 'GET http://127.0.0.1:%s/ready\n' "${port}" >>"${out}"
      printf 'GET http://127.0.0.1:%s/metrics\n' "${port}" >>"${out}"
      printf 'POST http://127.0.0.1:%s/api/v1/extractions\n' "${port}" >>"${out}"
      printf 'POST http://127.0.0.1:%s/api/v1/extractions\n' "${port}" >>"${out}"
      printf 'POST http://127.0.0.1:%s/api/v1/extractions\n' "${port}" >>"${out}"
    done
  done
}

run_one() {
  local k="$1"
  local dir="${OUT_DIR}/k${k}"
  mkdir -p "${dir}"
  for idx in $(seq 0 $((k - 1))); do
    local port=$((BASE_PORT + idx))
    start_server "${port}" "${dir}/server-${port}.log"
  done
  for idx in $(seq 0 $((k - 1))); do
    local port=$((BASE_PORT + idx))
    if ! wait_health "${port}"; then
      log "k=${k}: instance on :${port} did not become healthy"
      stop_servers
      return 1
    fi
    start_probes "${port}" "${dir}/probes-${port}.txt"
  done
  local targets="${dir}/targets-mixed.txt"
  build_targets "${k}" "${targets}"
  log "k=${k}: attacking ${RATE} rps for ${DURATION} against ${k} instances"
  vegeta attack \
    -targets="${targets}" \
    -body="${RUN_DIR}/body5p.bin" \
    -header="Content-Type: multipart/form-data; boundary=${BOUNDARY}" \
    -rate="${RATE}" -duration="${DURATION}" -timeout="${TIMEOUT}" \
    -max-body="${MAX_BODY}" \
    -output="${dir}/results-mixed.bin"
  stop_probes
  vegeta report <"${dir}/results-mixed.bin" >"${dir}/vegeta-report-mixed.txt"
  vegeta report --type=json <"${dir}/results-mixed.bin" >"${dir}/results-mixed.json"
  log "k=${k}: done"
  grep -E "Requests|Success|Status Codes|Latencies" "${dir}/vegeta-report-mixed.txt" || true
  echo "--- probes por instancia ---"
  for idx in $(seq 0 $((k - 1))); do
    local port=$((BASE_PORT + idx))
    awk '{print $1, $2}' "${dir}/probes-${port}.txt" | sort | uniq -c | sort -rn | sed "s/^/  :${port} /"
  done
  stop_servers
}

mkdir -p "${OUT_DIR}"

for k in ${REPLICAS_LIST}; do
  echo "==================== k=${k} ===================="
  run_one "${k}"
done

log "done; artifacts in ${OUT_DIR}"
