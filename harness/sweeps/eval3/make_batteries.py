#!/usr/bin/env python3
"""Write the 200 Gb/s batteries g0-g7 (CLAUDE.md 21) as sweep files.

Two experiment links, 2 x 100 Gb/s, on sm220u: every run uses both ports
(PORTS=2; run_eval.sh sets it for g* batteries), the same profile on each, at
98% of each port's line rate -- 196 Gb/s in all, the rxBisect paper's load.
The dut runs 8 queues per port on 16 cores of the NIC's socket
(setup/topology.py); every system is per port (shRing: one RMP of 1,024 per
port, 8 queues sharing it, as the paper's two shared rings; dyn: one pool of
1,024 credits per port).

The cells mirror the 100 Gb/s batteries they repeat (eval2/make_batteries.py:
rounds shuffled with a fixed seed, 10 s warm-up, a 10 kpps latency stream on
every queue of both ports); named lcores are p<port>q<queue>, resolved on the
node. The question is what doubling the load and the posted buffers (16
queues, privring-1024's 24 MiB against a 24 MiB LLC) does to each system.

Usage: python3 harness/sweeps/eval3/make_batteries.py   (rewrites g*.txt here)
"""
import importlib.util
import os

HERE = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location(
    "eval2_batteries", os.path.join(HERE, "..", "eval2", "make_batteries.py"))
eval2 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(eval2)

CORE, LAT, WARM, MAWI = eval2.CORE, eval2.LAT, eval2.WARM, eval2.MAWI
line, rounds, weights = eval2.line, eval2.rounds, eval2.weights

# Port 0's queue 3 is the stalled or target queue, as queue 3 at 100 Gb/s;
# the variability battery has one target per port, the paper's "one target
# core per NIC".
STALL = "p0q3"
TARGETS = "p0q3,p1q3"


def write(name, header, body):
    path = os.path.join(HERE, name)
    with open(path, "w") as f:
        f.write("".join("# %s\n" % h if h else "#\n" for h in header))
        f.write("#\n# Needs PORTS=2 and TGEN_MAC2 (run_eval.sh sets PORTS=2 for g*).\n")
        f.write("#\n# label  system  window_ms  headroom_pct  options / extra rate_sweep.py args\n")
        f.write("\n".join(body) + "\n")
    runs = sum(1 for b in body if b and not b.startswith("#"))
    print("%-24s %4d runs" % (name, runs))


def g0():
    cells = [line("g0_bal_" + s, s, "rates=50,98 duration=20 %s %s" % (WARM, LAT)) for s in CORE]
    cells += [
        line("g0_heavy_dyn-128", "dyn-128", "rates=98 duration=20 work=48 payload=1 %s %s"
             % (WARM, LAT)),
        line("g0_stall_shringfix-8", "shringfix-8", "rates=98 duration=20 stall=%s:1000:100 %s %s"
             % (STALL, WARM, LAT)),
        line("g0_pv1600_dyn-128", "dyn-128", "rates=98 duration=20 work=2 target=%s:1600 "
             "--queue-weights %s %s %s" % (TARGETS, weights(0.0722), WARM, LAT)),
        line("g0_trace_fill-128", "fill-128", "rates=98 duration=20 --trace %s %s %s"
             % (MAWI, WARM, LAT)),
    ]
    write("g0_smoke.txt", [
        "Smoke of the 200 Gb/s layout (~12 min), read before the rest: both ports",
        "carry traffic (per queue rx (port 0) and (port 1) both filled); 50% and 98%",
        "of 2 x 100 Gb/s without loss for privring-128, fill-128 and dyn-128 (if",
        "privring-1024 loses at 98% here, it is the doubled working set or PCIe,",
        "not the setup: compare its 50% window); latency streams on p0q0-p1q7;",
        "the stall line names lcore of p0q3, the target line two lcores (WORK).",
    ], cells)


def g1():
    cells = [("bal", s, "rates=98 duration=60 %s %s" % (WARM, LAT)) for s in CORE]
    write("g1_balanced.txt", [
        "Balanced load at 2 x 98 Gb/s, 60 s after a 10 s warm-up, n = 5: throughput,",
        "loss, latency per queue, occupancy, DRAM, cycles (f1 at twice the load and",
        "twice the queues; rxBisect Fig. 10's 200 Gb/s).",
    ], rounds("g1", cells, 5))


def g2():
    cells = [("ddio", s, "rates=98 duration=30 pcm=pcie %s %s" % (WARM, LAT)) for s in CORE]
    write("g2_ddio.txt", [
        "DDIO hit rate (pcm-pcie, the NIC's socket) at 2 x 98 Gb/s, n = 3 (f2).",
    ], rounds("g2", cells, 3))


def g3():
    cells = [("mi%dp" % work, s, "rates=98 duration=30 work=%d payload=1 %s %s"
              % (work, WARM, LAT)) for work in (16, 48, 96, 128) for s in CORE]
    write("g3_heavy.txt", [
        "A heavy NF at 2 x 98 Gb/s (f17 with payload read): 16, 48, 96 and 128",
        "random reads per packet in 40 MiB on every lcore, n = 3. Each core sees",
        "the packet rate it saw at 100 Gb/s, but the LLC holds twice the queues:",
        "where each system saturates, and by how much it falls.",
    ], rounds("g3", cells, 3))


def g4():
    tests = {
        "cold": "stall=%s:1000:100" % STALL,
        "hot": "stall=p0q0:1000:100 --hot-share 0.5",
        "twob": "stall=%s:1000:100 --hot-share 0.5" % STALL,
    }
    cells = [(t, s, "rates=98 duration=30 %s %s %s" % (opt, WARM, LAT))
             for t, opt in tests.items() for s in CORE]
    write("g4_stalls.txt", [
        "One lcore stalled 1 ms every 100 ms at 2 x 98 Gb/s, n = 5 (f4): port 0's",
        "queue 3 (cold), port 0's hot queue 0 (hot share 0.5 on both ports), and",
        "two borrowers. Port 1 is a bystander with its own RMP or pool: its loss",
        "says whether anything crosses ports.",
    ], rounds("g4", cells, 5))


def g5():
    accesses = (800, 1600, 2400, 4800)
    cells = [("pv%d" % a, s, "rates=98 duration=30 work=2 target=%s:%d --queue-weights %s %s %s"
              % (TARGETS, a, weights(0.0722), WARM, LAT)) for a in accesses for s in CORE]
    write("g5_proc_var.txt", [
        "Processing variability at 2 x 98 Gb/s (rxBisect Fig. 14, one target core",
        "per NIC): every lcore 2 random reads per packet, queue 3 of each port",
        "(~1 Gb/s of that port) %s. n = 3." % "/".join(str(a) for a in accesses),
    ], rounds("g5", cells, 3))


def g6():
    variants = [("trace", ""), ("trace48p", "work=48 payload=1")]
    cells = [(name, s, "rates=98 duration=60 %s --trace %s --timeseries %s %s"
              % (opts, MAWI, WARM, LAT)) for name, opts in variants for s in CORE]
    write("g6_mawi.txt", [
        "The MAWI trace's load pattern on both ports (f22), plain l3fwd and a NF of",
        "48 reads with payload, n = 3. No STREAM variant: with 16 lcores the NIC's",
        "socket has no core left for a hog.",
    ], rounds("g6", cells, 3))


def g7():
    rates = "50,60,70,80,85,90,95,98,100"
    cells = [("load", s, "rates=%s duration=20 work=96 payload=1 %s %s" % (rates, WARM, LAT))
             for s in CORE]
    write("g7_load_heavy.txt", [
        "Latency against offered load with the heavy NF (96 reads, payload), %s%%" % rates,
        "of each port's line rate, 20 s each, n = 3 (f21 at 200 Gb/s).",
    ], rounds("g7", cells, 3))


# --- Round 2 at 200 Gb/s (CLAUDE.md 21.6): every eval2 experiment g0-g8 lacks --------
# The paper's evaluation moves to 2 x 100 Gb/s, the rxBisect paper's load, so
# the 100 Gb/s experiments g0-g8 did not repeat are added here. Runs whose
# result is loss or latency use pcm=off: with every CPU of the NIC's socket an
# lcore, PCM's uncore reads interrupt the socket's last CPU (p1q7) by IPI and
# cost it packets and tail (21.4-21.5). Runs that need the DRAM column keep it.
# dynold-128 is dyn-128 with the overload policy and the rate floor off, the
# ablation of the paper (eval2's OLD).
OFF = "pcm=off"
OLD = eval2.OLD


def systems_rounds(battery, tests, systems, n):
    """tests: [(cell, options)]; systems: [(label name, system, extra options)]."""
    out = []
    for k in range(1, n + 1):
        order = [(cell, name, system, ("%s %s" % (opts, extra)).strip())
                 for cell, opts in tests for name, system, extra in systems]
        eval2.random.Random("%s-%d" % (battery, k)).shuffle(order)
        out.append("# round %d" % k)
        out += [line("%s_%s_%s_n%02d" % (battery, cell, name, k), system, options)
                for cell, name, system, options in order]
    return out


CORE_SYS = [(s, s, "") for s in CORE]
DYNOLD = ("dynold-128", "dyn-128", OLD)


def gs():
    tv = weights(0.0722)
    cells = [
        line("gs_r100_64B_privring-128", "privring-128",
             "rates=100 duration=10 --pkt-size 60 %s %s %s" % (OFF, WARM, eval2.LAT_NDR)),
        line("gs_r100_64B_dyn-128", "dyn-128",
             "rates=100 duration=10 --pkt-size 60 %s %s %s" % (OFF, WARM, eval2.LAT_NDR)),
        line("gs_len500_dyn-128", "dyn-128", "rates=98 duration=20 stall=%s:500:100 %s %s %s"
             % (STALL, OFF, WARM, LAT)),
        line("gs_pv2400_dynold-128", "dyn-128", "rates=98 duration=20 work=2 target=%s:2400 "
             "--queue-weights %s %s %s %s %s" % (TARGETS, tv, OLD, OFF, WARM, LAT)),
        line("gs_switch_dyn-128", "dyn-128", "rates=98 duration=40 work=2 target=%s:800 "
             "--queue-weights %s --schedule switch:20:%s --timeseries %s %s %s"
             % (TARGETS, tv, weights(1), OFF, WARM, LAT)),
        line("gs_c0r8_dyn-128", "dyn-128", "rates=98 duration=20 cache=0 groups=8 %s %s"
             % (WARM, LAT)),
        line("gs_skew_dyn-128", "dyn-128", "rates=98 duration=20 work=2 --queue-weights %s "
             "--schedule skew:1 --timeseries %s %s %s" % (weights(1), OFF, WARM, LAT)),
        line("gs_hotmv_fill-128", "fill-128", "rates=98 duration=20 --hot-share 0.5 "
             "--hot-move 5 %s %s %s" % (OFF, WARM, LAT)),
        line("gs_tv20_dyn-128", "dyn-128", "rates=98 duration=20 work=2 --queue-weights %s "
             "--queue-sizes %s %s %s %s" % (weights(0.2 / 0.8 * 7), eval2.sizes(60), OFF, WARM, LAT)),
    ]
    write("gs_smoke.txt", [
        "Smoke of round 2 at 200 Gb/s (~15 min), read before the rest: the two 64 B",
        "runs say whether TRex and the NIC carry 2 x 148.8 Mpps at all (if privring-128",
        "cannot, g12 measures the testbed, not the Rx path: keep 64 B at one port);",
        "the stall run loses on p0q3 only; dynold-128's p0q3 and p1q3 budgets reach",
        "~800 (no overloads), the switch run shows overloads after 20 s on both",
        "targets; c0r8 prints 8 MEMPOOL lines and 4 credit pools per port; the skew,",
        "hot-move and tv20 runs fill both ports' per-queue lines as their schedules say.",
    ], cells)


def g9():
    tests = [("len%d" % us, "rates=98 duration=30 stall=%s:%d:100 %s %s %s"
              % (STALL, us, OFF, WARM, LAT)) for us in (100, 250, 500, 750, 1000, 1500, 2000)]
    write("g9_stall_len.txt", [
        "Cold-core stall from 100 us to 2 ms every 100 ms on p0q3 (f5), n = 5, PCM",
        "off: where each system starts to lose; port 1 is a bystander port.",
    ], systems_rounds("g9", tests, CORE_SYS, 5))


def g10():
    tv = weights(0.0722)
    tests = [("pv%d" % a, "rates=98 duration=30 work=2 target=%s:%d --queue-weights %s %s %s %s"
              % (TARGETS, a, tv, OFF, WARM, LAT)) for a in (1600, 2400, 3200, 4800, 6400)]
    tests.append(("switch", "rates=98 duration=40 work=2 target=%s:800 --queue-weights %s "
                  "--schedule switch:20:%s --timeseries %s %s %s"
                  % (TARGETS, tv, weights(1), OFF, WARM, LAT)))
    write("g10_overload.txt", [
        "The slow target (f6) and the load switch (f13) with the overload policy's",
        "ablation, PCM off (g5's latency had PCM on), n = 3: target p0q3 and p1q3",
        "at 1,600-6,400 reads per packet, and both switching to their full share",
        "with 800 reads at 20 s; dynold-128 = dyn-128 with overload and rate floor off.",
    ], systems_rounds("g10", tests, CORE_SYS + [DYNOLD], 3))


def g11():
    tests = [("mi%d" % w, "rates=98 duration=30 work=%d payload=0 %s %s"
              % (w, WARM, LAT)) for w in (64, 96, 128)]
    write("g11_heavy_nopayload.txt", [
        "The heavy NF without the payload read (f17's untouched half; g3 had it",
        "read): 64, 96 and 128 random reads per packet, n = 3, PCM on for the",
        "DRAM column (the throughput and loss are what the paper quotes).",
    ], systems_rounds("g11", tests, CORE_SYS, 3))


def g12():
    # --pkt-size excludes the FCS (64 B on the wire = 60); 1500 is every other run's.
    sizes = ((64, 60), (128, 124), (256, 252), (512, 508), (1024, 1020), (1500, 1500))
    tests = [("sz%d" % name, "duration=10 --pkt-size %d --search 5,100 %s %s %s"
              % (size, OFF, WARM, eval2.LAT_NDR)) for name, size in sizes]
    body = systems_rounds("g12", tests, CORE_SYS, 3)
    # The rate floor's ablation where it matters: 64 B.
    abl = [("sz64", "duration=10 --pkt-size 60 --search 5,100 %s %s %s"
            % (OFF, WARM, eval2.LAT_NDR))]
    body += ["# rate-floor ablation"] + systems_rounds("g12", abl, [DYNOLD], 3)
    write("g12_ndr_size.txt", [
        "No-drop rate (loss <= 1e-6, 10 s trials) at 64-1,500 B frames on both ports",
        "(f9 multi-core and f10), n = 3, PCM off; dynold-128 at 64 B (the rate",
        "floor's ablation). Read gs first: if TRex or the NIC cannot carry 2 x",
        "148.8 Mpps, the small sizes measure the testbed.",
    ], body)


def g13():
    tests = []
    for f in (0.05, 0.20, 0.40):
        w = 7 * f / (1 - f)
        tests.append(("tv%02d" % round(100 * f), "rates=98 duration=30 work=2 --queue-weights %s "
                      "--queue-sizes %s %s %s %s" % (weights(w), eval2.sizes(60), OFF, WARM, LAT)))
    write("g13_traffic_var.txt", [
        "Traffic variability (f7; shRing Fig. 12, rxBisect Fig. 15) on both ports:",
        "5/20/40% of each port's packets to its queue 3 as 64 B frames, n = 3, PCM off.",
    ], systems_rounds("g13", tests, CORE_SYS, 3))


def g14():
    cells = [("skew", s, "rates=98 duration=60 work=2 --queue-weights %s --schedule skew:{k} "
              "--timeseries %s %s" % (weights(1), WARM, LAT)) for s in CORE]
    write("g14_skew.txt", [
        "The CAIDA-like time-varying skew (f12) on both ports, n = 3, no STREAM",
        "variant (no free core on the NIC's socket), PCM on for the DRAM column.",
    ], rounds("g14", cells, 3))


def g15():
    configs = ((0, "ring_mp_mc", 1), (0, "ring_mp_mc", 8), (0, "stack", 8),
               (32, "ring_mp_mc", 1), (32, "stack", 1))
    tests = [("c%d%s%d" % (c, "s" if o == "stack" else "r", g),
              "rates=98 duration=30 cache=%d ops=%s groups=%d %s %s" % (c, o, g, WARM, LAT))
             for c, o, g in configs]
    systems = [(s, s, "") for s in ("privring-128", "shringfix-8", "dyn-128")]
    write("g15_mempool.txt", [
        "The shared mempool (f14) at 200 Gb/s, n = 3, PCM on (DRAM is the point of",
        "the cache-32 cells): cache 0 with one pool per port (c0r1, the control",
        "the 100 Gb/s battery lacked) and with 8 pools of two lcores (4 credit pools",
        "per port), ring or stack; cache 32 with the FIFO ring or the LIFO stack.",
    ], systems_rounds("g15", tests, systems, 3))


def g16():
    tests = [("hot", "rates=98 duration=30 --hot-share 0.5 %s %s" % (WARM, LAT)),
             ("hotmv", "rates=98 duration=60 --hot-share 0.5 --hot-move 5 %s %s" % (WARM, LAT))]
    write("g16_hot.txt", [
        "One hot queue per port (half of each port's traffic), fixed or moving every",
        "5 s, no stall (f15), n = 3, PCM on (DRAM write is what differs).",
    ], systems_rounds("g16", tests, CORE_SYS, 3))


def g17():
    rates = "10,30,50,70,80,90,95,98,100"
    tests = [("load", "rates=%s duration=20 %s %s %s" % (rates, OFF, WARM, LAT))]
    write("g17_load_latency.txt", [
        "Latency against offered load with plain l3fwd (f3), %s%%, n = 3, PCM off." % rates,
    ], systems_rounds("g17", tests, CORE_SYS, 3))


def g18():
    rates = "50,60,70,80,85,90,95,98,100"
    tests = [("load", "rates=%s duration=20 work=96 payload=1 %s %s %s" % (rates, OFF, WARM, LAT))]
    write("g18_load_heavy.txt", [
        "g7 again with PCM off and all five systems (g8 had three): latency against",
        "load with the heavy NF (96 reads, payload), %s%%, n = 3." % rates,
    ], systems_rounds("g18", tests, CORE_SYS, 3))


def g19():
    tests = [("trace48p", "rates=98 duration=60 work=48 payload=1 --trace %s --timeseries %s %s %s"
              % (MAWI, OFF, WARM, LAT))]
    write("g19_mawi_heavy.txt", [
        "The MAWI trace with 48 reads and payload (g6's heavy half) with PCM off and",
        "all five systems (g8 had three), n = 3.",
    ], systems_rounds("g19", tests, CORE_SYS, 3))


def g20():
    tests = [("cold", "rates=98 duration=30 stall=%s:1000:100 %s %s %s" % (STALL, OFF, WARM, LAT)),
             ("hot", "rates=98 duration=30 stall=p0q0:1000:100 --hot-share 0.5 %s %s %s"
              % (OFF, WARM, LAT))]
    body = systems_rounds("g20", tests, [("shring-8", "shring-8", "")], 5)
    drift = [("bal", "rates=98 duration=60 %s %s" % (WARM, LAT)),
             ("cold", "rates=98 duration=30 stall=%s:1000:100 %s %s" % (STALL, WARM, LAT))]
    body += ["# drift check against all3 (g1, g4): same cells, same PCM"]
    body += systems_rounds("g20d", drift, [("fill-128", "fill-128", ""),
                                           ("dyn-128", "dyn-128", "")], 1)
    write("g20_published_drift.txt", [
        "The published shRing code (shring-8, no race fix) under the cold and hot",
        "stalls, n = 5, PCM off: does it wedge at 200 Gb/s as at 100 (5 of 10)?",
        "Then a drift check: four cells of all3 run again with PCM, as g1/g4 were.",
    ], body)


ROUND2 = (gs, g9, g10, g11, g12, g13, g14, g15, g16, g17, g18, g19, g20)


def main():
    for battery in (g0, g1, g2, g3, g4, g5, g6, g7) + ROUND2:
        battery()


if __name__ == "__main__":
    main()
