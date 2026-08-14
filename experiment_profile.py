"""CloudLab profile: two-node back-to-back 100 GbE testbed for DPDK receive-path research.

Topology:

    +-------------------+   100 GbE (back-to-back)   +-------------------+
    |        dut        |<-------------------------->|       tgen        |
    |  system under test|                            | traffic generator |
    +-------------------+                            +-------------------+

The default node type is sm110p (CloudLab Wisconsin): a single-socket Intel Xeon
Silver 4314 (Ice Lake) with 16 cores, roughly 24 MB of last-level cache, and a
dual-port Mellanox ConnectX-6 DX 100 Gb NIC with both ports usable. Ice Lake
provides Intel DDIO, which is required to measure the I/O working set effects
this project studies. AMD nodes such as d6515 and c6525-100g do NOT provide
DDIO and are unsuitable as the primary platform.

Usage: create a new profile at https://www.cloudlab.us/manage_profile.php,
select "Git Repo" or paste this file as the profile source, then instantiate.
"""

import geni.portal as portal
import geni.rspec.pg as pg


# --- Site and image constants ---------------------------------------------

WISCONSIN_MANAGER_URN = "urn:publicid:IDN+wisc.cloudlab.us+authority+cm"
CLEMSON_MANAGER_URN = "urn:publicid:IDN+clemson.cloudlab.us+authority+cm"

UBUNTU_22_IMAGE_URN = (
    "urn:publicid:IDN+emulab.net+image+emulab-ops//UBUNTU22-64-STD"
)

# --- Experiment network ----------------------------------------------------

EXPERIMENT_NETMASK = "255.255.255.0"
DUT_ADDRESS = "10.10.1.1"
TRAFFIC_GENERATOR_ADDRESS = "10.10.1.2"

# CloudLab expresses link bandwidth in kilobits per second.
LINK_BANDWIDTH_100G_KBPS = 100 * 1000 * 1000

# --- Boot-time setup -------------------------------------------------------
# Only steps that must survive every reboot live here. Toolchain installation
# and DPDK builds are performed manually so that each change is deliberate.

BOOTSTRAP_COMMAND = " && ".join([
    "sudo sh -c 'echo 8192 > /sys/kernel/mm/hugepages/hugepages-2048kB/nr_hugepages'",
    "sudo mkdir -p /mnt/huge",
    "sudo mount -t hugetlbfs nodev /mnt/huge",
    "sudo systemctl stop irqbalance || true",
    "sudo sh -c 'for g in /sys/devices/system/cpu/cpu*/cpufreq/scaling_governor;"
    " do echo performance > $g 2>/dev/null || true; done'",
])


def define_parameters(context):
    """Declare the knobs exposed on the CloudLab instantiation form."""
    context.defineParameter(
        "hardwareType",
        "Physical node type",
        portal.ParameterType.STRING,
        "sm110p",
        longDescription=(
            "sm110p (Wisconsin) is the primary choice. r650 (Clemson) is the "
            "fallback when sm110p is unavailable. Both are Intel Ice Lake with "
            "100 Gb Mellanox NICs."
        ),
    )
    context.defineParameter(
        "osImage",
        "Disk image",
        portal.ParameterType.IMAGE,
        UBUNTU_22_IMAGE_URN,
        longDescription=(
            "Ubuntu 22.04 has in-tree rdma-core new enough for the mlx5 poll "
            "mode driver. Change only after confirming driver support."
        ),
    )
    context.defineParameter(
        "scratchSize",
        "Scratch filesystem size",
        portal.ParameterType.STRING,
        "100GB",
        longDescription=(
            "Mounted at /mydata. The root filesystem is too small for DPDK "
            "source trees, traffic captures, and result archives."
        ),
    )
    context.defineParameter(
        "pinToSite",
        "Pin the experiment to a specific cluster",
        portal.ParameterType.BOOLEAN,
        True,
        longDescription=(
            "When enabled, the experiment is bound to the cluster matching the "
            "selected node type. Disable to choose the cluster manually."
        ),
    )
    return context.bindParameters()


def manager_urn_for(hardware_type):
    """Return the component manager that owns the given node type."""
    if hardware_type.startswith("sm"):
        return WISCONSIN_MANAGER_URN
    if hardware_type.startswith("r6") or hardware_type.startswith("c66"):
        return CLEMSON_MANAGER_URN
    return None


def build_node(request, params, name, address):
    """Create one bare-metal node with an experiment interface and scratch space."""
    node = request.RawPC(name)
    node.hardware_type = params.hardwareType
    node.disk_image = params.osImage

    manager_urn = manager_urn_for(params.hardwareType)
    if params.pinToSite and manager_urn is not None:
        node.component_manager_id = manager_urn

    interface = node.addInterface("experiment-nic")
    interface.addAddress(pg.IPv4Address(address, EXPERIMENT_NETMASK))

    scratch = node.Blockstore(name + "-scratch", "/mydata")
    scratch.size = params.scratchSize
    scratch.placement = "any"

    node.addService(pg.Execute(shell="sh", command=BOOTSTRAP_COMMAND))

    return node, interface


def main():
    context = portal.Context()
    params = define_parameters(context)
    context.verifyParameters()

    request = context.makeRequestRSpec()

    dut, dut_interface = build_node(request, params, "dut", DUT_ADDRESS)
    tgen, tgen_interface = build_node(
        request, params, "tgen", TRAFFIC_GENERATOR_ADDRESS
    )

    # A direct point-to-point link. Requesting the full physical rate keeps
    # CloudLab from inserting a shaping node between the two servers.
    experiment_link = request.Link("experiment-link")
    experiment_link.addInterface(dut_interface)
    experiment_link.addInterface(tgen_interface)
    experiment_link.bandwidth = LINK_BANDWIDTH_100G_KBPS

    context.printRequestRSpec(request)


main()
