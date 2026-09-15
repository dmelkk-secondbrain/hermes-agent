#!/usr/bin/env bash
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/../.." && pwd)
TOOL="$ROOT/tools/t041_custody.py"
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

fail_fixture() {
  local name=$1
  local body=$2
  printf '%s\n' "$body" > "$TMP/$name.json"
  if python3 "$TOOL" verify-fixture --fixture "$TMP/$name.json"; then
    printf 'expected verifier failure: %s\n' "$name" >&2
    exit 1
  fi
}

fail_fixture timeout '{"elapsed_seconds": 2, "command_exit": "timeout", "start_evidence": true, "pids": [10,11]}'
fail_fixture unchanged '{"elapsed_seconds": 2, "command_exit": 0, "start_evidence": true, "pids": [10,10]}'
fail_fixture missing-start '{"elapsed_seconds": 2, "command_exit": 0, "start_evidence": false, "pids": [10,11]}'

mkdir "$TMP/repo"
git -C "$TMP/repo" init -q
git -C "$TMP/repo" config user.email t041@example.invalid
git -C "$TMP/repo" config user.name t041
printf 'baseline\n' > "$TMP/repo/file"
git -C "$TMP/repo" add file
git -C "$TMP/repo" commit -qm baseline
HEAD=$(git -C "$TMP/repo" rev-parse HEAD)
python3 "$TOOL" rollback-guard --repo "$TMP/repo" --expected-head "$HEAD"
printf 'dirty\n' >> "$TMP/repo/file"
if python3 "$TOOL" rollback-guard --repo "$TMP/repo" --expected-head "$HEAD"; then
  echo 'expected dirty rollback refusal' >&2; exit 1
fi
git -C "$TMP/repo" checkout -q -- file
if python3 "$TOOL" rollback-guard --repo "$TMP/repo" --expected-head 0000000000000000000000000000000000000000; then
  echo 'expected wrong HEAD rollback refusal' >&2; exit 1
fi
# Restoration proof is whole-tree identity, not a selected-file diff.
[ "$(git -C "$TMP/repo" write-tree)" = "$(git -C "$TMP/repo" rev-parse "$HEAD^{tree}")" ]
echo 't041 custody tests: PASS'
