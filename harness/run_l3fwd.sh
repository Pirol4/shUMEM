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
# b96c6a87a, fill_runway_ns, default 12000 since 5f3a20100, 0: off) raises a
# queue's floor to that much of its own traffic, for 64 B frames at line rate
# (CLAUDE.md 20.12, 20.15).
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
# PORTS=2 runs both experiment ports (two links, CLAUDE.md 21): QUEUES queues
# on each (default: 8 when the NIC's socket has the cores, setup/topology.py),
# every system per port (shRing: one RMP per port; dyn: one credit pool per
# port), the tgen MACs as TGEN_MAC (link 1) and TGEN_MAC2 (link 2):
#   sudo PORTS=2 TGEN_MAC=<mac> TGEN_MAC2=<mac> harness/run_l3fwd.sh dyn-128
# With one port nothing changes: lcores 1-8, queue q on lcore q+1.
#
# The whole output, including the final per-queue counters and the shRing
# `contention` line, is saved to $RESULTS_DIR/l3fwd_<system>_<timestamp>.log.

set -euo pipefail

SYSTEM="${1:-}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

SHRING_DIR="${SHRING_DIR:-/mydata/dpdk-research/shring-dpdk}"
RESULTS_DIR="${RESULTS_DIR:-/mydata/dpdk-research/results}"
L3FWD="$SHRING_DIR/build/examples/dpdk-l3fwd"

TOPOLOGY="$SCRIPT_DIR/../setup/topology.py"
PORTS="${PORTS:-1}"

# Rx queues on distinct physical cores of the NIC's socket (setup/topology.py):
# one port, 8 queues on lcores 1-8 with CPU 0 left to the OS; two ports,
# QUEUES each, port p's queue q on lcore number p * QUEUES + q of the list.
layout="$(python3 "$TOPOLOGY" --role dut --ports "$PORTS" ${QUEUES:+--queues "$QUEUES"} shell)" \
    || { echo "ERROR: setup/topology.py failed (message above)" >&2; exit 1; }
eval "$layout"
export SHUMEM_HK_CPU          # pcm_memory.sh and pcm_pcie.sh run there
read -r -a LCORES <<<"$SHUMEM_LCORE_LIST"
read -r -a LINK_IFACES <<<"$SHUMEM_IFACES"
read -r -a LINK_PCIS <<<"$SHUMEM_PCIS"
NB_QUEUES=$SHUMEM_QUEUES

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

learn_neighbor_mac() {
    local iface="$1" ip="$2"
    ping -c 2 -W 1 -I "$iface" "$ip" >/dev/null 2>&1 || true
    { ip neigh show "$ip" dev "$iface" | awk '/lladdr/ {print $3}' | head -1; } || true
}

queue_config() {
    local p q config=""
    for ((p = 0; p < PORTS; p++)); do
        for ((q = 0; q < NB_QUEUES; q++)); do
            config+="($p,$q,${LCORES[p * NB_QUEUES + q]}),"
        done
    done
    echo "${config%,}"
}

[[ "$EUID" -eq 0 ]] || die "must run as root (sudo $0 $SYSTEM)"
[[ -n "$SYSTEM" ]] || usage
select_system "$SYSTEM"
[[ -x "$L3FWD" ]] || die "$L3FWD not found; run setup/dev-environment.sh dut first"

(( ${#LINK_IFACES[@]} >= PORTS )) || die "no interface holds 10.10.1.1 — is this the dut node?"

# l3fwd rewrites the destination MAC of every forwarded packet to the tgen's
# on the same link. A stale MAC (they change on every re-instantiation) shows
# up as 100% loss. Learning it fails while TRex holds the tgen port (its flow
# rules keep the ARP request from the tgen kernel): then pass the src_mac of
# tgen's trex_cfg.yaml, TGEN_MAC for link 1 and TGEN_MAC2 for link 2.
TGEN_MACS=("${TGEN_MAC:-}" "${TGEN_MAC2:-}")
for ((link = 0; link < PORTS; link++)); do
    [[ -n "${TGEN_MACS[link]}" ]] \
        || TGEN_MACS[link]="$(learn_neighbor_mac "${LINK_IFACES[link]}" "10.10.$((link + 1)).2")"
    [[ -n "${TGEN_MACS[link]}" ]] || die "could not learn the tgen MAC on link $((link + 1)) via 10.10.$((link + 1)).2 (TRex running?); set $( ((link)) && echo TGEN_MAC2 || echo TGEN_MAC) as in tgen's trex_cfg.yaml"
done

# DPDK numbers the ports in PCI order, which need not be link order. Port k
# gets its PCI's link's MAC, so whatever the order, each port sends to the
# tgen port it is cabled to. When the order is reversed, TRex's link-1 traffic
# (to 198.18.0.1, l3fwd's route to port 0) leaves on link 2: per-queue counts
# and totals are unaffected, TRex's per-port counters swap.
mapfile -t DPDK_PCIS < <(printf '%s\n' "${LINK_PCIS[@]:0:PORTS}" | sort)
EAL_PORTS=() ETH_DEST=() PCI_LIST=()
for ((k = 0; k < PORTS; k++)); do
    for ((link = 0; link < PORTS; link++)); do
        [[ "${LINK_PCIS[link]}" == "${DPDK_PCIS[k]}" ]] || continue
        EAL_PORTS+=(-a "${DPDK_PCIS[k]},$DEVARGS")
        ETH_DEST+=(--eth-dest="$k,${TGEN_MACS[link]}")
        PCI_LIST+=("${DPDK_PCIS[k]}(link $((link + 1)))")
    done
done
PORT_MASK=$(printf '0x%x' $(( (1 << PORTS) - 1 )))

mkdir -p "$RESULTS_DIR"
LOG="$RESULTS_DIR/l3fwd_${SYSTEM}_$(date +%Y%m%d-%H%M%S).log"

echo "system=$SYSTEM ports=$PORTS queues=$NB_QUEUES lcores=$SHUMEM_LCORES numa=$SHUMEM_NUMA" \
     "pci=${PCI_LIST[*]} devargs=$DEVARGS nb_rxd=$NB_RXD tgen_mac=${TGEN_MACS[*]:0:PORTS}"
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
"$L3FWD" -l "$SHUMEM_LCORES" -n 4 "${EAL_PORTS[@]}" "${EAL_EXTRA[@]}" -- \
    -p "$PORT_MASK" \
    --config="$(queue_config)" \
    "${ETH_DEST[@]}" \
    --nb-rxd="$NB_RXD" \
    2>&1 | tee -i "$LOG"
