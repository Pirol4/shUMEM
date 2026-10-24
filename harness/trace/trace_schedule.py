#!/usr/bin/env python3
"""Turn a packet trace into the load pattern rate_sweep.py replays (CLAUDE.md 20.18).

A backbone trace (MAWI, CAIDA: headers only, ~1-10 Gb/s) cannot be replayed
packet by packet at 100 Gb/s by TRex's pcap push (one core, file I/O). What
the systems under test react to is the load each Rx queue gets and the mix of
packet sizes, so that is what is kept:

  - every IPv4 packet is put on the queue the dut's RSS would choose for its
    source and destination addresses (Toeplitz, mlx5's default key, 8-entry
    table: the same model as udp_multiflow.py, checked against the NIC);
  - a frame longer than the MTU (a segment the capturing NIC coalesced) counts
    as the 1514 B frames it was on the wire, plus the remainder;
  - per PERIOD seconds of trace, each queue's share of the frames becomes one
    step of a weights schedule, replayed one step per PERIOD seconds at the
    test's offered rate (so the trace's imbalance over time is kept, its
    sub-period burst structure is not);
  - a sample of the frame sizes (quantiles of the whole slice) becomes the
    size list TRex draws each packet's size from.

IPv6 packets (MAWI: ~17%) and non-IP frames are left out: l3fwd routes IPv4.

Usage (on the tgen; the trace can be streamed, no full download needed):
  curl -s <url>.pcap.gz | gunzip | harness/trace/trace_schedule.py - --seconds 60 \\
      --out /mydata/trace/mawi.json
Writes {"source", "period_s", "steps": [[t, [w0..w7]], ...], "size_list": [...],
"mean_size": ..., "summary": {...}} for rate_sweep.py --trace.
"""
import argparse
import collections
import gzip
import ipaddress
import json
import struct
import sys

QUEUES = 8
MTU_FRAME = 1514     # largest frame without FCS on a 1500 B MTU
MIN_FRAME = 60
# The dut's RSS (mlx5_rxq.c rss_hash_default_key), as in udp_multiflow.py.
MLX5_RSS_KEY = bytes([
    0x2c, 0xc6, 0x81, 0xd1, 0x5b, 0xdb, 0xf4, 0xf7, 0xfc, 0xa2, 0x83, 0x19,
    0xdb, 0x1a, 0x3e, 0x94, 0x6b, 0x9e, 0x38, 0xd9, 0x2c, 0x9c, 0x03, 0xd1,
    0xad, 0x99, 0x44, 0xa7, 0xd9, 0x56, 0x3d, 0x59, 0x06, 0x3c, 0x25, 0xf3,
    0xfc, 0x1f, 0xdc, 0x2a,
])
KEY_INT, KEY_BITS = int.from_bytes(MLX5_RSS_KEY, "big"), len(MLX5_RSS_KEY) * 8


def toeplitz(data):
    result = 0
    for bit in range(len(data) * 8):
        if data[bit // 8] & (0x80 >> (bit % 8)):
            result ^= (KEY_INT >> (KEY_BITS - 32 - bit)) & 0xFFFFFFFF
    return result


def check_rss_model():
    """The mapping udp_multiflow.py validated on the NIC (CLAUDE.md 19.25)."""
    expected = {"16.0.0.1": 0, "17.0.0.8": 1, "17.0.0.9": 2, "17.0.0.2": 3}
    dst = ipaddress.ip_address("198.18.0.1").packed
    for ip, queue in expected.items():
        assert toeplitz(ipaddress.ip_address(ip).packed + dst) % QUEUES == queue, ip


def frames_of(wire_len):
    """The frames a captured length was on a 1500 B MTU link."""
    if wire_len <= MTU_FRAME:
        return [max(MIN_FRAME, wire_len)]
    body = wire_len - 14
    full, rest = divmod(body, 1500)
    return [MTU_FRAME] * full + ([max(MIN_FRAME, rest + 14)] if rest else [])


def packets(stream):
    """(timestamp, captured bytes, wire length) of each record of a pcap."""
    header = stream.read(24)
    magic = struct.unpack("<I", header[:4])[0]
    if magic in (0xa1b2c3d4, 0xa1b23c4d):
        order, nano = "<", magic == 0xa1b23c4d
    elif magic in (0xd4c3b2a1, 0x4d3cb2a1):
        order, nano = ">", magic == 0x4d3cb2a1
    else:
        sys.exit("not a pcap file (pcapng is not supported)")
    if struct.unpack(order + "I", header[20:24])[0] != 1:
        sys.exit("only Ethernet captures (linktype 1) are supported")
    record = struct.Struct(order + "IIII")
    while True:
        head = stream.read(16)
        if len(head) < 16:
            return
        sec, frac, incl, orig = record.unpack(head)
        data = stream.read(incl)
        if len(data) < incl:
            return
        yield sec + frac / (1e9 if nano else 1e6), data, orig


def ipv4_pair(data):
    """The 8 bytes of source + destination IPv4 address, or None."""
    offset, ethertype = 14, data[12:14]
    while ethertype in (b"\x81\x00", b"\x88\xa8") and len(data) >= offset + 4:
        ethertype, offset = data[offset + 2:offset + 4], offset + 4
    if ethertype != b"\x08\x00" or len(data) < offset + 20:
        return None
    return data[offset + 12:offset + 20]


def build(stream, seconds, period, samples):
    check_rss_model()
    queue_of, steps = {}, collections.defaultdict(lambda: [0] * QUEUES)
    sizes, kept, skipped, start = collections.Counter(), 0, 0, None
    pair_frames = collections.Counter()
    for ts, data, wire_len in packets(stream):
        if start is None:
            start = ts
        if ts - start >= seconds:
            break
        pair = ipv4_pair(data)
        if pair is None:
            skipped += 1
            continue
        queue = queue_of.get(pair)
        if queue is None:
            queue = queue_of[pair] = toeplitz(pair) % QUEUES
        frames = frames_of(wire_len)
        steps[int((ts - start) // period)][queue] += len(frames)
        pair_frames[pair] += len(frames)
        for f in frames:
            sizes[f] += 1
        kept += 1
    if not steps:
        sys.exit("no IPv4 packet in the input")
    return summarize(steps, sizes, kept, skipped, len(queue_of), pair_frames, period, samples)


def quantiles(sizes, samples):
    """`samples` frame sizes spread over the distribution (TRex draws uniformly)."""
    total, ordered, out = sum(sizes.values()), sorted(sizes.items()), []
    for k in range(samples):
        target, seen = (k + 0.5) / samples * total, 0
        for size, count in ordered:
            seen += count
            if seen >= target:
                out.append(size)
                break
    return out


def summarize(steps, sizes, kept, skipped, pairs, pair_frames, period, samples):
    ordered = [steps[i] for i in sorted(steps)]
    # A queue with no frame in a step would have no stream to update; one
    # frame is noise against the ~10^5 a step holds.
    weights = [[max(c, 1) for c in counts] for counts in ordered]
    ratios = [max(w) / min(w) for w in weights]
    total_frames = sum(sizes.values())
    totals = [sum(w[q] for w in weights) for q in range(QUEUES)]
    size_list = quantiles(sizes, samples)
    return {
        "period_s": period,
        "steps": [[round(i * period, 3), w] for i, w in enumerate(weights)],
        "size_list": size_list,
        "mean_size": round(sum(s * c for s, c in sizes.items()) / total_frames, 1),
        "summary": {
            "ipv4_packets": kept, "skipped_packets": skipped, "frames": total_frames,
            "src_dst_pairs": pairs, "steps": len(weights),
            "queue_share_pct": [round(100 * t / sum(totals), 2) for t in totals],
            "max_min_ratio_per_step": {"min": round(min(ratios), 2),
                                       "median": round(sorted(ratios)[len(ratios) // 2], 2),
                                       "max": round(max(ratios), 2)},
            "top_pair_share_pct": [round(100 * c / total_frames, 2)
                                   for _, c in pair_frames.most_common(5)],
            "frames_le_128B_pct": round(100 * sum(c for s, c in sizes.items() if s <= 128)
                                        / total_frames, 1),
            "frames_ge_1400B_pct": round(100 * sum(c for s, c in sizes.items() if s >= 1400)
                                         / total_frames, 1),
        },
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("pcap", help="pcap file (.gz too), or - for stdin")
    parser.add_argument("--seconds", type=float, default=60.0,
                        help="trace seconds to use, from its start (default 60)")
    parser.add_argument("--period", type=float, default=1.0,
                        help="trace seconds per schedule step (default 1)")
    parser.add_argument("--samples", type=int, default=64,
                        help="frame sizes in the size list (default 64)")
    parser.add_argument("--source", default="", help="where the trace came from, kept in the JSON")
    parser.add_argument("--out", required=True, help="JSON for rate_sweep.py --trace")
    args = parser.parse_args()
    if args.pcap == "-":
        stream = sys.stdin.buffer
    elif args.pcap.endswith(".gz"):
        stream = gzip.open(args.pcap, "rb")
    else:
        stream = open(args.pcap, "rb")
    result = build(stream, args.seconds, args.period, args.samples)
    result["source"] = args.source or args.pcap
    with open(args.out, "w") as f:
        json.dump(result, f)
    print(json.dumps(result["summary"], indent=1))
    print("mean frame %.0f B; %d steps of %g s -> %s"
          % (result["mean_size"], len(result["steps"]), args.period, args.out))


if __name__ == "__main__":
    main()
