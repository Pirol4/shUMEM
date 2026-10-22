#!/usr/bin/env python3
"""Write the paper-style evaluation batteries f0-f15 (CLAUDE.md 20) as sweep files.

Every battery is a set of cells (system x one value of its variable), repeated
in rounds: round k runs every cell once, in an order shuffled with a fixed
seed, so slow drift on the node (19.22) spreads over all systems and values
instead of landing on whichever ran last. Labels carry the round as _n<k>
(sweep.sh's own _r<k> suffix only marks a label used twice).

Common to every measured run unless a battery says otherwise: 1500 B frames,
98% of line rate, a 10 s warm-up window before the measured one, and a
latency stream of 10 kpps on every Rx queue with TRex's 1 us HDR histogram.

Usage: python3 harness/sweeps/eval2/make_batteries.py   (rewrites f*.txt here)
"""
import os
import random

HERE = os.path.dirname(os.path.abspath(__file__))

# The systems of the comparison. shRing is the artifact with its head-advance
# race fixed (rmp_fix=1, 19.25-19.27), which only makes the baseline stronger;
# the published shring-8 appears where its wedge is part of the result (f4).
CORE = ["privring-1024", "privring-128", "shringfix-8", "fill-128", "dyn-128"]

LAT = "--lat-queues all --lat-pps 10000 --hdrh required"
LAT_NDR = "--lat-queues all --lat-pps 1000 --hdrh required"
WARM = "--warmup 10"
QUEUES = 8

# Target core of the variability batteries: lcore 4 serves queue 3, the queue
# every stall test has used (17.10).
TARGET_LCORE, TARGET_QUEUE = 4, 3


def weights(target_weight, queue=TARGET_QUEUE, others=1.0):
    return ",".join(str(round(target_weight, 6)) if q == queue else str(others)
                    for q in range(QUEUES))


def sizes(target_size, queue=TARGET_QUEUE, others=1500):
    return ",".join(str(target_size if q == queue else others) for q in range(QUEUES))


def line(label, system, options):
    return "%-40s %-14s -  -  %s" % (label, system, options)


def rounds(battery, cells, n):
    """cells: [(name, system, options)]; n rounds, each shuffled."""
    out = []
    for k in range(1, n + 1):
        order = list(cells)
        random.Random("%s-%d" % (battery, k)).shuffle(order)
        out.append("# round %d" % k)
        out += [line("%s_%s_%s_n%02d" % (battery, name, system, k), system,
                     options.replace("{k}", str(k)))
                for name, system, options in order]
    return out


def write(name, header, body):
    path = os.path.join(HERE, name)
    with open(path, "w") as f:
        f.write("".join("# %s\n" % h if h else "#\n" for h in header))
        f.write("#\n# label  system  window_ms  headroom_pct  options / extra rate_sweep.py args\n")
        f.write("\n".join(body) + "\n")
    runs = sum(1 for b in body if b and not b.startswith("#"))
    print("%-24s %4d runs" % (name, runs))


def f0():
    cells = []
    for s in CORE + ["shring-8"]:
        cells.append(line("f0_occ_" + s, s, "rates=98 duration=10 %s %s" % (WARM, LAT)))
    cells += [
        line("f0_work2_dyn-128", "dyn-128", "rates=98 duration=10 work=2 %s" % LAT),
        line("f0_payload_privring-1024", "privring-1024", "rates=98 duration=10 payload=1 %s" % LAT),
        line("f0_pv800_shringfix-8", "shringfix-8", "rates=98 duration=10 work=2 target=4:800 "
             "--queue-weights %s %s" % (weights(0.0722), LAT)),
        line("f0_tv20_dyn-128", "dyn-128", "rates=98 duration=10 work=2 --queue-weights %s "
             "--queue-sizes %s %s" % (weights(0.2 / 0.8 * 7), sizes(60), LAT)),
        line("f0_skew_dyn-128", "dyn-128", "rates=98 duration=10 work=2 --queue-weights %s "
             "--schedule skew:1 --timeseries %s" % (weights(1), LAT)),
        line("f0_hog4_privring-1024", "privring-1024", "rates=98 duration=10 work=2 hog=4 %s" % LAT),
        line("f0_ndr1q_dyn-128", "dyn-128", "duration=10 --queue-weights 1,0,0,0,0,0,0,0 "
             "--search 10,100 --resolution 5 %s" % LAT_NDR),
        line("f0_ddio_dyn-128", "dyn-128", "rates=98 duration=10 pcm=pcie %s" % LAT),
    ]
    write("f0_smoke.txt", [
        "Smoke test of everything new (~20 min). Read its summary before the rest:",
        "every LAT line has source hdrh (p99.99 not a multiple of 10) and lost=0",
        "under balanced load; OCCUPANCY lines for every system, shring-8 included;",
        "WORK lines with work_cyc/pkt; the tv20 run shows queue 3 at ~20% of the",
        "packets; the skew run changes rates every second (series in the .jsonl);",
        "the hog run has a hog: line; the ndr1q run ends with an NDR line; the",
        "ddio run writes pcm-pcie_*.csv.",
    ], cells)


def f1():
    cells = [("bal", s, "rates=98 duration=60 %s %s" % (WARM, LAT)) for s in CORE]
    write("f1_balanced.txt", [
        "Balanced load, 98%, 60 s after a 10 s warm-up, n = 10: throughput, loss,",
        "latency per queue (p50-p99.99), ring occupancy, DRAM, cycles. The",
        "shRing paper's Fig. 9 and the rxBisect paper's Fig. 10 with l3fwd as the NF.",
    ], rounds("f1", cells, 10))


def f2():
    cells = [("ddio", s, "rates=98 duration=30 pcm=pcie %s %s" % (WARM, LAT)) for s in CORE]
    write("f2_ddio.txt", [
        "DDIO hit rate (pcm-pcie) under balanced load, n = 5 (Fig. 9d / 10d).",
    ], rounds("f2", cells, 5))


def f3():
    rates = "10,30,50,70,80,90,95,98,100"
    cells = [("load", s, "rates=%s duration=20 %s %s" % (rates, WARM, LAT)) for s in CORE]
    write("f3_load_latency.txt", [
        "Latency against offered load, 10-100%% of line rate (%s), 20 s each," % rates,
        "n = 5: where each system's tail starts to grow. Not in the papers, which",
        "report latency at full load only.",
    ], rounds("f3", cells, 5))


def f4():
    tests = {
        "cold": "stall=4:1000:100",
        "hot": "stall=1:1000:100 --hot-share 0.5",
        "twob": "stall=4:1000:100 --hot-share 0.5",
    }
    systems = CORE + ["shring-8"]
    cells = [(t, s, "rates=98 duration=30 %s %s %s" % (opt, WARM, LAT))
             for t, opt in tests.items() for s in systems]
    write("f4_stalls.txt", [
        "One lcore stalled 1 ms every 100 ms (stall_core.py), n = 10: cold core",
        "(lcore 4), the hot queue's core (lcore 1, hot share 0.5), and two",
        "borrowers (hot queue 0 plus lcore 4 stalled). Loss per queue and, new,",
        "the latency of the stalled queue and of every bystander. shring-8 (the",
        "published code) beside shringfix-8: its wedge is part of the result.",
    ], rounds("f4", cells, 10))


def f5():
    cells = [("len%d" % us, s, "rates=98 duration=30 stall=4:%d:100 %s %s" % (us, WARM, LAT))
             for us in (100, 250, 500, 750, 1000, 1500, 2000) for s in CORE]
    write("f5_stall_len.txt", [
        "Cold-core stall from 100 us to 2 ms every 100 ms, n = 5 (19.27 item 8",
        "had n = 1): where each system starts to lose, and how much.",
    ], rounds("f5", cells, 5))


def f6():
    # The target queue gets ~1 Gbps: weight w against 1 on each of the other
    # seven gives it w / (7 + w) of 98 Gbps; w = 0.0722 -> 1.0 Gbps.
    accesses = (0, 50, 100, 200, 400, 800, 1600, 2400, 3200, 4800, 6400)
    cells = [("pv%d" % a, s, "rates=98 duration=30 work=2 target=%d:%d --queue-weights %s %s %s"
              % (TARGET_LCORE, a, weights(0.0722), WARM, LAT))
             for a in accesses for s in CORE]
    write("f6_proc_var.txt", [
        "Processing variability (shRing Fig. 11, rxBisect Fig. 14): every lcore",
        "does 2 random reads per packet in a 40 MiB buffer; lcore 4 (queue 3, ~1",
        "Gbps of the traffic) does %s." % "/".join(str(a) for a in accesses),
        "When the target cannot keep up, shRing's",
        "shared ring blocks every queue; per-queue rings and credits should lose",
        "only the target's excess. n = 5.",
    ], rounds("f6", cells, 5))


def f7():
    shares = (0.01, 0.05, 0.10, 0.15, 0.20, 0.25, 0.30, 0.40, 0.50)
    cells = []
    for f in shares:
        # f of the packets to the target queue: weight w with w / (7 + w) = f.
        w = 7 * f / (1 - f)
        cells += [("tv%02d" % round(100 * f), s,
                   "rates=98 duration=30 work=2 --queue-weights %s --queue-sizes %s %s %s"
                   % (weights(w), sizes(60), WARM, LAT)) for s in CORE]
    write("f7_traffic_var.txt", [
        "Traffic variability (shRing Fig. 12, rxBisect Fig. 15): a share of the",
        "packets (%s) goes to queue 3 as 64 B frames, the rest" % "/".join(
            "%g%%" % (100 * f) for f in shares),
        "as 1500 B frames to the other seven; every lcore does 2 random reads per",
        "packet. The target's bandwidth stays small; the question is whether its",
        "overload spreads to the other queues. n = 5.",
    ], rounds("f7", cells, 5))


def f8():
    cells = []
    for payload in (0, 1):
        for work in (0, 1, 2, 4, 8, 16):
            name = "mi%d%s" % (work, "p" if payload else "")
            cells += [(name, s, "rates=98 duration=30 work=%d payload=%d %s %s"
                       % (work, payload, WARM, LAT)) for s in CORE]
    write("f8_mem_intensity.txt", [
        "Memory intensity of the NF (shRing Fig. 7/8): every lcore does 0-16",
        "random reads per packet in 40 MiB, without and with reading the payload",
        "(the read is where an I/O working set beyond the LLC costs the CPU).",
        "Balanced, 98%: delivered throughput, loss, latency, cycles. n = 5.",
    ], rounds("f8", cells, 5))


def f9():
    systems = ["privring-64", "privring-128", "privring-256", "privring-512", "privring-1024",
               "fill-128", "dyn-32-8", "dyn-64-16", "dyn-128", "shringfix-8"]
    cells = []
    for s in systems:
        cells.append(("multi", s, "duration=10 --search 50,100 %s %s" % (WARM, LAT_NDR)))
        cells.append(("single", s, "duration=10 --queue-weights 1,0,0,0,0,0,0,0 "
                      "--search 10,100 %s %s" % (WARM, LAT_NDR)))
    write("f9_ndr_ring.txt", [
        "RFC 2544 no-drop rate (loss <= 1e-6, 10 s trials), 1500 B, with traffic",
        "spread over 8 cores (multi) or all on queue 0 (single): shRing Fig. 3,",
        "rxBisect Fig. 9 and 12 (buffer sharing absorbs a single core's bursts).",
        "privRing at 64-1024 entries against the shared designs with 1024 or",
        "fewer buffers in total (dyn-E-F: E per queue, 8E credits, floor F, so one",
        "queue can hold 8E - 7F). n = 3.",
    ], rounds("f9", cells, 3))


def f10():
    cells = [("sz%d" % (size + 4), s, "duration=10 --pkt-size %d --search 5,100 %s %s"
              % (size, WARM, LAT_NDR))
             for size in (60, 124, 252, 508, 1020) for s in CORE]
    write("f10_ndr_size.txt", [
        "No-drop rate at 64, 128, 256, 512 and 1024 B frames (1500 B is in f9),",
        "8 cores, n = 3: the per-packet cost of each Rx path (shRing caps at",
        "~19.6 Mpps, 19.27).",
    ], rounds("f10", cells, 3))


def f11():
    cells = [("hog%d" % h, s, "rates=98 duration=30 work=2 hog=%d %s %s" % (h, WARM, LAT))
             for h in (0, 1, 2, 4, 7) for s in CORE]
    write("f11_colocation.txt", [
        "Co-located memory-bandwidth load (rxBisect Fig. 17): 0-7 stress-ng",
        "STREAM workers on CPUs 9-15 while the NF (2 random reads per packet)",
        "forwards 98% balanced. n = 5.",
    ], rounds("f11", cells, 5))


def f12():
    cells = [("skew%s" % ("hog4" if hog else ""), s,
              "rates=98 duration=60 work=2 %s--queue-weights %s --schedule skew:{k} "
              "--timeseries %s %s" % ("hog=4 " if hog else "", weights(1), WARM, LAT))
             for hog in (0, 4) for s in CORE]
    write("f12_skew.txt", [
        "Imbalance like the CAIDA trace of rxBisect Fig. 4/16 (CLAUDE.md 20.2): per-",
        "queue rates redrawn every second with max/min 3.25-4.33, busy queues",
        "persisting (schedule.py). Round k uses seed k for every system, so the",
        "systems see the same sequence. With and without 4 STREAM workers. n = 5.",
    ], rounds("f12", cells, 5))


def f13():
    # Before: the target gets ~1 Gbps (weight 0.0722); from 20 s on, its fair
    # share (weight 1) with 800 reads per packet, more than lcore 4 can serve.
    cells = [("switch", s, "rates=98 duration=40 work=2 target=%d:800 --queue-weights %s "
              "--schedule switch:20:%s --timeseries %s %s"
              % (TARGET_LCORE, weights(0.0722), weights(1), WARM, LAT)) for s in CORE]
    write("f13_switch.txt", [
        "A load that turns pathological mid-run (shRing Fig. 13): per-second",
        "throughput and loss when, at 20 s, the slow target lcore starts to get",
        "its full share of the traffic. n = 5.",
    ], rounds("f13", cells, 5))


def f14():
    cells = []
    for cache, ops, groups in ((32, "ring_mp_mc", 1), (32, "stack", 1), (32, "ring_mp_mc", 4),
                               (32, "stack", 4), (0, "ring_mp_mc", 4), (0, "stack", 4)):
        name = "c%d%s%d" % (cache, "s" if ops == "stack" else "r", groups)
        cells += [(name, s, "rates=98 duration=30 cache=%d ops=%s groups=%d %s %s"
                   % (cache, ops, groups, WARM, LAT))
                  for s in ("privring-128", "shringfix-8", "dyn-128")]
    write("f14_mempool.txt", [
        "The shared mempool (19.27 item 6, n = 1 then): per-lcore cache 32/0,",
        "FIFO ring or LIFO stack, 1 or 4 pools. n = 3.",
    ], rounds("f14", cells, 3))


def f15():
    cells = [("hotmv", s, "rates=98 duration=60 --hot-share 0.5 --hot-move 5 %s %s"
              % (WARM, LAT)) for s in CORE]
    cells += [("hot", s, "rates=98 duration=30 --hot-share 0.5 %s %s" % (WARM, LAT))
              for s in CORE]
    write("f15_hot.txt", [
        "One hot queue (half the traffic) fixed and moving every 5 s, no stall,",
        "n = 3: the 19.27 control that a hot queue on a clean core costs nothing.",
    ], rounds("f15", cells, 3))


def main():
    for battery in (f0, f1, f2, f3, f4, f5, f6, f7, f8, f9, f10, f11, f12, f13, f14, f15):
        battery()


if __name__ == "__main__":
    main()
