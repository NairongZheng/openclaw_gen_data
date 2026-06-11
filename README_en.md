<div align="center">

# openclaw_gen_data

**Stage 2 + 3 of the ISE pipeline: Multi-Turn Simulation + Execution Grounding.**

*Drives a local OpenClaw agent through role-locked multi-turn interaction, runs every tool call against a real OS workspace, archives full session trajectories, and converts them into training-ready OpenAI format.*

English · [简体中文](README.md)

[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-blue.svg)](#)

</div>

- 📄 **Paper:** [arXiv:2606.11520](https://arxiv.org/abs/2606.11520)
- 🛠️ **Umbrella project:** [ISE-Trace](https://github.com/Valiere01/ISE-Trace) — *Intent → Simulate → Execute*
- 🧩 **Upstream (Stage 1, intent construction):** https://github.com/NairongZheng/intent_creator
- 🤗 **Dataset (ISETrace):** https://huggingface.co/datasets/valiere/ISETrace

---

## Where It Fits in the ISE Pipeline

`openclaw_gen_data` is not standalone — it is one stage under the [ISE-Trace](https://github.com/Valiere01/ISE-Trace) umbrella. The pipeline is split across two repositories, one per phase:

```
   intent_creator              openclaw_gen_data
  +-------------------+        +-------------------+        +-----------+
  | [1] Intent        | intents| [2] Simulate      |        |           |
  |                   | .jsonl | [3] Execute       |        |  ISETrace |
  | Persona x Domain  |------->| role-locked sim   |------->|  23,132   |
  | x Task x Complex  |        | + real OS exec    |        |  traj.    |
  +-------------------+        +-------------------+        +-----------+
        Stage I                   Stage S + E                  output
```

> **[1] Intent** — `intent_creator`: samples 4D structured intents over `Persona x Domain x Task x Complexity`.
> **[2] Simulate** + **[3] Execute** — `openclaw_gen_data` (this repo): role-locked multi-turn simulation, every tool call run on a real OS in isolation.
> Produces **ISETrace**: 23,132 multi-turn, execution-grounded trajectories.

- **Input:** structured intents sampled by `intent_creator` over `Persona × Domain × Task × Complexity` (JSONL).
- **Output:** full raw session trajectories + training OpenAI format, feeding into the **ISETrace** dataset.

---

## Overview

This repository is **Stage 2 + Stage 3** of the **ISE** (**I**ntent → **S**imulate → **E**xecute) three-stage paradigm. It consumes the structured intents produced upstream by `intent_creator` and engineers the following chain to be automated, recoverable, and reproducible:

```
structured user intent  →  multi-turn agent interaction (simulated user)  →  raw session trajectory  →  training OpenAI format
```

The goal is not "call one agent once to finish a task," but to **batch-synthesize high-quality, execution-grounded, multi-turn trajectory data**. Unlike most pipelines that back-derive tasks from an API catalog, are single-turn, and simulate tool calls, this pipeline emphasizes:

- **Genuine user simulation** — an external LLM acts as a role-locked user simulator, deciding turn by turn "what the next query is / whether the task is complete," instead of dumping the whole intent on the agent at once.
- **Real execution grounding** — every tool call runs in an isolated, real OS workspace, preserving authentic failure → recovery dynamics rather than fabricated tool responses.
- **Runtime fidelity** — captures the tool definitions OpenClaw **actually sends to the model** (`tools` + `system_prompt`), avoiding the mismatch between a static scan and the real runtime.
- **Stability at scale** — multi-worker concurrency, progress-file resume, per-worker runtime snapshots, deferred session finalization, and OpenClaw runtime self-healing with auto-restart.

The main entry point is [`scripts/run_generation.py`](scripts/run_generation.py).

---

## Two-Model Separation of Duties

The system makes two distinct kinds of model calls with strictly separated roles:

| Role | Model | Responsibility | Config block |
|------|-------|----------------|--------------|
| **Executor** | OpenClaw underlying model | Actually drives the OpenClaw agent to perform tasks and call tools | `openclaw.*` |
| **User simulator** | External `LLMClient` | High-level query scheduling: decides the next query, judges task completion | `llm.*` |

---

## Architecture

Logically split into 6 layers:

| Layer | Responsibility | Core files |
|-------|----------------|------------|
| Input | Load & normalize tasks (`intent` / `direct_query`) | `src/intent_loader.py` |
| Orchestration | Orchestrate the global flow, build the task queue, summarize, trigger recovery | `scripts/run_generation.py`, `src/generation_support.py` |
| Decision (User Loop) | Simulate the user advancing the task, generate queries / judge completion turn by turn | `src/llm_client.py`, `prompts/user_model_system_prompt.txt` |
| Execution | Real interaction with the OpenClaw agent; session reset/archive/restore; runtime probe | `src/openclaw_wrapper.py`, `scripts/init_agents.py` |
| Recovery | Worker snapshots, pending-session recovery, config baseline rollback, gateway restart | `src/worker_snapshot.py`, `src/agent_runtime.py`, `src/runtime_recovery.py`, `src/fs_utils.py` |
| Conversion | Raw session → training OpenAI format | `src/session_parser.py`, `src/converter.py` |

Full design notes: [`docs/project-architecture-and-introduction.md`](docs/project-architecture-and-introduction.md).

---

## Quick Start

Choose how to run:

- **Docker container** (recommended, zero local deps) → see Docker Quick Run below
- **Local run** (openclaw + Python already installed) → steps 1–4

### Docker Quick Run

```bash
# Build image (amd64)
docker buildx build --platform linux/amd64 -t openclaw-gen-data:amd64 --load .

# Run
docker run --rm -it \
  -v /path/to/config.yaml:/tmp/config.yaml:ro \
  -v /path/to/intents.jsonl:/tmp/intents.jsonl:ro \
  -v /path/to/output:/tmp/output \
  -e CONFIG_PATH=/tmp/config.yaml \
  -e OUTPUT_DIR=/tmp/output \
  -e INTENTS_FILE=/tmp/intents.jsonl \
  -e CONCURRENT_NUM=3 \
  -e OPENCLAW_MODEL_URL=https://your-model-endpoint/v1 \
  -e OPENCLAW_MODEL_API_KEY=sk-xxx \
  -e OPENCLAW_MODEL_NAME=your-model \
  -e LLM_BASE_URL=https://your-model-endpoint/v1 \
  -e LLM_API_KEY=sk-xxx \
  -e LLM_MODEL_NAME=your-model \
  openclaw-gen-data:amd64 \
  /workspace/scripts/start_generation_in_container.sh
```

> For more details (CI, Serper search, arm64 build), see [`docs/search-and-deployment.md`](docs/search-and-deployment.md).

---

### Local Run

#### 1. Install dependencies

```bash
pip install -r requirements.txt
cp config/config_example.yaml config/config.yaml
```

> Install into an isolated virtual environment (venv / conda). Python 3.10+ is required.

#### 2. Prepare configuration

The most commonly used settings:

```yaml
generation:
  intents_per_session: "${INTENTS_PER_SESSION:-1}"
  append_query_enabled: "${APPEND_QUERY_ENABLED:-false}"
  append_query_file: "${APPEND_QUERY_FILE:-}"

paths:
  intents_file: "${INTENTS_FILE:-data_examples/intents.jsonl}"
```

Runtime precedence: **ENV > CLI > config**.

At minimum you must provide two model endpoints (the executor + the user simulator), which can go through environment variables:

```bash
# OpenClaw underlying executor model
export OPENCLAW_MODEL_URL=...      # OpenAI / Anthropic compatible endpoint
export OPENCLAW_MODEL_API_KEY=*** OPENCLAW_MODEL_NAME=...

# User simulator (User Loop)
export LLM_BASE_URL=...
export LLM_API_KEY=*** LLM_MODEL_NAME=...
```

To enable search, additionally provide `OPENCLAW_SEARCH_PROVIDER` / `OPENCLAW_SEARCH_API_KEY` / `OPENCLAW_SEARCH_BASE_URL`.

If you start `openclaw gateway run` manually inside a container, make sure `~/.openclaw/openclaw.json` contains:

```json
{
  "discovery": {
    "mdns": {
      "mode": "off"
    }
  }
}
```

This prevents `gateway` from crashing on startup in containers with very long hostnames, where the mDNS broadcast name exceeds the length limit.

#### 3. Initialize agents

```bash
python scripts/init_agents.py --num-agents 4 --force-recreate --refresh-tools
```

Notes:

- `--refresh-tools` fires a short-lived runtime probe during init, capturing the shared runtime metadata OpenClaw **actually sends to the model** (`tools` + `system_prompt`).
- Written by default to `output/worker_snapshots/runtime_metadata/runtime_metadata.json`.
- Probe debug snapshots are additionally written to `output/worker_snapshots/runtime_metadata/probe/`.
- For details on tool extraction, see [`tools/tool-inspector/README.md`](tools/tool-inspector/README.md).

#### 4. Run generation

```bash
python scripts/run_generation.py --concurrent 4
```

To launch the whole flow in a container, see the Docker example in [`docs/search-and-deployment.md`](docs/search-and-deployment.md).

---

## Run Modes

| Mode | When to use | Key config |
|------|-------------|------------|
| intent mode | Tasks with `natural_language_intent`, driven multi-turn via the LLM user loop | `INTENTS_FILE` |
| query mode | Tasks with `query`/`question`, sent directly to OpenClaw | `INTENTS_FILE`, `APPEND_QUERY_ENABLED=false` |
| intent + finalize query | intent task + one appended query before each session finalizes | `APPEND_QUERY_ENABLED=true`, `APPEND_QUERY_FILE` |

Details: [`docs/run-modes.md`](docs/run-modes.md).

---

## Command-Line Arguments

### `scripts/run_generation.py`

| Argument | Description |
|----------|-------------|
| `--config` | Config file path |
| `--intents-file` | Override the intents file path from config |
| `--limit N` | Process only the first N tasks (for debugging) |
| `--concurrent N` | Number of concurrent workers |
| `--intents-per-session N` | How many intents a worker processes consecutively before resetting the session |
| `--refresh-tools` | Force-refresh runtime metadata (tools + system prompt) before start |

### `scripts/init_agents.py`

| Argument | Description |
|----------|-------------|
| `--config` | Config file path (default `config/config.yaml`) |
| `--num-agents N` | Number of agents to create (defaults to `openclaw.num_workers`) |
| `--worker-prefix` | Worker agent prefix (defaults to `openclaw.worker_prefix`) |
| `--workspace-root` | Isolated workspace root (defaults to `openclaw.workspace_root`) |
| `--force-recreate` | Force-delete all worker agents and recreate them (use when the count changes) |
| `--refresh-tools` | Refresh all agents' runtime metadata after init |

---

## Common Configuration

| Key | Description |
|-----|-------------|
| `paths.intents_file` | Main task file; an intent JSONL or a query JSONL |
| `generation.intents_per_session` | How many tasks share one session before it finalizes |
| `generation.append_query_enabled` | Whether to append one query before a session finalizes |
| `generation.append_query_file` | Appended query pool file |
| `generation.max_turns` | Max User Loop turns (a fuse against infinite loops) |
| `openclaw.num_workers` | Number of concurrent workers |

Common environment variables: `INTENTS_FILE`, `INTENTS_PER_SESSION`, `APPEND_QUERY_ENABLED`, `APPEND_QUERY_FILE`, `CONCURRENT_NUM`, `OPENCLAW_MODEL_URL` / `OPENCLAW_MODEL_API_KEY` / `OPENCLAW_MODEL_NAME`, `LLM_BASE_URL` / `LLM_API_KEY` / `LLM_MODEL_NAME`.

---

## Output

| Content | Path |
|---------|------|
| Raw sessions | [`output/sessions`](output/sessions) |
| Middle format | [`output/middle_format`](output/middle_format) |
| Runtime metadata | `output/worker_snapshots/runtime_metadata/runtime_metadata.json` |
| Probe debug snapshots | `output/worker_snapshots/runtime_metadata/probe/` |
| Progress file | `output/progress.json` |
| Run summary | `output/summary.json` |

OpenAI format structure (OpenAI-style messages, plus project-specific metadata):

```
status / session_id / source_intent_ids / messages / tools / skills / final_output / metadata
```

`messages` is the key field: user messages come from the raw session message; assistant messages keep text, `tool_calls`, and `reasoning_content`; tool messages keep the tool name, `tool_call_id`, content, and success.

---

## Related Docs

- [`docs/project-architecture-and-introduction.md`](docs/project-architecture-and-introduction.md): full write-up of background, architecture, technical details, hard parts, and highlights.
- [`docs/design-decisions.md`](docs/design-decisions.md): key architectural decisions and trade-off rationale (why it's designed this way).
- [`docs/run-modes.md`](docs/run-modes.md): the three run modes, input files, and config semantics.
- [`docs/search-and-deployment.md`](docs/search-and-deployment.md): search providers, Serper, Docker, CI.
- [`data_examples/`](data_examples/): one high-quality session and its corresponding OpenAI format example.

---

## Citation

If you use ISE-Trace, the ISE paradigm, or the ISETrace dataset, please cite:

```bibtex
@article{isetrace2026,
  title   = {From Intent to Trajectory: Execution-Grounded Multi-Turn Data Synthesis for OS Agents},
  author  = {Valiere01},
  journal = {arXiv preprint arXiv:2606.11520},
  year    = {2026},
  url     = {https://arxiv.org/abs/2606.11520}
}
```

---

## License

Code in this project is released under the [MIT License](LICENSE). The ISETrace dataset is distributed separately; see the dataset card for its terms.
