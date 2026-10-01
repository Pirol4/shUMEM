#!/usr/bin/env python3
"""Packets lost per stall of one lcore: the model of CLAUDE.md 17.10 against
the measurements of 17.11, as a table on stdout and a figure.

Model (balanced load, one queue stalled for S us):
  privRing-N  loses  max(0, r_q * S - N)          only on the stalled queue
  shRing-8    loses  max(0, r_all * S - B)        spread over every queue
where r_q is one queue's arrival rate, r_all the NIC's, and B the 1,024
shared buffers. Nothing is fitted.

Usage:
  python3 analysis/stall_model.py [--out docs/figures/stall_loss]
"""
import argparse

# Measured on sm110p, 2026-10-01: 100% of line rate, 1500 B, 30 s, stalls
# every 100 ms on lcore 4 (queue 3) => 300 stalls inside the traffic window.
STALLS = 300
R_QUEUE_PER_US = 30_694_089 / 30e6      # queue 3's packets per us (17.10)
R_ALL_PER_US = 246_063_243 / 30e6       # whole NIC
SHRING_BUFFERS = 1024

STALL_US = [250, 500, 1000, 2000]
MEASURED_MISSED = {                      # l3fwd rx_missed_errors per run
    "privRing-1024": [0, 0, 2_074, 308_862],
    "privRing-128": [39_911, 116_648, 270_213, 577_140],
    "shRing-8": [335_119, 952_243, 2_183_169, 4_641_334],
}

# Fixed categorical order (blue, orange, aqua): one color per system,
# the same in every panel.
COLORS = {"privRing-1024": "#2a78d6", "privRing-128": "#eb6834", "shRing-8": "#1baf7a"}
INK, INK_MUTED, GRID = "#1f1f1e", "#6b6a63", "#e4e3dc"


def model_loss_per_stall(system, stall_us):
    if system == "shRing-8":
        return max(0.0, R_ALL_PER_US * stall_us - SHRING_BUFFERS)
    ring = int(system.split("-")[1])
    return max(0.0, R_QUEUE_PER_US * stall_us - ring)


def print_table():
    print("%-14s %8s %12s %12s %8s" % ("system", "stall_us", "model", "measured", "diff"))
    for system, values in MEASURED_MISSED.items():
        for stall_us, missed in zip(STALL_US, values):
            model = model_loss_per_stall(system, stall_us) * STALLS
            diff = "%+.1f%%" % (100 * (missed - model) / model) if model else "-"
            print("%-14s %8d %12.0f %12d %8s" % (system, stall_us, model, missed, diff))


def plot(out):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(1, 3, figsize=(10, 3.4), sharey=True, constrained_layout=True)
    curve_x = list(range(0, 2101, 10))
    for ax, system in zip(axes, MEASURED_MISSED):
        color = COLORS[system]
        ax.plot(curve_x, [model_loss_per_stall(system, s) for s in curve_x],
                color=color, linewidth=2, label="model")
        ax.plot(STALL_US, [m / STALLS for m in MEASURED_MISSED[system]],
                linestyle="none", marker="o", markersize=8, color=color,
                markeredgecolor="white", markeredgewidth=2, label="measured")
        ax.set_title(system, color=INK, fontsize=11, loc="left")
        ax.set_xlabel("stall length (µs)", color=INK_MUTED)
        ax.set_xlim(0, 2100)
        ax.grid(axis="y", color=GRID, linewidth=0.8)
        ax.set_axisbelow(True)
        for side in ("top", "right"):
            ax.spines[side].set_visible(False)
        for side in ("left", "bottom"):
            ax.spines[side].set_color(GRID)
        ax.tick_params(colors=INK_MUTED, labelsize=9)
    axes[0].set_ylabel("packets lost per stall", color=INK_MUTED)
    axes[0].set_ylim(bottom=0)
    axes[0].yaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: "{:,.0f}".format(v)))
    axes[0].legend(frameon=False, fontsize=9, labelcolor=INK)
    fig.suptitle("One stalled lcore under balanced load: shRing spreads the loss over every queue",
                 color=INK, fontsize=11, x=0.01, ha="left")
    for ext in ("png", "pdf"):
        fig.savefig("%s.%s" % (out, ext), dpi=200, facecolor="white")
    print("wrote %s.png and %s.pdf" % (out, out))


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", default="docs/figures/stall_loss")
    args = parser.parse_args()
    print_table()
    plot(args.out)


if __name__ == "__main__":
    main()
