"""Per-queue weights that change while traffic runs (rate_sweep.py --schedule).

A schedule is a list of (t, weights): from t seconds into a measured window
the per-queue data streams of udp_multiflow.py (queue_weights mode) send
packets in proportion to `weights`. Specs:

  skew:<seed>[:<period_s>[:<lo>-<hi>]]
      An imbalance like the CAIDA trace the rxBisect paper analyses (its
      Fig. 4): every period_s (default 1) a new weight vector whose max/min
      ratio is drawn in [lo, hi] (default 3.25-4.33, the paper's range), from
      log-weights that follow an AR(1) walk, so busy queues stay busy for a
      while and the busiest one changes over time. Same seed, same schedule,
      for every system.
  switch:<t>:<w0,...,w7>
      The weights of --queue-weights until t, then these (shRing's Fig. 13:
      a load that turns pathological mid-run).
  <file.json>
      [[t, [w0, ..., w7]], ...], t ascending, the first at 0.
"""
import json
import math
import random

QUEUES = 8
SKEW_PERIOD_S = 1.0
SKEW_RATIO = (3.25, 4.33)
SKEW_PERSISTENCE = 0.8      # AR(1) coefficient of the log-weights


def skew_steps(seed, duration, period=SKEW_PERIOD_S, ratio=SKEW_RATIO):
    rng = random.Random(seed)
    logs = [rng.gauss(0, 1) for _ in range(QUEUES)]
    steps, t = [], 0.0
    while t < duration - 1e-9:
        target = rng.uniform(*ratio)
        lo, hi = min(logs), max(logs)
        scale = math.log(target) / (hi - lo) if hi > lo else 0.0
        steps.append((t, [round(math.exp((x - lo) * scale), 4) for x in logs]))
        logs = [SKEW_PERSISTENCE * x + math.sqrt(1 - SKEW_PERSISTENCE ** 2) * rng.gauss(0, 1)
                for x in logs]
        t += period
    return steps


def parse(spec, duration, initial_weights):
    """The schedule a spec names, for a window of `duration` seconds."""
    if spec.startswith("skew:"):
        parts = spec.split(":")[1:]
        seed = int(parts[0])
        period = float(parts[1]) if len(parts) > 1 else SKEW_PERIOD_S
        ratio = (tuple(float(x) for x in parts[2].split("-")) if len(parts) > 2
                 else SKEW_RATIO)
        steps = skew_steps(seed, duration, period, ratio)
    elif spec.startswith("switch:"):
        _, t, weights = spec.split(":", 2)
        steps = [(0.0, list(initial_weights)),
                 (float(t), [float(w) for w in weights.split(",")])]
    else:
        with open(spec) as f:
            steps = [(float(t), [float(w) for w in ws]) for t, ws in json.load(f)]
    check(steps, initial_weights)
    return steps


def check(steps, initial_weights):
    if not steps or steps[0][0] != 0:
        raise ValueError("a schedule starts at t = 0")
    for (t0, _), (t1, _) in zip(steps, steps[1:]):
        if t1 <= t0:
            raise ValueError("schedule times must increase")
    for _, weights in steps:
        if len(weights) != QUEUES or any(w < 0 for w in weights) or not sum(weights):
            raise ValueError("schedule weights: %d values >= 0, positive sum" % QUEUES)
        # A queue with no stream cannot be given traffic later, and a stream
        # cannot be set to 0 pps; the profile's weights fix which exist.
        for w, w0 in zip(weights, initial_weights):
            if (w == 0) != (w0 == 0):
                raise ValueError("schedule weights must be 0 exactly where "
                                 "--queue-weights is 0")


def stream_pps(weights, sizes, rate_pct, line_gbps=100.0):
    """Packets per second per queue so that the data fill rate_pct of the line."""
    share = [w / sum(weights) for w in weights]
    bits_per_pkt = sum(s * (size + 4 + 20) * 8 for s, size in zip(share, sizes))
    total_pps = rate_pct / 100.0 * line_gbps * 1e9 / bits_per_pkt
    return [s * total_pps for s in share]
