#!/usr/bin/env python3
"""Offer a fixed profile at a list of rates and record loss and latency per rate.

Runs on the tgen against an already running TRex server (t-rex-64 -i, with
--hdrh for 1 us latency percentiles). The dut must already be forwarding with
the system under test; this script only knows that system through --label,
which it copies into every CSV row.

Examples:
  ./rate_sweep.py --label privring-1024 --rates 10,50,90,100 --duration 30
  ./rate_sweep.py --label shring-8 --pkt-size 512 --search 50,100 --duration 10
      highest rate (RFC 2544 style binary search) whose loss is <= --loss-tol
  ./rate_sweep.py --label dyn-128 --rates 98 --duration 60 --hot-share 0.5 --hot-move 5
      the hot flow moves to the next Rx queue every 5 s (0, 1, ..., 7, 0, ...)
  ./rate_sweep.py --label dyn-128 --rates 98 --duration 30 --warmup 10 \\
        --lat-queues all --lat-pps 10000 --hdrh required
      a 10 s warm-up window first (phase "warmup"), then the measured one,
      with a latency stream on every Rx queue
  ./rate_sweep.py --label shringfix-8 --rates 98 --duration 30 \\
        --queue-weights 0.2,0.8,0.8,0.8,0.8,0.8,0.8,0.8 --queue-sizes 60,1500,1500,1500,1500,1500,1500,1500
      per-queue streams: queue 0 gets 3.5% of the packets, as 64 B frames

Besides one CSV row per window (--out), every window appends JSON lines to
--details (default: --out with .jsonl): the latency of every latency stream
(percentiles and the encoded HDR histogram), the estimated packets offered to
each queue, and with --timeseries one sample per second.
"""
import argparse
import csv
import json
import os
import sys
import time

TREX_API_DIR = os.environ.get(
    "TREX_API_DIR", "/mydata/trex/v3.07/automation/trex_control_plane/interactive")
sys.path.insert(0, TREX_API_DIR)
from trex.stl.api import STLClient, STLProfile  # noqa: E402

import schedule as schedule_mod  # noqa: E402
import trex_latency  # noqa: E402

TX_PORT = 0
LATENCY_PG_ID = 1          # the profile's single legacy latency stream
LATENCY_PG_BASE = 10       # the profile's per-queue latency streams: 10 + queue
RX_DRAIN_SECONDS = 1.0     # let in-flight packets come back before reading counters
SAMPLE_SECONDS = 1.0       # --timeseries and --schedule tick

# Loss is counted from unicast frames only. The CloudLab switch injects STP and
# LLDP multicast into the link (CLAUDE.md 17.3) and ipackets counts it, which
# under-reports loss; every frame l3fwd sends back is unicast to the tgen MAC.
RX_UNICAST_XSTAT = "rx_unicast_packets"
RX_NOISE_XSTATS = ("rx_multicast_packets", "rx_broadcast_packets")

CSV_FIELDS = [
    "label", "phase", "rate_pct", "duration_s", "pkt_size", "hot_share", "hot_queue",
    "hot_move_s", "queue_weights", "queue_sizes", "schedule", "lat_queues",
    "tx_pkts", "rx_pkts", "rx_port_pkts", "rx_noise_pkts", "loss_pkts", "loss_pct",
    "tx_mpps", "rx_mpps", "rx_gbps_l1", "lat_source", "lat_samples",
    "lat_avg_us", "lat_min_us", "lat_max_us",
    "lat_p50_us", "lat_p90_us", "lat_p99_us", "lat_p999_us", "lat_p9999_us",
    "lat_jitter_us", "lat_dropped",
]
QUEUES = 8


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--label", required=True, help="system under test, e.g. shring-8")
    parser.add_argument("--profile", default=os.path.join(os.path.dirname(__file__),
                                                           "udp_multiflow.py"))
    parser.add_argument("--rates", default="10,25,50,75,90,100",
                        help="comma-separated percentages of line rate")
    parser.add_argument("--duration", type=float, default=30.0, help="seconds per rate")
    parser.add_argument("--pkt-size", type=int, default=1500)
    parser.add_argument("--flows", type=int, default=4096)
    parser.add_argument("--hot-share", type=float, default=0.0,
                        help="fraction of the data rate sent as one flow to one queue "
                             "(imbalance); put it in --label too")
    parser.add_argument("--hot-queue", type=int, default=0,
                        help="Rx queue of the hot and latency flows (default 0)")
    parser.add_argument("--hot-move", type=float, default=0.0, metavar="SECONDS",
                        help="move the hot and latency flows to the next queue every "
                             "SECONDS within each window (0: never)")
    parser.add_argument("--queue-weights", metavar="W0,...,W7",
                        help="per-queue streams with these relative packet rates")
    parser.add_argument("--queue-sizes", metavar="S0,...,S7",
                        help="frame size per queue (without FCS; 60 = 64 B on the wire)")
    parser.add_argument("--schedule", metavar="SPEC",
                        help="change the per-queue weights during each measured window: "
                             "skew:<seed>[:<period>[:<lo>-<hi>]], switch:<t>:<w0,...>, "
                             "or a JSON file (harness/trex/schedule.py)")
    parser.add_argument("--lat-queues", default="hot",
                        help="latency streams: hot (one, on the hot queue), all, none, "
                             "or q,q,... (default hot)")
    parser.add_argument("--lat-pps", type=int, default=1000, help="rate of each latency stream")
    parser.add_argument("--hdrh", choices=("auto", "required", "off"), default="auto",
                        help="required: stop if TRex sends no HDR histograms (not started "
                             "with --hdrh); off: use the coarse histogram only")
    parser.add_argument("--warmup", type=float, default=0.0, metavar="SECONDS",
                        help="first offer the first rate for SECONDS (phase warmup)")
    parser.add_argument("--timeseries", action="store_true",
                        help="record per-second throughput, loss and latency max")
    parser.add_argument("--search", metavar="LO,HI",
                        help="instead of --rates, binary-search the highest rate in "
                             "[LO, HI]%% whose loss is <= --loss-tol; --duration per trial")
    parser.add_argument("--loss-tol", type=float, default=0.0001,
                        help="loss %% a search trial may have and still pass (default "
                             "0.0001%%, i.e. 1 in a million)")
    parser.add_argument("--resolution", type=float, default=0.5,
                        help="stop the search when HI - LO is below this many %% (default 0.5)")
    parser.add_argument("--out", default="results.csv", help="CSV file, appended to")
    parser.add_argument("--details", help="JSON lines file, appended to "
                                          "(default: --out with .jsonl)")
    args = parser.parse_args()
    if not 0 <= args.hot_queue < QUEUES:
        parser.error("--hot-queue must be 0-%d" % (QUEUES - 1))
    if args.schedule and not args.queue_weights:
        parser.error("--schedule needs --queue-weights")
    if args.schedule and args.hot_move:
        parser.error("--schedule and --hot-move are exclusive")
    args.details = args.details or os.path.splitext(args.out)[0] + ".jsonl"
    args.sizes = ([int(s) for s in args.queue_sizes.split(",")] if args.queue_sizes
                  else [args.pkt_size] * QUEUES)
    args.weights = ([float(w) for w in args.queue_weights.split(",")]
                    if args.queue_weights else None)
    return args


def load_streams(args, hot_queue):
    tunables = dict(pkt_size=args.pkt_size, flows=args.flows, hot_share=args.hot_share,
                    hot_queue=hot_queue, lat_queues=args.lat_queues, lat_pps=args.lat_pps)
    if args.queue_weights:
        tunables.update(queue_weights=args.queue_weights,
                        queue_sizes=",".join(str(s) for s in args.sizes))
    return STLProfile.load_py(args.profile, **tunables).get_streams()


def latency_pgs(args, hot_queue):
    """pg_id -> Rx queue, for the latency streams the profile builds."""
    if args.lat_queues == "hot":
        return {LATENCY_PG_ID: hot_queue}
    if args.lat_queues == "none":
        return {}
    queues = range(QUEUES) if args.lat_queues == "all" else \
        sorted({int(q) for q in args.lat_queues.split(",")})
    return {LATENCY_PG_BASE + q: q for q in queues}


def by_pg(table, pg):
    """TRex keys per-pg_id dicts by int or by str depending on the path."""
    return table.get(pg, table.get(str(pg), {}))


def port_count(per_port):
    return per_port.get(TX_PORT, per_port.get(str(TX_PORT), 0)) if per_port else 0


class Window:
    """One traffic window: start, follow the schedule, sample, collect."""

    def __init__(self, client, args, streams_by_queue, rate_pct, duration):
        self.client, self.args = client, args
        self.streams_by_queue = streams_by_queue
        self.rate_pct, self.duration = rate_pct, duration
        self.series, self.steps_done, self.offered = [], [], [0.0] * QUEUES

    def run(self, steps):
        """Offer the rate for the window; returns stats, xstats, latency by pg."""
        client, args = self.client, self.args
        client.reset(ports=[TX_PORT])
        client.clear_stats()
        snapshots, queue, elapsed = {}, args.hot_queue, 0.0
        while elapsed < self.duration - 1e-9:
            seconds = (self.duration - elapsed if args.hot_move <= 0
                       else min(args.hot_move, self.duration - elapsed))
            client.remove_all_streams(ports=[TX_PORT])
            streams = self.streams_by_queue[queue]
            ids = client.add_streams(streams, ports=[TX_PORT])
            client.start(ports=[TX_PORT], mult="%g%%" % self.rate_pct, duration=seconds)
            self.follow(seconds, data_stream_ids(streams, ids), steps)
            client.wait_on_traffic(ports=[TX_PORT])
            stats = client.get_stats()
            for pg in latency_pgs(args, queue):
                snapshots.setdefault(pg, []).append(by_pg(stats.get("latency", {}), pg))
            elapsed += seconds
            queue = (queue + 1) % QUEUES
        time.sleep(RX_DRAIN_SECONDS)
        stats = client.get_stats()
        latency = {}
        for pg, snaps in snapshots.items():
            latency[pg] = trex_latency.merge_snapshots(snaps + [by_pg(stats.get("latency", {}), pg)])
        return stats, client.get_xstats(TX_PORT), latency

    def follow(self, seconds, data_ids, steps):
        """Apply the schedule and take per-second samples until the segment ends."""
        timeseries, weights = self.args.timeseries, self.args.weights
        if not steps and not timeseries:
            self.account(0.0, seconds, weights)
            return
        start = time.time()
        last = self.sample(start, None) if timeseries else None
        pending, mark, next_sample = list(steps), 0.0, SAMPLE_SECONDS
        while True:
            now = time.time() - start
            while pending and pending[0][0] <= now:
                _, upcoming = pending.pop(0)
                self.account(mark, now, weights)
                weights, mark = upcoming, now
                self.update_rates(data_ids, weights)
                self.steps_done.append(round(now, 3))
            if timeseries and now >= next_sample:
                last = self.sample(start, last)
                next_sample += SAMPLE_SECONDS
            if now >= seconds:
                break
            wake = min([seconds] + ([next_sample] if timeseries else [])
                       + [t for t, _ in pending[:1]])
            time.sleep(max(0.001, wake - now))
        self.account(mark, seconds, weights)

    def update_rates(self, data_ids, weights):
        pps = schedule_mod.stream_pps(weights, self.args.sizes, self.rate_pct)
        for q, sid in data_ids.items():
            self.client.update_streams(TX_PORT, mult="%.1fpps" % pps[q], stream_ids=[sid])

    def account(self, t0, t1, weights):
        """Packets offered to each queue in [t0, t1], at the commanded rates."""
        if t1 <= t0 or weights is None:
            return
        pps = schedule_mod.stream_pps(weights, self.args.sizes, self.rate_pct)
        for q in range(QUEUES):
            self.offered[q] += pps[q] * (t1 - t0)

    def sample(self, start, last):
        stats = self.client.get_stats(ports=[TX_PORT])
        xstats = self.client.get_xstats(TX_PORT)
        now = {"t": round(time.time() - start, 3),
               "tx": stats[TX_PORT]["opackets"],
               "rx": xstats.get(RX_UNICAST_XSTAT, 0),
               "lat_last_max": {str(pg): by_pg(stats.get("latency", {}), pg)
                                .get("latency", {}).get("last_max")
                                for pg in latency_pgs(self.args, self.args.hot_queue)}}
        if last is not None:
            dt = max(now["t"] - last["t"], 1e-3)
            self.series.append({"t": now["t"],
                                "tx_pps": round((now["tx"] - last["tx"]) / dt),
                                "rx_pps": round((now["rx"] - last["rx"]) / dt),
                                "lat_last_max": now["lat_last_max"]})
        return now


def data_stream_ids(streams, ids):
    """Rx queue -> stream id of the per-queue data streams (named data-q<q>)."""
    found = {}
    for stream, sid in zip(streams, ids or []):
        name = stream.get_name() if hasattr(stream, "get_name") else getattr(stream, "name", "")
        if name and name.startswith("data-q"):
            found[int(name[len("data-q"):])] = sid
    return found


def read_xstat(xstats, name):
    """Return one NIC counter, failing loudly if this driver does not expose it."""
    if name not in xstats:
        raise RuntimeError("xstat %r not exposed by this NIC; available: %s"
                           % (name, ", ".join(sorted(xstats))))
    return xstats[name]


def latency_records(args, stats, latency):
    """Per-pg summaries, and the window's aggregate over all of them."""
    records = {}
    for pg, entry in latency.items():
        flow = by_pg(stats.get("flow_stats", {}), pg)
        records[pg] = trex_latency.summarize(entry, port_count(flow.get("tx_pkts")),
                                             port_count(flow.get("rx_pkts")))
    return records, trex_latency.aggregate(list(records.values()))


def check_hdrh(args, records):
    if args.hdrh == "off" or not records:
        return
    if any(r["source"] != "hdrh" for r in records.values() if r.get("samples")):
        message = ("TRex sent no HDR histogram: start it with --hdrh "
                   "(sudo ./t-rex-64 -i -c 6 --no-ofed-check --hdrh)")
        if args.hdrh == "required":
            sys.exit("ERROR: " + message)
        print("WARNING: " + message + "; percentiles are 1-digit buckets", flush=True)
        args.hdrh = "off"


def summarize(args, phase, rate_pct, duration, stats, xstats, agg):
    pkt_size = args.pkt_size
    port = stats[TX_PORT]
    tx = port["opackets"]
    rx = read_xstat(xstats, RX_UNICAST_XSTAT)
    rx_noise = sum(read_xstat(xstats, name) for name in RX_NOISE_XSTATS)
    loss = tx - rx
    if args.weights:
        share = [w / sum(args.weights) for w in args.weights]
        wire_bits_per_pkt = sum(s * (size + 4 + 20) * 8 for s, size in zip(share, args.sizes))
    else:
        wire_bits_per_pkt = (pkt_size + 4 + 20) * 8   # + FCS + preamble/SFD/IFG
    return {
        "label": args.label,
        "phase": phase,
        "rate_pct": rate_pct,
        "duration_s": duration,
        "pkt_size": pkt_size,
        "hot_share": args.hot_share,
        "hot_queue": args.hot_queue,
        "hot_move_s": args.hot_move,
        "queue_weights": args.queue_weights or "",
        "queue_sizes": args.queue_sizes or "",
        "schedule": args.schedule or "",
        "lat_queues": args.lat_queues,
        "tx_pkts": tx,
        "rx_pkts": rx,
        "rx_port_pkts": port["ipackets"],
        "rx_noise_pkts": rx_noise,
        "loss_pkts": loss,
        "loss_pct": round(100.0 * loss / tx, 6) if tx else 0.0,
        "tx_mpps": round(tx / duration / 1e6, 4),
        "rx_mpps": round(rx / duration / 1e6, 4),
        "rx_gbps_l1": round(rx * wire_bits_per_pkt / duration / 1e9, 3),
        "lat_source": agg.get("source", ""),
        "lat_samples": agg.get("samples", 0),
        "lat_avg_us": round(agg["avg_us"], 2) if agg.get("avg_us") is not None else None,
        "lat_min_us": agg.get("min_us"),
        "lat_max_us": agg.get("max_us"),
        "lat_p50_us": agg.get("p50"),
        "lat_p90_us": agg.get("p90"),
        "lat_p99_us": agg.get("p99"),
        "lat_p999_us": agg.get("p999"),
        "lat_p9999_us": agg.get("p9999"),
        "lat_jitter_us": agg.get("jitter_us"),
        "lat_dropped": agg.get("dropped"),
    }


def check_csv(path):
    """Refuse to append rows to a CSV written with another set of columns."""
    if not os.path.exists(path) or os.path.getsize(path) == 0:
        return
    with open(path, newline="") as f:
        header = next(csv.reader(f), [])
    if header != CSV_FIELDS:
        sys.exit("%s has other columns (an older rate_sweep.py wrote it); use a new --out"
                 % path)


def append_row(path, row):
    is_new = not os.path.exists(path) or os.path.getsize(path) == 0
    with open(path, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        if is_new:
            writer.writeheader()
        writer.writerow(row)


def append_details(path, rows):
    with open(path, "a") as f:
        for row in rows:
            f.write(json.dumps(row, sort_keys=True) + "\n")


def window_steps(args, duration, phase):
    """The schedule for a measured window (a warm-up keeps the start weights)."""
    if not args.schedule or phase == "warmup":
        return []
    return schedule_mod.parse(args.schedule, duration, args.weights)


def run_and_report(client, args, streams_by_queue, phase, rate_pct, duration=None):
    duration = duration or args.duration
    window = Window(client, args, streams_by_queue, rate_pct, duration)
    stats, xstats, latency = window.run(window_steps(args, duration, phase))
    records, agg = latency_records(args, stats, latency)
    check_hdrh(args, records)
    row = summarize(args, phase, rate_pct, duration, stats, xstats, agg)
    append_row(args.out, row)

    key = {"label": args.label, "phase": phase, "rate_pct": rate_pct, "duration_s": duration,
           "time": time.strftime("%Y-%m-%d %H:%M:%S")}
    pgs = latency_pgs(args, args.hot_queue)
    details = [dict(key, kind="latency", pg_id=pg, queue=pgs.get(pg), **rec)
               for pg, rec in sorted(records.items())]
    details.append(dict(key, kind="latency", pg_id=None, queue="all", **agg))
    offered = None
    if args.weights:
        total = sum(window.offered)
        data_tx = row["tx_pkts"] - sum(r.get("tx") or 0 for r in records.values())
        offered = [round(o / total * data_tx) for o in window.offered] if total else None
    details.append(dict(key, kind="window", tx_pkts=row["tx_pkts"], rx_pkts=row["rx_pkts"],
                        loss_pkts=row["loss_pkts"], queue_offered_est=offered,
                        schedule_steps=window.steps_done))
    if window.series:
        details.append(dict(key, kind="series", samples=window.series))
    append_details(args.details, details)

    # sweep.sh stamps each line with " tx=" and " rx=" as the end of a window,
    # and takes the window's length from " dur=".
    print("%-16s %5.1f%%  tx=%d rx=%d noise=%d loss=%.4f%%  lat avg/max=%s/%s us"
          "  p50/p99/p99.9/p99.99=%s/%s/%s/%s us  dur=%g phase=%s" % (
              args.label, rate_pct, row["tx_pkts"], row["rx_pkts"], row["rx_noise_pkts"],
              row["loss_pct"], row["lat_avg_us"], row["lat_max_us"],
              row["lat_p50_us"], row["lat_p99_us"], row["lat_p999_us"], row["lat_p9999_us"],
              duration, phase), flush=True)
    for pg, rec in sorted(records.items()):
        lost = (rec["tx"] - rec["rx"]) if rec.get("tx") is not None and rec.get("rx") is not None else None
        print("LAT %s %s q%s n=%s avg=%s p50=%s p99=%s p99.9=%s p99.99=%s max=%s lost=%s" % (
            args.label, phase, pgs.get(pg), rec.get("samples"),
            None if rec["avg_us"] is None else round(rec["avg_us"], 1),
            rec.get("p50"), rec.get("p99"), rec.get("p999"), rec.get("p9999"),
            rec.get("max_us"), lost), flush=True)
    return row


def search(client, args, streams_by_queue):
    """Binary search for the highest passing rate in [LO, HI] (RFC 2544 style)."""
    low, high = (float(x) for x in args.search.split(","))
    best = None
    row = run_and_report(client, args, streams_by_queue, "search", high)
    if row["loss_pct"] <= args.loss_tol:
        best = high
    else:
        while high - low > args.resolution:
            mid = round((low + high) / 2, 2)
            row = run_and_report(client, args, streams_by_queue, "search", mid)
            if row["loss_pct"] <= args.loss_tol:
                best, low = mid, mid
            else:
                high = mid
    line_mpps = 100e9 / ((args.pkt_size + 4 + 20) * 8) / 1e6
    if best is None:
        print("NDR %s: no rate in [%s, %s]%% passed (loss tolerance %g%%)"
              % (args.label, args.search.split(",")[0], args.search.split(",")[1],
                 args.loss_tol), flush=True)
    else:
        print("NDR %s: %.2f%% of line rate = %.3f Mpps at %d B (loss tolerance %g%%)"
              % (args.label, best, best / 100 * line_mpps, args.pkt_size, args.loss_tol),
              flush=True)


def main():
    args = parse_args()
    check_csv(args.out)
    queues = range(QUEUES) if args.hot_move > 0 else [args.hot_queue]
    streams_by_queue = {q: load_streams(args, q) for q in queues}

    client = STLClient()
    client.connect()
    try:
        client.acquire(ports=[TX_PORT], force=True)
        rates = ([float(args.search.split(",")[1])] if args.search
                 else [float(r) for r in args.rates.split(",")])
        if args.warmup > 0:
            run_and_report(client, args, streams_by_queue, "warmup", rates[0], args.warmup)
        if args.search:
            search(client, args, streams_by_queue)
        else:
            for rate_pct in rates:
                run_and_report(client, args, streams_by_queue, "sweep", rate_pct)
    finally:
        client.disconnect()


if __name__ == "__main__":
    main()
