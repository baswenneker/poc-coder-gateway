#!/usr/bin/env bash
# Create a small opencode demo project that talks to the Coder Gateway.
#
# Usage: scripts/setup-demo.sh [target-dir]      (default: /tmp/coder-gateway-demo)
# Env:   CODER_GATEWAY_URL  (default http://127.0.0.1:8787)
#        CODER_GATEWAY_KEY  (default sk-gw-fwd-demo)
#
# The project contains calc.py + test_calc.py (tests pass), an opencode.json with the Gateway as
# provider, and the gateway-status skill in .opencode/skills/. It is a git repo with one commit.
set -euo pipefail

repo="$(cd "$(dirname "$0")/.." && pwd)"
target="${1:-/tmp/coder-gateway-demo}"
url="${CODER_GATEWAY_URL:-http://127.0.0.1:8787}"
key="${CODER_GATEWAY_KEY:-sk-gw-fwd-demo}"

if [[ -e "$target" ]] && [[ -n "$(ls -A "$target" 2>/dev/null)" ]]; then
  echo "Target $target exists and is not empty. Remove it first: rm -rf '$target'" >&2
  exit 1
fi
mkdir -p "$target/.opencode/skills"
cd "$target"

cat > calc.py <<'PY'
"""Tiny calculator module for the Coder Gateway demo."""


def add(a: int, b: int) -> int:
    return a + b


def subtract(a: int, b: int) -> int:
    return a - b
PY

cat > test_calc.py <<'PY'
from calc import add, subtract


def test_add() -> None:
    assert add(2, 3) == 5


def test_subtract() -> None:
    assert subtract(5, 3) == 2
PY

cat > opencode.json <<JSON
{
  "\$schema": "https://opencode.ai/config.json",
  "provider": {
    "gw": {
      "npm": "@ai-sdk/openai-compatible",
      "name": "Coder Gateway",
      "options": { "baseURL": "${url%/}/v1", "apiKey": "${key}" },
      "models": { "fwd-coder": { "name": "fwd-coder" } }
    }
  },
  "model": "gw/fwd-coder",
  "small_model": "gw/fwd-coder"
}
JSON

cp -R "$repo/skills/gateway-status" .opencode/skills/
printf '__pycache__/\n.pytest_cache/\n' > .gitignore

git init -q
git add -A
git -c user.name="Coder Gateway demo" -c user.email="demo@example.invalid" commit -qm "Demo project"

echo "Demo project ready in $target"
echo "  Gateway: ${url}  (key ${key})"
echo "  Start:   cd '$target' && opencode"
