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

Tunables (console: -t pkt_size=1500,flows=4096,hot_share=0.3):
  pkt_size   Frame size in bytes, excluding the 4-byte FCS (default 1500)
  flows      Number of distinct source IPs (default 4096)
  hot_share  Fraction of the data rate sent as one flow, 0 to 1 (default 0)
"""
from trex_stl_lib.api import *

LATENCY_PG_ID = 1
LATENCY_PPS = 1000

SRC_IP_FIRST = "16.0.0.1"
DST_IP = "198.18.0.1"


def build_base_packet(pkt_size):
    headers = Ether() / IP(src=SRC_IP_FIRST, dst=DST_IP) / UDP(sport=1025, dport=12)
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

    def get_streams(self, direction=0, pkt_size=1500, flows=4096, hot_share=0.0, **kwargs):
        pkt_size = int(pkt_size)
        flows = int(flows)
        hot_share = float(hot_share)
        if not 0.0 <= hot_share <= 1.0:
            raise ValueError("hot_share must be between 0 and 1, got %g" % hot_share)
        base = build_base_packet(pkt_size)

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
                packet=STLPktBuilder(pkt=base),
                mode=STLTXCont(percentage=100 * hot_share),
            ))
        latency = STLStream(
            name="latency",
            packet=STLPktBuilder(pkt=base),
            mode=STLTXCont(pps=LATENCY_PPS),
            flow_stats=STLFlowLatencyStats(pg_id=LATENCY_PG_ID),
        )
        return streams + [latency]


def register():
    return UdpMultiFlow()
