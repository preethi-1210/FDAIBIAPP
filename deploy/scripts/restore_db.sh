#!/usr/bin/env bash
# Restores a backup into a NEW database; it never overwrites an existing one.
#
#   sudo -u postgres /opt/ryze/bin/restore_db.sh /var/backups/ryze/<file>.dump ryze_production_restored
#
# To put the restored copy into service, an administrator then points DATABASE_URL in
# /opt/ryze/shared/backend.env at the new database (or renames databases during a planned
# outage) and restarts ryze-backend. That switch is deliberately manual.
set -euo pipefail

DUMP="${1:?usage: restore_db.sh <dump-file> <new-database-name>}"
TARGET="${2:?usage: restore_db.sh <dump-file> <new-database-name>}"
OWNER="${DB_ROLE:-ryze_app}"

[[ -f "${DUMP}" ]] || { echo "Backup ${DUMP} not found."; exit 1; }
[[ "${TARGET}" =~ ^[a-z_][a-z0-9_]{0,62}$ ]] || { echo "Use a simple lowercase database name."; exit 1; }
if psql -XAtc "SELECT 1 FROM pg_database WHERE datname = '${TARGET}'" | grep -q 1; then
  echo "Database ${TARGET} already exists. Choose a new name; existing databases are never overwritten."
  exit 1
fi
if [[ -f "${DUMP}.sha256" ]]; then
  ( cd "$(dirname "${DUMP}")" && sha256sum --check --quiet "$(basename "${DUMP}").sha256" )
fi
createdb --owner="${OWNER}" "${TARGET}"
pg_restore --no-owner --role="${OWNER}" --exit-on-error --dbname="${TARGET}" "${DUMP}"
echo "Restored $(basename "${DUMP}") into new database ${TARGET} (owner ${OWNER})."
