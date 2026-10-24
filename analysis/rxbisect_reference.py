#!/usr/bin/env python3
"""rxBisect's published numbers, and what they project onto our testbed (CLAUDE.md 20.8).

rxBisect (Pismenny et al., OSDI '25) cannot run here (an ASIC change; its
emulator was never published, CLAUDE.md 11), so it is compared *indirectly*:
every experiment of its paper also measures baselines we measure (shRing,
small privRing, privRing), and the ratio rxBisect / baseline from the paper,
times that baseline measured here, is an estimate of rxBisect on this node:

    rxBisect_est = ours[anchor] * paper[rxBisect] / paper[anchor]

computed through two anchors, shRing and small privRing (privring-128). The
transfer only holds when the anchor is in the same regime in both papers
(e.g. shRing collapsed by the same fraction), so each estimate says whether
its anchor's normalised value here is within REGIME_TOL of the paper's; an
anchor outside it is reported and not used. The two anchors' spread is the
estimate's uncertainty.

Read every number below as the paper's, read off its figures (uncertainty
given per figure) or quoted from its text. rxBisect was *emulated* there; by
the paper's own Table 2 emulation costs up to 12% throughput and 94% latency,
so the estimates are lower bounds for rxBisect in hardware: a margin smaller
than that is not a win over rxBisect.

Usage: python3 analysis/rxbisect_reference.py <cells.csv from eval_report.py>
"""
import csv
import sys

REGIME_TOL = 0.10     # anchors' normalised values may differ this much

# --- the paper's numbers ------------------------------------------------------------

# Fig. 14, processing variability: Gb/s of 2 NICs (200 Gb/s) against the
# target core's memory accesses per packet. Read off the figure, +-2 Gb/s.
FIG14_X = [0, 25, 50, 75, 100, 125, 150, 175, 200, 225, 250, 275, 300]
FIG14 = {
    "rxbisect": [194] * 13,
    "small_privring": [195] * 13,
    "privring": [174, 173, 172, 172, 171, 171, 171, 171, 171, 171, 171, 171, 171],
    "shring": [195, 195, 195, 195, 195, 182, 154, 133, 118, 106, 96, 88, 81],
    "dshring": [195, 195, 195, 195, 195, 182, 171, 171, 171, 171, 171, 171, 171],
}
FIG14_KNEE = 112      # accesses where shRing starts to fall (between 100 and 125)

# Fig. 15, traffic variability: Gb/s against the % of packets (64 B) sent to
# the target core. +-2 Gb/s; the rxBisect drop past 20% is its emulator (text).
FIG15_X = [0, 5, 10, 15, 18.5, 22, 25.5, 28.5, 31.5]
FIG15 = {
    "rxbisect": [194, 194, 193, 191, 189, 188, 186, 184, 183],
    "small_privring": [195, 195, 194, 194, 193, 192, 191, 191, 192],
    "privring": [174, 173, 171, 171, 169, 169, 168, 168, 168],
    "shring": [195, 195, 194, 194, 180, 150, 129, 112, 100],
}

# Fig. 9, l3fwd RFC 2544 no-drop rate, 1500 B, % of one 100 Gb/s NIC. +-2.
FIG9 = {
    "single": {"privring": 80, "small_privring": 40, "shring": 80, "rxbisect": 82},
    "multi": {"privring": 97, "small_privring": 98, "shring": 97, "rxbisect": 97},
}

# Fig. 10, LB / NAT at 200 Gb/s: throughput Gb/s, ring occupancy %, memory
# bandwidth % (of the node's peak), DDIO hit %. +-2 (bars); latency from
# Table 2 (non-emulated) and the text ("100-119 us" for small working sets).
FIG10 = {
    "LB": {"throughput": {"privring": 165, "small_privring": 195, "shring": 196, "rxbisect": 196},
           "occupancy": {"privring": 93, "small_privring": 2, "shring": 3, "rxbisect": 8},
           "membw": {"privring": 40, "small_privring": 12, "shring": 13, "rxbisect": 25},
           "ddio": {"privring": 12, "small_privring": 84, "shring": 82, "rxbisect": 67}},
    "NAT": {"throughput": {"privring": 158, "small_privring": 196, "shring": 195, "rxbisect": 196},
            "occupancy": {"privring": 81, "small_privring": 2, "shring": 3, "rxbisect": 10},
            "membw": {"privring": 38, "small_privring": 14, "shring": 15, "rxbisect": 26},
            "ddio": {"privring": 11, "small_privring": 85, "shring": 80, "rxbisect": 66}},
}

# Table 2, emulation fidelity (exact): Gb/s and us, non-emulated / emulated.
TABLE2 = {
    ("LB", "privring"): ((162, 145), (1224, 1432)),
    ("LB", "small_privring"): ((195, 192), (110, 96)),
    ("LB", "shring"): ((194, 195), (69, 69)),
    ("NAT", "privring"): ((155, 136), (1256, 2431)),
    ("NAT", "small_privring"): ((196, 179), (89, 124)),
    ("NAT", "shring"): ((195, 195), (67, 67)),
}

# Fig. 12, l3fwd no-drop Gb/s, 4 cores, one flow, against Ax (rxBisect) or
# Rx (privRing) size; Bx = 1 Ki. +-2.
FIG12_X = [64, 128, 256, 512, 1024, 2048]
FIG12 = {"rxbisect": [45, 60, 81, 81, 81, 81], "privring": [22, 39, 47, 61, 80, 80]}

# Fig. 17, Mpps with 0-3 co-located STREAM instances (CAIDA trace). +-0.3.
FIG17_X = [0, 1, 2, 3]
FIG17 = {
    "NAT": {"rxbisect": [17.7, 17.5, 17.0, 15.9], "dshring": [19.0, 17.8, 16.2, 14.6],
            "dshring_emulated": [16.5, 15.1, 14.7, 14.2]},
    "LB": {"rxbisect": [18.4, 18.1, 17.6, 16.6], "dshring": [19.4, 18.0, 16.4, 14.7],
           "dshring_emulated": [17.1, 15.5, 15.3, 14.7]},
}

# Quoted from the text.
TEXT = {
    "fig16_gain_over_dshring": {"LB": 0.16, "NAT": 0.20},
    "fig18_shring_cycles_over_privring": 0.34,        # up to, non-emulated
    "fig18_shring_cycles_over_small_privring": 0.46,
    "fig18_rxbisect_cycles_vs_shring": -0.10,         # rxBisect ~10% fewer
    "table3_allocator_cycles_max": 0.002,             # < 0.2% of all cycles
    "emulation_cost": {"throughput": 0.12, "latency": 0.94},
}

# Our systems for the paper's names.
OURS = {"rxbisect_counterpart": "dyn-128", "shring": "shringfix-8",
        "small_privring": "privring-128", "privring": "privring-1024"}


# --- projection ---------------------------------------------------------------------

def load_cells(path):
    cells = {}
    for row in csv.DictReader(open(path)):
        cells[(row["battery"], row["x"], row["system"])] = row
    return cells


def value(cells, battery, x, system, metric="gbps"):
    row = cells.get((battery, str(x), system))
    if row is None or row.get(metric) in (None, ""):
        return None
    return float(row[metric])


def interpolate(xs, ys, x):
    if x <= xs[0]:
        return ys[0]
    for (x0, y0), (x1, y1) in zip(zip(xs, ys), zip(xs[1:], ys[1:])):
        if x <= x1:
            return y0 + (y1 - y0) * (x - x0) / (x1 - x0)
    return ys[-1]


def project(paper, ours_norm, paper_norm):
    """Estimates of rxBisect's normalised value through each anchor."""
    out = {}
    for anchor in ("small_privring", "shring"):
        ours_a, paper_a = ours_norm.get(anchor), paper_norm.get(anchor)
        if ours_a is None or paper_a is None:
            continue
        in_regime = abs(ours_a - paper_a) <= REGIME_TOL
        out[anchor] = (ours_a * paper_norm["rxbisect"] / paper_a, in_regime)
    return out


def processing_variability(cells):
    """Fig. 14 against f6, x normalised by where shRing starts to fall."""
    print("\n== Fig. 14 (processing variability) against f6, throughput / its value at x = 0")
    print("   x mapped by the knee: paper %d accesses, ours %d (f6: shRing intact at 1600, "
          "down at 2400)" % (FIG14_KNEE, OUR_F6_KNEE))
    print("%8s %6s | %7s %7s %7s | %8s %8s | %15s %15s" % (
        "ours x", "x/knee", "dyn", "small", "shRing", "pap rxB", "pap shR",
        "rxB est (small)", "rxB est (shR)"))
    for x in (0, 800, 1600, 2400, 3200, 4800, 6400):
        ours = {name: value(cells, "f6", x, OURS[key]) for key, name in
                (("rxbisect_counterpart", "dyn"), ("small_privring", "small_privring"),
                 ("shring", "shring"))}
        base = {name: value(cells, "f6", 0, OURS[key]) for key, name in
                (("rxbisect_counterpart", "dyn"), ("small_privring", "small_privring"),
                 ("shring", "shring"))}
        if any(v is None for v in list(ours.values()) + list(base.values())):
            continue
        norm = {k: ours[k] / base[k] for k in ours}
        px = x / OUR_F6_KNEE * FIG14_KNEE
        paper_norm = {k: interpolate(FIG14_X, ys, min(px, FIG14_X[-1])) / ys[0]
                      for k, ys in FIG14.items()}
        est = project(FIG14, norm, paper_norm)
        print("%8d %6.2f | %7.3f %7.3f %7.3f | %8.3f %8.3f | %15s %15s" % (
            x, x / OUR_F6_KNEE, norm["dyn"], norm["small_privring"], norm["shring"],
            paper_norm["rxbisect"], paper_norm["shring"],
            fmt_est(est.get("small_privring")), fmt_est(est.get("shring"))))
    print("   (the paper's axis ends at 300 = %.1f knees; beyond it the paper's last point is used)"
          % (FIG14_X[-1] / FIG14_KNEE))


def traffic_variability(cells):
    """Fig. 15 against f7, x = share of packets to the target."""
    print("\n== Fig. 15 (traffic variability) against f7, throughput / its value at 1%")
    print("%6s | %7s %7s %7s | %8s %8s | %15s %15s" % (
        "share", "dyn", "small", "shRing", "pap rxB", "pap shR", "rxB est (small)",
        "rxB est (shR)"))
    for share in ("0.05", "0.1", "0.15", "0.2", "0.25", "0.3", "0.4", "0.5"):
        names = (("rxbisect_counterpart", "dyn"), ("small_privring", "small_privring"),
                 ("shring", "shring"))
        ours = {n: value(cells, "f7", share, OURS[k]) for k, n in names}
        base = {n: value(cells, "f7", "0.01", OURS[k]) for k, n in names}
        if any(v is None for v in list(ours.values()) + list(base.values())):
            continue
        norm = {k: ours[k] / base[k] for k in ours}
        px = min(float(share) * 100, FIG15_X[-1])
        paper_norm = {k: interpolate(FIG15_X, ys, px) / ys[0] for k, ys in FIG15.items()}
        est = project(FIG15, norm, paper_norm)
        print("%6s | %7.3f %7.3f %7.3f | %8.3f %8.3f | %15s %15s" % (
            share, norm["dyn"], norm["small_privring"], norm["shring"],
            paper_norm["rxbisect"], paper_norm["shring"],
            fmt_est(est.get("small_privring")), fmt_est(est.get("shring"))))
    print("   (paper's rxBisect drop past 20% is its emulator core, by its own text; past 31.5% "
          "the paper's last point is used)")


def balanced(cells):
    """Fig. 10 against f1: ratios to shRing, the one anchor with a small working set."""
    print("\n== Fig. 10 (balanced, full load) against f1: each system / shRing")
    dyn_mem = value(cells, "f1", "bal", OURS["rxbisect_counterpart"], "dram_write")
    shr_mem = value(cells, "f1", "bal", OURS["shring"], "dram_write")
    small_mem = value(cells, "f1", "bal", OURS["small_privring"], "dram_write")
    for nf, fig in FIG10.items():
        rb = fig["membw"]["rxbisect"] / fig["membw"]["shring"]
        sp = fig["membw"]["small_privring"] / fig["membw"]["shring"]
        print("   memory bandwidth, %s: paper rxBisect/shRing %.2f (small privRing %.2f)"
              % (nf, rb, sp))
    if dyn_mem and shr_mem:
        print("   DRAM write here: dyn-128/shRing %.2f (privring-128 %.2f) — paper's rxBisect "
              "is emulated, so its ratio overstates hardware rxBisect" % (
                  dyn_mem / shr_mem, small_mem / shr_mem if small_mem else float("nan")))
    for nf, fig in FIG10.items():
        print("   occupancy, %s: paper rxBisect %d%% shRing %d%%; DDIO hit rxBisect %d%% shRing %d%%"
              % (nf, fig["occupancy"]["rxbisect"], fig["occupancy"]["shring"],
                 fig["ddio"]["rxbisect"], fig["ddio"]["shring"]))
    dyn_ddio = value(cells, "f2", "ddio", OURS["rxbisect_counterpart"], "ddio_hit_pct")
    shr_ddio = value(cells, "f2", "ddio", OURS["shring"], "ddio_hit_pct")
    if dyn_ddio and shr_ddio:
        print("   DDIO hit: paper rxBisect/shRing %.2f (LB) %.2f (NAT), emulated; here dyn-128 "
              "%.1f%% / shRing %.1f%% = %.3f" % (
                  FIG10["LB"]["ddio"]["rxbisect"] / FIG10["LB"]["ddio"]["shring"],
                  FIG10["NAT"]["ddio"]["rxbisect"] / FIG10["NAT"]["ddio"]["shring"],
                  dyn_ddio, shr_ddio, dyn_ddio / shr_ddio))
    dyn_cyc = value(cells, "f1", "bal", OURS["rxbisect_counterpart"], "fwd_cyc")
    shr_cyc = value(cells, "f1", "bal", OURS["shring"], "fwd_cyc")
    if dyn_cyc and shr_cyc:
        print("   cycles per packet: paper (Fig. 18, low load) rxBisect ~%.2f x shRing; here "
              "dyn-128 %.2f x shRing (f1, full load, fwd cycles)" % (
                  1 + TEXT["fig18_rxbisect_cycles_vs_shring"], dyn_cyc / shr_cyc))


def no_drop(cells):
    print("\n== Fig. 9 (l3fwd no-drop, 1500 B) against f9, %% of line rate")
    for mode in ("multi", "single"):
        paper = FIG9[mode]
        ours = {k: value(cells, "f9", mode, OURS[k], "ndr_pct")
                for k in ("small_privring", "shring", "privring")}
        dyn = value(cells, "f9", mode, OURS["rxbisect_counterpart"], "ndr_pct")
        print("   %-6s paper: rxBisect %d, small %d, shRing %d, privRing %d | here: dyn %s, "
              "small %s, shRing %s, privRing %s" % (
                  mode, paper["rxbisect"], paper["small_privring"], paper["shring"],
                  paper["privring"], fmt(dyn), fmt(ours["small_privring"]),
                  fmt(ours["shring"]), fmt(ours["privring"])))
    print("   single core: small privRing 40% there and ~100% here — another regime (isolated "
          "cores never pause here), so no anchor transfers; compare with stalls (f4/f5)")


def fmt(v):
    return "-" if v is None else "%.1f" % v


def fmt_est(est):
    if est is None:
        return "-"
    v, ok = est
    if v > 1.0 + 1e-3:
        return "%.3f (>1, invalid)" % v
    return "%.3f%s" % (v, "" if ok else " (off-regime)")


OUR_F6_KNEE = 1700    # f6: shRing intact at 1600 accesses, collapsed at 2400


def main():
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    cells = load_cells(sys.argv[1])
    print(__doc__.split("\n\n")[0])
    balanced(cells)
    no_drop(cells)
    processing_variability(cells)
    traffic_variability(cells)


if __name__ == "__main__":
    main()
