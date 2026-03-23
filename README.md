# OpenClaw Gen Data

基于本地 OpenClaw agent 自动生成 trajectory 数据，并转换为训练使用的 middle format。

## 项目简介

这个项目的目标是：

- 从 [data_examples/intents.jsonl](data_examples/intents.jsonl) 读取 user intent
- 使用 user loop 持续与 OpenClaw 交互，直到任务完成或达到保险轮次
- 保存完整 session 轨迹
- 转换为训练中间格式，参考 [data_examples/middle_format_data.json](data_examples/middle_format_data.json)
- 支持并发执行与断点续跑

当前实现基于 OpenClaw CLI 和本地 session 文件，而不是直接依赖 HTTP API。

## 核心特性

- 单一入口脚本：[scripts/run_generation.py](scripts/run_generation.py)
- worker-agent 绑定模型：多个 worker 并发，同一个 worker 内串行复用 agent
- 独立 workspace：每个 worker agent 使用自己的隔离 workspace，避免文件操作互相冲突
- 自动 resume：基于 [output/progress.json](output/progress.json) 跳过已成功 intent
- 自动 tools catalog：优先读取缓存，不存在时自动生成
- 自动 session 归档：每条 intent 的原始 session 会保存到 [output/sessions](output/sessions)
- middle format 转换：输出到 [output/middle_format](output/middle_format)

## 目录结构

- [config](config)：配置文件
- [data_examples](data_examples)：输入与输出示例
- [docs](docs)：设计文档
- [scripts](scripts)：运行脚本
- [src](src)：核心实现
- [tools/fetch_tools](tools/fetch_tools)：tools 提取脚本
- [output](output)：运行输出

关键文件：

- [scripts/run_generation.py](scripts/run_generation.py)：主流程，负责生成、归档、转换、resume
- [scripts/init_agents.py](scripts/init_agents.py)：初始化 worker agents
- [src/openclaw_wrapper.py](src/openclaw_wrapper.py)：OpenClaw CLI 与 session 管理
- [src/llm_client.py](src/llm_client.py)：生成下一轮 query
- [src/converter.py](src/converter.py)：session 转 middle format
- [tools/fetch_tools/dump_tools.mjs](tools/fetch_tools/dump_tools.mjs)：提取完整 tools catalog

## 环境要求

- Python 3.9+
- Node.js
- 已安装并可直接调用的 `openclaw`
- 可用的 LLM 服务（兼容 OpenAI SDK）

## 安装

1. 激活 Python 环境

```bash
conda activate dev
```

2. 安装依赖

```bash
pip install -r requirements.txt
```

## 配置

复制配置模板：

```bash
cp config/config.yaml.example config/config.yaml
```

核心配置位于 [config/config.yaml](config/config.yaml)。

示例：

```yaml
openclaw:
  base_agent: "main"
  worker_prefix: "gendata-worker"
  workspace_root: "~/.openclaw/workspaces"
  num_workers: 30
  thinking: "off"

llm:
  base_url: "http://your-llm-endpoint/v1"
  api_key: "your-api-key"
  model: "your-model"
  temperature: 0.7

generation:
  max_turns: 20
  timeout: 600

paths:
  intents_file: "data_examples/intents.jsonl"
  output_dir: "output"
  sessions_dir: "output/sessions"
  middle_format_dir: "output/middle_format"
  progress_file: "output/progress.json"
  logs_dir: "output/logs"
  tools_cache_file: "output/tools/openclaw_all_tools.json"
```

配置说明：

- `openclaw.worker_prefix`：worker agent 前缀，例如 `gendata-worker-1`
- `openclaw.workspace_root`：worker 独立 workspace 根目录，每个 agent 会使用 `<workspace_root>/<agent_id>`
- `openclaw.num_workers`：默认并发 worker 数
- `openclaw.thinking`：OpenClaw thinking 级别
- `generation.max_turns`：保险轮次，避免死循环
- `generation.timeout`：单次 OpenClaw 调用超时
- `paths.tools_cache_file`：完整 tools catalog 缓存文件位置

## 使用方式

### 1. 初始化 agents

创建 agents 并配置工具（默认配置19个内置工具），默认生成工具列表：

```bash
python scripts/init_agents.py --num-agents 30
```

创建 agents + 生成工具列表：

```bash
python scripts/init_agents.py --num-agents 30 --refresh-tools
```

强制删除并重建所有 worker agents：

```bash
python scripts/init_agents.py --num-agents 30 --force-recreate
```

刷新单个 agent 的工具列表：

```bash
python scripts/init_agents.py --refresh-agent gendata-worker-1
```

### 2. 正式运行

按配置中的默认并发运行：

```bash
python scripts/run_generation.py
```

指定并发数：

```bash
python scripts/run_generation.py --concurrent 30
```

只跑前 10 条 intent：

```bash
python scripts/run_generation.py --limit 10
```

启用沙箱模式运行：

```bash
python scripts/run_generation.py --concurrent 30 --enable-sandbox
```

刷新工具列表后运行：

```bash
python scripts/run_generation.py --refresh-tools --limit 1
```

完整命令（沙箱 + 刷新工具 + 并发）：

```bash
python scripts/run_generation.py --enable-sandbox --refresh-tools --concurrent 30
```

## 运行流程

主流程如下：

1. 读取配置
2. 确保 worker agents 存在
3. 检查这些 agents 是否使用隔离 workspace
4. 加载或生成完整 tools catalog
5. 读取 intents
6. 根据 progress 文件过滤已完成任务
7. 每个 worker 绑定一个固定 agent，并发消费 intent 队列
8. 对每条 intent：
   - reset 当前 agent 的 main session
   - 调用 LLM 生成下一条 query
   - 调用 OpenClaw 执行一轮交互
   - 重复直到完成或达到 `max_turns`
   - 归档 session 文件
   - 转换 middle format
   - 再次 reset session

## 输出说明

### Session 归档

归档后的原始 session 文件保存在 [output/sessions](output/sessions)。

文件名示例：

- `intent_123__gendata-worker-1__<session_id>.jsonl`

### Middle Format

转换后的数据保存在 [output/middle_format](output/middle_format)。

每条 intent 对应一个 JSON 文件。

### 进度文件

进度文件位于 [output/progress.json](output/progress.json)。

它用于：

- 记录每条 intent 的状态
- 下次运行时自动 resume
- 跳过已成功完成的 intent

### Tools Catalog

完整 tools catalog 缓存位于 [output/tools/openclaw_all_tools.json](output/tools/openclaw_all_tools.json)。

默认行为：

- 如果缓存存在，直接读取
- 如果缓存不存在，自动调用 [tools/fetch_tools/dump_tools.mjs](tools/fetch_tools/dump_tools.mjs) 生成
- 如果生成失败，则退回 session 元数据兜底

## 关于 tools 与 skills

### tools

`tools` 字段优先使用完整 catalog，这样能拿到更完整的：

- tool name
- description
- parameters schema

当前 [tools/fetch_tools/dump_tools.mjs](tools/fetch_tools/dump_tools.mjs) 已经改成动态发现当前 OpenClaw 工具，而不是仅依赖固定列表。

不过需要注意：少数工具本身是运行时动态拼 schema，静态提取可能仍然不完整，这时会退回兜底策略。

### skills

`skills` 字段来自当前 session 对应的 `skillsSnapshot`，用于保存这次运行时 agent 可见的技能信息。

## 设计说明

### 为什么使用 CLI 而不是 HTTP API

- OpenClaw 当前最稳定、最贴近真实运行状态的是 CLI + 本地 session
- session 文件天然可归档，便于后处理
- `openclaw agent --json` 已经能返回足够的运行元数据

### 为什么不是并发复用同一个 agent

因为同一个 agent 的 main session 会冲突。

当前实现采用：

- 整体并发 = worker 数
- 一个 worker 对应一个 agent
- 同一个 worker 内串行处理多条 intent

这样既保留并发能力，又避免 session 串线。

### 为什么还保留 `max_turns`

`max_turns` 是保险丝，不是主要停止机制。

真正的停止逻辑由 user loop 判断是否完成；`max_turns` 只是用来防止异常情况下进入死循环。

## 常见问题

### 1. 为什么 `--session-id` 不能可靠复用会话

在当前环境中，`openclaw agent --session-id ...` 并不会稳定切换到指定 session，因此项目采用“worker main session + 显式 reset”的方式管理会话。

### 2. 为什么每个 agent 必须使用独立 workspace

因为多个 agent 可能执行文件读写、生成脚本、落临时文件或修改相对路径下的内容。共用 workspace 时，这些行为会互相覆盖或污染结果。

当前实现会要求每个 worker agent 使用独立目录；如果发现已有 agent 仍然指向共享 workspace，主流程会直接报错，避免在不安全状态下运行。

### 3. 为什么 tools 有时不完整

原因通常有三种：

- tools catalog 缓存不存在且自动生成失败
- 当前 OpenClaw 版本中某些工具 schema 是运行时动态拼出来的
- 插件工具定义位于额外扩展目录，需要脚本额外扫描

建议先执行：

```bash
python scripts/init_agents.py --num-agents 30 --refresh-tools
```

或运行时刷新：

```bash
python scripts/run_generation.py --refresh-tools --limit 1
```

### 4. 为什么可以直接 resume

因为 [output/progress.json](output/progress.json) 会记录已完成 intent；再次运行时会自动过滤成功项。

## 相关文档

- [docs/raw_design.txt](docs/raw_design.txt)：原始设计思路
- [docs/plan.md](docs/plan.md)：历史开发计划
- [data_examples/middle_format_data.json](data_examples/middle_format_data.json)：middle format 示例

## 当前状态

- 主流程可用
- 自动 tools catalog 已接入主流程
- session 归档与 middle format 转换已接通
- README 与当前实现已基本对齐

如果你接下来要继续完善，优先建议关注：

- `message / cron / web_fetch` 这类运行时动态 schema 工具的进一步补齐
- 更细的错误重试与失败归因
- 生成结果质量评估
