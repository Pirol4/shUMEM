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

# --- Pause frames off on the experiment NIC --------------------------------
# 802.3x flow control lets an overloaded receiver pause the sender instead of
# dropping, which turns loss into lower throughput and hides exactly the
# difference between receive-ring designs. Both nodes shipped with RX/TX pause
# on, and ethtool -A does not survive a reboot, hence doing it here.
EXPERIMENT_SUBNET_PREFIX=10.10.1.
experiment_iface=$(ip -o -4 addr show | awk -v p="$EXPERIMENT_SUBNET_PREFIX" 'index($4, p) == 1 {print $2}' | head -1)
if [ -n "$experiment_iface" ]; then
    if sudo ethtool -A "$experiment_iface" autoneg off rx off tx off 2>/dev/null \
        || sudo ethtool -A "$experiment_iface" rx off tx off; then
        echo "node-setup: pause frames off on $experiment_iface"
    else
        echo "node-setup: WARNING could not disable pause frames on $experiment_iface" >&2
    fi
else
    echo "node-setup: WARNING no interface in ${EXPERIMENT_SUBNET_PREFIX}0/24; pause frames left as they are" >&2
fi

# --- ACPI interrupt off the forwarding cores --------------------------------
# The ACPI SCI fires twice a second and arrived on CPU 1, the lcore serving
# queue 0: two ~77 us pauses a second there, which is all the loss the hot
# queue test showed until it was found (CLAUDE.md 19.9, 19.12). CPU 0 is left
# to the OS by every run (lcores are 1-8).
acpi_irq=$(awk '$NF == "acpi" {sub(":", "", $1); print $1}' /proc/interrupts | head -1)
if [ -n "$acpi_irq" ] && echo 0 | sudo tee "/proc/irq/$acpi_irq/smp_affinity_list" >/dev/null 2>&1; then
    echo "node-setup: ACPI IRQ $acpi_irq routed to CPU 0"
else
    echo "node-setup: WARNING could not route the ACPI IRQ to CPU 0" >&2
fi

echo "node-setup: complete ($allocated x 2 MiB hugepages, $HUGEPAGE_MOUNT mounted)"
