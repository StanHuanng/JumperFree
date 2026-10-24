#!/usr/bin/env bash
# Remove only the scratch state this run created. Evidence stays under evidence/.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=common.sh
source "$SCRIPT_DIR/common.sh"

if [[ -z "${JF_VERIFY_STATE_ROOT:-}" && -z "${JF_VERIFY_RUN_ID:-}" ]]; then
  echo "CLEANUP_SKIP no JF_VERIFY_RUN_ID or JF_VERIFY_STATE_ROOT set" >&2
  exit 0
fi

# Refuse to delete paths outside /tmp or under the skill evidence tree by mistake
case "$STATE_ROOT" in
  /tmp/jumperfree-verify-*)
    if [[ -d "$STATE_ROOT" ]]; then
      rm -rf -- "$STATE_ROOT"
      echo "CLEANUP_OK removed $STATE_ROOT"
    else
      echo "CLEANUP_OK already absent $STATE_ROOT"
    fi
    ;;
  *)
    echo "CLEANUP_REFUSED refusing to delete unexpected STATE_ROOT=$STATE_ROOT" >&2
    exit 1
    ;;
esac

if [[ -d "$EVIDENCE_DIR" ]]; then
  echo "CLEANUP_OK evidence retained at $EVIDENCE_DIR"
else
  echo "CLEANUP_WARN evidence dir missing: $EVIDENCE_DIR" >&2
fi
