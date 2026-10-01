#!/usr/bin/env python3
"""Steal a CPU from one l3fwd lcore in controlled, periodic stalls.

Runs on the dut as root. Pinned to --core with SCHED_FIFO priority, it sleeps
until the next period and then busy-waits --stall-us; real-time priority makes
the kernel preempt the polling lcore at once, so for that long the lcore's
queue is not served (CLAUDE.md 17.9). Unlike a `nice` CPU hog, the stall
length and rate are known, so expected loss can be computed from them.

Example: 1 ms stall every 100 ms on lcore 4 (queue 3), until Ctrl-C:
  sudo harness/stall_core.py --core 4 --stall-us 1000 --period-ms 100
"""
import argparse
import os
import sys
import time

FIFO_PRIORITY = 1   # any real-time priority outranks SCHED_OTHER l3fwd


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--core", type=int, required=True, help="CPU to stall")
    parser.add_argument("--stall-us", type=int, required=True, help="length of each stall")
    parser.add_argument("--period-ms", type=float, required=True, help="time between stalls")
    args = parser.parse_args()
    if args.stall_us <= 0 or args.stall_us >= args.period_ms * 1000:
        parser.error("need 0 < stall-us < period-ms * 1000")
    return args


def take_core(core):
    """Pin to `core` and outrank any normal task on it."""
    os.sched_setaffinity(0, {core})
    os.sched_setscheduler(0, os.SCHED_FIFO, os.sched_param(FIFO_PRIORITY))


def busy_wait(seconds):
    end = time.perf_counter() + seconds
    while time.perf_counter() < end:
        pass


def main():
    args = parse_args()
    if os.geteuid() != 0:
        sys.exit("must run as root (SCHED_FIFO)")
    take_core(args.core)

    stall_s = args.stall_us / 1e6
    period_s = args.period_ms / 1e3
    stalls = 0
    stalled_s = 0.0
    started = time.perf_counter()
    next_stall = started + period_s
    print("stalling CPU %d for %d us every %g ms; Ctrl-C to stop"
          % (args.core, args.stall_us, args.period_ms), flush=True)
    try:
        while True:
            time.sleep(max(0.0, next_stall - time.perf_counter()))
            t0 = time.perf_counter()
            busy_wait(stall_s)
            stalled_s += time.perf_counter() - t0
            stalls += 1
            next_stall += period_s
    except KeyboardInterrupt:
        pass
    elapsed = time.perf_counter() - started
    print("%d stalls, mean %.0f us, %.2f%% of CPU %d over %.1f s"
          % (stalls, 1e6 * stalled_s / max(stalls, 1), 100 * stalled_s / elapsed,
             args.core, elapsed))


if __name__ == "__main__":
    main()
