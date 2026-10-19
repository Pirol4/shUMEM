#!/usr/bin/env bash
#
# Kernel boot parameters for the dut's measurement runs (CLAUDE.md 15.5, 19.25):
# isolate the l3fwd lcores, hyperthreading off, 1 GiB hugepages. These need a
# reboot, so they cannot live in node-setup.sh (which runs at every boot and
# checks them afterwards).
#
# Usage (on dut only, once per instantiation, after setup/dev-environment.sh):
#   sudo setup/kernel-tuning.sh          # writes the GRUB drop-in, update-grub
#   sudo reboot                          # then log in again (~3-5 min)
#   setup/kernel-tuning.sh --check       # after the reboot: all lines OK?
#   sudo setup/kernel-tuning.sh --undo   # back to the stock command line
#
# What each parameter does, for lcores 1-8 (l3fwd's forwarding cores):
#   isolcpus=nohz,domain,managed_irq,1-8   no scheduler balancing onto them and
#                                          no managed IRQs; only pinned threads run
#   nohz_full=1-8                          no periodic timer tick while one task runs
#   rcu_nocbs=1-8                          RCU callbacks run on the other CPUs
#   irqaffinity=0,9-15                     default IRQ affinity: never the lcores
#   nosmt                                  hyperthreads off (16 CPUs, 0-15), as the
#                                          shRing paper runs
#   nmi_watchdog=0                         no periodic NMI on every CPU
#   default_hugepagesz=1G hugepagesz=1G hugepages=16
#                                          16 GiB of 1 GiB pages, as the paper; the
#                                          mbuf pool then needs a handful of TLB entries
# The tgen is left alone: TRex already runs on distinct physical cores.

set -euo pipefail

DROPIN=/etc/default/grub.d/99-shumem-tuning.cfg
LCORES=1-8
HOUSEKEEPING=0,9-15
PARAMS="isolcpus=nohz,domain,managed_irq,$LCORES nohz_full=$LCORES rcu_nocbs=$LCORES"
PARAMS+=" irqaffinity=$HOUSEKEEPING nosmt nmi_watchdog=0"
PARAMS+=" default_hugepagesz=1G hugepagesz=1G hugepages=16"

check() {
    local bad=0 cmdline isolated nohz smt huge1g
    cmdline="$(cat /proc/cmdline)"
    isolated="$(cat /sys/devices/system/cpu/isolated 2>/dev/null)"
    nohz="$(cat /sys/devices/system/cpu/nohz_full 2>/dev/null)"
    smt="$(cat /sys/devices/system/cpu/smt/active 2>/dev/null || echo '?')"
    huge1g="$(cat /sys/kernel/mm/hugepages/hugepages-1048576kB/nr_hugepages 2>/dev/null || echo 0)"
    ok()   { echo "  OK    $*"; }
    fail() { echo "  FAIL  $*"; bad=1; }
    [[ "$cmdline" == *"isolcpus="* ]] && ok "booted with the tuning parameters" \
        || fail "not booted with them (/proc/cmdline lacks isolcpus=): reboot after running this script"
    [[ "$isolated" == "$LCORES" ]] && ok "isolated CPUs $isolated" || fail "isolated CPUs '$isolated', want $LCORES"
    [[ "$nohz" == "$LCORES" ]] && ok "nohz_full CPUs $nohz" || fail "nohz_full CPUs '$nohz', want $LCORES"
    [[ "$smt" == 0 ]] && ok "hyperthreading off" || fail "hyperthreading active ($smt)"
    (( huge1g >= 16 )) && ok "$huge1g x 1 GiB hugepages" || fail "$huge1g x 1 GiB hugepages, want 16"
    lsmod | grep -q '^msr ' && ok "msr module loaded (PCM)" || fail "msr module not loaded: sudo modprobe msr"
    return "$bad"
}

case "${1:-}" in
    --check)
        check ;;
    --undo)
        [[ "$EUID" -eq 0 ]] || { echo "run with sudo" >&2; exit 1; }
        rm -f "$DROPIN"
        update-grub
        echo "Removed $DROPIN; reboot to return to the stock command line." ;;
    "")
        [[ "$EUID" -eq 0 ]] || { echo "run with sudo" >&2; exit 1; }
        [[ "$(hostname)" == dut* ]] || { echo "this is for the dut (hostname $(hostname))" >&2; exit 1; }
        mkdir -p "$(dirname "$DROPIN")"
        # A drop-in is read after /etc/default/grub, and rewriting it makes the
        # script idempotent; the stock command line is kept and extended.
        printf '# Written by shUMEM setup/kernel-tuning.sh\nGRUB_CMDLINE_LINUX="$GRUB_CMDLINE_LINUX %s"\n' \
            "$PARAMS" >"$DROPIN"
        update-grub
        grep -q "isolcpus=nohz,domain,managed_irq,$LCORES" /boot/grub/grub.cfg \
            || { echo "ERROR: update-grub did not put the parameters in /boot/grub/grub.cfg" >&2; exit 1; }
        echo "Wrote $DROPIN and updated GRUB. Next: sudo reboot, then setup/kernel-tuning.sh --check" ;;
    *)
        echo "usage: sudo $0 [--check|--undo]" >&2; exit 1 ;;
esac
