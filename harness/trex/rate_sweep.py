#!/usr/bin/env python3
"""Offer a fixed profile at a list of rates and record loss and latency per rate.

Runs on the tgen against an already running TRex server (t-rex-64 -i). The dut
must already be forwarding with the system under test; this script only knows
that system through --label, which it copies into every CSV row.

Example:
  ./rate_sweep.py --label privring-1024 --rates 10,50,90,100 --duration 30
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
    "label", "rate_pct", "duration_s", "tx_pkts", "rx_pkts", "rx_port_pkts", "rx_noise_pkts",
    "loss_pkts", "loss_pct",
    "tx_mpps", "rx_mpps", "rx_gbps_l1", "lat_avg_us", "lat_min_us", "lat_max_us",
    "lat_jitter_us", "lat_dropped",
]


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
    parser.add_argument("--out", default="results.csv", help="CSV file, appended to")
    return parser.parse_args()


def load_streams(args):
    profile = STLProfile.load_py(args.profile, pkt_size=args.pkt_size, flows=args.flows,
                                 hot_share=args.hot_share)
    return profile.get_streams()


def run_one_rate(client, streams, rate_pct, duration):
    """Offer `rate_pct`% of line rate for `duration` seconds.

    Returns the port stats and the NIC xstats; clear_stats() above resets both,
    so every counter covers this rate only.
    """
    client.reset(ports=[TX_PORT])
    client.add_streams(streams, ports=[TX_PORT])
    client.clear_stats()
    client.start(ports=[TX_PORT], mult="%g%%" % rate_pct, duration=duration)
    client.wait_on_traffic(ports=[TX_PORT])
    time.sleep(RX_DRAIN_SECONDS)
    return client.get_stats(), client.get_xstats(TX_PORT)


def read_xstat(xstats, name):
    """Return one NIC counter, failing loudly if this driver does not expose it."""
    if name not in xstats:
        raise RuntimeError("xstat %r not exposed by this NIC; available: %s"
                           % (name, ", ".join(sorted(xstats))))
    return xstats[name]


def summarize(label, rate_pct, duration, stats, xstats, pkt_size):
    port = stats[TX_PORT]
    tx = port["opackets"]
    rx = read_xstat(xstats, RX_UNICAST_XSTAT)
    rx_noise = sum(read_xstat(xstats, name) for name in RX_NOISE_XSTATS)
    loss = tx - rx
    wire_bits_per_pkt = (pkt_size + 4 + 20) * 8   # + FCS + preamble/SFD/IFG
    latency = stats.get("latency", {}).get(LATENCY_PG_ID, {})
    lat = latency.get("latency", {})
    return {
        "label": label,
        "rate_pct": rate_pct,
        "duration_s": duration,
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
        "lat_jitter_us": lat.get("jitter"),
        "lat_dropped": latency.get("err_cntrs", {}).get("dropped"),
    }


def append_row(path, row):
    is_new = not os.path.exists(path) or os.path.getsize(path) == 0
    with open(path, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        if is_new:
            writer.writeheader()
        writer.writerow(row)


def main():
    args = parse_args()
    rates = [float(r) for r in args.rates.split(",")]
    streams = load_streams(args)

    client = STLClient()
    client.connect()
    try:
        client.acquire(ports=[TX_PORT], force=True)
        for rate_pct in rates:
            stats, xstats = run_one_rate(client, streams, rate_pct, args.duration)
            row = summarize(args.label, rate_pct, args.duration, stats, xstats, args.pkt_size)
            append_row(args.out, row)
            print("%-16s %5.1f%%  tx=%d rx=%d noise=%d loss=%.4f%%  lat avg/max=%s/%s us" % (
                args.label, rate_pct, row["tx_pkts"], row["rx_pkts"], row["rx_noise_pkts"],
                row["loss_pct"],
                row["lat_avg_us"], row["lat_max_us"]))
    finally:
        client.disconnect()


if __name__ == "__main__":
    main()
