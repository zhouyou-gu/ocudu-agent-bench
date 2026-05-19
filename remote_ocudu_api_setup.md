# Remote OCUDU Runtime Setup And API Readiness

This runbook prepares the remote OCUDU system for the paper-aligned task model used by OCUDUAgentBench. It is a setup and readiness guide, not a task definition and not a scored agent workflow.

The task model is:

```text
T = <G, E, U, I, J>

G: Agent Goal
E: OCUDU Runtime Setup
U: Benchmark Stimulus
I: RAN APIs
J: Task Scoring
```

This runbook mainly prepares `E`, verifies the implemented API bindings used by `I`, and checks that required oracle sources for `J` can be produced. Stimulus `U` is configured by task episodes and uses the prepared runtime mechanisms.

## 1. Readiness Concepts

| Term | Meaning |
|---|---|
| Runtime setup `E` | Source, build, image, config, topology, port, and component requirements needed to instantiate OCUDU runtime `R`. |
| API readiness for `I` | Evidence APIs, action APIs, and feedback APIs are reachable and behave as expected. |
| Oracle readiness for `J` | Runtime metrics, logs, decoded traces, PCAP/log summaries, cleanup checks, or other scorer inputs can be produced. |
| Conformance gate | Pre-scoring check proving that a task's required setup, APIs, and oracle sources are usable. |
| Task episode | One scored or diagnostic run using `G`, `E`, `U`, `I`, and `J`. |

Conformance gates run before scored interaction. They are not LLM-agent actions and must not be reported as agent performance.

## 2. Configuration

Start from the tracked template:

```bash
cp .config.example .config
```

Fill site-local values in ignored `.config`:

```text
remote.ssh=user@host
remote.ssh-key=~/.ssh/key
remote.workspace=~/skillful-ran-benchmark-workspace
remote.ocudu-root=~/skillful-ran-benchmark-workspace/ocudu

runtime.open5gs-compose=~/skillful-ran-benchmark-workspace/assets/open5gs-core/compose/docker-compose.open5gs.yml
runtime.e2e-config-dir=~/skillful-ran-benchmark-workspace/assets/ocudu-zmq-open5gs-e2e/config
runtime.gnb-image=skillful-ran/ocudu-build:release_26_04
runtime.ue-image=skillful-ran/srsran-4g-ue-build:release_23_11
runtime.open5gs-image=<open5gs image>

sources.ocudu-repo=https://gitlab.com/ocudu/ocudu.git
sources.ocudu-ref=release_26_04
sources.srsran-4g-repo=<UE emulator repo>
sources.srsran-4g-ref=release_23_11
sources.flexric-ocudu-repo=https://github.com/zhouyou-gu/flexric-ocudu-kpm-v05.git
sources.flexric-ocudu-ref=<validated ref>

ric.provider=flexric
```

Keep real hostnames, user names, private paths, keys, tokens, kubeconfigs, and endpoints out of git.

## 3. Setup Workflow

Recommended first setup sequence:

```bash
python3 benchmark/benchctl.py remote check --config .config --json
python3 benchmark/benchctl.py remote init --config .config --json
python3 benchmark/benchctl.py remote sync --config .config --json
python3 benchmark/benchctl.py remote provision --config .config --json
python3 benchmark/benchctl.py remote ric-prepare --config .config --json
python3 benchmark/benchctl.py conformance list --json
```

Use dry-run before a first remote setup:

```bash
python3 benchmark/benchctl.py remote provision --config .config --dry-run --json
```

Run individual setup stages when debugging:

```bash
python3 benchmark/benchctl.py remote provision --config .config --stage assets --json
python3 benchmark/benchctl.py remote provision --config .config --stage images --json
python3 benchmark/benchctl.py remote provision --config .config --stage ocudu --json
python3 benchmark/benchctl.py remote provision --config .config --stage runtime-deps --json
python3 benchmark/benchctl.py remote provision --config .config --stage ric --json
```

Destructive reset:

```bash
python3 benchmark/benchctl.py remote reset-workspace --config .config --force --json
```

This deletes prior benchmark workspace state under `remote.workspace`. It does not prune Docker daemon images by default.

## 4. Readiness By Task Element

| Task element | Remote readiness needed |
|---|---|
| `G` Agent Goal | Local task metadata and management context are available. |
| `E` OCUDU Runtime Setup | OCUDU source/build/install or image, Open5GS, UE emulator, ZMQ assets, optional RIC/xApp assets, configs, ports, and cleanup hooks are ready. |
| `U` Benchmark Stimulus | Traffic generation, UE activity, timing controls, settling windows, backend availability controls, and radio-emulation mechanisms needed by the task are ready. |
| `I` RAN APIs | Evidence APIs, action APIs, feedback paths, validation rules, and no-action handling are available. |
| `J` Task Scoring | Oracle artifacts, logs, decoded traces, metrics summaries, action/feedback traces, and cleanup checks are produced. |

## 5. API And Oracle Readiness

| Binding | Required readiness |
|---|---|
| JSON metrics evidence | WebSocket endpoint is available, subscription succeeds, metric frames parse, freshness can be reported. |
| WebSocket PRB action | Invalid PRB command is rejected, valid command is accepted, feedback record is produced. |
| WebSocket SSB action | Invalid SSB command is rejected, valid command is accepted, feedback record is produced. |
| E2 KPM evidence | RIC starts, OCUDU E2 setup succeeds, KPM xApp emits decoded KPM v05 records. |
| E2 CCC action | FlexRIC-derived image exposes the CCC control tool and oracle evidence confirms control outcome. |
| E2 RC DU action | UE identity is discoverable, RC DU control tool is present, oracle evidence confirms control outcome. |
| Traffic health | UE attach and ping or task traffic path can be observed. |
| Oracle artifacts | Required logs, decoded KPM/control records, PCAP/log summaries, and trace summaries exist when required. |
| Cleanup | Episode containers stop and benchmark ports close after each run. |

A scored task must run only after all conformance checks required by its `E`, `I`, and `J` components pass.

## 6. Conformance Commands

WebSocket, JSON metrics, and traffic health gate:

```bash
python3 benchmark/benchctl.py conformance run \
  --config .config \
  --json \
  --checks docker_e2e_assets,open5gs_core_health,srsue_zmq_attach,ping_traffic_path,websocket_prb_policy_action
```

SSB action gate:

```bash
python3 benchmark/benchctl.py conformance run \
  --config .config \
  --json \
  --checks websocket_ssb_power_action
```

E2 KPM evidence gate:

```bash
python3 benchmark/benchctl.py conformance run \
  --config .config \
  --json \
  --checks flexric_docker_assets,near_rt_ric_health,ocudu_e2_config,e2_setup_path,e2_kpm_subscription,e2_pcap_log_oracle
```

E2 control gates:

```bash
python3 benchmark/benchctl.py conformance run \
  --config .config \
  --json \
  --checks e2_ccc_prb_control_path,e2_rc_du_prb_control_path
```

If a gate fails, the related task is unscored until setup, API readiness, or oracle readiness is restored.

## 7. Task Episode Execution

Single episode:

```bash
python3 benchmark/benchctl.py episode run \
  --config .config \
  --task ws_prb_ping_v1 \
  --duration 30 \
  --json
```

Suite:

```bash
python3 benchmark/benchctl.py episode suite \
  --config .config \
  --task ran_policy_triage_v1 \
  --controller triage_reference \
  --runs 12 \
  --seed 1 \
  --json
```

LLM agents should use the Python API for the live loop:

```python
from benchmark.benchmark_api.env import BenchmarkEnv

env = BenchmarkEnv(config_path=".config")
obs = env.reset({"task": "ran_policy_triage_v1", "conformance": "required"})
action = decide_from_structured_evidence(obs)
feedback = env.act(action, telemetry={"rationale": rationale, "token_usage": token_usage})
summary = env.close()
```

The observation `obs` is an evidence frame from `I`; `action` is `A_i` or no-action; `feedback` is immediate `F_i`; `summary` is produced by `J` after the episode.

## 8. Troubleshooting

| Symptom | Likely category | Response |
|---|---|---|
| SSH or rsync fails | Setup | Fix `.config`, key permissions, host reachability, or remote shell. |
| OCUDU binary or image missing | Setup | Run `remote provision --stage ocudu` or `--stage images`; inspect manifest and build logs. |
| Runtime libraries missing | Setup | Run runtime dependency stage; avoid untracked host package changes unless chosen outside the benchmark. |
| WebSocket port busy | Runtime or cleanup | Cleanup stale episode containers/processes, then rerun conformance. |
| Metrics do not arrive | API readiness | Check JSON metrics config, gNB logs, and WebSocket subscription result. |
| KPM records missing | API or oracle readiness | Check RIC/xApp logs, decoded KPM output, E2 setup, and image manifest. |
| CCC/RC blocked | API or oracle readiness | Confirm FlexRIC-derived image contains required one-shot tools and control oracle support. |
| PCAP/log summary missing | Oracle | Treat run as unscored; inspect remote artifact paths. |
| Containers remain after run | Cleanup | Run episode cleanup and verify ports `8001`, `36421`, and control ports are closed. |

## 9. Safety Rules

- Do not copy OCUDU source, raw logs, captures, PCAPs, Docker artifacts, or remote run directories into local git.
- Do not score a task when setup, readiness, runtime, oracle, or cleanup gates fail.
- Do not expose hidden runtime condition, oracle labels, task scoring answers, future evidence, or internal episode metadata in agent observations.
- Do not treat provisioning, conformance, ping setup, cleanup, SSH, Docker, or artifact collection as OCUDU-native RAN control APIs.
- Do not expose a control as a scored action until source evidence and conformance results prove a stable process-external API.
