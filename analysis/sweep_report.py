#!/usr/bin/env python3
"""Turn harness/sweep.sh's summary.txt into a table, a CSV and the trade-off figure.

One row per traffic window of every run: TRex loss, DRAM read/write over that
window (sweep.sh's "DRAM window" lines), cycles per packet and common-ring
objects per packet (fork 9601a7530 on), the stall that ran, and the run's
system and knobs. Runs from before these lines existed parse with the
missing fields empty.

The figure is CLAUDE.md 19.19's curve: loss per minute against DRAM write in
the last window of every balanced run without a stall (the steady state,
19.15), one point per run, one color and marker per system.

Usage (anywhere; the figure needs matplotlib):
  python3 analysis/sweep_report.py summary.txt [--csv out.csv] [--plot docs/figures/tradeoff]
"""
import argparse
import csv
import re
import sys

RUN_HEADER = re.compile(r"^== (\S+) \((.+)\)$")
RUN_LINE = re.compile(r"^\s+run: (.*)$")
RATE_LINE = re.compile(r"^\s+(\S+)\s+([\d.]+)%\s+tx=(\d+) rx=(\d+) noise=(\d+) loss=(-?[\d.]+)%"
                       r"\s+lat avg/max=(\S+)/(\S+) us")
MISSED_LINE = re.compile(r"^\s+rx_missed_errors=(\d*)")
QUEUES_LINE = re.compile(r"^\s+per queue rx:(.*)$")
CYCLES_LINE = re.compile(r"^\s+cycles/pkt over (\d+) pkts: rx ([\d.]+)\s+fwd ([\d.]+)"
                         r"\s+common-ring objects/pkt ([\d.]+)")
DRAM_LINE = re.compile(r"^\s+DRAM window (\d+) .*: n=(\d+) read=([\d.]+) write=([\d.]+)")
STALL_LINE = re.compile(r"^\s+stall_core: (\d+) stalls, mean (\d+) us")
CONTENTION_LINE = re.compile(r"^\s+contention (\d+)")

FIELDS = ["label", "date", "system", "window_ms", "headroom_pct", "stall", "extra",
          "window", "rate_pct", "tx_pkts", "loss_pkts", "loss_per_min", "lat_avg_us",
          "lat_max_us", "dram_n", "dram_read_mb_s", "dram_write_mb_s", "rx_cyc_per_pkt",
          "fwd_cyc_per_pkt", "ring_objs_per_pkt", "rx_missed_run", "contention_run",
          "stalls", "stall_mean_us", "queue_rx_run"]

# Fixed categorical order, the first three as in stall_model.py so a system
# keeps its color across figures; marker shape is the second encoding.
STYLE = {
    "privring-1024": ("#2a78d6", "o"),
    "privring-128": ("#eb6834", "s"),
    "shring-8": ("#1baf7a", "D"),
    "fill-128": ("#eda100", "^"),
    "dyn-128": ("#e87ba4", "o"),
}
INK, INK_MUTED, GRID = "#1f1f1e", "#6b6a63", "#e4e3dc"


def system_of(run):
    """`dyn-128-64` and `dyn-128` share a style; anything unknown gets none."""
    system = run.get("system", "")
    for key in STYLE:
        if system == key or system.startswith(key + "-"):
            return key
    return system


def parse_runs(path):
    runs, run = [], None
    with open(path) as f:
        for line in f:
            line = line.rstrip("\n")
            m = RUN_HEADER.match(line)
            if m:
                run = {"label": m.group(1), "date": m.group(2), "windows": [], "dram": {}}
                runs.append(run)
                continue
            if run is None:
                continue
            if (m := RUN_LINE.match(line)):
                for item in m.group(1).split(" "):
                    key, _, value = item.partition("=")
                    if key == "extra":   # the rest of the line, spaces included
                        run["extra"] = m.group(1).split("extra=", 1)[1]
                        break
                    run[key] = value
            elif (m := RATE_LINE.match(line)):
                run["windows"].append({
                    "rate_pct": float(m.group(2)), "tx_pkts": int(m.group(3)),
                    "loss_pkts": int(m.group(3)) - int(m.group(4)),
                    "lat_avg_us": m.group(7), "lat_max_us": m.group(8)})
            elif (m := MISSED_LINE.match(line)):
                run["rx_missed_run"] = m.group(1)
            elif (m := QUEUES_LINE.match(line)):
                run["queue_rx_run"] = " ".join(m.group(1).split())
            elif (m := CYCLES_LINE.match(line)):
                run["rx_cyc_per_pkt"], run["fwd_cyc_per_pkt"] = m.group(2), m.group(3)
                run["ring_objs_per_pkt"] = m.group(4)
            elif (m := DRAM_LINE.match(line)):
                run["dram"][int(m.group(1))] = (m.group(2), m.group(3), m.group(4))
            elif (m := STALL_LINE.match(line)):
                run["stalls"], run["stall_mean_us"] = m.group(1), m.group(2)
            elif (m := CONTENTION_LINE.match(line)):
                run["contention_run"] = m.group(1)
    return runs


def rows(runs):
    for run in runs:
        duration = float(run.get("duration") or 0)
        for k, window in enumerate(run["windows"], start=1):
            n, read, write = run["dram"].get(k, ("", "", ""))
            row = {key: run.get(key, "") for key in FIELDS}
            row.update(window)
            row.update({"window": k, "dram_n": n, "dram_read_mb_s": read,
                        "dram_write_mb_s": write,
                        "loss_per_min": round(60 * window["loss_pkts"] / duration, 1)
                        if duration else ""})
            yield row


def print_table(table):
    print("%-22s %-14s %3s %10s %9s %9s %7s %7s %10s %s" % (
        "label", "system", "win", "loss", "loss/min", "DRAM wr", "rx cyc", "fwd cyc",
        "ring/pkt", "stall"))
    for r in table:
        print("%-22s %-14s %3s %10s %9s %9s %7s %7s %10s %s" % (
            r["label"], r["system"] or "?", r["window"], r["loss_pkts"], r["loss_per_min"],
            r["dram_write_mb_s"] or "-", r["rx_cyc_per_pkt"] or "-",
            r["fwd_cyc_per_pkt"] or "-", r["ring_objs_per_pkt"] or "-",
            r["stall"] if r["stall"] not in ("", "-") else ""))


def steady_state_points(runs):
    """Last window of balanced runs with no stall and a DRAM figure."""
    for run in runs:
        if run.get("stall", "-") != "-" or "hot-share" in run.get("extra", ""):
            continue
        if not run["windows"] or not run.get("duration"):
            continue
        last = len(run["windows"])
        if last not in run["dram"]:
            continue
        loss = run["windows"][-1]["loss_pkts"]
        # Stray unicast to the tgen MAC makes TRex's loss negative (17.8); the
        # dut's own count for the last window is what earlier windows left.
        if loss < 0 and run.get("rx_missed_run"):
            earlier = sum(w["loss_pkts"] for w in run["windows"][:-1])
            loss = max(int(run["rx_missed_run"]) - earlier, 0)
        yield (system_of(run), run["label"], float(run["dram"][last][2]),
               60 * loss / float(run["duration"]))


def plot(runs, out):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    points = list(steady_state_points(runs))
    if not points:
        sys.exit("no balanced run with a DRAM window to plot")
    fig, ax = plt.subplots(figsize=(6.4, 4.0), constrained_layout=True)
    seen = set()
    # In palette order, so fill-128 is drawn over privring-128, which it
    # measures like by design (CLAUDE.md 18.7) and would otherwise hide.
    for system, label, write, loss in sorted(points, key=lambda p: list(STYLE).index(p[0])
                                             if p[0] in STYLE else len(STYLE)):
        color, marker = STYLE.get(system, (INK_MUTED, "x"))
        ax.plot([write], [max(loss, 0)], linestyle="none", marker=marker,
                markersize=10 if marker == "^" else 8, color=color,
                markeredgecolor="white", markeredgewidth=2, clip_on=False, zorder=3,
                label=None if system in seen else system)
        seen.add(system)
        # Direct labels only for the policy's own points: their knobs are the
        # reading; the anchors are named by the legend.
        if system == "dyn-128":
            ax.annotate(label.replace("dyn-128_", "").replace("dyn-128", "ref"),
                        (write, max(loss, 0)), xytext=(6, 4), textcoords="offset points",
                        fontsize=8, color=INK_MUTED)
    ax.set_xscale("log")
    ax.set_xlabel("DRAM write, steady-state window (MB/s, log)", color=INK_MUTED)
    ax.set_ylabel("packets lost per minute", color=INK_MUTED)
    ax.set_ylim(bottom=0)
    ax.margins(x=0.08)
    ax.grid(color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(GRID)
    ax.tick_params(colors=INK_MUTED, labelsize=9)
    ax.legend(frameon=False, fontsize=9, labelcolor=INK)
    ax.set_title("Balanced load, 98%: what each system's working set costs in loss",
                 color=INK, fontsize=11, loc="left")
    for ext in ("png", "pdf"):
        fig.savefig("%s.%s" % (out, ext), dpi=200, facecolor="white")
    print("wrote %s.png and %s.pdf (%d points)" % (out, out, len(points)))


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("summary", help="harness/sweep.sh's summary.txt")
    parser.add_argument("--csv", help="also write one row per window here")
    parser.add_argument("--plot", metavar="OUT", help="write OUT.png and OUT.pdf")
    args = parser.parse_args()

    runs = parse_runs(args.summary)
    table = list(rows(runs))
    print_table(table)
    if args.csv:
        with open(args.csv, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(table)
    if args.plot:
        plot(runs, args.plot)


if __name__ == "__main__":
    main()
