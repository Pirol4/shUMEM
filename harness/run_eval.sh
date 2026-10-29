#!/usr/bin/env bash
#
# Run evaluation batteries one after another with harness/sweep.sh, each into
# its own TRex CSV (and .jsonl details), stopping at the first that fails:
# e0-e8 are the first pass (harness/sweeps/eval, CLAUDE.md 19.26), f0-f15 the
# paper-style evaluation (harness/sweeps/eval2, CLAUDE.md 20), g0-g7 the same
# at 2 x 100 Gb/s on two ports (harness/sweeps/eval3, CLAUDE.md 21; PORTS=2 is
# set for them, TGEN_MAC2 must be). Run the smoke
# battery (e0, f0) alone first and read its summary before the rest. A battery
# started again resumes after its last finished run (RESUME=1). KEEP_GOING=1
# goes on to the next battery when one fails (unattended nights); run the
# same command again later to finish the failed one.
#
# Usage (on dut, inside tmux, as your own user):
#   TGEN_HOST=<tgen control name> TGEN_MAC=<mac> harness/run_eval.sh f0
#   TGEN_HOST=... TGEN_MAC=... harness/run_eval.sh f1 f4 f6 f7
#   TGEN_HOST=... TGEN_MAC=... harness/run_eval.sh all2    # f1 to f15, priority order
#   TGEN_HOST=... TGEN_MAC=... harness/run_eval.sh all     # e1 to e8
#   TGEN_HOST=... TGEN_MAC=... harness/run_eval.sh consol  # f23, then the c batteries (20.20)
#   TGEN_HOST=... TGEN_MAC=... TGEN_MAC2=... harness/run_eval.sh g0     # 200 Gb/s smoke
#   TGEN_HOST=... TGEN_MAC=... TGEN_MAC2=... harness/run_eval.sh all3   # g1 to g7

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SWEEPS_DIR="$SCRIPT_DIR/sweeps"
EXP_DIR="${EXP_DIR:-/mydata/exp}"

[[ $# -gt 0 ]] || { sed -n '3,22p' "$0" >&2; exit 1; }
if [[ "$1" == all ]]; then
    set -- e1 e2 e3 e4 e5 e6 e7 e8
elif [[ "$1" == all2 ]]; then
    # The core of the paper comparison first (CLAUDE.md 20.3).
    set -- f1 f4 f6 f7 f9 f3 f10 f8 f11 f12 f13 f5 f2 f14 f15
elif [[ "$1" == consol ]]; then
    # The first dyn's batteries again at the final defaults (CLAUDE.md 20.20),
    # after a drift check; make_batteries.py's CONSOLIDATE order.
    set -- f23 c1 c4 c6 c7 c5 c17 c2 c3 c9 c10 c8 c11 c12 c13 c15 c14
elif [[ "$1" == all3 ]]; then
    # 200 Gb/s (CLAUDE.md 21): the comparison's core first.
    set -- g1 g4 g5 g3 g6 g2 g7
fi

failed=()
for battery in "$@"; do
    plan=("$SWEEPS_DIR"/eval*/"${battery}"_*.txt)
    [[ -r "${plan[0]}" ]] || { echo "ERROR: no battery $battery in $SWEEPS_DIR/eval*" >&2; exit 1; }
    echo "[eval $(date +%H:%M:%S)] battery $battery: ${plan[0]##*/}"
    ports="${PORTS:-1}"
    [[ "$battery" != g* ]] || ports="${PORTS:-2}"
    if ! PORTS="$ports" RESUME="${RESUME:-1}" CSV="$EXP_DIR/eval_${battery}.csv" "$SCRIPT_DIR/sweep.sh" "${plan[0]}"; then
        echo "[eval $(date +%H:%M:%S)] battery $battery FAILED"
        [[ "${KEEP_GOING:-0}" == 1 ]] || exit 1
        failed+=("$battery")
    fi
done
echo "[eval $(date +%H:%M:%S)] done: $*${failed[*]:+ (failed: ${failed[*]})}"
