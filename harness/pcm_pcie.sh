#!/usr/bin/env bash
#
# Record PCIe and DDIO counters (pcm-pcie -e) on the dut, one sample per second,
# while a system runs: inbound writes that hit the LLC (ItoMCacheNear) against
# all inbound full-line writes (ItoM), the DDIO view of CLAUDE.md 9.1 and 11.
# pcm-pcie and pcm-memory both program the uncore, so a run records one or the
# other (PCM_TOOL=pcie in run_l3fwd.sh, pcm=pcie in a sweep line).
#
# Usage (on dut):
#   sudo harness/pcm_pcie.sh <label>
#
# Writes $EXP_DIR/pcm-pcie_<label>.csv.

set -euo pipefail

LABEL="${1:-}"
PCM_PCIE="${PCM_PCIE:-/mydata/dpdk-research/pcm/build/bin/pcm-pcie}"
EXP_DIR="${EXP_DIR:-/mydata/exp}"

die() { echo "ERROR: $*" >&2; exit 1; }

[[ "$EUID" -eq 0 ]] || die "must run as root (sudo $0 $LABEL)"
[[ -n "$LABEL" ]] || die "usage: sudo $0 <label>"
[[ -x "$PCM_PCIE" ]] || die "$PCM_PCIE not found; run setup/dev-environment.sh dut first"

mkdir -p "$EXP_DIR"
CSV="$EXP_DIR/pcm-pcie_$LABEL.csv"
[[ -e "$CSV" ]] && die "$CSV exists; move it away or pick another label"

echo "pcm-pcie: recording to $CSV — start the traffic in ~10 s, Ctrl-C when it ends"
# 1 s samples, extended events (-e). Pinned to the first
# housekeeping CPU (SHUMEM_HK_CPU from run_l3fwd.sh, setup/topology.py; CPU 0
# with one port), so PCM never preempts a polling lcore.
# exec: the caller's PID is PCM itself, so a signal to it reaches PCM.
exec taskset -c "${SHUMEM_HK_CPU:-0}" "$PCM_PCIE" -e -csv="$CSV" 1 >/dev/null
