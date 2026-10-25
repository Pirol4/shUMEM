# Project Context — Decoupled Receive Rings with a Shared UMEM in DPDK

> **How to use this file.** Save it as `CLAUDE.md` at the root of the working repository. Claude Code loads it automatically at the start of every session, so each new chat begins with full project context instead of from scratch. Keep it up to date: when a design decision is made or a phase is completed, edit the relevant section so the document always reflects the current state.

&nbsp;

---

## 1\. How to work with me (persistent instructions)

Follow these on every session unless I say otherwise:

&nbsp;

- **I am learning, so explain to the maximum depth.** I need to deeply understand every change we make and *why* — not just receive working code. When you propose a change, explain the mechanism, the trade-offs, and how it fits the overall design before showing code.  
- **Claude applies the changes (revised 2026-10-05; I used to apply them by hand).** Edit the files, build, commit in my name (no co-author trailer) and push — harness and docs in `shUMEM`, driver code on the `fill-ring` branch of the fork. The explanation still comes first and in full: I must be able to defend every line. Never paste or ask for tokens in the chat; if a push is refused, stop and tell me.  
- **Guide me one step at a time on the nodes.** I run the CloudLab commands myself and paste the output; prefer single, ordered commands over large blocks, so I can run and verify each step. One small commit per step in the driver, each building on its own.  
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

- **Our fork of shRing-dpdk (the working tree):** [https://github.com/Pirol4/shRing-dpdk.git](https://github.com/Pirol4/shRing-dpdk.git), branch **`fill-ring`**, built on shRing's `c191506e3`. The implementation (§18) is written here; `git diff v21.05-rmp..fill-ring` is exactly what this project adds. Its first commit is the thin-archive build fix of §14.1, so the tree builds as cloned. `setup/dev-environment.sh` clones this branch on the nodes and refuses a tree that does not contain the pinned shRing commit. BSD-3-Clause like DPDK: keep the existing copyright lines, add ours to new files.  
- **shRing on DPDK (upstream of the fork, remote `upstream`):** [https://github.com/BorisPis/shRing-dpdk.git](https://github.com/BorisPis/shRing-dpdk.git) The published artifact and the source of both baselines.  
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
3. **mlx5 Rx path.** ✅ **Answered by reading the source (2026-10-05), §18.1:** the scalar `mlx5_rx_burst` refills each consumed slot in place, one for one, so the ring is always full; the hook is to post at a separate producer index instead. Still to prove on a node: that the NIC runs indefinitely with far fewer posted WQEs than the ring size.  
4. **UMEM \= mempool?** ✅ **Yes (§18.1):** l3fwd already creates one `rte_mempool` per port and socket and every Rx queue refills from it — the UMEM is shared today, under privRing too. What is private is the *posted* set.  
5. **Allocator semantics.** Half answered (§18.2): **a credit is one posted buffer** (one WQE the NIC may fill). The rebalancing policy is Phase 3's research question and is still open.  
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
  **Status (2026-10-13): Phases 1 and 2 done.** The FILL-ring path (fork branch `fill-ring`, `fill_en=1,fill_budget=<n>`) runs on the NIC with 8 queues over one mempool; ring 1024 with budget 128 measures like privRing-128 on DRAM write, a stalled core and a hot queue (§18.7–18.9). Compare systems at 98% of line rate, not 100% (§18.7).
  &nbsp;
- **Phase 3 — Dynamic allocation (the novelty).** Add the credit/allocation pool so cores take more/less UMEM dynamically and rebalance under imbalanced load. *Done when:* under a deliberately imbalanced load, the allocator shifts capacity and avoids the shRing-style bottleneck.  
  &nbsp;  
  **Status (2026-10-13): mechanism and policy run on the NIC.** `fill_dyn=1` (fork `ae3e52b94`): per-queue budgets that follow the backlog, a per-port pool, entitlement reclaimed from borrowers by an inverse lottery (§19). The first full table against shRing and the static budget is §19.17; it is one to three runs per row, and its open items are listed there.
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
- **The paper-style evaluation of 2026-10-22 (batteries f0–f15, per-queue HDR latency, synthetic NF, variability tests) is §20.**
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
| `harness/trex/udp_multiflow.py` | tgen | TRex profile: UDP 1500 B to `198.18.0.1` (l3fwd's built-in route back out port 0), source IP walking 4096 values so `ETH_RSS_IP` spreads it over all queues, plus a 1000 pps latency stream. `hot_share` (`rate_sweep.py --hot-share`) splits that fraction off as a single flow, i.e. one hot queue — the same queue as the latency stream. |
| `harness/trex/rate_sweep.py` | tgen | Offers the profile at 10/25/50/75/90/100% for 30 s each, appends one CSV row per rate: tx/rx packets, loss, Mpps, Gb/s, latency avg/min/max/jitter. Since 2026-10-01 rx and loss come from `rx_unicast_packets`; the switch's multicast/broadcast goes to `rx_noise_pkts` (§17.3). |
| `harness/pcm_memory.sh <label>` | dut | Records `pcm-memory` (1 s samples, system totals, pinned to core 0) to `/mydata/exp/pcm-mem_<label>.csv`. Easiest through `run_l3fwd.sh` with `PCM_LABEL=<label>`, which records for l3fwd's whole life and stops with it. |
| `harness/stall_core.py --core C --stall-us S --period-ms P` | dut | Stalls one lcore (root, `SCHED_FIFO`) for S µs every P ms and reports the stalls it made (§17.9). |
| `analysis/stall_model.py` | anywhere (needs matplotlib) | Stall-loss model vs measurements (§17.11): table on stdout, `docs/figures/stall_loss.{png,pdf}`. |
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


### 17.5 Ring-size sweep: the DDIO knee (2026-10-01)

privRing at every power of two from 64 to 4096 descriptors, same protocol as §17.4 (100% line rate, 60 s, one run each). "Written lines" is 8 queues × N buffers × 1536 B, the part of each buffer the NIC writes at 1500 B.

| system | written lines | DRAM write | ÷ 12.6 GB/s | DRAM read | loss | lat avg/max |
|---|---|---|---|---|---|---|
| privRing-64 | 0.75 MiB | 577 MB/s | 5% | 81 MB/s | **1,812 pkts (0.0004%)** | 39 / 107 µs |
| privRing-128 | 1.5 MiB | 962 MB/s | 8% | 190 MB/s | 0 | 72 / 124 µs |
| privRing-256 | 3 MiB | 2,763 MB/s | 22% | 176 MB/s | 0 | 39 / 113 µs |
| privRing-512 | 6 MiB | 7,001 MB/s | 56% | 363 MB/s | 0 | 42 / 107 µs |
| privRing-1024 | 12 MiB | 10,405 MB/s | 83% | 637 MB/s | 0 | 55 / 110 µs |
| privRing-2048 | 24 MiB | 10,494 MB/s | 83% | 653 MB/s | 0 | 43 / 114 µs |
| privRing-4096 | 48 MiB | 10,789 MB/s | 86% | 695 MB/s | 0 | 48 / 124 µs |
| shRing-8 (1 × 1024 shared) | 1.5 MiB | 227 MB/s | 2% | 26 MB/s | 0 | 48 / 109 µs |

- **The knee sits between 128 and 1024 descriptors per queue**, i.e. ~1.5–12 MiB of posted buffers, with the steepest rise from 256 to 512 (3 → 6 MiB). Above 1024 the curve is flat at ~83–86%: everything that can leak already does. The Xeon Silver 4314 has a 24 MiB LLC; at 12 ways that is 2 MiB per way, so the knee is consistent with the default 2 DDIO ways (~4 MiB). Consistent, not proven: the posted buffers are not the whole working set (next point), so the knee does not read off `|DDIO|` directly.
- **The default 1024 ring is past the knee on this node**, the same situation the shRing and rxBisect papers start from.
- **shRing-8 writes less than even privRing-64**, which posts half as many buffers (512 vs 1024). The shared ring's effective working set is smaller than its posted count, and the posted count alone does not explain the curve. `mlx5_rx_burst_rmp` refills with the same `rte_mbuf_raw_alloc` as the stock path, so the difference is not a different allocator; candidates are buffers held in the TX rings and the per-lcore mempool caches. Open; to be settled by reading the code and, if needed, counting buffers per stage.
- **privRing-64 is the first configuration that loses packets** under balanced load: 1,812 at line rate. A 64-entry ring at ~1 Mpps per queue holds ~64 µs of traffic, too little to absorb bursts. This is the trade-off the rxBisect paper reports for small privRing, and the one this project's allocator aims to remove: small working set *and* burst absorption.
- **The 1,812 lost packets are ring starvation, exactly.** l3fwd's final `rx_missed_errors` = 1,812, the same count TRex lost, and in this PMD `imissed` *is* the NIC's `out_of_buffer` queue counter (`mlx5_stats.c:150`, read through the DevX queue counter in `mlx5_os_read_dev_stat`): a packet arrived and the ring had no posted buffer. `nombuf` = 0, so the mempool never ran dry — the ring did. Oddly the `rx_out_of_buffer` *xstat*, which names the same counter, printed 0; unexplained, so read starvation from `rx_missed_errors`.
- privRing-128's 72 µs mean latency in §17.4 now looks like an outlier: 256 and 512 give 39–42 µs. Latency has no clear trend with ring size at this load.
- **Caveat:** one run per point. Repeat the knee region (128–1024) before using the curve in the thesis.

&nbsp;

### 17.6 Repetitions of the knee region (2026-10-01)

Two more runs (r2, r3) of each point, PCM now started by `run_l3fwd.sh` and pinned to core 0 (r1 was unpinned). DRAM write in MB/s, then mean and range:

| system | r1 | r2 | r3 | mean | range | loss r1 / r2 / r3 |
|---|---|---|---|---|---|---|
| privRing-64 | 577 | 111 | 270 | 319 | 111–577 | 1,812 / 1,911 / 1,760 |
| privRing-128 | 962 | 731 | 600 | 764 | 600–962 | 0 / 0 / 0 |
| privRing-256 | 2,763 | 3,062 | 2,633 | 2,819 | 2,633–3,062 | 0 / 0 / 0 |
| privRing-512 | 7,001 | 7,573 | 7,004 | 7,193 | 7,001–7,573 | 0 / 0 / 0 |
| privRing-1024 | 10,405 | 10,319 | 10,240 | 10,321 | 10,240–10,405 | 0 / 0 / 0 |
| shRing-8 | 227 | 201 | 122 | 183 | 122–227 | 0 / 0 / **198** |

(shRing-8's r2/r3 PCM files are named `privring-8_r2/_r3` — a typo in `PCM_LABEL`; `run_l3fwd.sh` rejects `privring-8`, so those runs were shRing.)

- **The knee is reproducible.** From 256 up, run-to-run spread is ≤ 10% of the mean and the ranges never overlap; 256 → 512 remains the steepest step.
- **Below the knee the absolute numbers are small and noisy** (privRing-64 spans 5×), and r1 is the highest in both 64 and 128 — possibly the unpinned PCM of r1, possibly order; unresolved. Treat values under ~1 GB/s as "almost nothing leaks", not as precise.
- **Revised from §17.4/17.5:** shRing-8 writes clearly less than privRing-128 (ranges 122–227 vs 600–962, no overlap), but it is **indistinguishable from privRing-64** (111–577). The claim "shRing writes less than even privRing-64" does not survive repetition.
- **privRing-64's loss is systematic:** 1,760–1,911 per minute in every run, all `rx_missed_errors` (ring starvation).
- **shRing-8 lost 198 packets in r3, under balanced load**, again all `rx_missed_errors` (`nombuf` 0), with `contention` = 254,779 (≈ 1 per 1,930 packets, the same rate as §17.2's 1 per 2,000). The shared ring ran out of posted buffers once in three runs. Whether that run had more contention than r1/r2 is still to be compared.
- **Latency does not separate the systems at this load:** 38–44 µs mean in almost every run, with isolated ~70 µs runs (privRing-128 r1, privRing-256 r2) that follow no system.

&nbsp;

### 17.7 First imbalance run: one hot queue (2026-10-01)

`--hot-share 0.5` at 100% line rate, 30 s per system, one run each. RSS split confirmed from l3fwd's xstats: queue 0 got 138.4 M of 246.1 M packets (**56.3%**), each other queue 15.4 M (6.25%) — one core at ~4.6 Mpps, seven at ~0.5 Mpps. The latency stream shares queue 0, so latency is the hot queue's.

| system | posted buffers | loss (pkts) | loss % | DRAM write | DRAM read | lat avg/max (hot queue) |
|---|---|---|---|---|---|---|
| privRing-1024 | 8 × 1024 | **0** | 0 | 6,919 MB/s | 876 MB/s | **161 / 217 µs** |
| privRing-256 | 8 × 256 | 5,940 | 0.0024% | 1,624 MB/s | 149 MB/s | 62 / 136 µs |
| privRing-128 | 8 × 128 | 13,556 | 0.0055% | 550 MB/s | 63 MB/s | 55 / 134 µs |
| privRing-64 | 8 × 64 | 17,329 | 0.0070% | 451 MB/s | 92 MB/s | 58 / 133 µs |
| **shRing-8** | 1 × 1024 shared | **170** | 0.0001% | 1,138 MB/s | 228 MB/s | 93 / 158 µs |

Label hygiene for whoever reads `/mydata/exp`: the privRing-128 run is `hot50_privring-128_certo` in PCM and was mislabelled `hot50_privring-1024` in tgen's `probe.csv` (second row with that label); `hot50_privring-128` in PCM is an aborted start with no traffic.

- **Imbalance turns the small-ring trade-off into loss.** Under balanced load only privRing-64 lost; with one hot queue every small privRing loses, monotonically in ring size (64 > 128 > 256 > 1024 = 0). The hot queue drains its ring ~9× faster than balanced, so a 128-entry ring holds only ~28 µs of its traffic.
- **shRing-8 is the paper's result reproduced:** with the same 1024 buffers as privRing-128 it loses **80× less** (170 vs 13,556), close to privRing-1024, while writing **6× less DRAM** than privRing-1024. The hot queue borrows the idle queues' buffers.
- **The cost moved to latency.** On the hot queue privRing-1024 queues packets the longest (161 µs) — a deep ring is a long queue; small privRings stay at 55–62 µs because what would wait is dropped instead; shRing sits between (93 µs).
- privRing-1024's DRAM write fell from 10.3 GB/s balanced to 6.9 GB/s here. Not yet understood (the hot ring recycles ~9× faster, the cold ones ~2× slower).
- **Not yet shown: shRing's bottleneck.** The shRing paper (and this project's motivation, §3) says the shared ring degrades under imbalance through contention; one hot queue at 50% did not break it. Next: shRing `contention` for this run, repetitions, and a `hot_share` sweep (0.25 / 0.75) to find where it does break — that regime is where the contribution has to win.

&nbsp;

### 17.8 Imbalance sweep, and why shRing's contention falls (2026-10-01)

`hot_share` 0.25 / 0.5 / 0.75, 100% line rate, 30 s, one run each (0.5 from §17.7). Hot-queue share from `rx_q0_packets`. Loss is TRex's, and **in every run it equals l3fwd's `rx_missed_errors` exactly** — all loss is ring starvation, none on TX or in the mempool (`nombuf` 0).

| hot_share | queue 0 | privRing-1024 loss / lat | privRing-128 loss / lat | shRing-8 loss / lat | shRing `contention` |
|---|---|---|---|---|---|
| 0 (balanced, §17.6, 60 s) | 12.5% | 0 / ~40 µs | 0 / ~40 µs | 0–198 / ~45 µs | ~255 k per 60 s |
| 0.25 | 34.4% | 0 / 138 µs | 5,291 / 77 µs | **0** / 90 µs | 97,776 |
| 0.5 | 56.3% | 0 / 161 µs | 13,556 / 55 µs | 170 / 93 µs | 32,142 |
| 0.75 | 78.1% | 0\* / 126 µs | 21,753 / 49 µs | 64 / 140 µs | 6,824 |

\* TRex saw 13 *more* unicast frames than it sent: something besides l3fwd's forwarding sends unicast to the tgen MAC (the dut kernel shares the port; §17.3's ARP probes are one known source). Loss resolution from TRex is therefore ~±tens of packets; `rx_missed_errors` on the dut is the exact count.

- **One core handles 78% of line rate** (~6.4 Mpps at 1500 B) without loss under privRing-1024, so none of this loss is CPU-bound.
- **privRing-128's loss grows with the skew** (0 → 5 k → 14 k → 22 k); **shRing-8 stays near zero** at every skew with the same 1024 buffers. Under a hot-queue imbalance shRing is not the bottleneck — it is the best of the small-working-set systems.
- **shRing's `contention` falls as the skew rises** (≈128 k per 30 s balanced → 6.8 k at 0.75). Reading the code explains it: `contention` counts only failed CAS on the shared ring's head (`mlx5_rx.c:1134-1141`), i.e. two cores trying to advance it at the same moment. Fewer active cores, fewer races.
- **The head advances only in sequence** (`mlx5_rx.c:1119-1146`): it moves by a 64-entry block once *every* entry of that block has been consumed, whoever's queue it landed on, and only then does the doorbell hand those buffers back to the NIC. A core that falls behind therefore holds back buffer return for **all** queues — head-of-line blocking. That, not CAS contention, is the mechanism by which shRing should degrade, and a hot queue served by a core that keeps up never triggers it. **Hypothesis to test:** stall one core (not overload the link) and shRing should lose on every queue while privRing loses only on the stalled one. The unexplained 198-packet loss in balanced shRing r3 (§17.6) fits it if an OS hiccup paused one lcore — `isolcpus` is still off (§15.5).
- **Implication for this project's framing (§3):** "shRing bottlenecks under imbalanced load" needs to say *which* imbalance. Skewed packet rates with cores that keep up do not hurt it here; a slow or stalled consumer should. Check this against the rxBisect paper's own description before writing it into the thesis.

&nbsp;

### 17.9 Stalling one core: shRing's head-of-line blocking confirmed (2026-10-01)

Balanced load, 100% line rate, 30 s. A CPU hog shares lcore 4 (queue 3): `sudo nice -n 19 taskset -c 4 sh -c 'while :; do :; done'`. Unstalled, each queue gets ~30.75 M packets in 30 s (RSS split is deterministic: q0 30,784,156, q4 30,814,221, the rest 30,754,155/156 in every run).

| system | loss (= `rx_missed_errors`) | q3 received | other queues | shRing `contention` |
|---|---|---|---|---|
| privRing-1024 | 26.4 M (10.7%) | **4.26 M** (−86%) | untouched | – |
| privRing-128 | 2.93 M (1.19%) | **27.76 M** (−9.7%) | untouched (q6 −18) | – |
| shRing-8 | **23.5 M (9.57%)** | 27.76 M (−9.7%) | **every queue 27.81–27.87 M (−9.5%)** | 118,916 |

- **The hypothesis of §17.8 holds.** Under privRing a stalled core loses only its own queue's packets; the other seven receive their exact unstalled counts. Under shRing the loss spreads evenly over **all eight queues**, each losing the fraction the stalled queue lost alone (~9.5% vs privRing-128's 9.7% on q3). One slow consumer blocks the in-sequence head, the doorbell stops returning buffers, and the whole NIC queue set starves — **8× the loss of privRing-128 for the same stall**. This is the shRing bottleneck this project targets, and per-core FILL rings over a shared UMEM have no shared in-sequence head to block.
- **privRing-1024 losing 86% of q3 is not understood.** A nice-19 hog should get ~1.5% of the core, far too little to explain it, and a bigger ring should absorb more, not less. Most likely the stall was not the same across runs: a `nice` hog's slices depend on the scheduler, and nothing measured them. Do not use this row.
- **Hence `harness/stall_core.py`:** a controlled stall injector — pinned to one core at `SCHED_FIFO` priority, it busy-waits `--stall-us` every `--period-ms` and reports the stalls it actually made, so expected loss can be computed and checked. Predictions for 1 ms every 100 ms on lcore 4 (300 stalls in 30 s; one queue receives ~1,025 packets/ms, all queues ~8.2 per µs):
  - privRing-N loses ≈ max(0, 1,025 − N) per stall: privRing-1024 ≈ 0, privRing-128 ≈ 0.27 M, privRing-64 ≈ 0.29 M;
  - shRing-8's 1,024 shared buffers last ~125 µs once the head blocks: ≈ (1,000 − 125) × 8.2 ≈ 7.2 k per stall ≈ **2.2 M**.

&nbsp;

### 17.10 Controlled stalls match a simple model (2026-10-01)

`stall_core.py --core 4 --stall-us 1000 --period-ms 100` (lcore 4 = queue 3; it reported mean stall 1,000 µs, 1.00% of the CPU), balanced load, 100% line rate, 30 s, so ~300 stalls inside the traffic window. Queue 3's unstalled share is 30,694,089 packets (from the privRing-1024 run: received + missed; every other queue's count is the deterministic one of §17.9), i.e. **1,023 packets per ms**; all queues together bring **8.20 packets per µs**. In every run the per-queue counts plus `rx_missed_errors` add up exactly to TRex's tx.

| system | model per stall | model total | measured `rx_missed_errors` | per stall | where |
|---|---|---|---|---|---|
| privRing-1024 | max(0, 1,023 − 1,024) = 0 | 0 | 2,074 | 6.9 | q3 only |
| privRing-128 | 1,023 − 128 = 895 | 268.5 k | **270,213** | 900.7 | q3 only |
| shRing-8 | (1,000 − 1,024/8.20) × 8.20 = 7,178 | 2.15 M | **2,183,169** | 7,277 | **272–273 k on each of the 8 queues** |

- **The model predicts the loss within 1.5%** for both small-ring privRing and shRing, and privRing-1024 sits at the edge (its ring holds ~1 ms of one queue's traffic, so ~7 packets per stall spill). The mechanism is understood quantitatively, not just qualitatively:
  - privRing: a stalled queue loses what arrives beyond its own ring capacity, `rate_q × stall − N`; nobody else notices.
  - shRing: once the stalled core blocks the in-sequence head, the *whole* NIC's arrival rate drains the shared ring in `B / rate_total` (125 µs for 1,024 buffers at 8.2 Mpps), and everything after that is lost **on every queue alike**.
- **Same 1,024 buffers, 8× the loss:** shRing-8 loses 2.18 M where privRing-128 loses 0.27 M. Sharing helps when cores keep up (§17.7–17.8) and hurts badly when one does not.
- **Latency is not informative here:** the latency stream sits on queue 0, and packets lost during a stall do not count; privRing-1024's 136 µs mean (q0 unstalled) is unexplained run-to-run variation of the kind §17.6 saw.
- **The design target this sets for Phase 1:** shRing's two properties come from the same structure — one shared ring gives the hot queue idle queues' buffers (good) and one in-sequence head (bad). The mempool is *already* shared by all privRing queues; what privRing lacks is a way for a hot queue to have more buffers *posted*. So the contribution is a per-queue posted-buffer budget drawn from a global one (the credit pool of §4): small and private by default, so a stall stays local (privRing's row above), growable for a hot queue (shRing's §17.7 row). §9.3–9.5 now have a concrete question: can the mlx5 RQ keep fewer WQEs posted than its size and post more on demand?

&nbsp;

### 17.11 Stall-length sweep: the model holds from 250 µs to 2 ms (2026-10-01)

Same setup as §17.10, stalls of 250 / 500 / 1000 / 2000 µs every 100 ms (300 per run). `analysis/stall_model.py` holds the data, prints model vs measured and draws `docs/figures/stall_loss.{png,pdf}` (one panel per system, shared y axis). Nothing is fitted.

| system | 250 µs | 500 µs | 1000 µs | 2000 µs |
|---|---|---|---|---|
| privRing-1024 model / measured | 0 / 0 | 0 / 0 | 0 / 2,074 | 306,682 / 308,862 (+0.7%) |
| privRing-128 model / measured | 38,335 / 39,911 (+4.1%) | 115,070 / 116,648 (+1.4%) | 268,541 / 270,213 (+0.6%) | 575,482 / 577,140 (+0.3%) |
| shRing-8 model / measured | 307,958 / 335,119 (+8.8%) | 923,116 / 952,243 (+3.2%) | 2,153,432 / 2,183,169 (+1.4%) | 4,614,065 / 4,641,334 (+0.6%) |

- **The model holds across an 8× range of stall lengths.** The measured excess is a near-constant number of packets per stall, not a proportion — ~5 for privRing-128, ~95 for shRing-8 — which is why the relative error is largest at 250 µs. ~5 packets at one queue's 1.02 per µs is ~5 µs, plausibly the extra time the stall really takes (wake-up and context switch around the busy-wait). For shRing those 5 µs explain only ~40 of the ~95; the rest would mean ~50 fewer usable buffers than 1,024 — consistent with the head moving in 64-entry blocks (§17.8), which leaves up to 63 consumed buffers not yet returned to the NIC at any moment. A hypothesis, not checked.
- **privRing-1024 starts losing once the stall outlasts its ring** (between 1 and 2 ms, as predicted): a bigger ring only moves the threshold. shRing loses at every stall length, because its ring is drained by the whole NIC, not one queue.
- **One count does not reconcile:** at 2 ms, privRing-1024's TRex loss is 310,640 but `rx_missed_errors` is 308,862 — 1,778 packets (0.6%) lost somewhere other than ring starvation, the only run of the day where the two differ. Candidate: l3fwd drops on TX when, after a 2 ms backlog, a full ring's worth is forwarded in one go and the TX queue is full (l3fwd frees what `rte_eth_tx_burst` refuses, and no counter shown here records it). Unchecked.
- The 2 ms point at 0.13–1.9% loss and the 1 ms point are the clearest pair for the thesis: same buffers, shRing loses 8× what privRing-128 loses.

&nbsp;

---

## 18\. Phase 1 design: a posted-buffer budget per queue (2026-10-05)

&nbsp;

### 18.1 What the source says (shRing tree, `drivers/net/mlx5`, privRing path)

- **The RQ is a cyclic work queue** (`MLX5_WQ_TYPE_CYCLIC`, `mlx5_devx.c:297`) of `nb_rxd` WQEs. Each WQE holds one buffer address; the NIC fills them in ring order. Software tells the NIC how far it may go by writing a free-running counter to the doorbell record `rq_db`: "WQEs posted so far".
- **At start the ring is posted full:** `mlx5_rxq_initialize` puts a buffer in every WQE and writes `rq_db = wqe_n` (`mlx5_rx.c:427-430`).
- **`mlx5_rx_burst` refills in place, one for one** (`mlx5_rx.c:841`): for each received packet it takes the mbuf of slot `rq_ci & mask`, allocates a replacement from the same pool (`rte_mbuf_raw_alloc`, line 869), writes the replacement's address into *the same* WQE (line 924), and at the end of the burst writes `rq_db = rq_ci` (line 956).
- **So one variable does two jobs.** `rq_ci` is both the read position (the slot the next completion belongs to) and the count of posted WQEs, and the two differ by exactly the ring size, forever. That identity — *posted = consumed + ring size* — is the "dual role of the Rx ring" in this driver, and it is why ring size, burst capacity and I/O working set are one number under privRing.
- **The driver already runs the RQ less than full elsewhere.** The vectorized path keeps separate counters — `rq_pi` consumed, `rq_ci` posted — and replenishes lazily in bulk at the producer position (`mlx5_rx_replenish_bulk_mbuf`, `mlx5_rxtx_vec.c:87`), only once ≥ 64 slots are empty. So the NIC accepts fewer posted WQEs than the ring holds; what is unproven is a *large, permanent* gap (128 posted in a ring of 1024).
- **The UMEM is already shared.** l3fwd creates one mempool per (port, socket) (`examples/l3fwd/main.c:902-907`) and passes it to every Rx queue, with a 256-mbuf per-lcore cache. Nothing needs to change to share buffers; only how many each queue keeps *posted*.
- **New datapaths are added beside the old ones.** shRing gates its own burst functions behind `rmp_en` in `mlx5_select_rx_function` (`mlx5_ethdev.c:592-615`) and leaves `mlx5_rx_burst` untouched. Ours goes in the same way, so one binary still yields every system.

&nbsp;

### 18.2 The design

- **Separate the two jobs of `rq_ci`.** Keep a read index (slots consumed) and a post index (WQEs posted), with `posted − consumed = budget ≤ ring size`. On receive, take the mbuf at the read index and post a fresh one at the *post* index — `budget` slots ahead — instead of into the slot just emptied. The ring stays large (room to grow); the working set is `budget` buffers.
- **Mapping to AF_XDP (§5):** the post index is the FILL ring's producer, the read index the RX ring's consumer, the mempool the UMEM, and **a credit is one posted buffer**.
- **Devargs**, next to shRing's: `fill_en=1,fill_budget=<n>` (names provisional). `fill_en=0` must leave privRing and shRing bit-for-bit as they are.
- **Phase 1 (static budget, any number of queues)** is done when a ring of 1024 with budget 128 forwards without loss and **measures like privRing-128** on the three tests we already have: DRAM write (§17.6: ~0.6–1 GB/s, not privRing-1024's 10 GB/s), hot queue (§17.8), stalled core (§17.11: loss only on the stalled queue, `rate × stall − budget`). Same numbers as privRing-128 is the *success* criterion here — it proves the budget, not the ring size, sets the working set.
- **Phase 3 then makes the budget move:** a queue that runs low borrows credits from a global pool and posts further ahead; an idle one gives them back. No shared in-sequence head (§17.8), so a stalled core can at worst sit on the credits it holds. That policy is the contribution and is not designed yet.

&nbsp;

### 18.3 Risks to retire first

1. **Does the NIC run with a permanent large gap?** Retired by the first Phase 1 run itself: budget 128 in a ring of 1024 at line rate.
2. **Initialization.** `mlx5_rxq_initialize` and the elts allocation assume a full ring (`rxq_alloc_elts_sprq`, `mlx5_rxq.c`); with a budget only the first `budget` slots are posted, and stop/free paths (`rxq_free_elts_sprq`) must free only slots that hold an mbuf.
3. **Error recovery** (`mlx5_rx_err_handle`, `mlx5_rx.c:~500`) re-posts the whole ring; it must restore `budget`, not `wqe_n`.
4. **Scatter (`sges_n`) and CQE compression** are off in our configuration (§15); the first version asserts that rather than supporting them.

&nbsp;

### 18.4 How the design follows AF_XDP and the two papers (read 2026-10-05)

**AF_XDP** (docs.ebpf.io). The FILL ring is where the process *produces* chunk addresses for the kernel to fill; the RX ring is where the kernel *produces* descriptors of filled chunks. Both are single-producer, single-consumer. If the FILL ring is empty the packet is dropped. With `XDP_SHARED_UMEM` across queues, the UMEM is one but **each (netdev, queue) pair gets its own FILL ring** and each socket its own RX ring, and the docs leave to the process the "algorithms to maintain a desired balance of chunks". That is this design's shape exactly: one mempool, a private FILL position per queue written only by that queue's lcore (so no cross-core synchronisation on the datapath, unlike shRing), and the balancing algorithm is the credit pool of Phase 3.

**rxBisect** (OSDI '25) names the same split: *allocation* (Ax) rings, where a core produces empty buffers, and *bisected reception* (Bx) rings, where the NIC produces packets; "the number of allocated buffers in each Ax ring can be smaller than the size of the Bx rings, which is the key to reducing the I/O working set". Its receive loop (Listing 1) is: consume a Bx entry, hand up the packet, allocate a replacement into the Ax ring, advance the Ax tail and ring its doorbell — the loop of §18.2 with `fill_budget` as |Ax|. Its sizing rules (§4.5) give our defaults: |Bx| = 1024 to absorb bursts; k × |Ax| × 1500 B ≤ |DDIO| and k × |Ax| ≥ |Bx|, with a vendor minimum of 64. For 8 queues that is **|Ax| = 128**: 8 × 128 = 1024, and ~1.5 MiB, below the knee measured in §17.5.

In mlx5 terms the Bx ring already half exists: packets are *delivered* through the completion queue (CQ), a separate ring of descriptors, while the RQ holds the buffers. What ties them is only the in-place refill of §18.1.

**Where we differ from rxBisect, and must say so.** In rxBisect the *NIC* takes a buffer from another core's Ax ring when the target's is empty; that needs an ASIC change, and the paper evaluates it with a software NIC on a dedicated emulator core. A commodity NIC fills a queue only from that queue's posted WQEs, so here buffers move between queues in *software*: a queue that runs low takes credits from the shared pool and posts further ahead. Consequence: it can help a hot queue whose core is running, but not a core that is not polling at all — that queue is bounded by what it had posted, like privRing with that ring size. No ASIC change and no emulator core is the trade.

**Both papers describe shRing's bottleneck the way §17.9–17.11 measured it:** overloaded cores, whose service rate is below their arrival rate, "monopolize" the shared ring and packets to healthy cores are dropped (shRing §4.5, rxBisect §3.2 and Fig. 6c); dynamic shRing turns itself off in that case. So §3's "bottlenecks under imbalanced load" means *a consumer that cannot keep up*, not skewed rates as such — which is why a hot queue on a core that kept up did not hurt it (§17.8). rxBisect's stated property is the target: an overloaded core "can only hog packet buffers… but cannot interfere with packet reception by other cores".

&nbsp;

### 18.5 Progress

- **Step 2 (fork `502d9a270`):** `fill_en` and `fill_budget` are parsed and validated (`mlx5.c`, `mlx5_fill_args_validate`): a budget of 1–65535, not together with `rmp_en`, `rx_vec_en` or `mprq_en`.
- **Step 4 (fork `9884138a1`):** `mlx5_rx_burst_fill()` and `mlx5_rxq_fill_post()` in `mlx5_rx.c`. `rq_pi` counts WQEs consumed (RX-ring consumer), `rq_ci` WQEs posted (FILL-ring producer, the doorbell value); the slot a packet is read from is left `NULL` and fresh buffers go to `rq_ci` until `rq_ci − rq_pi` is back at `fill_budget`. Allocation is per buffer with `rte_mbuf_raw_alloc`, as in the stock path, to keep the allocator cost comparable. One deliberate difference from stock: reception does not wait for the replacement buffer, so a failed allocation delays posting, not delivery.
- **Step 3 (fork `b46c129b8`):** queue setup checks single-segment and budget ≤ ring (`mlx5_rxq_new`), allocates only `fill_budget` mbufs (`rxq_alloc_elts_sprq`), and `mlx5_rxq_fill_initialize()` writes every WQE's size and lkey, packs the held buffers into the first slots and sets the doorbell to their count. `mlx5_select_rx_function` returns the new burst when `fill_en` is set. Stop/free needed no change.
- **`harness/run_l3fwd.sh fill-<B>`** runs it: ring 1024, budget B, §15 devargs, and `--log-level=pmd.net.mlx5:info` so each queue prints `FILL-ring mode, B of 1024 WQEs posted`.
- **Verified so far:** the whole tree builds here (Ubuntu 24.04, gcc 13) with no new warnings, and a model of the index logic (NIC filling in ring order up to the doorbell, random bursts, allocation failures, resets) holds its invariants over 20,000 steps for five ring/budget pairs. **Not verified:** anything on a NIC. The error-recovery path cannot be provoked on demand.
- **With `fill_en=0` nothing changes** except one predictable branch at the top of `mlx5_rxq_initialize` and of `mlx5_select_rx_function`, both off the datapath; `mlx5_rx_burst` itself is untouched. The baselines should still be re-measured once on this binary before being compared with it.

### 18.6 First test on the nodes (next)

On a fresh instantiation the setup clones the fork's `fill-ring` branch. In order:

1. **It starts:** `run_l3fwd.sh fill-128` prints the eight `FILL-ring mode, 128 of 1024 WQEs posted` lines and reaches the main loop.
2. **It forwards:** 10% of line rate for 10 s with zero loss and the usual per-queue split. This also retires risk 1 of §18.3 — the NIC running with a permanent large gap.
3. **It refuses bad arguments:** `fill_budget` above the ring, or `fill_en` with `rmp_en`, fails at start with the message, not at run time.
4. **It measures like privRing-128** (Phase 1's criterion), same three tests, with privRing-128 and privRing-1024 re-run beside it on the same binary: DRAM write at 100% (§17.6), one hot queue (§17.8), one stalled core at 1 ms (§17.10: loss only on queue 3, ≈ 270 k).

&nbsp;

### 18.7 First run on the NIC: the budget sets the working set (2026-10-13)

Fresh instantiation, fork `b46c129b8`, dut port `51:00.0` (MAC `b8:3f:d2:13:08:ee`), tgen MAC `b8:3f:d2:13:08:42`. `trex-setup.sh` ran end to end on a real node for the first time, unmodified.

- **Tests 1–3 of §18.6 pass.** All eight queues report `FILL-ring mode, 128 of 1024 WQEs posted`. 10% of line rate for 10 s, twice: 8,202,101 sent and received each time, `rx_missed_errors`, `rx_out_of_buffer` and `nombuf` all 0 — **the NIC runs with 128 WQEs posted in a ring of 1024** (risk 1 of §18.3 retired). `fill_en` with `rmp_en` fails the probe; `fill_budget=2048` in a ring of 1024 fails queue setup with the message (it returns `-12`; `-22` would be right — cosmetic, not fixed).
- **`run_l3fwd.sh fill-<B>-<N>`** (shUMEM `038b1a8`) posts B buffers in a ring of N; `fill-<B>` is still N = 1024.

**Balanced load, 1500 B, 60 s, one run each** (DRAM from PCM; loss and latency from TRex):

| system | ring | posted | 98%: DRAM write | 98%: loss | 98%: lat avg | 100%: DRAM write | 100%: loss |
|---|---|---|---|---|---|---|---|
| privRing-128 | 128 | 128 | 735 MB/s | 0 | 32 µs | 1,206 MB/s | 0 |
| fill-128-128 | 128 | 128 | 597 MB/s | 0 | 32 µs | 2,392 MB/s | 8,714 |
| **fill-128** | **1024** | **128** | **614 MB/s** | 9 | 32 µs | 3,624 / 3,812 MB/s | 11,865 / 27,792 |
| privRing-1024 | 1024 | 1024 | 10,131 MB/s | 0 | 32 µs | 10,431 MB/s | 0 |

(100% also: fill-128-256 3,950 MB/s, 24,268 lost; fill-128-512 4,137 MB/s, 28,081 lost.)

- **Phase 1's first criterion holds at 98%:** a ring of 1024 with 128 buffers posted writes what privRing-128 writes (614 vs 735 MB/s, inside the §17.6 spread for this region) and **16× less than privRing-1024 with the same ring**. The budget, not the ring size, sets the I/O working set. `fill-128-128` and `fill-128` are indistinguishable, so nothing that scales with the ring (the CQ, the WQE and elts arrays) matters at this load.
- **The 9 packets `fill-128` lost at 98% are ring starvation on one queue:** `rx_missed_errors` = 9, queue 5 received 60,277,921 against 60,277,929/930 on the queues with the same share, Tx equals Rx on every queue. With 128 buffers posted and ~1 packet/µs, a ~137 µs pause of that lcore does it (`isolcpus` is off, §15.5). It is what a budget of 128 costs, not a defect of the path; privRing-128 would lose the same to the same pause.
- **At exactly 100% the FILL runs fall into a Tx backlog, and the numbers there are not a property of the Rx path.** In every 100% FILL run all loss is on the dut's *Tx*: `rx_good_packets` equals what TRex sent, `rx_missed_errors` is 0, and `tx_good_packets` is short by exactly TRex's loss. In `fill-128-128` the whole 8,714 is queue 4 (rx 61,628,386, tx 61,619,672; the other seven match packet for packet) — the busiest queue, ~0.2% above the mean. Reading: at 100% the dut must send exactly as fast as it receives, so any backlog in the Tx rings never drains, it lands on the queue that asks for more than its fair share of the wire, and l3fwd drops when that 1024-entry Tx ring is full. Up to ~1000 packets parked in a Tx ring is ~1.5 MB more buffers in use, which would explain the doubled DRAM write and the DRAM *read* of 1.1–2.2 GB/s (38–42 MB/s at 98%). CPU is not the limit (per-lcore Rx/Tx cycles equal privRing-128's) and the INFO log level prints nothing during traffic.
- **Open: why only the FILL path falls into it** — 5 of 5 FILL runs at 100% against 0 of 2 privRing runs, interleaved. With ring = budget the two burst functions do the same work; the one ordering difference is that the FILL path allocates its replacements after the burst instead of one per packet. Not explained by reading the code.
- **Consequence for the method:** exactly 100% is a knife edge on the Tx side that has nothing to do with the Rx ring. Latency says the same: 32 µs for every system at 98%, 99–199 µs at 100% (privRing included). Compare systems at 98%; treat the 100% rows of §17 as carrying this caveat until re-measured.


### 18.8 A stalled core costs the FILL path exactly what it costs privRing-128 (2026-10-13)

`stall_core.py --core 4 --stall-us 1000 --period-ms 100` (lcore 4 = queue 3; it reported mean 1,001 µs, 1.00% of the CPU, and was left running across the three runs), balanced load, **98%** of line rate, 30 s, so ~300 stalls per run. Model of §17.10 at this rate: queue 3 receives 1,002.7 packets per ms (received + missed), so a 1,001 µs stall with 128 buffers posted loses 1,003.7 − 128 = 875.7, **262.7 k** in 300 stalls.

| system | ring / posted | loss (TRex) | `rx_missed_errors` | per stall | q3 received | other seven queues |
|---|---|---|---|---|---|---|
| privRing-128 | 128 / 128 | 264,061 | 264,061 | 880.2 | 29,816,037 | unstalled counts |
| **fill-128** | **1024 / 128** | **263,916** | **263,916** | **879.7** | 29,816,182 | **the same counts as privRing-128, packet for packet** |
| privRing-1024 | 1024 / 1024 | 882 | 199 | – | – | – |

- **Second Phase 1 criterion holds.** The FILL path loses what privRing-128 loses (145 packets apart in 264 k), 0.5% above the model, all of it `rx_missed_errors`, **all of it on the stalled queue**: q0–q2 and q4–q7 hold identical counts in the two runs. No shared head, so no head-of-line blocking — the property shRing lacks (§17.10: 2.18 M spread over all eight queues for the same stall at 100%).
- **The ring of 1024 does not help the FILL path here, by design:** with a static budget a stalled queue is bounded by what it had posted. That is the "like privRing with that ring size" of §18.4, and the room Phase 3's credits are meant to use.
- **privRing-1024 loses on Tx, not Rx.** Its l3fwd log also covers an aborted ~2 s start with a wrong label, so its counters are lifetime totals: 199 `rx_missed_errors` plus **713 packets received and never sent, all on queue 3** (`rx_q3` 31,975,263, `tx_q3` 31,974,550) = 912, against TRex's 882 for the 30 s window. The ring absorbs the stall (~1,003 packets in 1,024 slots), then the lcore hands ~1,000 packets to a 1,024-entry Tx ring on a wire with 2% of slack and l3fwd drops what does not fit. ~300× less loss than the 128-buffer systems, at 16× their DRAM write. **For Phase 3:** a queue that is lent more buffers absorbs a longer stall on Rx only to release it as a Tx burst; where the Tx ring is the next limit has to be measured, not assumed.

### 18.9 One hot queue: the static budget loses like privRing-128, and Phase 1 is done (2026-10-13)

`--hot-share 0.5`, **98%** of line rate, 30 s, one run each. Queue 0 gets 135,655,342 of 241,141,744 packets (56.3%). In all three runs `tx_good_packets` equals `rx_good_packets`, and the loss is exactly what queue 0 is short of that count.

| system | ring / posted | `rx_missed_errors` | loss (TRex) | DRAM write | DRAM read | lat avg/max (hot queue) |
|---|---|---|---|---|---|---|
| privRing-128 | 128 / 128 | 13,022 | 13,009 | 322 MB/s | 45 MB/s | 24 / 77 µs |
| **fill-128** | **1024 / 128** | **13,097** | 13,083 | 476 MB/s | 48 MB/s | 22 / 93 µs |
| privRing-1024 | 1024 / 1024 | 0 | −12 | 5,915 MB/s | 429 MB/s | 21 / 95 µs |

- **Third Phase 1 criterion holds:** 13,097 against 13,022, all on the hot queue; DRAM write in the "almost nothing leaks" region for both (§17.6: values under ~1 GB/s are not precise), 12–18× below privRing-1024. TRex is ~13 packets off the dut's count in each run, the known stray unicast of §17.8.
- **Phase 1 is done, and Phase 2 with it** (the code never was single-queue): with a static budget of 128 in a ring of 1024, eight queues over one mempool measure like privRing-128 on DRAM write (§18.7), a stalled core (§18.8) and a hot queue (this table), at 98% of line rate. The budget, not the ring, is the working set.
- **This table is the case Phase 3 has to win.** The hot queue loses 13 k because it may post only 128 buffers while seven queues at 6% of the load sit on 128 each and 896 slots of its own ring stay empty. shRing lost 170 here (§17.7, at 100%) by lending it the idle queues' buffers; privRing-1024 loses nothing at 12× the DRAM write. Target: the hot queue's loss near zero with the total posted still ~1,024, **and** the stalled-core row of §18.8 unchanged (loss on the stalled queue only).

### 18.10 shRing-8 re-measured at 98%, and the table Phase 3 is judged by (2026-10-13)

Same instantiation, same binary, same three tests. Stall model for shRing at this rate (§17.10): the 1,024 shared buffers last 1,024 / 8.038 per µs = 127 µs once the head blocks, so a 1,001 µs stall loses (1,001 − 127) × 8.038 = 7,022, **2.107 M** in 300 stalls.

| test (98% of line rate) | privRing-128 | **fill-128** (ring 1024) | shRing-8 | privRing-1024 |
|---|---|---|---|---|
| balanced, 60 s: DRAM write | 735 MB/s | 614 MB/s | **232 MB/s** | 10,131 MB/s |
| balanced: loss | 0 | 9 | 0 | 0 |
| 1 ms stall on lcore 4, 30 s: loss | 264,061 | 263,916 | **2,131,633** | 882 |
| stall: where | q3 only | q3 only | **every queue, −0.9% each** | q3 (Tx) |
| hot queue 56%, 30 s: loss | 13,022 | 13,097 | **0** | 0 |
| hot queue: DRAM write | 322 MB/s | 476 MB/s | 800 MB/s | 5,915 MB/s |

- **shRing's two faces hold at 98%.** It lends the hot queue the idle queues' buffers (0 lost where the 128-buffer systems lose 13 k) and it is the leanest under balanced load, but one stalled core costs it **8.1× what it costs the FILL path** (2.13 M vs 0.26 M), spread over all eight queues (each ~29.87 M received against ~30.14 M unstalled), 1.2% above the model. `contention` 245,396 balanced in 60 s, 120,454 in the stall run.
- **The hot-queue l3fwd log of shRing was not captured in the paste** (a ~5 s aborted start, `l3fwd_shring-8_20261013-143121.log`, took its place among the last three); loss is TRex's, and it is 0.
- **What Phase 3 has to show, in this table's terms:** the hot-queue row of shRing (≈0) together with the stall row of fill-128 (loss on the stalled queue only, ~264 k), at a balanced DRAM write that stays under ~1 GB/s. No system here has both.

&nbsp;

---

## 19\. Phase 3 design: credits moved by an inverse lottery (direction approved 2026-10-13; modelled in §19.6, no driver code yet)

The advisor's suggestion: base the credit policy on lottery scheduling — Waldspurger & Weihl, *Lottery Scheduling: Flexible Proportional-Share Resource Management*, OSDI '94 (assumed to be the paper meant; confirm).

&nbsp;

### 19.1 What the paper gives us

- **Tickets are rights, not the resource** (§2). A client holding `t` of `T` tickets is entitled to `t/T` of the resource under contention; what a client does not use is available to the others.
- **Space-shared resources use an inverse lottery** (§6.2, the paper's example is physical pages): when a unit is needed and none is free, a *loser* is drawn to give one up, with probability `p = (1/(n−1)) · (1 − t/T)` for a client with `t` tickets among `n` clients. More tickets, less likely to lose. Posted buffers are exactly this kind of resource: held, not consumed in quanta.
- **Not taken:** compensation tickets (§3.4) correct for a partly used time quantum, which has no analog for a held buffer; ticket transfers (§3.1) and inflation (§3.2) are for clients that block on each other; currencies (§3.3) would be one pool per port or NUMA node, which one port does not need.

| lottery scheduling | here |
|---|---|
| resource unit | a **credit** = one posted buffer (§18.2) |
| client | an Rx queue (its lcore) |
| ticket | the queue's relative right to credits; equal by default |
| entitlement | `e_i = C · t_i / T`, with `C` the total credits (= `n × fill_budget`, 1,024 today) |
| inverse lottery | choosing which queue gives credits up when another runs short |

&nbsp;

### 19.2 What a commodity NIC changes

1. **A credit cannot be taken, only given.** A posted WQE cannot be un-posted and only the queue's own lcore writes its ring. A loser gives a credit up by *not re-posting* after it reads a packet. So revocation is a **debt** the loser pays at its own pace, and a queue that receives nothing, or whose core is stalled, does not pay.
2. **A queue learns it is short only after the fact.** The signal is the backlog found at a poll: completions waiting = posted buffers already used. Backlog near the budget means the NIC was close to (or at) dropping.
3. **(Wrong as stated — corrected in §19.7.)** **Unused posted buffers cost nothing.** A buffer the NIC never fills is never touched. So there is no reason to take credits from a queue until another queue needs them: **all `C` credits stay distributed and move on demand**, as pages do in the paper. Under balanced load nothing moves and the system *is* fill-128 (§18.7).

&nbsp;

### 19.3 The mechanism

**Where §19.6 changed this section, §19.6 wins.** Per queue, written only by its lcore: `budget` (credits held; Phase 1's static `fill_budget` becomes its initial value and its entitlement). Shared, atomic, touched only on the slow path — never per packet, unlike shRing's head: per-queue `debt` (credits it has been asked to give) and one `pool` (credits given and not yet taken).

- **Pressure.** After a full burst, look at the completion `H` entries ahead (`H` = budget/2 to start): if it is already there, the backlog is ≥ `H` and the queue is short. One extra CQE read, only on full bursts. (To verify in the source: testing a CQE ahead of `cq_ci` is what `rx_queue_count` does.)
- **Ask.** A queue under pressure first takes what is in `pool`. If that is not enough it draws a loser by inverse lottery among the other queues and adds `k` to that queue's `debt`. It asks for as much as it holds (doubling): being short costs packets, holding too much costs only cache.
- **Pay.** At each burst a queue with `debt` lowers its `budget` by what it can spare, skips that many re-posts, and moves the credits to `pool` once they are really un-posted. What it can spare: everything above its entitlement, always; below its entitlement, only what its own recent backlog shows it does not need, and never below a floor (64, the vendor minimum of §18.4).
- **Guarantee.** A queue below its entitlement that is under pressure posts up to its entitlement **at once**, without waiting for the pool, and leaves the debt on the borrowers. Posted buffers then exceed `C` for a while, by at most what unresponsive borrowers hold. This is what keeps a stalled core from hurting anyone: it can sit on what it borrowed, never on what another queue is entitled to.
- **Why a lottery and not "take from the largest holder":** the largest holder may be exactly the stalled core, which never pays. A random draw repeated while the pressure lasts reaches a queue that can pay with probability that grows with every draw; a deterministic rule can pick the dead one forever. With unequal tickets the same draw gives proportional shares of the surplus.

&nbsp;

### 19.4 What it should do on the §18.10 table (predictions to test)

| test | expected | why |
|---|---|---|
| balanced | = fill-128 | no pressure, no credit moves |
| hot queue 56% | loss → near shRing's 0; total posted stays 1,024 | q0 doubles 128 → 256 → 512 → ~576 as seven queues at 6% of the load pay down to the floor (7 × 64 = 448 lent) |
| 1 ms stall, lcore 4 | loss on q3 only, **below** fill-128's 264 k | after its first stall q3 is under pressure and borrows; a stall then finds more than 128 posted. The others reclaim at once if they need to |
| stall of the *hot* queue's core (new test) | the other seven lose nothing | they are at or below entitlement; the guarantee does not wait for q0 |

Known limits, to state in the thesis: the first burst that finds a queue short is lost before the policy reacts; a lender is exposed to a burst until it reclaims; and with more buffers a stalled queue releases a longer Tx burst (§18.8).

&nbsp;

### 19.5 Steps

1. **Model first, no node** (`analysis/credit_model.py`): queues as arrival rates with poll gaps and stalls, the three tests of §18.10 with the measured rates. Fixes `H`, the floor and the ask size before any driver code, and checks the invariants (credits conserved; overshoot bounded).
2. **Driver, equal tickets:** `budget` dynamic, `debt`, `pool`, uniform inverse lottery, pay above the floor. Devarg `fill_dyn=1` next to `fill_en`; `fill_dyn=0` stays Phase 1 bit for bit. Test: the three rows of §18.10.
3. **Entitlement guarantee and reclaim.** Test: stall the hot queue's core; move the hot flow from one queue to another mid-run.
4. **Unequal tickets** (optional): a devarg for per-queue weights; show the surplus divides in proportion.

**Decided with the user (2026-10-13): the simplest version that causes the fewest problems.** `C` stays fixed at 1,024 so every comparison keeps the same total; tickets are equal (step 4 dropped unless the advisor asks for it); revocation is on demand; and the entitlement guarantee of §19.3 (post at once, overcommit) is **deferred** — the floor is the guarantee: nobody lends below 64 or below what its own recent backlog justifies. Step 3 comes back only if a test shows a lender hurt by a stalled borrower.

&nbsp;

### 19.6 The model (`analysis/credit_model.py`) and what it changed

An event model: constant arrival rates (the measured ones at 98%), and loss only while an lcore is paused — a pause of `d` µs lets `rate × d` packets into `budget` buffers. With nothing moving (`static`) it reproduces fill-128's measurements, which is the check that the model is worth reading: **0 / 13,090 / 262,702 against 0 / 13,097 / 263,916** measured (balanced / hot queue / stall).

- **The hot-queue loss of this testbed is one periodic pause on lcore 1 (inferred, not observed).** Loss against ring size in §17.7 is a straight line, ~59.5 packets fewer per extra buffer in 30 s: ~2 pauses per second of ~77 µs on the hot queue's lcore. The same pause on that lcore *alone* gives balanced privRing-64's ~1,800 per minute (§17.6) — if every lcore had it, that would be 8× more. So the hot-queue test loses because the hot flow hashes to queue 0, which lcore 1 serves. To check on a node: what runs on CPU 1 twice a second (PCM is pinned to core 0), and a hot flow on another queue.
- **The need signal is the peak backlog, not a fixed fraction of the budget.** With `H = budget/2` (§19.3) the 77-packet backlog of lcore 1 counts as pressure under balanced load and credits slosh for nothing. Instead a queue keeps the largest backlog of the last 1–2 s (`peak`) and wants `ALPHA × peak` (1.5): it asks when it holds less — doubling when the backlog filled the whole budget, since the real need is then unknown — and as a loser it pays only what it holds above `max(64, ALPHA × peak)`. Under balanced load nobody is short and nothing moves.
- **One lottery per chunk of 32 credits**, not one per ask: an ask for 128 is four draws, so it spreads over several losers as the paper's one-unit lotteries do. A debt a loser cannot pay is dropped, not kept.

Model results, 30 s at 98%, 20 seeds, total credits 1,024, floor 64, `ALPHA` 1.5:

| scenario | static (fill-128) | lottery | where the credits end up |
|---|---|---|---|
| balanced | 0 | 0 | 128 each: nothing moves |
| hot queue 56% | 13,090 | **317** (312–344) | q0 523, the others 66–74 |
| 1 ms stall on lcore 4 | 262,702 | **139,116** (130–145 k), q3 only | q3 544, q0 96, the others 64 |
| hot queue, its own core stalled | 1,332,600 | 1,185,934, **q0 only** | q0 576, the others 64 and losing nothing |
| hot queue + a *cold* core stalled | 125,535 | 19,346 | q3 515, **q0 only 125** |

- **Both targets of §18.10 at once, in the model:** the hot queue's loss falls 40× to shRing's order (its 317 is the two pauses it takes to grow 128 → 256 → 523), and a stall stays on the stalled queue, at half of fill-128's loss because the stalled queue borrows too.
- **Insensitive to the two parameters:** `ALPHA` 1.25–2 and floor 32–64 leave the hot-queue row at 312–317 and the balanced row at 0; a floor of 32 lends more to a stalled queue (stall row ~80 k) at the price of leaner lenders.
- **Equal tickets make the lottery equivalent to asking every other queue for an equal share** (`broadcast` in the model: 312 and 138,933). The draw earns its place only with unequal tickets or to disturb fewer queues per ask; it is kept because it is the advisor's direction and costs ten lines.
- **Two borrowers are served first come, not in proportion.** In the last row the stalled queue asks five times as often as the hot one (every 100 ms against every 505 ms) and takes the whole surplus; the hot queue keeps losing its 13 k. Total loss still falls 6×, but this is where proportional shares between borrowers — the paper's actual subject — would matter, and the simplest version does not have them.
- **Not in the model:** rare pauses on the other lcores (the 137 µs one of §18.7 would cost a lender at 64 some 70 packets), the Tx ring (§18.8), and the cost of measuring the backlog.

Next: step 2, the driver. To settle while writing it: how to count the completions waiting (ownership bit of a CQE ahead of `cq_ci`), and counters that show each queue's budget over a run.


&nbsp;

### 19.7 Lean budgets: the configuration aimed at shRing (2026-10-13, recommended over §19.3's debts)

**Correction to §19.2 point 3.** A posted buffer is untouched only on a queue with *no traffic*. On a queue with traffic the NIC fills WQEs in ring order, so every posted buffer is used in turn: a queue cycles through its whole budget however small its backlog is, and budget above need is working set for nothing. The data already said so — under the same balanced load privRing-64 writes less DRAM than privRing-128 (319 vs 764 MB/s mean, §17.6). So leaving all 1,024 credits posted when nobody needs them (§19.3) gives away exactly the metric where shRing leads.

**Lean policy.** Every queue holds `max(floor, ALPHA × peak)` and no more: what it does not need it un-posts by itself, at the end of a window, into the pool; a queue that is short takes from the pool. `C` = 1,024 becomes a **cap** on the posted total, not the posted total. No debts, no cross-queue requests, one shared atomic counter — simpler than §19.3. The inverse lottery is kept for the one case that needs an arbiter: the pool is empty and two queues want more.

Model, same scenarios as §19.6 (`lean` in `analysis/credit_model.py`):

| scenario | static (fill-128) | debts + lottery (§19.6) | **lean** | posted total, lean |
|---|---|---|---|---|
| balanced | 0 | 0 | 0 | **565** (q0 117, the others 64) against 1,024 |
| hot queue 56% | 13,090 | 317 | 533 | 971 |
| 1 ms stall on lcore 4 | 262,702 | 139,116 | 148,430, q3 only | 1,024 |
| hot queue, its own core stalled | 1,332,600 | 1,185,934 | 1,190,389, q0 only | 1,024 |
| hot queue + a cold core stalled | 125,535 | 19,346 (q0 starved) | 47,244 (q0 269, q3 371) | 1,024 |

Loss is the same order as with debts (the hot queue pays one more pause to grow from where it started); what changes is the first row: about half the buffers posted under balanced load.

**Where this can and cannot beat the two systems — to be claimed only as measured:**

| | shRing-8 (measured, §18.10) | lean credits (model / expectation) | rxBisect (paper; not runnable, §11) |
|---|---|---|---|
| balanced: DRAM write | 232 MB/s | **unknown** — 565 buffers posted instead of 1,024; privRing-64's 111–577 MB/s is the nearest measurement | small working set, as shRing |
| hot queue: loss | 0 | ~300–500 in 30 s, all while the queue grows | ~0 |
| stalled core: loss | 2.13 M on every queue | ~140–150 k on the stalled queue only (**~15× less**) | the NIC takes other cores' buffers for the stalled queue: expected near 0 up to the ring size |
| a stalled core hurts others | yes | no | no |
| shared state on the fast path | bitmap and head, CAS per burst | none | none (NIC side) |
| needs | RMP support | **a stock NIC** | an ASIC change, or an emulator on a dedicated core |

- **Against shRing** the case is: equal where it is good (hot queue, working set) and an order of magnitude better where it is bad (one slow core). The balanced DRAM row is the one not yet in hand.
- **Against rxBisect** the honest claim is *the same isolation property on hardware that exists*, not better numbers: software cannot hand a buffer to a queue whose core is not running, so a first stall costs what the queue had posted (§18.4). rxBisect cannot be measured here at all.
- **What decides the floor, and so the balanced row:** a budget sweep on the NIC — `fill-16`, `fill-32`, `fill-64`, `fill-96` beside `fill-128`, balanced, 98%, 60 s, DRAM write and loss per queue. The harness already runs it. The floor is the smallest budget that loses nothing on the seven quiet lcores; the sweep also says whether DRAM write keeps falling below 128 or flattens.

&nbsp;

### 19.8 Budget sweep on the NIC: six queues are safe at 16, and DRAM write vanishes below 64 (2026-10-13)

`fill-<B>` in a ring of 1,024, balanced, 98%, 60 s, one run each (fill-128 is §18.7's run). Per-queue loss is each queue's shortfall against its count in a loss-free run; it adds up to `rx_missed_errors` in every row. Tx equals Rx everywhere.

| budget | posted total | DRAM write | `rx_missed_errors` | q0 (lcore 1) | q5 (lcore 6) | the other six | model for q0 (§19.6) |
|---|---|---|---|---|---|---|---|
| 16 | 128 | **< 17 MB/s** (mean 2.8) | 7,557 | 7,222 | 336 | **0** | 7,309 |
| 32 | 256 | **< 14 MB/s** (mean 4.3) | 5,702 | 5,384 | 318 | **0** | 5,405 |
| 64 | 512 | 288 MB/s | 1,609 | 1,499 | 110 | **0** | 1,600 |
| 96 | 768 | 695 MB/s | 44 | 0 | 42 | **0** | 0 |
| 128 | 1,024 | 614 MB/s | 9 | 0 | 9 | **0** | 0 |
| shRing-8 (§18.10) | 1,024 | 232 MB/s | 0 | | | | |

(PCM labels carry a `_98_2` suffix; the 16 and 32 files span the whole run — 94 and 74 samples — and no sample exceeds 16.5 MB/s, idle being ~2.)

- **The pause on lcore 1 is confirmed, and it is not PCM.** The model fitted to the 2026-10-01 hot-queue runs (77 µs, ~2 per second, lcore 1 only) predicts queue 0's loss at three budgets it never saw, within 1–7%. The hot-queue run repeated *without* PCM loses 13,019 against 13,097 with it. CPU 1's hyperthread sibling is CPU 17, which we do not use. Cause still unknown — to trace with `perf` on CPU 1.
- **lcore 6 pauses now and then:** about eight times a minute, 40–140 µs (336 / 318 / 110 / 42 / 9 lost as the budget grows). Rare enough that a peak remembered for 1–2 s (§19.6) would forget it between two pauses.
- **Six of eight lcores never leave their queue unread for 16 µs in a minute.** Under this load they need 16 buffers, not 128.
- **DRAM write has a knee between 32 and 64 buffers per queue:** practically zero at 16 and 32, 288 MB/s at 64, 600–700 at 96 and 128. Below the knee the path writes **an order of magnitude less than shRing's 232 MB/s** — at the price of loss on the two queues whose lcores pause.
- **What this fixes in the policy (§19.7):** floor **32** (16 buys nothing in DRAM and halves the margin); the peak must be remembered for tens of seconds, not 1–2, so that queue 5 keeps what its rare pauses need; and the balanced row to beat shRing is then six queues at 32 plus queues 0 and 5 at ~1.5 × their peak — about 500 buffers posted where they are needed, instead of 1,024 spread evenly. Whether that lands under 232 MB/s depends on whether the knee is per queue or on the total; only the dynamic driver can measure it.

&nbsp;

### 19.9 The pause is the ACPI interrupt, and the driver has credits (2026-10-13)

**The pause on lcore 1 is IRQ 9, `acpi`.** `perf record -C 1 -e sched:sched_switch,irq:irq_handler_entry,workqueue:workqueue_execute_start` for 10 s with l3fwd running: IRQ 9 fires on CPU 1 **twice in a row (~220 µs apart), once per second** — the "two pauses per second" of §19.6. Nothing else on that CPU is long enough: `kworker/1:0` running `psi_avgs_work` holds it 2–8 µs about once a second, `migration/1` 1 µs every 4 s. The handler's own duration was not traced (`irq_handler_exit` was not recorded); ~77 µs each is what the loss says.

- **So the hot-queue loss of §17.7, §18.9 and §18.10 is an interrupt that happens to be routed to the lcore serving the hot queue.** It is a real disturbance, but an accidental one: with the hot flow on another queue, or IRQ 9 routed elsewhere, the 128-buffer systems would probably lose nothing in that test.
- **For the method:** route IRQ 9 away from the lcores in `node-setup.sh` (`/proc/irq/9/smp_affinity_list`; check `irqbalance`), then inject pauses on purpose with `stall_core.py` — short ones on the hot queue's lcore are the honest version of the hot-queue test. Until that is done, every row measured so far carries this interrupt on lcore 1. Not yet tried on the node.

**Step 2 is written (fork `deb358d5f`, not yet run on a NIC):** `fill_dyn=1` next to `fill_en=1,fill_budget=<E>`, optional `fill_floor=<F>` (default 32). It is the lean policy of §19.7:

- a queue starts at the floor; the rest of its entitlement `E` goes to a per-port pool, one atomic word (`priv->fill_pool`);
- **backlog** = packets the burst returned, plus — when the burst came back full — the completions still waiting, counted by a binary search on the CQE ownership bit (`mlx5_rxq_fill_pending`, ≤ 10 CQE reads, on the first full burst of a run and every 8th after);
- a new peak in the current window calls `mlx5_rxq_fill_rebudget`: target = 1.5 × the largest backlog of the last one to two 10 s windows, at least twice the budget if the backlog used every posted buffer, clamped to [floor, ring]; credits come from the pool;
- every 256 bursts the TSC is read; when a window ends the older peak is forgotten and the budget may come down. A credit returns to the pool only once its buffer has been read (`fill_held`);
- at stop each queue logs `FILL credits: budget <now>, <mean> mean, <max> max (floor F); peak backlog; grows, shrinks, denied; pool`.

No debts and no lottery yet: the pool is first come, first served. `fill_dyn=0` is Phase 1 plus one branch per burst. `harness/run_l3fwd.sh dyn-<E>[-<F>]` runs it. Invariant by construction: held credits of all queues + pool = `E` × queues, and a queue never has more posted than it holds.

&nbsp;

### 19.10 First runs of the credit path on the NIC (2026-10-13, fork `deb358d5f`: pool only, no reclaim)

`run_l3fwd.sh dyn-128` (entitlement 128, floor 32, ring 1,024, cap 1,024), 98% of line rate, IRQ 9 still on lcore 1 as in every other row. Smoke at 10% first: 0 lost, eight budgets at 32, pool 768.

| test | privRing-128 | fill-128 | shRing-8 | **dyn-128** |
|---|---|---|---|---|
| balanced, 60 s: DRAM write | 735 MB/s | 614 MB/s | 232 MB/s | **~10–20 MB/s** (mean 9.4 over the whole file, max 21.4) |
| balanced: loss | 0 | 9 | 0 | 282 (q5 220, q0 61) |
| balanced: posted total | 1,024 | 1,024 | 1,024 | ~420 |
| 1 ms stall on lcore 4, 30 s: loss | 264,061 | 263,916 | 2,131,633 | **68,334** (q3 65,471; **q0 2,695; q5 168**) |
| hot queue 56%, 30 s: loss | 13,022 | 13,097 | 0 | **747** |
| hot queue: DRAM write | 322 MB/s | 476 MB/s | 800 MB/s | 427 MB/s |

- **Balanced: the working set follows the load.** Queue 0 settled at 121 (mean 114, peak backlog 89; the model said ~117), queue 5 at 72 (peak 48), the other six never left 32. DRAM write is an order of magnitude under shRing's, at the DRAM cost of fill-32 and a twentieth of its loss (282 against 5,702). The 282 are the pauses that found queues 0 and 5 still small; shRing and fill-128 lose 0–9 here.
- **Stall: 4× less than a static 128 and 31× less than shRing.** Queue 3 doubled five times to 800 (32 + the whole pool) and each stall then lost ~200 instead of ~875. `tx_good_packets` equals `rx_good_packets`: releasing 800 packets at once did not overflow the Tx ring.
- **But the stalled queue starved two others: the isolation claim failed in this version.** With the pool at 0, queue 0 was refused 25 times and queue 5 20 times; they stayed at 32 and lost 2,695 and 168 packets they would have kept with a static 128. First come, first served is not enough — the case §19.6 flagged in the model between two borrowers also hits a queue asking for less than its own entitlement.
- **Hot queue: 747 lost against 13,097**, all while queue 0 grew, with about half of shRing's DRAM write. (That run's per-queue `FILL credits` lines were not captured.)

**Fix, fork `155a4b111`:** entitlement and reclaim. Credits up to `fill_budget` are a queue's own; above that they are borrowed. A queue the pool leaves below its entitlement draws a borrower by lottery, weighted by what each has borrowed, and leaves it a debt; the borrower pays at its next burst regardless of its own backlog and cannot re-take those credits while they are still posted (`fill_owed`). The asker retries every 256 bursts. No overcommit: the asker waits for the borrower's lcore to run, which for a 1 ms stall is at most 1 ms. Not yet run.

&nbsp;

### 19.11 With reclaim, a stalled queue no longer starves the others (2026-10-13, fork `155a4b111`)

Same stall test (1 ms every 100 ms on lcore 4, balanced, 98%, 30 s), `dyn-128`:

| | pool only (`deb358d5f`) | **with reclaim (`155a4b111`)** | fill-128 | shRing-8 |
|---|---|---|---|---|
| total loss | 68,334 | **100,084** | 263,916 | 2,131,633 |
| q3 (stalled) | 65,471, budget 800 | 99,876, budget 703 (789 max), 181 credits paid | 263,916 | ~266 k |
| q0 | **2,695**, stuck at 32, 25 refusals | **54**, budget 129, 7 reclaims | 0 | ~266 k |
| q5 | 168, stuck at 32, 20 refusals | 154, up to 72, 4 reclaims | 0 | ~266 k |
| the other five | 0 | 0 | 0 | ~266 k each |

- **Queue 0 got its credits back from the stalled queue** and lost 54 packets, what it loses with no stall at all while it grows from the floor (61 in the balanced run of §19.10). Queue 5's 154 is likewise its balanced-run figure (220): its rare, longer pauses against a lean budget, not the stall.
- **The stalled queue gave 181 credits back and so lost more** (99.9 k against 65.5 k): it now keeps what the others do not need, not what they are entitled to. Still 2.6× less than a static 128 and 21× less than shRing, on one queue.
- Tx equals Rx again: ~700 packets released at once fit the Tx ring.

The hot-queue run of §19.10 (pool-only build), from its `FILL credits` lines: queue 0 at 531 (mean 359, max 544, peak backlog 363 — the model said 523), queue 5 up to 64, the rest at 32, 224 credits never used.

**Where Phase 3 stands.** One build, `dyn-128`, at 98% of line rate against shRing-8: 10–20× less DRAM write under balanced load, about half under a hot queue, 21× less loss with a stalled core and none of it on other queues; it loses a few hundred packets where shRing loses none (282 balanced, 747 hot queue), all while queues grow from the floor. Balanced and hot-queue runs have not been repeated on the reclaim build, nothing has been repeated at all, and every row still has IRQ 9 on lcore 1 (§19.9).

&nbsp;

### 19.12 The whole table on the reclaim build, and IRQ 9 moved (2026-10-13, fork `155a4b111`)

Repeats of the balanced and hot-queue runs on the final build, then the balanced run again after `echo 0 > /proc/irq/9/smp_affinity_list`.

| run (98%) | loss | DRAM write | queue 0 | queue 5 | the other six |
|---|---|---|---|---|---|
| balanced, 60 s (first build, §19.10) | 282 | ~11 MB/s under load (max 21) | 121, peak backlog 89 | up to 96, peak 48 | 32 |
| balanced, 60 s | 282 | ~49 MB/s under load (max 80) | mean 112, max 147, peak backlog 98 | mean 55, max 96 | 32 |
| hot queue, 30 s | 704 (747 before) | 456 MB/s (427 before) | 531, mean 377, peak backlog 363 | up to 64 | 32 |
| **balanced, 60 s, IRQ 9 on CPU 0** | **173** | ~34 MB/s under load (max 105) | **32, peak backlog 13, never grew** | mean 48, max 96 | 32 |

("Under load" removes the idle samples from the whole-file means of 9.4, 36.2 and 29.6 MB/s, taking idle as 2 MB/s and 61 loaded samples.)

- **The reclaim build changes nothing when nobody is short:** no refusals, no reclaims, the same 282 packets, the hot queue within 6% of the first run.
- **Balanced DRAM write is tens of MB/s, not a fixed number:** 11, 49 and 34 MB/s in three runs — 5 to 20 times under shRing's 232, with single samples up to 105.
- **Moving IRQ 9 removes lcore 1's pause, as §19.9 said it would.** Queue 0's peak backlog falls from 89–98 to 13 and it stays at the floor; what loss remains (173) is queue 5's. `setup/node-setup.sh` now routes the ACPI IRQ to CPU 0 at every boot.
- **Consequence: the hot-queue rows of §17.7, §18.9, §18.10 and above measure that interrupt.** They have to be re-measured with IRQ 9 away, where a hot queue on a core that keeps up should cost the 128-buffer systems nothing, and replaced by pauses injected on purpose (`stall_core.py` on the hot queue's lcore).
- **Queue 5 is now the only loss under balanced load** (150–220 a minute). Its lcore pauses ~8 times a minute for 40–140 µs, cause not traced; a budget that follows 1.5 × the recent peak is caught by the longer ones. Trace CPU 6 as CPU 1 was traced before tuning the window or the headroom around it.

&nbsp;

### 19.13 Without IRQ 9 the hot queue costs a static budget nothing; lcore 6's pauses are the control NIC (2026-10-13)

IRQ 9 on CPU 0, hot queue 56%, 98%, 30 s:

| system | loss | with IRQ 9 on lcore 1 |
|---|---|---|
| fill-128 | **0** (`rx_missed_errors` 0, queue 0 complete) | 13,097 |
| dyn-128, **with `perf record -C 6` running** | 15,007: queue 0 lost 50, the rest is queue 5 | 704 |

- **Confirmed: the hot-queue loss was the ACPI interrupt.** A queue with 56% of the load on a core that is left alone loses nothing with 128 buffers. The hot-queue test as run until now does not separate the systems; what does is a pause, and from here on pauses are injected on purpose.
- **The dyn-128 run is contaminated by the tracing and is not a result.** Queue 5 saw a backlog of 697 packets — a pause of ≥ 1.4 ms on lcore 6 at that queue's 0.5 packets/µs, ten times anything it showed in six untraced runs today — while `perf` was recording on that very CPU. It took 760 credits, and the reclaim worked a second time: queue 0, refused 5 times, still reached 108 and lost 50. Not to be quoted; to be re-run untraced.
- **What the trace did show on CPU 6:** besides `migration/6` every ~4 s, six interrupts in 30 s from `mlx5_comp18@pci:0000:8a:00.0` — a completion vector of the *control* NIC, i.e. the kernel receiving our own SSH traffic on an lcore. That rate (~12 a minute) and the cost of a NET_RX softirq fit queue 5's "8 pauses a minute, 40–140 µs" (§19.8). CPU 1 showed the same device once (`mlx5_comp25`, §19.9's trace). Which lcore gets it depends on how the SSH flow hashes, so it moves between sessions.
- **`setup/node-setup.sh` now routes every movable interrupt** to the CPUs no run uses (not 1–8 nor their hyperthreads), instead of the ACPI one alone. Not yet tried on a node; per-CPU and managed interrupts refuse the write.
- **Under hot load queue 0's ordinary backlog reaches 72** (4.5 Mpps into bursts of 32), so it needs ~108 buffers, not the floor: 50 packets lost while it grew. A static 128 covers that from the start.

&nbsp;

### 19.14 Every movable interrupt off the lcores (2026-10-13, fork `155a4b111`)

177 interrupts accepted the new affinity (CPUs 0, 9–16, 25–31), the ACPI and control-NIC ones among them. Then `dyn-128` at 98%:

| run | loss | DRAM write | queue 0 | queue 5 | the other six |
|---|---|---|---|---|---|
| balanced, 60 s | 178 | ~11 MB/s under load (max 26.5) | 32, peak backlog 15 | mean 55, max 96, "peak" 48, 4 grows, 6 shrinks | 32 |
| hot queue, 30 s | 52 | – | mean 62, max 105, peak backlog 70 | mean 50, max 64 | 32 |

- **Queue 5 still pauses with the interrupts gone**, with the same signature as before (§19.12): so the control NIC's interrupts were not it, or not all of it. Cause open. Not to be traced during a measured run (§19.13).
- **Its loss is partly the policy's own doing.** A backlog that fills the budget was remembered as the budget, so the window's end set the target to 1.5 × a budget that had just failed: 32 → 64, back to 48, → 96, back to 72… and a loss at each step. Fixed in fork `ae3e52b94`: a saturated backlog is remembered as one that calls for the doubled budget, so the queue only comes down after windows with no saturation. Not yet run.
- **With clean cores a static 128 loses nothing in either test, and dyn-128 loses 52–178**: the queues that need more than the floor find out by losing. That is the price of starting lean; the steady state is what a second traffic window in the same l3fwd run will show.

&nbsp;

### 19.15 Two windows in a row: start-up against steady state (2026-10-13, fork `ae3e52b94`, interrupts off the lcores)

`rate_sweep.py --rates 98,98`: two traffic windows in one l3fwd run, so the second starts with budgets already sized. `dyn-128`, loss per window from TRex, total from the dut.

| run | window 1 | window 2 | dut total | DRAM write | what moved |
|---|---|---|---|---|---|
| balanced, 2 × 60 s | 109 | **71** | 180 | ~16 MB/s under load (max 83) | queue 5 only: mean 78, max 129, 8 grows, 3 shrinks |
| hot queue, 2 × 30 s | 6 | **~0** (−11: TRex's stray unicast) | 20 | – | queue 0: mean 75, max 111, peak backlog 74; queue 5 up to 64 |

- **Steady state is not zero, but it is small:** 71 packets in 482 M under balanced load (0.15 ppm), a handful under the hot queue. The shrink fix halved queue 5's loss per minute (178–282 before) without removing it.
- **What is left is the lean policy itself:** a queue comes down after one to two windows (10–20 s) without a large backlog, and the next large one finds it small. Queue 5's pauses (≥ 64 packets at times, cause still open) and queue 0's ordinary bursts under hot load (peak 74 against a mean budget of 75) both do that. Longer memory or more headroom trade this loss for buffers; that trade-off curve — loss against DRAM write over window, headroom and floor — is a result to measure, not a constant to guess.
- **The comparison as it stands, with clean cores:** static 128 and shRing lose 0 where dyn-128 loses tens of packets per minute; dyn-128 writes ~10–50 MB/s where they write 232 (shRing) and 600–700 (static 128).

&nbsp;

### 19.16 Stalling the hot queue's core on purpose: the test that separates the systems (2026-10-13)

Hot queue 56% (queue 0, 4.52 packets/µs), `stall_core.py --core 1 --stall-us 1000 --period-ms 100` (mean 1,001 µs, 1.00% of the CPU), 98%, 30 s, interrupts off the lcores, one run each. About 4,526 packets arrive for queue 0 during a stall, more than any system has posted, so the stalled queue loses in all three; the question is the other seven. Per-queue loss is the shortfall against the counts the fill-128 and dyn-128 runs agree on. Tx equals Rx in all three.

| system | total loss | queue 0 (stalled, hot) | each cold queue | model |
|---|---|---|---|---|
| fill-128 | 1,325,368 | 1,325,368 | **0** | (4,526 − 128) × 300 = 1.320 M |
| **dyn-128** (`ae3e52b94`) | **1,136,545** | 1,136,531, budget 800 at most | **0** (queue 5: 14) | (4,526 − 800) × 300 = 1.118 M |
| shRing-8 | 2,138,085 | 1,202,827 | **133,370 – 133,879**, 0.89% of its traffic | (1,001 − 127) × 8.038 × 300 = 2.107 M; 131.7 k per cold queue |

- **shRing spreads one core's stall over every queue; the FILL path does not, with or without credits.** Seven queues that did nothing wrong lose 133 k packets each under shRing and none under fill-128 or dyn-128. This is the head-of-line blocking of §17.9 under an imbalanced load, the regime §3 is about, and unlike the old hot-queue test it does not depend on an accidental interrupt.
- **Credits give the stalled queue 14% back** (1.14 M against 1.32 M): it took the whole pool, 800 buffers. It cannot do better than the ring: 4,526 packets do not fit in 1,024.
- **Reclaim worked under it:** queue 5 was refused three times with the pool empty, asked, and reached 64; queue 0 came down from 800 to 768. Queue 5 lost 14 packets.
- **All three models are within 2%.** Releasing 800 packets at once on a queue that already carries half the wire did not overflow the Tx ring.


&nbsp;

### 19.17 Where Phase 3 stands at the end of 2026-10-13

Last three runs, interrupts off the lcores: shRing-8 balanced (DRAM write 117 MB/s, **94 lost**, `contention` 246,519), fill-128 balanced (756 MB/s, 0 lost), and dyn-128 (`ae3e52b94`) with lcore 4 stalled 1 ms every 100 ms (**73,970** lost: queue 3 ~73.9 k at a budget of 738–800, queue 5 40, queue 0 none; queue 5 refused 8 times, reclaimed 8 times, reached 94).

Everything at 98% of line rate, 1500 B, 8 queues, ring 1,024, 1,024 credits. Ranges are the runs of today (one to three each); no row has been repeated enough to give an interval.

| | fill-128 (static) | shRing-8 | **dyn-128** |
|---|---|---|---|
| balanced: DRAM write | 614–756 MB/s | 117–232 MB/s | **11–49 MB/s** |
| balanced: loss per minute | 0–9 | 0–94 | 71–282 (71–109 once sized) |
| hot queue 56%: DRAM write | 476 MB/s | 800 MB/s | 427–456 MB/s |
| hot queue 56%, clean cores: loss in 30 s | 0 | not re-measured (0 with IRQ 9 on the hot lcore) | 6–52 at start, ~0 once sized |
| cold core stalled: loss in 30 s | 263,916, stalled queue only | 2,131,633, every queue | **73,970–100,084**, stalled queue only |
| hot core stalled: loss in 30 s | 1,325,368, stalled queue only | 2,138,085, **133 k on each cold queue** | **1,136,545**, stalled queue only |
| shared state per burst | none | bitmap + head (CAS) | none |

- **What can be claimed against shRing, on this evidence:** several times less DRAM write under balanced load (2.4× at the least favourable pair of runs, 21× at the most); a stalled core costs 21–29× fewer packets and none of them on other queues; under a stalled hot core the seven bystander queues lose nothing instead of 0.9% each. **What cannot:** lower loss under balanced load — both lose tens of packets a minute at most, shRing 0 in one run and 94 in another, dyn-128 71–282.
- **Against rxBisect** nothing was measured (§11); the claim stays the one of §19.7 — its isolation property on a stock NIC, bounded by what a queue had posted when its core stopped.
- **Against the static budget it grew from:** 15–60× less DRAM write, 2.6–3.6× less loss under a stalled cold core, for tens of packets a minute of its own.

**Open, in the order they matter for the thesis:**
1. **Repetitions.** Every number above is one to three runs. Three to five of each row, same build, interrupts routed by `node-setup.sh` from boot.
2. **The loss–DRAM trade-off curve:** window (10 s now), headroom (1.5×), floor (32) are three guesses; sweep them and plot loss per minute against DRAM write, with shRing and the static budgets as points on the same axes.
3. **Queue 5's pauses** (lcore 6, ≥ 64 packets a few times a minute, not an interrupt we could move): trace it between runs, never during one (§19.13).
4. **Two queues both wanting more than their entitlement** are still served first come (§19.6); the lottery only arbitrates entitlement. Test: hot queue plus a stalled cold core.
5. **Cost of the path:** cycles per packet against privRing and shRing (l3fwd prints `rx_cyc`), and the Rx burst with `fill_dyn=0` against Phase 1's.
6. **Carried over:** why the FILL path builds a Tx backlog at exactly 100% (§18.7); the error-recovery path, never exercised; `-12` for `-22` (§18.7).
7. `node-setup.sh`'s interrupt routing has run by hand only; check its message on the next boot.

&nbsp;

### 19.18 Repetitions: the stall rows are tight, the balanced rows are not (2026-10-13, fork `ae3e52b94`, interrupts off the lcores)

Two more runs of each of the three tests, dyn-128 and shRing-8 alternating, into `reps.csv`. With the earlier run of each that makes three per cell (shRing's first cold-stall run predates the interrupt routing; a stall of 1 ms does not care).

| test (98%) | dyn-128, three runs | shRing-8, three runs | ratio of means |
|---|---|---|---|
| cold core stalled, 30 s: loss | 73,970 / 74,857 / 76,845 — mean **75,224**, spread 3.8% | 2,131,633 / 2,129,617 / 2,127,617 — mean **2,129,622**, spread 0.2% | **28×** |
| …loss outside the stalled queue | 0 on six queues; queue 5 tens of packets | ~266 k on every queue | |
| hot core stalled, 30 s: loss | 1,136,545 / 1,133,721 / 1,134,734 — mean **1,135,000**, spread 0.2% | 2,138,085 / 2,138,217 / 2,138,727 — mean **2,138,343**, spread 0.03% | 1.9× |
| …loss on each cold queue | **0** (queue 1 complete in all three) | **133.4–133.9 k** | |
| balanced, 60 s: loss | 78 / 177, and 109 / 71 in the two-window run | 94 / 113 / 75 | — |
| balanced: DRAM write under load | ~16 / ~21 / ~111 MB/s | 117 / 153 / 300 MB/s | 2.7–7× in runs taken back to back |

- **The stall results are reproducible to a fraction of a percent** for shRing and a few percent for dyn-128, whose spread is how fast the stalled queue reaches its 800 buffers. These two rows can be quoted.
- **Balanced loss is a tie.** With the interrupts off the lcores shRing loses 75–113 packets a minute and dyn-128 71–177; the 0 that shRing showed earlier in the day (§18.10) did not come back in three runs. Whatever pauses lcore 6 hurts both.
- **Balanced DRAM write moves a lot between runs, for both systems, and together:** the third pair (111 and 300 MB/s) is two to five times the second (21 and 153), taken minutes apart. The order holds in every back-to-back pair, but the ratio is 2.7–7×, not the 20× of the best pair. It needs more runs and a look at what else changes on the node between them (PCM itself samples once a second on core 0).
- **For the summary of §19.17:** read "11–49 MB/s" as 11–111, and "0–94" for shRing's balanced loss as 0–113.

&nbsp;

### 19.19 The loss–DRAM trade-off sweep: knobs as devargs, plan for one hour on the nodes (2026-10-14)

Fork `4552a4a52` makes the two compile-time guesses of the policy devargs: `fill_window_ms` (1–600,000, default 10,000) and `fill_headroom` (percent of the peak backlog, 100–1,000, default 150); with neither set it behaves exactly as `ae3e52b94`. The l3fwd start-up line now prints window and headroom. Harness: `sudo FILL_WINDOW_MS=30000 FILL_HEADROOM=300 harness/run_l3fwd.sh dyn-128` (the floor stays `dyn-<E>-<F>`).

**Why these two knobs.** With interrupts off the lcores, dyn-128's steady-state loss is queue 5's rare pauses (§19.15: ~8 a minute, 40–140 µs, cause open), and a 140 µs pause leaves ~143 packets waiting at 1.02 per µs. A queue only holds enough if it still remembers such a pause (window) and keeps enough above it (headroom). Both make that queue hold more, which is what writes DRAM. The curve is the claim: how much DRAM the last tens of packets a minute cost.

**Runner.** `harness/sweep.sh harness/sweeps/tradeoff.txt` on the dut (own user, inside tmux, `TGEN_MAC=` set) runs the whole table unattended: per line it starts `run_l3fwd.sh` with `PCM_LABEL`, waits for the port, runs `rate_sweep.py` on the tgen over ssh on the control network, stops l3fwd with SIGINT and appends counters, `FILL credits` lines and the PCM mean to `/mydata/exp/sweep/summary.txt`.

**Protocol.** Balanced, 98%, `--rates 98,98 --duration 60` (window 1 = start-up, window 2 = steady state; the steady-state window is the point on the curve), `PCM_LABEL` per run, all rows into one `tradeoff.csv`. Per-queue `FILL credits` lines from the l3fwd log. Order, so that a short session still gives a curve and brackets the drift of §19.18:

| # | system | window | headroom | why |
|---|---|---|---|---|
| 1 | dyn-128 | 10 s | 150 | reference; also checks the new build reproduces §19.15 |
| 2 | dyn-128 | 30 s | 150 | remember a rare pause longer |
| 3 | dyn-128 | 10 s | 300 | more above the remembered pause |
| 4 | dyn-128 | 30 s | 300 | most conservative corner |
| 5 | dyn-128 | 2 s | 150 | forgetful corner |
| 6 | shring-8 | — | — | anchor on the same axes, same session |
| 7 | dyn-128 | 10 s | 150 | drift check against #1 |
| 8 | dyn-128-64 | 10 s | 150 | floor instead of headroom |
| 9 | fill-128 | — | — | static anchor |

**Expected:** loss in window 2 falls and DRAM write rises from #5 to #4; the question is whether some point reaches shRing's balanced loss (75–113 a minute, §19.18) or 0 while staying under shRing's DRAM write. If even #4 keeps losing on queue 5, its pauses outgrow what a 1,024-entry policy can remember and the fix belongs to the core, not the budget.

&nbsp;

### 19.20 The advisor's questions on the shared mempool, and what 21/10 measures for them (2026-10-16)

Reply to the progress e-mail, on "a ring of 1,024 keeping only 128 buffers posted measures like a ring of 128": **"How many cores?"** and **"How is the shared access to the mempool done?"** Neither was in the e-mail. The second is the real one: if every queue draws from one shared pool, did the design trade shRing's shared head for another shared hot spot?

**Answers, from the source:**

- **Cores:** 8 lcores (1–8), one Rx queue each, one port. 128 per queue is 1,024 posted in total — the same total as shRing-8's one shared ring of 1,024, which is what makes that comparison like for like.
- **The mempool is untouched.** It is l3fwd's stock `rte_mempool` (one per port and socket, `examples/l3fwd/main.c:906`, default ops `ring_mp_mc`), which every queue already shares under privRing (§18.1). Each lcore allocates and frees through a 256-mbuf per-lcore cache with no atomics; only when that cache runs empty or overflows does it move a bulk to or from the pool's common ring (lock-free, CAS). The cache is LIFO, so a freed buffer is the next one allocated, still warm. In l3fwd the lcore that receives a packet also frees it on Tx completion, so each cache should refill itself and the common ring should be touched rarely.
- **What the design adds that is shared is the credit counter, not buffers:** `priv->fill_pool` (one int per port, CAS in `mlx5_fill_pool_take`) and one `fill_debt` per queue for the lottery, both touched only when a budget changes. Per burst, one relaxed load of the queue's own `fill_debt`. The read and post indices of a queue are written only by its lcore (AF_XDP's single-producer FILL ring, §18.4).
- **Not measured until now:** how often the lcores actually reach the common ring.

**Instrumentation, fork `9601a7530` (l3fwd only, the driver is unchanged):**

- `MEMPOOL <pool>: common ring get=<n> put=<n> objects, cache 256 per lcore, <n> in pool` at exit: how far the ring's consumer and producer tails moved between launching the lcores and stopping them — every object that went through the common ring, in either direction, read without a `RTE_LIBRTE_MEMPOOL_DEBUG` build (which would add cookie checks to every get and put). Printed before the ports stop, so the posted buffers handed back at stop are not counted. Wraps at 2³² per direction.
- `CYCLES lcore <n>: pkts=… rx_cyc/pkt=… fwd_cyc/pkt=… tsc_mhz=…` per lcore: TSC cycles per packet in the Rx burst alone (the driver path: credits, backlog search and posting are inside it) and from the Rx burst through lookup and Tx enqueue, both over non-empty bursts only, so idle polling and the gaps between traffic windows do not count. Packets and cycles are reset together with l3fwd's own counters, so both cover the same span on each lcore.
- `harness/sweep.sh` adds both to each run's block in `summary.txt`, plus one line: packet-weighted cycles per packet and **common-ring objects per packet**.
- Checked here on `net_null` (no NIC, offload temporarily off): `get=288 put=0` — exactly the first refill of an empty cache (32 + 256) and nothing after it, ~18 Rx cycles per packet.

**Plan for 21/10, in order:**

1. `git pull` + `ninja` on the dut (fork `9601a7530`), `git pull` of `/local/repository`; check `node-setup.sh`'s interrupt routing message after boot (§19.17 item 7).
2. The trade-off sweep of §19.19 (`harness/sweeps/tradeoff.txt`), if the 2026-10-14 run did not finish or its `summary.txt` was not kept. Every run now also yields cycles and mempool lines.
3. `harness/sweeps/pathcost.txt`: privRing-1024, privRing-128, fill-128, dyn-128, shRing-8, then the same in reverse — balanced, 98%, 2 × 60 s. Answers the advisor with numbers (common-ring objects per packet for each system; ~0 is the claim) and gives cycles per packet (§19.17 item 5) and two more balanced repetitions of each system (§19.18).
4. More balanced repetitions of dyn-128 and shRing-8 back to back if time remains, for the DRAM-write spread of §19.18.

**What would change the story:** common-ring traffic of the same order as the packet count would mean the caches are not absorbing it, and the design would share a hot structure after all — at which point the per-queue FILL position no longer removes cross-core synchronisation, it only moves it. A difference between systems would point at the Tx side (buffers freed by one lcore's Tx completion while another lcore allocates), not at the credits.

&nbsp;

### 19.21 Ready for 21/10: one runner for every test, and the runbook (2026-10-16)

**What the harness does now** (all tried end to end on this WSL box against mocks of l3fwd, ssh, TRex and PCM — nothing on a node yet):

- **`harness/sweep.sh` runs stall tests too.** Per-line options in the sweep file: `rates=…`, `duration=…`, `stall=<core>:<us>:<ms>` (starts `stall_core.py` just before the traffic, stops it right after, its report goes into the summary); anything else goes to `rate_sweep.py` (`--hot-share 0.5`).
- **DRAM per traffic window.** sweep.sh stamps each `rate_sweep.py` result line with the dut's clock as it arrives; `harness/pcm_window.py` averages the PCM samples between window start and end (2 s trimmed at each end), by timestamp. This replaces `pcm_summary.sh`'s "write > 20 MB/s" rule, which drops dyn-128's whole run (it writes ~10–20 MB/s under load, §19.10), and gives the steady-state window of `--rates 98,98` its own figure.
- **Each summary block starts with a `run:` line** (system, knobs, rates, duration, stall, extra args), so the summary alone can be analysed.
- **Checks before the first run:** l3fwd built from fork `9601a7530` or later (else it prints the pull + ninja to run); numbered interrupts that fire on lcores 1–8 within 3 s (warning, with names); ACPI IRQ 9 not on CPU 0 (warning); pause frames on (warning).
- **Two bugs fixed that the 2026-10-14 sweep would have hit:** (1) l3fwd wrote its final counters into `<label>.out` at its own file offset, over the `rate_sweep.py` lines `tee` had appended — the summary lost TRex's per-window loss (the tgen CSV kept it); every writer now appends. (2) `stall_core.py` started in the background inherits SIGINT ignored, so Python never raised KeyboardInterrupt: it never stopped and never printed its stall count; it now installs its own handlers (sweep.sh also kills it after 10 s).
- **`analysis/sweep_report.py summary.txt [--csv f] [--plot out]`**: one row per window (loss, loss per minute, DRAM read/write, cycles per packet, common-ring objects per packet, stall count), and the trade-off figure — loss per minute against DRAM write (log) in the last window of every balanced run without a stall, one color and marker per system (privRing-1024, privRing-128 and shRing-8 keep their `stall_model.py` colors), dyn-128 points labelled by their knobs. Needs matplotlib (not on this box: `pip install --target <dir> matplotlib` and `PYTHONPATH=<dir>`).

**Batteries** (`harness/sweeps/`, ~70 s per 30 s run and ~155 s per 2 × 60 s run, setup included):

| file | runs | time | answers |
|---|---|---|---|
| `stall.txt` | 15 | ~18 min | n = 5 for the two stall rows of §19.18; first two-borrower measurement (§19.17 item 4) |
| `pathcost.txt` | 10 | ~26 min | the advisor's mempool question (§19.20), cycles per packet, two balanced repetitions of every system |
| `tradeoff.txt` | 9 | ~23 min | the loss–DRAM curve (§19.19; the 2026-10-14 session ran out of machine time before it started, so nothing of it exists yet) |

**The reservation is a full week from 21/10**, so the three batteries are day one, not the whole session.

**Runbook, in order.** Every command on the dut, from `/local/repository`, inside tmux, as your own user unless it says sudo.

1. Nodes: if it is a fresh instantiation, setup clones the fork's newest `fill-ring`; otherwise `sudo git -C /mydata/dpdk-research/shring-dpdk pull && sudo ninja -C /mydata/dpdk-research/shring-dpdk/build` and `sudo git -C /local/repository pull` (also on the tgen). `sudo mkdir -p /mydata/exp && sudo chown $USER /mydata/exp` on both nodes. Note both experiment MACs.
2. tgen: TRex in tmux (`cd /mydata/trex/v3.07 && sudo ./t-rex-64 -i -c 6 --no-ofed-check`); dut's ssh key in tgen's `~/.ssh/authorized_keys2` (§19.19).
3. dut, the three batteries back to back, stall first (it is the result that can be quoted):
   ```
   for b in stall pathcost tradeoff; do
     TGEN_HOST=<tgen control name> TGEN_MAC=<tgen mac> CSV=/mydata/exp/day21_$b.csv \
       harness/sweep.sh harness/sweeps/$b.txt || break
   done
   ```
   Read the warnings of the first seconds: an interrupt on an lcore or a stale build is worth stopping for.
4. Before ending the experiment: copy `/mydata/exp` from both nodes and `/mydata/dpdk-research/results` from the dut.
5. Here: `python3 analysis/sweep_report.py summary.txt --csv … --plot docs/figures/tradeoff`, then §19.22 with the numbers.

**Predictions to hold the results against** (98%, 30 s, 300 stalls; queue 3 under `--hot-share 0.5` gets 6.25% of 8.04 packets/µs = 0.50 per µs, ~503 per 1 ms stall):

| test | fill-128 | shRing-8 | dyn-128 |
|---|---|---|---|
| cold stall | ~263 k, queue 3 only (§18.8) | ~2.13 M, every queue | ~75 k, queue 3 only (§19.18) |
| hot-core stall | ~1.32 M, queue 0 only | ~2.14 M, ~133 k on each cold queue | ~1.13 M, queue 0 only |
| **two borrowers** | (503 − 128) × 300 ≈ **113 k**, queue 3 only; queue 0 0 (IRQs moved, §19.13) | ≈ (1,001 − 127) × 8.04 × 300 ≈ **2.1 M**, every queue | queue 3 doubles 32 → 512 in four stalls, then holds ~500 of the ~660 credits left after queue 0's ~108 and the others' floors: **a few thousand**, all in the first stalls; queue 0 near 0 unless the pool runs dry |

The two-borrower row is the first test where the credits should beat the static budget by more than the 2–4× of the cold stall — about 50× — because the stalled queue's need (~500) fits in what the others leave. If queue 0 loses there, first come first served is the cause (§19.6) and proportional shares stop being optional.

&nbsp;

### 19.22 Day one of the week: the three batteries (2026-10-19, fork `9601a7530`, interrupts routed by `node-setup.sh`)

New instantiation (dut `51:00.0` MAC `b8:3f:d2:13:09:5e`; tgen `51:00.1` / `ens2f1np1`, MAC `b8:3f:d2:13:07:6b`). The profile was stale again (`/local/repository` at `52a1055`, fixed by hand), the dut's `/mydata` Blockstore was not created (its `sda4` is an LVM member with no volume group; `/mydata` is a plain directory on the 56 GB root disk this time), and `node-setup.sh` routed all 177 movable interrupts off the lcores on both nodes — its first run on real nodes. `sweep.sh` ran the three batteries (34 runs, 12:35–13:36) unattended in tmux; the ssh session dropping at 12:57 did not touch it. Data: `C:\Users\filip\shumem_day21` (both nodes' `/mydata/exp`, the dut's results). Figure: `docs/figures/tradeoff.{png,pdf}`.

**Harness bugs this run found (fixed, shUMEM after `c5e88b8`):** (1) `rate_sweep.py`'s stdout is block-buffered over ssh, so both result lines of a two-window run reached the dut together at exit and `summary.txt` gives window 2's DRAM for window 1 too; window 2 is right, window 1 was recomputed from the PCM CSVs (61 s earlier, checked against privRing-1024's trace) for the table below. `print(..., flush=True)` and `PYTHONUNBUFFERED=1` now. (2) `stall_core.py`'s report was lost in most runs: `pkill -f` signals both `sudo` and Python and sudo relays it, so a second SIGINT landed inside the final `print`; it now ignores further signals once stopping. The stall itself ran (328 stalls in 32.8 s where reported). (3) `sweep.sh`'s IRQ 9 warning wanted CPU 0 alone; it now warns only when the affinity reaches lcores 1–8. Also: three tradeoff runs (13:17–13:23) received ~50–100 stray unicast frames (on queue 2) and twice the usual multicast, so TRex shows window 2 loss of −50/−25/−21; their real window-2 loss is the dut's `rx_missed_errors` minus window 1's (50 / 25 / 26), and `sweep_report.py` now plots that.

**On this node the pausing lcore is 5 (queue 4)**, not 6 as on 2026-10-13: every packet dyn-128 loses under balanced load is queue 4's, and queue 4 is the only one that grows (peak backlog 87–119). Cause open, as before.

**Stall tests** (98%, 30 s, 1 ms every 100 ms):

| test | fill-128 | shRing-8 | dyn-128 |
|---|---|---|---|
| cold core stalled (lcore 4) | 263,977 / 264,072, queue 3 only | 2,130,435, every queue; **and one run that wedged: 195,935,787 (81%)** | 107,900 / 93,545, queue 3 (+ 132 on queue 4) |
| hot core stalled (lcore 1, hot share 0.5) | 1,325,759 / 1,325,996 | 2,137,810 / 2,138,407, ~133.5 k on each cold queue | 1,137,821 / 1,140,566, cold queues 0 (queue 4: 35–49) |
| **two borrowers** (hot share 0.5 + lcore 4 stalled) | **112,843**, queue 3 only | **2,125,267**, every queue | **12,972**: queue 3 12,909, queue 4 56, queue 0 7 |

- **Hot-core stall, n = 5 over two instantiations:** dyn-128 mean 1,136,677 (1,133,721–1,140,566), shRing 2,138,249 (2,137,810–2,138,727), fill-128 ~1,325,700. Bystander queues: shRing 133 k each, dyn-128 and fill-128 none. The quotable row is unchanged.
- **Two borrowers, measured for the first time: 8.7× under the static budget and 164× under shRing.** The prediction (113 k / 2.1 M / "a few thousand") holds for fill-128 and shRing to the percent; dyn-128 lost ~3× the guess because queue 4's pauses reclaimed from the stalled queue 29 times (queue 3 paid 768 credits). The hot queue lost 7: with IRQs off lcore 1 it needed only ~108 buffers (peak backlog 54), which the pool had, so first come first served did not hurt it here.
- **Cold stall: dyn-128 lost 94–108 k today against 74–77 k on 2026-10-13**, for the same reason: queue 4 reclaimed 11 times and queue 3 paid 305–338 credits (181 then). That is the policy doing what §19.10 asked — a stalled borrower gives back what others are entitled to — and its cost lands on the stalled queue. Still 2.5–2.8× under fill-128 and ~20× under shRing, on one queue.
- **shRing wedged once.** `cold-stall_shring-8` (12:36): all eight queues received ~5.65 M packets — ~5.6 s of traffic — and then nothing for the remaining ~24 s; `rx_missed_errors` 195.9 M, `nombuf` 0, `contention` 25 k, PCM back to idle from 12:36:41. The next identical run (r2) was normal. One in eleven shRing stall runs so far (5 cold, 5 hot, 1 two-borrower). Signature of the shared ring's head never advancing again: some 64-entry block never counted as fully consumed. Candidates, unverified: the invalid acquire *store* on that path (§15.6, `mlx5_rx.c:1134`) or the `THRESHOLD % 64` assumption in its doorbell logic. Next: repeat the cold stall on shRing 10–20 times to get a rate, and read the head/bitmap code for a lost update. If it reproduces, it is a robustness result on its own: under shRing one stalled core can stop reception on every queue for good.

**Balanced, 98%, 2 × 60 s** (window 1 = start-up, window 2 = steady state; DRAM write in MB/s, window 1 recomputed; loss in packets per window; cycles per packet over non-empty bursts):

| system (runs) | DRAM write W1 | DRAM write W2 | loss W1 | loss W2 | Rx cyc/pkt | fwd cyc/pkt |
|---|---|---|---|---|---|---|
| privRing-1024 (2) | 10,059 / 10,082 | 8,938 / 8,983 | 0 / 0 | 0 / 0 | 415 / 419 | 575 / 580 |
| privRing-128 (2) | 606 / 637 | 508 / 519 | 3 / 18 | 0 / 0 | 302 / 303 | 463 / 464 |
| fill-128 (3) | 1,063 / 1,134 / 801 | 814 / 893 / 594 | 0 | 0 | 304–307 | 450–452 |
| shRing-8 (3) | 168 / 152 / 147 | **36 / 78 / 28** | 40 / 5 / 30 | **0 / 0 / 0** | **870 / 917 / 902** | 1,054 / 1,111 / 1,094 |
| dyn-128, 10 s, 150% (4) | 87 / 12 / 8 / 8 | **55 / 12 / 6 / 6** | 149 / 144 / 160 / 254 | **0 / 69 / 0 / 108** | 296–298 | 444–447 |
| dyn-128, 30 s, 150% | 11 | 3 | 95 | 50 | 297 | 446 |
| dyn-128, 10 s, 300% | 39 | 23 | 113 | 25 | 297 | 447 |
| dyn-128, 30 s, 300% | 48 | 24 | 62 | 26 | 297 | 446 |
| dyn-128, 2 s, 150% | 37 | 16 | 568 | 370 | 297 | 445 |
| dyn-128, floor 64 | 142 | 149 | 69 | 0 | 298 | 449 |

- **DRAM write falls from window 1 to window 2 in every system** (privRing-1024 −11%, fill-128 −23%, shRing −50 to −80%). Compare systems in the same window only; the drift is not understood.
- **Steady state, dyn-128 against shRing: a tie, not a win.** DRAM write 6–55 against 28–78 MB/s (dyn lower in three of four runs, medians ~9 against ~36); loss 0–108 against 0 per minute. No dyn-128 setting is below shRing on both axes in a repeatable way: `w10_h150` gave (6 MB/s, 0) once and (6, 108) the next time. Longer memory or more headroom (30 s, 300%) halve the steady loss to ~25–50 at ~3–24 MB/s; floor 64 removes it at 149 MB/s, above shRing. The 2 s window is clearly worse (370). The loss is queue 4's pauses, so the curve is the price of an lcore that pauses, which shRing absorbs by sharing.
- **The Rx path costs shRing ~3× per packet:** 870–928 cycles against ~300 for privRing-128, fill-128 and dyn-128 (and 574–600 under the hot-queue tests). The credits cost nothing measurable (dyn 296–298 against privRing-128's 302–303). privRing-1024 pays ~415, +38% over the 128-buffer systems — the price of its working set in cache misses. Not yet explained for shRing: the CAS on the shared head (`contention` 505–532 k per run) and the shared bitmap are the candidates; mean burst size per system would separate per-burst from per-packet cost.
- **The advisor's mempool question, answered with numbers (§19.20):** under balanced load every system takes exactly the initial cache fill from the common ring (get = 2,056 = 8 lcores × 257, put = 0; once 2,313, one more refill) and nothing in 964 M packets — **0 common-ring objects per packet**; the per-lcore caches absorb all of it. Under stalls dyn-128 moves 144–185 k objects (≈ 1 per 650–850 packets), because a queue that grows to ~800 posted buffers drains its 256-buffer cache into the NIC several times; shRing moves 79 k in the hot-core stall, fill-128 ~0. The common ring is touched only when budgets move, three orders of magnitude below the packet rate.

**Open after today, in order:** (1) the shRing wedge: rate and mechanism; (2) the W1→W2 DRAM drift; (3) queue 4 / lcore 5 pauses (and lcore 6 on the previous node) — tracing between runs, `isolcpus`/`nohz_full` as the structural fix (§15.5); (4) shRing's 3× Rx cycles: burst size and contention per burst; (5) the credit policy charges a stalled borrower for every bystander's pause (cold-stall drift 75 k → 100 k): consider a minimum time between reclaims from the same queue.

&nbsp;

### 19.23 The advisor's reply to §19.20 (received 2026-10-16, read 2026-10-19) — open, nothing decided

Three points, verbatim in substance:

1. **"Better to run the tests after implementing the system."** Measure the finished design, not each step. The 2026-10-19 batteries (§19.22) had already run when this was read; the rest of the reserved week should go to implementing first.
2. **"Why did you choose the inverse lottery? The AI had suggested credits."** The mechanism *is* credits (a credit = one posted buffer, §18.2; entitlement = the queue's tickets). The inverse lottery (Waldspurger & Weihl §6.2) is only the rule that picks *which borrower* gives credits back when the pool is empty and a queue is below its entitlement (§19.10). §19.6 already found that with equal tickets it behaves like asking every borrower in turn; it earns its place only with unequal tickets. It can be replaced by a deterministic rule (largest borrower first) without changing any result so far — the case to keep in mind is a borrower whose core is stalled and cannot pay (§19.3).
3. **"The global access to the mempool must not be a bottleneck: use shards, groups of cores. Maybe the RadixVM paper helps."** RadixVM (Clements, Kaashoek, Zeldovich, EuroSys 2013 — to be read in full before citing): scalable address spaces where operations on disjoint regions touch disjoint cache lines; its Refcache keeps per-core reference-count deltas and reconciles them with the shared count lazily, by epochs.

**What the data says today (§19.22):** in l3fwd's run-to-completion model the common ring of the shared mempool is touched 0 times per packet under balanced load and ≤ 1 per ~650 packets under stalls; the shared credit counter (`priv->fill_pool`) only when a budget changes. So at 8 cores, one port, Rx and Tx free on the same lcore, it is *not* a bottleneck — and a shard design can only show a benefit in a scenario where the global structure is actually hit.

**Proposal (not approved):**

- **Credits in shards, Refcache style:** each group of `k` queues (e.g. 2 × 4) has its own pool; a queue takes from its group's pool first and reaches the port-wide pool only when the group's is empty, so the common case touches a line shared by `k` cores, not 8. Same idea as the mempool's own per-lcore cache over its common ring.
- **Mempool shards:** one `rte_mempool` per (port, group of cores) in l3fwd instead of per (port, socket), each Rx queue set up with its group's pool. An mbuf always returns to its own pool (`mbuf->pool`), so a free by another group's core goes through that core's cache *of that pool* and contends only on that pool's ring. The posted working set is unchanged; each shard needs enough mbufs for its group's maximum posted budget plus caches and Tx rings.
- **Cache-line hygiene (RadixVM's disjoint-lines rule):** `fill_debt`, which other lcores CAS on reclaim, sits in `struct mlx5_rxq_data` next to `fill_polls` / `fill_full_run`, which the owner writes every burst (`mlx5_rx.h:166-175`): give the cross-core fields their own cache line.
- **A test that makes the global mempool hot**, without which the shards cannot be evaluated: Tx on a different lcore than Rx (pipeline), or a small per-lcore cache (e.g. 32 or 0), more queues; measure common-ring objects per packet and cycles per packet with one pool against shards.

**Latency in the 2026-10-19 runs** (TRex's 1000 pps latency stream, which always lands on queue 0 — §17.3; mean/max µs; lost packets do not count; stock TRex, ~10 µs resolution, §16.1):

| test | fill-128 | shRing-8 | dyn-128 |
|---|---|---|---|
| balanced, every window | 31–34 / 53–62 | 32–35 / 52–91 | 31–36 / 52–59 |
| cold stall (queue 0 not stalled) | 31–33 / 49–56 | 33 / 53–55 | 31 / 55–57 |
| hot-core stall (queue 0 is the stalled one) | 20–21 / **41–54** | 23–25 / **90–98** | 23–24 / **166–182** |
| two borrowers (queue 0 hot, not stalled) | 21 / 36 | 21 / 42 | 21 / **126** |

- **Mean latency is a tie everywhere.** Stalls are 1% of the time, and every system holds 21–36 µs on average.
- **Under a stall the maximum is the time the posted buffers hold, which is the other side of losing fewer packets.** A packet that finds a buffer during a 1 ms stall waits until the core is back; how long the queue's buffers last at its rate bounds that wait: fill-128 128 / 4.5 per µs ≈ 28 µs (+~20 base → 41–54), shRing 1,024 / 8 per µs ≈ 127 µs (→ 90–98), dyn-128 ~800 / 4.5 ≈ 178 µs (→ 166–182). Each saved packet waited instead of being dropped. Worth stating in the thesis as a trade-off, not hiding: credits buy fewer drops with a longer tail for the packets saved.
- **Two borrowers' 126 µs max on an unstalled queue is unexplained** (one packet of ~30 k). Hypothesis: queue 3, holding ~500 packets after each stall, releases them as one Tx burst onto the same 100 Gb/s wire (~60 µs of wire time) ahead of queue 0's packets. Unchecked.

&nbsp;

### 19.24 Diagnostics before the next design round (2026-10-19 evening, fork `17317a1ba`, `harness/sweeps/diag.txt`)

Plan agreed with the user: diagnose → change the code and the environment → evaluate once, on the final setup, from 21/10 (the advisor's "test after implementing the system"). These 27 runs only decide what to change. Data: `C:\Users\filip\shumem_diag`.

**1. Small packets: shRing's Rx cost caps its throughput** (balanced, 20 s per rate; TSC 2,400 MHz):

| 512 B (line rate 23.5 Mpps) | 50% | 75% | 90% | 98% | Rx / fwd cyc per pkt |
|---|---|---|---|---|---|
| privRing-128 loss | 125 | 116 | 293 | 320 | 266 / 413 |
| shRing-8 loss | 1,083 | 2,166 | **25.4 M (6.1%)** | **62.7 M (13.7%)** | 792 / 917 |
| dyn-128 loss | 255 | 0 | 0 | 356 | 254 / 386 |

- shRing received exactly 394.3 M packets in both the 90% and the 98% window: it **saturates at ~19.7 Mpps** (2.46 Mpps per lcore = 975 cycles per packet available, its forwarding path takes ~917 plus polling). Latency jumps to 98 µs there (queues full). At 256 B it delivered 22.15 Mpps of the 22.3 offered at 50% (0.75% lost) and nothing was lost at 25 and 40%.
- privRing-128 and dyn-128 take every rate offered, up to 23 Mpps, with ~400 cycles per forwarded packet: their limit (~48 Mpps by the same arithmetic) was not reached. **The 3× Rx cost of §19.22 becomes throughput**: at 512 B shRing tops out ~15% below line rate where the credits path does not.
- **shRing's small-packet drops are invisible to the dut's drop counters:** 88.2 M packets counted in `rx_phy_packets` and not in `rx_good_packets`, while `rx_missed_errors` says 14 k and `rx_out_of_buffer` / `rx_phy_discard_packets` 0. Under stalls its drops *do* show in `rx_missed_errors`. Count loss from TRex or `rx_phy_packets − rx_good_packets`, never from `rx_missed_errors` alone.
- DRAM write is idle-level (~2 MB/s) for every system at 512 and 256 B: buffers of that size stay inside DDIO.

**2. The shared mempool: the per-lcore cache does two jobs** (1500 B, 98%, 30 s; `L3FWD_MEMPOOL_CACHE`):

| cache per lcore | privRing-128 | shRing-8 | dyn-128 |
|---|---|---|---|
| 256 (§19.22): common-ring objects/pkt · Rx cyc · DRAM write | 0 · 302 · 508–519 MB/s | 0 · ~900 · 28–78 | 0 · 297 · 6–55 |
| **32** | 1.14 · 381 · **7,034 MB/s** (47 lost) | 0.62 · 966 · **4,098** (479 lost) | 0.46 · 334 · **3,094** (184 lost) |
| **0** | 2.58 · 4,029 · 41.5% lost | 2.02 · 4,586 · 48.7% lost | 2.02 · 3,950 · 40.5% lost |

- **Contention:** with no cache every get and put is a CAS on the common ring shared by 8 lcores; Rx cost goes up 10–13× and every system falls to ~4–5 Mpps. The advisor's concern is real once the cache stops absorbing.
- **Locality, the less obvious one:** with a cache of 32 the CPU cost rises only 7–26%, but **DRAM write rises 10–500×**. The common ring is FIFO: a buffer that goes back to it comes out again only after the other ~10–18 k free buffers, so the buffers in use cycle through the whole mempool (~20–36 MB, more than the 24 MB LLC) and the posted budget no longer bounds the working set. The per-lcore LIFO cache is what keeps a freed buffer the next one allocated.
- dyn-128 still touches the common ring least (0.46 objects per packet) and writes least: fewer buffers posted, fewer in flight.
- **Consequences for the shard design (§19.23):** (a) shards by group of cores cut the contention (a ring shared by `k` lcores instead of 8); (b) the level behind the per-lcore cache must reuse hot buffers first — a LIFO pool (DPDK's `stack` mempool ops) or small per-shard pools — or sharding fixes contention and keeps the DRAM blow-up; (c) the scenario that evaluates it is a small cache or a pipeline (Tx freed on another lcore), with cache 256 run-to-completion as the control.

**3. shRing's wedge reproduces.** 15 identical cold-stall runs (lcore 4, 1 ms every 100 ms, 98%, 30 s): 14 normal (2,128,9xx lost each, the §19.18 figure to 0.1%), and **r10 (19:39) wedged** exactly like 12:36's run: all eight queues stopped together after ~6.2 s (6.26 M packets each), 191.1 M lost (79%), DRAM back to idle at 19:39:17, `contention` 25.5 k, `nombuf` 0. **2 of 17 cold-stall runs today** (none in 5 hot-core, 1 two-borrower, 3 cold on 2026-10-13): roughly one 30 s cold-stall run in 8–13. It is a result, not noise: under shRing a 1 ms stall of one core can stop reception on the whole port for good. Mechanism still to find in the code (§19.22).

**Harness:** `stall_core.py` still lost its report in some runs (a second signal arrived before the handler of the first had finished); its signals now only set a flag that the loop checks.

**What changes before the evaluation (Etapa 1), from these answers:** small-packet throughput (RFC 2544 search at 256/512 B, up to line rate) joins the final batteries as a main result; the shard design must address locality, not only contention, and is evaluated with a small cache and a pipeline mode; the wedge gets a code-reading pass and, if found, a minimal reproduction.

&nbsp;

### 19.25 Etapa 1 (2026-10-19 night): what changed before the evaluation, and what waits for a decision

**Done (pushed; nothing of it run on a node yet):**

- **Environment, dut only — `setup/kernel-tuning.sh`** writes `/etc/default/grub.d/99-shumem-tuning.cfg` and runs `update-grub`: `isolcpus=domain,managed_irq,1-8 irqaffinity=0,9-15 nosmt nmi_watchdog=0 default_hugepagesz=1G hugepagesz=1G hugepages=16` (first version also had `nohz` in isolcpus, `nohz_full=1-8` and `rcu_nocbs=1-8`: Ubuntu 22.04's generic kernel 5.15.0-187 has no `CONFIG_NO_HZ_FULL`, logged "Housekeeping: nohz unsupported" and dropped the whole `isolcpus=`, so nothing was isolated — tried 2026-10-19; the periodic tick stays on the lcores unless a nohz_full kernel such as Ubuntu's lowlatency flavour is installed); reboot, then `setup/kernel-tuning.sh --check`; `--undo` removes it. This closes the three open rows of §15.5 (1 GiB pages, hyperthreading off, isolation) and should remove the lcore pauses behind dyn-128's balanced loss (§19.22). `node-setup.sh` now uses the 1 GiB pages when present (no 2 MiB pages, mount with `pagesize=1G`), loads `msr` at boot (PCM would fail after a reboot otherwise) and routes IRQs to online CPUs only; `dev-environment.sh` leaves 1 GiB pages alone; `sweep.sh` warns when lcores 1–8 are not isolated.
- **Driver, fork `100359d1b`:** `fill_debt` alone on its cache line (§19.23's RadixVM point).
- **Harness, shUMEM `2413151`:** `rate_sweep.py --search LO,HI` (binary search for the highest rate with loss ≤ `--loss-tol`, default 1 in 10⁶, RFC 2544 style; prints an `NDR` line); `--hot-queue Q` and `--hot-move S` (the hot flow on any queue, or moving to the next queue every S s — streams swapped between segments, a gap of tens of ms); p50/p99/p99.9 latency from TRex's histogram (bucket lower bounds: 10 µs wide below 100 µs, 100 µs above); new CSV columns, and a refusal to append to a CSV with the old ones. `udp_multiflow.py` chooses the single-flow source IP from the dut's RSS hash: Toeplitz over source+destination IPv4 with mlx5's default key (`mlx5_rxq.c:34`) and an 8-entry indirection table, **checked against the 2026-10-19 counters: it predicts 511 data flows on queue 3, 513 on queue 4, 512 elsewhere and 16.0.0.1 on queue 0, exactly as measured**; hot IPs per queue 0–7: 16.0.0.1, 17.0.0.8, .9, .2, .10, .1, .7, .11. `sweep.sh`: `pcm=pcie` per line (`harness/pcm_pcie.sh`, `pcm-pcie -e`; PCM_TOOL in `run_l3fwd.sh` — pcm-pcie and pcm-memory cannot share the uncore in one run), NDR lines and `rx_phy − rx_good` in the summary. Tested against a fake TRex only (the search converges to the fake's capacity to 0.1%); the moving hot queue's latency merge does not know whether TRex keeps a latency stream's counters across remove/add and handles both — check on the node.

**The shRing wedge, from the code (`mlx5_rx_burst_rmp`, `mlx5_rx.c:1711-1731`; `head` starts at `wqe_n − 1`, so a block is 64 entries ending at `head_idx`):** any lcore, after its burst, reads `head`, loads the bitmap word of the next block (`blk`, line 1717), and if all 64 bits are set it **first clears the word (line 1722) and only then CASes `head` forward (line 1724)**, ringing the doorbell with a fresh read of `head` if its CAS won. Two windows let a delayed lcore break this for good:

1. *Late clear by a loser.* Lcores A and B both see block W full. A clears it, wins the CAS, rings the doorbell; the NIC may now reuse W's 64 entries. If B is preempted between its load (1717) and its clear (1722) for longer than the NIC takes to come round the 1,024-entry ring (~127 µs at 8 packets/µs), B's clear lands after other lcores have already set bits of W's *next* lap. Those bits are lost, W can never read as full again, `head` stops, and once the NIC has used the buffers still posted every queue drops everything. B's CAS fails and counts one `contention`; nothing else notices.
2. *Stale doorbell by a winner.* A wins its CAS, reads `head` for the doorbell (1727) and is preempted before the MMIO write; C advances `head` twice more and rings with the newer value; A's write then lowers the producer index the NIC sees. Blocks the NIC was allowed to fill are no longer posted, the block after them never fills, and no lcore advances `head` again.

Both need an lcore stopped for longer than the ring lasts at a precise point of a few instructions, which is exactly what a 1 ms SCHED_FIFO stall does when it happens to land there — rarely: ~2 of 17 cold-stall runs of 300 stalls each. Neither is the invalid acquire *store* of §15.6 (that changes ordering, not the logic). Not yet proven: a patched shRing (only the CAS winner clears the word, before ringing; the doorbell written monotonically) that stops wedging over ~30 stall runs would confirm it.

**Decided by the user the same night (all four as recommended) and implemented — see 19.26.** The options as they were put:

- **(a) Mempool shards with LIFO reuse (§19.23–19.24).** Two independent knobs in l3fwd, so their effects can be told apart: `L3FWD_MEMPOOL_OPS=stack` (DPDK's lock-based `stack` or `lf_stack` mempool driver instead of the FIFO `ring_mp_mc`: a buffer returned to the shared level is the next one out, still hot) and `L3FWD_MEMPOOL_GROUPS=k` (one mempool per group of `k` lcores; each Rx queue set up with its group's pool; an mbuf always returns to its own pool through the freeing lcore's cache of that pool). Then the credits: a pool per group (`priv->fill_pool[g]`), taken from first; a group whose pool is empty takes from another group's. Evaluated with cache 256 (control), 32 and 0: single ring / single stack / shards ring / shards stack, objects per packet, cycles per packet, DRAM write.
- **(b) Stop charging a stalled borrower for bystanders' pauses (§19.22):** keep a reserve of `R` credits (e.g. 64) in the pool that only a queue below its entitlement may take; a queue growing beyond its entitlement cannot take the last `R`. A bystander's short pause is then served from the reserve instead of a reclaim from the stalled queue.
- **(c) The lottery (advisor's question):** with equal tickets, and the stalled queue being the largest borrower, a deterministic "largest borrower first" rule picks the same queue the weighted draw picks almost always; switching makes the mechanism simpler to explain and changes no measured result. Recommended; or keep it and show unequal tickets.
- **(d) shRing baseline:** report the artifact as is and the wedge with its mechanism, and/or add a `rmp_fix=1` devarg with the two fixes above, run as a second shRing baseline (the published system, and the published system without the race).

&nbsp;

### 19.26 Etapa 1 done: the code and the batteries for the final evaluation (2026-10-19 night)

**Fork (`fill-ring`), on top of `100359d1b`:**

- **`70849f04d` net/mlx5.** (1) `fill_groups=G` (1–8, default 1): `priv->fill_pools[8]`, one credit pool per run of consecutive queues (`idx·G/rxqs_n`, l3fwd's lcore order), each on its own cache line; `mlx5_rxq_fill_take()` takes from the queue's group first, then from the others in turn. Credits can migrate between groups (a freed or paid credit goes to the payer's pool), so per-group pool counts can end below zero at teardown; the total is conserved. (2) `fill_reserve=R` (default **64**; 0 = the policy measured until §19.24): in `mlx5_rxq_fill_rebudget()` the part of a raise that keeps the queue within its entitlement may empty a pool, the part beyond it must leave `R` in every pool. (3) `mlx5_rxq_fill_reclaim()` asks the queue holding the most credits beyond its entitlement (ties: the first after the asker); the xorshift lottery and `fill_rng` are gone. (4) `rmp_fix=1` (requires `rmp_en`): `mlx5_rmp_advance_serial()` — trylock on `rmp_shared.advance_lock` (new field, `mlx5_common_devx.h`); the winner, while the next block's bitmap word is all ones, clears it and moves `head` by 64; then one release store of `head`, `rte_io_wmb()`, one doorbell write. A loser counts `contention` and returns. `rmp_fix=0` leaves `mlx5_rx_burst_rmp` exactly as published. Note while reading it: `rmp_shared.head` is a `uint16_t` that the published code CASes through an `int *`; harmless on little-endian (wraps at 65,536, a multiple of the ring) but another sign of the code's state (§15.6).
- **`a8a7459be` examples/l3fwd.** `L3FWD_MEMPOOL_OPS` (mempool driver: `ring_mp_mc` default, `stack`, `lf_stack` — all three linked) and `L3FWD_MEMPOOL_GROUPS=G` (one mbuf pool per group of consecutive Rx lcores, sized as the group's share of the stock pool plus one ring; each Rx queue set up with its lcore's group pool). Every pool prints a `MEMPOOL` line; a stack pool's shared level cannot be counted (no tails) and is reported as such — its traffic equals a ring pool's under the same cache, which alone decides when the shared level is reached. **For shRing the groups change nothing:** the RMP's buffers are allocated once from queue 0's pool and every replacement comes from the consumed buffer's pool (`seg->pool`), so all of them stay in group 0's pool. Checked on `net_null` (offload switched off for the test only): 1 pool ring, 2 pools stack, 4 pools ring with cache 32 (1.0 common-ring object per packet per pool, as on the NIC).
- Built here with no new warnings (`maybe-uninitialized` on `idx` in `mlx5_rx_burst_rmp` predates us). **Nothing of it has run on a NIC.**

**Harness (shUMEM `8600b98`):** `run_l3fwd.sh` adds `shringfix-8` and `FILL_GROUPS` / `FILL_RESERVE`; `sweep.sh` adds `ops=`, `groups=` (mbuf pools and, for dyn-*, credit pools), `reserve=` per line, and refuses an l3fwd older than `a8a7459be`. The whole runner was exercised against mocks (sudo, ssh, l3fwd, TRex, stall_core): options reach the environment, `run:`, `NDR` and `stall_core:` lines reach the summary.

**The final batteries, `harness/sweeps/eval/`, run with `harness/run_eval.sh` (each into `/mydata/exp/eval_<e>.csv`), systems alternated in every battery:**

| file | runs | ~time | what |
|---|---|---|---|
| `e0_smoke` | 8 | 10 min | tuned node + every new knob once (rmp_fix under a stall, groups+stack, moving hot queue, NDR search). Read before the rest. |
| `e1_stalls` | 70 | 85 min | cold / hot-core / two borrowers × fill-128, shring-8, shringfix-8, dyn-128, **n = 5**; dyn-128 `reserve=0` on cold and two-borrower |
| `e2_balanced` | 18 | 47 min | 6 systems × 3, `98,98` × 60 s |
| `e3_ndr` | 20 | 45 min | NDR (loss ≤ 10⁻⁶, 10 s trials) at 1500 / 1024 / 512 / 256 B × 5 systems |
| `e4_moving` | 8 | 17 min | hot share 0.5 moving every 5 s, 60 s |
| `e5_mempool` | 21 | 25 min | cache 256/32/0 × ring/stack × 1/4 groups × privring-128, shring-8, dyn-128 |
| `e6_stall_len` | 12 | 15 min | cold stall 250 µs – 2 ms × fill-128, shringfix-8, dyn-128 |
| `e7_ddio` | 6 | 8 min | `pcm=pcie`, balanced, 6 systems |
| `e8_wedge` | 30 | 35 min | 15 cold stalls each, shring-8 vs shringfix-8 |

**Runbook for the reservation (from 21/10), in order:**

1. CloudLab: **Update the profile**, then instantiate. On both nodes check `git -C /local/repository log --oneline -1` (≥ `8600b98`). If the dut has no `/mydata` (19.22), `sudo mkdir -p /mydata`.
2. `sudo setup/dev-environment.sh dut` / `… tgen`, then `sudo setup/trex-setup.sh` on the tgen.
3. **dut only:** `sudo setup/kernel-tuning.sh`, `sudo reboot`, then — **CloudLab resets `/local/repository` to the profile's commit on every boot** (seen 2026-10-19: the reboot brought back `52a1055` and its old `node-setup.sh` ran) — if the profile was not updated, `sudo git -C /local/repository fetch origin && sudo git -C /local/repository checkout -B main origin/main && sudo /local/repository/setup/node-setup.sh`; then `setup/kernel-tuning.sh --check` (six OK lines: booted with the parameters, isolated 1-8, nohz_full 1-8, hyperthreading off, 16 × 1 GiB pages, msr loaded). If the node does not come back, the CloudLab console shows why; `--undo` and reboot reverts.
4. Both nodes: `sudo mkdir -p /mydata/exp && sudo chown $USER /mydata/exp`; dut key into the tgen's `~/.ssh/authorized_keys2`; note `TGEN_MAC` (`grep src_mac /etc/trex_cfg.yaml` on tgen) and `TGEN_HOST` (List View).
5. tgen, tmux: `cd /mydata/trex/v3.07 && sudo ./t-rex-64 -i -c 6 --no-ofed-check`.
6. dut, tmux, own user, from `/local/repository`: `TGEN_HOST=… TGEN_MAC=… harness/run_eval.sh e0`; send the summary for checking; then `harness/run_eval.sh all` (~4.8 h; can be split per battery across days).
7. Copy `/mydata/exp` from both nodes and `/mydata/dpdk-research/results` from the dut to `C:\Users\filip\shumem_eval` before ending.

**Predictions to hold the results against** (from §19.18–19.24 and the code changes): shringfix-8 never wedges in e8 and otherwise measures like shring-8 (the stall loss is head-of-line blocking, not the race); with isolated cores dyn-128's balanced loss falls towards 0 (the lcore pauses were the cause, §19.22); reserve 64 cuts dyn-128's cold-stall loss back towards the ~75 k of 2026-10-13 and the two-borrower loss below 13 k; NDR at 512 B: shring-8 ≈ 84% of line rate, the others ≥ 98%; e5: stack restores cache-256-like DRAM write under cache 32, groups cut cycles per packet under cache 0.

&nbsp;

### 19.27 The final evaluation, first pass (2026-10-19 21:30 – 2026-10-20 00:50, tuned dut, fork `181be3bbf`)

Same instantiation as §19.22–19.24, dut rebooted with `isolcpus=domain,managed_irq,1-8 irqaffinity=0,9-15 nosmt nmi_watchdog=0`, 16 × 1 GiB pages (no `nohz_full`: the kernel lacks it, §19.25). e0 smoke passed after one fix: `rmp_fix`'s first version tried the lock at every burst (23.6 M failed tries in 10 s, 1,490 Rx cycles per packet); `181be3bbf` checks the block read-only first (162 k, 980). Then e1, e8, e3, e5, e2, e4, e6, e7 unattended, 185 runs plus 5 from a first start outside tmux (kept: same build and node). Data: `C:\Users\filip\shumem_eval`. 98% of line rate and 1500 B unless stated; loss is TRex's (`rx_phy − rx_good` on the dut agrees).

**1. Stall table (e1, n = 5–6).**

| test | fill-128 | shring-8 | shringfix-8 | dyn-128 (reserve 64) | dyn-128 reserve 0 |
|---|---|---|---|---|---|
| cold core (lcore 4) | 264,057 (264.0–264.1 k), queue 3 only | 2,130,603, ~266 k on **every** queue | 2,130,348, the same | **84,419** (84.3–84.6 k), queue 3 only | **65,642** (65.3–65.8 k) |
| hot core (lcore 1, hot share 0.5) | 1,326,228 | 2,138,024–2,139,103 (4 runs) **+ 1 wedge (177 M)** | 2,138,990, ~133.6 k on each cold queue | **1,146,948**, cold queues 0 | – |
| two borrowers | 112,781, queue 3 only | 2,127,136, every queue | 2,127,136 | **1,541** (1,505–1,565) | 1,550 |

- **Two borrowers is now the strongest row: 73× under the static budget, ~1,380× under shRing**, n = 5 within ±2%. On 2026-10-19 afternoon it was 12,972: the difference is the isolated cores — with no lcore pauses no queue reclaims from the stalled one (§19.22 had 29 reclaims), so queue 3 keeps what it borrowed.
- **The reserve costs and, on isolated cores, buys nothing.** With no bystander pauses there are no reclaims to prevent; the reserve only caps a borrower 64 lower (736 instead of 800), so each 1 ms stall loses ~281 instead of ~219 packets (84.4 k against 65.6 k). Its intended benefit (§19.25 b) was measured only on the untuned node (cold stall 94–108 k with reclaims, against 74–77 k before they existed) and has not been measured directly with and without the reserve under pauses. **Open decision: default `fill_reserve` back to 0, or keep it as the price of robustness to pauses and show both rows.**
- shring-8 and shringfix-8 lose the same everywhere they do not wedge: the stall loss is head-of-line blocking, not the race. `contention` is ~3.5× higher with `rmp_fix` (trylock failures when two lcores see the same full block) at the same Rx cost (~970 cycles per packet).

**2. The wedge (e8 + e1): shring-8 wedged 3 times in 31 stall runs tonight (hot-stall r4 at 21:41, e8 r16 at 22:39, e8 r18 at 22:42), shringfix-8 0 times in 31.** With the afternoon's 2 in 17, the published code wedges in 5 of 48 stall runs (~10%); the chance of 0 in 31 at that rate is (1 − 0.104)³¹ ≈ 3%. Same signature each time: every queue stops at the same moment (e.g. r16: 24.4 M short on each), DRAM to idle. **The race of §19.25 is the cause, beyond reasonable doubt, and the fix removes it at ~no cost.** Also seen in a hot-core stall, so it is not specific to lcore 4.

**3. NDR, loss ≤ 10⁻⁶, 10 s trials (e3).**

| packet | privring-128 | fill-128 | dyn-128 | shring-8 | shringfix-8 |
|---|---|---|---|---|---|
| 1500 B (8.20 Mpps) | 100% | 100% | 100% | 100% | 100% |
| 1024 B (11.93 Mpps) | 100% | 100% | 100% | 100% | 100% |
| 512 B (23.32 Mpps) | 99.6% | 100% | 100% | **84.0% (19.59 Mpps)** | **84.0%** |
| 256 B (44.64 Mpps) | 100% | 100% | 100% | **none ≥ 50%** | **none ≥ 50%** |

- TRex really offered those rates (44.64 Mpps measured at 256 B), and privring-128 / fill-128 / dyn-128 forwarded them with no loss.
- **Correction to §19.24: shRing's limit is not simply CPU.** At 256 B it forwarded 44.56 Mpps of 44.64 (the "~1,000 cycles per packet" is measured over non-empty bursts and falls with load as bursts fill). What it shows instead: at 512 B a hard cap at 19.64 Mpps (identical receive count at 87.5% and 100% offered); at 256 B a **constant ~0.18% loss at every rate from 22 to 44 Mpps**, so no rate passes 10⁻⁶. In both cases the dropped packets are counted by `rx_phy_packets` and by no drop counter the dut prints (`rx_missed_errors` 0). Mechanism open (candidates: the 64-entry in-sequence return delaying the doorbell at high packet rates; an RMP-level drop the per-RQ counters do not see).

**4. Balanced (e2, n = 3, two 60 s windows; window 1 now measured correctly).**

| system | loss W1 / W2 | DRAM write W1 | DRAM write W2 | Rx cycles/pkt |
|---|---|---|---|---|
| privring-1024 | 0 / 0 | 9,664 | 8,283 | 412 |
| privring-128 | 0 / 0 | 798 | 724 | 315 |
| fill-128 | 0 / 0 | 565 | 514 | 304 |
| shring-8 | 0 / 0 | 99 | **16** (9–22) | **963** |
| shringfix-8 | 0 / 0 | 97 | 20 (14–24) | 978 |
| dyn-128 | 0 / 0 | 39 | **24** (20–30) | **303** |

- **With isolated cores dyn-128 loses nothing under balanced load** (it lost 71–282 a minute before, §19.18 and §19.22): the loss was the lcore pauses, as §19.22 said. Balanced is now a tie on loss and on DRAM write with shRing (dyn writes less in window 1, a little more in window 2), at a third of shRing's Rx cycles.
- (The few negative losses are stray unicast, §17.8.)

**5. DDIO (e7, `pcm-pcie -e`, 30 loaded seconds each).** Inbound full-line writes (ItoM) 1.92–1.93 × 10⁸/s in every system — 98% × 8.2 Mpps × 24 lines, so the counter is the NIC's packet writes — and the share that hit the LLC: **privring-1024 21.6%, privring-128 93.0%, fill-128 95.4%, shring-8 98.7%, shringfix-8 98.8%, dyn-128 99.5%.** NIC reads (PCIRdCur, the Tx side) hit ≥ 99.4% everywhere. This is the metric of the rxBisect paper's Fig. 10, measured directly; it orders the systems as DRAM write does.

**6. Mempool (e5, 30 s each, one run per cell): groups fix contention, a stack fixes locality.**

| cache · ops · groups | privring-128: loss · Rx cyc · DRAM wr | shring-8 | dyn-128 |
|---|---|---|---|
| 256 · ring · 1 (control) | 0 · 317 · 889 | 0 · 952 · 157 | 0 · 303 · 63 |
| 32 · ring · 1 | 0 · 389 · **7,186** | 0 · 1,025 · 3,947 | 0 · 344 · **3,504** |
| 32 · **stack** · 1 | 0 · 317 · **204** | 0 · 963 · 51 | 0 · 304 · **2** |
| 32 · ring · **4** | 0 · 388 · 7,224 | 0 · 1,002 · 2,288 | 0 · 342 · 3,334 |
| 32 · stack · 4 | 0 · 342 · 2,048 | 0 · 1,024 · 35 | 0 · 303 · 86 |
| 0 · ring · 4 | 0 · 936 · 7,249 | **48% lost** · 4,537 | 0 · 516 · 10,198 |
| 0 · stack · 4 | 0 · 1,434 · 487 | **56% lost** · 4,576 | **0 · 421 · 57** |

- **Contention (the advisor's concern):** with no cache and one pool every system lost 40–49% (§19.24); with 4 groups privring-128 and dyn-128 lose nothing and dyn-128's Rx cost falls from 3,950 to 516 (ring) / 421 (stack) cycles. **shRing cannot use groups:** all its buffers come from queue 0's pool (§19.26), so it still collapses.
- **Locality:** with a 32-buffer cache the FIFO ring blows DRAM write up 10–60×, and groups do not help (each group's ring is still FIFO); a LIFO stack brings it back to or below the 256-cache control (dyn-128 **2 MB/s**, privring-128 204, shRing 51).
- **dyn-128 with no per-lcore cache at all, 4 stack groups: 0 lost, 421 cycles, 57 MB/s** — the shared mempool made harmless by structure, not by the cache.
- Unexplained, one run each: privring-128 with stack·4 writes 2,048 MB/s against 204 with stack·1; privring-128 with cache 0 · ring · 4 counts 11.9 common-ring objects per packet (2.0 for dyn-128 in the same cell). Repeat before reading anything into them.

**7. Moving hot queue (e4, hot share 0.5 moving every 5 s, 60 s, n = 2).** Every system loses ≤ 19; DRAM write dyn-128 21, shRing 166, fill-128 404 MB/s. On isolated cores a hot queue at 4.5 Mpps needs only ~25–35 buffers (peak backlog 23–33; it needed ~108 with the timer and IRQ pauses, §19.13), so the credits barely move (budgets ≤ 49): **this test does not stress the policy on a clean node.**

**8. Stall length (e6, cold core, one run each).**

| stall | fill-128 | shringfix-8 | dyn-128 | dyn model: 300 × max(0, 1.004·d − 736) |
|---|---|---|---|---|
| 250 µs | 38,448 | 319,065 | **558** | 0 (+ growth) |
| 500 µs | 113,674 | 927,286 | **1,671** | 0 (+ growth) |
| 1 ms | 264,022 | 2,131,566 | 84,418 | 80.4 k |
| 2 ms | 565,010 | 4,540,365 | 385,306 | 381.6 k |

Below ~730 µs the stalled queue's need fits in what it can borrow and dyn-128 loses only while it grows (the first stalls); above, it loses what exceeds its 736 buffers, as the model says (within 1–5%). 69–570× under the others at 250 µs.

**Predictions of §19.26 against this:** shringfix never wedges ✔; dyn balanced loss → 0 ✔; NDR 512 B shRing ≈ 84% ✔ (but not for the reason given); reserve cuts the cold-stall loss ✘ (it raises it on isolated cores); two borrowers below 13 k ✔ (1.5 k); stack restores DRAM under cache 32 ✔; groups cut cycles under cache 0 ✔.

**Open:** the reserve default; shRing's small-packet drops (where, why constant 0.18%); the two unexplained mempool cells; the moving test needs pauses (stall_core following the hot queue) to exercise the policy; repetitions for e5/e6 (n = 1); figures (stall length, mempool, DDIO, NDR).

&nbsp;

---

## 20\. The paper-style evaluation (planned 2026-10-22; harness ready, nothing run yet)

The advisor approved the architecture and asked for a stronger evaluation: test what the shRing (OSDI '23) and rxBisect (OSDI '25) papers test, with the focus on latency, its tail (p99 and beyond) and loss %. The nodes are reserved for a week from 2026-10-22; the user asked for the batteries to be complete and to go beyond the papers where useful (bad results are kept and documented too; what goes into the paper is decided later). CAIDA: decide later whether to pursue access; until then a synthetic imbalance with the same statistics (20.2, f12).

### 20.1 What the papers test, and what was missing here

| experiment | paper | metrics | before 20 | battery |
|---|---|---|---|---|
| NDR against ring size, multicore and **single core** | shRing Fig. 3; rxBisect Fig. 9, 12 | throughput | multicore only (e3) | f9 |
| full load, balanced | shRing Fig. 9; rxBisect Fig. 10 | throughput, latency, **ring occupancy**, DDIO, DRAM | all but occupancy, n = 3 | f1, f2 |
| memory intensity of the NF | shRing Fig. 7/8 | throughput, latency, cycles | — | f8 |
| **processing variability** (slow target core) | shRing Fig. 11; rxBisect Fig. 14 | throughput | only the artificial 1 ms stalls | f6 |
| **traffic variability** (X% of packets, 64 B, to the target) | shRing Fig. 12; rxBisect Fig. 15 | throughput | — | f7 |
| real imbalanced trace + co-located STREAM/PageRank | rxBisect Fig. 16/17 | throughput | — | f11, f12 |
| allocator cost | rxBisect Table 3 | % cycles | yes (MEMPOOL/CYCLES) | every run, f14 |
| load turning pathological mid-run | shRing Fig. 13 | throughput per second | — | f13 |
| methodology | both | 10 runs, trimmed mean (min/max dropped), stdev < 5% | n = 1–6 | n = 10 tables, 5 curves, 3 NDR |

Not replicated, on purpose: MICA (an application, outside Rx in DPDK), netperf (Linux kernel stack), rxBisect itself (its emulator was never published, §11). As in the rxBisect paper, an idealised dynamic shRing is the better of privRing-1024 and shRing at each point, computed in the analysis. **Neither paper reports any percentile** — only mean latency; p99/p99.99 is beyond them.

**Weaknesses of the data before this section:** one latency stream of 1000 pps, always on queue 0 (a stall of lcore 4 measured an untouched queue); p99.99 resting on ~3 samples; TRex's default histogram has one significant digit (p99 "40" = 40–49 µs; 100 and 199 µs in one bucket); plain l3fwd is so light that every system forwards 1500 B at line rate, so there was no throughput result at all; few repetitions, no warm-up, a fixed order.

### 20.2 What changed (fork `c4df0acf6`, `290de897f`; shUMEM this commit)

- **Fork:** `fill_reserve` defaults to **0** (19.27: on isolated cores the reserve only costs). l3fwd: `l3fwd_work.{c,h}` — `L3FWD_WORK_ACCESSES=N` (N random 8-byte reads per packet from a 40 MiB buffer shared by all lcores: the papers' synthetic NF, FastClick WorkPackage), `L3FWD_WORK_TARGET=<lcore>:<N>` (the papers' target core), `L3FWD_WORK_PAYLOAD=1` (read the payload, one word per cache line), `L3FWD_WORK_MIB`; `L3FWD_OCCUPANCY_US=U` samples `rte_eth_rx_queue_count` (completions waiting on the CQ: the papers' ring occupancy; works for every system, the RMP path included, since each queue has its own CQ) every U µs while packets arrive. `WORK` and `OCCUPANCY` lines per lcore at exit; `CYCLES` unchanged (fwd_cyc now includes the work). Index without a division (high half of x × words). Checked on `net_null` (offload off for the test only): 50 reads ≈ 970 cycles per packet on this laptop; `rx_queue_count` is -ENOTSUP there, as expected.
- **TRex:** start the server with **`--hdrh`** (in TRex since 3.06: an HDR histogram per latency stream, 1 µs resolution, 3 significant digits, `json["hdrh"]`); `setup/trex-setup.sh` installs `hdrhistogram` for Python. `udp_multiflow.py`: per-queue mode (`queue_weights`, `queue_sizes`: one stream per Rx queue from 64 source IPs that the validated Toeplitz model puts on that queue) and per-queue latency streams (`lat_queues=all`, pg_id 10 + queue, at `lat_pps`, each the size of its queue's data). Legacy behaviour is unchanged when neither is set.
- **`rate_sweep.py`:** `--lat-queues/--lat-pps`, `--hdrh required` (stops at once if TRex was started without it), `--warmup S` (a window of S s before the measured ones, phase `warmup`), `--queue-weights/--queue-sizes`, `--schedule` (`schedule.py`: `skew:<seed>` = per-queue rates redrawn every second with max/min in 3.25–4.33, the range rxBisect's Fig. 4 measures on CAIDA 2018, log-weights following an AR(1) walk so busy queues persist; `switch:<t>:<weights>`; or a JSON file) applied with `update_streams` without stopping the traffic, keeping 98% of line rate in bandwidth; `--timeseries` (per-second tx/rx and latency max). New CSV columns (p90, p99.99, source, samples). A `.jsonl` next to the CSV keeps per window the latency of every stream (percentiles + encoded HDR histogram), the packets offered to each queue, and the series.
- **`sweep.sh`:** `work=`, `target=`, `payload=`, `mib=`, `hog=<n>` (n `stress-ng --stream` workers on CPUs 9–15, rxBisect Fig. 17); occupancy always on (`OCCUPANCY_US`, default 1000); summary gains `LAT` lines (per queue), per-queue Tx, per-lcore `CYCLES/WORK/OCCUPANCY`, the hog's metrics, DRAM per window labelled with its phase and its own length; `RESUME=1` skips runs already in the summary (`run_eval.sh` sets it). Preflight refuses an l3fwd older than `290de897f`, a tgen without `hdrh`, and `hog=` without stress-ng (`setup/dev-environment.sh` installs it).
- **Batteries** `harness/sweeps/eval2/f0`–`f15`, written by `make_batteries.py`: each round runs every cell (system × value) once in an order shuffled with a fixed seed; labels `f<b>_<cell>_<system>_n<round>`. Systems: privring-1024, privring-128, **shringfix-8 as "shRing"** (decided 2026-10-22: the race fix only strengthens the baseline), fill-128, dyn-128; shring-8 (published) only in f4. Defaults: 1500 B, 98%, 10 s warm-up, latency 10 kpps on each of the 8 queues (~1 Gb/s on top of the 98%).
- **`analysis/eval_report.py <data dir> --out <dir>`:** trimmed mean ± sd per cell, percentiles both per run (trimmed mean) and **pooled over every run's HDR histogram**, latency and probe loss by role (stalled/target queue against bystanders), DDIO from the pcm-pcie CSVs, `runs.csv`, `cells.csv`, and figures in the papers' layouts (Fig. 10 bars, latency CCDF, Fig. 14/15 curves, NDR bars, time series). Needs `matplotlib` and `hdrhistogram`.
- Everything tested end to end against mocks (fake sudo, ssh, l3fwd, TRex with HDR, stress-ng) in the session scratchpad; **nothing on a node yet.** Unverified until f0: TRex's real `hdrh` string decodes in Python; `update_streams` while traffic runs; 80 kpps of latency packets with no TRex errors; `rx_queue_count` on mlx5 for each system.

### 20.3 The batteries (run in this order with `harness/run_eval.sh all2`; ~41 h in all)

| | runs | ~time | what |
|---|---|---|---|
| f0 smoke | 14 | 20 min | everything new once; read before the rest |
| **f1 balanced** | 50 | 1.5 h | n = 10, 60 s: throughput, loss, latency per queue, occupancy, DRAM, cycles |
| **f4 stalls** | 180 | 4 h | cold / hot core / two borrowers × 6 systems, n = 10, latency of the stalled queue and of bystanders |
| **f6 processing var.** | 225 | 5 h | target lcore 4 gets ~1 Gb/s and 0–6400 reads per packet, all others 2 |
| **f7 traffic var.** | 225 | 5 h | 1–50% of the packets to queue 3 as 64 B, the rest 1500 B, work 2 |
| f9 NDR ring size | 60 | 2.3 h | multi and single core; privRing 64–1024, fill-128, dyn-32-8, dyn-64-16, dyn-128, shRing; n = 3 |
| f3 load–latency | 25 | 1.7 h | 10–100% of line rate |
| f10 NDR frame size | 75 | 3.3 h | 64–1024 B |
| f8 memory intensity | 300 | 6.7 h | 0–16 reads per packet on every lcore, payload untouched/read |
| f11 co-location | 125 | 2.8 h | 0–7 STREAM workers, work 2 |
| f12 skew | 50 | 1.5 h | CAIDA-like schedule (seed = round), with and without 4 STREAM workers |
| f13 switch | 25 | 40 min | target goes from ~1 Gb/s to its full share at 20 s with 800 reads per packet |
| f5 stall length | 175 | 3.9 h | 100 µs – 2 ms, n = 5 |
| f2 DDIO | 25 | 35 min | pcm-pcie, n = 5 |
| f14 mempool | 54 | 1.2 h | 19.27 item 6 again, n = 3 |
| f15 hot queue | 30 | 50 min | fixed and moving hot queue, no stall (control) |

**Check before f13:** its `target=4:800` assumes 800 reads per packet overload lcore 4 at a full share (~1 Mpps) but not at ~1 Gb/s (~82 kpps). f0's `pv800` run and f6 say whether; change the value in `make_batteries.py` and regenerate if not.

### 20.4 Runbook

1. CloudLab: **Update the profile**, instantiate; on both nodes `git -C /local/repository log --oneline -1` must show this commit or later (else `sudo git -C /local/repository fetch origin && sudo git -C /local/repository checkout -B main origin/main`). Dut without `/mydata`: `sudo mkdir -p /mydata`.
2. `sudo setup/dev-environment.sh dut` / `… tgen`; `sudo setup/trex-setup.sh` on the tgen. On nodes set up before this commit instead: tgen `sudo pip3 install hdrhistogram`; dut `sudo apt-get install -y stress-ng` and `sudo git -C /mydata/dpdk-research/shring-dpdk pull && sudo ninja -C /mydata/dpdk-research/shring-dpdk/build`.
3. dut: `sudo setup/kernel-tuning.sh && sudo reboot`; after the boot re-check `/local/repository` (CloudLab resets it, 19.26), rerun `sudo /local/repository/setup/node-setup.sh` if it was reset, then `setup/kernel-tuning.sh --check`.
4. Both: `sudo mkdir -p /mydata/exp && sudo chown $USER /mydata/exp`; dut key in the tgen's `~/.ssh/authorized_keys2`; note `TGEN_MAC` and `TGEN_HOST`.
5. tgen, tmux: `cd /mydata/trex/v3.07 && sudo ./t-rex-64 -i -c 6 --no-ofed-check --hdrh`.
6. dut, tmux, own user: `TGEN_HOST=… TGEN_MAC=… harness/run_eval.sh f0`; check its summary against the header of `f0_smoke.txt`; then `… harness/run_eval.sh all2`. Interrupted? Run the same command again: it resumes.
7. Every day or so, and before ending: copy `/mydata/exp` (both nodes) and `/mydata/dpdk-research/results` (dut) to `C:\Users\filip\shumem_eval2`; here: `python3 analysis/eval_report.py /mnt/c/Users/filip/shumem_eval2 --out docs/eval2`.

### 20.5 Predictions (from §17–19.27)

- f1: every system 98 Gb/s, loss 0; mean latency a tie; occupancy lowest for dyn-128 and shRing; DRAM write privring-1024 ≫ privring-128 ≈ fill-128 > shRing ≈ dyn-128; Rx cycles shRing ~3× the others.
- f4: as 19.27 (dyn-128 cold ~66 k with reserve 0, two borrowers ~1.5 k, shRing ~2.13 M on every queue); new: shRing's bystanders' latency probes lose ~0.9% while ours lose 0, and the stalled queue's p99.99 is longer the more buffers it held (fill < shRing < dyn, 19.23).
- f6/f7: shRing's delivered throughput falls once the target cannot keep up (papers: −60% / −49%), with loss on every queue; privRing, fill and dyn lose only the target's excess, throughput ≥ ~97 Gb/s. The question for dyn-128: does a target that never empties its queue take the whole pool (it is a borrower that cannot pay)?
- f8: with payload reads, privring-1024 loses throughput first (its working set exceeds the LLC); the 128-buffer systems later. f9 single core: privring-128 and fill-128 well below privring-1024; dyn-128 and shRing close to it (buffer sharing absorbs a single core's bursts).

### 20.6 Status: the run on the nodes (2026-10-22)

**Instantiation (reserved 2026-10-22 to 2026-10-29):** dut `sm110p-10s10603.wisc.cloudlab.us` (experiment MAC `b8:3f:d2:13:04:b3`), tgen `sm110p-10s10607.wisc.cloudlab.us` = `TGEN_HOST` (`ens2f0np0`, MAC `b8:3f:d2:13:08:9e` = `TGEN_MAC`), user `pirola`. Profile updated before instantiating; `/local/repository` at `763b7d1` survived the reboot; `kernel-tuning.sh --check` all OK (isolated 1-8, nosmt, 16 × 1 GiB, msr); `hdrh` imported on the tgen, stress-ng 0.13.12 on the dut, fork at `290de897f`. `git log` in `/mydata/dpdk-research/shring-dpdk` needs `sudo` (repository owned by root). The first `apt-get update` of `dev-environment.sh` is silent (`-qq`) for several minutes: normal.

**f0 (smoke), 14 runs, all as expected:**
- HDR latency works: 100,000 samples per queue in 10 s at 10 kpps, p99.99 at 1 µs resolution, no probe lost. Balanced 98%: every system p50 34–37 µs, p99 48–51, p99.99 54–57.
- Occupancy works on all six systems (shring-8 included): mean 0.1–0.2, max 3–5 under balanced load.
- DRAM write, balanced: privring-1024 10.5 GB/s, privring-128 1.0, fill-128 0.53, shringfix-8 0.13, shring-8 0.10, dyn-128 0.06.
- Work: 2 reads ≈ 19 cycles per packet; 800 reads = 12,669 cycles on lcore 4 (~16 per read). With ~92 kpps (1 Gb/s + its probe) lcore 4 saturates near ~1,800 reads → **f6 got 2,400 and 4,800 too (`3f60bec`, 275 runs)**; f13's 800 is right (half the core before the switch, ~5× overloaded after).
- tv20: 9.9 Mpps offered (20% of packets 64 B to queue 3), no loss. skew: weights changed every ~1 s, queue totals 5.5–15.8 M (2.9× over the window); queue 3's p99 72 µs against 49 balanced. hog4: DRAM write ~35 GB/s, each stress-ng worker reports ~15 GB/s; privring-1024 unaffected at work 2.
- **Single-core NDR, dyn-128: 97.19% of line rate (7.97 Mpps of 1500 B on one queue).**
- To explain later (f8 measures it): privring-1024 with `payload=1` wrote 0.2 GB/s to DRAM instead of 10.5.
- Bug found and fixed (`d8d5045`): the summary's stress-ng line did not match the real output; under `pipefail` the failed `grep` ended the sweep after `f0_hog4`. Rerunning `run_eval.sh f0` resumed with the two missing runs.

**`run_eval.sh all2` started on 2026-10-22 ~12:15** in tmux `eval` on the dut (TRex in tmux `trex` on the tgen, started with `--hdrh`). ~42 h. To follow and to resume:
```bash
grep -c '^== f' /mydata/exp/sweep/summary.txt        # runs done (dut)
tmux attach -t eval                                  # Ctrl-b d to leave
cd /local/repository && TGEN_HOST=sm110p-10s10607.wisc.cloudlab.us TGEN_MAC=b8:3f:d2:13:08:9e harness/run_eval.sh all2   # resumes
```
If the dut reboots: check `/local/repository` again (19.26) before resuming. Copy once a day from the WSL shell of the user's PC (C: is `/mnt/c`; a `C:\...` path makes scp read `c:` as a host), then analyse here with `python3 analysis/eval_report.py /mnt/c/Users/filip/shumem_eval2 --out docs/eval2`:
```bash
mkdir -p /mnt/c/Users/filip/shumem_eval2
scp -r pirola@sm110p-10s10603.wisc.cloudlab.us:/mydata/exp /mnt/c/Users/filip/shumem_eval2/dut_exp
scp -r pirola@sm110p-10s10607.wisc.cloudlab.us:/mydata/exp /mnt/c/Users/filip/shumem_eval2/tgen_exp
```
**Next:** first analysis after f1 + f4 (~6 h in); before the end also copy `/mydata/dpdk-research/results` from the dut. CAIDA: still to decide.

### 20.7 First reading: f1, f3, f4, f6–f11 (data copied 2026-10-23 while f11 ran; `C:\Users\filip\shumem_eval2`)

1,267 runs. `python3 analysis/eval_report.py /mnt/c/Users/filip/shumem_eval2 --out <dir>` (needs `hdrhistogram`, `matplotlib`). Trimmed means unless stated; 98% of line rate, 1500 B.

**Where we win, clearly and reproducibly:**
- **f1 balanced (n = 10):** loss 0 and latency a tie for all five (p99 49–50 µs, pooled p99.99 56–57 µs). DRAM write: privring-1024 9,972 MB/s, privring-128 907, fill-128 519, shRing 52, **dyn-128 32**. Rx cycles per packet: shRing 911, privring-1024 417, **dyn-128 300**. Occupancy 0.1–0.2 for all.
- **f4 stalls (n = 10, spread ≤ 0.1%):** cold core — dyn-128 0.026% lost (62.6 k) against 0.109% for privring-128/fill-128 (4.2×) and 0.883% for shRing (34×; its bystanders' probes lose 0.88%, ours 0); privring-1024 0.0004% but at 10.4 GB/s DRAM. Hot core — dyn 0.463% against 0.545% (static) and 0.888% (shRing); privring-1024 0.440%. **Two borrowers — dyn-128 lost 0 packets in all 10 runs**, static 0.047%, shRing 0.882%. **The published shring-8 collapsed (> 20% lost, 47–100%) in 5 of 10 cold and 2 of 10 hot runs; shringfix-8 in none of 30.** Stalled-queue p99.99 ≈ 1 ms for every system (the stall itself). Bystanders' p99.99 in two borrowers: dyn 123 µs and privring-1024 125 µs against 95–99 µs: the stalled queue releases its larger backlog as one Tx burst ahead of the hot queue's packets (19.23).
- **f6 processing variability (rxBisect Fig. 14):** up to 800 reads per packet nobody loses. From 1,600 on, the target lcore is overloaded: **shRing's throughput falls from 98 to 65 (2,400), 49, 33 and 25 Gb/s (6,400), its bystanders losing 29–75%** (the paper: −60%); privring, fill and dyn stay at 97.2–97.7 Gb/s with loss only on the target (0.36–0.86%) and bystanders at p99.99 48 µs.
- **f10 frame size:** shRing caps at ~20–22 Mpps at every size (14.7% of line at 64 B, 24.7% at 128, 45% at 256, 83% at 512 B).

**Where we lose or do not separate (keep, explain, improve):**
- **f6, latency of the overloaded target: dyn-128 behaves like a deep ring.** A target that never empties its queue looks like a queue in need: it borrows up to ~800 buffers (occupancy ~800), which do not reduce its loss (the excess over its service rate is lost anyway: 0.36–0.84%, as the static systems) but hold its packets for 13–35 ms (p99), against 2.6–6.7 ms for privring-128/fill-128 and 18–49 ms for privring-1024. Credits help transient need (stalls, bursts), not sustained overload. **Improvement to propose:** treat a backlog that does not drain within a window as overload, not burst, and stop lending beyond the entitlement to it (a sojourn-time target, like CoDel).
- **f10, 64 B: dyn-128 reaches only 59% of line rate (~88 Mpps)** where fill-128 and privring-128 forward all 148.8 Mpps. Not CPU (171 cycles per packet, ~8× headroom), not ring starvation (`rx_missed_errors` 34): 267 M packets in `rx_phy_packets` and in no drop counter DPDK shows — the same uncounted drop as shRing's small packets (19.27). dyn's budgets settle at ~75 (≈ 4 µs at 18.6 Mpps per queue) against fill's 128 (~7 µs). **Hypothesis:** the NIC needs a minimum of WQEs posted ahead in time (its descriptor fetch latency × packet rate); below it, it drops inside the NIC. Test: 64 B NDR for privring-64, fill-64/96, dyn-128 with floors 64/96/128, and the kernel's `ethtool -S` drop counters before/after (the bifurcated port keeps them).
- **f7 traffic variability (rxBisect Fig. 15):** no system collapses here, shRing included — every system starts losing at 30% of the packets (~0.01–0.02%), 2.3–3.3% at 40%, 12–14% at 50%, all of it on the target, bystanders at 0. Delivered bandwidth falls only ~1.2% at 50% (97.1 → 95.9 Gb/s for every system): the lost packets are 64 B. (The first table said 85 Gb/s: `rx_gbps_l1` multiplies received packets by the mean frame; `eval_report.py` now counts each queue at its own size.) Differs from both papers (shRing −49% / −54%); to explain.
- **f8 memory intensity (0–16 reads) and f11 co-location (0–7 STREAM):** every system 98 Gb/s, loss 0, latency a tie. l3fwd + 16 reads stays far from CPU saturation (~1 Mpps per core), so the working set never costs throughput; the papers' NFs (NAT/LB, 10 M flows) are much heavier. Needs more work per packet (32–128 reads, with STREAM) to reach the regime of their Fig. 8/17. Side finding: **reading the payload removes most DRAM write for every system** (privring-1024 10.3 GB/s → 0.24; others to ~2 MB/s) — once the core reads a line the NIC wrote, it no longer leaks from the DDIO ways; the working-set penalty measured with header-only l3fwd is largest when nobody reads the payload.
- **f9 NDR 1500 B:** ~100% for every system and ring size, multi and single core: on isolated cores a single queue forwards 8.2 Mpps of 1500 B even with 64 buffers. Burst absorption matters only when a core pauses (f4/f5), which is the honest framing of the paper's Fig. 3 on this node. dyn single core 99.7% (tiny loss at 100% while growing).
- **f3 load–latency:** a tie at every load 10–100% (dyn's p99.99 at 100%: 71 against 56–57 µs).

**Proposed next (not approved):** (a) policy change for sustained overload, then rerun f6 (and f13); (b) f16 64 B diagnosis battery with `ethtool -S` deltas, then a rate-aware floor if the hypothesis holds; (c) f17 heavier memory intensity / co-location (32–128 reads, payload, with STREAM). **Do not `git pull` on the dut while `all2` runs:** bash reads `sweep.sh` as it goes.

### 20.8 rxBisect by projection from its paper (2026-10-23)

Decided with the user: while the rxBisect code is not available (ask Pismenny again), its published numbers serve as a projected reference — **always labelled "rxBisect (estimated from [Pismenny '25])", never as a measurement**. `analysis/rxbisect_reference.py <cells.csv>` holds the paper's numbers (Fig. 9, 10, 12, 14, 15, 17, Tables 2–3 and the text's figures; read off rendered figures, ±2 Gb/s) and projects each onto this node through a baseline both papers measure: `rxBisect_est = ours[anchor] × paper[rxBisect] / paper[anchor]`, with shRing and small privRing as anchors, an anchor used only when its normalised value here is within 10% of the paper's (same regime), and estimates above line rate rejected. The x axis of Fig. 14 is mapped by where shRing starts to fall (paper ~112 accesses, f6 ~1,700). Emulation costs rxBisect up to 12% throughput and 94% latency (its Table 2), so the estimates are lower bounds for hardware rxBisect: a margin smaller than that is not a win.

First projection (f1, f6, f7, f9 of 20.7):
- **Fig. 14 / f6:** through small privRing, rxBisect ≈ 0.992–0.996 of its unloaded throughput at 1.4–3.8 knees; dyn-128 0.992–0.996. **Parity.** The shRing anchor transfers poorly (its collapse depth depends on the x mapping: estimates 0.81–0.88).
- **Fig. 15 / f7:** through small privRing, rxBisect 0.946–0.963 from 30% on; dyn-128 0.987–1.000 — but the paper attributes rxBisect's drop to its emulator core, so hardware rxBisect would sit near small privRing (~1.0): **parity, not a win.** The shRing anchor is off-regime (shRing does not collapse here).
- **Fig. 10 / f1, memory:** paper rxBisect / shRing memory bandwidth 1.73–1.92 (emulated); here dyn-128 / shRing DRAM write 0.61. **Cycles:** paper rxBisect ≈ 0.90 × shRing (Fig. 18, low load); here dyn-128 0.41 × shRing (fwd cycles, full load). Favourable, but different metrics and emulation inflate rxBisect's side: supporting evidence only.
- **Fig. 9 / f9:** another regime (single-core small privRing 40% there, ~100% here); nothing transfers.

### 20.9 The whole evaluation: final analysis (all 16 batteries, 1,688 runs, finished 2026-10-23)

Data: `C:\Users\filip\shumem_eval2` (`dut_exp`, `tgen_exp`, `dut_results`). Tables, figures and `cells.csv` in **`docs/eval2/`** (`tables.txt`, `rxbisect_projection.txt`); regenerate with `python3 analysis/eval_report.py /mnt/c/Users/filip/shumem_eval2 --out docs/eval2`. Integrity: every battery complete (f0 14/14 … f15 30/30), no run retried, no warning; HDR latency in every window (the one exception is a published shring-8 run that wedged at 100% loss: no packet, no sample); `stall_core` made 439 stalls of the requested length in all 355 stall runs. Two report bugs fixed on the way: delivered Gb/s with mixed frame sizes (f7, 20.7) and the DDIO reader dropping privring-1024's Hit rows (it hits ~20%, below the loaded-second threshold).

Systems: privring-1024 (default), privring-128 (small privRing), shRing = shringfix-8 (published shring-8 only in f4), fill-128 (static budget), **dyn-128 (ours)**. 98% of line rate, 1500 B, trimmed means.

**1. Cost of the working set under balanced load (f1, f2, f8, f12, f15) — our clearest win.**

| | privring-1024 | privring-128 | fill-128 | shRing | **dyn-128** |
|---|---|---|---|---|---|
| DRAM write, f1 (MB/s) | 9,972 | 907 | 519 | 52 | **32** |
| DDIO hit, f2 (%) | 19.5 | 91.9 | 95.4 | 99.0 | **99.6** |
| Rx cycles/pkt, f1 | 417 | 313 | 305 | 911 | **300** |
| fwd cycles/pkt with 16 reads/pkt, f8 | 1,024 | 913 | — | 1,532 | **878** |
| DRAM write with 2 / 16 reads/pkt, f8 (MB/s) | 11,163 / 11,669 | 1,538 / 2,176 | 997 / 1,675 | 769 / 1,656 | **437 / 800** |
| posted buffers (mean) | 8,192 | 1,024 | 1,024 | 1,024 | **256** |

Loss 0 and latency a tie everywhere here (p99 49–51 µs, pooled p99.99 56–58 µs). With work the NF's own reads evict packet buffers, and dyn keeps about half of shRing's DRAM write at every work level. **Cycles per packet are the throughput we would get at saturation**: at 16 reads, 2,400 MHz / cycles = 2.73 Mpps per core for dyn, 2.34 (privring-1024, −14%), 1.57 (shRing, −43%). Gb/s does not show it because l3fwd at ~1 Mpps per core has headroom (f8/f11 all 98 Gb/s, loss 0, even with 7 STREAM workers writing ~36 GB/s). A heavier NF (f17) is what turns this into a throughput result like the papers' +12–37%. Payload reads remove most DRAM write for every system (privring-1024 10.3 → 0.24 GB/s at 0 reads): the leak is lines the NIC wrote and nobody touched.

**2. Isolation under core stalls (f4, f5) — our second clear win.**

| loss % | privring-1024 | privring-128 / fill-128 | shRing | **dyn-128** |
|---|---|---|---|---|
| cold 1 ms (f4) | 0.0004 | 0.109 | 0.883 (bystanders 0.88) | **0.026** |
| hot core 1 ms (f4) | 0.440 | 0.545 | 0.888 | **0.463** |
| two borrowers (f4) | 0 | 0.047 | 0.882 | **0 (10/10 runs)** |
| stall 100 / 250 / 500 / 750 µs (f5) | 0 / 0 / 0 / 0 | 0 / 0.016 / 0.047 / 0.078 | 0 / 0.13 / 0.38 / 0.63 | **0 / 0 / 0 / 0** |
| stall 1 / 1.5 / 2 ms (f5) | 0.0004 / 0.061 / 0.124 | 0.110 / 0.172 / 0.234 | 0.88 / 1.38 / 1.88 | **0.026 / 0.088 / 0.151** |

- dyn-128 loses **nothing up to 750 µs stalls** with 256 posted buffers in steady state; privring-1024 does the same with 8,192. Above ~750 µs dyn loses what exceeds the ~830 buffers a queue can hold, ~1.2× privring-1024's loss at 1.5–2 ms, ~⅔ of the static budget's, 6–12× under shRing.
- Our bystanders lose 0 in every stall test; shRing's lose 0.13–1.88%. n = 10 (f4), 5 (f5); spread ≤ 0.1%.
- **Published shring-8 collapsed (47–100% lost) in 5/10 cold and 2/10 hot-core runs; shringfix-8 in 0/30.**
- Latency: the stalled queue's p99.99 ≈ the stall length for every system (packets wait out the stall). Bystanders' p99.99: equal, except two borrowers (dyn 123, privring-1024 125 against 95–99 µs: the stalled queue's backlog leaves as one Tx burst).

**3. A slow or overloaded core (f6, f13) — win on isolation, loss on the overloaded queue's latency.**

- shRing collapses: f6 98 → 65 / 49 / 33 / 25 Gb/s at 2,400–6,400 reads per packet on the target, bystanders losing 29–75%; f13 (target goes from ~1 Gb/s to its full share at 20 s, 800 reads) 58 Gb/s, 41% lost, delivered/offered falling from 100% to 18% in one second (time series). All others keep 97.2–97.7 Gb/s (f6) and lose only the target's excess (f13: 5.1–5.2% for all, bystanders at 0 and p99.99 50 µs).
- **dyn-128's weakness:** an overloaded queue borrows up to ~800 buffers (its occupancy ~800 in f6, ~310 in f13), which do not cut its loss (same as the static systems) but hold its packets: target p99 13–35 ms (f6) and 4.6 ms (f13), against 2.6–6.7 ms and 0.87 ms for privring-128/fill-128, and 18–49 ms / 6.2 ms for privring-1024.

**4. No separation (kept as evidence, explained):**
- f3 load 10–100%: a tie at every load (dyn's p99.99 71 against 56–57 µs at exactly 100%).
- f7 traffic variability (64 B to the target): no collapse for anyone, shRing included; loss from 30% of the packets, ~1.2% of bandwidth at 50%, bystanders 0. Differs from both papers; open.
- f9 NDR 1500 B: ~100% for every system and ring size, one core or eight (fill/dyn single core 99.65%): isolated cores do not pause, so ring depth only matters with stalls (f4/f5).
- f11 co-location and f12 time-varying skew (with and without STREAM): every system 98–99 Gb/s, loss 0, p99 76 µs under skew for all; DRAM write follows f1's order.
- f15 hot queue fixed or moving: loss 0 for all; DRAM write dyn 18–29 vs shRing 68–97 MB/s.
- f14 mempool: confirms 19.27 with n = 3 — without a per-lcore cache shRing collapses (50% lost) while dyn loses 0 (525 cycles with 4 ring pools, 430 with 4 stack pools, DRAM 28 MB/s); cache 32 + stack: dyn 2 MB/s.

**5. 64 B: dyn-128's other weakness (f10).** NDR (Mpps): privring-1024 / privring-128 / fill-128 forward line rate at every size (148.8 Mpps at 64 B); **dyn-128 88.4 Mpps (59%) at 64 B**, line rate from 128 B on; shRing caps at 19.5–21.8 Mpps at every size (15% at 64 B). dyn's 64 B drops are uncounted NIC drops (20.7), not CPU (171 cycles/pkt) nor ring starvation; hypothesis: too few WQEs posted ahead in time (budgets ~75 ≈ 4 µs at 18.6 Mpps per queue).

**6. Against rxBisect (projection, 20.8, `docs/eval2/rxbisect_projection.txt`):** parity on its central figures (Fig. 14: estimated rxBisect 0.992–0.996, dyn 0.992–0.996 of unloaded throughput; Fig. 15: estimated 0.946–0.963 because of its emulator, dyn 0.987–1.000, hardware rxBisect likely ~1.0). Favourable but caveated on cost: memory bandwidth vs shRing 0.61× (paper's emulated rxBisect 1.73–1.92×), DDIO hit 1.006× shRing (rxBisect 0.82×, emulated), cycles 0.41× shRing (rxBisect ~0.90×, low load).

**What the evaluation says the paper can claim:** (1) the I/O working set of shRing or better (lowest DRAM write, highest DDIO hit, 4× fewer posted buffers than the static 128) at privRing's per-packet cost (⅓ of shRing's cycles); (2) isolation: a stalled or slow core costs only its own queue, with no loss up to 750 µs stalls, where shRing collapses or loses on every queue; (3) rxBisect's variability results reproduced on an unmodified NIC (by projection). **What must be fixed or framed:** latency of a persistently overloaded queue (credits should not feed sustained overload), and 64 B packet rates above ~88 Mpps.

**Decision pending with the user** (after this analysis, nothing started): (a) overload policy — stop lending beyond the entitlement to a queue whose backlog never drains — then rerun f6/f13; (b) f16 64 B diagnosis (`ethtool -S` deltas; privring-64, fill-64/96, dyn floors 64/96/128); (c) f17 heavier NF (32–128 reads, payload, STREAM) to turn the cycles advantage into throughput; (d) e-mail to Pismenny for the rxBisect code. Nodes still up (reserved to 2026-10-29).

### 20.10 What the paper can say about rxBisect without its code (2026-10-23)

The user expects no answer from Pismenny before the deadline. Claims about rxBisect therefore come from three sources only, each labelled as such:

1. **By construction (cite its paper):** rxBisect needs an ASIC change (Ax/Bx rings; the NIC fetching buffers from any core's Ax ring) and is evaluated only through a software emulator on a dedicated core; ours runs on an unmodified ConnectX-6 Dx. rxBisect keeps 128 buffers posted per Ax ring (1,024) at all times; ours posts 256 under balanced load and grows only where needed. Our policy is software (devargs), rxBisect's is in hardware.
2. **By bound, through our measured baselines:** (a) a stalled rxBisect queue holds at most its Bx ring (1 Ki), so privring-1024 (1,024 dedicated per queue) loses no more than rxBisect would: its loss is a lower bound on rxBisect's. f5: dyn-128 = that bound (0 lost) up to 750 µs, ≤ 1.2× it up to 2 ms, with 1/32 of the posted buffers. (b) fill-128 posts what rxBisect's Ax rings post (128 per core): dyn-128 writes 16× less DRAM (32 vs 519 MB/s) and hits DDIO more (99.6 vs 95.4%) — a proxy, say so. (c) An overloaded rxBisect queue can fill its 1 Ki Bx ring from everyone's buffers; privring-1024 is the measured instance of that depth (f6 p99 18–49 ms). dyn-128 has the same weakness today; **with the overload policy (20.9 a) it would bound that latency, a property rxBisect's design lacks — the one axis where "better than rxBisect" holds without its code.**
3. **By projection (20.8), labelled "rxBisect (estimated from [Pismenny '25])":** parity on Fig. 14/15; cost ratios favourable but different metrics and emulated: supporting only.

Not claimable: measured throughput or latency wins over rxBisect; rxBisect numbers beside ours without the label; margins inside its emulation error (12% throughput, 94% latency). Framing: *rxBisect shows the principle with an ASIC change; we reach it in software on today's NICs, matching its isolation and working set, and add overload control its hardware design lacks.* This makes (a) the overload policy necessary, not optional, and the 64 B fix (b) advisable before submission.

### 20.11 Round 2 (2026-10-23 night): 64 B, a heavier NF, the overload policy

Decided with the user: test (1) a heavier NF and (2) the 64 B drops with new batteries only, and (4) write the overload policy now and run it overnight. (3), a larger credit cap for long stalls, was **rejected**: more buffers than shRing, even if used only during a stall, is the argument shRing would turn against us.

- **Fork `7cbfa5102`, `fill_overload_ms=<ms>` (default 0 = off, so every earlier result is reproducible):** the datapath marks a burst that comes back short (`fill_drained`, one byte, in the branch that already resets `fill_full_run`); at each clock read (every 256 bursts, `mlx5_rxq_fill_tick`) `mlx5_rxq_fill_overload()` finds a queue that has not drained for `fill_overload_ms` overloaded: `mlx5_rxq_fill_rebudget()` then targets its floor (32: one burst, enough to keep its core busy) and it borrows nothing; credits return to the pool as their buffers are read. The first short burst ends it and clears the remembered peaks, so it grows again from its current need. Stalls are untouched: a stalled core drains within ~1 ms of running again, far under 50 ms. The FILL credits line counts `overloads`. Built here, no new warning; not yet run on the NIC.
- **Harness (shUMEM, this commit):** `run_l3fwd.sh` FILL_OVERLOAD_MS; `sweep.sh` `overload=<ms>`, and the kernel's `ethtool -S` counters of the experiment NIC before/after the traffic with non-zero drop/discard/error deltas as an `ethtool:` summary line (the NIC's own view of where packets die); `run_eval.sh` KEEP_GOING=1 goes on to the next battery when one fails. `eval_report.py` knows f16–f18 (`dynov-128` = dyn-128 with `overload=50`; overloads and ethtool per run).
- **Batteries:** `s1` smoke (4 runs, ~6 min: pv2400 with the policy must show overloads > 0 and budget 32 on queue 3; a cold stall with the policy 0 overloads and ~63 k lost; a 64 B run with an `ethtool:` line; mi64 with WORK lines). `f16` 64 B (66 runs, ~1.8 h): NDR and a fixed 100% window for privring-64/128, fill-32/64/96/128, dyn-128 with floors 32/64/96/128 and dynov-128, n = 3. `f18` overload policy (117 runs, ~2.8 h): dyn-128, dynov-128, fill-128 × f6's slow target at 800–6400 reads, f13's switch, f4's three stalls, f5 at 500 µs / 2 ms, balanced, hot queue, n = 3. `f17` heavy NF (180 runs, ~4 h): 32–128 reads per packet, payload untouched/read, 5 systems, n = 3.
- **Runbook:** dut — `sudo git -C /mydata/dpdk-research/shring-dpdk pull && sudo ninja -C /mydata/dpdk-research/shring-dpdk/build` and `sudo git -C /local/repository pull`; then in tmux `TGEN_HOST=sm110p-10s10607.wisc.cloudlab.us TGEN_MAC=b8:3f:d2:13:08:9e harness/run_eval.sh s1`, check the summary, then `KEEP_GOING=1 TGEN_HOST=… TGEN_MAC=… harness/run_eval.sh f16 f18 f17` (~9 h).
- **Predictions:** f18 — dynov-128's slow-target p99 falls from 13–35 ms to below fill-128's 2.6–6.7 ms (it keeps 32, fill keeps 128), same loss; stalls, balanced and hot queue identical to dyn-128. f16 — if the NIC needs a minimum posted per queue at 18.6 Mpps, fill-32/64 and privring-64 fall short like dyn-128 and the `ethtool:` line names the counter (e.g. `rx_discards_phy`); a floor near 96–128 restores line rate. f17 — delivered Gb/s orders as f8's cycles: dyn ≥ privring-128/fill > privring-1024 ≫ shRing once the cores saturate (~60–100 reads).
- **s1 on the NIC (2026-10-24 01:20):** the policy fired (queue 3: 2 overloads, budget 32 at the end, p50 880 µs) with the same loss (0.363%), but p99 stayed 13 ms: checked only at the clock read every 256 bursts (~130 ms apart at ~0.5 ms per burst on that core), it took ~260 ms to see the overload, long enough for the queue to grow to 800 again after the gap between the warm-up and the measured window. **Fork `93066965d`** also checks every 16 full bursts in a row (one TSC read, paid only by a queue that is behind), and the batteries use `overload=20`. Cold stall with the policy: 0 overloads, 41.8 k lost in 20 s (= f4's 62.6 k per 30 s) ✓. **64 B: `ethtool` names the drop: `rx_prio0_buf_discard` = 6,818,973 against TRex's 6,824,937** — the NIC's internal receive buffer overflows while it waits for posted descriptors, which supports the too-few-posted hypothesis; dyn-128's queue 3 budget was 53 on average (max 93) at 18.6 Mpps. `s2` (2 runs) checks the fix before f16 f18 f17.


### 20.12 Round 2 results (night of 2026-10-23/24: s2, f16, f18, f17; 365 runs, all complete)

Figures and tables regenerated for both rounds in `docs/eval2/` (f16, f17_*, f18, f18_series new).

**Overload policy (s2, f18; fork `93066965d`, `overload=20`) — works, no false positive.** It fired 2× in every slow-target run (warm-up and measured window: the traffic restarts between them), 1× in each switch run, and **0× in all 27 stall, balanced and hot-queue runs**. Loss, DRAM and cycles identical to dyn-128 everywhere (cold 0.0260%, hot 0.463%, two borrowers 0, 500 µs 0, 2 ms 0.151%). Slow target queue (pooled over n = 3, µs):

| | p50 | p99 | p99.9 | p99.99 |
|---|---|---|---|---|
| 2,400 reads: fill-128 / dyn-128 / **dynov-128** | 2,471 / 13,319 / **869** | 2,641 / 13,519 / **1,037** | 2,651 / 13,559 / 6,807 | 2,661 / 13,743 / 13,319 |
| 3,200 reads | 3,339 / 17,791 / **1,212** | 3,511 / 17,983 / **1,380** | 3,525 / 18,015 / 10,287 | 3,535 / 18,111 / 17,807 |
| 6,400 reads | 6,811 / 35,455 / **2,573** | 6,987 / 35,711 / **2,745** | 7,007 / 35,775 / 22,319 | 7,035 / 35,999 / 35,519 |
| switch (f13) | 79 / 79 / 79 | 893 / 4,587 / **358** | 900 / 4,603 / **363** | 904 / 4,619 / 4,551 |

- **Up to p99 the overloaded queue now waits 2.5–2.8× less than the static budget and 13× less than dyn-128 did**, at the same loss: it keeps its floor (32) instead of 128 or ~800.
- **Residual: the onset.** Before the policy sees the overload (~20 ms + checks) the queue grows to the whole pool once, and those ~800 packets wait one pool-drain (13–35 ms): that is the p99.9–p99.99 tail (≈ 0.1–1% of probes, twice per run here because the traffic restarts). A real overload pays it once per episode. Making the onset cheaper would need limiting growth while a queue is behind, which is what stall absorption relies on: left as is, stated.
- **Recommendation: make `fill_overload_ms=20` the default of fill_dyn** (no cost measured anywhere, the one property rxBisect's design lacks, 20.10).

**64 B (f16): the drops are the NIC's internal buffer, and 128 posted buffers end them.** Every lost packet is in `rx_prio0_buf_discard` (ethtool), monotonic in the buffers posted in a ring of 1,024: 32 → 861 M per 10 s (NDR 0%), 64 → 310 M (58%), dyn-128's ~61 mean → 86–137 M (59%), 96 → ~20 k (71%, unstable), **128 → 0 (100%, 148.8 Mpps)**; dyn-128 with floor 128 → 0 (100%); dynov-128 = dyn-128 (59%, 0 overloads: the NIC drops before any queue falls behind). **But privring-64 (64 posted in a ring of 64) forwards 148.8 Mpps with 0 drops**, so the need is not a count of buffers alone: in a 1,024-entry ring the NIC needs ~100–128 WQEs posted ahead at 18.6 Mpps per queue (~6–7 µs of traffic). Hypothesis: the NIC's WQE prefetch/cache — a 64-entry ring (1 KiB of WQEs) stays cached, a 1,024-entry one does not and freshly written WQEs must be fetched just in time. Test: fill-64-64 (64 posted in a ring of 64). **Fix proposed: a rate-aware floor**, floor = max(32, packets/s × ~7 µs) measured per queue at each clock read: 32 at 1500 B (~1 Mpps per queue: working set and DRAM unchanged), ~130 at 64 B line rate.

**Heavy NF (f17): the cycles advantage becomes throughput once cores saturate.** Every system forwards 98 Gb/s up to 96 reads per packet (privring-1024 loses 0.79% at 96 with payload). At 128 reads:

| delivered Gb/s (loss %) | privring-1024 | privring-128 | shRing | fill-128 | **dyn-128** |
|---|---|---|---|---|---|
| payload untouched | 87.89 (10.3) | 97.80 (0.20) | 92.81 (5.3) | 97.73 (0.27) | **97.65 (0.36)** |
| payload read | 75.88 (22.6) | 92.67 (5.4) | 83.97 (14.3) | 92.24 (5.9) | **92.16 (6.0)** |

- **dyn-128 delivers +11% / +21% over privring-1024 and +5% / +10% over shRing** (untouched / payload); shRing is also the idealised dynamic shRing here (better of privring-1024 and shRing), so +5–10% over dshRing. Same order as rxBisect's reported gains (+20% LB/NAT over dshRing, +18–37% over privRing).
- dyn-128 ties the small privRing (−0.2 to −0.5%), as the papers' rxBisect does: same small working set; what separates them is burst/stall absorption (f4/f5: dyn 0 lost up to 750 µs, small privRing 0.016–0.078%).
- Cycles per packet below saturation (payload read): dyn 1,182–2,086, privring-128 1,218–2,134, shRing 1,807–2,335, privring-1024 1,458–2,386 — dyn lowest at every level.
- Projection (20.8, small-privRing anchor): rxBisect ≈ small privRing in throughput in both of its figures, so rxBisect estimated here ≈ 97.8 / 92.7 Gb/s: **parity with dyn-128**.

**Next (proposed):** (1) default `fill_overload_ms=20`; (2) rate-aware floor + diagnostic fill-64-64, then rerun f16 (64 B) and f1/f5 (1500 B unchanged?); (3) optionally f17 at 104–120 reads to place each system's knee. Nodes up to 2026-10-29.

### 20.13 Round 3 (2026-10-24): the rate floor, overload on by default

Approved by the user: (1) `fill_overload_ms=20` as fill_dyn's default; (2) a floor that follows the packet rate, to fix 64 B.

- **Fork `b96c6a87a`:** `fill_runway_ns=<ns>` (default 7000): at each clock read, over at least 1 ms (`MLX5_FILL_RATE_MIN_US`), a queue measures its packet rate from `rq_pi` (WQEs consumed: nothing new on the datapath) and its floor becomes `max(fill_floor, rate × runway)`, capped at its entitlement (beyond it credits are borrowed and can be asked back). The rate floor rises at once and, like a peak backlog, is remembered for one to two windows (`fill_rate_floor[2]`, rotated at the window end). 1500 B at ~1 Mpps per queue → 7 → the floor stays 32 (working set unchanged); 64 B at 18.6 Mpps → 130 → capped at 128, the budget that ended the NIC's drops in f16. `fill_overload_ms` now defaults to 20; both take 0 for off (`dyn-128` with `overload=0 runway=0` = the dyn of rounds 1–2, labelled `dynold-128`). The FILL credits line ends with `rate floor <now>, <max>`.
- **Harness:** `FILL_RUNWAY_NS` / `runway=<ns>`; `sweep.sh` requires a build with `fill_runway_ns`. Batteries `s3` (smoke, 3 runs, ~4 min) and `f19` (108 runs, ~2.8 h, n = 3): dyn-128 (new defaults) against dynold-128 at 64 B and 128 B (NDR + a 100% window, beside fill-128, fill-64 in rings of 1024 and 64, privring-64 — is the NIC's need a time or a ring?) and at 1500 B (balanced with shringfix-8 and fill-128; cold stall 0.5/1/2 ms; hot core; two borrowers; hot queue; slow target at 2,400 reads; heavy NF 128 reads with and without payload).
- **Predictions:** dyn-128 reaches 148.8 Mpps at 64 B with no `rx_prio0_buf_discard`; at 1500 B every number equals dynold-128 except the slow target's p99 (overload, 20.12); fill-64-64 forwards line rate if the NIC's need is WQE locality (a small ring), fails like fill-64 if it is posted time.
- **Runbook (dut):** `sudo git -C /mydata/dpdk-research/shring-dpdk pull && sudo ninja -C /mydata/dpdk-research/shring-dpdk/build`; `sudo git -C /local/repository pull`; tmux: `KEEP_GOING=1 TGEN_HOST=sm110p-10s10607.wisc.cloudlab.us TGEN_MAC=b8:3f:d2:13:08:9e harness/run_eval.sh s3 f19`.

### 20.14 Why latency ties at light load, and where it does not (2026-10-24)

The user asked why a smaller working set and fewer cycles do not lower latency. Answer (from the data and both papers): end-to-end latency (~35 µs) is the fixed path (TRex, switch, NIC) plus l3fwd's Tx batching (holds up to 32 packets or 100 µs: up to ~32 µs at ~1 Mpps per queue), plus the wait in the Rx queue — and with CPU headroom the queue is empty (occupancy 0.1–0.2 in every system). The cycles saved (dyn 448 vs privring-1024 577 per packet at f1 = 0.05 µs) are below the 1 µs histogram resolution. Both papers attribute their latency gains to queueing avoided when the baseline saturates: shRing Fig. 2 ("cycles-per-packet meet the line rate budget … low occupancy … substantially reducing the latency"; "<5 µs in favor of privRing" at lower load), rxBisect Fig. 10 ("comparable average latency (100–119 µs)" for small working sets; privRing 11× from full rings). **Our data shows the same once cores saturate (f17):** 96 reads with payload — privring-1024 occupancy 136, mean 174 µs, p99 1,138 µs against dyn-128 1.8, 38 µs, 52 µs (4.6× / 22×); shRing 42 / 57 µs. 128 reads untouched — privring-1024 1,172 µs (full ring, 1,024) against dyn 124 µs. Caveat: at 128 reads untouched the old dyn (no overload policy) had 124 µs / p99 664 against fill-128's 63 / 174 (occupancy 64 vs 26): it borrowed while saturated — f19 measures that cell with the policy on. Proposed (not approved): a load–latency battery with a heavy NF (e.g. 96 reads, payload), 50–100% offered, as the paper's latency figure.

### 20.15 Round 3 results: s3 + f19 (2026-10-24, fork `b96c6a87a`; 111 runs, complete)

dyn-128 = new defaults (overload 20 ms, runway 7 µs); dynold-128 = both off. n = 3.

- **1500 B, nothing changed (as required):** balanced DRAM 37 vs 32 MB/s (fill 624, shRing 54), loss 0; cold 0.0260% both; hot 0.4629% both; two borrowers 0 both; 500 µs 0 both; 2 ms 0.1508% both; hot queue 0 both. Rate floor at 1500 B: 7 → floor stays 32 ✓.
- **Overload on by default works on the slow target:** pv2400 p99 1,002 µs vs 13,439 (old), same loss ✓.
- **64 B NOT fixed:** dyn-128 NDR 59.0% (old 59.1%); 100% window 3.4% lost (old 5.0%), `rx_prio0_buf_discard` ~128 M per 10 s. The rate floor reached only ~85 (s3: 82 now, 85 max): **it measures the rate the queue receives (`rq_pi`), and the NIC drops before that count** — a self-limiting loop (drops → lower measured rate → lower floor → drops), settling at ~12 Mpps delivered × 7 µs ≈ 85. Fix: a runway that reaches the entitlement at the delivered rate the drops leave (≥ 128 / 12 Mpps ≈ 11 µs → 12 µs; at 1500 B still 12 → floor 32).
- **Discovery: fill-64-64 (64 posted in a ring of 64) forwards 64 B at line rate with 0 drops, like privring-64; fill-64 (64 posted in a ring of 1,024) stops at 57.8%.** So the NIC's need is not posted time alone: with the same 64 posted and the same rate, the ring size decides. Likely the NIC's WQE fetch/prefetch (a 64-entry ring = 1 KiB of WQEs; a 1,024-entry ring's WQEs are fetched over a 16 KiB area) — to state as observed, mechanism unverified.
- **Heavy NF at saturation (mi128/mi128p): the overload policy flaps.** 806–1,634 overload entries per run (~every 15–30 ms) on every queue: when all cores sit at ~100% of capacity a queue capped at its floor (32) sees a short burst now and then, leaves overload, forgets its peaks, regrows (budget max 551–800), and enters again 20 ms later. Untouched payload: still better than old (loss 0.23 vs 0.41%, mean 68 vs 117 µs, p99 385 vs 597 µs); payload read: **worse** (loss 7.58 vs 6.12%, p99 419 vs 190 µs). Fix: leave and enter overload on an *empty* poll (nb_rx = 0: the queue really caught up) instead of a short burst — under ρ ≈ 1 a queue rarely goes empty, under recovery or ordinary load (stalls drain then idle; hot queue at half capacity) it goes empty constantly.
- Proposed round 4 (not approved yet): fork change = drained := empty poll; runway default 12 µs; battery f20 confirming 64 B, heavy NF (mi96p, mi128, mi128p), slow target, stalls, balanced/hot queue.

### 20.16 Round 4 (2026-10-24): overload on an empty poll, runway 12 µs, latency under a heavy NF

Approved by the user: (1) driver fix, (2) confirmation battery, (3) load–latency battery with a heavy NF.

- **Fork `5f3a20100`:** a queue counts as drained only on an empty poll (`nb_rx == 0`), for both entering and leaving overload — a saturated core held at its floor no longer flaps (20.15). `fill_runway_ns` default 12,000: reaches 128 at the ~12 Mpps a queue still receives while the NIC drops at 64 B; ~12 at 1500 B, under the floor of 32. The start-up log reads "overload after … ms without an empty poll", which `sweep.sh` now requires.
- **Batteries:** `s4` smoke (4 runs, ~5 min: 64 B rate floor 128 and no `rx_prio0_buf_discard`; mi128p a handful of overloads, not hundreds; pv2400 overloads on queue 3 only; balanced rate floor 32). `f20` (126 runs, ~3 h, n = 3): 64 B NDR/100% (dyn, dynold, fill-128); heavy NF mi96p/mi128/mi128p (dyn, dynold, fill-128, privring-1024, shringfix-8); pv2400, pv6400, switch (dyn, dynold, fill-128); cold, hot, two borrowers, 2 ms, hot queue, balanced (dyn, dynold). `f21` (15 runs, ~1 h): latency against offered load 50–100% with 96 reads and payload, 5 systems, n = 3 — the paper's latency figure (20.14).
- **Runbook (dut):** pull + ninja the fork, pull `/local/repository`, then in tmux `KEEP_GOING=1 TGEN_HOST=sm110p-10s10607.wisc.cloudlab.us TGEN_MAC=b8:3f:d2:13:08:9e harness/run_eval.sh s4 f20 f21` (~4.2 h).
- **Predictions:** 64 B line rate for dyn with 0 NIC drops; mi128p loss ≤ dynold's 6.1% with p99 ≤ 190 µs and overloads in single digits per queue; slow target as f19 (p99 ~1 ms); everything else at 1500 B identical; f21: privring-1024's latency rises from ~95% offered, the small-working-set systems stay flat to 98–100%.

### 20.17 Round 4 results: s4, f20, f21 (2026-10-24, fork `5f3a20100`; 145 runs, complete)

dyn-128 = current defaults (overload on an empty poll, 20 ms; runway 12 µs); dynold-128 = both off. n = 3. Figures/tables for all rounds regenerated in `docs/eval2/` (f20, f21 new).

- **64 B fixed:** dyn-128 NDR **100%** (148.8 Mpps; dynold 59.3%), 100% window **0 lost** (dynold 4.0%); s4: rate floor 128 on all 8 queues, `rx_prio0_buf_discard` 3,913 per 10 s (was ~128 M).
- **1500 B unchanged:** balanced DRAM 31 vs 29 MB/s, loss 0; cold 0.0260%, hot 0.4629%, two borrowers 0, 2 ms 0.1508%, hot queue 0 — identical to dynold.
- **Slow target unchanged from f19:** pv2400 p99 1,002 µs (dynold 13,447; fill 2,596), pv6400 2,644 (35,540; 6,868), switch 337 (4,551; 872); same loss.
- **Heavy NF at saturation — dyn-128 now leads on throughput and loss:**

| 128 reads | privring-1024 | shRing | fill-128 | dynold-128 | **dyn-128** |
|---|---|---|---|---|---|
| untouched: Gb/s / loss / mean / p99 / p99.99 (µs) | 88.1 / 10.1% / 1,168 / 1,233 / 1,254 | 92.6 / 5.56% / 66 / 177 / 194 | 97.8 / 0.237% / 63 / 172 / 183 | 97.7 / 0.344% / 120 / 632 / 674 | **97.9 / 0.111% / 52 / 175 / 554** |
| payload read | 75.9 / 22.6% / 1,356 / 1,431 / 1,455 | 83.8 / 14.5% / 71 / 197 / 220 | 92.4 / 5.76% / 168 / 188 / 198 | 92.2 / 5.92% / 168 / 189 / 199 | **94.0 / 4.07% / 81 / 369 / 970** |

  dyn-128: +24% Gb/s over privring-1024, +12% over shRing, +1.8% over the static budget (payload read); the lowest loss and mean latency of all. **Remaining cost: the tail at full saturation** (p99.99 554–970 µs against fill's 183–198): a few queues still leave overload on an occasional empty poll (s4 mi128p: 2–397 overloads per queue, from 806–1,634), regrow and hold a long queue briefly. Candidate fix (not done): after leaving overload, no borrowing beyond the entitlement for one window (cool-down).
- **Latency against load with a heavy NF (f21, 96 reads, payload; the paper's latency figure):** every system ties from 50% to 95% (p99 53–90 µs falling with load; privring-1024 a few µs higher from 95%). **At 98% privring-1024's queue builds: mean 172 µs, p99 1,139 µs, 0.79% lost, against 38 µs / 52–56 µs / 0 for privring-128, fill-128, dyn-128** (4.5× mean, 22× p99), shRing 41 / 56 µs (+3 µs of synchronisation). At exactly 100% offered every system queues on Tx (mean ~300 µs, p99 ~980 µs; the knife edge of 18.7), privring-1024 1,123 µs and 7.9% lost. DRAM write at 98%: dyn 836 MB/s against 1,350–1,626 for the other small-working-set systems and 12,785 for privring-1024. (f21's occupancy column is per run, not per rate.)
- **Decided with the user (2026-10-24): no cool-down fix for the saturated tail.** It would be one more knob tuned on the same batteries for the p99.99 of an extreme case where every system loses 4–22% and dyn already leads on throughput, loss and mean latency — overfitting. It is stated as a limitation (with the overload onset). The review doc (Claude Docs "shUMEM — Avaliação e comparação com o rxBisect") was updated with rounds 3–4: summary, a versions note, the f21 latency figure, the heavy-NF table, 64 B fixed, the rxBisect table, limitations.
- **Decided with the user (2026-10-24):** in the paper the projected rxBisect rows stay in the same comparison table as the measured systems, each labelled "rxBisect (estimated from [Pismenny '25])".

### 20.18 Round 5 (2026-10-24): a real trace's load — MAWI

The user chose to skip CAIDA for now (access is restricted: request + Acceptable Use Agreement, approval time unknown) and use a public trace: **MAWI samplepoint-F** (WIDE backbone, Japan; daily 15-min traces since 2006, anonymized, headers only, caplen 96, Ethernet; no request needed). The 2026-10-14 14:00 trace: 472 M packets in 15 min, 2.8 Gb/s average, 11.9 GB compressed; ~80% IPv4; 14% of the records are segments the capturing NIC coalesced (> 1514 B).

**Why not replay the pcap:** TRex's server push sends a file from one core and its docs say the rate is limited by I/O; 100 Gb/s of real-size frames is ~12 GB/s out of a file, and the truncated records would have to be padded first (hundreds of GB). **What is replayed instead is the trace's load pattern** — what the systems react to:
- `harness/trace/trace_schedule.py`: each IPv4 packet goes to the queue the dut's RSS picks for its addresses (Toeplitz, mlx5 key, self-checked against the mapping validated on the NIC); an over-MTU record counts as the 1514 B frames it was on the wire plus the remainder; per second of trace, each queue's share of frames = one schedule step; 64 quantiles of the frame sizes = a size list. IPv6 is left out (l3fwd routes IPv4). ~250 k packets/s of parsing.
- `harness/trace/fetch_mawi.sh [<id> [<s>]]` (tgen): streams the trace (`curl | gunzip | trace_schedule.py`), stopping after the first 60 s (~1.5 GB downloaded), writes `/mydata/trace/mawi-<id>.json`.
- `udp_multiflow.py` `size_list`: each frame's size drawn at random from the list (field engine: trim + IP/UDP lengths, UDP checksum 0). `rate_sweep.py --trace <json>`: step 0's weights, the mean frame on every queue (for rates and Gb/s), the size list, and the steps as the schedule (one step per second, via `update_streams`). `schedule.py` reads the JSON's `steps`. `sweep.sh` checks the JSON exists on the tgen.
- Checked here: the first 2 s of the 2026-10-14 trace — queue shares 9.4–16.7%, max/min per second 1.80–1.86 (8 queues; rxBisect's CAIDA analysis: 3.25–4.33 over 16 cores), 35% of frames ≤ 128 B and 35% ≥ 1,400 B, mean 856 B (~13.7 Mpps at 98 Gb/s, ~1.7 Mpps per core); the top src–dst pairs carry 2–3% of the frames each. Mock end-to-end: the steps apply about every second and the offered estimate per queue follows them.
- **What it keeps and what it does not (state it):** the trace's per-queue imbalance second by second and its frame-size mix; not its sub-second bursts or the exact packet sequence; IPv4 only; rates scaled to 98% of 100 Gb/s (~35×).
- **Batteries:** `s5` smoke (2 runs, ~3 min). `f22` (60 runs, ~2 h, n = 3): 5 systems × {plain l3fwd; 32 reads + payload; 48 reads + payload (near saturation at ~1.7 Mpps per core); 32 reads + payload + 4 STREAM workers (rxBisect's co-location)}, 60 s each with `--timeseries`.
- **Runbook:** tgen — `sudo git -C /local/repository pull`, `sudo mkdir -p /mydata/trace && sudo chown $USER /mydata/trace`, `/local/repository/harness/trace/fetch_mawi.sh` (minutes, depends on the download). dut — `sudo git -C /local/repository pull`, then in tmux `KEEP_GOING=1 TGEN_HOST=sm110p-10s10607.wisc.cloudlab.us TGEN_MAC=b8:3f:d2:13:08:9e harness/run_eval.sh s5 f22` (~2 h). No fork change.

### 20.19 Round 5 results: the MAWI trace (f22; 60 runs, complete)

The s5 smoke failed after its warm-up: its 20 s window was shorter than the 60-step trace, and a step due as TRex stopped the traffic hit `update_streams` on an idle port. Fixed (`rate_sweep.py`, no step within 0.2 s of the window's end). The warm-up already showed the replay working on the real TRex: 14.1 Mpps, 97.9 Gb/s, mean frame 844 B (the 60 s JSON), 0 loss. f22 (60 s windows, 60 steps) was not affected: every run applied 60 steps, the last at ~59.02 s; per-queue offered share over the 60 s 9.8–14.2% (max/min 1.44; per second ~1.8–2).

| f22, n = 3 | privring-1024 | privring-128 | shRing | fill-128 | **dyn-128** |
|---|---|---|---|---|---|
| plain: Gb/s / loss / p99 (µs) / DRAM write (MB/s) | 98.0 / 0 / 36 / 2,291 | 98.0 / 0 / 36 / 22 | 98.0 / 0 / 37 / 3 | 98.0 / 0 / 36 / 18 | **98.0 / 0 / 36 / 2** |
| 32 reads + payload: loss / p99.99 (µs) / DRAM | 0.018% / 414 / 10,038 | 0 / 48 / 1,507 | 0.028% / 94 / 1,533 | 0 / 48 / 1,631 | **0 / 48 / 1,154** |
| 48 reads + payload: Gb/s / loss | 93.5 / 5.38% | 97.76 / 0.34% | 89.7 / 8.59% | 97.73 / 0.37% | **97.81 / 0.27%** |
| … mean / p99 / p99.9 / p99.99 (µs) | 254 / 606 / 620 / 628 | 28 / 79 / 83 / 89 | 39 / 107 / 116 / 122 | 29 / 80 / 84 / 90 | **28 / 39 / 47 / 335** |
| 32 reads + payload + 4 STREAM: Gb/s / loss | 94.7 / 4.06% | 97.75 / 0.36% | 89.5 / 8.86% | 97.73 / 0.38% | **97.77 / 0.32%** |
| … mean / p99 / p99.9 / p99.99 (µs) | 227 / 586 / 601 / 611 | 29 / 79 / 85 / 92 | 39 / 108 / 117 / 124 | 29 / 80 / 85 / 93 | **28 / 40 / 50 / 330** |

- **Under a real trace's load, near saturation, dyn-128 has the highest throughput, the lowest loss and the lowest latency up to p99.9** (p99 39–40 µs against 79–80 for the static small rings, 107–108 for shRing, 586–606 for privring-1024): the overload policy keeps the busiest queues short (occupancy 4.7–5.3 against 10–12). Over the idealised dynamic shRing (better of privring-1024 and shRing): +3.3% / +4.6% Gb/s; over shRing alone +9%. Against the small privRing — rxBisect's throughput proxy — +0.05 Gb/s, with less loss: parity.
- **The tail again:** p99.99 330–335 µs against 89–93 for the static small rings — the same saturated-tail limitation as 20.17, now with a real load.
- **shRing** loses 8.6–8.9% here: with the trace's size mix (~14 Mpps) its Rx path costs 330–480 cycles per packet against 120–190 for the others.
- **Plain trace load:** a tie on loss and latency; DRAM write: dyn 2 MB/s, shRing 3, fill 18, privring-128 22, privring-1024 2,291 — with a third of the frames small, the working set is lighter for everyone, and dyn's is the lightest.

### 20.20 Consolidation round (planned 2026-10-24 night): every number from the final dyn

Rounds 1–2 (f1–f18) ran the first dyn (no overload policy, no rate floor); only f20–f22 ran the final one (fork `5f3a20100`: overload on an empty poll, 20 ms; rate floor, 12 µs). f19–f20 matched them at 1500 B, but the paper's tables must all come from one version. Decided with the user: rerun **only dyn** in the round-1/2 batteries, at the final defaults; the baselines did not change.

- **Batteries `c<N>`** (`make_batteries.py consolidation()`): the dyn-* lines of `f<N>_*.txt`, copied with the same options, rounds and shuffled order, label system `dynfin-*` (e.g. `f1_bal_dynfin-128_n01`, `f9_single_dynfin-64-16_n02`). For f1–f15 and f17 (f16, f18 were round-2 diagnostics, superseded by f20; f0 is the smoke): 378 runs, ~6.9 h by the round-1 run times.
- **Drift check `f23`** (16 runs, ~20 min, first): `dynold-128` (dyn-128 with `overload=0 runway=0`, the first dyn) and fill-128 in four round-1 cells — f1 balanced, f4 cold stall, f8 16 reads + payload, f7 40% of 64 B packets — n = 2. Matching round 1 says the baselines of 2026-10-22/23 still compare.
- **`run_eval.sh consol`** = `f23 c1 c4 c6 c7 c5 c17 c2 c3 c9 c10 c8 c11 c12 c13 c15 c14` (priority order); one CSV per battery (`eval_c<N>.csv`), so with `KEEP_GOING=1` a failed run costs only the rest of its battery, and the same command resumes.
- **`eval_report.py`:** reads `eval_c*.csv/jsonl` too; in f1–f18 the label's `dynfin-*` is shown as `dyn-*` and the first dyn's `dyn-*` as `dynold-*` (same meaning as in f19–f20), so `dyn-*` is the final system in every table and figure, and `rxbisect_reference.py` follows.
- Mock end to end (f4 + f23 + c4 + c9 shortened): batteries, alias, knobs (`overload=0 runway=0` only on dynold) and the renaming in the report all as intended.
- **Runbook:** tgen — `sudo git -C /local/repository pull`, TRex still up in tmux `trex`. dut — `sudo git -C /local/repository pull`, then in tmux `cd /local/repository && KEEP_GOING=1 TGEN_HOST=sm110p-10s10607.wisc.cloudlab.us TGEN_MAC=b8:3f:d2:13:08:9e harness/run_eval.sh consol` (~7.2 h). No fork change.
- **Watch for** (dyn's behaviour can change where the policy acts): f9 single-core NDR (one queue near its capacity may rarely poll empty → overload → floor ~98 at 8.2 Mpps); f6 slow target (p99 ~1 ms as f20, not 13–35 ms); f17 at saturation (f20's tail: p99.99 554–970 µs); f10 64 B (100% as f20).

### 20.21 Consolidation results: f23 + c1–c17 (night of 2026-10-24/25, fork `5f3a20100`; 394 runs, complete)

394 / 394 runs (f23 16, c-batteries 378), each once (no retry), no warning; HDR latency in every window; 439 stalls of the requested length in every stall run. Data in `C:\Users\filip\shumem_eval2` (same folders). `docs/eval2` regenerated: **from here on `dyn-*` in f1–f17 is the final dyn** (overload 20 ms on an empty poll, runway 12 µs) and `dynold-*` the first one, so every table f1–f22 comes from one version.

- **No drift (f23).** dynold-128 / fill-128 against round 1 (2026-10-22/23): cold stall 0.0260 / 0.1096% (round 1 0.0260 / 0.1095), 16 reads + payload DRAM write 228 / 271 MB/s (222 / 272), balanced 0 loss with Rx cycles within 3%, 40% of 64 B packets 2.36 / 2.30% (2.60 / 2.51; a noisy cell for every system). The baselines of 22–23/10 still compare with the final dyn.
- **Where the policy has nothing to do, the final dyn is the first one.** It fired only in f6 (≥ 1,600 reads), f13 and f17 at 128 reads: 0 overloads in every balanced, load, stall, stall-length, traffic-variability, memory-intensity, co-location, skew, mempool and hot-queue cell — no false positive. Loss there matches round 1 to the third decimal: f4 cold 0.026% (fill-128 0.110, privring-1024 0.0004, shRing 0.883), hot 0.463%, two borrowers 0 in 10 / 10; f5 0 loss up to 750 µs, 0.026 / 0.089 / 0.151% at 1 / 1.5 / 2 ms. Memory: f1 DRAM write 21 MB/s (first dyn 32; shRing 52, fill-128 519, privring-128 907, privring-1024 9,972), DDIO hit 99.5% (shRing 99.0, fill 95.4, privring-128 91.9, privring-1024 19.5), 256 posted. Price of the policy: Rx 309 cycles/packet against 300 (+3%), forwarding at 16 reads 887 against 878 (+1%) — still under privring-128 (913), privring-1024 (1,024) and shRing (1,532).
- **f6 slow target** (1–2 overloads per run, no flapping): target-queue p99 (pooled) 1.04 / 1.38 / 2.07 / 2.75 ms at 2,400 / 3,200 / 4,800 / 6,400 reads, against 2.60 / 3.46 / 5.17 / 6.88 for privring-128 (fill-128 the same), 13.4 / 17.8 / 26.7 / 35.5 for the first dyn and 18–49 ms for privring-1024: 2.5× under the static small rings, 13× under the first dyn. Target occupancy 33 (static 128, first dyn 798); loss the static rings' (0.36–0.84%); bystanders untouched (p99.99 48 µs, 0 loss); shRing collapses 5 / 5 from 2,400 reads (65 → 25 Gb/s). At 1,600 reads privring-128, fill-128 and both dyns ~150 µs, shRing 237, privring-1024 12.3 ms. **Tail:** target p99.9 6.8–24 ms and p99.99 13–36 ms = the onset (the queue grows towards the pool once before the policy reacts) — the limitation of 20.12, unchanged.
- **f13 switch:** target p99 357 µs against 889–895 static, 4,587 first dyn, 6,255 privring-1024; same loss (5.10%); shRing collapses (41%). p99.99 4,555 µs = onset.
- **f10 64 B:** dyn NDR 100% (148.8 Mpps) against 59.4% for the first dyn — the runway fix holds in the full battery. shRing 14.7% (21.8 Mpps). **f9 single-core NDR:** 100% (8.20 Mpps) for dyn-128, dyn-32-8, dyn-64-16 (first dyn 99.65%); the watch item (a queue near capacity polling empty) did not happen.
- **f17 heavy NF, 128 reads, no payload — dyn best on every column, tail included, 3 / 3 runs:** 97.97 Gb/s, 0.027% loss (privring-128 97.80 / 0.20, fill 97.73 / 0.27, first dyn 97.65 / 0.36, shRing 92.81 / 5.30, privring-1024 87.89 / 10.31); mean 45 µs, p99 72, p99.99 127 against 60 / 173 / 184 for privring-128 (470–770 overloads per run, harmless here).
- **f17 128 reads + payload (beyond saturation) — best mean, bimodal:** 93.33 Gb/s, 4.77% loss (privring-128 92.67 / 5.44, fill 92.24 / 5.88, shRing 83.97 / 14.32, privring-1024 75.88 / 22.57), sd 1.5 pp: one run 94.97 Gb/s / 3.09% / p99 81 µs with 50 overloads, two runs 92.1–92.9 / 5.2–6.0% / p99 940–952 µs with 1,452–2,284 overloads. With f20's three runs of this cell (707 / 247 / 1,581 overloads → 94.73 / 94.78 / 92.51 Gb/s): **3 of 6 runs flap**; a run that does not flap is ~2.5% above the static small rings with half their p99 (81 vs 189 µs), a run that flaps measures like them on throughput and ~5× worse on p99. This is 20.17's saturated-tail limitation (no fix, decided), now with its frequency.
- **Ties stay ties:** f3 load-latency (all within a few µs up to 100%; at 100% both dyns p99.99 67–71 µs against 56–57), f7 traffic variability (everyone 0.01–0.02% at 30%, 2.3–3.3% at 40%, 12–14% at 50%, all on the target; no collapse), f8 / f11 / f12 / f15 (98 Gb/s, 0 loss everywhere; dyn's DRAM write the lowest or tied). f14 mempool: dyn 0 loss in all six configurations, cache 0 included; shRing collapses at cache 0 (3 / 3).
- **rxBisect projection** (`docs/eval2/rxbisect_projection.txt`) follows: DRAM write dyn / shRing 0.41 (was 0.61), forwarding cycles 0.43× shRing, single-core NDR 100%; Fig. 14 / 15 parity unchanged.

**For the paper:** the headline claims hold with the final dyn, from one version: (1) memory — 21 MB/s DRAM write and 99.5% DDIO hit at 256 posted, lowest of all systems; (2) stall isolation — cold-core loss 4× under a static 128 ring and 34× under shRing, only on the stalled queue, two borrowers 0 loss; (3) no collapse where shRing collapses (slow target, switch, small packets, no mempool cache); (4) slow target / switch latency 2.5× under the static rings and 13× under the first dyn; (5) heavy NF without payload: best throughput, loss and latency. Limitations, quantified: onset tail (p99.9–p99.99 of a slow target), half of the runs at sustained saturation with payload flap (p99 ~5× the static rings'), +3% Rx cycles for the policy. Next: update the review doc (drop the "versões do dyn" note), tag the evaluated code (fork `5f3a20100`, shUMEM this commit), paper figures and text. Nodes reserved to 2026-10-29.
