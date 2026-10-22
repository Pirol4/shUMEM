#!/usr/bin/env python3
"""Tables and figures of the paper-style evaluation (batteries f1-f15, CLAUDE.md 20).

Reads, from a directory copied off the nodes (searched recursively):
  eval_f*.csv / eval_f*.jsonl   tgen: rate_sweep.py rows and per-window details
  summary.txt                   dut: sweep.sh's block per run (counters, cycles,
                                occupancy, credits, DRAM per window)
  pcm-pcie_*.csv                dut: DDIO counters (f2)

Per cell (battery, variable value, system) it reports the trimmed mean of the
runs (min and max dropped when n >= 4, as both papers do with 10 runs), their
standard deviation, and latency percentiles twice: the trimmed mean of each
run's own percentile, and the percentile of all runs' samples pooled (their
HDR histograms added), which is what p99.99 needs to rest on many samples.
Latency is split by role where a battery has a special queue (the stalled or
target queue against the bystanders), and the loss of each role comes from
its latency probes (10 kpps per queue: a sample of that queue's packets).

Usage (anywhere; figures need matplotlib, latency needs hdrh):
  python3 analysis/eval_report.py C:/.../shumem_eval2 --out docs/eval2 [--battery f1 f4]
"""
import argparse
import collections
import csv
import glob
import json
import math
import os
import re
import statistics
import sys

try:
    from hdrh.histogram import HdrHistogram
except ImportError:
    HdrHistogram = None

LABEL = re.compile(r"^(f\d+)_([^_]+)_([^_]+)_n(\d+)(?:_r\d+)?$")
QUEUES = 8

# The queue each battery singles out; the others are its bystanders.
SPECIAL_QUEUE = {
    "f4": {"cold": 3, "hot": 0, "twob": 3},
    "f5": 3, "f6": 3, "f7": 3, "f13": 3,
    "f15": {"hot": 0},
}
SYSTEM_ORDER = ["privring-1024", "privring-512", "privring-256", "privring-128", "privring-64",
                "shring-8", "shringfix-8", "fill-128", "dyn-32-8", "dyn-64-16", "dyn-128"]
STYLE = {   # as analysis/sweep_report.py, so a system keeps its color
    "privring-1024": ("#2a78d6", "o"), "privring-128": ("#eb6834", "s"),
    "shring-8": ("#1baf7a", "D"), "fill-128": ("#eda100", "^"),
    "dyn-128": ("#e87ba4", "o"), "shringfix-8": ("#7a5bd6", "d"),
}
INK, INK_MUTED, GRID = "#1f1f1e", "#6b6a63", "#e4e3dc"


# --- reading ----------------------------------------------------------------------

def find(root, pattern):
    return sorted(glob.glob(os.path.join(root, "**", pattern), recursive=True))


def number(text):
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


def read_csv_rows(root):
    rows = []
    for path in find(root, "eval_f*.csv"):
        with open(path, newline="") as f:
            rows += list(csv.DictReader(f))
    return rows


def read_details(root):
    """label -> phase -> list of detail records, in file order."""
    details = collections.defaultdict(lambda: collections.defaultdict(list))
    for path in find(root, "eval_f*.jsonl"):
        with open(path) as f:
            for line in f:
                rec = json.loads(line)
                details[rec["label"]][rec["phase"]].append(rec)
    return details


SUMMARY_PATTERNS = {
    "missed": re.compile(r"rx_missed_errors=(\d+)\s+rx_phy_packets=(\d+)\s+rx_good_packets=(\d+)"),
    "queues_rx": re.compile(r"per queue rx:(.*)$"),
    "queues_tx": re.compile(r"per queue tx:(.*)$"),
    "cycles": re.compile(r"cycles/pkt over (\d+) pkts: rx ([\d.]+)\s+fwd ([\d.]+)"
                         r"\s+common-ring objects/pkt ([\d.]+)"),
    "lcore_cycles": re.compile(r"CYCLES lcore (\d+): pkts=(\d+) rx_cyc/pkt=([\d.]+) "
                               r"fwd_cyc/pkt=([\d.]+)"),
    "work": re.compile(r"WORK lcore (\d+): accesses=(\d+) work_cyc/pkt=([\d.]+)"),
    "occupancy": re.compile(r"OCCUPANCY lcore (\d+) queue (\d+): samples=(\d+) "
                            r"mean=([\d.]+) max=(\d+)"),
    "credits": re.compile(r"Rx queue (\d+): FILL credits: budget (\d+) now, (\d+) mean, "
                          r"(\d+) max"),
    "dram": re.compile(r"DRAM window (\d+) \(.*?(phase=\w+)?\): n=(\d+) read=([\d.]+) "
                       r"write=([\d.]+)"),
    "contention": re.compile(r"^\s+contention (\d+)"),
    "ndr": re.compile(r"NDR \S+: ([\d.]+)% of line rate = ([\d.]+) Mpps"),
    "stall": re.compile(r"stall_core: (\d+) stalls"),
}


def read_summaries(root):
    """label -> what the dut recorded for that run."""
    runs, run = {}, None
    for path in find(root, "summary.txt"):
        with open(path) as f:
            for line in f:
                line = line.rstrip("\n")
                m = re.match(r"^== (\S+) \(", line)
                if m:
                    run = runs[m.group(1)] = {"lcores": {}, "queues": {}, "dram": [],
                                              "credits": {}}
                    continue
                if run is None:
                    continue
                parse_summary_line(run, line)
    return runs


def parse_summary_line(run, line):
    p = SUMMARY_PATTERNS
    if (m := p["missed"].search(line)):
        run["rx_missed"], run["phy_good"] = int(m.group(1)), int(m.group(2)) - int(m.group(3))
    elif (m := p["queues_rx"].search(line)):
        run["queue_rx"] = [int(x) for x in m.group(1).split() if x.isdigit()]
    elif (m := p["queues_tx"].search(line)):
        run["queue_tx"] = [int(x) for x in m.group(1).split() if x.isdigit()]
    elif (m := p["cycles"].search(line)):
        run["rx_cyc"], run["fwd_cyc"] = float(m.group(2)), float(m.group(3))
        run["ring_objs"] = float(m.group(4))
    elif (m := p["lcore_cycles"].search(line)):
        run["lcores"].setdefault(int(m.group(1)), {}).update(
            pkts=int(m.group(2)), rx_cyc=float(m.group(3)), fwd_cyc=float(m.group(4)))
    elif (m := p["work"].search(line)):
        run["lcores"].setdefault(int(m.group(1)), {}).update(work_cyc=float(m.group(3)))
    elif (m := p["occupancy"].search(line)):
        run["queues"][int(m.group(2))] = {"occ_mean": float(m.group(4)),
                                          "occ_max": int(m.group(5))}
    elif (m := p["credits"].search(line)):
        run["credits"][int(m.group(1))] = {"budget_mean": int(m.group(3)),
                                           "budget_max": int(m.group(4))}
    elif (m := p["dram"].search(line)):
        run["dram"].append({"phase": (m.group(2) or "phase=sweep")[6:],
                            "read": float(m.group(4)), "write": float(m.group(5))})
    elif (m := p["contention"].search(line)):
        run["contention"] = int(m.group(1))
    elif (m := p["ndr"].search(line)):
        run["ndr_pct"], run["ndr_mpps"] = float(m.group(1)), float(m.group(2))
    elif (m := p["stall"].search(line)):
        run["stalls"] = int(m.group(1))


def read_ddio(root):
    """label -> share of the NIC's full-line writes that hit the LLC, loaded seconds."""
    ddio = {}
    for path in find(root, "pcm-pcie_*.csv"):
        label = os.path.basename(path)[len("pcm-pcie_"):-len(".csv")]
        total = hit = 0
        rows = list(csv.reader(open(path)))
        for row in rows:
            if len(row) < 3 or row[0] == "Skt":
                continue
            itom = number(row[2])
            if itom is None or itom < 1e8:     # idle seconds: the NIC writes little
                continue
            if row[-1].endswith("(Total)"):
                total += itom
            elif row[-1].endswith("(Hit)"):
                hit += itom
        if total:
            ddio[label] = 100.0 * hit / total
    return ddio


# --- one record per measured window -------------------------------------------------

def special_queue(battery, name):
    spec = SPECIAL_QUEUE.get(battery)
    if isinstance(spec, dict):
        for key, queue in spec.items():
            if name.startswith(key):
                return queue
        return None
    return spec


def variable(battery, name, row):
    """The cell's x: a number where the battery sweeps one, else the name."""
    if battery == "f3":
        return float(row["rate_pct"])
    if battery == "f7":
        return int(re.sub(r"\D", "", name)) / 100.0
    if battery == "f8":
        return name     # mi<k>[p]: plotted by payload and k
    m = re.search(r"(\d+)$", name)
    return int(m.group(1)) if m and battery in ("f5", "f6", "f10", "f11") else name


def windows(rows, details, summaries, ddio):
    """Measured windows (phase sweep, or the NDR search's best) as flat records."""
    out = []
    ndr_seen = set()
    sweep_index = collections.Counter()
    for row in rows:
        m = LABEL.match(row["label"])
        if not m or row["phase"] == "warmup":
            continue
        battery, name, system, rep = m.group(1), m.group(2), m.group(3), int(m.group(4))
        summary = summaries.get(row["label"], {})
        if row["phase"] == "search":
            if row["label"] in ndr_seen:
                continue
            ndr_seen.add(row["label"])
            # No NDR line in a summary that has the run: no rate passed.
            known = row["label"] in summaries
            out.append(dict(battery=battery, name=name, system=system, rep=rep,
                            label=row["label"], x=variable(battery, name, row),
                            ndr_pct=summary.get("ndr_pct", 0.0) if known else None,
                            ndr_mpps=summary.get("ndr_mpps", 0.0) if known else None))
            continue
        rec = dict(battery=battery, name=name, system=system, rep=rep, label=row["label"],
                   x=variable(battery, name, row), rate_pct=float(row["rate_pct"]),
                   tx=int(row["tx_pkts"]), loss_pkts=int(row["loss_pkts"]),
                   loss_pct=max(float(row["loss_pct"]), 0.0),
                   gbps=float(row["rx_gbps_l1"]), mpps=float(row["rx_mpps"]))
        for key in ("avg", "p50", "p99", "p999", "p9999", "max"):
            rec["lat_" + key] = number(row.get("lat_%s_us" % key))
        add_latency_roles(rec, battery, name, details.get(row["label"], {}),
                          row["phase"], row["rate_pct"])
        add_dut(rec, summary, battery, name, sweep_index[row["label"]])
        sweep_index[row["label"]] += 1
        rec["ddio_hit_pct"] = ddio.get(row["label"])
        out.append(rec)
    return out


def add_latency_roles(rec, battery, name, details, phase, rate_pct):
    """Per-queue latency of this window, grouped as special queue / bystanders / all."""
    special = special_queue(battery, name)
    recs = [d for d in details.get(phase, []) if d["kind"] == "latency"
            and float(d["rate_pct"]) == float(rate_pct)]
    rec["hdrh"] = {"all": [], "special": [], "bystander": []}
    lost = {"special": [0, 0], "bystander": [0, 0]}
    for d in recs:
        if d.get("queue") == "all":
            if d.get("hdrh"):
                rec["hdrh"]["all"].append(d["hdrh"])
            continue
        role = "special" if d.get("queue") == special else "bystander"
        if d.get("hdrh"):
            rec["hdrh"][role].append(d["hdrh"])
        if d.get("tx"):
            lost[role][0] += d["tx"] - (d.get("rx") or 0)
            lost[role][1] += d["tx"]
        for key in ("p99", "p9999", "max_us"):
            rec.setdefault("%s_%s" % (role, key), [])
            if d.get(key) is not None:
                rec["%s_%s" % (role, key)].append(d[key])
    for role, (n_lost, n_tx) in lost.items():
        rec[role + "_probe_loss_pct"] = 100.0 * n_lost / n_tx if n_tx else None
    # A role's own per-run figure: the worst queue in it.
    for role in ("special", "bystander"):
        for key in ("p99", "p9999", "max_us"):
            values = rec.pop("%s_%s" % (role, key), [])
            rec["%s_%s" % (role, key)] = max(values) if values else None
    for kind in ("series", "window"):
        found = [d for d in details.get(phase, []) if d["kind"] == kind
                 and float(d["rate_pct"]) == float(rate_pct)]
        if found:
            rec[kind] = found[-1]


def add_dut(rec, summary, battery, name, index):
    """`index`: which measured window of the run this is (f3 has one per rate)."""
    dram = [d for d in summary.get("dram", []) if d["phase"] == "sweep"]
    if index < len(dram):
        rec["dram_write"], rec["dram_read"] = dram[index]["write"], dram[index]["read"]
    for key in ("rx_cyc", "fwd_cyc", "ring_objs", "rx_missed", "phy_good", "contention",
                "stalls"):
        rec[key] = summary.get(key)
    occ = summary.get("queues", {})
    if occ:
        rec["occ_mean"] = statistics.mean(q["occ_mean"] for q in occ.values())
        rec["occ_max"] = max(q["occ_max"] for q in occ.values())
        special = special_queue(battery, name)
        if special in occ:
            rec["special_occ_mean"] = occ[special]["occ_mean"]
    credits = summary.get("credits", {})
    if credits:     # the driver prints each queue's line twice; keyed by queue
        rec["posted_mean"] = sum(c["budget_mean"] for c in credits.values())
    lcores = summary.get("lcores", {})
    special = special_queue(battery, name)
    if special is not None and special + 1 in lcores:
        rec["special_fwd_cyc"] = lcores[special + 1].get("fwd_cyc")


# --- cells ------------------------------------------------------------------------

def trimmed(values):
    values = sorted(v for v in values if v is not None and not math.isnan(v))
    if not values:
        return None, None, 0
    sd = statistics.stdev(values) if len(values) > 1 else 0.0
    if len(values) >= 4:
        values = values[1:-1]
    return statistics.mean(values), sd, len(values)


def pooled(encoded, percentiles=(99.0, 99.9, 99.99)):
    if HdrHistogram is None or not encoded:
        return {}
    hist = HdrHistogram(1, 10 * 1000 * 1000, 3)
    for e in encoded:
        hist.add(HdrHistogram.decode(e))
    out = {"p%g" % p: hist.get_value_at_percentile(p) for p in percentiles}
    out.update(n=hist.get_total_count(), max=hist.get_max_value())
    return out


METRICS = ["ndr_pct", "ndr_mpps", "gbps", "mpps", "loss_pct", "loss_pkts", "lat_avg", "lat_p50",
           "lat_p99", "lat_p9999", "lat_max", "special_p9999", "bystander_p9999",
           "special_probe_loss_pct", "bystander_probe_loss_pct", "dram_write", "rx_cyc",
           "fwd_cyc", "special_fwd_cyc", "occ_mean", "special_occ_mean", "posted_mean",
           "ddio_hit_pct", "contention", "ring_objs"]


def cells(records):
    groups = collections.defaultdict(list)
    for r in records:
        groups[(r["battery"], str(r["x"]), r["system"])].append(r)
    out = []
    for (battery, x, system), runs in groups.items():
        cell = {"battery": battery, "x": runs[0]["x"], "system": system, "n": len(runs)}
        for metric in METRICS:
            mean, sd, _ = trimmed([r.get(metric) for r in runs])
            cell[metric], cell[metric + "_sd"] = mean, sd
        for role in ("all", "special", "bystander"):
            for key, value in pooled([e for r in runs for e in r.get("hdrh", {}).get(role, [])]).items():
                cell["pooled_%s_%s" % (role, key)] = value
        cell["wedged"] = sum(1 for r in runs if (r.get("loss_pct") or 0) > 20)
        out.append(cell)
    return sorted(out, key=lambda c: (int(c["battery"][1:]), sort_x(c["x"]), system_rank(c["system"])))


def sort_x(x):
    return (0, x, "") if isinstance(x, (int, float)) else (1, 0, str(x))


def system_rank(system):
    return SYSTEM_ORDER.index(system) if system in SYSTEM_ORDER else len(SYSTEM_ORDER)


# --- printing ---------------------------------------------------------------------

def fmt(value, digits=1):
    if value is None:
        return "-"
    if abs(value) >= 1e5:
        return "%.3g" % value
    return ("%." + str(digits) + "f") % value


def print_tables(table):
    current = None
    for c in table:
        if c["battery"] != current:
            current = c["battery"]
            print("\n== %s" % current)
            print("%-10s %-14s %3s %7s %9s %8s %7s %7s %8s %8s %8s %8s %7s %6s %6s" % (
                "x", "system", "n", "Gbps", "loss%", "NDR%", "avg", "p99", "p99.99*",
                "spec*", "byst*", "bystL%", "DRAMwr", "rxcyc", "occ"))
        print("%-10s %-14s %3d %7s %9s %8s %7s %7s %8s %8s %8s %8s %7s %6s %6s%s" % (
            str(c["x"])[:10], c["system"], c["n"], fmt(c["gbps"], 2), fmt(c["loss_pct"], 4),
            fmt(c["ndr_pct"], 1), fmt(c["lat_avg"]), fmt(c["lat_p99"], 0),
            fmt(c.get("pooled_all_p99.99"), 0), fmt(c.get("pooled_special_p99.99"), 0),
            fmt(c.get("pooled_bystander_p99.99"), 0), fmt(c["bystander_probe_loss_pct"], 4),
            fmt(c["dram_write"], 0), fmt(c["rx_cyc"], 0), fmt(c["occ_mean"], 1),
            "  wedged %d" % c["wedged"] if c["wedged"] else ""))
    print("\n* pooled over every run's HDR histogram; spec = stalled/target queue, "
          "byst = the other queues; bystL% = loss of the bystanders' latency probes")


# --- figures ----------------------------------------------------------------------

def setup_axes(ax, xlabel, ylabel, logy=False, logx=False):
    if logy:
        ax.set_yscale("log")
    if logx:
        ax.set_xscale("symlog", linthresh=10)
        ax.set_xlim(left=0)
    ax.set_xlabel(xlabel, color=INK_MUTED)
    ax.set_ylabel(ylabel, color=INK_MUTED)
    ax.grid(color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(GRID)
    ax.tick_params(colors=INK_MUTED, labelsize=8)


def style(system):
    return STYLE.get(system, (INK_MUTED, "x"))


def line_panels(plt, table, battery, panels, xlabel, out, title, logx=False):
    """One panel per metric, one line per system, x = the battery's variable."""
    rows = [c for c in table if c["battery"] == battery and isinstance(c["x"], (int, float))]
    if not rows:
        return
    fig, axes = plt.subplots(1, len(panels), figsize=(4.2 * len(panels), 3.4),
                             constrained_layout=True, squeeze=False)
    axes = axes[0]
    for ax, (metric, ylabel, logy) in zip(axes, panels):
        for system in sorted({c["system"] for c in rows}, key=system_rank):
            pts = sorted((c["x"], c.get(metric)) for c in rows
                         if c["system"] == system and c.get(metric) is not None)
            if not pts:
                continue
            color, marker = style(system)
            ys = [max(y, 1e-6) if logy else y for _, y in pts]
            ax.plot([x for x, _ in pts], ys, color=color, marker=marker, markersize=5,
                    linewidth=1.6, label=system)
        setup_axes(ax, xlabel, ylabel, logy, logx)
    axes[0].legend(frameon=False, fontsize=8)
    fig.suptitle(title, color=INK, fontsize=11, x=0.01, ha="left")
    save(fig, out, battery)


def bar_panels(plt, table, battery, panels, out, title, groups=None):
    rows = [c for c in table if c["battery"] == battery]
    if not rows:
        return
    groups = groups or sorted({str(c["x"]) for c in rows})
    systems = sorted({c["system"] for c in rows}, key=system_rank)
    fig, axes = plt.subplots(1, len(panels), figsize=(3.6 * len(panels), 3.4),
                             constrained_layout=True, squeeze=False)
    axes = axes[0]
    width = 0.8 / len(systems)
    for ax, (metric, ylabel, logy) in zip(axes, panels):
        for i, system in enumerate(systems):
            ys = []
            for g in groups:
                cell = next((c for c in rows if str(c["x"]) == g and c["system"] == system), {})
                ys.append(cell.get(metric) or 0)
            ax.bar([k + i * width for k in range(len(groups))], ys, width,
                   color=style(system)[0], label=system)
        ax.set_xticks([k + width * (len(systems) - 1) / 2 for k in range(len(groups))])
        ax.set_xticklabels(groups, fontsize=8)
        setup_axes(ax, "", ylabel, logy)
    axes[0].legend(frameon=False, fontsize=7)
    fig.suptitle(title, color=INK, fontsize=11, x=0.01, ha="left")
    save(fig, out, battery)


def latency_ccdf(plt, records, battery, out, title):
    """Tail of the pooled latency distribution per system: P(latency > t)."""
    if HdrHistogram is None:
        return
    by_system = collections.defaultdict(list)
    for r in records:
        if r["battery"] == battery:
            by_system[r["system"]] += r.get("hdrh", {}).get("all", [])
    if not by_system:
        return
    fig, ax = plt.subplots(figsize=(5.6, 3.6), constrained_layout=True)
    for system in sorted(by_system, key=system_rank):
        hist = HdrHistogram(1, 10 * 1000 * 1000, 3)
        for e in by_system[system]:
            hist.add(HdrHistogram.decode(e))
        total = hist.get_total_count()
        if not total:
            continue
        xs, ys = [], []
        for p in [50, 75, 90, 95, 99, 99.5, 99.9, 99.95, 99.99, 99.995, 99.999]:
            xs.append(hist.get_value_at_percentile(p))
            ys.append(1 - p / 100.0)
        ax.plot(xs, ys, color=style(system)[0], marker=style(system)[1], markersize=4,
                label=system)
    setup_axes(ax, "latency (us)", "fraction of packets slower", logy=True)
    ax.legend(frameon=False, fontsize=8)
    ax.set_title(title, color=INK, fontsize=11, loc="left")
    save(fig, out, battery + "_ccdf")


def timeseries(plt, records, battery, out, title):
    series = collections.defaultdict(list)
    for r in records:
        if r["battery"] == battery and r.get("series"):
            series[r["system"]].append(r["series"]["samples"])
    if not series:
        return
    fig, ax = plt.subplots(figsize=(6.4, 3.4), constrained_layout=True)
    for system in sorted(series, key=system_rank):
        by_second = collections.defaultdict(list)
        for samples in series[system]:
            for s in samples:
                by_second[round(s["t"])].append(s["rx_pps"] / max(s["tx_pps"], 1))
        xs = sorted(by_second)
        ax.plot(xs, [100 * statistics.mean(by_second[x]) for x in xs],
                color=style(system)[0], label=system, linewidth=1.6)
    setup_axes(ax, "time in the measured window (s)", "delivered / offered (%)")
    ax.legend(frameon=False, fontsize=8)
    ax.set_title(title, color=INK, fontsize=11, loc="left")
    save(fig, out, battery + "_series")


def save(fig, out, name):
    os.makedirs(out, exist_ok=True)
    for ext in ("png", "pdf"):
        fig.savefig(os.path.join(out, "%s.%s" % (name, ext)), dpi=180, facecolor="white")
    import matplotlib.pyplot as plt
    plt.close(fig)


def figures(table, records, out):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    bar_panels(plt, table, "f1", [("gbps", "delivered Gb/s", False),
                                  ("lat_avg", "mean latency (us)", False),
                                  ("pooled_all_p99.99", "p99.99 latency (us)", False),
                                  ("occ_mean", "ring occupancy (pkts)", False),
                                  ("dram_write", "DRAM write (MB/s)", True)],
               out, "Balanced load, 98% (rxBisect Fig. 10)")
    latency_ccdf(plt, records, "f1", out, "Balanced load: latency tail")
    bar_panels(plt, table, "f2", [("ddio_hit_pct", "NIC writes hitting the LLC (%)", False)],
               out, "DDIO hit rate")
    line_panels(plt, table, "f3", [("lat_avg", "mean latency (us)", False),
                                   ("lat_p99", "p99 (us)", False),
                                   ("pooled_all_p99.99", "p99.99 (us)", True)],
                "offered load (% of line rate)", out, "Latency against load")
    bar_panels(plt, table, "f4", [("loss_pct", "loss (%)", True),
                                  ("bystander_probe_loss_pct", "bystander loss (%)", True),
                                  ("pooled_special_p99.99", "stalled queue p99.99 (us)", False),
                                  ("pooled_bystander_p99.99", "bystanders p99.99 (us)", False)],
               out, "One lcore stalled 1 ms every 100 ms", groups=["cold", "hot", "twob"])
    line_panels(plt, table, "f5", [("loss_pct", "loss (%)", True),
                                   ("pooled_special_p99.99", "stalled queue p99.99 (us)", False)],
                "stall length (us)", out, "Stall length")
    line_panels(plt, table, "f6", [("gbps", "delivered Gb/s", False),
                                   ("bystander_probe_loss_pct", "bystander loss (%)", True),
                                   ("pooled_bystander_p99.99", "bystanders p99.99 (us)", True)],
                "target lcore's random reads per packet", out,
                "Processing variability (rxBisect Fig. 14)", logx=True)
    line_panels(plt, table, "f7", [("gbps", "delivered Gb/s", False),
                                   ("bystander_probe_loss_pct", "bystander loss (%)", True),
                                   ("pooled_bystander_p99.99", "bystanders p99.99 (us)", True)],
                "share of packets to the target queue", out,
                "Traffic variability (rxBisect Fig. 15)")
    for payload in (False, True):
        rows = [dict(c, x=int(re.sub(r"\D", "", c["x"]))) for c in table
                if c["battery"] == "f8" and str(c["x"]).endswith("p") == payload]
        line_panels(plt, rows, "f8", [("gbps", "delivered Gb/s", False),
                                      ("lat_avg", "mean latency (us)", True),
                                      ("fwd_cyc", "cycles / packet", False)],
                    "random reads per packet", os.path.join(out, "payload" if payload else "nopayload"),
                    "Memory intensity, payload %s (shRing Fig. 8)" % ("read" if payload else "untouched"))
    bar_panels(plt, table, "f9", [("ndr_pct", "no-drop rate (% of line)", False)], out,
               "No-drop rate, 1500 B (shRing Fig. 3, rxBisect Fig. 9)", groups=["multi", "single"])
    line_panels(plt, table, "f10", [("ndr_mpps", "no-drop rate (Mpps)", False)],
                "frame size (B)", out, "No-drop rate against frame size")
    line_panels(plt, table, "f11", [("gbps", "delivered Gb/s", False),
                                    ("loss_pct", "loss (%)", True),
                                    ("pooled_all_p99.99", "p99.99 (us)", True)],
                "STREAM workers on other cores", out, "Memory co-location (rxBisect Fig. 17)")
    bar_panels(plt, table, "f12", [("gbps", "delivered Gb/s", False),
                                   ("loss_pct", "loss (%)", True),
                                   ("pooled_all_p99.99", "p99.99 (us)", False)],
               out, "Time-varying imbalance (CAIDA-like, rxBisect Fig. 16)")
    timeseries(plt, records, "f12", out, "Time-varying imbalance, per second")
    timeseries(plt, records, "f13", out, "Load turns pathological at 20 s (shRing Fig. 13)")


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("data", help="directory with the files copied off both nodes")
    parser.add_argument("--out", help="write runs.csv, cells.csv and figures here")
    parser.add_argument("--battery", nargs="*", help="only these batteries, e.g. f1 f4")
    args = parser.parse_args()
    if HdrHistogram is None:
        print("WARNING: no hdrh module: pooled percentiles and tails are skipped "
              "(pip install hdrhistogram)", file=sys.stderr)

    records = windows(read_csv_rows(args.data), read_details(args.data),
                      read_summaries(args.data), read_ddio(args.data))
    if args.battery:
        records = [r for r in records if r["battery"] in args.battery]
    if not records:
        sys.exit("no f-battery runs found under %s" % args.data)
    table = cells(records)
    print_tables(table)
    if args.out:
        os.makedirs(args.out, exist_ok=True)
        flat = [{k: v for k, v in r.items() if k not in ("hdrh", "series", "window")}
                for r in records]
        write_csv(os.path.join(args.out, "runs.csv"), flat)
        write_csv(os.path.join(args.out, "cells.csv"), table)
        figures(table, records, args.out)
        print("wrote %s/runs.csv, cells.csv and figures" % args.out)


def write_csv(path, rows):
    fields = sorted({k for r in rows for k in r})
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


if __name__ == "__main__":
    main()
