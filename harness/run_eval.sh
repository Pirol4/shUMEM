#!/usr/bin/env bash
#
# Run evaluation batteries one after another with harness/sweep.sh, each into
# its own TRex CSV (and .jsonl details), stopping at the first that fails:
# e0-e8 are the first pass (harness/sweeps/eval, CLAUDE.md 19.26), f0-f15 the
# paper-style evaluation (harness/sweeps/eval2, CLAUDE.md 20). Run the smoke
# battery (e0, f0) alone first and read its summary before the rest. A battery
# started again resumes after its last finished run (RESUME=1).
#
# Usage (on dut, inside tmux, as your own user):
#   TGEN_HOST=<tgen control name> TGEN_MAC=<mac> harness/run_eval.sh f0
#   TGEN_HOST=... TGEN_MAC=... harness/run_eval.sh f1 f4 f6 f7
#   TGEN_HOST=... TGEN_MAC=... harness/run_eval.sh all2    # f1 to f15, priority order
#   TGEN_HOST=... TGEN_MAC=... harness/run_eval.sh all     # e1 to e8

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SWEEPS_DIR="$SCRIPT_DIR/sweeps"
EXP_DIR="${EXP_DIR:-/mydata/exp}"

[[ $# -gt 0 ]] || { sed -n '3,12p' "$0" >&2; exit 1; }
if [[ "$1" == all ]]; then
    set -- e1 e2 e3 e4 e5 e6 e7 e8
elif [[ "$1" == all2 ]]; then
    # The core of the paper comparison first (CLAUDE.md 20.3).
    set -- f1 f4 f6 f7 f9 f3 f10 f8 f11 f12 f13 f5 f2 f14 f15
fi

for battery in "$@"; do
    plan=("$SWEEPS_DIR"/eval*/"${battery}"_*.txt)
    [[ -r "${plan[0]}" ]] || { echo "ERROR: no battery $battery in $SWEEPS_DIR/eval*" >&2; exit 1; }
    echo "[eval $(date +%H:%M:%S)] battery $battery: ${plan[0]##*/}"
    RESUME="${RESUME:-1}" CSV="$EXP_DIR/eval_${battery}.csv" "$SCRIPT_DIR/sweep.sh" "${plan[0]}"
done
echo "[eval $(date +%H:%M:%S)] done: $*"
