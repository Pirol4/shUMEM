#!/usr/bin/env python3
"""CPU and port layout of a testbed node, for every setup and harness script.

One port (sm110p, CLAUDE.md 15-20) the layout is the one every result so far
used: 8 queues on lcores 1-8, CPU 0 for the OS, PCM and ssh, CPUs 9-15 free
for co-located memory hogs. Two ports (sm220u, 2 x 100 Gb/s, CLAUDE.md 21)
it is derived from the machine instead of written down, because which socket
the NIC hangs off and how the CPUs are numbered are only known on the node.

Experiment ports. Port p is the interface holding 10.10.<p+1>.x: the profile
puts link 1 on 10.10.1.0/24 and link 2 on 10.10.2.0/24; the dut holds .1, the
tgen .2. Both ports of the ConnectX-6 Dx are on one NIC, so on one NUMA node.

Lcores (the dut's forwarding cores, the tgen's TRex threads) are physical
cores of the NIC's NUMA node, never a hyperthread sibling (the CPU numbers are
the same with or without `nosmt`), CPU 0 left out. On a multi-socket node
whose NIC socket is short of cores, CPU 0 is used too and housekeeping moves
to the other socket -- what the rxBisect paper does with its 16 cores per
socket, 8 per NIC. Queues per port are a power of two (8 when they fit): the
traffic profile predicts the dut's RSS queue as hash % queues, which holds
only when the indirection table has exactly one entry per queue (mlx5 sizes
it to the queue count rounded up to a power of two).

Housekeeping is every physical core left, the not isolated ones first; its
first CPU runs PCM, ssh and the sweep. Hog CPUs are the NIC socket's
physical cores no lcore and no housekeeping CPU uses: memory hogs must share
the LLC and the memory controllers with the lcores to compete with them.

Usage:
  setup/topology.py [--ports N] [--queues Q] [--role dut|tgen] <what>
  what: shell (KEY=value lines, for eval), report, or one of
        lcores busy housekeeping hk-cpu hog-cpus ifaces pcis numa ports queues
  SHUMEM_SYSFS (default /sys) and SHUMEM_IP_ADDR (a file holding the output
  of `ip -o -4 addr show`) replace the machine, for tests.
"""
import argparse
import os
import re
import shlex
import socket
import subprocess
import sys

SYSFS = os.environ.get("SHUMEM_SYSFS", "/sys")
EXPERIMENT_NET = re.compile(r"^10\.10\.(\d+)\.(\d+)/")
TREX_WORKERS_PER_PORT = 6     # 100 Gb/s of 1500 B frames: CLAUDE.md 16.3
MAX_QUEUES = 8


class LayoutError(Exception):
    pass


def read(path, default=None):
    try:
        with open(os.path.join(SYSFS, path)) as f:
            return f.read().strip()
    except OSError:
        return default


def parse_cpulist(text):
    cpus = []
    for part in (text or "").split(","):
        part = part.strip()
        if not part:
            continue
        lo, _, hi = part.partition("-")
        cpus.extend(range(int(lo), int(hi or lo) + 1))
    return sorted(set(cpus))


def format_cpulist(cpus):
    """[0, 1, 2, 5] -> '0-2,5'."""
    cpus, runs = sorted(cpus), []
    for cpu in cpus:
        if runs and cpu == runs[-1][1] + 1:
            runs[-1][1] = cpu
        else:
            runs.append([cpu, cpu])
    return ",".join(str(a) if a == b else "%d-%d" % (a, b) for a, b in runs)


def numa_nodes():
    nodes = parse_cpulist(read("devices/system/node/online", ""))
    return nodes or [0]


def node_cpus(node):
    listed = read("devices/system/node/node%d/cpulist" % node)
    if listed is None:      # no NUMA in sysfs: every present CPU is node 0
        return parse_cpulist(read("devices/system/cpu/present", "0"))
    return parse_cpulist(listed)


def physical_cores(node):
    """Online CPUs of `node` that are the first thread of their core."""
    cores = []
    for cpu in node_cpus(node):
        if read("devices/system/cpu/cpu%d/online" % cpu, "1") != "1":
            continue
        siblings = parse_cpulist(read("devices/system/cpu/cpu%d/topology/thread_siblings_list"
                                      % cpu, str(cpu)))
        if cpu == min(siblings or [cpu]):
            cores.append(cpu)
    return cores


def isolated_cpus():
    return set(parse_cpulist(read("devices/system/cpu/isolated", "")))


def ip_addresses():
    path = os.environ.get("SHUMEM_IP_ADDR")
    if path:
        with open(path) as f:
            return f.read()
    try:
        return subprocess.run(["ip", "-o", "-4", "addr", "show"], capture_output=True,
                              text=True, check=False).stdout
    except OSError:
        return ""


def experiment_ports():
    """[(port, iface, host octet)], port p on 10.10.<p+1>.0/24."""
    found = {}
    for line in ip_addresses().splitlines():
        fields = line.split()
        if len(fields) < 4:
            continue
        match = EXPERIMENT_NET.match(fields[3])
        if match and int(match.group(1)) >= 1:
            found[int(match.group(1)) - 1] = (fields[1], int(match.group(2)))
    return [(port, iface, host) for port, (iface, host) in sorted(found.items())]


def pci_of(iface):
    target = os.path.realpath(os.path.join(SYSFS, "class/net", iface, "device"))
    return os.path.basename(target)


def numa_of(pci):
    node = read("bus/pci/devices/%s/numa_node" % pci, "-1")
    return max(int(node), 0)


def guess_role(ports):
    hosts = {host for _, _, host in ports}
    if hosts == {1}:
        return "dut"
    if hosts == {2}:
        return "tgen"
    name = socket.gethostname()
    return "tgen" if name.startswith("tgen") else "dut"


def candidates(nic_node, need):
    """Physical cores lcores may use, in order: the NIC socket's without CPU
    0, or with it when the socket is short and another socket can take the
    housekeeping."""
    local = physical_cores(nic_node)
    others = [c for n in numa_nodes() if n != nic_node for c in physical_cores(n)]
    without_zero = [c for c in local if c != 0]
    if len(without_zero) >= need or not others:
        return without_zero
    return local


def default_queues(nic_node, ports):
    queues = MAX_QUEUES
    while queues > 1 and len(candidates(nic_node, ports * queues)) < ports * queues:
        queues //= 2
    return queues


class Layout:
    def __init__(self, ports=None, queues=None, role=None):
        found = experiment_ports()
        self.role = role or guess_role(found)
        self.ifaces = [iface for _, iface, _ in found]
        self.pcis = [pci_of(iface) for iface in self.ifaces]
        self.numa = numa_of(self.pcis[0]) if self.pcis else 0
        self.ports = ports if ports is not None else max(len(found), 1)
        if self.ports < 1:
            raise LayoutError("ports must be at least 1")
        if found and self.ports > len(found):
            raise LayoutError("%d ports asked, %d experiment interfaces (10.10.<n>.x) found"
                              % (self.ports, len(found)))
        isolated = isolated_cpus()
        if self.role == "tgen":
            self._trex_layout(queues)
        else:
            self._dut_layout(queues)
        everyone = [c for n in numa_nodes() for c in physical_cores(n)]
        rest = [c for c in everyone if c not in self.busy]
        free = [c for c in rest if c not in isolated] or rest
        if not free:
            raise LayoutError("no CPU left for housekeeping")
        self.housekeeping = sorted(free)
        self.hk_cpu = self.housekeeping[0]
        self.hog_cpus = [c for c in physical_cores(self.numa)
                         if c not in self.busy and c != self.hk_cpu]

    def _dut_layout(self, queues):
        self.queues = queues or default_queues(self.numa, self.ports)
        if self.queues < 1 or self.queues > MAX_QUEUES or self.queues & (self.queues - 1):
            raise LayoutError("queues per port must be 1, 2, 4 or 8, not %d" % self.queues)
        need = self.ports * self.queues
        pool = candidates(self.numa, need)
        if len(pool) < need:
            raise LayoutError("%d ports x %d queues need %d physical cores on NUMA node %d; "
                              "it has %d (QUEUES=%d fits)" % (
                                  self.ports, self.queues, need, self.numa, len(pool),
                                  default_queues(self.numa, self.ports)))
        self.lcores = pool[:need]
        self.busy = list(self.lcores)

    def _trex_layout(self, workers):
        self.queues = 0
        workers = workers or TREX_WORKERS_PER_PORT * self.ports
        need = 1 + workers
        pool = candidates(self.numa, need)
        if len(pool) < need:
            raise LayoutError("TRex needs %d physical cores on NUMA node %d (latency + %d "
                              "workers); it has %d" % (need, self.numa, workers, len(pool)))
        self.lcores = pool[:need]
        self.trex_latency, self.trex_workers = self.lcores[0], self.lcores[1:]
        self.busy = list(self.lcores)

    def lcore_of(self, port, queue):
        if self.role != "dut":
            raise LayoutError("p<port>q<queue> names a dut lcore")
        if not (0 <= port < self.ports and 0 <= queue < self.queues):
            raise LayoutError("p%dq%d: ports 0-%d, queues 0-%d" % (
                port, queue, self.ports - 1, self.queues - 1))
        return self.lcores[port * self.queues + queue]

    def values(self):
        out = {
            "ROLE": self.role, "PORTS": self.ports, "QUEUES": self.queues,
            "NUMA": self.numa, "NODES": len(numa_nodes()),
            "IFACES": " ".join(self.ifaces), "PCIS": " ".join(self.pcis),
            "LCORES": format_cpulist(self.lcores),
            "LCORE_LIST": " ".join(map(str, self.lcores)),
            "BUSY": format_cpulist(self.busy),
            "HOUSEKEEPING": format_cpulist(self.housekeeping),
            "HK_CPU": self.hk_cpu,
            "HOG_CPUS": format_cpulist(self.hog_cpus),
        }
        if self.role == "tgen":
            out.update(TREX_LATENCY=self.trex_latency,
                       TREX_WORKERS=" ".join(map(str, self.trex_workers)))
        return out


def resolve_lcore(layout, name):
    """A CPU number as given, or p<port>q<queue> -> that queue's lcore."""
    match = re.fullmatch(r"p(\d+)q(\d+)", name)
    if match:
        return layout.lcore_of(int(match.group(1)), int(match.group(2)))
    if not name.isdigit():
        raise LayoutError("%r: want a CPU number or p<port>q<queue>" % name)
    return int(name)


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--ports", type=int)
    parser.add_argument("--queues", type=int,
                        help="queues per port (dut), or TRex workers (tgen)")
    parser.add_argument("--role", choices=("dut", "tgen"))
    parser.add_argument("what", help="shell, report, a key (lcores, busy, ...), "
                                     "or lcore:<p<port>q<queue>|cpu>[,...]")
    args = parser.parse_args()
    try:
        layout = Layout(args.ports, args.queues, args.role)
        if args.what.startswith("lcore:"):
            print(",".join(str(resolve_lcore(layout, n)) for n in args.what[6:].split(",")))
            return
        values = layout.values()
    except LayoutError as error:
        sys.exit("topology: %s" % error)
    if args.what == "shell":
        for key, value in values.items():
            print("SHUMEM_%s=%s" % (key, shlex.quote(str(value))))
    elif args.what == "report":
        for key, value in values.items():
            print("%-13s %s" % (key.lower(), value))
    else:
        key = args.what.upper().replace("-", "_")
        if key not in values:
            sys.exit("topology: unknown %r; one of: shell report %s"
                     % (args.what, " ".join(k.lower().replace("_", "-") for k in values)))
        print(values[key])


if __name__ == "__main__":
    main()
