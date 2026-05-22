# Benchmark Reference Cache Index

This file indexes the ignored local reference cache used while designing
OCUDUAgentBench. Downloaded code, papers, reports, and checksums live under:

```text
.benchmark-workspace/external/benchmark-references/
```

That directory is ignored by git through the existing `.benchmark-workspace/`
rule. This index is the only tracked record of the cache.

Retrieved on: 2026-05-22

## Cache Layout

| Local path | Purpose |
| --- | --- |
| `.benchmark-workspace/external/benchmark-references/code/` | Shallow code and documentation clones. |
| `.benchmark-workspace/external/benchmark-references/papers/` | Public benchmark-design papers as PDFs. |
| `.benchmark-workspace/external/benchmark-references/reports/` | Public project documentation/report snapshots. |
| `.benchmark-workspace/external/benchmark-references/checksums/sha256sums.txt` | SHA256 checksums for downloaded PDFs and HTML snapshots. |

## Code And Documentation References

| Title | Source URL | Local path | Source type | Retrieval command | Commit | Benchmark relevance |
| --- | --- | --- | --- | --- | --- | --- |
| OCUDU release 26.04 local reference | `https://gitlab.com/ocudu/ocudu` | `.benchmark-workspace/external/ocudu-release_26_04` | Existing ignored code clone | Existing local reference; not duplicated | `050a2bb72e1d794cd60570d809987c1fcda3e54b` | Ground truth for OCUDU config, WebSocket/CLI surfaces, runtime structure, and live-adapter planning. |
| OCUDU documentation | `https://gitlab.com/ocudu/ocudu_docs.git` | `.benchmark-workspace/external/benchmark-references/code/ocudu_docs` | Shallow documentation clone | `git clone --depth 1 https://gitlab.com/ocudu/ocudu_docs.git .benchmark-workspace/external/benchmark-references/code/ocudu_docs` | `fc6a24f84a6a9ab0b10ebf59ae159b8bb0a4419a` | Reference for user-facing OCUDU setup, configuration, tutorials, and terminology. |
| FlexRIC OCUDU KPM v05 local reference | local fork/reference clone | `.benchmark-workspace/external/flexric-ocudu-kpm-v05` | Existing ignored code clone | Existing local reference; not duplicated | `dc668edbccbeb51d93075826d7eece044ca6cd93` | Ground truth for current benchmark E2SM-KPM v05 decoding experiments and OCUDU/FlexRIC integration notes. |
| Upstream FlexRIC | `https://github.com/openaicellular/flexric.git` | `.benchmark-workspace/external/benchmark-references/code/flexric_upstream` | Shallow code clone | `git clone --depth 1 https://github.com/openaicellular/flexric.git .benchmark-workspace/external/benchmark-references/code/flexric_upstream` | `1f04cc558ebc8da9de6a620762bc02f5db4ecb4a` | Reference for E2 agent, near-RT RIC, xApp, and service-model implementation boundaries. |
| Open5GS | `https://github.com/open5gs/open5gs.git` | `.benchmark-workspace/external/benchmark-references/code/open5gs` | Shallow code clone | `git clone --depth 1 https://github.com/open5gs/open5gs.git .benchmark-workspace/external/benchmark-references/code/open5gs` | `e71ef41e9f350d6d67fc01be88f75226172d14d4` | Reference for benchmark-owned core runtime-support tasks, NF restart semantics, and UE-registration repair planning. |
| srsRAN Project archive | `https://github.com/srsran/srsRAN_Project.git` | `.benchmark-workspace/external/benchmark-references/code/srsRAN_Project` | Shallow code clone | `git clone --depth 1 https://github.com/srsran/srsRAN_Project.git .benchmark-workspace/external/benchmark-references/code/srsRAN_Project` | `4bf1543936d062686d64c10724d2f27a9854f065` | Reference for historical srsRAN/OCUDU lineage, ZMQ examples, Open5GS integration, and RAN configuration conventions. |

## Benchmark Implementation References

These repositories are reference implementations for agent/interactive benchmark
machinery. They are not dependencies of OCUDUAgentBench and are kept ignored.

| Title | Source URL | Local path | Source type | Retrieval command | Commit | Benchmark relevance |
| --- | --- | --- | --- | --- | --- | --- |
| AgentBench | `https://github.com/THUDM/AgentBench.git` | `.benchmark-workspace/external/benchmark-references/code/AgentBench` | Shallow code clone | `git clone --depth 1 https://github.com/THUDM/AgentBench.git .benchmark-workspace/external/benchmark-references/code/AgentBench` | `d1e4a10db08c87075c78972e48ecc182be03e2d5` | Reference for multi-environment task servers, agent session handling, and function-calling benchmark orchestration. |
| WebArena | `https://github.com/web-arena-x/webarena.git` | `.benchmark-workspace/external/benchmark-references/code/webarena` | Shallow code clone | `git clone --depth 1 https://github.com/web-arena-x/webarena.git .benchmark-workspace/external/benchmark-references/code/webarena` | `dce04686a56253aefba7b18a4fa0937cf1dc987b` | Reference for self-hosted realistic environments, task config structure, observation/action loops, and reproducible evaluation. |
| SWE-bench | `https://github.com/SWE-bench/SWE-bench.git` | `.benchmark-workspace/external/benchmark-references/code/SWE-bench` | Shallow code clone | `git clone --depth 1 https://github.com/SWE-bench/SWE-bench.git .benchmark-workspace/external/benchmark-references/code/SWE-bench` | `f7bbbb2ccdf479001d6467c9e34af59e44a840f9` | Reference for executable task validation, dataset packaging, run evaluation, and leaderboard-compatible summaries. |
| tau-bench | `https://github.com/sierra-research/tau-bench.git` | `.benchmark-workspace/external/benchmark-references/code/tau-bench` | Shallow code clone | `git clone --depth 1 https://github.com/sierra-research/tau-bench.git .benchmark-workspace/external/benchmark-references/code/tau-bench` | `59a200c6d575d595120f1cb70fea53cef0632f6b` | Reference for tool-agent-user interaction, policy compliance, repeated-trial reliability, and domain API simulation. |
| tau2-bench | `https://github.com/sierra-research/tau2-bench.git` | `.benchmark-workspace/external/benchmark-references/code/tau2-bench` | Shallow code clone | `git clone --depth 1 https://github.com/sierra-research/tau2-bench.git .benchmark-workspace/external/benchmark-references/code/tau2-bench` | `5a8fce3d52ba27f526dc98ec593d9c42544314d2` | Reference for dual-control and changing-environment benchmark mechanics relevant to latent-cause and stimulus design. |
| OSWorld | `https://github.com/xlang-ai/OSWorld.git` | `.benchmark-workspace/external/benchmark-references/code/OSWorld` | Shallow code clone | `git clone --depth 1 https://github.com/xlang-ai/OSWorld.git .benchmark-workspace/external/benchmark-references/code/OSWorld` | `705623ca18e0055dd995fd5a350d6588cff2caf5` | Reference for environment setup, observation/action traces, task verification, and leakage risks in real computer-use benchmarks. |
| Terminal-Bench | `https://github.com/laude-institute/terminal-bench.git` | `.benchmark-workspace/external/benchmark-references/code/terminal-bench` | Shallow code clone | `git clone --depth 1 https://github.com/laude-institute/terminal-bench.git .benchmark-workspace/external/benchmark-references/code/terminal-bench` | `1a6ffa9674b571da0ed040c470cb40c4d85f9b9b` | Reference for terminal sandboxing, task-local verifiers, task packaging, and robust failure reporting. |
| SWE-Skills-Bench | `https://github.com/GeniusHTX/SWE-Skills-Bench.git` | `.benchmark-workspace/external/benchmark-references/code/SWE-Skills-Bench` | Shallow code clone | `git clone --depth 1 https://github.com/GeniusHTX/SWE-Skills-Bench.git .benchmark-workspace/external/benchmark-references/code/SWE-Skills-Bench` | `95b3ce519fcb58d0b19e90a5b6e5165211dc6dd1` | Reference for skill-ablation task construction and measuring marginal benefit across nearby task variants. |

## Papers

| Title | Source URL | Local path | Source type | Retrieval command | SHA256 | Benchmark relevance |
| --- | --- | --- | --- | --- | --- | --- |
| Establishing Best Practices for Building Rigorous Agentic Benchmarks | `https://arxiv.org/pdf/2507.02825` | `.benchmark-workspace/external/benchmark-references/papers/agentic_benchmark_checklist_2507.02825.pdf` | Paper PDF | `curl -fL https://arxiv.org/pdf/2507.02825 -o .benchmark-workspace/external/benchmark-references/papers/agentic_benchmark_checklist_2507.02825.pdf` | `6bee07172c55c1146fdc8a14e0a4839854f7b4be36616729fda3ee6d519a698b` | Checklist reference for leakage control, reward validity, failure attribution, and benchmark rigor. |
| AgentBench: Evaluating LLMs as Agents | `https://arxiv.org/pdf/2308.03688` | `.benchmark-workspace/external/benchmark-references/papers/agentbench_2308.03688.pdf` | Paper PDF | `curl -fL https://arxiv.org/pdf/2308.03688 -o .benchmark-workspace/external/benchmark-references/papers/agentbench_2308.03688.pdf` | `9c780e35fc0b2de6c2e21e0572f6aaaadcf7ecdd56d63cfaae2b415bc0dc83c3` | General multi-environment agent benchmark framing and evaluation dimensions. |
| WebArena: A Realistic Web Environment for Building Autonomous Agents | `https://arxiv.org/pdf/2307.13854` | `.benchmark-workspace/external/benchmark-references/papers/webarena_2307.13854.pdf` | Paper PDF | `curl -fL https://arxiv.org/pdf/2307.13854 -o .benchmark-workspace/external/benchmark-references/papers/webarena_2307.13854.pdf` | `f9731b92bc3d29a2ea7b5f9cb46b48540c76bc3f84a57e0c48fc37ea73107f95` | Reference for realistic environments, task success criteria, and multi-step agent interaction design. |
| SWE-bench: Can Language Models Resolve Real-World GitHub Issues? | `https://arxiv.org/pdf/2310.06770` | `.benchmark-workspace/external/benchmark-references/papers/swe_bench_2310.06770.pdf` | Paper PDF | `curl -fL https://arxiv.org/pdf/2310.06770 -o .benchmark-workspace/external/benchmark-references/papers/swe_bench_2310.06770.pdf` | `f7e8e1df64129742b8199a21a042734519a823a1dafd6f48f8f3ddcfb48ee296` | Reference for using real implementation contexts, executable validation, and issue/task construction. |
| tau-bench: A Benchmark for Tool-Agent-User Interaction in Real-World Domains | `https://arxiv.org/pdf/2406.12045` | `.benchmark-workspace/external/benchmark-references/papers/tau_bench_2406.12045.pdf` | Paper PDF | `curl -fL https://arxiv.org/pdf/2406.12045 -o .benchmark-workspace/external/benchmark-references/papers/tau_bench_2406.12045.pdf` | `0ce66a1763d698c61bb311c3c874bf593d1e9a5bfff11bb35f6f72b981f6da56` | Reference for tool-use policy compliance, user/environment interaction, and task outcome scoring. |
| OSWorld: Benchmarking Multimodal Agents for Open-Ended Tasks in Real Computer Environments | `https://arxiv.org/pdf/2404.07972` | `.benchmark-workspace/external/benchmark-references/papers/osworld_2404.07972.pdf` | Paper PDF | `curl -fL https://arxiv.org/pdf/2404.07972 -o .benchmark-workspace/external/benchmark-references/papers/osworld_2404.07972.pdf` | `d4c6e20dd59467f005561b1e97199f9842fd3b0e9fdd93e66e06ba0ec09edfdb` | Reference for long-horizon interaction, environment realism, and reproducible agent evaluation. |
| Terminal-Bench: Benchmarking Agents on Hard, Realistic Tasks in Command Line Interfaces | `https://arxiv.org/pdf/2601.11868` | `.benchmark-workspace/external/benchmark-references/papers/terminal_bench_2601.11868.pdf` | Paper PDF | `curl -fL https://arxiv.org/pdf/2601.11868 -o .benchmark-workspace/external/benchmark-references/papers/terminal_bench_2601.11868.pdf` | `4430e04a507b2442bb7b24a88899b750948fa0a6b3bbc33a8d541d6d4debcc1e` | Reference for hard operational tasks, terminal/tool execution, and error analysis. |
| SWE-Skills-Bench: Do Agent Skills Actually Help in Real-World Software Engineering? | `https://arxiv.org/pdf/2603.15401` | `.benchmark-workspace/external/benchmark-references/papers/swe_skills_bench_2603.15401.pdf` | Paper PDF | `curl -fL https://arxiv.org/pdf/2603.15401 -o .benchmark-workspace/external/benchmark-references/papers/swe_skills_bench_2603.15401.pdf` | `981c0df0f9d2fa8fc433d094392e1911fefac4223bbe245b8d6d9d1479586ba0` | Reference for isolating marginal skill value through controlled task variants. |

## Reports And Documentation Snapshots

| Title | Source URL | Local path | Source type | Retrieval command | SHA256 | Benchmark relevance |
| --- | --- | --- | --- | --- | --- | --- |
| srsRAN Project Documentation PDF | `https://docs.srsran.com/_/downloads/project/en/latest/pdf/` | `.benchmark-workspace/external/benchmark-references/reports/srsran_project_documentation_latest.pdf` | Documentation PDF | `curl -fL https://docs.srsran.com/_/downloads/project/en/latest/pdf/ -o .benchmark-workspace/external/benchmark-references/reports/srsran_project_documentation_latest.pdf` | `e4cc2632399beac3f684ea3b7c2faec3c7d0a5332d32537e910f577f9816a7c0` | Reference for ZMQ setup, Open5GS integration, gNB configuration, and historical srsRAN project behavior. |
| Open5GS Documentation Index | `https://open5gs.org/open5gs/docs/` | `.benchmark-workspace/external/benchmark-references/reports/open5gs_docs_index.html` | Documentation HTML snapshot | `curl -fL https://open5gs.org/open5gs/docs/ -o .benchmark-workspace/external/benchmark-references/reports/open5gs_docs_index.html` | `dfe572dc44e76daff5c32deca286b00c08eb0f8465bb51732fa2940e282152e0` | Reference entry point for Open5GS deployment, subscriber configuration, and core-network task design. |

## Restricted Or License-Gated References

The cache intentionally does not download restricted or license-gated standards
documents. Use the official portals below for human lookup only, and record
only public citations or summaries allowed by their terms.

| Reference family | Official link | Local status | Benchmark use |
| --- | --- | --- | --- |
| O-RAN Alliance specifications | `https://www.o-ran.org/specifications` | Not downloaded | Check terminology and control-plane expectations only when access is permitted. |
| 3GPP specifications | `https://www.3gpp.org/specifications-technologies/specifications-by-series` | Not downloaded | Check public spec identifiers and architecture terminology only when access is permitted. |

## Verification Commands

```bash
git check-ignore .benchmark-workspace/external/benchmark-references
find .benchmark-workspace/external/benchmark-references -maxdepth 3 -type f | sort
for d in .benchmark-workspace/external/benchmark-references/code/*; do git -C "$d" rev-parse HEAD; done
shasum -a 256 .benchmark-workspace/external/benchmark-references/papers/*.pdf
shasum -a 256 .benchmark-workspace/external/benchmark-references/reports/*
```
