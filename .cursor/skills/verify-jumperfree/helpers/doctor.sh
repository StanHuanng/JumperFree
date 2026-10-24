#!/usr/bin/env bash
# Read-only health check: is this checkout worth driving?
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=common.sh
source "$SCRIPT_DIR/common.sh"
require_repo

fail() { echo "DOCTOR_FAIL $*" >&2; exit 1; }
ok() { echo "DOCTOR_OK $*"; }

# Python version
ver="$($PY -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
maj="${ver%%.*}"
min="${ver#*.}"
if (( maj < 3 || (maj == 3 && min < 11) )); then
  fail "python $ver < 3.11"
fi
ok "python=$ver"

# Package import
mod="$($PY -c 'import jumperfree; print(jumperfree.__file__)')"
[[ -n "$mod" ]] || fail "cannot import jumperfree"
ok "import=$mod"

# CLI entry responds
help_out="$($PY -m jumperfree --help 2>&1)" || fail "jumperfree --help failed"
echo "$help_out" | grep -q 'route' || fail "help missing route"
echo "$help_out" | grep -q 'host' || fail "help missing host"
ok "cli_help"

# Optional: state root from a prior launch
if [[ -d "$STATE_ROOT" ]]; then
  [[ -f "$STATE_ROOT/.ready" ]] || fail "state root exists but not marked ready: $STATE_ROOT"
  [[ -w "$STATE_ROOT" ]] || fail "state root not writable: $STATE_ROOT"
  ok "state_root=$STATE_ROOT"
else
  ok "state_root=absent (run launch.sh first for drives that need isolation)"
fi

# Quick unit-test smoke is intentionally NOT here (too slow). Use Drive / CI for that.
ok "doctor_complete"
