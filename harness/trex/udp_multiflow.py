"""Balanced multi-flow UDP load for the privRing vs shRing baseline.

One continuous data stream whose source IP walks through `flows` addresses,
so the dut's RSS (ETH_RSS_IP: hashes src+dst IP only) spreads it evenly over
every Rx queue. The destination 198.18.0.1 matches l3fwd's built-in LPM route
198.18.0.0/24 -> port 0, so the dut sends each packet back out the port it
arrived on, towards the tgen.

A second, low-rate stream carries TRex latency signatures. The data stream
deliberately has no per-stream flow stats: on mlx5 those need hardware flow
rules, and the port counters already give loss.

Imbalance (hot_share > 0): that fraction of the data rate is split off into a
"hot" stream from a single source IP, which RSS sends to a single queue; the
rest stays spread over all queues. The hot IP is the latency stream's own, so
both land on the same queue and the latency stream measures the hot queue.

hot_queue picks which Rx queue the hot flow (and the latency stream) hits: the
source IP is chosen by computing the dut's RSS hash (Toeplitz over source and
destination IPv4 with mlx5's default key, indirection table of 8 entries, queue
i in entry i). Checked against the measured split on 2026-10-19 (CLAUDE.md
19.25): it predicts 511 of the 4096 data flows on queue 3, 513 on queue 4, 512
elsewhere, and 16.0.0.1 on queue 0, as the per-queue counters show.

Tunables (console: -t pkt_size=1500,flows=4096,hot_share=0.3,hot_queue=0):
  pkt_size   Frame size in bytes, excluding the 4-byte FCS (default 1500)
  flows      Number of distinct source IPs (default 4096)
  hot_share  Fraction of the data rate sent as one flow, 0 to 1 (default 0)
  hot_queue  Rx queue of the hot and latency flows, 0-7 (default 0)
"""
import ipaddress
from trex_stl_lib.api import *

LATENCY_PG_ID = 1
LATENCY_PPS = 1000

SRC_IP_FIRST = "16.0.0.1"
DST_IP = "198.18.0.1"
# Single-flow (hot, latency) source IPs are searched here, outside the data
# flows' 16.0.0.1-16.0.15.255; 16.0.0.1 itself stays the queue-0 choice so
# hot_queue=0 sends exactly what earlier runs sent.
HOT_IP_SEARCH_FIRST = "17.0.0.1"

# The dut's RSS (mlx5_rxq.c rss_hash_default_key; RETA of 8, entry i = queue i).
MLX5_RSS_KEY = bytes([
    0x2c, 0xc6, 0x81, 0xd1, 0x5b, 0xdb, 0xf4, 0xf7, 0xfc, 0xa2, 0x83, 0x19,
    0xdb, 0x1a, 0x3e, 0x94, 0x6b, 0x9e, 0x38, 0xd9, 0x2c, 0x9c, 0x03, 0xd1,
    0xad, 0x99, 0x44, 0xa7, 0xd9, 0x56, 0x3d, 0x59, 0x06, 0x3c, 0x25, 0xf3,
    0xfc, 0x1f, 0xdc, 0x2a,
])
RSS_QUEUES = 8


def toeplitz_hash(data, key=MLX5_RSS_KEY):
    key_int, key_bits = int.from_bytes(key, "big"), len(key) * 8
    result = 0
    for bit in range(len(data) * 8):
        if data[bit // 8] & (0x80 >> (bit % 8)):
            result ^= (key_int >> (key_bits - 32 - bit)) & 0xFFFFFFFF
    return result


def rss_queue(src_ip, dst_ip=DST_IP):
    data = ipaddress.ip_address(src_ip).packed + ipaddress.ip_address(dst_ip).packed
    return toeplitz_hash(data) % RSS_QUEUES


def single_flow_ip(queue):
    """A source IP whose packets the dut's RSS puts on Rx queue `queue`."""
    if rss_queue(SRC_IP_FIRST) == queue:
        return SRC_IP_FIRST
    candidate = int(ipaddress.ip_address(HOT_IP_SEARCH_FIRST))
    while rss_queue(str(ipaddress.ip_address(candidate))) != queue:
        candidate += 1
    return str(ipaddress.ip_address(candidate))


def build_base_packet(pkt_size, src_ip=SRC_IP_FIRST):
    headers = Ether() / IP(src=src_ip, dst=DST_IP) / UDP(sport=1025, dport=12)
    padding = max(0, pkt_size - len(headers))
    return headers / Raw(b"\x00" * padding)


def source_ip_sweep(flows):
    """Field-engine program: increment the source IP per packet, fix the checksum."""
    first = ip2int(SRC_IP_FIRST)
    return STLScVmRaw([
        STLVmFlowVar(name="src_ip", min_value=first, max_value=first + flows - 1,
                     size=4, op="inc"),
        STLVmWrFlowVar(fv_name="src_ip", pkt_offset="IP.src"),
        STLVmFixIpv4(offset="IP"),
    ])


class UdpMultiFlow(object):

    def get_streams(self, direction=0, pkt_size=1500, flows=4096, hot_share=0.0,
                    hot_queue=0, **kwargs):
        pkt_size = int(pkt_size)
        flows = int(flows)
        hot_share = float(hot_share)
        hot_queue = int(hot_queue)
        if not 0.0 <= hot_share <= 1.0:
            raise ValueError("hot_share must be between 0 and 1, got %g" % hot_share)
        if not 0 <= hot_queue < RSS_QUEUES:
            raise ValueError("hot_queue must be 0-%d, got %d" % (RSS_QUEUES - 1, hot_queue))
        base = build_base_packet(pkt_size)
        single = build_base_packet(pkt_size, single_flow_ip(hot_queue))

        # Percentages only set the ratio between streams: the -m multiplier
        # scales the whole profile to the requested share of line rate.
        streams = []
        if hot_share < 1.0:
            streams.append(STLStream(
                name="data",
                packet=STLPktBuilder(pkt=base, vm=source_ip_sweep(flows)),
                mode=STLTXCont(percentage=100 * (1.0 - hot_share)),
            ))
        if hot_share > 0.0:
            streams.append(STLStream(
                name="hot",
                packet=STLPktBuilder(pkt=single),
                mode=STLTXCont(percentage=100 * hot_share),
            ))
        latency = STLStream(
            name="latency",
            packet=STLPktBuilder(pkt=single),
            mode=STLTXCont(pps=LATENCY_PPS),
            flow_stats=STLFlowLatencyStats(pg_id=LATENCY_PG_ID),
        )
        return streams + [latency]


def register():
    return UdpMultiFlow()
