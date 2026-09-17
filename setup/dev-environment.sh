#!/usr/bin/env bash
#
# CloudLab node setup for the decoupled-FILL/RX DPDK experiment.
# Target: sm110p (Xeon Silver 4314, single NUMA, ConnectX-6 DX 100Gb), Ubuntu 22.04.
#
# Usage (run manually on each node after SSH'ing in, once per node):
#   sudo ./setup.sh dut
#   sudo ./setup.sh tgen
#
# Safe to re-run: heavy steps (clone, build) are skipped if already done.

set -euo pipefail

# --- Role argument -----------------------------------------------------------

ROLE="${1:-}"
if [[ "$ROLE" != "dut" && "$ROLE" != "tgen" ]]; then
    echo "Usage: sudo $0 <dut|tgen>" >&2
    exit 1
fi

if [[ "$EUID" -ne 0 ]]; then
    echo "Must run as root (sudo $0 $ROLE)" >&2
    exit 1
fi

SCRATCH=/mydata
REPO_DIR="$SCRATCH/dpdk-research"
VANILLA_DIR="$REPO_DIR/dpdk-vanilla"
SHRING_DIR="$REPO_DIR/shring-dpdk"
RXBISECT_DIR="$REPO_DIR/rxbisect"
RESULTS_DIR="$REPO_DIR/results"
PCM_DIR="$REPO_DIR/pcm"
HUGEPAGE_COUNT=4096   # 4096 x 2MB = 8GB

# Comparison baselines (shRing, rxBisect) are opt-in. For the initial development
# phase, only vanilla DPDK is needed to build the solution against before
# comparing to the others (CLAUDE.md Phase 0 comparison comes later).
# Re-run with INSTALL_COMPARISON_DPDKS=true ./setup.sh <role> once ready for that.
INSTALL_COMPARISON_DPDKS="${INSTALL_COMPARISON_DPDKS:-false}"

# Claude Code is installed for the human login user (not root), since it stores
# auth/config under that user's home and is meant to be run interactively.
TARGET_USER="${SUDO_USER:-root}"
TARGET_HOME="$(getent passwd "$TARGET_USER" | cut -d: -f6)"

log()     { echo -e "\n[setup] $*"; }
section() { echo -e "\n==== $* ====\n"; }

# --- 1. Base packages ---------------------------------------------------------

section "Installing base packages (role: $ROLE)"

export DEBIAN_FRONTEND=noninteractive
apt-get update -qq

apt-get install -y -qq \
    build-essential git cmake ninja-build meson pkg-config \
    python3-pip python3-pyelftools \
    "linux-headers-$(uname -r)" libnuma-dev numactl \
    rdma-core libibverbs-dev librdmacm-dev ibverbs-utils libmlx5-1 \
    ethtool pciutils msr-tools \
    linux-tools-common "linux-tools-$(uname -r)" linux-tools-generic

log "Base packages installed."

# --- 2. Sanity checks: mlx5/rdma-core, NUMA -----------------------------------

section "Sanity checks"

log "PCI devices matching Mellanox:"
lspci | grep -i mellanox || echo "  WARNING: no Mellanox device found via lspci."

log "rdma-core view of the device (ibv_devinfo):"
ibv_devinfo || echo "  WARNING: ibv_devinfo failed — rdma-core may not see the mlx5 port yet."

log "NUMA topology (expect a single node on sm110p):"
lscpu | grep -E "NUMA node|CPU\(s\)"

# NOTE: mlx5 is a bifurcated driver. Unlike ixgbe/i40e, the port stays bound to
# the kernel's mlx5_core driver — there is deliberately no dpdk-devbind.py /
# vfio-pci / igb_uio step here. DPDK talks to the NIC through rdma-core/verbs
# while the kernel driver stays attached.

log "Experiment-NIC candidate interfaces (Mellanox-backed netdevs):"
for iface in /sys/class/net/*; do
    name="$(basename "$iface")"
    if [[ -e "$iface/device/vendor" ]] && grep -qi "0x15b3" "$iface/device/vendor" 2>/dev/null; then
        echo "  $name"
    fi
done
echo "  (Confirm which of these is the experiment link, not the control-plane NIC.)"

# --- 3. Scratch layout + repo clones ------------------------------------------

section "Setting up $REPO_DIR"

if [[ ! -d "$SCRATCH" ]]; then
    echo "ERROR: $SCRATCH not found — is the Blockstore mounted?" >&2
    exit 1
fi

mkdir -p "$VANILLA_DIR" "$SHRING_DIR" "$RXBISECT_DIR" "$RESULTS_DIR"

clone_if_missing() {
    local url="$1" dir="$2"
    if [[ -d "$dir/.git" ]]; then
        log "Already cloned: $dir (skipping)"
    else
        log "Cloning $url into $dir"
        git clone "$url" "$dir"
    fi
}

clone_if_missing "https://github.com/DPDK/dpdk.git" "$VANILLA_DIR"

if [[ "$INSTALL_COMPARISON_DPDKS" == "true" ]]; then
    clone_if_missing "https://github.com/BorisPis/shRing-dpdk.git" "$SHRING_DIR"
    if [[ ! -d "$RXBISECT_DIR/.git" ]]; then
        log "TODO: rxBisect has no known public repo URL yet (CLAUDE.md §9 open question)."
        log "      Created empty $RXBISECT_DIR — clone it here manually once the source is located."
    fi
else
    log "Skipping shRing/rxBisect clone (INSTALL_COMPARISON_DPDKS=false)."
    log "Only vanilla DPDK is set up for now — develop your solution against it first,"
    log "then re-run with INSTALL_COMPARISON_DPDKS=true $0 $ROLE to add the comparison baselines."
fi

# --- 4. Hugepages (runtime only, no reboot) -----------------------------------

section "Configuring hugepages"

python3 "$VANILLA_DIR/usertools/dpdk-hugepages.py" -p 2M --setup "${HUGEPAGE_COUNT}"
python3 "$VANILLA_DIR/usertools/dpdk-hugepages.py" -s

log "Reserved ${HUGEPAGE_COUNT} x 2MB hugepages (runtime-only; lost on reboot)."
log "For persistent 1GB hugepages instead, add to /etc/default/grub's GRUB_CMDLINE_LINUX"
log "and run update-grub + reboot yourself (not automated here):"
log "  default_hugepagesz=1G hugepagesz=1G hugepages=8"

# --- 5. Build DPDK trees -------------------------------------------------------

build_dpdk_tree() {
    local dir="$1" label="$2"
    section "Building $label ($dir)"
    if [[ -x "$dir/build/app/dpdk-testpmd" ]]; then
        log "$label already built (skipping). Delete $dir/build to force a rebuild."
        return
    fi
    ( cd "$dir" && meson setup build -Dexamples=l3fwd && ninja -C build )
    log "$label build complete: $dir/build"
}

build_dpdk_tree "$VANILLA_DIR" "vanilla DPDK"

if [[ "$INSTALL_COMPARISON_DPDKS" == "true" ]]; then
    build_dpdk_tree "$SHRING_DIR" "shRing-dpdk"
    if [[ -d "$RXBISECT_DIR/.git" ]]; then
        build_dpdk_tree "$RXBISECT_DIR" "rxBisect"
    fi
fi

# --- 6. Role-specific: Intel PCM on dut only -----------------------------------

if [[ "$ROLE" == "dut" ]]; then
    section "Building Intel PCM (LLC / memory-bandwidth counters)"
    if [[ -x "$PCM_DIR/build/bin/pcm" ]]; then
        log "PCM already built (skipping)."
    else
        clone_if_missing "https://github.com/intel/pcm.git" "$PCM_DIR"
        ( cd "$PCM_DIR" && mkdir -p build && cd build && cmake .. && cmake --build . --parallel "$(nproc)" )
        log "PCM build complete: $PCM_DIR/build"
    fi
    log "PCM needs MSR access: this script installed msr-tools and loaded msr below."
    modprobe msr || log "WARNING: could not load msr module — PCM may need it at runtime."
else
    log "Role tgen: skipping PCM. Traffic will be generated with dpdk-testpmd (built above)."
fi

# --- 6b. Claude Code CLI (dut only — this is where the DPDK code gets written) -

if [[ "$ROLE" == "dut" ]]; then
    section "Installing Claude Code for $TARGET_USER"

    if sudo -u "$TARGET_USER" -H bash -lc 'command -v claude' >/dev/null 2>&1; then
        log "Claude Code already installed for $TARGET_USER (skipping)."
    else
        sudo -u "$TARGET_USER" -H bash -lc 'curl -fsSL https://claude.ai/install.sh | bash'
        log "Claude Code installed for $TARGET_USER (home: $TARGET_HOME)."
        log "Open a new shell (or 'source ~/.bashrc') so the updated PATH takes effect, then run 'claude'."
    fi
else
    log "Role tgen: skipping Claude Code install — development happens on dut."
fi

# --- 7. CPU tuning (non-destructive, no reboot) --------------------------------

section "CPU frequency governor"

if command -v cpupower >/dev/null; then
    cpupower frequency-set -g performance >/dev/null && log "CPU governor set to performance."
else
    log "WARNING: cpupower not available; skipping governor change."
fi

log "For further tuning (isolcpus, IRQ affinity for the experiment NIC), see CLAUDE.md §9/§10;"
log "those typically require a reboot or node-wide IRQ changes and are left to you deliberately."

# --- 8. Summary -----------------------------------------------------------------

section "Setup summary (role: $ROLE)"

echo "Hugepages:"
python3 "$VANILLA_DIR/usertools/dpdk-hugepages.py" -s

echo
echo "rdma-core device:"
ibv_devinfo -l || true

echo
echo "NUMA topology:"
numactl --hardware | head -n 3

echo
echo "Built trees:"
TREES=("$VANILLA_DIR")
if [[ "$INSTALL_COMPARISON_DPDKS" == "true" ]]; then
    TREES+=("$SHRING_DIR" "$RXBISECT_DIR")
fi
for d in "${TREES[@]}"; do
    if [[ -x "$d/build/app/dpdk-testpmd" ]]; then
        echo "  OK    $d"
    else
        echo "  MISSING  $d"
    fi
done
if [[ "$INSTALL_COMPARISON_DPDKS" != "true" ]]; then
    echo "  (shRing/rxBisect skipped — set INSTALL_COMPARISON_DPDKS=true to add them later)"
fi

if [[ "$ROLE" == "dut" ]]; then
    if [[ -x "$PCM_DIR/build/bin/pcm" ]]; then
        echo "  OK    $PCM_DIR (PCM)"
    else
        echo "  MISSING  $PCM_DIR (PCM)"
    fi

    if sudo -u "$TARGET_USER" -H bash -lc 'command -v claude' >/dev/null 2>&1; then
        echo "  OK    claude CLI (user: $TARGET_USER)"
    else
        echo "  MISSING  claude CLI (user: $TARGET_USER)"
    fi
fi

log "Done. Next: confirm the experiment-NIC interface name above, then continue with"
log "CLAUDE.md Phase 0 (baseline l3fwd runs on vanilla DPDK / shRing)."
