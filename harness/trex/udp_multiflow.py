"""Balanced multi-flow UDP load for the privRing vs shRing baseline.

One continuous data stream whose source IP walks through `flows` addresses,
so the dut's RSS (ETH_RSS_IP: hashes src+dst IP only) spreads it evenly over
every Rx queue. The destination 198.18.0.1 matches l3fwd's built-in LPM route
198.18.0.0/24 -> port 0, so the dut sends each packet back out the port it
arrived on, towards the tgen.

A second, low-rate stream carries TRex latency signatures. The data stream
deliberately has no per-stream flow stats: on mlx5 those need hardware flow
rules, and the port counters already give loss.

Tunables (console: -t pkt_size=1500,flows=4096):
  pkt_size   Frame size in bytes, excluding the 4-byte FCS (default 1500)
  flows      Number of distinct source IPs (default 4096)
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

    def get_streams(self, direction=0, pkt_size=1500, flows=4096, **kwargs):
        pkt_size = int(pkt_size)
        flows = int(flows)
        base = build_base_packet(pkt_size)

        data = STLStream(
            name="data",
            packet=STLPktBuilder(pkt=base, vm=source_ip_sweep(flows)),
            mode=STLTXCont(percentage=100),
        )
        latency = STLStream(
            name="latency",
            packet=STLPktBuilder(pkt=base),
            mode=STLTXCont(pps=LATENCY_PPS),
            flow_stats=STLFlowLatencyStats(pg_id=LATENCY_PG_ID),
        )
        return [data, latency]


def register():
    return UdpMultiFlow()
