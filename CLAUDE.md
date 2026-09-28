# Project Context — Decoupled Receive Rings with a Shared UMEM in DPDK

> **How to use this file.** Save it as `CLAUDE.md` at the root of the working repository. Claude Code loads it automatically at the start of every session, so each new chat begins with full project context instead of from scratch. Keep it up to date: when a design decision is made or a phase is completed, edit the relevant section so the document always reflects the current state.

&nbsp;

---

## 1\. How to work with me (persistent instructions)

Follow these on every session unless I say otherwise:

&nbsp;

- **I am learning, so explain to the maximum depth.** I need to deeply understand every change we make and *why* — not just receive working code. When you propose a change, explain the mechanism, the trade-offs, and how it fits the overall design before showing code.  
- **I apply changes manually.** Do not patch files directly. Give me the change and let me apply it myself.  
- **Guide me one step at a time.** Prefer single, ordered commands over large multi-command blocks, so I can run and verify each step.  
- **All code in English**, following clean-code practices and good naming. Keep functions small, intentions explicit, and comments meaningful.  
- **Investigate before generating.** For anything touching DPDK internals or the mlx5 PMD, read the actual source in the repos first (see §7) and reason from it. Do not invent PMD behavior from memory — verify it.

&nbsp;

---

## 2\. One-paragraph summary

I am a master's student in advanced operating systems and computer networks. This project improves **network packet reception** by bringing the **AF\_XDP ring model into DPDK**. AF\_XDP uses four rings (RX, TX, FILL, COMPLETION) to pass ownership of UMEM chunks between kernel and application. I use only the **reception** side — a **FILL/RX pair per core** — and have **multiple such pairs share a single UMEM**, each taking the slice it needs and (later) taking more dynamically. The equivalent idea already exists in eBPF/AF\_XDP; the goal is to make it work in **DPDK**, like rxBisect does, and to reduce reception latency and packet loss.

&nbsp;

---

## 3\. Problem statement

- CPUs parallelize packet processing across cores using **per-core Rx rings**, typically sized `>= 1Ki` entries to absorb bursts.  
- The combined **I/O working set** (all packet buffers pointed to by all Rx rings) easily **exceeds the LLC capacity**, which raises memory-bandwidth pressure and degrades performance.  
- **shRing** reduces the working-set size by **sharing Rx rings among cores**, but it **bottlenecks under imbalanced load**, which is common in practice.

&nbsp;

**Design tension to resolve:** shrink the I/O working set (like shRing) *without* introducing the shared-ring bottleneck under imbalance.

&nbsp;

---

## 4\. Hypothesis and contribution

**Hypothesis.** If we decouple the NIC's *descriptor ring* (small, per core) from the *buffer supply* (a single UMEM shared across cores), and feed empty buffers from that shared UMEM through a per-core FILL ring, we keep the working set small **and** avoid the single-shared-ring serialization that hurts shRing under imbalance.

&nbsp;

**Contribution (the differentiator vs rxBisect).** rxBisect decouples the dual role of the Rx ring but does **not** provide an **allocation pool**. Our contribution is a **software-only instantiation of that buffer-decoupling principle on commodity NICs** — no ASIC changes, no dedicated emulator core — that uses DPDK's **shared mempool as the shared UMEM**, governed by a **credit/allocation pool** that hands UMEM capacity to each core's FILL ring and can rebalance it under imbalanced load. Getting this allocator right is the heart of the novelty; treat it as the core research object, not a detail.

&nbsp;

---

## 5\. Conceptual mapping: AF\_XDP \-\> DPDK

State this mapping explicitly and verify each row against the DPDK/mlx5 source before relying on it (open questions in §9):

&nbsp;

| AF\_XDP concept | DPDK analog (to validate) |
| :---- | :---- |
| UMEM (buffer region) | An `rte_mempool` of mbufs, shared across cores |
| FILL ring (post empties) | The refill path that hands empty mbufs to the Rx ring |
| RX ring (receive filled) | The NIC Rx descriptor ring (per queue / per core) |
| Chunk ownership handoff | Descriptor \<-\> mbuf lifecycle in the PMD |
| TX / COMPLETION rings | **Out of scope** (reception only, like rxBisect) |

&nbsp;

The key restructuring: in stock DPDK the Rx ring plays a **dual role** — it both holds descriptors *and* implicitly sources buffers via the mempool refill. We want to **separate** those roles so the shared UMEM is the single buffer source and a per-core FILL mechanism controls how empties are posted, mediated by the credit allocator.

&nbsp;

---

## 6\. Scope and non-goals

- **In scope:** reception path only; multiple FILL/RX pairs; shared UMEM; static partitioning first, then dynamic (credit-based) allocation.  
- **Non-goals (for now):** transmission (TX/COMPLETION), and full dynamic growth can come after a working static version.

&nbsp;

---

## 7\. Repositories and how to use them

- **shRing on DPDK (primary base):** [https://github.com/BorisPis/shRing-dpdk.git](https://github.com/BorisPis/shRing-dpdk.git) Use this as the starting point to understand the changes we need to make.  
- **Vanilla DPDK (reference):** [https://github.com/DPDK/dpdk.git](https://github.com/DPDK/dpdk.git) **Diff shRing against vanilla DPDK** to see exactly what Pismenny changed and where the Rx-ring/mempool machinery lives.  
- **AF\_XDP reference (concept only):** [https://docs.ebpf.io/linux/concepts/af\_xdp/](https://docs.ebpf.io/linux/concepts/af_xdp/)

&nbsp;

**Background papers (do not reproduce their text — summarize in your own words):**

&nbsp;

- ShRing: Networking with Shared Receive Rings (OSDI '23), Boris Pismenny — [https://www.usenix.org/conference/osdi23/presentation/pismenny](https://www.usenix.org/conference/osdi23/presentation/pismenny)  
- Disentangling the Dual Role of NIC Receive Rings (OSDI '25), Boris Pismenny — [https://www.usenix.org/conference/osdi25/presentation/pismenny](https://www.usenix.org/conference/osdi25/presentation/pismenny)

&nbsp;

---

## 8\. Test environment (CloudLab)

- Experiments run on **CloudLab** for stable, replicable results.  
- **Node: `sm110p` (CloudLab Wisconsin)** — decided 2026-09-08, encoded in `profile.py`. Single-socket Intel Xeon Silver 4314 (Ice Lake), 16 cores, ~24 MB LLC, **single NUMA domain**, dual-port Mellanox ConnectX-6 DX 100 Gb NIC. `r650` (CloudLab Clemson, dual-socket Xeon Platinum 8360Y, ConnectX-6 100 Gb) is the documented fallback; if used, pin everything to **socket 0 / NPS1** and read only that socket's IMC/CHA counters.  
- **Why these three constraints (DDIO alone is not the filter):**  
  1. **Intel.** DDIO is present on every Xeon since 2012, so it does not narrow the choice. Intel matters because the uncore counter tooling (Intel PMC / PCM: `pcm-memory`, CHA/IMC) is mature and is what both papers use; AMD's LLC-injection semantics differ and are uncharacterised here. This is why the AMD nodes (`d6515`, `c6525-100g`) are excluded.  
  2. **100 GbE.** Needed to push enough traffic that the *aggregate* buffer working set exceeds the LLC at realistic core counts. 25 GbE most likely will not produce measurable memory-bandwidth pressure.  
  3. **Mellanox ConnectX-5/6 (`mlx5` PMD).** shRing and rxBisect are *both* implemented as patches to DPDK's mlx5 driver. A node with an Intel E810 NIC (e.g. `c6620`, ~132-node pool) would require porting both baselines and is therefore excluded despite its availability.  

  `sm110p`'s single-NUMA, modest-LLC layout also makes the "working set > LLC" effect easy to induce and to defend in the thesis.  
- **Availability.** The `sm110p` pool is ~20 nodes at a single site. Do **not** solve contention by switching hardware — use a **CloudLab resource reservation** (2 nodes for the working window; small requests are usually granted). Phase 0 code-reading and DPDK builds need no CloudLab node at all.  
- **rxBisect run parameters** (from the paper's author, mlx5 driver): `rxb_en`, `rxb_rqs`, `rxb_emu_mask`, `rxb_emu_type`.  
  - `rxb_en` — `1` enables rxBisect.  
  - `rxb_rqs` — number of cores sharing queues/buffers (author used **8 per 100 Gbps NIC**).  
  - `rxb_emu_mask` — bitmap of cores the emulator thread may run on (same format as DPDK's `-C` flag).  
  - `rxb_emu_type` — `0` \= baseline emulated via the rxBisect emulator thread; `1` \= rxBisect; `2` \= shRing.  
  - Author's own example: `rxb_en=1,rxb_rqs=8,rxb_emu_mask=0x22222222,rxb_emu_type=1` (rxBisect, 8 cores sharing rings/buffers, emulator on odd cores / NUMA 1).

&nbsp;

### 8.1 Node type and topology (fixed by `profile.py`)

- `profile.py` pins the **disk image to Ubuntu 22.04** (`UBUNTU22-64-STD`). Ubuntu 20.04 was tried on 2026-09-28 to match DPDK 21.05's era and **CloudLab refuses it**: `OS 'emulab-ops/UBUNTU20-64-STD' (OS-11517) does not run on this hardware type!` for both `dut` and `tgen`, and the experiment sits in Pending forever. 22.04 is the oldest image `sm110p` accepts, and it is the one §14.4 already validated end-to-end. The DPDK 21.05 build problem is solved in the build instead — see §14.1.
- `profile.py` pins the node type to **`sm110p`** (Xeon Silver 4314, single NUMA node, 32 logical CPUs, ConnectX-6 Dx 100Gb) on the **Wisconsin** CloudLab cluster (`*.wisc.cloudlab.us`) — this answers open question §9.1's "which profile" half. **DDIO support is expected for this Xeon Scalable generation but has not been independently measured yet** — do not treat it as confirmed until the Phase 0 measurement harness (§11) checks it via PCM/PMU counters.
- The profile allocates exactly two roles, **`dut`** and **`tgen`**, each with a `/mydata` blockstore (100GB) and two Mellanox NICs:
  - A **ConnectX-6 Lx** — the control-plane NIC, DHCP-assigned on the shared cluster network, carries SSH. **Never point DPDK/EAL at this one.**
  - A **ConnectX-6 Dx** — the 100Gb **experiment NIC**, wired as a dedicated back-to-back link declared in the RSpec as `experiment-link` (VLAN-tagged, `best_effort`), private subnet `10.10.1.0/24` with **no default route**. It physically connects only `dut:experiment-nic` <-> `tgen:experiment-nic` and cannot reach the campus network, the CloudLab control plane, or any other experiment — safe to saturate at line rate.
- **PCI addresses and interface names are not guaranteed stable across re-instantiations** — always re-derive them with the recipe in §14.2 rather than hardcoding. For reference, this is what was observed on the instantiation validated in §14.4: experiment NIC (Dx) = `0000:51:00.0` on both nodes (`dut` = `10.10.1.1`, MAC `b8:3f:d2:13:08:a6`; `tgen` = `10.10.1.2`, MAC `b8:3f:d2:13:08:ae`); control NIC (Lx) = `0000:8a:00.x` on both nodes.

&nbsp;

---

## 9\. Open questions to resolve BEFORE writing code

Answer these by reading the source and, where needed, running small probes. Do not start Phase 1 until §9.1–§9.3 are settled.

&nbsp;

1. **DDIO node.** ✅ Node type fixed by `profile.py`: `sm110p` (see §8.1). Still open: independently confirm DDIO is active for this generation via PCM/PMU counters, not just assumed from CPU spec (fold into the §11 measurement harness).  
2. **Fork point.** ✅ **RESOLVED (2026-09-28): build on `shRing-dpdk`.** Diffing it against upstream settles it. `git merge-base` lands exactly on the `v21.05` release commit (`175af2573`), so the fork is clean and its whole delta is 13 commits / 26 files / ~3.1k inserted lines. Decisively, every shRing datapath change is gated behind the `rmp_en` devarg and adds *separate* burst functions (`mlx5_rx_burst_rmp`, `mlx5_rx_burst_rmp_mprq`) rather than altering the stock ones — so **one binary yields both baselines**: `rmp_en=0` is privRing, `rmp_en=1,rqs_per_rmp=N` is shRing. Building here removes the DPDK-version confounder from the comparison entirely. The vanilla `v21.05` tree is kept only to validate that equivalence and to diff against while reading. Caveat: shRing also patches `examples/l3fwd`, so the two trees' `l3fwd` binaries are **not** identical — hence the validation is required, not assumed.  
3. **mlx5 Rx path.** Where exactly does the mlx5 PMD refill the Rx ring from the mempool? What is the smallest hook point to insert a FILL/credit step without rewriting the datapath?  
4. **UMEM \= mempool?** Confirm that a single shared `rte_mempool` is the right "UMEM" abstraction, and how per-core FILL rings draw from it.  
5. **Allocator semantics.** What does a "credit" represent (chunks? bytes? descriptors?), and what is the rebalancing policy under imbalance?  
6. **Measurement.** How do we measure LLC misses / memory-bandwidth pressure on the chosen node (e.g., PMU counters), not just throughput?

&nbsp;

---

## 10\. Incremental plan (\~1 month, \>= 2 h/day)

Each phase has an explicit **Done when** so we know it is complete.

&nbsp;

- **Phase 0 — Environment & baselines.** Pick the DDIO node (§8). Build vanilla DPDK, shRing, and rxBisect. Run `l3fwd` on each and capture the measurement harness (throughput, latency, loss, and an LLC/bandwidth counter). *Done when:* all three baselines run `l3fwd` reproducibly and we log the same metrics for each.  
  &nbsp;  
  **Status:** environment setup is validated end-to-end (§14) — vanilla DPDK builds and a real cross-node packet test passed on both nodes. **Not done yet:** shRing/rxBisect are not built (`INSTALL_COMPARISON_DPDKS` still `false`), and no `l3fwd` baseline runs or metric logging have happened — that is the remaining work for this phase.  
  &nbsp;  
- **Phase 1 — Single FILL/RX pair, static UMEM slice.** One core, one FILL/RX pair drawing empties from a shared UMEM (a fixed slice). Prove the decoupled mechanism works and forwards packets. *Done when:* one core forwards traffic via the decoupled FILL/RX path with no loss at a modest rate, matching vanilla correctness.  
  &nbsp;  
- **Phase 2 — Multiple FILL/RX pairs, static partitioning.** N cores, N pairs, all sharing one UMEM with a fixed per-core partition. *Done when:* multi-core forwarding is correct and the combined working set is measurably smaller than vanilla per-core rings.  
  &nbsp;  
- **Phase 3 — Dynamic allocation (the novelty).** Add the credit/allocation pool so cores take more/less UMEM dynamically and rebalance under imbalanced load. *Done when:* under a deliberately imbalanced load, the allocator shifts capacity and avoids the shRing-style bottleneck.  
  &nbsp;  
- **Phase 4 — Evaluation.** Compare **vanilla DPDK, shRing, rxBisect, and our implementation** with the same `l3fwd` workload, under balanced and imbalanced load. *Done when:* we have a fair, repeatable comparison table \+ plots across all four, on the same node and traffic.

&nbsp;

---

## 11\. Evaluation methodology

- **Common benchmark:** DPDK `l3fwd` — it already exists in all four systems, so it runs the same workload everywhere and yields comparable results.  
- **Systems compared:** vanilla DPDK, shRing, rxBisect, ours.  
- **Load regimes:** balanced **and** imbalanced (imbalance is where shRing is expected to degrade and where our allocator should win).  
- **Metrics:** throughput, latency (incl. tail), packet loss, and a working-set / LLC-pressure signal (memory bandwidth or LLC-miss counters).  
- **Fairness:** identical node, NIC, core counts, ring sizes, and traffic across systems; change one variable at a time.

&nbsp;

---

## 12\. Glossary (keep terminology consistent)

- **UMEM** — the shared buffer region packets are received into; here, a shared DPDK mempool.  
- **FILL ring** — posts *empty* buffers to be filled by the NIC.  
- **RX ring** — delivers *filled* buffers to the core.  
- **I/O working set** — the set of packet buffers referenced by all Rx rings at once; when it exceeds the LLC, memory-bandwidth pressure rises.  
- **Credit / allocation pool** — the mechanism that grants shared-UMEM capacity to each core and rebalances it; the project's main contribution.  
- **DDIO** — Intel Data Direct I/O; lets the NIC place packets into LLC. Central to the papers' effects, so the test node must support it.

&nbsp;

---

## 13\. Session kickoff checklist

At the start of a development session, before coding:

&nbsp;

1. Confirm which **phase** we are in (§10) and the current **Done when**.  
2. Confirm the CloudLab node and that DDIO is available (§8).  
3. Re-read the relevant part of the shRing/DPDK source for the change at hand.  
4. Propose the change with a deep explanation first; I apply it manually, one step at a time.

&nbsp;

---

## 14\. Environment setup runbook (run after every fresh CloudLab instantiation)

This section exists so a fresh instantiation never re-discovers the same bugs from scratch. Follow it in order before touching any experiment code.

&nbsp;

### 14.1 Run the setup script on both nodes

SSH into each node and run, once per node (idempotent — safe to re-run; heavy steps like clone/build are skipped if already done):

```bash
sudo setup/dev-environment.sh dut     # on the dut node
sudo setup/dev-environment.sh tgen    # on the tgen node
```

**Bugs already fixed in this script (kept here as history, in case a similar script is written later):**

- `linux-cpupower` is **not a real Ubuntu/Debian package** — that name is from Fedora/RHEL (`kernel-tools`). On Ubuntu, `cpupower` ships inside `linux-tools-common` + `linux-tools-$(uname -r)` + `linux-tools-generic`, which the script already installs. Adding `linux-cpupower` to the `apt-get install` list makes the whole install fail (`set -euo pipefail` aborts the script right there).
- `dpdk-hugepages.py --setup` expects the **total memory size to reserve** (e.g. `8192M`, `8G`), **not a page count**. Passing a raw page count (e.g. `4096`) gets silently parsed as a byte count and fails with `Huge reservation 4Kb is not a multiple of page size 2Mb`. The fix: compute the size explicitly, e.g. `--setup "$((HUGEPAGE_COUNT * 2))M"` when `HUGEPAGE_COUNT` is a number of 2MB pages.
- **Do not narrow the build with `-Denable_drivers=net/mlx5`.** On DPDK 21.05 that option does not resolve dependencies: it produced a configure with `net:` *empty* and no mempool driver either (286 targets instead of 2138), exit code 0 and no warning. The resulting build is useless and only fails at runtime. Build the full tree.
- **DPDK 21.05 does not build unmodified on Ubuntu 22.04's toolchain, and `setup/patches/` fixes it.** The script applies every patch there to both trees, idempotently. The failure, if you ever see it raw: `ar: 'x' cannot be used on thin archives.` Cause: meson 0.61+ builds *uninstalled* static libraries as **thin archives** (`LINK_ARGS = csrDT`, versus `csrD` for the rest), and DPDK 21.05's `buildtools/gen-pmdinfo-cfile.py` calls `ar x`, which GNU ar refuses on those. It hits `libtmp_rte_common_mlx5.a`, so skipping unrelated drivers does not dodge it. Note this is a **meson** problem, not a binutils one — meson 0.53.2 (Ubuntu 20.04) has no thin-archive logic at all, 0.61.2 (22.04) does. The C sources are fine: they compile clean even under gcc 13 with zero compiler errors. The fix is the upstream one, backported verbatim from DPDK 23.11.
- **Verification of that patch (2026-09-28):** on Ubuntu 24.04 / binutils 2.42 / meson 1.3.2 / gcc 13 — harsher than the 22.04 target — the shRing tree goes from a failing build to `ninja exit=0`, zero failed targets, producing `librte_net_mlx5.a`, `dpdk-l3fwd` and `dpdk-testpmd`.

&nbsp;

Both baselines come out of the shRing tree, from the same binary (see §9.2):

```bash
# privRing (stock per-core Rx rings)
sudo ./build/examples/dpdk-l3fwd -l <cores> -n 4 -a <pci> -- ...
# shRing (N cores sharing one Rx ring)
sudo ./build/examples/dpdk-l3fwd -l <cores> -n 4 -a <pci>,rmp_en=1,rqs_per_rmp=8 -- ...
```

&nbsp;

### 14.2 Identify the control-plane NIC vs. the experiment NIC (do this every time — do not assume PCI addresses or interface names carry over between instantiations)

**Find the control-plane NIC** (the one carrying your SSH session — never point DPDK at it):

```bash
read -r CLIENT_IP CLIENT_PORT SERVER_IP SERVER_PORT <<< "$SSH_CONNECTION"
ip route get "$CLIENT_IP"        # the "dev <iface>" shown is the control NIC
```

**Find the experiment NIC** (the ConnectX-6 Dx on the private `10.10.1.0/24` link):

```bash
# profile.py assigns dut=10.10.1.1, tgen=10.10.1.2 statically — confirm via the manifest if unsure:
geni-get manifest | grep -A2 experiment-nic

IFACE=$(ip -o -4 addr show | awk '/10\.10\.1\./{print $2}')
ethtool -i "$IFACE" | grep bus-info      # PCI address to pass as `-a` to DPDK/EAL
```

**Verify isolation before generating any real traffic** (must show only the local subnet, no `default`):

```bash
ip route show dev "$IFACE"
```

&nbsp;

### 14.3 Sanity-check the build (in order — each step touches more of the stack)

1. **No hardware touched** — confirms the build and hugepages alone:
   ```bash
   sudo ./build/app/dpdk-testpmd -l 0-1 -n 4 --no-pci -- -i
   ```
   Should reach the `testpmd>` prompt with no errors. `quit` to exit.

2. **Real port, one node, no traffic** — confirms the mlx5 PMD initializes the experiment NIC:
   ```bash
   sudo ./build/app/dpdk-testpmd -l 0-3 -n 4 -a <pci-addr-from-14.2> -- -i
   testpmd> show port info 0     # expect: Link status: up, Link speed: 100 Gbps
   testpmd> quit
   ```

3. **Paired end-to-end test** — two SSH sessions, one per node, confirms the full cross-node path:

   On `dut` (start first, leave running):
   ```bash
   sudo ./build/app/dpdk-testpmd -l 0-3 -n 4 -a <dut-pci> -- -i --forward-mode=rxonly
   testpmd> start
   ```

   On `tgen` (in a separate session):
   ```bash
   sudo ./build/app/dpdk-testpmd -l 0-3 -n 4 -a <tgen-pci> -- -i --forward-mode=txonly --eth-peer=0,<dut-experiment-nic-mac>
   testpmd> start
   # wait ~5s
   testpmd> stop
   testpmd> show port stats all   # note TX-packets
   testpmd> quit
   ```

   Back on `dut`:
   ```bash
   testpmd> stop
   testpmd> show port stats all   # RX-packets should be close to tgen's TX-packets
   testpmd> quit
   ```

&nbsp;

### 14.4 Last validated run (reference only — re-run 14.2–14.3 fresh each instantiation, do not assume these values still hold)

- Node type: `sm110p`, Wisconsin cluster.
- Control NIC: ConnectX-6 Lx, PCI `0000:8a:00.0` (`dut` and `tgen` both), interface `ens1f0np0`.
- Experiment NIC: ConnectX-6 Dx, PCI `0000:51:00.0` (`dut` and `tgen` both), interface `ens2f0np0`.
- `dut` experiment IP `10.10.1.1` (MAC `b8:3f:d2:13:08:a6`); `tgen` experiment IP `10.10.1.2` (MAC `b8:3f:d2:13:08:ae`).
- Result: `tgen` `txonly` sent ~1.16B 64B packets (single core, software-bound — the resulting `TX-dropped` count is expected and not a hardware fault); `dut` `rxonly` received ~1.0B packets. Confirms the DPDK build, the mlx5 PMD, and the isolated cross-node link all work correctly.

&nbsp;
