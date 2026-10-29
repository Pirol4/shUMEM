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


def main():
    for battery in (g0, g1, g2, g3, g4, g5, g6, g7):
        battery()


if __name__ == "__main__":
    main()
