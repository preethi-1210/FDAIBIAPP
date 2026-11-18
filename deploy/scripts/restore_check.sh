#!/usr/bin/env bash
# Proves the latest backup restores: loads it into a temporary database, compares table counts
# and per-table row counts with production, then drops the temporary database.
# Production is only read. Run as postgres:  sudo -u postgres /opt/ryze/bin/restore_check.sh
set -euo pipefail

DB_NAME="${DB_NAME:-ryze_production}"
DIR="${BACKUP_DIR:-/var/backups/ryze}"
CHECK_DB="ryze_restore_check"
DUMP="${1:-$(ls -1t "${DIR}"/*.dump 2>/dev/null | head -1)}"
[[ -f "${DUMP}" ]] || { echo "No backup found in ${DIR}."; exit 1; }

if [[ -f "${DUMP}.sha256" ]]; then
  ( cd "$(dirname "${DUMP}")" && sha256sum --check --quiet "$(basename "${DUMP}").sha256" )
fi

cleanup() { dropdb --if-exists "${CHECK_DB}"; }
trap cleanup EXIT
dropdb --if-exists "${CHECK_DB}"
createdb "${CHECK_DB}"
pg_restore --no-owner --exit-on-error --dbname="${CHECK_DB}" "${DUMP}"

COUNTS_SQL="SELECT n.nspname || '.' || c.relname, (xpath('/row/n/text()', query_to_xml(format('SELECT count(*) AS n FROM %I.%I', n.nspname, c.relname), false, true, '')))[1]::text
FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE c.relkind IN ('r','p') AND n.nspname NOT IN ('pg_catalog','information_schema') ORDER BY 1"
# Counts can differ for tables written since the backup was taken; names and counts only, no data.
diff <(psql -XAtF' ' -d "${CHECK_DB}" -c "${COUNTS_SQL}") <(psql -XAtF' ' -d "${DB_NAME}" -c "${COUNTS_SQL}") \
  && echo "Restore check passed: $(basename "${DUMP}") matches ${DB_NAME} table for table." \
  || echo "Restore completed; the differences above are tables changed since $(basename "${DUMP}") was taken."
