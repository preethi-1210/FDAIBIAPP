#!/usr/bin/env bash
# Activates an already-unpacked release on the server. Run by GitHub Actions as the deploy user:
#
#   bash /opt/ryze/releases/<id>/deploy/scripts/deploy_release.sh <id>
#
# Steps: lock -> install dependencies -> import check -> switch `current` -> restart -> health
# check -> on failure, switch back to the previous release and restart it.
# The previous release is never modified. Database migrations are never run here.
set -euo pipefail

RELEASE_ID="${1:?usage: deploy_release.sh <release-id>}"
APP_ROOT="${APP_ROOT:-/opt/ryze}"
RELEASE="${APP_ROOT}/releases/${RELEASE_ID}"
KEEP_RELEASES=5
LOG="${APP_ROOT}/shared/logs/deploy.log"

exec > >(tee -a "${LOG}") 2>&1
echo "[$(date -u +%FT%TZ)] deploy ${RELEASE_ID} starting"

# Only one deployment at a time, even if two workflow runs reach the server.
exec 9>"${APP_ROOT}/.deploy.lock"
flock -n 9 || { echo "Another deployment holds the lock; stopping."; exit 1; }

[[ -d "${RELEASE}/backend" && -d "${RELEASE}/frontend" ]] || { echo "Release ${RELEASE} is incomplete."; exit 1; }
# shellcheck source=deploy/scripts/healthcheck.sh
source "${RELEASE}/deploy/scripts/healthcheck.sh"

echo "-- Backend dependencies"
# Virtual environments are shared between releases with identical requirements and kept by hash,
# so a rollback finds its environment intact and an unchanged deploy installs nothing.
REQ_HASH="$(sha256sum "${RELEASE}/backend/requirements.txt" | cut -c1-16)"
VENV="${APP_ROOT}/shared/venvs/${REQ_HASH}"
if [[ ! -f "${VENV}/.complete" ]]; then
  # Built at its final path (installed launchers record that path); the marker is written only
  # after every package installed, so an interrupted build is discarded and redone.
  rm -rf -- "${VENV}"
  python3.13 -m venv "${VENV}"
  "${VENV}/bin/pip" install --quiet --upgrade pip
  "${VENV}/bin/pip" install --quiet -r "${RELEASE}/backend/requirements.txt"
  touch "${VENV}/.complete"
fi
ln -sfn "${VENV}" "${RELEASE}/backend/.venv"

echo "-- Backend import check"
# No production secrets are read here: the check imports the code with default settings.
( cd "${RELEASE}/backend" && env -i PATH=/usr/bin:/bin HOME="$(mktemp -d)" "${VENV}/bin/python" -c "import app.main" )

echo "-- Frontend dependencies"
( cd "${RELEASE}/frontend" && npm ci --no-audit --no-fund --loglevel=error )
[[ -d "${RELEASE}/frontend/.next" ]] || { echo "The release has no frontend build."; exit 1; }
# Next.js writes its runtime cache here; the release folder itself stays read-only to the service.
rm -rf "${RELEASE}/frontend/.next/cache"
ln -sfn "${APP_ROOT}/shared/next-cache" "${RELEASE}/frontend/.next/cache"

PREVIOUS="$(readlink -f "${APP_ROOT}/current" 2>/dev/null || true)"

switch_to() {
  ln -sfn "$1" "${APP_ROOT}/current.next"
  mv -Tf "${APP_ROOT}/current.next" "${APP_ROOT}/current"   # atomic rename
  sudo /usr/bin/systemctl restart ryze-backend.service
  sudo /usr/bin/systemctl restart ryze-frontend.service
}

echo "-- Switching to ${RELEASE_ID} (previous: ${PREVIOUS:-none})"
switch_to "${RELEASE}"

if wait_for_app 180; then
  echo "${RELEASE_ID}" >"${APP_ROOT}/shared/last_successful_release"
  echo "[$(date -u +%FT%TZ)] deploy ${RELEASE_ID} healthy"
else
  echo "Health checks failed for ${RELEASE_ID}."
  if [[ -n "${PREVIOUS}" && -d "${PREVIOUS}" && "${PREVIOUS}" != "${RELEASE}" ]]; then
    echo "-- Rolling back to $(basename "${PREVIOUS}")"
    switch_to "${PREVIOUS}"
    if wait_for_app 180; then
      echo "[$(date -u +%FT%TZ)] rolled back to $(basename "${PREVIOUS}"); it is healthy"
    else
      echo "[$(date -u +%FT%TZ)] ROLLBACK ALSO UNHEALTHY: check journalctl -u ryze-backend -u ryze-frontend"
    fi
  else
    echo "No previous release to roll back to."
  fi
  exit 1
fi

echo "-- Pruning old releases (keeping ${KEEP_RELEASES}, always keeping current and previous)"
CURRENT="$(readlink -f "${APP_ROOT}/current")"
mapfile -t OLD < <(ls -1dt "${APP_ROOT}"/releases/*/ 2>/dev/null | sed 's:/$::' | tail -n +$((KEEP_RELEASES + 1)))
for dir in "${OLD[@]}"; do
  [[ "${dir}" == "${CURRENT}" || "${dir}" == "${PREVIOUS}" ]] && continue
  rm -rf -- "${dir}"
done
# Remove environments no remaining release uses.
for venv in "${APP_ROOT}"/shared/venvs/*/; do
  venv="${venv%/}"
  if ! find "${APP_ROOT}/releases" -maxdepth 3 -lname "${venv}" -print -quit | grep -q .; then
    rm -rf -- "${venv}"
  fi
done
echo "[$(date -u +%FT%TZ)] deploy ${RELEASE_ID} complete"
