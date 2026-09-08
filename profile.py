"""CloudLab profile: two-node back-to-back 100 GbE testbed for DPDK receive-path research.

Topology:

    +--------------------+   100 GbE (back-to-back)   +--------------------+
    |        dut         |<-------------------------->|        tgen        |
    |  system under test |                            | traffic generator |
    +--------------------+                            +--------------------+

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
    "r650": NodeType(
        CLEMSON_CM,
        "Fallback. Xeon Platinum 8360Y (Ice Lake), 2 sockets / 72 cores, "
        "ConnectX-6 100 Gb. Pin the workload to socket 0 (NPS1) and read only "
        "that socket's uncore counters.",
    ),
}

DEFAULT_NODE_TYPE = "sm110p"

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
        "osImage",
        "Disk image",
        portal.ParameterType.IMAGE,
        UBUNTU_22_IMAGE,
        longDescription=(
            "Ubuntu 22.04 ships an in-tree rdma-core new enough for the mlx5 "
            "poll-mode driver. Change only after confirming driver support."
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
    context.verifyParameters()
    return context, params


def build_node(request, params, name):
    """Create one bare-metal node: experiment NIC, scratch space, boot-time setup."""
    node_type = SUPPORTED_NODE_TYPES[params.hardwareType]

    node = request.RawPC(name)
    node.hardware_type = params.hardwareType
    node.disk_image = params.osImage
    node.component_manager_id = node_type.component_manager

    scratch = node.Blockstore(name + "-scratch", "/mydata")
    scratch.size = params.scratchSize

    node.addService(pg.Execute(shell="sh", command=NODE_SETUP_COMMAND))

    interface = node.addInterface("experiment-nic")
    if params.dataPlaneIp:
        interface.addAddress(
            pg.IPv4Address(DATA_PLANE_IPS[name], DATA_PLANE_NETMASK)
        )
    return interface


def build_experiment_link(request, interfaces):
    """Wire the nodes with an unshaped, native-rate point-to-point link."""
    link = request.Link("experiment-link")
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

    interfaces = [
        build_node(request, params, name) for name in EXPERIMENT_NODES
    ]
    build_experiment_link(request, interfaces)

    context.printRequestRSpec(request)


main()
