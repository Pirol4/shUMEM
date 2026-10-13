#!/usr/bin/env python3
"""Event model of the Phase 3 credit policy (CLAUDE.md 19) against the three
tests of 18.10, before any driver code.

What is modelled. A queue receives at a constant rate and its lcore polls
often enough that nothing is lost, except while the lcore is paused. A pause
of d us lets rate * d packets arrive into `budget` posted buffers: the excess
is lost, the rest is the backlog the lcore finds when it returns. Pauses are
the only loss in the measured runs too (rx_missed_errors, 18.7-18.9).

Policy (one lottery per chunk of credits, equal tickets):
  - a queue remembers the largest backlog of the last 1-2 windows (`peak`);
  - backlog == budget (it dropped, the real need is unknown): ask to double;
    backlog > budget / ALPHA: ask for ALPHA * peak - budget;
  - an ask takes what the pool holds, then draws a loser per chunk among the
    other queues and adds the chunk to the loser's debt;
  - a loser pays what it holds above max(FLOOR, ALPHA * peak), drops the rest
    of the debt, and the credits reach the pool once it has received that
    many packets (a posted buffer is only given up by not re-posting it).
`broadcast` replaces the draw by an equal share of the ask on every other
queue; `static` never moves a credit (Phase 1, fill-128).

`lean` has no debts and no draw: every queue holds max(FLOOR, ALPHA * peak),
gives the rest to the pool by itself at the end of each window, and a queue
that is short takes from the pool, first come first served. Credits in the
pool are not posted, so the posted total follows the load instead of staying
at CREDITS.

Rates are the measured ones at 98% of line rate. The pause on lcore 1 is
inferred, not observed: see CALIBRATION.

Usage:
  python3 analysis/credit_model.py [--seeds 20]
"""
import argparse
import heapq
import math
import random
import statistics

N_QUEUES = 8
CREDITS = 1024                 # C: total posted buffers, fixed (= 8 x fill-128)
ENTITLEMENT = CREDITS // N_QUEUES
RING = 1024
FLOOR = 64                     # never lent below this (vendor minimum, 18.4)
ALPHA = 1.5                    # budget a queue wants per packet of peak backlog
CHUNK = 32                     # credits per lottery
WINDOW_US = 1e6                # peak is the max of the current and last window
DURATION_US = 30e6

# Packets per us, from l3fwd's per-queue counters (18.7, 18.9).
BALANCED = [c / 60e6 for c in (60_337_930, 60_277_929, 60_277_929, 60_160_199,
                               60_395_659, 60_277_930, 60_277_930, 60_277_930)]
HOT50 = [135_655_342 / 30e6] + [(241_141_744 - 135_655_342) / 7 / 30e6] * 7

# CALIBRATION. Loss against ring size with one hot queue (17.7, 100%: 17,329 /
# 13,556 / 5,940 for 64 / 128 / 256 buffers) is linear, ~59.5 packets fewer
# per extra buffer in 30 s, which is what ~2 pauses per second of ~77 us on
# the hot queue's lcore give; the same pause on that lcore alone also gives
# balanced privRing-64's ~1,800 per minute (17.6). So: lcore 1 (queue 0)
# pauses 77 us every 505 ms, and no other lcore pauses on its own.
LCORE1_PAUSE = (0, 505e3, 77.0)                 # (queue, period us, length us)
STALL_US, STALL_PERIOD_US = 1001.0, 100e3       # stall_core.py, 18.8

MEASURED_STATIC = {"balanced": 0, "hot": 13_097, "stall": 263_916}   # fill-128


class Queue:
    def __init__(self, rate):
        self.rate = rate
        self.budget = ENTITLEMENT
        self.debt = 0
        self.want = 0
        self.paused = False
        self.peak = [0.0, 0.0]          # last window, current window
        self.window = 0
        self.lost = 0.0

    def observe(self, now, backlog):
        window = int(now // WINDOW_US)
        if window != self.window:
            self.peak = [self.peak[1] if window == self.window + 1 else 0.0, 0.0]
            self.window = window
        self.peak[1] = max(self.peak[1], backlog)

    def target(self):
        return max(FLOOR, math.ceil(ALPHA * max(self.peak)))


class Model:
    def __init__(self, policy, rates, pauses, seed):
        self.policy = policy
        self.queues = [Queue(r) for r in rates]
        self.pool = 0
        self.rng = random.Random(seed)
        self.events = []                # (time, sequence, kind, queue, value)
        self.sequence = 0
        self.max_budget = [ENTITLEMENT] * N_QUEUES
        if policy == "lean":
            for i in range(N_QUEUES):
                t = WINDOW_US
                while t < DURATION_US:
                    self.push(t, "window", i, 0)
                    t += WINDOW_US
        for queue, period, length in pauses:
            t = self.rng.uniform(0, period)
            while t + length < DURATION_US:
                self.push(t, "pause", queue, length)
                t += period

    def push(self, time, kind, queue, value):
        self.sequence += 1
        heapq.heappush(self.events, (time, self.sequence, kind, queue, value))

    def run(self):
        while self.events:
            now, _, kind, i, value = heapq.heappop(self.events)
            q = self.queues[i]
            if kind == "pause":
                q.paused = True
                q.pause_budget = q.budget
                self.push(now + value, "resume", i, value)
            elif kind == "resume":
                q.paused = False
                arrived = q.rate * value
                backlog = min(arrived, q.pause_budget)
                q.lost += arrived - backlog
                q.observe(now, backlog)
                if self.policy != "static":
                    self.pay(now, i)
                    self.ask(now, i, saturated=arrived >= q.pause_budget)
            elif kind == "window":
                # No pause in a whole window still moves the peak along.
                q.observe(now, 0.0)
                if not q.paused:
                    q.debt = q.budget
                    self.pay(now, i)
            elif kind == "paid":
                self.pool += value
                for j in self.rng.sample(range(N_QUEUES), N_QUEUES):
                    self.take(j)
        return self

    def take(self, i):
        q = self.queues[i]
        got = 0 if q.paused else min(q.want, self.pool)
        self.pool -= got
        q.want -= got
        q.budget += got
        self.max_budget[i] = max(self.max_budget[i], q.budget)

    def ask(self, now, i, saturated):
        q = self.queues[i]
        want = q.budget if saturated else q.target() - q.budget
        q.want = max(0, min(want, RING - q.budget))
        self.take(i)
        others = [j for j in range(N_QUEUES) if j != i]
        if self.policy == "lean":
            return
        if self.policy == "broadcast":
            shares = {j: math.ceil(q.want / len(others)) for j in others}
        else:
            shares = dict.fromkeys(others, 0)
            for _ in range(math.ceil(q.want / CHUNK)):
                shares[self.rng.choice(others)] += CHUNK
        for j, share in shares.items():
            self.queues[j].debt += share
            if not self.queues[j].paused:
                self.pay(now, j)

    def pay(self, now, i):
        q = self.queues[i]
        amount = max(0, min(q.debt, q.budget - q.target()))
        q.debt = 0
        if amount:
            q.budget -= amount
            self.push(now + amount / q.rate, "paid", i, amount)


SCENARIOS = {
    # name: (rates, pauses besides lcore 1's own)
    "balanced": (BALANCED, []),
    "hot": (HOT50, []),
    "stall": (BALANCED, [(3, STALL_PERIOD_US, STALL_US)]),
    "hot+stall-hot": (HOT50, [(0, STALL_PERIOD_US, STALL_US)]),
    "hot+stall-cold": (HOT50, [(3, STALL_PERIOD_US, STALL_US)]),
}


def summarize(policy, name, seeds):
    rates, pauses = SCENARIOS[name]
    runs = [Model(policy, rates, [LCORE1_PAUSE] + pauses, seed).run() for seed in range(seeds)]
    total = [sum(q.lost for q in m.queues) for m in runs]
    per_queue = [statistics.mean(m.queues[i].lost for m in runs) for i in range(N_QUEUES)]
    budgets = [statistics.mean(m.queues[i].budget for m in runs) for i in range(N_QUEUES)]
    return statistics.mean(total), min(total), max(total), per_queue, budgets


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--seeds", type=int, default=20)
    args = parser.parse_args()

    print(f"{'scenario':<15} {'policy':<10} {'loss mean':>10} {'min':>9} {'max':>9}"
          f"  loss per queue (k) / final budgets")
    for name in SCENARIOS:
        for policy in ("static", "lottery", "broadcast", "lean"):
            mean, low, high, per_queue, budgets = summarize(policy, name, args.seeds)
            measured = MEASURED_STATIC.get(name) if policy == "static" else None
            note = f"  (measured {measured:,})" if measured is not None else ""
            print(f"{name:<15} {policy:<10} {mean:>10,.0f} {low:>9,.0f} {high:>9,.0f}"
                  f"  {' '.join(f'{x / 1e3:.1f}' for x in per_queue)}{note}")
            if policy != "static":
                print(f"{'':<46}  budgets {' '.join(f'{b:.0f}' for b in budgets)}"
                      f"  = {sum(budgets):.0f} posted")
        print()


if __name__ == "__main__":
    main()
