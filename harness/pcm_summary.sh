#!/usr/bin/env bash
#
# Summarise the pcm_memory.sh CSVs: mean DRAM read and write over the seconds
# that carried traffic. A sample counts as loaded when its write bandwidth is
# above LOADED_MB_S; idle on sm110p is ~2 MB/s, so 20 sits clear of the noise
# without assuming how much a given system writes (CLAUDE.md 17.4).
#
# Usage (on dut):
#   harness/pcm_summary.sh                  # every $EXP_DIR/pcm-mem_*.csv
#   harness/pcm_summary.sh privring-1024 shring-8

set -euo pipefail

EXP_DIR="${EXP_DIR:-/mydata/exp}"
LOADED_MB_S="${LOADED_MB_S:-20}"

# Columns 12 and 13 are the System Read and Write totals of pcm-memory -nc -csv.
summarize() {
    awk -F, -v label="$1" -v loaded="$LOADED_MB_S" '
        NR > 2 && $13 > loaded {
            n++; read += $12; write += $13
            if (min == "" || $13 < min) min = $13
            if ($13 > max) max = $13
        }
        END {
            if (n == 0) { printf "%-16s no loaded samples\n", label; exit }
            printf "%-16s n=%3d  read=%8.1f  write=%8.1f  (write min=%.1f max=%.1f) MB/s\n",
                   label, n, read / n, write / n, min, max
        }' "$2"
}

if (( $# )); then
    labels=("$@")
else
    labels=()
    for f in "$EXP_DIR"/pcm-mem_*.csv; do
        [[ -e "$f" ]] || continue
        f="${f##*/pcm-mem_}"; labels+=("${f%.csv}")
    done
fi

for label in "${labels[@]}"; do
    csv="$EXP_DIR/pcm-mem_$label.csv"
    [[ -e "$csv" ]] || { echo "$label: $csv not found" >&2; continue; }
    summarize "$label" "$csv"
done
