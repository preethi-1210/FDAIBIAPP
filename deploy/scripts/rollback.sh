#!/usr/bin/env bash
# Switch back to an earlier release and restart. Run on the server as the deploy user:
#
#   /opt/ryze/bin/rollback.sh            # the most recent release before the current one
#   /opt/ryze/bin/rollback.sh <id>       # a specific release (ls /opt/ryze/releases)
#
# Code only: it does not undo database changes. Restore those from a backup if ever needed.
set -euo pipefail

APP_ROOT="${APP_ROOT:-/opt/ryze}"
LOG="${APP_ROOT}/shared/logs/deploy.log"
exec > >(tee -a "${LOG}") 2>&1
exec 9>"${APP_ROOT}/.deploy.lock"
flock -n 9 || { echo "A deployment is in progress; try again when it finishes."; exit 1; }
# shellcheck source=deploy/scripts/healthcheck.sh
source "$(dirname "$(readlink -f "$0")")/healthcheck.sh"

CURRENT="$(readlink -f "${APP_ROOT}/current")"
if [[ -n "${1:-}" ]]; then
  TARGET="${APP_ROOT}/releases/$1"
else
  TARGET="$(ls -1dt "${APP_ROOT}"/releases/*/ | sed 's:/$::' | grep -vxF "${CURRENT}" | head -1 || true)"
fi
[[ -n "${TARGET}" && -d "${TARGET}/backend/.venv/bin" && -d "${TARGET}/frontend/.next" ]] \
  || { echo "No usable release to roll back to."; exit 1; }
[[ "${TARGET}" != "${CURRENT}" ]] || { echo "$(basename "${TARGET}") is already current."; exit 0; }

echo "[$(date -u +%FT%TZ)] rollback $(basename "${CURRENT}") -> $(basename "${TARGET}")"
ln -sfn "${TARGET}" "${APP_ROOT}/current.next"
mv -Tf "${APP_ROOT}/current.next" "${APP_ROOT}/current"
sudo /usr/bin/systemctl restart ryze-backend.service
sudo /usr/bin/systemctl restart ryze-frontend.service
if wait_for_app 180; then
  basename "${TARGET}" >"${APP_ROOT}/shared/last_successful_release"
  echo "Rollback complete; $(basename "${TARGET}") is healthy."
else
  echo "Rolled back, but health checks fail. Check: journalctl -u ryze-backend -u ryze-frontend -n 200"
  exit 1
fi
