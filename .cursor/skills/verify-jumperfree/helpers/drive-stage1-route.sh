#!/usr/bin/env bash
# Drive feature: stage1-route (full-connect net routing via CLI).
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=common.sh
source "$SCRIPT_DIR/common.sh"
require_repo

[[ -f "$STATE_ROOT/.ready" ]] || {
  echo "DRIVE_FAIL run helpers/launch.sh first (missing $STATE_ROOT/.ready)" >&2
  exit 1
}

FEAT="stage1-route"
OUT="$EVIDENCE_DIR/$FEAT"
mkdir -p "$OUT"
STATE="$STATE_ROOT/stage1-routing-state.json"
LOG="$OUT/transcript.txt"
: > "$LOG"

run() {
  echo "+ $*" | tee -a "$LOG"
  set +e
  jf "$@" > "$OUT/last.stdout" 2> "$OUT/last.stderr"
  ec=$?
  set -e
  {
    echo "exit=$ec"
    echo "--- stdout ---"
    cat "$OUT/last.stdout"
    echo "--- stderr ---"
    cat "$OUT/last.stderr"
    echo
  } | tee -a "$LOG"
  return "$ec"
}

run route init --nodes 8 --seed 7 --state "$STATE"
grep -q 'ROUTE_JSON' "$OUT/last.stdout" || { echo "DRIVE_FAIL init missing ROUTE_JSON" >&2; exit 1; }
cp "$OUT/last.stdout" "$OUT/01-init.stdout"

run route create --net NET_1 --nodes 0,1,2 --state "$STATE"
grep -q '"ok": true' "$OUT/last.stdout" || grep -q '"ok":true' "$OUT/last.stdout" || {
  # canonical_json may compact; also accept ROUTE create summary
  grep -q 'ROUTE create net=NET_1' "$OUT/last.stdout" || {
    echo "DRIVE_FAIL create NET_1" >&2
    exit 1
  }
}
cp "$OUT/last.stdout" "$OUT/02-create-net1.stdout"

run route create --net NET_2 --nodes 3,4 --state "$STATE"
grep -q 'ROUTE create net=NET_2' "$OUT/last.stdout" || { echo "DRIVE_FAIL create NET_2" >&2; exit 1; }
cp "$OUT/last.stdout" "$OUT/03-create-net2.stdout"

run route query --state "$STATE"
cp "$OUT/last.stdout" "$OUT/04-query.stdout"

run route list --state "$STATE"
cp "$OUT/last.stdout" "$OUT/05-list.stdout"
grep -q 'NET_1' "$OUT/05-list.stdout" || { echo "DRIVE_FAIL list missing NET_1" >&2; exit 1; }
grep -q 'NET_2' "$OUT/05-list.stdout" || { echo "DRIVE_FAIL list missing NET_2" >&2; exit 1; }

run route delete --net NET_1 --state "$STATE"
cp "$OUT/last.stdout" "$OUT/06-delete.stdout"

run route list --state "$STATE"
cp "$OUT/last.stdout" "$OUT/07-list-after-delete.stdout"
grep -q 'NET_1' "$OUT/07-list-after-delete.stdout" && {
  echo "DRIVE_FAIL NET_1 still listed after delete" >&2
  exit 1
}
grep -q 'NET_2' "$OUT/07-list-after-delete.stdout" || {
  echo "DRIVE_FAIL NET_2 missing after deleting NET_1" >&2
  exit 1
}

# Side-effect proof: state file still exists and is valid JSON
"$PY" -c "import json,sys; json.load(open(sys.argv[1])); print('state_ok')" "$STATE" \
  | tee "$OUT/08-state-json-ok.txt"
cp "$STATE" "$OUT/final-state.json"

{
  echo "feature=$FEAT"
  echo "entry=python -m jumperfree route ..."
  echo "state=$STATE"
  echo "proof=transcript + list after delete shows NET_2 only"
  echo "status=PASS"
} > "$OUT/PROOF.txt"

echo "DRIVE_OK feature=$FEAT evidence=$OUT"
