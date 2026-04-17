# OpenClaw Gen Data

基于本地 OpenClaw agent 自动生成 trajectory 数据，并转换为训练使用的 middle format。

## 这是什么

这个项目用于：

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

## 三种运行模式

### 1. 普通 intent 模式

主输入文件放在 `paths.intents_file`，每行至少包含 `natural_language_intent`。

示例：

```jsonl
{"id": "intent_1", "natural_language_intent": "帮我总结最近的 AI 新闻"}
```

运行命令：

```bash
INTENTS_FILE=data/intents.jsonl \
python scripts/run_generation.py --concurrent 4
```

### 2. 纯 query 模式

如果你不想跑 intent，只想直接跑 search query，也已经支持。

这时仍然使用 `paths.intents_file`，但文件内容改成 query 数据。支持：

```jsonl
{"id": "query_1", "query": "2025 年最值得关注的 AI Agent 产品有哪些？"}
{"id": "query_2", "question": "2025 年最强多模态模型有哪些？", "answer": "可选参考答案"}
```

运行命令：

```bash
INTENTS_FILE=data/merged_data_sample_20.jsonl \
APPEND_QUERY_ENABLED=false \
python scripts/run_generation.py --concurrent 4
```

说明：

- `query` / `question` 会被归一化为 `direct_query`
- 不走 LLM user loop
- 直接把 query 发给 OpenClaw
- 仍然会归档 session，并产出 middle format

### 3. intent + 收口追加 query 模式

如果你想：

- 主文件跑普通 intents
- 每个 session 正常收口时，再追加 1 条 query

那么需要两个文件：

- `paths.intents_file`：intent 文件
- `generation.append_query_file`：query 池文件

配置示例：

```yaml
generation:
  intents_per_session: 3
  append_query_enabled: true
  append_query_file: "data/merged_data_sample_20.jsonl"

paths:
  intents_file: "data/intents.jsonl"
```

运行命令：

```bash
INTENTS_FILE=data/intents.jsonl \
INTENTS_PER_SESSION=3 \
APPEND_QUERY_ENABLED=true \
APPEND_QUERY_FILE=data/merged_data_sample_20.jsonl \
python scripts/run_generation.py --concurrent 4
```

当前语义很简单：

- 只要 session 正常 finalize
- 且 `append_query_enabled=true`
- 且 `append_query_file` 非空
- 就会在收口前固定追加 1 条 query

## 输入格式

主流程会自动把输入规范成 task：

- 有 `natural_language_intent`：当作 `intent`
- 有 `query` 或 `question`：当作 `direct_query`
- `question/answer` 数据可直接使用
- 没有显式 `id` 时会自动生成稳定 id

这意味着像 [data_examples/queries.jsonl](data_examples/queries.jsonl) 这类 search 数据可以直接跑，不需要额外转换。

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
- [docs/raw_design.txt](docs/raw_design.txt)：原始设计记录
- [docs/plan.md](docs/plan.md)：历史开发计划
- [data_examples/safety_compliance_audit_middle_format.json](data_examples/safety_compliance_audit_middle_format.json)：middle format 示例