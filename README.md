<div align="center">

# openclaw_gen_data

**ISE 流水线的 Stage 2 + 3：多轮模拟 + 真实执行落地。**

*驱动本地 OpenClaw agent 完成 role-locked 多轮交互，把每一次工具调用都放进真实 OS workspace 里跑，归档完整 session 轨迹并转换为训练用 OpenAI format。*

简体中文 · [English](README_en.md)

[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-blue.svg)](#)

</div>

> **论文（Paper）:** coming soon
>
> **隶属项目（umbrella）:** [ISE-Trace](https://github.com/Valiere01/ISE-Trace) — *Intent → Simulate → Execute*
>
> **上游（Stage 1，意图构造）:** https://github.com/NairongZheng/intent_creator
>
> **数据集 ISETrace:** coming soon

---

## 在 ISE 流水线中的位置

`openclaw_gen_data` 不是孤立项目，它是 [ISE-Trace](https://github.com/Valiere01/ISE-Trace) 总入口（umbrella）下的一个阶段。整条流水线按阶段拆成两个子仓库：

```
   intent_creator              openclaw_gen_data
  +-------------------+        +-------------------+        +-----------+
  | [1] Intent        | intents| [2] Simulate      |        |           |
  |                   | .jsonl | [3] Execute       |        |  ISETrace |
  | Persona x Domain  |------->| role-locked sim   |------->|  23,132   |
  | x Task x Complex  |        | + real OS exec    |        |  轨迹     |
  +-------------------+        +-------------------+        +-----------+
        Stage I                   Stage S + E                  output
```

> **[1] Intent** — `intent_creator`：在 `Persona x Domain x Task x Complexity` 上采样 4D 结构化意图。
> **[2] Simulate** + **[3] Execute** — `openclaw_gen_data`（本仓库）：role-locked 多轮模拟，每个工具调用在真实 OS 上隔离执行。
> 产出 **ISETrace**：23,132 条多轮、执行落地的轨迹。

- **输入**：`intent_creator` 在 `Persona × Domain × Task × Complexity` 上采样得到的结构化意图（JSONL）。
- **输出**：完整 session 原始轨迹 + 训练 OpenAI format，汇入数据集 **ISETrace**。

---

## 概述

本仓库是 **ISE**（**I**ntent → **S**imulate → **E**xecute）三阶段范式中的 **Stage 2 + Stage 3**。它接收上游 `intent_creator` 产出的结构化意图，完成下面这条链路的工程化、自动化、可恢复化：

```
结构化用户意图  →  多轮 Agent 交互（模拟用户）  →  原始 session 轨迹  →  训练 OpenAI format
```

它解决的不是「单次调一个 Agent 完成任务」，而是**批量构造高质量、真实落地的多轮 trajectory 数据**。与多数「从 API 目录反推任务、单轮、模拟工具调用」的合成流程不同，本管线强调：

- **真实模拟用户**：由一个外部 LLM 扮演 role-locked 用户模拟器，逐轮决定「下一条 query 是什么 / 任务是否完成」，而非把整段意图一次性丢给 Agent。
- **真实执行落地**：每一次工具调用都在隔离的真实 OS workspace 里执行，保留真实的「失败 → 恢复」动态，而非伪造的工具响应。
- **真实运行时保真**：捕获 OpenClaw 最终**外发给模型**的工具定义（`tools` + `system_prompt`），避免「静态扫描」与「真实运行时」不一致。
- **规模化稳定**：多 worker 并发、进度文件 resume、worker runtime snapshot、session 延迟收口、OpenClaw runtime 自愈与自动重启。

主入口是 [`scripts/run_generation.py`](scripts/run_generation.py)。

---

## 双模型职责分离

系统中存在两类模型调用，职责严格分离：

| 角色 | 模型 | 职责 | 配置段 |
|------|------|------|--------|
| **执行模型** | OpenClaw 底层模型 | 实际驱动 OpenClaw agent 执行任务、调用工具 | `openclaw.*` |
| **用户模拟器** | 外部 `LLMClient` | 高层 query 调度：决定下一条 query、判定任务是否完成 | `llm.*` |

---

## 架构

逻辑上拆为 6 层：

| 层 | 职责 | 核心文件 |
|----|------|----------|
| 输入层 | 读取并标准化任务（`intent` / `direct_query`） | `src/intent_loader.py` |
| 调度层 | orchestrate 全局流程、构建任务队列、汇总、触发恢复 | `scripts/run_generation.py`, `src/generation_support.py` |
| 决策层（User Loop） | 模拟用户推进任务，逐轮生成 query / 判定完成 | `src/llm_client.py`, `prompts/user_model_system_prompt.txt` |
| 执行层 | 与 OpenClaw agent 真实交互、session 重置/归档/恢复、runtime probe | `src/openclaw_wrapper.py`, `scripts/init_agents.py` |
| 恢复层 | worker snapshot、pending session 恢复、配置 baseline 回滚、gateway 重启 | `src/worker_snapshot.py`, `src/agent_runtime.py`, `src/runtime_recovery.py`, `src/fs_utils.py` |
| 转换层 | 原始 session → 训练 OpenAI format | `src/session_parser.py`, `src/converter.py` |

完整设计见 [`docs/project-architecture-and-introduction.md`](docs/project-architecture-and-introduction.md)。

---

## 快速开始

下面这套流程适用于：

- 你已经在本机安装并初始化过 `openclaw`
- 你希望直接在当前 Python 环境里运行，而不是通过 Docker 容器启动

如果你主要在容器里跑，推荐直接使用 [`scripts/start_generation_in_container.sh`](scripts/start_generation_in_container.sh)，并参考 [`docs/search-and-deployment.md`](docs/search-and-deployment.md)。

### 1. 安装依赖

```bash
pip install -r requirements.txt
cp config/config.yaml.example config/config.yaml
```

> 建议在独立的虚拟环境（venv / conda）中安装，需要 Python 3.10+。

### 2. 准备配置

最常用的几个配置：

```yaml
generation:
  intents_per_session: "${INTENTS_PER_SESSION:-1}"
  append_query_enabled: "${APPEND_QUERY_ENABLED:-false}"
  append_query_file: "${APPEND_QUERY_FILE:-}"

paths:
  intents_file: "${INTENTS_FILE:-data_examples/intents.jsonl}"
```

运行时参数优先级：**ENV > CLI > config**。

至少要填的是两套模型端点（执行模型 + 用户模拟器），可走环境变量：

```bash
# OpenClaw 底层执行模型
export OPENCLAW_MODEL_URL=...      # OpenAI / Anthropic 兼容端点
export OPENCLAW_MODEL_API_KEY=...
export OPENCLAW_MODEL_NAME=...

# 用户模拟器（User Loop）
export LLM_BASE_URL=...
export LLM_API_KEY=...
export LLM_MODEL_NAME=...
```

如果要启用搜索能力，还需额外提供 `OPENCLAW_SEARCH_PROVIDER` / `OPENCLAW_SEARCH_API_KEY` / `OPENCLAW_SEARCH_BASE_URL`。

如果你在容器中手动启动 `openclaw gateway run`，建议确保 `~/.openclaw/openclaw.json` 中有：

```json
{
  "discovery": {
    "mdns": {
      "mode": "off"
    }
  }
}
```

这是为了避免某些长 hostname 容器里 `gateway` 因 mDNS 广播名超长而启动后立刻崩溃。

### 3. 初始化 agents

```bash
python scripts/init_agents.py --num-agents 4 --force-recreate --refresh-tools
```

说明：

- `--refresh-tools` 会在初始化阶段发起一次短生命周期 runtime probe，捕获 OpenClaw **真实发给模型**的共享 runtime metadata（`tools` + `system_prompt`）。
- 默认写入 `output/worker_snapshots/runtime_metadata/runtime_metadata.json`。
- probe 调试快照会额外写入 `output/worker_snapshots/runtime_metadata/probe/`。
- 更详细的工具提取说明见 [`tools/tool-inspector/README.md`](tools/tool-inspector/README.md)。

### 4. 开始运行

```bash
python scripts/run_generation.py --concurrent 4
```

如果你希望用容器启动整套流程，见 [`docs/search-and-deployment.md`](docs/search-and-deployment.md) 中的 Docker 示例。

---

## 运行模式

| 模式 | 适用场景 | 关键配置 |
|------|----------|----------|
| intent 模式 | 有 `natural_language_intent` 的任务，走 LLM user loop 多轮驱动 | `INTENTS_FILE` |
| query 模式 | 有 `query`/`question` 的任务，直接发给 OpenClaw | `INTENTS_FILE`，`APPEND_QUERY_ENABLED=false` |
| intent + 收口 query | intent 任务 + 每个 session 收口前追加 1 条 query | `APPEND_QUERY_ENABLED=true`，`APPEND_QUERY_FILE` |

详细说明见 [`docs/run-modes.md`](docs/run-modes.md)。

---

## 命令行参数

### `scripts/run_generation.py`

| 参数 | 说明 |
|------|------|
| `--config` | 配置文件路径 |
| `--intents-file` | 覆盖配置中的 intents 文件路径 |
| `--limit N` | 仅处理前 N 条任务（调试用） |
| `--concurrent N` | 并发 worker 数 |
| `--intents-per-session N` | 每个 worker 连续处理多少个 intent 后再重置 session |
| `--refresh-tools` | 启动前强制刷新运行时 metadata（tools + system prompt） |

### `scripts/init_agents.py`

| 参数 | 说明 |
|------|------|
| `--config` | 配置文件路径（默认 `config/config.yaml`） |
| `--num-agents N` | 要创建的 agent 数量（默认读取 `openclaw.num_workers`） |
| `--worker-prefix` | worker agent 前缀（默认读取 `openclaw.worker_prefix`） |
| `--workspace-root` | 隔离 workspace 根目录（默认读取 `openclaw.workspace_root`） |
| `--force-recreate` | 强制删除所有 worker agents 并重新创建（数量变化时使用） |
| `--refresh-tools` | 初始化后刷新所有 agent 的运行时 metadata |

---

## 常用配置

| 配置项 | 说明 |
|--------|------|
| `paths.intents_file` | 主任务文件，可为 intent JSONL 或 query JSONL |
| `generation.intents_per_session` | 多少条 task 共用一个 session 后收口 |
| `generation.append_query_enabled` | 是否在 session 收口前追加 1 条 query |
| `generation.append_query_file` | 追加 query 池文件 |
| `generation.max_turns` | User Loop 最大交互轮次（防死循环保险丝） |
| `openclaw.num_workers` | 并发 worker 数 |

常用环境变量：`INTENTS_FILE`、`INTENTS_PER_SESSION`、`APPEND_QUERY_ENABLED`、`APPEND_QUERY_FILE`、`CONCURRENT_NUM`、`OPENCLAW_MODEL_URL` / `OPENCLAW_MODEL_API_KEY` / `OPENCLAW_MODEL_NAME`、`LLM_BASE_URL` / `LLM_API_KEY` / `LLM_MODEL_NAME`。

---

## 输出

| 内容 | 路径 |
|------|------|
| 原始 session | [`output/sessions`](output/sessions) |
| middle format | [`output/middle_format`](output/middle_format) |
| runtime metadata | `output/worker_snapshots/runtime_metadata/runtime_metadata.json` |
| probe 调试快照 | `output/worker_snapshots/runtime_metadata/probe/` |
| 进度文件 | `output/progress.json` |
| 运行汇总 | `output/summary.json` |

OpenAI format 输出结构（OpenAI 风格消息，并保留项目特定元数据）：

```
status / session_id / source_intent_ids / messages / tools / skills / final_output / metadata
```

其中 `messages` 最关键：user 消息来自 session 原始 message；assistant 消息保留文本、`tool_calls`、`reasoning_content`；tool 消息保留工具名、`tool_call_id`、content、success。

---

## 相关文档

- [`docs/project-architecture-and-introduction.md`](docs/project-architecture-and-introduction.md)：项目背景、架构、技术细节、难点与亮点的完整介绍。
- [`docs/run-modes.md`](docs/run-modes.md)：三种运行模式、输入文件和配置语义。
- [`docs/search-and-deployment.md`](docs/search-and-deployment.md)：搜索 provider、Serper、Docker、CI。
- [`data_examples/`](data_examples/)：一条高质量 session 与其对应 OpenAI format 的示例。

---

## Citation

如果你使用了 ISE-Trace、ISE 范式或 ISETrace 数据集，请引用：

```bibtex
@misc{isetrace2026,
  title        = {From Intent to Trajectory: Execution-Grounded Multi-Turn Data Synthesis for OS Agents},
  author       = {Valiere01},
  year         = {2026},
  howpublished = {\url{https://github.com/Valiere01/ISE-Trace}},
  note         = {Paper coming soon}
}
```

---

## License

本项目代码以 [MIT License](LICENSE) 发布。ISETrace 数据集单独分发，其许可条款见数据集卡片。
