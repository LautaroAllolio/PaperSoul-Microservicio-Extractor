#!/usr/bin/env bash
#
# TASK-39 — barrido de réplicas con Traefik desechable (balanceo real).
#
# Levanta el stack del Compose (k réplicas del extractor) detrás de un Traefik
# desechable (docker run, round-robin) y ejecuta Vegeta A (400 rps, 50 % POST /
# 50 % sondas) A TRAVÉS del balanceador. Por corrida guarda: resultados Vegeta
# (.bin/.json + reporte), access log de Traefik, el log de cada réplica,
# muestras de docker stats y el reparto de POST entre réplicas.
#
# El stack se levanta y se baja entre valores de k para no arrastrar un event
# loop saturado de la corrida anterior.
#
# Requiere: vegeta en el PATH, docker + docker compose, curl.
#
#   ./load-tests/sweep-replicas.sh                    # k=3,4,5 x 2 reps
#   SWEEP_REPLICAS=3 SWEEP_REPS=1 ./load-tests/sweep-replicas.sh
#
# Las tres condiciones sin las cuales la corrida mide la PLATAFORMA y no el
# servicio (las tres se pueden desactivar por entorno para diagnosticar):
#
#   1. CLIENT_GOMAXPROCS=2  el generador de carga no puede robarle la CPU al
#      sistema bajo prueba; si se queda sin CPU, el LB desborda su listen
#      backlog (somaxconn=4096), el kernel tira los SYN (tcp_max_syn_backlog
#      =256, sin syncookies) y el cliente entra en backoff exponencial: los
#      timeouts de 35 s no son del servicio (se ven como 499 en el access log
#      de Traefik).
#   2. Readiness real: las k réplicas deben estar "UP" en la API de Traefik
#      (su healthcheck les respondió /health). Con un gate laxo el ataque
#      arranca mientras el resto de las réplicas calienta y Traefik enruta a
#      contenedores que todavía no escuchan -> 502 y réplicas descartadas.
#   3. Host ocioso antes de medir: al levantar la pila (k x 8 procesos) el host
#      queda saturado; medir en pleno temporal de arranque mide el arranque.
#
# Nota de versión de Traefik: v3.5 usa un cliente de Docker que pide la API
# 1.24, y el daemon de Docker Engine 29.x exige >= 1.40 -> el Docker provider
# falla y TODO el tráfico al router devuelve 404. Se usa v3.7.16, que sí
# negocia la versión de API.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
RUN_DIR="${REPO_ROOT}/load-tests/run-9000"
OUT_DIR="${RUN_DIR}/sweep-replicas"
LB_TARGETS_SRC="${SCRIPT_DIR}/targets-mixed-lb.txt"
LB_TARGETS="${RUN_DIR}/targets-mixed-lb.txt"

SWEEP_REPLICAS="${SWEEP_REPLICAS:-3 4 5}"
SWEEP_REPS="${SWEEP_REPS:-2}"
SWEEP_RATE="${SWEEP_RATE:-400}"
SWEEP_DURATION="${SWEEP_DURATION:-30s}"
SWEEP_TIMEOUT="${SWEEP_TIMEOUT:-35s}"
SWEEP_MAX_BODY="${SWEEP_MAX_BODY:-1024}"

# Pool por réplica: N=7 (el óptimo medido en TASK-35/37). Con N=2 la puerta del
# event loop (K=N) rechazaría tráfico de forma prematura y mediríamos un cuello
# de botella artificial, no el techo real del cluster.
EXTRACTOR_WORKERS="${EXTRACTOR_WORKERS:-7}"
EXTRACTOR_MAX_CONCURRENT="${EXTRACTOR_MAX_CONCURRENT:-7}"
EXTRACTOR_QUEUE_TIMEOUT="${EXTRACTOR_QUEUE_TIMEOUT:-2}"
EXTRACTOR_MAX_UPLOAD_BYTES="${EXTRACTOR_MAX_UPLOAD_BYTES:-1048576}"

LB_PORT="${LB_PORT:-18080}"
LB_API_PORT="${LB_API_PORT:-18081}"
LB_HOST="${LB_HOST:-extractor.universidad.localhost}"
TRAEFIK_IMAGE="${TRAEFIK_IMAGE:-traefik:v3.7.16}"
TRAEFIK_CONTAINER="${TRAEFIK_CONTAINER:-traefik-sweep}"
TRAEFIK_NETWORK="${TRAEFIK_NETWORK:-mired}"
STATS_INTERVAL="${STATS_INTERVAL:-5}"
BOUNDARY="----pdfextractorVegetaB0UNDARY"

# CPU del generador de carga. En este host (8 vCPU) con GOMAXPROCS=8 (por
# defecto) el propio Vegeta se come varios núcleos y el montaje entero entra en
# un colapso espurio: el listen backlog de Traefik (somaxconn=4096) desborda,
# el kernel descarta los SYN (tcp_max_syn_backlog=256, sin syncookies) y el
# cliente entra en backoff exponencial -> timeouts de 35 s que NO son culpa del
# servicio. Con GOMAXPROCS=2 el generador deja CPU al sistema bajo prueba y la
# degradación es la real: 503 acotados a ~2 s, cero timeouts.
CLIENT_GOMAXPROCS="${CLIENT_GOMAXPROCS:-2}"

STATS_PID=""

log() { printf '[replicas] %s\n' "$*"; }

stop_stats() {
  if [[ -n "${STATS_PID}" ]] && kill -0 "${STATS_PID}" 2>/dev/null; then
    kill -TERM "${STATS_PID}" 2>/dev/null || true
    wait "${STATS_PID}" 2>/dev/null || true
  fi
  STATS_PID=""
}

stop_traefik() {
  docker rm -f "${TRAEFIK_CONTAINER}" >/dev/null 2>&1 || true
}

cleanup() {
  stop_stats
  docker compose -f "${REPO_ROOT}/docker-compose.yml" down --remove-orphans >/dev/null 2>&1 || true
  stop_traefik
}
trap cleanup EXIT INT TERM

# El Compose declara `mired` como red externa: si no existe, se crea aquí.
ensure_network() {
  if ! docker network inspect "${TRAEFIK_NETWORK}" >/dev/null 2>&1; then
    log "creating external network ${TRAEFIK_NETWORK}"
    docker network create "${TRAEFIK_NETWORK}" >/dev/null
  fi
}

start_traefik() {
  stop_traefik
  log "starting disposable Traefik (${TRAEFIK_IMAGE}) on :${LB_PORT}"
  docker run -d --name "${TRAEFIK_CONTAINER}" \
    --network "${TRAEFIK_NETWORK}" \
    -p "${LB_PORT}:80" \
    -p "${LB_API_PORT}:8080" \
    -v /var/run/docker.sock:/var/run/docker.sock:ro \
    "${TRAEFIK_IMAGE}" \
    --providers.docker.exposedbydefault=false \
    --providers.docker.network="${TRAEFIK_NETWORK}" \
    --entrypoints.http.address=:80 \
    --entrypoints.traefik.address=:8080 \
    --api=true \
    --api.insecure=true \
    --log.level=INFO \
    --accesslog=true >/dev/null
}

# Readiness real: el LB tiene que tener las k réplicas en "UP" (o sea, el
# healthcheck de Traefik les respondió /health) Y contestar 200. Con un readiness
# laxo (un solo 200) el ataque arranca mientras el resto de las réplicas sigue
# calentando: Traefik enruta a contenedores que aún no escuchan -> 502 y
# réplicas descartadas por el provider (k=5 rep=1: 162 rps y 3 réplicas en 0).
# La API va por su propio entrypoint (`traefik`), no el del LB desechable.
lb_servers_up() {
  curl -s --max-time 3 \
    "http://127.0.0.1:${LB_API_PORT}/api/http/services/extractor@docker" 2>/dev/null \
    | grep -o '"UP"' | wc -l
}

wait_lb() {
  local expected="$1"
  local up=0
  for _ in $(seq 1 240); do
    up=$(lb_servers_up)
    code=$(curl -s -o /dev/null -m 2 -w '%{http_code}' \
      -H "Host: ${LB_HOST}" "http://127.0.0.1:${LB_PORT}/health" 2>/dev/null || echo "000")
    if [[ "${up}" -ge "${expected}" && "${code}" == "200" ]]; then
      log "load balancer ready (${up}/${expected} backends Up)"
      return 0
    fi
    sleep 1
  done
  log "timeout esperando al LB (backends Up: ${up}/${expected})"
  return 1
}

# CPU ocupada en el instante (%), medida sobre el delta de /proc/stat.
# Campos de la línea "cpu": user nice system idle iowait irq softirq steal
# -> en awk son $2..$9; idle es $5 e iowait $6.
cpu_busy_pct() {
  local a b
  a=$(awk '/^cpu /{print $2, $3, $4, $5, $6, $7, $8}' /proc/stat)
  sleep 1
  b=$(awk '/^cpu /{print $2, $3, $4, $5, $6, $7, $8}' /proc/stat)
  awk -v a="${a}" -v b="${b}" 'BEGIN{
    split(a, A, " "); split(b, B, " ");
    total = 0; for (i = 1; i <= 7; i++) total += B[i] - A[i];
    idle = (B[4] - A[4]) + (B[5] - A[5]);
    if (total <= 0) { print "0.0" } else { printf "%.1f", 100 * (total - idle) / total }
  }'
}

# Espera a que el host tenga CPU libre antes de medir: al levantar la pila (k x 8
# procesos) el host queda saturado ~30 s y si el ataque arranca en pleno
# temporal de arranque el LB se queda sin CPU, su listen backlog (somaxconn=
# 4096) desborda, el kernel tira los SYN y el cliente entra en backoff
# exponencial -> timeouts de 35 s que miden la plataforma, no el servicio.
wait_host_idle() {
  local max_wait="${HOST_IDLE_MAX_WAIT:-180}"
  local min_wait="${HOST_IDLE_MIN_WAIT:-15}"
  local busy waited=0
  while ((waited < max_wait)); do
    busy=$(cpu_busy_pct)
    if ((waited >= min_wait)) && awk -v b="${busy}" -v t="${HOST_IDLE_CPU_PCT:-35}" 'BEGIN{exit !(b <= t)}'; then
      log "host idle (cpu=${busy}% after ${waited}s)"
      return 0
    fi
    sleep 2
    waited=$((waited + 3))
  done
  log "host did not settle within ${max_wait}s (cpu=${busy}%); measuring anyway"
}

# Targets del LB: se derivan del fichero versionado (fuente única de la mezcla)
# sustituyendo el puerto y el host de esta corrida.
write_lb_targets() {
  mkdir -p "${RUN_DIR}"
  sed -e "s|127\.0\.0\.1:18080|127.0.0.1:${LB_PORT}|g" \
      -e "s|^Host: extractor\.universidad\.localhost$|Host: ${LB_HOST}|" \
    "${LB_TARGETS_SRC}" >"${LB_TARGETS}"
}

# Muestreo de CPU/RAM por contenedor + carga del host durante el ataque.
start_stats() {
  local out="$1"
  : >"${out}"
  (
    while true; do
      {
        printf 'ts=%s host_load=%s host_mem_used_kb=%s\n' \
          "$(date +%s)" \
          "$(cut -d' ' -f1-3 /proc/loadavg)" \
          "$(awk '/MemAvailable/ {print $2}' /proc/meminfo)"
        docker stats --no-stream --format 'container={{.Name}} cpu={{.CPUPerc}} mem={{.MemUsage}}' \
          2>/dev/null | grep -E 'extractor|traefik' || true
      } >>"${out}"
      sleep "${STATS_INTERVAL}"
    done
  ) &
  STATS_PID=$!
}

# Reparto entre réplicas: los POST de cada contenedor (las sondas no se loguean).
replica_distribution() {
  local dir="$1"
  local out="${dir}/replica-distribution.txt"
  : >"${out}"
  {
    printf 'POST por réplica (log JSON del extractor):\n'
    for cid in $(docker ps --format '{{.Names}}' | grep extractor | sort); do
      printf '  %s: %s\n' "${cid}" \
        "$(docker logs "${cid}" 2>&1 | grep -c '"method":"POST"' || true)"
    done
    printf 'POST por réplica (access log de Traefik, backend):\n'
    docker logs "${TRAEFIK_CONTAINER}" 2>&1 \
      | grep '"POST /api/v1/extractions' \
      | grep -o 'http://[0-9.]*:[0-9]*' | sort | uniq -c | sed 's/^/  /' || true
    # Una réplica con 0 POST significa que el balanceo no la alcanzó (réplica
    # caída, descartada por el healthcheck o warming) -> la corrida no sirve.
    local empty
    empty=$(docker ps --format '{{.Names}}' | grep extractor | while read -r cid; do
      n=$(docker logs "${cid}" 2>&1 | grep -c '"method":"POST"' || true)
      if [[ "${n}" -eq 0 ]]; then printf '%s ' "${cid}"; fi
    done)
    if [[ -n "${empty}" ]]; then
      printf 'CORRIDA INVALIDA: réplicas sin POST: %s\n' "${empty}"
    else
      printf 'CORRIDA VALIDA: todas las réplicas recibieron POST\n'
    fi
  } >>"${out}"
  cat "${out}"
}

run_one() {
  local k="$1" rep="$2"
  local dir="${OUT_DIR}/k${k}_r${rep}"
  mkdir -p "${dir}"

  log "k=${k} rep=${rep}: bringing the stack down (clean slate)"
  docker compose -f "${REPO_ROOT}/docker-compose.yml" down --remove-orphans >/dev/null 2>&1 || true
  start_traefik

  log "k=${k} rep=${rep}: starting ${k} replicas (N=${EXTRACTOR_MAX_CONCURRENT})"
  EXTRACTOR_REPLICAS="${k}" \
  EXTRACTOR_WORKERS="${EXTRACTOR_WORKERS}" \
  EXTRACTOR_MAX_CONCURRENT="${EXTRACTOR_MAX_CONCURRENT}" \
  EXTRACTOR_ENTRYPOINT=http \
  EXTRACTOR_TLS=false \
  TRAEFIK_NETWORK="${TRAEFIK_NETWORK}" \
  PDFEXTRACTOR_QUEUE_TIMEOUT_SECONDS="${EXTRACTOR_QUEUE_TIMEOUT}" \
  PDFEXTRACTOR_MAX_UPLOAD_BYTES="${EXTRACTOR_MAX_UPLOAD_BYTES}" \
    docker compose -f "${REPO_ROOT}/docker-compose.yml" up -d >/dev/null

  if ! wait_lb "${k}"; then
    log "k=${k} rep=${rep}: the load balancer never had all ${k} backends Up"
    return 1
  fi
  # Que la plataforma se asiente antes de medir: al levantar k pila (k x 8
  # procesos) el host queda en load 15-30 durante medio minuto, y si el ataque
  # arranca en pleno temporal de arranque el LB se queda sin CPU: su listen
  # backlog (somaxconn=4096) desborda, el kernel tira los SYN y el cliente entra
  # en backoff exponencial -> timeouts de 35 s que miden la plataforma, no el
  # servicio (k=4 rep=1: 12.9 % de éxito con 1289 "499" en el access log).
  wait_host_idle
  log "k=${k} rep=${rep}: load balancer ready"

  write_lb_targets
  start_stats "${dir}/stats.txt"

  log "k=${k} rep=${rep}: attacking ${SWEEP_RATE} rps for ${SWEEP_DURATION} through Traefik"
  GOMAXPROCS="${CLIENT_GOMAXPROCS}" vegeta attack \
    -targets="${LB_TARGETS}" \
    -body="${RUN_DIR}/body5p.bin" \
    -header="Content-Type: multipart/form-data; boundary=${BOUNDARY}" \
    -rate="${SWEEP_RATE}" -duration="${SWEEP_DURATION}" -timeout="${SWEEP_TIMEOUT}" \
    -max-body="${SWEEP_MAX_BODY}" \
    -output="${dir}/results-mixed.bin"

  stop_stats

  vegeta report <"${dir}/results-mixed.bin" >"${dir}/vegeta-report-mixed.txt"
  vegeta report --type=json <"${dir}/results-mixed.bin" >"${dir}/results-mixed.json"
  docker logs "${TRAEFIK_CONTAINER}" >"${dir}/traefik-access.log" 2>&1 || true
  for cid in $(docker ps --format '{{.Names}}' | grep extractor | sort); do
    docker logs "${cid}" >"${dir}/server-${cid#papersoul-extractor-}.log" 2>&1 || true
  done

  log "k=${k} rep=${rep}: done"
  grep -E "Requests|Success|Status Codes|Latencies" "${dir}/vegeta-report-mixed.txt" || true
  replica_distribution "${dir}"

  docker compose -f "${REPO_ROOT}/docker-compose.yml" down --remove-orphans >/dev/null 2>&1 || true
}

ensure_network
write_lb_targets
log "building the image"
docker compose -f "${REPO_ROOT}/docker-compose.yml" build >/dev/null

mkdir -p "${OUT_DIR}"
for k in ${SWEEP_REPLICAS}; do
  for rep in $(seq 1 "${SWEEP_REPS}"); do
    echo "==================== k=${k} rep=${rep} ===================="
    # Una corrida abortada (LB que no levanta, imagen que no compila) no debe
    # tumbar el barrido entero: se registra y se sigue con la siguiente.
    if ! run_one "${k}" "${rep}"; then
      log "k=${k} rep=${rep}: corrida abortada"
    fi
  done
done

stop_traefik
log "sweep complete; artifacts in ${OUT_DIR}"
