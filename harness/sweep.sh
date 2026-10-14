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
#   TGEN_MAC=<mac> harness/sweep.sh harness/sweeps/tradeoff.txt
#
# Sweep file: one run per line, `#` comments,
#   <label> <system> <window_ms|-> <headroom_pct|-> [extra rate_sweep.py args]
# with <system> as for run_l3fwd.sh. A label already used gets _r2, _r3...
#
# Environment:
#   TGEN_MAC   required: src_mac in the tgen's /etc/trex_cfg.yaml
#   TGEN_HOST  tgen on the control network (default: this host's FQDN with
#              dut. replaced by tgen.); never the 10.10.1.x name, which TRex
#              holds
#   RATES      default 98,98: start-up window, then steady state (CLAUDE.md 19.15)
#   DURATION   seconds per rate, default 60
#   CSV        rate_sweep.py output on the tgen, default /mydata/exp/tradeoff.csv
#
# Output: $EXP_DIR/sweep/<label>.out (l3fwd and rate_sweep output) and
# $EXP_DIR/sweep/summary.txt (one block per run), next to the PCM CSVs.

set -euo pipefail

PLAN="${1:-}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
EXP_DIR="${EXP_DIR:-/mydata/exp}"
OUT_DIR="$EXP_DIR/sweep"
RATES="${RATES:-98,98}"
DURATION="${DURATION:-60}"
CSV="${CSV:-/mydata/exp/tradeoff.csv}"
TGEN_HOST="${TGEN_HOST:-$(hostname -f | sed 's/^dut\./tgen./')}"
TGEN_TREX_DIR=/local/repository/harness/trex
# The ssh client runs beside busy-polling lcores 1-8; keep it on CPU 0.
SSH=(taskset -c 0 ssh -o BatchMode=yes -o ConnectTimeout=10
     -o StrictHostKeyChecking=accept-new "$TGEN_HOST")

die() { echo "ERROR: $*" >&2; exit 1; }
log() { echo "[sweep $(date +%H:%M:%S)] $*"; }

# --- Checks before the first run, so a broken setup fails in seconds --------
[[ -n "$PLAN" && -r "$PLAN" ]] || die "usage: TGEN_MAC=<mac> $0 <sweep file>"
[[ "$EUID" -ne 0 ]] || die "run as your own user, not with sudo: the ssh to tgen needs your key"
[[ -n "${TGEN_MAC:-}" ]] || die "set TGEN_MAC (src_mac in tgen's /etc/trex_cfg.yaml)"
sudo -n true 2>/dev/null || die "sudo asks for a password; run_l3fwd.sh needs root"
[[ -w "$EXP_DIR" ]] || die "$EXP_DIR not writable: sudo mkdir -p $EXP_DIR && sudo chown \$USER $EXP_DIR"
! pgrep -x dpdk-l3fwd >/dev/null || die "an l3fwd is already running; stop it first"

tgen_ip="$(getent hosts "$TGEN_HOST" | awk '{print $1; exit}')"
[[ -n "$tgen_ip" ]] || die "cannot resolve $TGEN_HOST; set TGEN_HOST to the tgen's control-network name (hostname -f on tgen)"
[[ "$tgen_ip" != 10.10.1.* ]] || die "$TGEN_HOST is the experiment link ($tgen_ip), which TRex holds; set TGEN_HOST to the control-network name"
"${SSH[@]}" true 2>/dev/null || die "cannot ssh to $TGEN_HOST without a password.
       On dut:  ssh-keygen -t ed25519 -N '' -f ~/.ssh/id_ed25519 (if absent); cat ~/.ssh/id_ed25519.pub
       On tgen: append that line to ~/.ssh/authorized_keys"
"${SSH[@]}" "pgrep -f '_t-rex-6[4]'" >/dev/null || die "TRex is not running on $TGEN_HOST (cd /mydata/trex/v3.07 && sudo ./t-rex-64 -i -c 6 --no-ofed-check)"

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
trap 'stop_l3fwd' EXIT
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
summarize() {
    local label="$1" out="$2"
    {
        echo "== $label ($(date '+%F %T'))"
        grep -E ' tx=[0-9]+ rx=[0-9]+ ' "$out" | sed 's/^/   /' || true
        awk '/ \[[0-9]+\] = -?[0-9]+$/ { v[$1] = $NF }
             END {
                 printf "   rx_missed_errors=%s  rx_phy_packets=%s  tx_phy_packets=%s\n",
                        v["rx_missed_errors"], v["rx_phy_packets"], v["tx_phy_packets"]
                 line = "   per queue rx:"
                 for (q = 0; q < 8; q++) line = line " " v["rx_q" q "_packets"]
                 print line
             }' "$out"
        grep -oE 'contention [0-9]+' "$out" | tail -1 | sed 's/^/   /' || true
        grep -oE 'Rx queue [0-9]+: FILL credits:.*' "$out" | sed 's/^/   /' || true
        "$SCRIPT_DIR/pcm_summary.sh" "$label" 2>&1 | sed 's/^/   /' || true
    } | tee -a "$SUMMARY"
}

run_one() {
    local label="$1" system="$2" window="$3" headroom="$4"
    shift 4
    local extra=("$@") out env_args=()

    label="$(free_label "$label")"
    out="$OUT_DIR/$label.out"
    [[ "$window" == - ]] || env_args+=("FILL_WINDOW_MS=$window")
    [[ "$headroom" == - ]] || env_args+=("FILL_HEADROOM=$headroom")

    log "$label: starting $system ${env_args[*]:-}"
    sudo -n env PCM_LABEL="$label" TGEN_MAC="$TGEN_MAC" "${env_args[@]}" \
        "$SCRIPT_DIR/run_l3fwd.sh" "$system" >"$out" 2>&1 &
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

    log "$label: traffic, rates $RATES x ${DURATION} s ${extra[*]:-}"
    if ! "${SSH[@]}" "cd $TGEN_TREX_DIR && ./rate_sweep.py --label $label --rates $RATES --duration $DURATION --out $CSV ${extra[*]:-}" \
            2>&1 | tee -a "$out"; then
        stop_l3fwd
        die "$label: rate_sweep.py failed on $TGEN_HOST (see $out)"
    fi
    sleep 2
    stop_l3fwd
    summarize "$label" "$out"
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
