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
  - A **ConnectX-6 Dx** — the 100Gb **experiment NIC**, on the link declared in the RSpec as `experiment-link` (VLAN-tagged, `best_effort`), private subnet `10.10.1.0/24` with **no default route**. The VLAN carries only `dut:experiment-nic` <-> `tgen:experiment-nic` and cannot reach the campus network, the CloudLab control plane, or any other experiment — safe to saturate at line rate.
  - **It is NOT a back-to-back cable** (corrected 2026-09-29). A capture on the dut showed Rapid STP BPDUs (2 every 2 s) and LLDP (every ~30 s) from `e8:65:5f:99:84:73`, which identifies itself as `spinesw-z9432-10s10525` — a CloudLab spine switch sits in the path. Consequences: (a) one switch hop (~1 µs) is inside every latency number; (b) `best_effort` means capacity is not guaranteed — line rate has been clean so far, but unexplained loss should put the switch on the suspect list; (c) the switch's multicast reaches both NICs and cannot be silenced from the nodes (§17.3).
- **PCI addresses and interface names are not guaranteed stable across re-instantiations** — always re-derive them with the recipe in §14.2 rather than hardcoding. For reference, this is what was observed on the instantiation validated in §14.4: experiment NIC (Dx) = `0000:51:00.0` on both nodes (`dut` = `10.10.1.1`, MAC `b8:3f:d2:13:08:a6`; `tgen` = `10.10.1.2`, MAC `b8:3f:d2:13:08:ae`); control NIC (Lx) = `0000:8a:00.x` on both nodes.

&nbsp;

---

## 9\. Open questions to resolve BEFORE writing code

Answer these by reading the source and, where needed, running small probes. Do not start Phase 1 until §9.1–§9.3 are settled.

&nbsp;

1. **DDIO node.** ✅ Node type fixed by `profile.py`: `sm110p` (see §8.1). ✅ **Instrumentation confirmed (2026-09-28):** PCM reads this node's uncore — `pcm-pcie` returns `PCIRdCur` / `ItoM` / `ItoMCacheNear`, which are the DDIO events, and `pcm-memory` reports all 8 memory channels. Still open: the *value* of `|DDIO|` here. Measure it empirically rather than from an MSR whose address is microarchitecture-specific — sweep the ring size under load and find the knee where `pcm-memory` bandwidth rises and the `pcm-pcie` hit ratio falls, which is the paper's Figure 3 experiment. That needs the traffic generator first (§10, Phase 0). For reference, the shRing paper runs with **2 DDIO ways** (of 11, on a 22 MiB LLC ≈ 4 MiB).  
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
  **Status (2026-09-28).** Both baselines run. The shRing tree builds (§14.1), and `l3fwd` forwards 1500 B traffic at line rate with zero loss under **both** privRing and shRing, from the same binary — the configuration and its two mandatory devargs are in §15. Intel PCM reads the uncore counters on `sm110p` (16 CHA/CBO, 6 IIO/IRP, 8 memory channels), so `pcm`, `pcm-memory` and `pcm-pcie` all return real data. rxBisect is out as a measured baseline (§11).
  &nbsp;
  **Status (2026-09-29).** TRex v3.07 runs on `tgen` (§16) and the harness (§17) drives the first like-for-like comparison: privRing-1024, small privRing-128 and shRing-8, same binary, same 8 cores, same window, same offered load. Balanced 1500 B load, 10–100% of line rate, two repetitions: **all three tie** — zero loss everywhere, latency within run-to-run noise. Pause frames are now off on both nodes (§15.5).
  &nbsp;
  **Remaining for this phase, in order:** (1) make `rate_sweep.py` count loss from `rx_unicast_packets` — the switch's STP/LLDP multicast adds ~30–60 packets per 30 s window to TRex's receive count and cannot be silenced at the source (§17.3); (2) an RFC2544 no-drop search and the imbalanced load regime in the harness — the balanced run cannot separate the systems. The paper's imbalance comes from the CAIDA 2018 trace, whose access request has lead time (§11): request it early, and meanwhile build a synthetic skew with TRex (uneven per-flow rates); (3) measure `|DDIO|` on this node, which closes §9.1 and supplies the constant in §15.5; (4) close the remaining tuning gaps in §15.5 (1 GiB hugepages, hyperthreading off, `isolcpus` — all need a reboot); (5) settle the §15.6 memory-order question before any shRing number is published.
  &nbsp;
  **Can start with no CloudLab node:** §9.3–§9.5 (where mlx5 refills the Rx ring from the mempool, the smallest hook for a FILL/credit step, what a credit is). This is source reading, it is the gate to Phase 1, and it can run in parallel with (1)–(5).  
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

- **Common benchmark:** DPDK `l3fwd`, and it must be l3fwd rather than testpmd — shRing's `rx_contention` counter is only printed by the patched `l3fwd` (§15.3). It is also the benchmark both papers use.

&nbsp;

**Systems measured — four, all from the shRing tree, all with the §15 devargs:**

| # | System | How |
|---|---|---|
| 1 | privRing | `rmp_en=0`, per-core Rx ring of 1 Ki |
| 2 | **small privRing** | `rmp_en=0`, per-core ring shrunk 8× (1 Ki → 128) |
| 3 | shRing | `rmp_en=1,rqs_per_rmp=N` |
| 4 | ours | to be built |

**Do not skip #2.** The rxBisect paper carries it precisely because it has the *same I/O working set* as shRing while sharing nothing. Without it, any gain we measure is confounded: it is impossible to say whether the benefit came from sharing buffers or merely from a smaller ring. The paper's own finding is that small privRing gets the working set right but cannot absorb bursts — which is exactly the gap our allocator claims to close, so it is our most important baseline, not an optional extra.

**rxBisect is not measured.** It requires NIC ASIC changes and its paper evaluates it through a software emulation framework that was never published (no artifact, no repository). It appears in this thesis as a *cited* design reference and upper bound — never in the same table as our measured numbers. The framing: rxBisect shows the principle works if the ASIC changes; we show how much of that is reachable in software on a commodity NIC.

&nbsp;

- **Packet size: 1500 B.** Not 64 B. The rxBisect paper is explicit that it evaluates larger packets "to stress the memory subsystem" — at 64 B the bottleneck is the CPU and the working-set effect this project studies is invisible. Our own 64 B runs confirmed this (§15.2).
- **Load regimes:** balanced **and** imbalanced. The reference imbalance is the CAIDA 2018-03-15 NYC trace replayed by TRex, where the per-core min/max packet-rate ratio stays between 325–433%. CAIDA access needs a request with lead time.
- **Core count:** 8 per NIC, matching both papers.
- **Metrics — five, as in the rxBisect paper's Figure 10:** throughput (RFC2544 no-drop), latency including tail, ring occupancy, memory bandwidth (`pcm-memory`), and DDIO hit rate (`pcm-pcie`). The last three explain the mechanism; throughput and latency alone do not support the argument. `rx_contention` is a sixth, specific to quantifying shRing's bottleneck.
- **Fairness:** identical node, NIC, core counts, ring sizes, application and traffic; change one variable at a time. Critically, **`rx_vec_en=0` and `rxq_cqe_comp_en=0` on every system**, not just shRing — see §15.1 for why leaving them on for privRing would invalidate the whole comparison.

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
sudo setup/trex-setup.sh              # then, on tgen only (section 16)
```

`setup/node-setup.sh` runs on every boot (the profile's `pg.Execute`) and, besides hugepages, turns **pause frames off** on the experiment NIC of both nodes — `ethtool -A` does not survive a reboot, so it cannot live in the one-off setup script.

**Bugs already fixed in this script (kept here as history, in case a similar script is written later):**

- `linux-cpupower` is **not a real Ubuntu/Debian package** — that name is from Fedora/RHEL (`kernel-tools`). On Ubuntu, `cpupower` ships inside `linux-tools-common` + `linux-tools-$(uname -r)` + `linux-tools-generic`, which the script already installs. Adding `linux-cpupower` to the `apt-get install` list makes the whole install fail (`set -euo pipefail` aborts the script right there).
- `dpdk-hugepages.py --setup` expects the **total memory size to reserve** (e.g. `8192M`, `8G`), **not a page count**. Passing a raw page count (e.g. `4096`) gets silently parsed as a byte count and fails with `Huge reservation 4Kb is not a multiple of page size 2Mb`. The fix: compute the size explicitly, e.g. `--setup "$((HUGEPAGE_COUNT * 2))M"` when `HUGEPAGE_COUNT` is a number of 2MB pages.
- **Do not narrow the build with `-Denable_drivers=net/mlx5`.** On DPDK 21.05 that option does not resolve dependencies: it produced a configure with `net:` *empty* and no mempool driver either (286 targets instead of 2138), exit code 0 and no warning. The resulting build is useless and only fails at runtime. Build the full tree.
- **Two build messages are expected and harmless.** `mlx5_net: Failed to init cache list FDB_ingress_0_0_matcher_cache entry (nil)` prints ~5 times at every port start, under both privRing and shRing, and does not affect the datapath — traffic runs at line rate with it present. `EAL: Error: Invalid memory` prints 3 times while l3fwd shuts down, after the statistics are already out. Neither has been investigated; ignore them unless something else is actually wrong.
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

&nbsp;

**Instantiation live as of 2026-09-28** (the one all of §15 was measured on). Interface `ens2f0np0` and PCI `0000:51:00.0` on both nodes, same as before — but **the MACs changed**, which is exactly why §14.2 says to re-derive rather than reuse:

| | interface | PCI | MAC |
|---|---|---|---|
| `dut` | `ens2f0np0` | `0000:51:00.0` | `b8:3f:d2:13:04:82` |
| `tgen` | `ens2f0np0` | `0000:51:00.0` | `b8:3f:d2:13:04:7e` |

**Instantiation live as of 2026-09-29** (the one §16 and §17 were measured on). Interface and PCI held once more; the MACs changed again:

| | interface | PCI | MAC |
|---|---|---|---|
| `dut` | `ens2f0np0` | `0000:51:00.0` | `b8:3f:d2:13:08:ae` |
| `tgen` | `ens2f0np0` | `0000:51:00.0` | `b8:3f:d2:13:0a:7a` |

Both TRex's `dest_mac` and l3fwd's `--eth-dest` depend on these, and a stale one shows up as 100% loss with no error. `setup/trex-setup.sh` and `harness/run_l3fwd.sh` therefore learn the peer MAC over the link (ping + `ip neigh`) instead of reading it from here.

Re-derive with §14.2 after any re-instantiation; these are recorded only to save a step while this instantiation is up.
- Result: `tgen` `txonly` sent ~1.16B 64B packets (single core, software-bound — the resulting `TX-dropped` count is expected and not a hardware fault); `dut` `rxonly` received ~1.0B packets. Confirms the DPDK build, the mlx5 PMD, and the isolated cross-node link all work correctly.

&nbsp;

---

## 15\. Baseline configuration (validated 2026-09-28 — do not change without re-validating)

Both baselines come from the **shRing tree**, same binary, differing only in devargs. This is what removes the DPDK-version confounder (see §9.2).

```
privRing :  -a <pci>,rmp_en=0,rx_vec_en=0,rxq_cqe_comp_en=0
shRing   :  -a <pci>,rmp_en=1,rqs_per_rmp=N,rx_vec_en=0,rxq_cqe_comp_en=0
```

&nbsp;

### 15.1 Why `rx_vec_en=0` and `rxq_cqe_comp_en=0` are mandatory on BOTH

shRing is incompatible with two mlx5 fast-path features. **Neither incompatibility is documented in the paper**; both were found empirically here.

- **Vectorized Rx.** `mlx5_rxq.c:151` rejects the combination outright: `if (rmp && mlx5_rxq_check_vec_support(...) > 0)` → `RMP + VEC is not supproted yet`, and the port fails to start. There are simply no vectorized RMP burst functions — `mlx5_select_rx_function()` offers `mlx5_rx_burst_rmp` and `mlx5_rx_burst_rmp_mprq`, but the vector branch has no RMP variant.
- **CQE compression.** This one fails *silently and catastrophically*, which makes it the dangerous one. With compression on (the mlx5 default), the shared ring is filled once and never replenished: the receive path delivers roughly one ring's worth of packets and then starves forever while the NIC drops everything into `rx_missed_errors`. There is no error message.

**The methodological point: these flags must be set on privRing too.** Both are performance features. Running shRing without them and privRing with them measures vectorization and CQE compression, not ring sharing — the comparison would be worthless.

&nbsp;

### 15.2 The CQE-compression failure, for whoever hits it again

Measured at 1500 B, 100 Gbps line rate, 4 cores / 4 queues, one shared RMP:

| Configuration | Packets delivered | Loss |
|---|---|---|
| privRing (any) | 187,619,086 | **0%** |
| shRing, `rxq_cqe_comp_en=1`, `rxd=2048`, testpmd | 6,191 | 99.995% |
| shRing, `rxq_cqe_comp_en=1`, `rxd=1024`, testpmd | 1,023 | 99.999% |
| shRing, `rxq_cqe_comp_en=1`, `rxd=1024`, l3fwd | 9,471 | 99.994% |
| shRing, **`rxq_cqe_comp_en=0`**, `rxd=1024`, l3fwd | **81,721,361** | **0%** |

The signature is unmistakable once seen: `rx_good_packets` lands on roughly the ring size (1,023 for a 1024-entry RMP), `rx_missed_errors` absorbs everything else, and `rx_phy_discard_packets` and `rx_out_of_buffer` both stay at **0** — the NIC is healthy, software just never returns descriptors. `contention` stays at 1, proving the doorbell path barely ran. The mechanism is the in-sequence doorbell in `mlx5_rx_burst_rmp` (`mlx5_rx.c:1128`), which only rings once 64 *consecutive* ring entries complete.

Note that it **does** work with CQE compression at 64 B packets (235 M received), so a small-packet smoke test will not catch this. Always validate at 1500 B.

&nbsp;

### 15.3 Working run, for reference

`rmp_en=1,rqs_per_rmp=4,rx_vec_en=0,rxq_cqe_comp_en=0`, l3fwd, 4 cores, `rxd=1024`, 1500 B at line rate:

- 81,721,361 packets received **and forwarded**, `rx_missed_errors` = 0
- per-queue: 20,430,353 / 20,430,336 / 20,430,336 / 20,430,336 — four cores sharing one RMP, RSS spread under 0.001%
- `contention` = 12,366 — the shared-ring CAS counter is live. **This is the metric that quantifies shRing's bottleneck under imbalance**, and it is the one Phase 3 needs. It is exposed as a field shRing added to `rte_eth_stats` (`rx_contention`) and is printed only by the patched `l3fwd` (`l3fwd_lpm.c:292`) — testpmd does not know about it, so the harness must use l3fwd.
- `idle/total` = 0.21

&nbsp;

### 15.4 Dead ends, recorded so they are not retried

- **MPRQ + RMP does not initialize.** `mprq_en=1,rxqs_min_mprq=4` fails with `Cannot allocate memory` regardless of mbuf pool size. Cause: a double shift in `mlx5_rxq.c` — the RMP path computes `desc = (1 << rxq->elts_n)`, but `elts_n` was *already* divided by the stride count in `mlx5_rxq_new()`, and the RMP path divides it again (`desc >> mprq_stride_nums`). For any `rxd` below 4096 the result is 0, and `mlx5_malloc(0)` returns NULL, which the code reports as ENOMEM. **Do not patch this.** The shRing paper never uses MPRQ, so this path was evidently never exercised; patching the baseline to enable a mode its authors did not evaluate would mean no longer comparing against the published system.
- `-Denable_drivers=net/mlx5` — see §14.1.

&nbsp;

### 15.5 Where our testbed still differs from the shRing paper

From the paper's own setup section: *"we use default application settings: 1024 descriptor Rx and Tx rings and 2 DDIO LLC [ways]"*, on Ubuntu 18.04 / Linux 5.4, ConnectX-5, with TRex as load generator.

| | shRing paper | our `dut` | resolved? |
|---|---|---|---|
| Rx ring | 1024 desc | 1024 desc | ✅ matched |
| DDIO ways | 2 | to be measured | open (§9.1) |
| NIC | ConnectX-5 | ConnectX-6 Dx, fw 22.46.3048 | works, but a different generation |
| Hugepages | 1 GiB | 2 MiB | **open** |
| Hyperthreading | disabled | enabled (32 logical / 16 physical) | **open** |
| CPU isolation | `isolcpus` | not configured | **open** |
| Pause frames | disabled | disabled (since 2026-09-29, `node-setup.sh`) | ✅ closed |
| Load generator | TRex (patched for 1 µs latency accuracy) | stock TRex v3.07 (§16) | ✅ for loss/throughput; latency accuracy open |

The three open rows are tuning, not correctness — none of them block development, but all of them must be closed before any number goes into the thesis.

**Pause frames were ON before 2026-09-29, on both nodes** (`ethtool -a`: `RX: on`, `TX: on`, the image default). Everything in §15.2–15.3 was therefore measured with 802.3x flow control active: an overloaded dut could pause the sender instead of dropping. The works-versus-starves conclusions stand, but a "0% loss" from that period is not a no-drop result — re-measure before citing any of them.

&nbsp;

**Careful reading the §15.2 table.** The privRing row was measured with testpmd and the shRing rows with l3fwd, over different time windows. It establishes *works* versus *starves*, nothing more — the two systems have **not** been compared to each other yet. That comparison is what the harness is for, and it requires identical application, identical window and identical offered load.

&nbsp;

### 15.6 Known defects in the shRing artifact

Latent problems in the code we run as a baseline. Recorded because they may surface later, and because a thesis comparing against this artifact should be able to state what is in it.

- **Invalid C memory order in the shared-ring hot path.** `mlx5_rx.c:1134` does `__atomic_store_n(..., 0, __ATOMIC_ACQUIRE)`. Acquire is not a valid order for a *store* — only relaxed, release and seq_cst are — and gcc flags it (`-Winvalid-memory-model`) on every build. **Open question: what gcc actually emits for the invalid order.** If it falls back to seq_cst, shRing pays a full barrier every 64 received packets on its most contended path, which would make our shRing measurably slower than the paper's for reasons that have nothing to do with the design. Settle it before publishing any shRing number: compile a store under acquire/release/seq_cst and compare the assembly (on x86-64 a release or relaxed store is a plain `mov`; seq_cst is `xchg` or `mov`+`mfence`).
- **Double shift in the RMP + MPRQ allocation** — see §15.4. Deliberately unpatched.
- **Research-grade code in general.** Debug `printf`s on the datapath (`!!!! Creating RMP !!!!`), commented-out blocks, a `TODO: hidden assumption about (THRESHOLD % 64 == 0)` in the doorbell logic, and a typo in a driver error string. Read before trusting; do not assume any given path was exercised.

&nbsp;

---

## 16\. Traffic generator — TRex v3.07 on `tgen` (installed and validated 2026-09-29)

testpmd cannot do what §11 requires: no RFC2544 no-drop search, no tail latency, and `--txonly-multi-flow` gives no control over flow skew, which is precisely the imbalance regime where this project's contribution is supposed to win. The shRing paper uses **Cisco TRex**, so does the rxBisect paper, and it covers all three needs.

`setup/trex-setup.sh` does everything below on a fresh `tgen`; this section explains why each step is there.

&nbsp;

### 16.1 Decisions that still hold from the 2026-09-28 research

- **Version: v3.07.** TRex documents Mellanox compatibility per release, and v3.07 is the one listed against Ubuntu 22.04. Pinned rather than `latest`, for the same reason the DPDK trees are pinned. Installed at `/mydata/trex/v3.07`.
- **Do not install MLNX_OFED.** It would replace the system rdma-core wholesale. The narrower fix in §16.2 is enough.
- **The download needs `--no-check-certificate`.** `trex-tgn.cisco.com` serves an incomplete chain — the leaf is a valid Cisco certificate (`CN = trex-tgn.cisco.com, O = Cisco Systems Inc.`) but the intermediate to `IdenTrust Commercial Root CA 1` is missing, so verification fails with code 21. This is a server misconfiguration and it is why TRex's own instructions disable verification; the cost is that the download cannot be cryptographically traced to Cisco. Building from the GitHub source is the alternative if that matters.
- **The Mellanox port stays on the kernel `mlx5_core` driver**, exactly as with DPDK here — there is no `dpdk-devbind` step. TRex takes PCI addresses directly in `interfaces:`; `sudo ./dpdk_setup_ports.py -t` lists them (read-only, no OFED check).
- **Latency accuracy.** The shRing paper notes it modified TRex to improve latency measurement from 10–100 µs to 1 µs. Stock TRex is fine for throughput and loss; that modification becomes relevant only if the thesis claims tail latency.

&nbsp;

### 16.2 TRex needs rdma-core v44 — the 2026-09-28 assumption was wrong

The earlier plan said Ubuntu 22.04's rdma-core 39 was enough. **It is not.** TRex builds its own DPDK, but its mlx5 driver loads the *system* `libmlx5`/`libibverbs` at run time, and the loader checks versioned symbols, not just names:

| | highest `MLX5_1.x` |
|---|---|
| required by TRex v3.07 (`so/x86_64/libmlx5-64.so`) | `MLX5_1.24` |
| exported by rdma-core 39 (Ubuntu 22.04) | `MLX5_1.22` |
| exported by rdma-core 44 | `MLX5_1.24` (first release that has it) |

Check on the node with `objdump -T <lib> | grep -o 'MLX5_1\.[0-9]*' | sort -uV | tail -1`.

- **Fix: build rdma-core `v44.0` into `/usr/local`, on `tgen` only.** It wins over the apt copy because `/etc/ld.so.conf.d/libc.conf` (listing `/usr/local/lib`) sorts before `x86_64-linux-gnu.conf`; the apt package stays installed and untouched. Providers go along: the new `libibverbs` looks for them in `/usr/local/lib/libibverbs/`, so it never mixes with v39's.
- **A private prefix does not work:** `t-rex-64` does `export LD_LIBRARY_PATH=$PWD`, overwriting whatever you pass.
- **Never on the `dut`.** Its rdma-core 39 + DPDK 21.05 stack is the one §15 validated; changing a datapath library there would void that.
- Build flags: `cmake -GNinja -DNO_MAN_PAGES=1 -DNO_PYVERBS=1 ..` (no pandoc, no Cython). Verified: `ldconfig -p` lists `/usr/local/lib/libmlx5.so.1` first, it exports `MLX5_1.24`, and `/usr/local/bin/ibv_devinfo -l` lists the four `mlx5_*` devices.

&nbsp;

### 16.3 `/etc/trex_cfg.yaml`

```yaml
- version: 2
  port_limit: 2
  port_mtu: 1500
  interfaces: ['51:00.0', 'dummy']
  port_info:
    - src_mac:  <tgen experiment MAC>
      dest_mac: <dut experiment MAC>
    - src_mac:  00:00:00:00:00:00
      dest_mac: 00:00:00:00:00:00
  platform:
    master_thread_id: 0
    latency_thread_id: 1
    dual_if:
      - socket: 0
        threads: [2, 3, 4, 5, 6, 7]
```

- **`port_mtu: 1500` is mandatory.** Without it TRex asks for the largest MTU the PMD reports (`main_dpdk.cpp:4340`): mlx5 reports a generic 64 KiB `max_rx_pktlen`, TRex requests MTU 65518, the ConnectX-6 Dx refuses (max ~9.9 KB), and startup dies with `mlx5_net: port 0 failed to set MTU to 65518` / `dev_configure = -22`. 1500 covers our 1500 B frames; raise to 9000 only if jumbo frames are ever tested.
- **`'dummy'` second port.** TRex wants ports in pairs; the testbed has one cable. TRex's docs list *single interface, stateless* as functional. All traffic goes out port 0.
- **MACs, not IP/gateway.** l3fwd is pure DPDK and never answers ARP, so TRex has to address the dut's MAC directly (L2 mode).
- **Cores.** Master 0, latency 1, six workers 2–7, all distinct physical cores (HT siblings are `N,N+16` on `sm110p`). 100 Gb/s at 1500 B is only ~8.2 Mpps, so this is ample.

&nbsp;

### 16.4 Running it

```bash
cd /mydata/trex/v3.07 && sudo ./t-rex-64 -i -c 6 --no-ofed-check    # inside tmux
```

- **`-i`**: interactive server, required for stateless profiles and the Python API.
- **`--no-ofed-check`** replaces the old `ofed_info` stub trick. Side effect, from `dpdk_setup_ports.py`: with it TRex **no longer disables pause frames** on mlx5 — `node-setup.sh` does that instead (§14.1).
- No testpmd may be running on `tgen`; it would hold the port.

Smoke test, validated 2026-09-29 (`./trex-console`, then `start -f <profile> -m 10% -d 10 --port 0`, then `stats -p`): `opackets` = 8,202,100, exactly 10% of line rate for 10 s (a 1500 B frame is 1524 B on the wire, so line rate is 8.202 Mpps), `obytes/opackets` = 1504 (frame + FCS), `oerrors` = 0.

&nbsp;

---

## 17\. Measurement harness and first comparison (2026-09-29)

&nbsp;

### 17.1 Pieces

| File | Node | Role |
|---|---|---|
| `harness/run_l3fwd.sh <system>` | dut | Starts l3fwd as `privring-<N>` (any power of two 64–8192) or `shring-8`: same binary, cores 1–8, 8 queues (queue *q* on lcore *q+1*), §15 devargs, peer MAC learned over the link. Logs to `/mydata/dpdk-research/results/`. |
| `harness/trex/udp_multiflow.py` | tgen | TRex profile: UDP 1500 B to `198.18.0.1` (l3fwd's built-in route back out port 0), source IP walking 4096 values so `ETH_RSS_IP` spreads it over all queues, plus a 1000 pps latency stream. |
| `harness/trex/rate_sweep.py` | tgen | Offers the profile at 10/25/50/75/90/100% for 30 s each, appends one CSV row per rate: tx/rx packets, loss, Mpps, Gb/s, latency avg/min/max/jitter. Since 2026-10-01 rx and loss come from `rx_unicast_packets`; the switch's multicast/broadcast goes to `rx_noise_pkts` (§17.3). |
| `harness/pcm_memory.sh <label>` | dut | Records `pcm-memory` (1 s samples, system totals) to `/mydata/exp/pcm-mem_<label>.csv`. Start ~10 s before the traffic for an idle baseline; Ctrl-C after it. |
| `harness/pcm_summary.sh [labels]` | dut | Mean DRAM read/write over the loaded seconds (write > 20 MB/s) of each CSV. |

Protocol per system: start `run_l3fwd.sh` on dut → run `rate_sweep.py --label <system> --out <csv>` on tgen → Ctrl-C l3fwd, whose log now holds the final counters.

**Always log l3fwd through `tee -i`** (the script does). Ctrl-C reaches the whole pipeline; a plain `tee` dies at once, while l3fwd prints its final counters only *after* handling the signal — the first run of this harness lost exactly those lines.

&nbsp;

### 17.2 Result: balanced load, 1500 B, 8 cores

Two repetitions per system, 30 s per rate. **Loss: zero for all three systems at every rate in both runs**, with `rx_missed_errors` = `rx_out_of_buffer` = 0 on the dut. Mean latency (µs, run 1 / run 2):

| offered load | privRing-1024 | privRing-128 | shRing-8 |
|---|---|---|---|
| 10% | 57 / 60 | 57 / 65 | 69 / 58 |
| 25% | 48 / 48 | 63 / 52 | 50 / 44 |
| 50% | 41 / 37 | 39 / 39 | 38 / 36 |
| 75% | 32 / 32 | 32 / 30 | 33 / 33 |
| 90% | 32 / 34 | 32 / 33 | 32 / 33 |
| 100% | 39 / 43 | 39 / 40 | 40 / 40 |

- **The three systems are indistinguishable here.** Run-to-run variation of one system (up to 11 µs) exceeds the differences between systems. With 8 cores, 1500 B and balanced load the ring is not the bottleneck — which is what §11 predicts; separation needs imbalance, smaller packets or fewer cores.
- **shRing really ran shared:** `contention` = 440,085 over ~861 M packets (≈1 per 2,000), versus 0 for both privRings; the log shows `Creating RMP`. §15.3 had ≈1 per 6,600 at 4 cores, but under a different load, so the growth with core count is only a hint.
- **Counters reconcile exactly:** for privRing-1024 run 2 the 8 queues sum to 861,220,482, the exact number TRex sent across the six rates; queues differ by ~0.4% (identical split in every system, as RSS is deterministic).

&nbsp;

### 17.3 Measurement caveats found on the way

- **The latency stream always runs at 1000 pps.** `-m X%` does not scale it; TRex takes it out of the data stream to keep the total. Its fixed source IP lands every latency packet on queue 0, which is why queue 0 carries +1000 pps.
- **Switch multicast pollutes TRex's rx count.** TRex computes loss from port counters (`opackets − ipackets`), and `ipackets` includes ~1 multicast frame/s that is not ours. Source identified 2026-09-29 with `tcpdump -e` on the dut: **the CloudLab spine switch** (§8.1) — Rapid STP BPDUs, 2 every 2 s, plus LLDP every ~30 s; nothing came from either node's kernel. That is exactly the 31–33 "negative loss" per 30 s window (some runs saw ~57, presumably extra switch events). It sets loss resolution to ~10⁻⁵ % and could hide a real loss of a few dozen packets, so it must be fixed before any no-drop search. **It cannot be silenced at the source** (no access to the switch), so the fix is in the measurement: count `rx_unicast_packets` from TRex's xstats (`client.get_xstats(port)`, relative to the last `clear_stats()`, verified in the v3.07 API source) instead of `ipackets` — our returned traffic is all unicast to the tgen MAC, the switch's frames are all multicast. **Pending:** confirm the exact xstat name on the node (`stats -x --port 0` in `trex-console` after a short run), then change `rate_sweep.py` to use it, fail loudly if it is missing, and log the discarded noise as its own CSV column.
- **At low load, latency measures l3fwd's TX drain timer, not the ring.** l3fwd holds output until a burst of 32 (`MAX_PKT_BURST`) or a 100 µs timer (`BURST_TX_DRAIN_US`, `l3fwd.h:25-26`). At 10% load each of 8 queues sees ~100 kpps, i.e. ~310 µs to fill a burst, so the timer drains it — hence ~57 µs mean and ~150 µs max, falling to ~32 µs at 75–90%. The timer is identical for all systems, so comparisons stay fair, but ring-induced latency differences can only show at 75–100%.
- **Do not use l3fwd's `idle/total` as CPU utilisation.** It read 0.33 at 10% load (and 0.21 at 100% in §15.3), and idle + rx + tx + lookup cycles add up to only ~⅓ of `total_cyc`. How shRing's instrumentation accumulates these is unread; use loss, per-queue packets and `contention` until it is.
- **l3fwd prints the port counters once per lcore** (8 identical copies); only the cycle lines differ per core.

&nbsp;

### 17.4 DRAM bandwidth separates the systems (2026-10-01)

First run with `pcm-memory` beside the traffic, on a new instantiation (pause frames off, loss counted from unicast). Balanced load, 1500 B, **100% of line rate (8.2 Mpps) for 60 s**, one run per system. DRAM totals are the mean over the loaded seconds; idle is ~3 MB/s read, ~2 MB/s write.

| system | loss | DRAM read | DRAM write | write ÷ NIC write rate* | latency avg/max |
|---|---|---|---|---|---|
| privRing-1024 | 0 | 637 MB/s | **10,405 MB/s** | ~83% | 55 / 110 µs |
| privRing-128 | 0 | 190 MB/s | **962 MB/s** | ~8% | 72 / 124 µs |
| shRing-8 | 0 | 26 MB/s | **227 MB/s** | ~2% | 48 / 109 µs |

\* The NIC writes 8.2 Mpps × 24 cache lines (1536 B) ≈ 12.6 GB/s.

- **The working-set effect is real on this node, and large.** With 8 × 1024 buffers (~16 MiB) most of every received packet is written back to DRAM: the cores and the TX DMA read it while it is still in the LLC (read stays low), but its dirty lines are evicted before the buffer comes round again. An 8× smaller ring cuts DRAM writes ~11×. So `|DDIO|` here lies between the ~2 MiB and ~16 MiB working sets (§9.1); a ring-size sweep finds the knee.
- **It costs no throughput yet.** All three forward line rate with zero loss: ~10 GB/s of write-back fits in this node's 8-channel DRAM. Balanced 1500 B at 8 cores shows the mechanism, not a bottleneck — §17.2's tie stands for loss.
- **shRing-8 writes ~4× less than privRing-128** although both post 1024 buffers in total. Unexplained, and it matters for this project: it says the nominal ring capacity is not the whole working set. Candidates to check, by reading the code rather than guessing: buffers held in the TX rings until completion (`nb_txd` = 1024 per queue), the 256-entry per-lcore mempool caches (8 × 256 = 2048 buffers, more than either ring), and how `mlx5_rx_burst_rmp` refills the shared ring (§9.3).
- **privRing-128 had the highest latency** (72 µs vs 48–55). Consistent with a small ring filling during bursts, but it is one run, and §17.2 saw 11 µs run-to-run spread. Repeat before reading anything into it.
- privRing-1024's write fell steadily over the minute (11.2 → 10.0 GB/s). Not understood; watch whether it recurs.
- **Caveat:** one run per system. The shRing-8 CSV also holds a few seconds of an aborted run under identical settings.

