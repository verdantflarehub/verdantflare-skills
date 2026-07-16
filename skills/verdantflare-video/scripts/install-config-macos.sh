#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ "$(uname -s)" != "Darwin" ]]; then
  printf '%s\n' 'This installer supports macOS only.' >&2
  exit 1
fi

if ! command -v python3 >/dev/null 2>&1; then
  printf '%s\n' 'python3 >= 3.10 is required. Install it and run this command again.' >&2
  exit 1
fi

if ! python3 -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)'; then
  printf '%s\n' 'python3 >= 3.10 is required. Install it and run this command again.' >&2
  exit 1
fi

exec python3 "$SCRIPT_DIR/install-config.py"
