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
# With 1 GiB pages on the kernel command line (setup/kernel-tuning.sh, dut
# only) DPDK gets those and nothing else: no 2 MiB pages, and the mount uses
# the 1 GiB size. Otherwise reserve 2 MiB pages as before. The kernel can hand
# back fewer pages than asked when memory is fragmented, which only bites much
# later as mempool creation failures; surface it now.
HUGEPAGES_1G_SYSFS=/sys/kernel/mm/hugepages/hugepages-1048576kB/nr_hugepages
huge_1g=$(cat "$HUGEPAGES_1G_SYSFS" 2>/dev/null || echo 0)
if [ "$huge_1g" -gt 0 ]; then
    echo 0 | sudo tee "$HUGEPAGES_2M_SYSFS" >/dev/null
    page_size=1G
    allocated="$huge_1g x 1 GiB"
else
    echo "$HUGEPAGES_2M" | sudo tee "$HUGEPAGES_2M_SYSFS" >/dev/null
    page_size=2M
    got=$(cat "$HUGEPAGES_2M_SYSFS")
    if [ "$got" -lt "$HUGEPAGES_2M" ]; then
        echo "node-setup: WARNING wanted $HUGEPAGES_2M hugepages, got $got" >&2
    fi
    allocated="$got x 2 MiB"
fi

if ! mountpoint -q "$HUGEPAGE_MOUNT"; then
    sudo mkdir -p "$HUGEPAGE_MOUNT"
    sudo mount -t hugetlbfs -o pagesize="$page_size" nodev "$HUGEPAGE_MOUNT"
fi

# PCM reads the uncore through MSRs; the module does not load itself at boot.
sudo modprobe msr 2>/dev/null || echo "node-setup: WARNING could not load msr (PCM)" >&2

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
# Every experiment port: 10.10.1.x, and 10.10.2.x with two links (CLAUDE.md 21).
experiment_ifaces=$(ip -o -4 addr show | awk '$4 ~ /^10\.10\.[0-9]+\./ {print $2}' | sort -u)
if [ -n "$experiment_ifaces" ]; then
    for experiment_iface in $experiment_ifaces; do
        if sudo ethtool -A "$experiment_iface" autoneg off rx off tx off 2>/dev/null \
            || sudo ethtool -A "$experiment_iface" rx off tx off; then
            echo "node-setup: pause frames off on $experiment_iface"
        else
            echo "node-setup: WARNING could not disable pause frames on $experiment_iface" >&2
        fi
    done
else
    echo "node-setup: WARNING no interface in 10.10.<n>.0/24; pause frames left as they are" >&2
fi

# --- Interrupts off the forwarding cores -------------------------------------
# Any interrupt on an lcore is a pause of its queue. Two were found by their
# loss (CLAUDE.md 19.9, 19.12-19.13): the ACPI SCI, twice a second on CPU 1,
# ~77 us each; and the control NIC's completion interrupts, wherever the SSH
# flow hashes, 40-140 us a few times a minute. So every interrupt that can be
# moved goes to the CPUs no run uses: not the lcores (l3fwd's on dut, TRex's
# on tgen; setup/topology.py: 1-8 on sm110p with one link) nor their
# hyperthreads. Per-CPU and managed interrupts refuse the write and stay.
busy_list=$(python3 "$(dirname "$0")/topology.py" busy 2>/dev/null) || busy_list=""
if [ -z "$busy_list" ]; then
    echo "node-setup: WARNING setup/topology.py failed; keeping interrupts off CPUs 1-8" >&2
    busy_list=1-8
fi
busy=" "
for range in $(echo "$busy_list" | tr ',' ' '); do
    for cpu in $(seq "${range%-*}" "${range#*-}"); do
        siblings=/sys/devices/system/cpu/cpu$cpu/topology/thread_siblings_list
        if [ -r "$siblings" ]; then
            for sibling in $(tr ',-' '  ' < "$siblings"); do
                busy="$busy$sibling "
            done
        else
            busy="$busy$cpu "
        fi
    done
done
housekeeping=""
# Online CPUs only: with hyperthreading off (nosmt) 16-31 are offline.
for cpu in $(seq 0 $(( $(nproc --all) - 1 ))); do
    [ "$(cat /sys/devices/system/cpu/cpu$cpu/online 2>/dev/null || echo 1)" = 1 ] || continue
    case "$busy" in
        *" $cpu "*) ;;
        *) housekeeping="${housekeeping:+$housekeeping,}$cpu" ;;
    esac
done
if [ -n "$housekeeping" ]; then
    moved=0
    for irq in /proc/irq/[0-9]*; do
        if echo "$housekeeping" | sudo tee "$irq/smp_affinity_list" >/dev/null 2>&1; then
            moved=$((moved + 1))
        fi
    done
    echo "node-setup: $moved interrupts routed to CPUs $housekeeping"
else
    echo "node-setup: WARNING no CPU besides $busy_list and their hyperthreads; interrupts left as they are" >&2
fi

isolated=$(cat /sys/devices/system/cpu/isolated 2>/dev/null)

# --- CPU 0 as a polling lcore (CLAUDE.md 21.4) -------------------------------
# Only when kernel-tuning.sh isolated CPU 0, i.e. the dut of sm220u with the
# NIC on socket 0 (lcores 0-15); one port on sm110p keeps CPU 0 for the OS and
# skips all of this. isolcpus=domain does not move what already sits on an
# isolated CPU, and CPU 0 is where boot puts it. Three things paused CPU 0 (or
# the socket's last CPU) for 100-600 us and lost packets on p0q0 at 2 x 98%:
#  - kernel threads and services left on 0-15 (jbd2 + kblockd for /mydata on
#    the root disk, kcompactd, rcu): every task that accepts it goes to the
#    housekeeping CPUs; per-CPU kthreads refuse, as they should;
#  - proactive compaction and NUMA balancing, which scan memory on any CPU;
#  - a firmware GPE (0x6C on sm220u: Intel SPS Node Manager, HECI-2) firing
#    twice a second, whose ACPI work the kernel always runs on CPU 0 (kacpid):
#    every GPE that fires while the node idles is disabled (undo: echo enable
#    > /sys/firmware/acpi/interrupts/gpeXX; a reboot also restores it).
case "$isolated" in
0|0,*|0-*)
    if [ -n "$housekeeping" ]; then
        moved=0
        for pid in $(ps -eo pid=); do
            case "$(cat /proc/$pid/comm 2>/dev/null)" in
                dpdk-*|lcore-worker*|_t-rex-64) continue ;;   # a run in progress
            esac
            sudo taskset -apc "$housekeeping" "$pid" >/dev/null 2>&1 && moved=$((moved + 1))
        done
        echo "node-setup: $moved tasks moved to CPUs $housekeeping (CPU 0 is an lcore)"
    fi
    echo 0 | sudo tee /proc/sys/vm/compaction_proactiveness >/dev/null 2>&1 || true
    echo 0 | sudo tee /proc/sys/kernel/numa_balancing >/dev/null 2>&1 || true
    gpe_count() { awk '{print $1}' "$1" 2>/dev/null || echo 0; }
    before=""
    for gpe in /sys/firmware/acpi/interrupts/gpe[0-9A-F][0-9A-F]; do
        [ -r "$gpe" ] && before="$before ${gpe##*/}=$(gpe_count "$gpe")"
    done
    sleep 3
    for entry in $before; do
        gpe=/sys/firmware/acpi/interrupts/${entry%=*}
        if [ "$(gpe_count "$gpe")" -gt "${entry#*=}" ]; then
            if echo disable | sudo tee "$gpe" >/dev/null 2>&1; then
                echo "node-setup: periodic ACPI ${entry%=*} disabled (its work runs on CPU 0)"
            else
                echo "node-setup: WARNING could not disable periodic ACPI ${entry%=*}" >&2
            fi
        fi
    done
    ;;
esac

echo "node-setup: complete ($allocated hugepages, $HUGEPAGE_MOUNT mounted)"
echo "node-setup: isolated CPUs ${isolated:-none}${isolated:+ (setup/kernel-tuning.sh --check for the rest)}"
