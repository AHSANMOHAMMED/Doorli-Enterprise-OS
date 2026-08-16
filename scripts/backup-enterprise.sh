#!/usr/bin/env bash
# Back up the Enterprise database and Frappe site files as one checksum-verified archive.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BACKUP_DIR="${DOORLI_BACKUP_DIR:-$ROOT/backups}"
RETENTION_DAYS="${DOORLI_BACKUP_RETENTION_DAYS:-30}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
OUTPUT="$BACKUP_DIR/enterprise-$STAMP.tar.gz"

mkdir -p "$BACKUP_DIR"
set -a
[ -f "$ROOT/.env" ] && . "$ROOT/.env"
set +a
: "${FRAPPE_SITE_NAME_HEADER:?FRAPPE_SITE_NAME_HEADER must be set}"
SITE="$FRAPPE_SITE_NAME_HEADER"

# bench creates a compressed SQL dump and copies private/public files into the
# site's private/backups directory. The archive preserves both for a full restore.
docker compose -f "$ROOT/docker-compose.yml" exec -T backend \
  bash -lc "bench --site '$SITE' backup --with-files --compress && tar -C /home/frappe/frappe-bench/sites -czf - '$SITE/private/backups'" > "$OUTPUT"

test -s "$OUTPUT"
sha256sum "$OUTPUT" > "$OUTPUT.sha256"
find "$BACKUP_DIR" -type f -name 'enterprise-*.tar.gz' -mtime "+$RETENTION_DAYS" -delete
find "$BACKUP_DIR" -type f -name 'enterprise-*.tar.gz.sha256' -mtime "+$RETENTION_DAYS" -delete
printf 'Created %s (%s)\n' "$OUTPUT" "$(du -h "$OUTPUT" | cut -f1)"
