#!/usr/bin/env bash
set -euo pipefail

ENTERPRISE_URL="${ENTERPRISE_URL:-https://enterprise.doorli.me}"
curl --fail --silent --show-error --max-time 15 "$ENTERPRISE_URL/api/method/ping" >/dev/null
printf 'PASS enterprise-ping %s\n' "$ENTERPRISE_URL/api/method/ping"
