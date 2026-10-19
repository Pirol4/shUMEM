#!/usr/bin/env python3
"""Offer a fixed profile at a list of rates and record loss and latency per rate.

Runs on the tgen against an already running TRex server (t-rex-64 -i). The dut
must already be forwarding with the system under test; this script only knows
that system through --label, which it copies into every CSV row.

Examples:
  ./rate_sweep.py --label privring-1024 --rates 10,50,90,100 --duration 30
  ./rate_sweep.py --label shring-8 --pkt-size 512 --search 50,100 --duration 10
      highest rate (RFC 2544 style binary search) whose loss is <= --loss-tol
  ./rate_sweep.py --label dyn-128 --rates 98 --duration 60 --hot-share 0.5 --hot-move 5
      the hot flow moves to the next Rx queue every 5 s (0, 1, ..., 7, 0, ...)
"""
import argparse
import csv
import os
import sys
import time

TREX_API_DIR = os.environ.get(
    "TREX_API_DIR", "/mydata/trex/v3.07/automation/trex_control_plane/interactive")
sys.path.insert(0, TREX_API_DIR)
from trex.stl.api import STLClient, STLProfile  # noqa: E402

TX_PORT = 0
LATENCY_PG_ID = 1          # must match the profile
RX_DRAIN_SECONDS = 1.0     # let in-flight packets come back before reading counters

# Loss is counted from unicast frames only. The CloudLab switch injects STP and
# LLDP multicast into the link (CLAUDE.md 17.3) and ipackets counts it, which
# under-reports loss; every frame l3fwd sends back is unicast to the tgen MAC.
RX_UNICAST_XSTAT = "rx_unicast_packets"
RX_NOISE_XSTATS = ("rx_multicast_packets", "rx_broadcast_packets")

CSV_FIELDS = [
    "label", "phase", "rate_pct", "duration_s", "pkt_size", "hot_share", "hot_queue",
    "hot_move_s", "tx_pkts", "rx_pkts", "rx_port_pkts", "rx_noise_pkts",
    "loss_pkts", "loss_pct",
    "tx_mpps", "rx_mpps", "rx_gbps_l1", "lat_avg_us", "lat_min_us", "lat_max_us",
    "lat_p50_us", "lat_p99_us", "lat_p999_us", "lat_jitter_us", "lat_dropped",
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
    parser.add_argument("--search", metavar="LO,HI",
                        help="instead of --rates, binary-search the highest rate in "
                             "[LO, HI]%% whose loss is <= --loss-tol; --duration per trial")
    parser.add_argument("--loss-tol", type=float, default=0.0001,
                        help="loss %% a search trial may have and still pass (default "
                             "0.0001%%, i.e. 1 in a million)")
    parser.add_argument("--resolution", type=float, default=0.5,
                        help="stop the search when HI - LO is below this many %% (default 0.5)")
    parser.add_argument("--out", default="results.csv", help="CSV file, appended to")
    return parser.parse_args()


def load_streams(args, hot_queue):
    profile = STLProfile.load_py(args.profile, pkt_size=args.pkt_size, flows=args.flows,
                                 hot_share=args.hot_share, hot_queue=hot_queue)
    return profile.get_streams()


def latency_of(stats):
    return stats.get("latency", {}).get(LATENCY_PG_ID, {})


def histogram_total(latency):
    return sum(latency.get("latency", {}).get("histogram", {}).values())


def run_one_rate(client, streams_by_queue, rate_pct, duration, hot_queue, hot_move):
    """Offer `rate_pct`% of line rate for `duration` seconds.

    With hot_move > 0 the window is cut into segments of hot_move seconds, each
    with the hot flow on the next queue; the streams are swapped between
    segments (a gap of tens of ms with no traffic). Returns the port stats and
    the NIC xstats, both covering this window only (cleared once at its start),
    and the latency of the whole window.
    """
    client.reset(ports=[TX_PORT])
    client.clear_stats()
    segments, queue, elapsed = [], hot_queue, 0.0
    while elapsed < duration - 1e-9:
        seconds = duration - elapsed if hot_move <= 0 else min(hot_move, duration - elapsed)
        client.remove_all_streams(ports=[TX_PORT])
        client.add_streams(streams_by_queue[queue], ports=[TX_PORT])
        client.start(ports=[TX_PORT], mult="%g%%" % rate_pct, duration=seconds)
        client.wait_on_traffic(ports=[TX_PORT])
        segments.append(latency_of(client.get_stats()))
        elapsed += seconds
        queue = (queue + 1) % QUEUES
    time.sleep(RX_DRAIN_SECONDS)
    stats = client.get_stats()
    return stats, client.get_xstats(TX_PORT), merge_latency(segments + [latency_of(stats)])


def merge_latency(snapshots):
    """One latency record for the window from per-segment snapshots.

    Whether TRex keeps a latency stream's counters when the stream is removed
    and added again is not documented; if every snapshot holds at least as
    many samples as the one before, they are cumulative and the last is the
    window; otherwise each segment started from zero and they are summed.
    """
    snapshots = [s for s in snapshots if s]
    if not snapshots:
        return {}
    totals = [histogram_total(s) for s in snapshots]
    if all(b >= a for a, b in zip(totals, totals[1:])):
        return snapshots[-1]
    histogram, worst, weighted, count = {}, None, 0.0, 0
    for snap in snapshots[:-1]:     # the last one repeats the final segment
        lat = snap.get("latency", {})
        for bucket, n in lat.get("histogram", {}).items():
            histogram[bucket] = histogram.get(bucket, 0) + n
        n = sum(lat.get("histogram", {}).values())
        if lat.get("average") is not None:
            weighted += lat["average"] * n
            count += n
        if lat.get("total_max") is not None:
            worst = max(worst or 0, lat["total_max"])
    return {"latency": {"histogram": histogram, "total_max": worst,
                        "average": weighted / count if count else None},
            "err_cntrs": snapshots[-1].get("err_cntrs", {})}


def percentile(histogram, fraction):
    """Latency below which `fraction` of the samples fall, from TRex's histogram.

    TRex buckets are coarse (10 us wide below 100 us, 100 us wide above); the
    bucket's lower bound is returned, so read it as "within one bucket".
    """
    total = sum(histogram.values())
    if not total:
        return None
    seen = 0
    for bucket in sorted(histogram, key=float):
        seen += histogram[bucket]
        if seen >= fraction * total:
            return float(bucket)
    return None


def read_xstat(xstats, name):
    """Return one NIC counter, failing loudly if this driver does not expose it."""
    if name not in xstats:
        raise RuntimeError("xstat %r not exposed by this NIC; available: %s"
                           % (name, ", ".join(sorted(xstats))))
    return xstats[name]


def summarize(args, phase, rate_pct, duration, stats, xstats, latency):
    pkt_size = args.pkt_size
    port = stats[TX_PORT]
    tx = port["opackets"]
    rx = read_xstat(xstats, RX_UNICAST_XSTAT)
    rx_noise = sum(read_xstat(xstats, name) for name in RX_NOISE_XSTATS)
    loss = tx - rx
    wire_bits_per_pkt = (pkt_size + 4 + 20) * 8   # + FCS + preamble/SFD/IFG
    lat = latency.get("latency", {})
    histogram = lat.get("histogram", {})
    return {
        "label": args.label,
        "phase": phase,
        "rate_pct": rate_pct,
        "duration_s": duration,
        "pkt_size": pkt_size,
        "hot_share": args.hot_share,
        "hot_queue": args.hot_queue,
        "hot_move_s": args.hot_move,
        "tx_pkts": tx,
        "rx_pkts": rx,
        "rx_port_pkts": port["ipackets"],
        "rx_noise_pkts": rx_noise,
        "loss_pkts": loss,
        "loss_pct": round(100.0 * loss / tx, 6) if tx else 0.0,
        "tx_mpps": round(tx / duration / 1e6, 4),
        "rx_mpps": round(rx / duration / 1e6, 4),
        "rx_gbps_l1": round(rx * wire_bits_per_pkt / duration / 1e9, 3),
        "lat_avg_us": lat.get("average"),
        "lat_min_us": lat.get("total_min"),
        "lat_max_us": lat.get("total_max"),
        "lat_p50_us": percentile(histogram, 0.50),
        "lat_p99_us": percentile(histogram, 0.99),
        "lat_p999_us": percentile(histogram, 0.999),
        "lat_jitter_us": lat.get("jitter"),
        "lat_dropped": latency.get("err_cntrs", {}).get("dropped"),
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


def run_and_report(client, args, streams_by_queue, phase, rate_pct):
    stats, xstats, latency = run_one_rate(client, streams_by_queue, rate_pct, args.duration,
                                          args.hot_queue, args.hot_move)
    row = summarize(args, phase, rate_pct, args.duration, stats, xstats, latency)
    append_row(args.out, row)
    # sweep.sh stamps each line with " tx=" and " rx=" as the end of a window.
    print("%-16s %5.1f%%  tx=%d rx=%d noise=%d loss=%.4f%%  lat avg/max=%s/%s us"
          "  p50/p99/p99.9=%s/%s/%s us" % (
              args.label, rate_pct, row["tx_pkts"], row["rx_pkts"], row["rx_noise_pkts"],
              row["loss_pct"], row["lat_avg_us"], row["lat_max_us"],
              row["lat_p50_us"], row["lat_p99_us"], row["lat_p999_us"]), flush=True)
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
    if not 0 <= args.hot_queue < QUEUES:
        sys.exit("--hot-queue must be 0-%d" % (QUEUES - 1))
    queues = range(QUEUES) if args.hot_move > 0 else [args.hot_queue]
    streams_by_queue = {q: load_streams(args, q) for q in queues}

    client = STLClient()
    client.connect()
    try:
        client.acquire(ports=[TX_PORT], force=True)
        if args.search:
            search(client, args, streams_by_queue)
        else:
            for rate_pct in (float(r) for r in args.rates.split(",")):
                run_and_report(client, args, streams_by_queue, "sweep", rate_pct)
    finally:
        client.disconnect()


if __name__ == "__main__":
    main()
