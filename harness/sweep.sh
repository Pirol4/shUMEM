#!/usr/bin/env bash
#
# Run a list of l3fwd configurations against the same traffic, unattended:
# for each line of a sweep file, start run_l3fwd.sh here on the dut, run
# rate_sweep.py on the tgen over ssh, stop l3fwd as Ctrl-C would, and keep a
# one-line summary per run. It replaces the by-hand protocol of CLAUDE.md 17
# (start l3fwd, run rate_sweep, Ctrl-C), one line of the file per round.
#
# Usage (on dut, inside tmux, as your own user: the ssh to tgen uses your key,
# and run_l3fwd.sh is started with sudo):
#   TGEN_HOST=<tgen control name> TGEN_MAC=<mac> harness/sweep.sh harness/sweeps/tradeoff.txt
#
# Sweep file: one run per line, `#` comments,
#   <label> <system> <window_ms|-> <headroom_pct|-> [options] [extra rate_sweep.py args]
# with <system> as for run_l3fwd.sh. A label already used gets _r2, _r3...
# Options, for this line only:
#   rates=<r1,r2,...>        instead of $RATES
#   duration=<s>             instead of $DURATION
#   stall=<core>:<us>:<ms>   run stall_core.py on <core> during the traffic,
#                            <us> every <ms> (CLAUDE.md 17.10)
#   cache=<n>                l3fwd's per-lcore mempool cache (default 256,
#                            0 = none): every get/put through the common ring
#   pcm=pcie                 record pcm-pcie (DDIO) instead of pcm-memory
#   ops=<driver>             l3fwd's mempool driver (ring_mp_mc, stack, lf_stack)
#   groups=<G>               G mbuf pools (l3fwd) and, for dyn-*, G credit pools
#   reserve=<R>              dyn-* credit pool reserve (default 64; 0 = earlier policy)
# e.g. `--hot-share 0.5` among the extra args makes queue 0 hot (17.7).
#
# Environment:
#   TGEN_MAC   required: src_mac in the tgen's /etc/trex_cfg.yaml
#   TGEN_HOST  required: the tgen's control-network name, as the CloudLab List
#              View gives it (sm110p-....wisc.cloudlab.us). Not derived from
#              this host's name: on 2026-10-14 tgen.<exp>... resolved to the
#              dut itself. Never the 10.10.1.x name, which TRex holds.
#   RATES      default 98,98: start-up window, then steady state (CLAUDE.md 19.15)
#   DURATION   seconds per rate, default 60
#   CSV        rate_sweep.py output on the tgen, default /mydata/exp/tradeoff.csv
#
# Output: $EXP_DIR/sweep/<label>.out (l3fwd and rate_sweep output),
# <label>.times (when each traffic window ended, dut clock), <label>.stall
# (stall_core.py's report) and $EXP_DIR/sweep/summary.txt (one block per run,
# DRAM per traffic window included), next to the PCM CSVs.

set -euo pipefail

PLAN="${1:-}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
EXP_DIR="${EXP_DIR:-/mydata/exp}"
OUT_DIR="$EXP_DIR/sweep"
RATES="${RATES:-98,98}"
DURATION="${DURATION:-60}"
CSV="${CSV:-/mydata/exp/tradeoff.csv}"
TGEN_HOST="${TGEN_HOST:-}"
L3FWD_BIN="${SHRING_DIR:-/mydata/dpdk-research/shring-dpdk}/build/examples/dpdk-l3fwd"
TGEN_TREX_DIR=/local/repository/harness/trex
# rate_sweep.py waits this long after each window before reading counters.
RX_DRAIN_S=1
# Trimmed from each end of a window before averaging DRAM: TRex's ramp, the
# ssh delay of the line that marks the end, PCM's 1 s sampling.
PCM_TRIM_S=2
# The ssh client runs beside busy-polling lcores 1-8; keep it on CPU 0.
SSH=(taskset -c 0 ssh -o BatchMode=yes -o ConnectTimeout=10
     -o StrictHostKeyChecking=accept-new "$TGEN_HOST")

die() { echo "ERROR: $*" >&2; exit 1; }
log() { echo "[sweep $(date +%H:%M:%S)] $*"; }

# --- Checks before the first run, so a broken setup fails in seconds --------
[[ -n "$PLAN" && -r "$PLAN" ]] || die "usage: TGEN_MAC=<mac> $0 <sweep file>"
[[ "$EUID" -ne 0 ]] || die "run as your own user, not with sudo: the ssh to tgen needs your key"
[[ -n "${TGEN_MAC:-}" ]] || die "set TGEN_MAC (src_mac in tgen's /etc/trex_cfg.yaml)"
[[ -n "$TGEN_HOST" ]] || die "set TGEN_HOST to the tgen's control-network name (CloudLab List View)"
sudo -n true 2>/dev/null || die "sudo asks for a password; run_l3fwd.sh needs root"
[[ -w "$EXP_DIR" ]] || die "$EXP_DIR not writable: sudo mkdir -p $EXP_DIR && sudo chown \$USER $EXP_DIR"
! pgrep -x dpdk-l3fwd >/dev/null || die "an l3fwd is already running; stop it first"

tgen_ip="$(getent hosts "$TGEN_HOST" | awk '{print $1; exit}')"
[[ -n "$tgen_ip" ]] || die "cannot resolve $TGEN_HOST"
[[ "$tgen_ip" != 10.10.1.* ]] || die "$TGEN_HOST is the experiment link ($tgen_ip), which TRex holds; set TGEN_HOST to the control-network name"
[[ "$("${SSH[@]}" hostname 2>/dev/null)" == tgen* ]] || die "$TGEN_HOST is not the tgen, or cannot be reached by ssh without a password.
       On dut:  ssh-keygen -t ed25519 -N '' -f ~/.ssh/id_ed25519 (if absent); cat ~/.ssh/id_ed25519.pub
       On tgen: append that line to ~/.ssh/authorized_keys2 (authorized_keys is rewritten by CloudLab)"
"${SSH[@]}" "pgrep -f '_t-rex-6[4]'" >/dev/null || die "TRex is not running on $TGEN_HOST (cd /mydata/trex/v3.07 && sudo ./t-rex-64 -i -c 6 --no-ofed-check)"

# The build must have the knobs the sweep files use.
# fork a8a7459be: mempool groups/ops in l3fwd, rmp_fix and fill_groups in mlx5.
grep -aq "L3FWD_MEMPOOL_GROUPS" "$L3FWD_BIN" 2>/dev/null && grep -aq "rmp_fix" "$L3FWD_BIN" \
    || die "$L3FWD_BIN is missing or older than fork a8a7459be:
       sudo git -C ${L3FWD_BIN%/build/*} pull && sudo ninja -C ${L3FWD_BIN%/examples/*}"

# Not fatal, but every number of CLAUDE.md 19.12-19.18 assumes them.
warn() { echo "WARNING: $*" >&2; }
# Numbered interrupts that fire on lcores 1-8 within 3 s (the ACPI one fires
# every second, the control NIC's with SSH traffic).
lcore_irqs="$(python3 - <<'PY'
import time
LCORES = range(1, 9)
def snapshot():
    rows = {}
    with open("/proc/interrupts") as f:
        cpus = f.readline().split()
        cols = [cpus.index("CPU%d" % c) for c in LCORES if "CPU%d" % c in cpus]
        for line in f:
            irq, _, rest = line.partition(":")
            fields = rest.split()
            if irq.strip().isdigit() and len(fields) > max(cols):
                rows[irq.strip()] = ([int(fields[c]) for c in cols], " ".join(fields[len(cpus):])[-40:])
    return rows
before = snapshot()
time.sleep(3)
after = snapshot()
hits = []
for irq, (counts, name) in after.items():
    old = before.get(irq, ([0] * len(counts), ""))[0]
    fired = [str(c) for c, a, b in zip(LCORES, old, counts) if b > a]
    if fired:
        hits.append("%s (%s) on lcore %s" % (irq, name.split()[-1] if name else "?", ",".join(fired)))
print("; ".join(hits))
PY
)"
[[ -z "$lcore_irqs" ]] || warn "interrupts firing on the lcores: $lcore_irqs — move them (setup/node-setup.sh, CLAUDE.md 19.14)"
# node-setup.sh routes it to every CPU off the lcores (e.g. 0,9-16,25-31),
# not to CPU 0 alone, so warn only when the list reaches lcores 1-8.
acpi_cpus="$(cat /proc/irq/9/smp_affinity_list 2>/dev/null || echo '?')"
acpi_on_lcores="$(python3 -c '
import sys
cpus = set()
for part in sys.argv[1].split(","):
    lo, _, hi = part.partition("-")
    cpus.update(range(int(lo), int(hi or lo) + 1))
print(",".join(str(c) for c in sorted(cpus & set(range(1, 9)))))
' "$acpi_cpus" 2>/dev/null || echo '?')"
[[ -z "$acpi_on_lcores" ]] || warn "ACPI IRQ 9 may fire on lcores $acpi_on_lcores (affinity $acpi_cpus): lcore 1 will pause twice a second (CLAUDE.md 19.9)"
# The final evaluation runs on a tuned kernel (setup/kernel-tuning.sh, 19.25).
isolated="$(cat /sys/devices/system/cpu/isolated 2>/dev/null)"
[[ "$isolated" == 1-8 ]] || warn "lcores 1-8 are not isolated (isolated: '${isolated:-none}'): run setup/kernel-tuning.sh and reboot for evaluation runs"
exp_if="$(ip -o -4 addr show | awk '$4 ~ /^10\.10\.1\.1\// {print $2; exit}')"
if [[ -n "$exp_if" ]] && sudo -n ethtool -a "$exp_if" 2>/dev/null | grep -qE '^(RX|TX):[[:space:]]+on'; then
    warn "pause frames are on on $exp_if (CLAUDE.md 15.5): sudo ethtool -A $exp_if rx off tx off"
fi

mkdir -p "$OUT_DIR"
SUMMARY="$OUT_DIR/summary.txt"

# --- One run ------------------------------------------------------------------
L3FWD_PID=""

# kill -0 cannot probe a root process from this user; ps can.
alive() { ps -p "$1" >/dev/null 2>&1; }

stop_l3fwd() {
    [[ -n "$L3FWD_PID" ]] || return 0
    # SIGINT, like Ctrl-C: l3fwd prints its counters and the driver its
    # FILL credits lines before exiting; run_l3fwd.sh then stops PCM.
    sudo pkill -INT -x dpdk-l3fwd 2>/dev/null || true
    local waited=0
    while alive "$L3FWD_PID" && (( waited < 60 )); do
        sleep 1; waited=$((waited + 1))
    done
    if alive "$L3FWD_PID"; then
        log "l3fwd did not stop in 60 s; killing it"
        sudo pkill -KILL -x dpdk-l3fwd 2>/dev/null || true
    fi
    wait "$L3FWD_PID" 2>/dev/null || true
    L3FWD_PID=""
}
STALL_PID=""

stop_stall() {
    [[ -n "$STALL_PID" ]] || return 0
    # SIGINT: stall_core.py prints how many stalls it made, and how long.
    sudo pkill -INT -f "stall_core.py --core" 2>/dev/null || true
    local waited=0
    while alive "$STALL_PID" && (( waited < 10 )); do
        sleep 1; waited=$((waited + 1))
    done
    if alive "$STALL_PID"; then
        log "stall_core.py did not stop in 10 s; killing it"
        sudo pkill -KILL -f "stall_core.py --core" 2>/dev/null || true
    fi
    wait "$STALL_PID" 2>/dev/null || true
    STALL_PID=""
}
trap 'stop_stall; stop_l3fwd' EXIT
trap 'log "interrupted"; exit 130' INT TERM

free_label() {
    local label="$1" n=2
    if [[ -e "$EXP_DIR/pcm-mem_$label.csv" || -e "$OUT_DIR/$label.out" ]]; then
        while [[ -e "$EXP_DIR/pcm-mem_${label}_r$n.csv" || -e "$OUT_DIR/${label}_r$n.out" ]]; do
            n=$((n + 1))
        done
        label="${label}_r$n"
    fi
    echo "$label"
}

# Last value of each port counter (every lcore prints the whole xstats list),
# the FILL credits lines, and the PCM mean, as one block of the summary.
# DRAM per traffic window, from the end times sweep.sh recorded.
dram_per_window() {
    local label="$1" duration="$2" csv="$EXP_DIR/pcm-mem_$1.csv" k=0 t rest end start
    [[ -e "$csv" && -e "$OUT_DIR/$label.times" ]] || return 0
    while read -r t rest; do
        k=$((k + 1))
        end=$(awk -v t="$t" -v d="$RX_DRAIN_S" -v m="$PCM_TRIM_S" 'BEGIN { printf "%.3f", t - d - m }')
        start=$(awk -v e="$end" -v d="$duration" -v m="$PCM_TRIM_S" 'BEGIN { printf "%.3f", e - d + 2 * m }')
        echo "DRAM window $k (${rest%%tx=*}): $("$SCRIPT_DIR/pcm_window.py" "$csv" "$start" "$end")"
    done <"$OUT_DIR/$label.times"
}

summarize() {
    local label="$1" out="$2" duration="$3" run="$4"
    {
        echo "== $label ($(date '+%F %T'))"
        echo "   run: $run"
        grep -E ' tx=[0-9]+ rx=[0-9]+ ' "$out" | sed 's/^/   /' || true
        grep -E '^NDR ' "$out" | sed 's/^/   /' || true
        awk '/ \[[0-9]+\] = -?[0-9]+$/ { v[$1] = $NF }
             END {
                 # shRing drops some packets without counting them as missed
                 # (19.24): rx_phy - rx_good is the dut-side loss that always holds.
                 printf "   rx_missed_errors=%s  rx_phy_packets=%s  rx_good_packets=%s  phy-good=%s  tx_phy_packets=%s\n",
                        v["rx_missed_errors"], v["rx_phy_packets"], v["rx_good_packets"],
                        v["rx_phy_packets"] - v["rx_good_packets"], v["tx_phy_packets"]
                 line = "   per queue rx:"
                 for (q = 0; q < 8; q++) line = line " " v["rx_q" q "_packets"]
                 print line
             }' "$out"
        grep -oE 'contention [0-9]+' "$out" | tail -1 | sed 's/^/   /' || true
        grep -oE 'Rx queue [0-9]+: FILL credits:.*' "$out" | sed 's/^/   /' || true
        # Cost of the path and the mempool's shared ring (fork 9601a7530 on).
        grep -oE 'MEMPOOL .*' "$out" | sed 's/^/   /' || true
        awk '/^CYCLES lcore/ {
                 split($4, a, "="); split($5, r, "="); split($6, f, "=")
                 n += a[2]; rx += a[2] * r[2]; fw += a[2] * f[2]
             }
             /^MEMPOOL .*common ring/ {
                 for (i = 1; i <= NF; i++)
                     if ($i ~ /^(get|put)=/) { split($i, g, "="); ring += g[2] }
             }
             END {
                 if (n > 0)
                     printf "   cycles/pkt over %.0f pkts: rx %.1f  fwd %.1f  common-ring objects/pkt %.5f\n",
                            n, rx / n, fw / n, ring / n
             }' "$out"
        "$SCRIPT_DIR/pcm_summary.sh" "$label" 2>&1 | sed 's/^/   /' || true
        dram_per_window "$label" "$duration" | sed 's/^/   /' || true
        [[ ! -s "$OUT_DIR/$label.stall" ]] || tail -1 "$OUT_DIR/$label.stall" | sed 's/^/   stall_core: /'
    } | tee -a "$SUMMARY"
}

run_one() {
    local label="$1" system="$2" window="$3" headroom="$4"
    shift 4
    local extra=() out env_args=() rates="$RATES" duration="$DURATION" stall="" cache="" pcm=memory ops="" groups="" reserve="" arg
    for arg in "$@"; do
        case "$arg" in
            rates=*)    rates="${arg#rates=}" ;;
            duration=*) duration="${arg#duration=}" ;;
            stall=*)    stall="${arg#stall=}"
                        [[ "$stall" =~ ^[0-9]+:[0-9]+:[0-9.]+$ ]] || die "$label: bad $arg, want stall=<core>:<us>:<ms>" ;;
            pcm=*)      pcm="${arg#pcm=}"
                        [[ "$pcm" == memory || "$pcm" == pcie ]] || die "$label: bad $arg, want pcm=memory|pcie" ;;
            ops=*)      ops="${arg#ops=}"
                        [[ "$ops" =~ ^[a-z_]+$ ]] || die "$label: bad $arg" ;;
            groups=*)   groups="${arg#groups=}"
                        [[ "$groups" =~ ^[1-8]$ ]] || die "$label: bad $arg, want groups=1..8" ;;
            reserve=*)  reserve="${arg#reserve=}"
                        [[ "$reserve" =~ ^[0-9]+$ ]] || die "$label: bad $arg, want reserve=<n>" ;;
            cache=*)    cache="${arg#cache=}"
                        [[ "$cache" =~ ^[0-9]+$ ]] || die "$label: bad $arg, want cache=<n>" ;;
            *)          extra+=("$arg") ;;
        esac
    done

    label="$(free_label "$label")"
    out="$OUT_DIR/$label.out"
    [[ "$window" == - ]] || env_args+=("FILL_WINDOW_MS=$window")
    [[ "$headroom" == - ]] || env_args+=("FILL_HEADROOM=$headroom")
    [[ -z "$cache" ]] || env_args+=("L3FWD_MEMPOOL_CACHE=$cache")
    env_args+=("PCM_TOOL=$pcm")
    [[ -z "$ops" ]] || env_args+=("L3FWD_MEMPOOL_OPS=$ops")
    [[ -z "$groups" ]] || env_args+=("L3FWD_MEMPOOL_GROUPS=$groups" "FILL_GROUPS=$groups")
    [[ -z "$reserve" ]] || env_args+=("FILL_RESERVE=$reserve")

    log "$label: starting $system ${env_args[*]:-}"
    # Append mode for every writer of $out: l3fwd's final counters would
    # otherwise land at its own file offset, over rate_sweep's lines.
    : >"$out"
    sudo -n env PCM_LABEL="$label" TGEN_MAC="$TGEN_MAC" "${env_args[@]}" \
        "$SCRIPT_DIR/run_l3fwd.sh" "$system" >>"$out" 2>&1 &
    L3FWD_PID=$!

    # Ready once the port is up; the lcores start right after.
    local waited=0
    until grep -qE 'entering main loop|Link up' "$out"; do
        alive "$L3FWD_PID" || { tail -20 "$out" >&2; die "$label: l3fwd exited during start-up (see $out)"; }
        (( waited < 120 )) || { stop_l3fwd; tail -20 "$out" >&2; die "$label: l3fwd not ready after 120 s (see $out)"; }
        sleep 1; waited=$((waited + 1))
    done
    grep -m1 -oE 'FILL-ring Rx enabled.*' "$out" | sed "s/^/[sweep]   /" || true
    # PCM's idle baseline, as in the by-hand protocol.
    sleep 10

    if [[ -n "$stall" ]]; then
        local s_core s_us s_ms
        IFS=: read -r s_core s_us s_ms <<<"$stall"
        sudo -n "$SCRIPT_DIR/stall_core.py" --core "$s_core" --stall-us "$s_us" --period-ms "$s_ms" \
            >"$OUT_DIR/$label.stall" 2>&1 &
        STALL_PID=$!
        sleep 1
        alive "$STALL_PID" || { cat "$OUT_DIR/$label.stall" >&2; stop_l3fwd; die "$label: stall_core.py did not start"; }
        log "$label: stalling CPU $s_core for $s_us us every $s_ms ms"
    fi

    log "$label: traffic, rates $rates x ${duration} s ${extra[*]:-}"
    # Each result line marks the end of one window; stamp it with the dut's
    # clock, the one PCM writes, for DRAM per window. Unbuffered: over ssh
    # Python block-buffers stdout and every line arrived at exit (19-Oct).
    : >"$OUT_DIR/$label.times"
    if ! "${SSH[@]}" "cd $TGEN_TREX_DIR && PYTHONUNBUFFERED=1 ./rate_sweep.py --label $label --rates $rates --duration $duration --out $CSV ${extra[*]:-}" 2>&1 \
            | while IFS= read -r line; do
                  printf '%s\n' "$line"
                  [[ "$line" != *" tx="*" rx="* ]] || printf '%s %s\n' "$(date +%s.%N)" "$line" >>"$OUT_DIR/$label.times"
              done | tee -a "$out"; then
        stop_stall
        stop_l3fwd
        die "$label: rate_sweep.py failed on $TGEN_HOST (see $out)"
    fi
    stop_stall
    sleep 2
    stop_l3fwd
    summarize "$label" "$out" "$duration" \
        "system=$system window_ms=$window headroom_pct=$headroom rates=$rates duration=$duration stall=${stall:--} cache=${cache:-256} pcm=$pcm ops=${ops:-ring_mp_mc} groups=${groups:-1} reserve=${reserve:--} extra=${extra[*]:--}"
    # Let the hugepages and the port come back before the next start.
    sleep 5
}

# --- The plan -------------------------------------------------------------------
mapfile -t lines < <(grep -vE '^[[:space:]]*(#|$)' "$PLAN")
log "${#lines[@]} runs from $PLAN, tgen $TGEN_HOST ($tgen_ip), summary in $SUMMARY"
i=0
for line in "${lines[@]}"; do
    i=$((i + 1))
    read -r -a f <<<"$line"
    (( ${#f[@]} >= 4 )) || die "bad line in $PLAN: $line"
    log "---- run $i/${#lines[@]}"
    run_one "${f[@]}"
done
log "done; copy $EXP_DIR from both nodes before ending the experiment"
