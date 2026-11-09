#!/usr/bin/env bash
# Control Viewlift AI without hardcoding PIDs or ports.
# Discovers running instances by process command line; ports come from lsof.
# start also launches the loopback MCP server. Stdio MCP exits with no client,
# so this companion uses streamable HTTP (see VIEWLIFT_AI_MCP_TRANSPORT).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CTL_NAME="$(basename "${BASH_SOURCE[0]}")"

usage() {
  cat <<EOF
Usage: $CTL_NAME <start|stop|restart|status|run|kill> [--bg]

  start | run      Start the console and the loopback MCP server
                   (console in foreground unless --bg; MCP stays in background)
  stop  | kill     Stop the console and the MCP server
  restart          stop, then start
  status           Show matching processes and their listen sockets

Ports follow VIEWLIFT_* env / app defaults (not hardcoded here).
EOF
}

# Lines of "app <pid>" or "mcp <pid>". Skips this helper script.
collect_pids() {
  local pid cmd
  while IFS= read -r pid; do
    [[ -z "$pid" ]] && continue
    [[ "$pid" == "$$" ]] && continue
    cmd="$(ps -p "$pid" -o command= 2>/dev/null || true)"
    [[ -z "$cmd" ]] && continue
    case "$cmd" in
      *"$CTL_NAME"*) continue ;;
    esac
    if [[ "$cmd" == *viewlift-ai-mcp* || "$cmd" == *viewlift_ai.mcp_server* ]]; then
      printf 'mcp %s\n' "$pid"
    elif [[ "$cmd" == */viewlift-ai* ]] \
      || [[ "$cmd" == *"uv run viewlift-ai"* ]] \
      || [[ "$cmd" == *viewlift_ai.app* ]]; then
      printf 'app %s\n' "$pid"
    fi
  done < <(pgrep -f 'viewlift-ai|viewlift_ai\.app|viewlift_ai\.mcp_server' 2>/dev/null || true)
}

pids_of() {
  local kind="$1"
  collect_pids | awk -v kind="$kind" '$1 == kind { print $2 }'
}

listen_summary() {
  local pid="$1"
  local ids="$pid"
  local kids
  # `uv run` holds no socket. The interpreter child does.
  kids="$(pgrep -P "$pid" 2>/dev/null | paste -sd, - || true)"
  if [[ -n "$kids" ]]; then
    ids="${pid},${kids}"
  fi
  lsof -nP -a -p "$ids" -iTCP -sTCP:LISTEN 2>/dev/null \
    | awk 'NR>1 { print $9 }' \
    | sort -u \
    | tr '\n' ' ' \
    | sed 's/[[:space:]]*$//'
}

print_group() {
  local label="$1"
  local pids="$2"
  local pid cmd listens
  if [[ -z "$pids" ]]; then
    echo "$label: not running"
    return 0
  fi
  while IFS= read -r pid; do
    [[ -z "$pid" ]] && continue
    cmd="$(ps -p "$pid" -o command= 2>/dev/null || echo '?')"
    listens="$(listen_summary "$pid")"
    if [[ -n "$listens" ]]; then
      echo "$label pid $pid  listen $listens"
    else
      echo "$label pid $pid  (no TCP listen yet)"
    fi
    echo "  $cmd"
  done <<<"$pids"
}

cmd_status() {
  local apps mcps
  apps="$(pids_of app | sort -u)"
  mcps="$(pids_of mcp | sort -u)"
  if [[ -z "$apps" && -z "$mcps" ]]; then
    echo "viewlift-ai: not running"
    return 1
  fi
  print_group "viewlift-ai" "$apps"
  print_group "viewlift-ai-mcp" "$mcps"
  [[ -n "$apps" ]]
}

# A killed console leaves its Bonjour advertisement (a dns-sd child) running with an old
# address. Phones can pick that record, so remove it whenever the console stops.
reap_advertisements() {
  pkill -u "$(id -u)" -f 'dns-sd -R Viewlift AI _viewliftdebug._tcp' 2>/dev/null || true
}

cmd_stop() {
  local pids
  pids="$( { pids_of app; pids_of mcp; } | sort -u )"
  if [[ -z "$pids" ]]; then
    reap_advertisements
    echo "viewlift-ai: not running"
    return 0
  fi
  echo "Stopping: $(tr '\n' ' ' <<<"$pids")"
  # TERM first, then KILL stragglers.
  # shellcheck disable=SC2086
  kill $pids 2>/dev/null || true
  local i
  for i in 1 2 3 4 5 6 7 8 9 10; do
    pids="$( { pids_of app; pids_of mcp; } | sort -u )"
    [[ -z "$pids" ]] && break
    sleep 0.3
  done
  pids="$( { pids_of app; pids_of mcp; } | sort -u )"
  if [[ -n "$pids" ]]; then
    echo "Force kill: $(tr '\n' ' ' <<<"$pids")"
    # shellcheck disable=SC2086
    kill -9 $pids 2>/dev/null || true
    sleep 0.2
  fi
  if [[ -n "$( { pids_of app; pids_of mcp; } )" ]]; then
    echo "viewlift-ai: still running after kill" >&2
    cmd_status || true
    return 1
  fi
  reap_advertisements
  echo "viewlift-ai: stopped"
}

start_mcp() {
  if [[ -n "$(pids_of mcp)" ]]; then
    return 0
  fi
  local log="$ROOT/.viewlift-ai-mcp.log"
  VIEWLIFT_AI_MCP_TRANSPORT=streamable-http \
    nohup uv run --extra mcp viewlift-ai-mcp >>"$log" 2>&1 &
  disown || true
  echo "viewlift-ai-mcp: started in background (pid $!)"
  echo "log: $log"
}

cmd_start() {
  local bg=0
  if [[ "${1:-}" == "--bg" ]]; then
    bg=1
  fi
  local have_app=0 have_mcp=0
  [[ -n "$(pids_of app)" ]] && have_app=1
  [[ -n "$(pids_of mcp)" ]] && have_mcp=1
  if [[ "$have_app" -eq 1 && "$have_mcp" -eq 1 ]]; then
    echo "viewlift-ai already running:" >&2
    cmd_status || true
    echo "Use: $CTL_NAME stop   or   $CTL_NAME restart" >&2
    return 1
  fi
  cd "$ROOT"
  if [[ "$have_app" -eq 0 && "$bg" -eq 0 ]]; then
    [[ "$have_mcp" -eq 0 ]] && start_mcp
    trap 'cmd_stop' INT TERM
    echo "viewlift-ai: starting in foreground (Ctrl+C to stop)"
    set +e
    uv run viewlift-ai
    local status=$?
    set -e
    trap - INT TERM
    cmd_stop
    return "$status"
  fi
  if [[ "$have_app" -eq 0 ]]; then
    local log="$ROOT/.viewlift-ai.log"
    nohup uv run viewlift-ai >>"$log" 2>&1 &
    disown || true
    echo "viewlift-ai: started in background (pid $!)"
    echo "log: $log"
  fi
  if [[ "$have_mcp" -eq 0 ]]; then
    start_mcp
  fi
  sleep 0.8
  cmd_status || true
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
