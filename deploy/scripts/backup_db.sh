#!/usr/bin/env bash
# Nightly PostgreSQL backup (ryze-db-backup.timer runs it as the postgres user).
# Custom-format dump, verified readable before it is kept; 14 days retained.
set -euo pipefail

DB_NAME="${DB_NAME:-ryze_production}"
DIR="${BACKUP_DIR:-/var/backups/ryze}"
KEEP_DAYS="${KEEP_DAYS:-14}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
FILE="${DIR}/${DB_NAME}_${STAMP}.dump"

umask 077
pg_dump --format=custom --no-owner --file="${FILE}.partial" "${DB_NAME}"
# A dump that pg_restore cannot list is not a backup.
pg_restore --list "${FILE}.partial" >/dev/null
mv "${FILE}.partial" "${FILE}"
sha256sum "${FILE}" >"${FILE}.sha256"
# Roles (with password hashes) are needed to restore onto a new server.
pg_dumpall --globals-only >"${DIR}/globals_${STAMP}.sql"

find "${DIR}" -maxdepth 1 -type f \( -name '*.dump' -o -name '*.sha256' -o -name 'globals_*.sql' \) -mtime +"${KEEP_DAYS}" -delete
echo "Backup written: $(basename "${FILE}") ($(du -h "${FILE}" | cut -f1))"
