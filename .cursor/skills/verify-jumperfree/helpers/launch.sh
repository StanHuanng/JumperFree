#!/usr/bin/env bash
# Prepare an isolated verification workspace (no long-lived server).
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=common.sh
source "$SCRIPT_DIR/common.sh"
require_repo

mkdir -p "$STATE_ROOT" "$EVIDENCE_DIR"
META="$EVIDENCE_DIR/launch-meta.txt"
{
  echo "run_id=$RUN_ID"
  echo "repo=$REPO_ROOT"
  echo "state_root=$STATE_ROOT"
  echo "evidence_dir=$EVIDENCE_DIR"
  echo "python=$($PY -c 'import sys; print(sys.version.split()[0])')"
  echo "git_head=$(git -C "$REPO_ROOT" rev-parse --short HEAD 2>/dev/null || echo unknown)"
  echo "launched_at=$(date -Iseconds)"
} > "$META"

# Ready = package importable and state dir writable
"$PY" -c "import jumperfree; print(jumperfree.__file__)" > "$EVIDENCE_DIR/import-path.txt"
printf 'ready\n' > "$STATE_ROOT/.ready"

echo "LAUNCH_OK run_id=$RUN_ID"
echo "STATE_ROOT=$STATE_ROOT"
echo "EVIDENCE_DIR=$EVIDENCE_DIR"
echo "To reuse this run in later shells:"
echo "  export JF_VERIFY_RUN_ID=$RUN_ID"
echo "  export JF_VERIFY_STATE_ROOT=$STATE_ROOT"
echo "  export JF_VERIFY_EVIDENCE_DIR=$EVIDENCE_DIR"
