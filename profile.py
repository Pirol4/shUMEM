"""CloudLab profile: two-node 100 GbE testbed for DPDK receive-path research.

Topology:

    +--------------------+   100 GbE, experiment-link    +--------------------+
    |        dut         |<----------------------------->|        tgen        |
    |  system under test |   100 GbE, experiment-link-2  | traffic generator |
    |                    |<- - - - - - (optional) - - - >|                    |
    +--------------------+                               +--------------------+

With "Experiment links" at 2 the second port of each node's ConnectX-6 Dx is
wired too: 2 x 100 Gb/s, the 200 Gb/s of the rxBisect paper's two NICs
(CLAUDE.md 21). Only node types whose two ports are both for experiments.

Primary node type: sm110p (CloudLab Wisconsin) -- single-socket Intel Xeon
Silver 4314 (Ice Lake), 16 cores, ~24 MB LLC, single NUMA domain, dual-port
Mellanox ConnectX-6 DX 100 Gb NIC. The hardware constraints (Intel uncore
tooling, a 100 GbE link, and the mlx5 PMD that shRing/rxBisect patch) are
documented in the project notes; r650 at Clemson is the recorded fallback.

Both nodes are always bound to the same cluster: the back-to-back link is a
physical cable and cannot span sites.

Usage: create a repo-backed profile at
https://www.cloudlab.us/manage_profile.php pointing at this repository, then
instantiate. On each node the repository is checked out at /local/repository and
setup/node-setup.sh runs on every boot.
"""

from collections import namedtuple

import geni.portal as portal
import geni.rspec.pg as pg


# --- Supported hardware ------------------------------------------------------
# Every node type we are willing to run on, paired with the cluster that owns
# it. The back-to-back link forces both nodes onto one cluster, so this table is
# also the single source of truth for site binding.

WISCONSIN_CM = "urn:publicid:IDN+wisc.cloudlab.us+authority+cm"
CLEMSON_CM = "urn:publicid:IDN+clemson.cloudlab.us+authority+cm"

NodeType = namedtuple("NodeType", ["component_manager", "summary"])

SUPPORTED_NODE_TYPES = {
    "sm110p": NodeType(
        WISCONSIN_CM,
        "Primary. Xeon Silver 4314 (Ice Lake), 1 socket / 16 cores, ~24 MB "
        "LLC, single NUMA, ConnectX-6 DX 100 Gb.",
    ),
    "sm220u": NodeType(
        WISCONSIN_CM,
        "200 Gb/s runs. 2 x Xeon Silver 4314 (Ice Lake), 2 sockets / 32 "
        "cores, ConnectX-6 DX 2 x 100 Gb, both ports for experiments.",
    ),
    "r650": NodeType(
        CLEMSON_CM,
        "Fallback. Xeon Platinum 8360Y (Ice Lake), 2 sockets / 72 cores, "
        "ConnectX-6 100 Gb. Pin the workload to socket 0 (NPS1) and read only "
        "that socket's uncore counters.",
    ),
}

DEFAULT_NODE_TYPE = "sm110p"

# Node types whose second ConnectX-6 Dx port is also on the experiment network
# (docs.cloudlab.us/hardware.html: "both ports available for experiment use").
TWO_PORT_NODE_TYPES = ("sm110p", "sm220u")

# Ubuntu 20.04 was tried first, to match the era of DPDK 21.05 (which shRing
# pins). CloudLab rejects it: UBUNTU20-64-STD "does not run on this hardware
# type" on sm110p, and the experiment never leaves the Pending state. 22.04 is
# the oldest image this node type takes, and it is the one already validated
# end-to-end on it.
#
# The DPDK 21.05 build problem that motivated 20.04 is handled in the build
# instead, by setup/patches/. Nothing here needs to change for it.
UBUNTU_22_IMAGE = "urn:publicid:IDN+emulab.net+image+emulab-ops//UBUNTU22-64-STD"


# --- Fixed experiment topology --------------------------------------------
# The experiment NIC carries no kernel L3 address by default: the port is handed
# to DPDK, and a host IP would let the kernel stack answer ARP/ICMP and steal
# flows. SSH uses the CloudLab control network instead. The optional data-plane
# IP exists only for a quick "is the cable up?" ping before DPDK binds the port.

EXPERIMENT_NODES = ("dut", "tgen")
NODE_SETUP_COMMAND = "sudo /local/repository/setup/node-setup.sh"

DATA_PLANE_NETMASK = "255.255.255.0"
DATA_PLANE_IPS = {"dut": "10.10.1.1", "tgen": "10.10.1.2"}

# The harness finds experiment port p by its address in 10.10.<p+1>.0/24
# (setup/topology.py). CloudLab gives link 1 10.10.1.x on its own; with two
# links both are set explicitly, so the numbering never depends on the order
# CloudLab assigns subnets in.
LINK_IPS = (
    {"dut": "10.10.1.1", "tgen": "10.10.1.2"},
    {"dut": "10.10.2.1", "tgen": "10.10.2.2"},
)


def bind_parameters():
    """Declare the instantiation-form knobs and return (context, bound params)."""
    context = portal.Context()

    context.defineParameter(
        "hardwareType",
        "Physical node type",
        portal.ParameterType.STRING,
        DEFAULT_NODE_TYPE,
        legalValues=[
            (name, "%s -- %s" % (name, spec.summary))
            for name, spec in sorted(SUPPORTED_NODE_TYPES.items())
        ],
        longDescription=(
            "Both nodes use this type and are bound to the cluster that owns it."
        ),
    )
    context.defineParameter(
        "experimentLinks",
        "Experiment links (100 GbE each)",
        portal.ParameterType.INTEGER,
        1,
        legalValues=[(1, "1 -- one port per node, 100 Gb/s"),
                     (2, "2 -- both ports, 2 x 100 Gb/s (sm110p, sm220u)")],
        longDescription=(
            "2 wires the second port of each node's ConnectX-6 Dx on a second "
            "link (10.10.2.0/24), for 200 Gb/s runs (CLAUDE.md 21)."
        ),
    )
    context.defineParameter(
        "osImage",
        "Disk image",
        portal.ParameterType.IMAGE,
        UBUNTU_22_IMAGE,
        longDescription=(
            "Ubuntu 22.04 ships an in-tree rdma-core new enough for the mlx5 "
            "poll-mode driver, and is the oldest image sm110p accepts -- "
            "20.04 is refused by CloudLab on this hardware type. DPDK 21.05 "
            "needs setup/patches/ to build here; see CLAUDE.md section 14.1."
        ),
    )
    context.defineParameter(
        "scratchSize",
        "Scratch filesystem size",
        portal.ParameterType.STRING,
        "100GB",
        longDescription=(
            "Mounted at /mydata for DPDK source trees, captures, and result "
            "archives; the root filesystem is too small."
        ),
    )
    context.defineParameter(
        "dataPlaneIp",
        "Put a kernel IP on the experiment NIC",
        portal.ParameterType.BOOLEAN,
        False,
        longDescription=(
            "Off by default. Turn on only for a link sanity check (ping) before "
            "DPDK takes over the port; leave off for measurement runs."
        ),
    )

    # legalValues already rejects an unknown hardwareType; verifyParameters
    # turns any accumulated error into a form-level message.
    params = context.bindParameters()
    if params.experimentLinks == 2 and params.hardwareType not in TWO_PORT_NODE_TYPES:
        context.reportError(portal.ParameterError(
            "%s has one experiment port; 2 links need one of %s"
            % (params.hardwareType, ", ".join(TWO_PORT_NODE_TYPES)),
            ["experimentLinks", "hardwareType"]))
    context.verifyParameters()
    return context, params


def build_node(request, params, name):
    """Create one bare-metal node: experiment NICs, scratch space, boot-time setup.

    Returns its experiment interfaces, one per link."""
    node_type = SUPPORTED_NODE_TYPES[params.hardwareType]

    node = request.RawPC(name)
    node.hardware_type = params.hardwareType
    node.disk_image = params.osImage
    node.component_manager_id = node_type.component_manager

    scratch = node.Blockstore(name + "-scratch", "/mydata")
    scratch.size = params.scratchSize

    node.addService(pg.Execute(shell="sh", command=NODE_SETUP_COMMAND))

    interfaces = []
    for link in range(params.experimentLinks):
        interface = node.addInterface("experiment-nic" + ("" if link == 0 else "-%d" % (link + 1)))
        if params.experimentLinks > 1:
            interface.addAddress(pg.IPv4Address(LINK_IPS[link][name], DATA_PLANE_NETMASK))
        elif params.dataPlaneIp:
            interface.addAddress(
                pg.IPv4Address(DATA_PLANE_IPS[name], DATA_PLANE_NETMASK)
            )
        interfaces.append(interface)
    return interfaces


def build_experiment_link(request, interfaces, name="experiment-link"):
    """Wire the nodes with an unshaped, native-rate point-to-point link."""
    link = request.Link(name)
    for interface in interfaces:
        link.addInterface(interface)

    # No bandwidth, delay, or loss is requested, so CloudLab wires the ports at
    # their native 100 Gb rate with no shaping node in the path; best_effort
    # keeps it that way. If the topology view ever shows an extra node on this
    # link, force it off explicitly:
    #     from geni.rspec import emulab
    #     link.addChild(emulab.setNoBandwidthShaping())
    link.best_effort = True
    return link


def main():
    context, params = bind_parameters()
    request = context.makeRequestRSpec()

    per_node = [build_node(request, params, name) for name in EXPERIMENT_NODES]
    for link in range(params.experimentLinks):
        build_experiment_link(
            request, [interfaces[link] for interfaces in per_node],
            "experiment-link" + ("" if link == 0 else "-%d" % (link + 1)))

    context.printRequestRSpec(request)


main()
