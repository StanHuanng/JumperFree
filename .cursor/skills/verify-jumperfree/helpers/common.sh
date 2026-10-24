#!/usr/bin/env bash
# Shared paths and env for verify-jumperfree helpers.
set -euo pipefail

SKILL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# skill lives at REPO/.cursor/skills/verify-jumperfree
REPO_ROOT="$(cd "$SKILL_DIR/../../.." && pwd)"

EVIDENCE_ROOT="${JF_VERIFY_EVIDENCE_ROOT:-$SKILL_DIR/evidence}"
RUN_ID="${JF_VERIFY_RUN_ID:-}"
if [[ -z "$RUN_ID" ]]; then
  RUN_ID="$(date '+%Y%m%d-%H%M%S')-$$"
fi
export JF_VERIFY_RUN_ID="$RUN_ID"

STATE_ROOT="${JF_VERIFY_STATE_ROOT:-/tmp/jumperfree-verify-$RUN_ID}"
EVIDENCE_DIR="${JF_VERIFY_EVIDENCE_DIR:-$EVIDENCE_ROOT/$RUN_ID}"

export JF_VERIFY_SKILL_DIR="$SKILL_DIR"
export JF_VERIFY_REPO_ROOT="$REPO_ROOT"
export JF_VERIFY_STATE_ROOT="$STATE_ROOT"
export JF_VERIFY_EVIDENCE_DIR="$EVIDENCE_DIR"
export JF_VERIFY_EVIDENCE_ROOT="$EVIDENCE_ROOT"
export PYTHONPATH="${REPO_ROOT}${PYTHONPATH:+:$PYTHONPATH}"
export PY="${JF_VERIFY_PYTHON:-python3}"

jf() {
  "$PY" -m jumperfree "$@"
}

require_repo() {
  if [[ ! -f "$REPO_ROOT/jumperfree/cli.py" ]]; then
    echo "DOCTOR_FAIL repo root missing jumperfree/cli.py: $REPO_ROOT" >&2
    return 1
  fi
}
