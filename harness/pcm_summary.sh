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

# The System Read and Write totals of pcm-memory -nc -csv, found by name in
# the two header rows: columns 12 and 13 with one socket, 17 and 18 with two
# (CLAUDE.md 21.5).
summarize() {
    awk -F, -v label="$1" -v loaded="$LOADED_MB_S" '
        NR == 1 { for (i = 1; i <= NF; i++) skt[i] = $i; next }
        NR == 2 {
            for (i = 1; i <= NF; i++) {
                m = $i; gsub(/^ +| +$/, "", m)
                if (skt[i] == "System" && m == "Read") rc = i
                if (skt[i] == "System" && m == "Write") wc = i
            }
            if (!rc || !wc) { printf "%-16s no System Read/Write columns\n", label; bad = 1; exit }
            next
        }
        $wc > loaded {
            n++; read += $rc; write += $wc
            if (min == "" || $wc < min) min = $wc
            if ($wc > max) max = $wc
        }
        END {
            if (bad) exit
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
