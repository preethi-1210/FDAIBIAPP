#!/usr/bin/env bash
# Ryze Infinity health checks. Reports status only: no business data, answers or secrets.
#
#   healthcheck.sh            local checks (services, API, web app, database, Caddy, resources, backups)
#   healthcheck.sh --public   also checks https://www.ryzeaibiapp.com and the root-domain redirect
#
# When sourced (by deploy_release.sh / rollback.sh) it only defines wait_for_app.
set -uo pipefail

API="http://127.0.0.1:8013"
WEB="http://127.0.0.1:3000"
SITE="${SITE:-https://www.ryzeaibiapp.com}"

api_healthy() {
  # /health reports "ok" only when the database answers.
  curl -fsS --max-time 5 "${API}/health" 2>/dev/null | grep -q '"status":"ok"'
}
web_healthy() {
  curl -fsS --max-time 5 -o /dev/null "${WEB}/login" 2>/dev/null
}
wait_for_app() {
  local deadline=$((SECONDS + ${1:-120}))
  while (( SECONDS < deadline )); do
    if api_healthy && web_healthy; then
      echo "API and web app are healthy."
      return 0
    fi
    sleep 3
  done
  api_healthy && echo "API: ok" || echo "API: not healthy"
  web_healthy && echo "Web app: ok" || echo "Web app: not healthy"
  return 1
}

[[ "${BASH_SOURCE[0]}" != "$0" ]] && return 0

FAIL=0
check() {  # check <label> <command...>
  local label="$1"; shift
  if "$@" >/dev/null 2>&1; then printf '  OK    %s\n' "${label}"; else printf '  FAIL  %s\n' "${label}"; FAIL=1; fi
}
warn() { printf '  WARN  %s\n' "$1"; }

echo "Services"
for unit in ryze-backend ryze-frontend caddy postgresql; do
  check "${unit} is running" systemctl is-active --quiet "${unit}"
done

echo "Application"
check "API /health (database reachable)" api_healthy
check "Web app responds" web_healthy
ready="$(curl -sS --max-time 10 "${API}/ready" 2>/dev/null || true)"
if [[ -n "${ready}" ]]; then
  # Booleans only. warehouse_configured means Databricks settings are present; it does not run a
  # warehouse query. Databricks connectivity is proven by the acceptance tests (an Ask AI answer).
  echo "${ready}" | jq -r '.checks | to_entries[] | "  \(if .value then "OK  " else "FAIL" end)  ready: \(.key)"' 2>/dev/null || warn "ready: unreadable response"
  echo "${ready}" | jq -e '.ready' >/dev/null 2>&1 || FAIL=1
else
  printf '  FAIL  ready endpoint did not answer\n'; FAIL=1
fi
check "PostgreSQL accepts connections" pg_isready -q -h 127.0.0.1 -p 5432

echo "Exposure"
for port in 3000 8013 5432; do
  if ss -ltnH "( sport = :${port} )" | awk '{print $4}' | grep -qvE '^(127\.0\.0\.1|\[::1\]):'; then
    printf '  FAIL  port %s listens beyond localhost\n' "${port}"; FAIL=1
  else
    printf '  OK    port %s is local only\n' "${port}"
  fi
done

echo "Resources"
disk_used="$(df --output=pcent /opt | tail -1 | tr -dc 0-9)"
mem_avail="$(awk '/MemAvailable/ {printf "%d", $2/1024}' /proc/meminfo)"
printf '  INFO  disk /opt %s%% used, %s MB memory available, load %s\n' "${disk_used}" "${mem_avail}" "$(cut -d' ' -f1-3 /proc/loadavg)"
(( disk_used < 85 )) || warn "disk above 85%"
(( mem_avail > 512 )) || warn "less than 512 MB memory available"

echo "Backups"
latest="$(sudo -n find /var/backups/ryze -name '*.dump' -mtime -2 -print -quit 2>/dev/null || true)"
if [[ -n "${latest}" ]]; then printf '  OK    a backup from the last 48 hours exists\n'; else warn "no backup from the last 48 hours visible (run as root to check)"; fi

if [[ "${1:-}" == "--public" ]]; then
  echo "Public HTTPS"
  check "${SITE}/login over HTTPS" curl -fsS --max-time 15 -o /dev/null "${SITE}/login"
  check "${SITE}/api/health over HTTPS" curl -fsS --max-time 15 -o /dev/null "${SITE}/api/health"
  root_location="$(curl -sS --max-time 15 -o /dev/null -w '%{redirect_url}' https://ryzeaibiapp.com/ || true)"
  [[ "${root_location}" == https://www.ryzeaibiapp.com/* ]] && printf '  OK    root domain redirects to www\n' || { printf '  FAIL  root domain does not redirect to www\n'; FAIL=1; }
  docs_code="$(curl -sS --max-time 15 -o /dev/null -w '%{http_code}' "${SITE}/api/docs" || true)"
  [[ "${docs_code}" == "404" ]] && printf '  OK    API docs are not public\n' || { printf '  FAIL  /api/docs returned %s\n' "${docs_code}"; FAIL=1; }
fi

exit "${FAIL}"
