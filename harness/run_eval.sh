#!/usr/bin/env bash
#
# Run the final evaluation batteries (harness/sweeps/eval, CLAUDE.md 19.26) one
# after another with harness/sweep.sh, each into its own TRex CSV, stopping at
# the first that fails. Run e0 alone first and read its summary before the rest.
#
# Usage (on dut, inside tmux, as your own user):
#   TGEN_HOST=<tgen control name> TGEN_MAC=<mac> harness/run_eval.sh e0
#   TGEN_HOST=... TGEN_MAC=... harness/run_eval.sh e1 e2 e3 e4 e5 e6 e7 e8
#   TGEN_HOST=... TGEN_MAC=... harness/run_eval.sh all     # e1 to e8

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
EVAL_DIR="$SCRIPT_DIR/sweeps/eval"
EXP_DIR="${EXP_DIR:-/mydata/exp}"

[[ $# -gt 0 ]] || { sed -n '3,12p' "$0" >&2; exit 1; }
if [[ "$1" == all ]]; then
    set -- e1 e2 e3 e4 e5 e6 e7 e8
fi

for battery in "$@"; do
    plan=("$EVAL_DIR/${battery}"_*.txt)
    [[ -r "${plan[0]}" ]] || { echo "ERROR: no battery $battery in $EVAL_DIR" >&2; exit 1; }
    echo "[eval $(date +%H:%M:%S)] battery $battery: ${plan[0]##*/}"
    CSV="$EXP_DIR/eval_${battery}.csv" "$SCRIPT_DIR/sweep.sh" "${plan[0]}"
done
echo "[eval $(date +%H:%M:%S)] done: $*"
