#!/bin/sh
# Boot-time node setup for the DPDK receive-path testbed.
#
# Invoked on every boot by the CloudLab profile (pg.Execute service), so every
# step here must be idempotent. Toolchain installation and DPDK builds are done
# by hand so that each change to the environment is deliberate.
set -eu

HUGEPAGE_MOUNT=/mnt/huge
HUGEPAGES_2M=8192          # 8192 * 2 MiB = 16 GiB reserved for DPDK mbuf pools
HUGEPAGES_2M_SYSFS=/sys/kernel/mm/hugepages/hugepages-2048kB/nr_hugepages

# --- Hugepages -------------------------------------------------------------
# The kernel can hand back fewer pages than asked when memory is fragmented,
# which only bites much later as mempool creation failures; surface it now.
echo "$HUGEPAGES_2M" | sudo tee "$HUGEPAGES_2M_SYSFS" >/dev/null
allocated=$(cat "$HUGEPAGES_2M_SYSFS")
if [ "$allocated" -lt "$HUGEPAGES_2M" ]; then
    echo "node-setup: WARNING wanted $HUGEPAGES_2M hugepages, got $allocated" >&2
fi

if ! mountpoint -q "$HUGEPAGE_MOUNT"; then
    sudo mkdir -p "$HUGEPAGE_MOUNT"
    sudo mount -t hugetlbfs nodev "$HUGEPAGE_MOUNT"
fi

# --- Steady-state performance -------------------------------------------
# irqbalance migrates IRQs at runtime and adds jitter to the receive path.
sudo systemctl stop irqbalance 2>/dev/null || true

# Lock every core to its top frequency so results are not governor-dependent.
for governor in /sys/devices/system/cpu/cpu*/cpufreq/scaling_governor; do
    [ -w "$governor" ] || continue
    echo performance | sudo tee "$governor" >/dev/null 2>&1 || true
done

echo "node-setup: complete ($allocated x 2 MiB hugepages, $HUGEPAGE_MOUNT mounted)"
