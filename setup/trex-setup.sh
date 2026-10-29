#!/usr/bin/env bash
#
# TRex traffic-generator setup for the tgen node. Run AFTER dev-environment.sh tgen.
# See CLAUDE.md section 16 for the reasoning behind every step.
#
# Usage (on tgen, with the dut up so its MACs can be learned over the links):
#   sudo setup/trex-setup.sh              # learns the dut MAC by pinging 10.10.1.1
#   sudo setup/trex-setup.sh <dut-mac>    # or pass it explicitly
#   sudo setup/trex-setup.sh <dut-mac> <dut-mac-2>   # two links (CLAUDE.md 21)
# With two experiment links (10.10.1.x and 10.10.2.x) both ports are TRex
# ports 0 and 1 and the 'dummy' port goes; the cores come from
# setup/topology.py (latency 1, workers 2-7 with one link, as before).
#
# Safe to re-run: the rdma-core build and the TRex download are skipped when
# already done; /etc/trex_cfg.yaml is regenerated (the old one is backed up),
# because MACs change on every CloudLab re-instantiation.

set -euo pipefail

if [[ "$EUID" -ne 0 ]]; then
    echo "Must run as root (sudo $0)" >&2
    exit 1
fi

SCRATCH=/mydata

# TRex v3.07 is the release TRex documents against Ubuntu 22.04. Pinned for the
# same reason the DPDK trees are: re-instantiations must run identical code.
TREX_VERSION=v3.07
TREX_PARENT="$SCRATCH/trex"
TREX_DIR="$TREX_PARENT/$TREX_VERSION"
TREX_URL="https://trex-tgn.cisco.com/trex/release/$TREX_VERSION.tar.gz"
TREX_CFG=/etc/trex_cfg.yaml

# TRex's libmlx5-64.so needs symbol version MLX5_1.24; Ubuntu 22.04's
# rdma-core 39 stops at MLX5_1.22 and v44 is the first release to export 1.24.
RDMA_CORE_REF=v44.0
RDMA_CORE_DIR="$SCRATCH/rdma-core-${RDMA_CORE_REF%.0}"
REQUIRED_MLX5_SYMVER=MLX5_1.24

TOPOLOGY="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/topology.py"

log()     { echo -e "\n[trex-setup] $*"; }
section() { echo -e "\n==== $* ====\n"; }
die()     { echo "ERROR: $*" >&2; exit 1; }

# --- Helpers --------------------------------------------------------------------

# These helpers print nothing (instead of aborting under pipefail) when the
# thing they look for does not exist, so callers can test for an empty result.
highest_mlx5_symver() {
    { objdump -T "$1" 2>/dev/null | grep -o 'MLX5_1\.[0-9]*' | sort -uV | tail -1; } || true
}

system_libmlx5() {
    { ldconfig -p | awk '/libmlx5\.so\.1 /{print $NF}' | head -1; } || true
}

interface_with_ip() {
    { ip -o -4 addr show | awk -v ip="$1" '$4 ~ "^"ip"/" {print $2}' | head -1; } || true
}

learn_neighbor_mac() {
    local iface="$1" ip="$2"
    ping -c 2 -W 1 -I "$iface" "$ip" >/dev/null 2>&1 || true
    { ip neigh show "$ip" dev "$iface" | awk '/lladdr/ {print $3}' | head -1; } || true
}

# core_id repeats on every socket, so a core is (package, core_id).
physical_core_of() {
    local topo="/sys/devices/system/cpu/cpu$1/topology"
    echo "$(cat "$topo/physical_package_id"):$(cat "$topo/core_id")"
}

# --- 1. Build dependencies --------------------------------------------------------

section "Installing build dependencies"

export DEBIAN_FRONTEND=noninteractive
apt-get install -y -qq build-essential cmake ninja-build pkg-config git wget \
    libudev-dev libnl-3-dev libnl-route-3-dev libsystemd-dev binutils python3-pip
# rate_sweep.py decodes the HDR latency histograms TRex sends with --hdrh.
pip3 install --quiet hdrhistogram

# --- 2. rdma-core v44 in /usr/local (tgen only) ----------------------------------

section "rdma-core $RDMA_CORE_REF"

# /usr/local wins over the apt copy because /etc/ld.so.conf.d/libc.conf (which
# lists /usr/local/lib) sorts before x86_64-linux-gnu.conf. A private prefix
# would not work: t-rex-64 overwrites LD_LIBRARY_PATH with its own directory.
# Never do this on the dut: its rdma-core 39 + DPDK 21.05 stack is the one
# validated in CLAUDE.md section 15.
current_symver="$(highest_mlx5_symver "$(system_libmlx5)")"
if [[ "$(printf '%s\n%s\n' "$current_symver" "$REQUIRED_MLX5_SYMVER" | sort -V | tail -1)" == "$current_symver" ]]; then
    log "System libmlx5 already exports $current_symver (skipping build)."
else
    log "System libmlx5 stops at $current_symver; TRex needs $REQUIRED_MLX5_SYMVER."
    if [[ ! -d "$RDMA_CORE_DIR/.git" ]]; then
        git clone -b "$RDMA_CORE_REF" --depth 1 \
            https://github.com/linux-rdma/rdma-core.git "$RDMA_CORE_DIR"
    fi
    # NO_MAN_PAGES avoids a pandoc dependency; NO_PYVERBS skips Cython bindings
    # nothing here uses. The default prefix is /usr/local.
    mkdir -p "$RDMA_CORE_DIR/build"
    ( cd "$RDMA_CORE_DIR/build" && cmake -GNinja -DNO_MAN_PAGES=1 -DNO_PYVERBS=1 .. \
        && ninja && ninja install )
    ldconfig
    current_symver="$(highest_mlx5_symver "$(system_libmlx5)")"
    [[ "$current_symver" == "$REQUIRED_MLX5_SYMVER" ]] \
        || die "after install, libmlx5 exports $current_symver, expected $REQUIRED_MLX5_SYMVER"
    log "Loader now resolves libmlx5 to $(system_libmlx5) ($current_symver)."
fi

/usr/local/bin/ibv_devinfo -l 2>/dev/null \
    || log "WARNING: /usr/local/bin/ibv_devinfo not found or failed."

# --- 3. TRex --------------------------------------------------------------------

section "TRex $TREX_VERSION"

if [[ -x "$TREX_DIR/t-rex-64" ]]; then
    log "TRex already extracted at $TREX_DIR (skipping download)."
else
    mkdir -p "$TREX_PARENT"
    # --no-check-certificate: trex-tgn.cisco.com serves an incomplete chain
    # (intermediate missing). See CLAUDE.md section 16.
    wget -q --no-check-certificate -O "$TREX_PARENT/$TREX_VERSION.tar.gz" "$TREX_URL"
    tar -xzf "$TREX_PARENT/$TREX_VERSION.tar.gz" -C "$TREX_PARENT"
    log "TRex extracted at $TREX_DIR"
fi

log "TRex libmlx5 requires $(highest_mlx5_symver "$TREX_DIR/so/x86_64/libmlx5-64.so")."

# --- 4. Discover the experiment links ---------------------------------------------

section "Discovering the experiment NICs"

# Ports, PCI addresses and the core layout (setup/topology.py). Cores must be
# distinct physical cores: TRex worker threads sharing a core through
# hyperthreading give an unstable transmit rate.
layout="$(python3 "$TOPOLOGY" --role tgen shell)" || die "setup/topology.py failed (message above)"
eval "$layout"
read -r -a IFACES <<<"$SHUMEM_IFACES"
read -r -a PCIS <<<"$SHUMEM_PCIS"
(( ${#IFACES[@]} >= 1 )) || die "no interface holds 10.10.<n>.2 — is this the tgen node?"
MASTER_CORE=$SHUMEM_HK_CPU
LATENCY_CORE=$SHUMEM_TREX_LATENCY
read -r -a WORKER_CORES <<<"$SHUMEM_TREX_WORKERS"

TGEN_MACS=() DUT_MACS=()
for p in "${!IFACES[@]}"; do
    TGEN_MACS[p]="$(cat "/sys/class/net/${IFACES[p]}/address")"
    mac="${*:p+1:1}"
    if [[ -z "$mac" ]]; then
        mac="$(learn_neighbor_mac "${IFACES[p]}" "10.10.$((p + 1)).1")"
        [[ -n "$mac" ]] || die "could not learn the dut MAC via 10.10.$((p + 1)).1; pass it as argument $((p + 1))"
    fi
    DUT_MACS[p]="$mac"
    log "Port $p: interface ${IFACES[p]}, PCI ${PCIS[p]}, tgen MAC ${TGEN_MACS[p]}, dut MAC ${DUT_MACS[p]}"
done

# --- 5. Verify the core layout ----------------------------------------------------

section "Checking that TRex cores are distinct physical cores"

declare -A seen_core=()
for cpu in "$MASTER_CORE" "$LATENCY_CORE" "${WORKER_CORES[@]}"; do
    core="$(physical_core_of "$cpu")"
    [[ -z "${seen_core[$core]:-}" ]] \
        || die "CPUs ${seen_core[$core]} and $cpu are hyperthreads of core $core; edit the core layout"
    seen_core[$core]="$cpu"
done
log "OK: CPUs $MASTER_CORE $LATENCY_CORE ${WORKER_CORES[*]} are on distinct physical cores."

# --- 6. /etc/trex_cfg.yaml --------------------------------------------------------

section "Writing $TREX_CFG"

if [[ -f "$TREX_CFG" ]]; then
    cp "$TREX_CFG" "$TREX_CFG.bak"
    log "Previous config saved to $TREX_CFG.bak"
fi

worker_list="$(IFS=,; echo "${WORKER_CORES[*]}" | sed 's/,/, /g')"

# port_mtu: without it TRex asks mlx5 for MTU 65518 (derived from the PMD's
# generic max_rx_pktlen) and dev_configure fails with -22.
# One link: 'dummy', because TRex needs ports in pairs. Two links: both real
# ports, TRex port p on link p+1 (10.10.<p+1>.0/24).
# MACs rather than IP/gateway: l3fwd is pure DPDK and never answers ARP.
if (( ${#IFACES[@]} == 1 )); then
    interfaces="'${PCIS[0]#0000:}', 'dummy'"
    second_port="    - src_mac:  00:00:00:00:00:00   # dummy port, never transmits
      dest_mac: 00:00:00:00:00:00"
else
    interfaces="'${PCIS[0]#0000:}', '${PCIS[1]#0000:}'"
    second_port="    - src_mac:  ${TGEN_MACS[1]}   # tgen ${IFACES[1]}, link 2
      dest_mac: ${DUT_MACS[1]}   # dut, link 2"
fi
cat > "$TREX_CFG" <<CFG
- version: 2
  port_limit: 2
  port_mtu: 1500
  interfaces: [$interfaces]
  port_info:
    - src_mac:  ${TGEN_MACS[0]}   # tgen ${IFACES[0]}, link 1
      dest_mac: ${DUT_MACS[0]}   # dut, link 1
$second_port
  platform:
    master_thread_id: $MASTER_CORE
    latency_thread_id: $LATENCY_CORE
    dual_if:
      - socket: $SHUMEM_NUMA
        threads: [$worker_list]
CFG
cat "$TREX_CFG"

# --- 7. Summary -------------------------------------------------------------------

section "Done"

for iface in "${IFACES[@]}"; do
    echo "Pause frames on $iface (expect RX/TX off; node-setup.sh sets this at boot):"
    ethtool -a "$iface" | grep -E '^(RX|TX):'
done
echo
echo "For the dut's harness: TGEN_MAC=${TGEN_MACS[0]}${TGEN_MACS[1]:+ TGEN_MAC2=${TGEN_MACS[1]}}"
echo
echo "Start the TRex server (inside tmux; keep it running; --hdrh gives 1 us latency percentiles):"
echo "  cd $TREX_DIR && sudo ./t-rex-64 -i -c ${#WORKER_CORES[@]} --no-ofed-check --hdrh"
