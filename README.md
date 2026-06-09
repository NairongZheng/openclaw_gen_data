# OpenClaw Gen Data

基于本地 OpenClaw agent 自动生成 trajectory 数据，并转换为标准的 openai format (middle format)。

- 读取 task 数据（intent 或 query）
- 驱动 OpenClaw agent 执行交互
- 归档完整 session 轨迹
- 转换为训练用 middle format
- 支持并发、resume、worker workspace 隔离

主入口是 [scripts/run_generation.py](scripts/run_generation.py)。

## 快速开始

下面这套快速开始适用于：

- 你已经在本机安装并初始化过 `openclaw`
- 你希望直接在当前 Python 环境里运行，而不是通过 Docker 容器启动

如果你主要在容器里跑，推荐直接使用 [scripts/start_generation_in_container.sh](scripts/start_generation_in_container.sh)，并参考 [docs/search-and-deployment.md](docs/search-and-deployment.md)。

1. 安装依赖

```bash
conda activate dev
pip install -r requirements.txt
cp config/config.yaml.example config/config.yaml
```

2. 准备配置

最常用的几个配置是：

```yaml
generation:
  intents_per_session: "${INTENTS_PER_SESSION:-1}"
  append_query_enabled: "${APPEND_QUERY_ENABLED:-false}"
  append_query_file: "${APPEND_QUERY_FILE:-}"

paths:
  intents_file: "${INTENTS_FILE:-data_examples/intents.jsonl}"
```

运行时优先级：`ENV > CLI > config`

如果你要启用搜索能力，还需要额外提供：

- `OPENCLAW_SEARCH_PROVIDER`
- `OPENCLAW_SEARCH_API_KEY`
- `OPENCLAW_SEARCH_BASE_URL`

如果你是在容器中手动启动 `openclaw gateway run`，还建议确保 `~/.openclaw/openclaw.json` 中有：

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

3. 初始化 agents

```bash
python scripts/init_agents.py --num-agents 4 --force-recreate --refresh-tools
```

说明：

- `--refresh-tools` 会在初始化阶段发起一次短生命周期 runtime probe，捕获 OpenClaw **真实发给模型** 的共享 runtime metadata（`tools` + `system_prompt`）
- 默认写入 `output/worker_snapshots/runtime_metadata/runtime_metadata.json`
- probe 调试快照会额外写入 `output/worker_snapshots/runtime_metadata/probe/`
- 更详细的工具提取说明见 [tools/tool-inspector/README.md](tools/tool-inspector/README.md)

1. 开始运行

```bash
python scripts/run_generation.py --concurrent 4
```

如果你希望用容器启动整套流程，见 [docs/search-and-deployment.md](docs/search-and-deployment.md) 中的 Docker 示例。

## 运行模式

| 模式 | 适用场景 | 关键配置 |
|------|----------|----------|
| intent 模式 | 有 `natural_language_intent` 的任务，走 LLM user loop 多轮驱动 | `INTENTS_FILE` |
| query 模式 | 有 `query`/`question` 的任务，直接发给 OpenClaw | `INTENTS_FILE`，`APPEND_QUERY_ENABLED=false` |
| intent + 收口 query | intent 任务 + 每个 session 收口前追加 1 条 query | `APPEND_QUERY_ENABLED=true`，`APPEND_QUERY_FILE` |

详细说明见 [docs/run-modes.md](docs/run-modes.md)。

## 常用配置

- `paths.intents_file`：主任务文件，可为 intent JSONL 或 query JSONL
- `generation.intents_per_session`：多少条 task 共用一个 session 后收口
- `generation.append_query_enabled`：是否在 session 收口前追加 1 条 query
- `generation.append_query_file`：追加 query 池文件
- `openclaw.num_workers`：并发 worker 数

常用环境变量：

- `INTENTS_FILE`
- `INTENTS_PER_SESSION`
- `APPEND_QUERY_ENABLED`
- `APPEND_QUERY_FILE`
- `CONCURRENT_NUM`
- `OPENCLAW_MODEL_URL` / `OPENCLAW_MODEL_API_KEY` / `OPENCLAW_MODEL_NAME`
- `LLM_BASE_URL` / `LLM_API_KEY` / `LLM_MODEL_NAME`

更完整的运行模式说明见 [docs/run-modes.md](docs/run-modes.md)。

## 输出

- 原始 session：保存在 [output/sessions](output/sessions)
- middle format：保存在 [output/middle_format](output/middle_format)
- runtime metadata：保存在 [output/worker_snapshots/runtime_metadata/runtime_metadata.json](output/worker_snapshots/runtime_metadata/runtime_metadata.json)
- probe 调试快照：保存在 [output/worker_snapshots/runtime_metadata/probe](output/worker_snapshots/runtime_metadata/probe)
- 进度文件：保存在 [output/progress.json](output/progress.json)

## 相关文档

- [docs/project-architecture-and-introduction.md](docs/project-architecture-and-introduction.md)：项目背景、架构、技术细节、难点与亮点的完整介绍
- [docs/run-modes.md](docs/run-modes.md)：三种运行模式、输入文件和配置语义
- [docs/search-and-deployment.md](docs/search-and-deployment.md)：搜索 provider、Serper、Docker、CI
- [data_examples/safety_compliance_audit_middle_format.json](data_examples/safety_compliance_audit_middle_format.json)：middle format 示例

历史设计记录：[docs/raw_design.txt](docs/raw_design.txt)、[docs/plan.md](docs/plan.md)