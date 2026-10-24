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
#   sudo harness/run_l3fwd.sh dyn-128
#
# fill-<B> is our FILL-ring path (CLAUDE.md section 18): a 1024-entry ring per
# queue of which only B buffers are kept posted. B is 1..1024.
# fill-<B>-<N> does the same in a ring of N entries (N as for privring-<N>,
# B <= N): at a fixed budget it shows what still scales with the ring.
#
# dyn-<E> is the FILL-ring path with credits (CLAUDE.md section 19): every
# queue is entitled to E buffers, their sum caps the posted total, and each
# queue holds what its backlog calls for, from a floor of 32. dyn-<E>-<F>
# sets the floor to F. The ring is 1024. When l3fwd stops, every queue prints
# a `FILL credits` line with its budget now, mean and max.
# FILL_WINDOW_MS (default 10000) and FILL_HEADROOM (percent of the peak
# backlog, default 150) set the other two knobs of the policy, for the
# loss-against-DRAM sweep (CLAUDE.md 19.19):
#   sudo FILL_WINDOW_MS=2000 FILL_HEADROOM=200 harness/run_l3fwd.sh dyn-128
# FILL_GROUPS=<G> splits the credits into G pools (fill_groups, 19.25) and
# FILL_RESERVE=<R> sets the pool reserve (fill_reserve: 64 in forks 70849f04d
# to 181be3bbf, 0 since c4df0acf6). l3fwd's own knobs pass through the
# environment: L3FWD_MEMPOOL_CACHE, L3FWD_MEMPOOL_OPS, L3FWD_MEMPOOL_GROUPS,
# and the synthetic NF and ring occupancy of fork 290de897f (l3fwd_work.h):
# L3FWD_WORK_ACCESSES, L3FWD_WORK_TARGET, L3FWD_WORK_PAYLOAD, L3FWD_WORK_MIB,
# L3FWD_OCCUPANCY_US.
# FILL_OVERLOAD_MS=<ms> (fork 7cbfa5102) makes a dyn-* queue that has not
# drained for that long keep its floor and borrow nothing (fill_overload_ms;
# default 20 since fork b96c6a87a, 0: off). FILL_RUNWAY_NS=<ns> (fork
# b96c6a87a, fill_runway_ns, default 7000, 0: off) raises a queue's floor to
# that much of its own traffic, for 64 B frames at line rate (CLAUDE.md 20.12).
#
# shringfix-8 is shring-8 with rmp_fix=1: its head advance serialised, which
# removes the race that can stop the shared ring for good (19.25).
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

# Ring a fill-<B> queue posts its budget into unless the name gives another:
# the default 1024, as privRing.
FILL_RING_SIZE=1024

# Extra EAL arguments for the selected system; empty for the baselines.
EAL_EXTRA=()

usage() {
    echo "Usage: sudo $0 <privring-<N>|shring-8|shringfix-8|fill-<B>[-<N>]|dyn-<E>[-<F>]>" >&2
    echo "       N: power of two, 64..8192;  B: 1..N (N defaults to $FILL_RING_SIZE for fill)" >&2
    echo "       E: 1..$FILL_RING_SIZE;  F: 1..E (default 32)" >&2
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
            NB_RXD=$FILL_RING_SIZE
            if [[ "$budget" == *-* ]]; then
                NB_RXD="${budget#*-}"
                budget="${budget%%-*}"
                is_ring_size "$NB_RXD" || usage
            fi
            [[ "$budget" =~ ^[0-9]+$ ]] && (( budget >= 1 && budget <= NB_RXD )) || usage
            DEVARGS="fill_en=1,fill_budget=$budget,$COMMON_DEVARGS"
            EAL_EXTRA=(--log-level=pmd.net.mlx5:info) ;;
        # Budgets that follow the load. The driver's per-queue summary at stop
        # is at INFO level too.
        dyn-*)
            local entitled="${1#dyn-}" floor=""
            if [[ "$entitled" == *-* ]]; then
                floor="${entitled#*-}"
                entitled="${entitled%%-*}"
                [[ "$floor" =~ ^[0-9]+$ ]] && (( floor >= 1 )) || usage
            fi
            [[ "$entitled" =~ ^[0-9]+$ ]] && (( entitled >= 1 && entitled <= FILL_RING_SIZE )) || usage
            [[ -z "$floor" ]] || (( floor <= entitled )) || usage
            [[ -z "${FILL_WINDOW_MS:-}" || "$FILL_WINDOW_MS" =~ ^[0-9]+$ ]] || usage
            [[ -z "${FILL_HEADROOM:-}" || "$FILL_HEADROOM" =~ ^[0-9]+$ ]] || usage
            [[ -z "${FILL_GROUPS:-}" || "$FILL_GROUPS" =~ ^[1-8]$ ]] || usage
            [[ -z "${FILL_RESERVE:-}" || "$FILL_RESERVE" =~ ^[0-9]+$ ]] || usage
            [[ -z "${FILL_OVERLOAD_MS:-}" || "$FILL_OVERLOAD_MS" =~ ^[0-9]+$ ]] || usage
            [[ -z "${FILL_RUNWAY_NS:-}" || "$FILL_RUNWAY_NS" =~ ^[0-9]+$ ]] || usage
            DEVARGS="fill_en=1,fill_budget=$entitled,fill_dyn=1${floor:+,fill_floor=$floor}"
            DEVARGS+="${FILL_WINDOW_MS:+,fill_window_ms=$FILL_WINDOW_MS}${FILL_HEADROOM:+,fill_headroom=$FILL_HEADROOM}"
            DEVARGS+="${FILL_GROUPS:+,fill_groups=$FILL_GROUPS}${FILL_RESERVE:+,fill_reserve=$FILL_RESERVE}"
            DEVARGS+="${FILL_OVERLOAD_MS:+,fill_overload_ms=$FILL_OVERLOAD_MS}"
            DEVARGS+="${FILL_RUNWAY_NS:+,fill_runway_ns=$FILL_RUNWAY_NS}"
            DEVARGS+=",$COMMON_DEVARGS"
            NB_RXD=$FILL_RING_SIZE
            EAL_EXTRA=(--log-level=pmd.net.mlx5:info) ;;
        # One RMP of nb_rxd entries shared by all NB_QUEUES queues.
        shring-8)      DEVARGS="rmp_en=1,rqs_per_rmp=$NB_QUEUES,$COMMON_DEVARGS"; NB_RXD=1024 ;;
        shringfix-8)   DEVARGS="rmp_en=1,rqs_per_rmp=$NB_QUEUES,rmp_fix=1,$COMMON_DEVARGS"; NB_RXD=1024 ;;
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
    # PCM_TOOL=pcie records PCIe/DDIO counters instead of DRAM bandwidth; the
    # two cannot share the uncore in one run.
    case "${PCM_TOOL:-memory}" in
        memory) pcm_script=pcm_memory.sh pcm_csv=pcm-mem_$PCM_LABEL.csv ;;
        pcie)   pcm_script=pcm_pcie.sh pcm_csv=pcm-pcie_$PCM_LABEL.csv ;;
        *)      die "PCM_TOOL=$PCM_TOOL: want memory or pcie" ;;
    esac
    [[ ! -e "${EXP_DIR:-/mydata/exp}/$pcm_csv" ]] \
        || die "$pcm_csv exists; pick another PCM_LABEL"
    "$SCRIPT_DIR/$pcm_script" "$PCM_LABEL" &
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
