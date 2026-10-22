#!/usr/bin/env bash
# Control Viewlift AI without hardcoding PIDs or ports.
# Discovers running instances by process command line; ports come from lsof.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CTL_NAME="$(basename "${BASH_SOURCE[0]}")"

usage() {
  cat <<EOF
Usage: $CTL_NAME <start|stop|restart|status|run|kill> [--bg]

  start | run      Start viewlift-ai from repo root (foreground unless --bg)
  stop  | kill     Stop every matching viewlift-ai process
  restart          stop, then start
  status           Show matching processes and their listen sockets

Ports follow VIEWLIFT_* env / app defaults (not hardcoded here).
EOF
}

# PIDs whose command looks like the app entrypoint, not this helper script.
find_pids() {
  local pid cmd
  while IFS= read -r pid; do
    [[ -z "$pid" ]] && continue
    [[ "$pid" == "$$" ]] && continue
    cmd="$(ps -p "$pid" -o command= 2>/dev/null || true)"
    [[ -z "$cmd" ]] && continue
    case "$cmd" in
      *"$CTL_NAME"*) continue ;;
    esac
    # Match: .../viewlift-ai, `uv run viewlift-ai`, or module entry.
    if [[ "$cmd" == */viewlift-ai* ]] \
      || [[ "$cmd" == *" uv run viewlift-ai"* ]] \
      || [[ "$cmd" == *"uv run viewlift-ai"* ]] \
      || [[ "$cmd" == *viewlift_ai.app* ]]; then
      printf '%s\n' "$pid"
    fi
  done < <(pgrep -f 'viewlift-ai|viewlift_ai\.app' 2>/dev/null || true)
}

listen_summary() {
  local pid="$1"
  lsof -nP -a -p "$pid" -iTCP -sTCP:LISTEN 2>/dev/null \
    | awk 'NR>1 { print $9 }' \
    | sort -u \
    | tr '\n' ' ' \
    | sed 's/[[:space:]]*$//'
}

cmd_status() {
  local pids
  pids="$(find_pids | sort -u)"
  if [[ -z "$pids" ]]; then
    echo "viewlift-ai: not running"
    return 1
  fi
  local pid cmd listens
  while IFS= read -r pid; do
    [[ -z "$pid" ]] && continue
    cmd="$(ps -p "$pid" -o command= 2>/dev/null || echo '?')"
    listens="$(listen_summary "$pid")"
    if [[ -n "$listens" ]]; then
      echo "pid $pid  listen $listens"
    else
      echo "pid $pid  (no TCP listen yet)"
    fi
    echo "  $cmd"
  done <<<"$pids"
  return 0
}

cmd_stop() {
  local pids
  pids="$(find_pids | sort -u)"
  if [[ -z "$pids" ]]; then
    echo "viewlift-ai: not running"
    return 0
  fi
  echo "Stopping: $(tr '\n' ' ' <<<"$pids")"
  # TERM first, then KILL stragglers.
  # shellcheck disable=SC2086
  kill $pids 2>/dev/null || true
  local i
  for i in 1 2 3 4 5 6 7 8 9 10; do
    pids="$(find_pids | sort -u)"
    [[ -z "$pids" ]] && break
    sleep 0.3
  done
  pids="$(find_pids | sort -u)"
  if [[ -n "$pids" ]]; then
    echo "Force kill: $(tr '\n' ' ' <<<"$pids")"
    # shellcheck disable=SC2086
    kill -9 $pids 2>/dev/null || true
    sleep 0.2
  fi
  if [[ -n "$(find_pids)" ]]; then
    echo "viewlift-ai: still running after kill" >&2
    cmd_status || true
    return 1
  fi
  echo "viewlift-ai: stopped"
}

cmd_start() {
  local bg=0
  if [[ "${1:-}" == "--bg" ]]; then
    bg=1
  fi
  if [[ -n "$(find_pids)" ]]; then
    echo "viewlift-ai already running:" >&2
    cmd_status || true
    echo "Use: $CTL_NAME stop   or   $CTL_NAME restart" >&2
    return 1
  fi
  cd "$ROOT"
  if [[ "$bg" -eq 1 ]]; then
    # Detached; logs to repo-local file so start stays portable.
    local log="$ROOT/.viewlift-ai.log"
    nohup uv run viewlift-ai >>"$log" 2>&1 &
    disown || true
    echo "viewlift-ai: started in background (pid $!)"
    echo "log: $log"
    sleep 0.8
    cmd_status || true
  else
    echo "viewlift-ai: starting in foreground (Ctrl+C to stop)"
    exec uv run viewlift-ai
  fi
}

main() {
  local action="${1:-}"
  shift || true
  case "$action" in
    start|run) cmd_start "${1:-}" ;;
    stop|kill) cmd_stop ;;
    restart)
      cmd_stop
      cmd_start "${1:-}"
      ;;
    status) cmd_status ;;
    -h|--help|help|"") usage; [[ -n "$action" ]] || exit 1 ;;
    *)
      echo "Unknown action: $action" >&2
      usage >&2
      exit 1
      ;;
  esac
}

main "$@"
