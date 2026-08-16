#!/usr/bin/env bash
# Verify an Enterprise backup checksum and archive contents without restoring data.
set -euo pipefail

if [ "$#" -ne 1 ]; then
  printf 'Usage: %s /path/to/enterprise-*.tar.gz\n' "$0" >&2
  exit 64
fi

ARCHIVE="$1"
test -s "$ARCHIVE"
if [ -f "$ARCHIVE.sha256" ]; then
  sha256sum --check "$ARCHIVE.sha256"
fi

tar -tzf "$ARCHIVE" | grep -q '/private/backups/'
printf 'Enterprise backup is readable and contains the Frappe backup directory: %s\n' "$ARCHIVE"
