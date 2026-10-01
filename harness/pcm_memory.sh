#!/usr/bin/env bash
#
# Record DRAM bandwidth on the dut, one sample per second, while a system runs.
# Start it ~10 s before the traffic so the CSV also holds an idle baseline, and
# stop it with Ctrl-C after the traffic ends (CLAUDE.md 17.4).
#
# Usage (on dut, in its own terminal):
#   sudo harness/pcm_memory.sh <label>      # e.g. privring-1024
#
# Writes $EXP_DIR/pcm-mem_<label>.csv; harness/pcm_summary.sh reads it.

set -euo pipefail

LABEL="${1:-}"
PCM_MEMORY="${PCM_MEMORY:-/mydata/dpdk-research/pcm/build/bin/pcm-memory}"
EXP_DIR="${EXP_DIR:-/mydata/exp}"

die() { echo "ERROR: $*" >&2; exit 1; }

[[ "$EUID" -eq 0 ]] || die "must run as root (sudo $0 $LABEL)"
[[ -n "$LABEL" ]] || die "usage: sudo $0 <label>"
[[ -x "$PCM_MEMORY" ]] || die "$PCM_MEMORY not found; run setup/dev-environment.sh dut first"

mkdir -p "$EXP_DIR"
CSV="$EXP_DIR/pcm-mem_$LABEL.csv"
[[ -e "$CSV" ]] && die "$CSV exists; move it away or pick another label"

echo "recording to $CSV — start the traffic in ~10 s, Ctrl-C when it ends"
# 1 s samples; -nc keeps only per-socket and system totals.
"$PCM_MEMORY" 1 -nc -csv="$CSV" >/dev/null
