#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INPUT_DIR="$SCRIPT_DIR/scenarios/real"
OUTPUT_DIR="$INPUT_DIR/output"
mkdir -p "$OUTPUT_DIR"

ok=0
fail=0

for manifest in "$INPUT_DIR"/*.yaml; do
    name="$(basename "$manifest")"
    out="$OUTPUT_DIR/patched_$name"
    echo ">>> $name"
    if python "$SCRIPT_DIR/analyze.py" "$manifest" > "$out"; then
        echo "    saved → $out"
        ok=$((ok + 1))
    else
        echo "    FAILED (exit $?)"
        fail=$((fail + 1))
    fi
done

echo ""
echo "Done: $ok ok, $fail failed"
