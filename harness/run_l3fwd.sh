#!/usr/bin/env bash
#
# Start the shRing tree's l3fwd on the dut as one of the systems under test.
# Every system uses the same binary, cores and queue layout; only the mlx5
# devargs and the Rx ring size change (CLAUDE.md sections 11 and 15).
#
# Usage (on dut, inside tmux; Ctrl-C stops it and prints the final counters):
#   sudo harness/run_l3fwd.sh privring-1024
#   sudo harness/run_l3fwd.sh privring-128
#   sudo harness/run_l3fwd.sh shring-8
#   sudo harness/run_l3fwd.sh fill-128
#
# fill-<B> is our FILL-ring path (CLAUDE.md section 18): a 1024-entry ring per
# queue of which only B buffers are kept posted. B is 1..1024.
#
# privring-<N> takes any power-of-two ring size from 64 to 8192, for sweeping
# the I/O working set (CLAUDE.md 17.4). l3fwd sizes its mbuf pool from N.
#
# With PCM_LABEL set, pcm_memory.sh records DRAM bandwidth for the whole life
# of l3fwd, idle start-up included, and stops with it:
#   sudo PCM_LABEL=privring-256_r2 TGEN_MAC=<mac> harness/run_l3fwd.sh privring-256
#
# The whole output, including the final per-queue counters and the shRing
# `contention` line, is saved to $RESULTS_DIR/l3fwd_<system>_<timestamp>.log.

set -euo pipefail

SYSTEM="${1:-}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

SHRING_DIR="${SHRING_DIR:-/mydata/dpdk-research/shring-dpdk}"
RESULTS_DIR="${RESULTS_DIR:-/mydata/dpdk-research/results}"
L3FWD="$SHRING_DIR/build/examples/dpdk-l3fwd"

DUT_IP=10.10.1.1
TGEN_IP=10.10.1.2

# 8 Rx queues on 8 distinct physical cores (1-8), leaving core 0 to the OS.
# Queue q is served by lcore q+1.
CORES=1-8
NB_QUEUES=8

# Mandatory on every system, not only shRing: see CLAUDE.md section 15.1.
COMMON_DEVARGS="rx_vec_en=0,rxq_cqe_comp_en=0"

# Ring a fill-<B> queue posts its budget into: the default 1024, as privRing.
FILL_RING_SIZE=1024

# Extra EAL arguments for the selected system; empty for the baselines.
EAL_EXTRA=()

usage() {
    echo "Usage: sudo $0 <privring-<N>|shring-8|fill-<B>>" >&2
    echo "       N: power of two, 64..8192;  B: 1..$FILL_RING_SIZE" >&2
    exit 1
}

is_ring_size() {
    [[ "$1" =~ ^[0-9]+$ ]] && (( $1 >= 64 && $1 <= 8192 && ($1 & ($1 - 1)) == 0 ))
}

die() { echo "ERROR: $*" >&2; exit 1; }

# Sets DEVARGS and NB_RXD for the requested system.
select_system() {
    case "$1" in
        # privring-128 has the same I/O working set as shring-8 (8 x 128 =
        # 1 x 1024 buffers) while sharing nothing: it separates "sharing" from
        # "smaller ring".
        privring-*)
            NB_RXD="${1#privring-}"
            is_ring_size "$NB_RXD" || usage
            DEVARGS="rmp_en=0,$COMMON_DEVARGS" ;;
        # Same ring as privring-1024, same posted buffers as privring-<B>. The
        # driver reports the posted count per queue at INFO level, which is the
        # only direct evidence that the ring really runs below full.
        fill-*)
            local budget="${1#fill-}"
            [[ "$budget" =~ ^[0-9]+$ ]] && (( budget >= 1 && budget <= FILL_RING_SIZE )) || usage
            DEVARGS="fill_en=1,fill_budget=$budget,$COMMON_DEVARGS"
            NB_RXD=$FILL_RING_SIZE
            EAL_EXTRA=(--log-level=pmd.net.mlx5:info) ;;
        # One RMP of nb_rxd entries shared by all NB_QUEUES queues.
        shring-8)      DEVARGS="rmp_en=1,rqs_per_rmp=$NB_QUEUES,$COMMON_DEVARGS"; NB_RXD=1024 ;;
        *) usage ;;
    esac
}

interface_with_ip() {
    { ip -o -4 addr show | awk -v ip="$1" '$4 ~ "^"ip"/" {print $2}' | head -1; } || true
}

learn_neighbor_mac() {
    local iface="$1" ip="$2"
    ping -c 2 -W 1 -I "$iface" "$ip" >/dev/null 2>&1 || true
    { ip neigh show "$ip" dev "$iface" | awk '/lladdr/ {print $3}' | head -1; } || true
}

queue_config() {
    local q config=""
    for ((q = 0; q < NB_QUEUES; q++)); do
        config+="(0,$q,$((q + 1))),"
    done
    echo "${config%,}"
}

[[ "$EUID" -eq 0 ]] || die "must run as root (sudo $0 $SYSTEM)"
[[ -n "$SYSTEM" ]] || usage
select_system "$SYSTEM"
[[ -x "$L3FWD" ]] || die "$L3FWD not found; run setup/dev-environment.sh dut first"

IFACE="$(interface_with_ip "$DUT_IP")"
[[ -n "$IFACE" ]] || die "no interface holds $DUT_IP — is this the dut node?"
PCI="$(basename "$(readlink -f "/sys/class/net/$IFACE/device")")"

# l3fwd rewrites the destination MAC of every forwarded packet to this one.
# A stale MAC (they change on every re-instantiation) shows up as 100% loss.
TGEN_MAC="${TGEN_MAC:-$(learn_neighbor_mac "$IFACE" "$TGEN_IP")}"
# Fails while TRex holds the tgen port: its flow rules keep the ARP request
# from reaching the tgen kernel. The MAC is the src_mac in tgen's trex_cfg.yaml.
[[ -n "$TGEN_MAC" ]] || die "could not learn the tgen MAC via $TGEN_IP (TRex running?); run: sudo TGEN_MAC=<mac> $0 $SYSTEM"

mkdir -p "$RESULTS_DIR"
LOG="$RESULTS_DIR/l3fwd_${SYSTEM}_$(date +%Y%m%d-%H%M%S).log"

echo "system=$SYSTEM pci=$PCI devargs=$DEVARGS nb_rxd=$NB_RXD tgen_mac=$TGEN_MAC"
echo "log: $LOG"

if [[ -n "${PCM_LABEL:-}" ]]; then
    [[ ! -e "${EXP_DIR:-/mydata/exp}/pcm-mem_$PCM_LABEL.csv" ]] \
        || die "pcm-mem_$PCM_LABEL.csv exists; pick another PCM_LABEL"
    "$SCRIPT_DIR/pcm_memory.sh" "$PCM_LABEL" &
    PCM_PID=$!
    # Ctrl-C normally reaches PCM directly (it installs its own SIGINT
    # handler); TERM covers every other way this script can end.
    trap 'kill -TERM "$PCM_PID" 2>/dev/null || true; wait "$PCM_PID" 2>/dev/null || true' EXIT
fi

# tee -i: Ctrl-C reaches the whole pipeline. Plain tee dies on it at once,
# while l3fwd only prints its final counters after handling the signal, so
# without -i the log loses exactly the lines that matter.
"$L3FWD" -l "$CORES" -n 4 -a "$PCI,$DEVARGS" "${EAL_EXTRA[@]}" -- \
    -p 0x1 \
    --config="$(queue_config)" \
    --eth-dest="0,$TGEN_MAC" \
    --nb-rxd="$NB_RXD" \
    2>&1 | tee -i "$LOG"
