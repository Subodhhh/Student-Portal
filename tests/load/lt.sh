#!/usr/bin/env bash
# The ONLY entry point of the load-test stack. Pins the project name, the interpolation file and the override, and runs the branch
# guard before anything that touches a database. It never runs `down -v`, prune, migrations or the dev project.
#
#   LT_TARGET=neon|local|stage2 tests/load/lt.sh <command>
#     config [app|app-stage1]    validate the merged Compose model of the target's profiles; prints service names and the
#                                background-job settings only (never values from env files)
#     guard <check|identify|pin> ...   branch guard (tests/load/guard/guard.py)
#     build <service...>         build loadtest/<service>:$LT_TAG images ONE at a time (dev :latest images untouched)
#     up <mode>                  start an operating mode (guard check first for app modes):
#                                  db          local PostgreSQL only                       (LT_TARGET=local)
#                                  app         preflight-only app stack: EVERY background writer off
#                                  app-stage1  the same, with the Institute -> Student reference sync ON (15 s)
#                                  student     Student services + Redis only, on the Stage 2 databases   (LT_TARGET=stage2)
#                                  monitoring  Prometheus, Grafana, Redis exporter (+ postgres exporter when local)
#                                The exam worker (profile `worker`) is never started by any mode.
#     sampler <start|stop|status>  host-side Docker stats sampler on 127.0.0.1:19101 (container CPU/RAM for Prometheus)
#     stop                       stop every container labelled with this project (no env files needed; volumes/networks kept)
#     ps | logs <service>        status / logs of the load-test project
#     k6 <script.js> [args]      co-located k6 run (results under tests/load/results/)
#     stage1 <plan|preflight|run|status|validate> [...]   Stage 1 loader (LT_TARGET=neon; `run` is a dry run without --execute)
#     stage2 <prepare|schema|plan|load|validate|fingerprint|bundle|drop> [--pair P] [--fraction F]   Stage 2 COPY dataset (LT_TARGET=local, guarded; above 5 % needs --approved-full)
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"
PROJECT=student-portal-loadtest
export LT_CONFIG_DIR="${LT_CONFIG_DIR:-./tests/load/config}"
COMPOSE_ENV="${LT_CONFIG_DIR}/compose.env"
export LT_TARGET="${LT_TARGET:-}"
export LT_TAG="${LT_TAG:-$(git rev-parse --short HEAD)}"
PY="${LT_PYTHON:-tests/load/.venv/bin/python}"
# Background writers are OFF unless a mode below turns the reference sync on. Always set here (never inherited from the caller's
# shell or from compose.env), so nothing outside this script can switch a writer on.
export LT_REFERENCE_SYNC_INTERVAL_SECONDS=0
STAGE1_SYNC_SECONDS=15

die() { echo "lt.sh: $*" >&2; exit 1; }
need_target() { [[ "$LT_TARGET" == neon || "$LT_TARGET" == local || "$LT_TARGET" == stage2 ]] || die "set LT_TARGET=neon, LT_TARGET=local or LT_TARGET=stage2"; }
compose() {
  [[ -f "$COMPOSE_ENV" ]] || die "$COMPOSE_ENV missing (create it with: python3 tests/load/config/init_env.py $LT_TARGET)"
  docker compose --project-directory "$ROOT" -p "$PROJECT" --env-file "$COMPOSE_ENV" \
    -f docker-compose.yml -f tests/load/compose.loadtest.yml "$@"
}
# Profiles a target may use. Neon never loads the local PostgreSQL services (and so never needs local/postgres.env).
target_profiles() {
  local p=(--profile app --profile optional --profile monitoring --profile k6)
  [[ "$LT_TARGET" != neon ]] && p+=(--profile db --profile monitoring-db)
  printf '%s\n' "${p[@]}"
}
guard() { "$PY" tests/load/guard/guard.py "$@"; }
mode_sync() {   # the reference-sync interval of an app mode
  case "$1" in
    app) echo 0 ;;
    app-stage1) echo "$STAGE1_SYNC_SECONDS" ;;
    *) die "unknown app mode '$1' (app | app-stage1)" ;;
  esac
}

cmd="${1:-}"; shift || true
case "$cmd" in
  config)
    need_target
    LT_REFERENCE_SYNC_INTERVAL_SECONDS="$(mode_sync "${1:-app}")"; export LT_REFERENCE_SYNC_INTERVAL_SECONDS
    mapfile -t profiles < <(target_profiles)
    err="$(mktemp)"; trap 'rm -f "$err"' EXIT
    # The rendered model holds env-file values (secrets): it stays in this variable and is only parsed for names and the four
    # background-job settings. A Compose failure is reported (paths shortened) instead of feeding empty output to the parser.
    if ! model="$(compose "${profiles[@]}" config --format json 2>"$err")"; then
      sed -e "s#$ROOT/##g" "$err" >&2
      die "Compose could not render the $LT_TARGET configuration (see the message above); nothing was started"
    fi
    printf '%s' "$model" | "${LT_PYTHON_SYS:-python3}" -c '
import json, sys
try:
    m = json.load(sys.stdin)
except ValueError:
    sys.exit("lt.sh: Compose returned no valid configuration")
JOBS = ("REFERENCE_SYNC_INTERVAL_SECONDS", "AUDIT_FORWARD_INTERVAL_SECONDS", "ATTEMPT_EXPIRY_SWEEP_SECONDS", "AUTO_EVALUATION_INTERVAL_SECONDS")
for name, s in sorted(m["services"].items()):
    print("%-34s %-36s %-22s %s" % (name, s.get("container_name", "-"), ",".join(s.get("profiles", [])), s.get("image", "-")))
print("background jobs (0 = off):")
for name, s in sorted(m["services"].items()):
    env = s.get("environment") or {}
    jobs = {k: env[k] for k in JOBS if k in env}
    if jobs:
        print("  %-34s %s" % (name, " ".join("%s=%s" % kv for kv in sorted(jobs.items()))))
' ;;
  guard) guard "$@" ;;
  build)
    need_target
    [[ $# -gt 0 ]] || die "name the services to build (one at a time keeps memory low)"
    # app + optional resolve every built service and its depends_on (e.g. optional -> redis); only the named service is built
    for s in "$@"; do compose --profile app --profile optional build "$s"; done ;;
  up)
    need_target
    mode="${1:-}"
    case "$mode" in
      db)  [[ "$LT_TARGET" == local ]] || die "mode db is for LT_TARGET=local"
           compose --profile db up -d --wait lt-postgres ;;
      app|app-stage1)
           LT_REFERENCE_SYNC_INTERVAL_SECONDS="$(mode_sync "$mode")"; export LT_REFERENCE_SYNC_INTERVAL_SECONDS
           guard check "$LT_TARGET" || die "branch guard refused: nothing started"
           compose --profile app up -d ;;
      student)
           [[ "$LT_TARGET" == stage2 ]] || die "mode student is for LT_TARGET=stage2"
           guard check stage2 || die "branch guard refused: nothing started"
           compose --profile app --profile optional up -d --no-build redis student-authentication-service student-profile-service \
             student-enrollment-service student-test-service student-attempt-service student-result-service student-ranking-service \
             student-notification-service ;;
      monitoring)
           profiles=(--profile monitoring); [[ "$LT_TARGET" == local ]] && profiles+=(--profile monitoring-db)
           compose "${profiles[@]}" up -d ;;
      *) die "unknown mode '$mode' (db | app | app-stage1 | student | monitoring)" ;;
    esac ;;
  sampler)
    run_dir="tests/load/results/monitoring"; pid_file="$run_dir/sampler.pid"
    mkdir -p "$run_dir"; chmod 700 tests/load/results "$run_dir"
    alive() { [[ -f "$pid_file" ]] && kill -0 "$(cat "$pid_file")" 2>/dev/null; }
    case "${1:-}" in
      start) alive && die "sampler already running (pid $(cat "$pid_file"))"
             nohup python3 tests/load/monitoring/docker_stats_exporter.py --project "$PROJECT" --host 127.0.0.1 --port 19101 \
               >> "$run_dir/sampler.log" 2>&1 &
             echo $! > "$pid_file"; chmod 600 "$pid_file" "$run_dir/sampler.log"; sleep 2
             alive || die "sampler did not start (see $run_dir/sampler.log)"
             echo "sampler running (pid $(cat "$pid_file")) on http://127.0.0.1:19101/metrics" ;;
      stop)  if alive; then kill "$(cat "$pid_file")"; echo "sampler stopped"; else echo "sampler not running"; fi; rm -f "$pid_file" ;;
      status) if alive; then echo "running (pid $(cat "$pid_file"))"; curl -fsS http://127.0.0.1:19101/metrics | grep -E '^lt_sampler_'; else echo "not running"; fi ;;
      *) die "sampler start|stop|status" ;;
    esac ;;
  stop)
    # No Compose model (so no env file is read): exactly the containers labelled with this project; kept, not removed.
    mapfile -t ids < <(docker ps -q --filter "label=com.docker.compose.project=$PROJECT")
    if [[ ${#ids[@]} -eq 0 ]]; then echo "no running $PROJECT containers"; else docker stop "${ids[@]}" >/dev/null; echo "stopped ${#ids[@]} $PROJECT container(s)"; fi ;;
  ps)    need_target; mapfile -t profiles < <(target_profiles); compose "${profiles[@]}" ps -a ;;
  logs)  need_target; mapfile -t profiles < <(target_profiles); compose "${profiles[@]}" logs --no-color --tail=200 "$@" ;;
  k6)
    need_target
    script="${1:?k6 script under tests/load/k6/}"; shift
    mkdir -p tests/load/results
    compose --profile k6 run --rm k6 run --out experimental-prometheus-rw "/scripts/$script" "$@" ;;
  stage1)
    [[ "$LT_TARGET" == neon ]] || die "Stage 1 runs against Neon only: set LT_TARGET=neon"
    py="$PY"; [[ "$py" == /* ]] || py="$ROOT/$py"
    (cd tests/load && exec "$py" -m generator "$@") ;;
  stage2)
    [[ "$LT_TARGET" == local ]] || die "Stage 2 loads the local PostgreSQL only: set LT_TARGET=local"
    py="$PY"; [[ "$py" == /* ]] || py="$ROOT/$py"
    (cd tests/load && exec "$py" -m bulk "$@") ;;
  *) sed -n '2,21p' "$0"; exit 1 ;;
esac
