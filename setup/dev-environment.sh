#!/usr/bin/env bash
#
# CloudLab node setup for the decoupled-FILL/RX DPDK experiment.
# Target: sm110p (Xeon Silver 4314, single NUMA, ConnectX-6 DX 100Gb), Ubuntu 22.04.
# DPDK 21.05 (pinned by shRing) does not build unmodified on this toolchain;
# setup/patches/ fixes that. See CLAUDE.md section 14.1.
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

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PATCH_DIR="$SCRIPT_DIR/patches"

SCRATCH=/mydata
REPO_DIR="$SCRATCH/dpdk-research"
VANILLA_DIR="$REPO_DIR/dpdk-vanilla"
SHRING_DIR="$REPO_DIR/shring-dpdk"
RESULTS_DIR="$REPO_DIR/results"
PCM_DIR="$REPO_DIR/pcm"
HUGEPAGE_COUNT=4096   # 4096 x 2MB = 8GB

# Both trees are pinned to exact commits, not branches: a re-instantiation months
# from now must get byte-identical code, or measurements stop being comparable
# with earlier ones.
#
# shRing forked cleanly from the v21.05 release commit — `git merge-base` against
# upstream DPDK lands exactly on it, with no intermediate commit. That makes
# v21.05 the only fair vanilla baseline; comparing against DPDK main would
# measure four years of mlx5 evolution rather than shRing itself.
VANILLA_REF=175af25734f295874e31b33ccd0879e69fd152a9   # tag v21.05 (2021-05-21)
SHRING_REF=c191506e337506ac4238dd2c602aa49b66720989    # branch v21.05-rmp

# The shRing tree is the primary working tree, not an optional extra: its mlx5
# changes are all gated behind the `rmp_en` devarg, so one binary yields both
# baselines — `rmp_en=0` is privRing and `rmp_en=1,rqs_per_rmp=N` is shRing. That
# removes the DPDK-version confounder from the comparison entirely.
#
# The vanilla tree is kept only to (a) validate that `rmp_en=0` really does match
# stock v21.05 and (b) diff against when reading shRing's changes. It is not an
# experiment target — note that shRing also patches examples/l3fwd, so the two
# trees' l3fwd binaries are NOT identical.
#
# rxBisect is deliberately absent: it requires NIC ASIC changes and is evaluated
# in its paper through a software emulation framework that was never published
# (no artifact, no public repo). It is a design reference for this project, not a
# measured baseline. See CLAUDE.md.

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

mkdir -p "$RESULTS_DIR"

# Clones at the pinned commit on first run. On later runs it only *reports* a
# mismatch: the shRing tree is where the implementation gets written, so moving
# HEAD or discarding the working tree automatically could destroy real work.
clone_at_ref() {
    local url="$1" dir="$2" ref="$3" label="$4" head
    if [[ ! -d "$dir/.git" ]]; then
        log "Cloning $label into $dir"
        git clone "$url" "$dir"
        ( cd "$dir" && git checkout --quiet --detach "$ref" )
        log "$label pinned at $ref"
        return
    fi
    head="$(cd "$dir" && git rev-parse HEAD)"
    if [[ "$head" == "$ref" ]]; then
        log "$label already at the pinned commit"
    else
        log "WARNING: $label is at $head but the pin is $ref."
        log "         Leaving it alone — it may hold your own work. Move it by"
        log "         hand once anything local is saved, or results from this"
        log "         node will not be comparable with earlier measurements."
    fi
}

# DPDK 21.05 predates the toolchain on the node, so it needs a small number of
# build fixes. They live as patch files rather than being edited in place, so
# that every deviation from the pinned upstream commit is auditable -- which
# matters when the thesis claims these trees are the published baselines.
apply_patches() {
    local dir="$1" label="$2" patch
    shopt -s nullglob
    for patch in "$PATCH_DIR"/*.patch; do
        if ( cd "$dir" && git apply --reverse --check "$patch" ) 2>/dev/null; then
            log "$label: already patched with $(basename "$patch")"
        else
            ( cd "$dir" && git apply "$patch" )
            log "$label: applied $(basename "$patch")"
        fi
    done
    shopt -u nullglob
}

clone_at_ref "https://github.com/BorisPis/shRing-dpdk.git" "$SHRING_DIR" \
             "$SHRING_REF" "shRing-dpdk (primary tree)"
clone_at_ref "https://github.com/DPDK/dpdk.git" "$VANILLA_DIR" \
             "$VANILLA_REF" "vanilla DPDK v21.05 (reference only)"

# Both trees are the same DPDK base, so both need the same build fixes.
apply_patches "$SHRING_DIR" "shRing-dpdk"
apply_patches "$VANILLA_DIR" "vanilla DPDK"

# --- 4. Hugepages (runtime only, no reboot) -----------------------------------

section "Configuring hugepages"

python3 "$SHRING_DIR/usertools/dpdk-hugepages.py" -p 2M --setup "$((HUGEPAGE_COUNT * 2))M"
python3 "$SHRING_DIR/usertools/dpdk-hugepages.py" -s

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

# Built in dependency order of importance: shRing is where the work happens.
# Note: do NOT try to narrow this with -Denable_drivers=net/mlx5. On 21.05 that
# option silently yields a build with no net driver and no mempool driver at all
# (verified locally: 286 targets, "net:" empty), which then fails at runtime.
build_dpdk_tree "$SHRING_DIR" "shRing-dpdk"
build_dpdk_tree "$VANILLA_DIR" "vanilla DPDK v21.05"

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
python3 "$SHRING_DIR/usertools/dpdk-hugepages.py" -s

echo
echo "rdma-core device:"
ibv_devinfo -l || true

echo
echo "NUMA topology:"
numactl --hardware | head -n 3

echo
echo "Built trees (with the commit each is pinned to):"
for d in "$SHRING_DIR" "$VANILLA_DIR"; do
    ref="$(cd "$d" 2>/dev/null && git rev-parse --short HEAD 2>/dev/null || echo '???????')"
    if [[ -x "$d/build/app/dpdk-testpmd" ]]; then
        echo "  OK       $d @ $ref"
    else
        echo "  MISSING  $d @ $ref"
    fi
done

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
log "CLAUDE.md Phase 0. Both baselines come from the shRing tree, same binary:"
log "  privRing :  -a <pci>"
log "  shRing   :  -a <pci>,rmp_en=1,rqs_per_rmp=8"
log "Before trusting either, verify that rmp_en=0 matches the vanilla v21.05 tree."
