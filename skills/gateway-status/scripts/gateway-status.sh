#!/usr/bin/env bash
# Print the Coder Gateway status (read API) for the most recently active conversation.
# Env: CODER_GATEWAY_URL (default http://127.0.0.1:8787), CODER_GATEWAY_KEY (default sk-gw-fwd-demo).
# Usage: gateway-status.sh [--json]
set -euo pipefail

url="${CODER_GATEWAY_URL:-http://127.0.0.1:8787}"
key="${CODER_GATEWAY_KEY:-sk-gw-fwd-demo}"

if ! body="$(curl -sS --fail-with-body --max-time 5 -H "Authorization: Bearer ${key}" "${url%/}/gateway/status")"; then
  echo "Coder Gateway not reachable at ${url} (or the key was rejected): ${body:-no response}" >&2
  exit 1
fi

if [[ "${1:-}" == "--json" ]]; then
  printf '%s\n' "$body"
  exit 0
fi

printf '%s' "$body" | python3 "$(dirname "$0")/format_status.py"
