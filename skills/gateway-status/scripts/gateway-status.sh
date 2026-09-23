#!/usr/bin/env bash
# Print the Coder Gateway status (read API) for the most recently active conversation.
# Env: CODER_GATEWAY_URL, CODER_GATEWAY_KEY. Unset, they fall back to the `gw` provider in the
# nearest opencode.json (walking up from the current directory), then to
# http://127.0.0.1:8787 / sk-gw-fwd-demo.
# Usage: gateway-status.sh [--json|--print-config]
set -euo pipefail

# Look for the nearest opencode.json (walking up from cwd) and print the Gateway's baseURL and
# apiKey from its first provider that has both, one per line. Nothing printed, nothing found.
_config_from_opencode_json() {
  local dir="$PWD"
  while true; do
    if [[ -f "$dir/opencode.json" ]]; then
      python3 - "$dir/opencode.json" <<'PY'
import json
import sys

path = sys.argv[1]
try:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
except (OSError, ValueError):
    sys.exit(0)
for provider in (data.get("provider") or {}).values():
    if not isinstance(provider, dict):
        continue
    options = provider.get("options") or {}
    base_url = options.get("baseURL")
    api_key = options.get("apiKey")
    if base_url and api_key:
        # setup-demo.sh writes "<gateway-url>/v1" as baseURL; strip it back off.
        if base_url.endswith("/v1"):
            base_url = base_url[: -len("/v1")]
        print(base_url)
        print(api_key)
        break
PY
      return 0
    fi
    [[ "$dir" == "/" ]] && return 0
    dir="$(dirname "$dir")"
  done
}

found_url=""
found_key=""
if [[ -z "${CODER_GATEWAY_URL:-}" || -z "${CODER_GATEWAY_KEY:-}" ]]; then
  config_out="$(_config_from_opencode_json)"
  if [[ -n "$config_out" ]]; then
    found_url="$(sed -n '1p' <<<"$config_out")"
    found_key="$(sed -n '2p' <<<"$config_out")"
  fi
fi

url="${CODER_GATEWAY_URL:-${found_url:-http://127.0.0.1:8787}}"
key="${CODER_GATEWAY_KEY:-${found_key:-sk-gw-fwd-demo}}"

if [[ "${1:-}" == "--print-config" ]]; then
  printf 'url=%s\nkey=%s\n' "$url" "$key"
  exit 0
fi

if ! body="$(curl -sS --fail-with-body --max-time 5 -H "Authorization: Bearer ${key}" "${url%/}/gateway/status")"; then
  echo "Coder Gateway not reachable at ${url} (or the key was rejected): ${body:-no response}" >&2
  exit 1
fi

if [[ "${1:-}" == "--json" ]]; then
  printf '%s\n' "$body"
  exit 0
fi

printf '%s' "$body" | python3 "$(dirname "$0")/format_status.py"
