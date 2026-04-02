# OpenClaw Gen Data

基于本地 OpenClaw agent 自动生成 trajectory 数据，并转换为训练使用的 middle format。

## 项目简介

这个项目的目标是：

- 从 [data_examples/intents.jsonl](data_examples/intents.jsonl) 读取 user intent
- 使用 user loop 持续与 OpenClaw 交互，直到任务完成或达到保险轮次
- 保存完整 session 轨迹
- 转换为训练中间格式，参考 [data_examples/safety_compliance_audit_middle_format.json](data_examples/safety_compliance_audit_middle_format.json)
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
- [tools/tool-inspector](tools/tool-inspector)：tools 提取脚本
- [output](output)：运行输出

关键文件：

- [scripts/run_generation.py](scripts/run_generation.py)：主流程，负责生成、归档、转换、resume
- [scripts/init_agents.py](scripts/init_agents.py)：初始化 worker agents、配置 model/skills/web-search、生成初始 workspace 快照
- [src/openclaw_wrapper.py](src/openclaw_wrapper.py)：OpenClaw CLI 与 session 管理
- [src/llm_client.py](src/llm_client.py)：生成下一轮 query
- [src/converter.py](src/converter.py)：session 转 middle format
- [tools/tool-inspector/dump_tools.mjs](tools/tool-inspector/dump_tools.mjs)：提取完整 tools catalog

## 环境要求

- Python 3.9+
- Node.js
- 已安装并可直接调用的 `openclaw`
- 可用的 LLM 服务（兼容 OpenAI SDK）

## 安装

如果你直接在本机运行仓库，可以按下面步骤安装。

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
  worker_prefix: "gendata-worker"
  workspace_root: "~/.openclaw/workspaces"
  num_workers: "${CONCURRENT_NUM:-30}"
  model_url: "${OPENCLAW_MODEL_URL:-https://your-openclaw-model-endpoint/v1}"
  model_api_key: "${OPENCLAW_MODEL_API_KEY:-your-openclaw-model-api-key}"
  model: "${OPENCLAW_MODEL_NAME:-your-model}"
  api: "openai-completions"
  enable_thinking: true
  thinking_level: "high"
  worker_tools_allow:
    - "read"
    - "write"
    - "edit"
    - "apply_patch"

llm:
  base_url: "${LLM_BASE_URL:-https://your-llm-endpoint/v1}"
  api_key: "${LLM_API_KEY:-your-llm-api-key}"
  model: "${LLM_MODEL_NAME:-your-model}"
  temperature: 0.7
  max_tokens: 4000
  timeout: 120
  retry_attempts: 3
  retry_base_delay: 1.0
  retry_max_delay: 8.0

generation:
  max_turns: 20
  intents_per_session: "${INTENTS_PER_SESSION:-1}"
  timeout: 600

paths:
  intents_file: "${INTENTS_FILE:-data_examples/intents.jsonl}"
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
- `openclaw.num_workers`：默认并发 worker 数，也可直接通过 `CONCURRENT_NUM` 环境变量覆盖
- `openclaw.model_url` / `openclaw.model_api_key` / `openclaw.model`：默认支持通过 `OPENCLAW_MODEL_URL` / `OPENCLAW_MODEL_API_KEY` / `OPENCLAW_MODEL_NAME` 环境变量覆盖
- `openclaw.enable_thinking`：是否开启 OpenClaw 推理模式
- `openclaw.thinking_level`：OpenClaw thinking 级别，只有开启推理模式时才生效
- `openclaw.api`：写入 OpenClaw provider 配置时使用的 API 类型
- `openclaw.worker_tools_allow`：worker agent 使用的工具 allowlist；未配置时使用代码默认值
- `llm.base_url` / `llm.api_key` / `llm.model`：默认支持通过 `LLM_BASE_URL` / `LLM_API_KEY` / `LLM_MODEL_NAME` 环境变量覆盖
- `llm.max_tokens`：user loop 调用 LLM 生成 query 的最大输出 token 数
- `llm.timeout`：user loop 调用 LLM 的请求超时
- `llm.retry_attempts`：user loop 的 user model 最大尝试次数（包含首次请求）
- `llm.retry_base_delay`：user model 重试的基础退避时间（秒）
- `llm.retry_max_delay`：user model 重试等待的上限（秒）
- `generation.max_turns`：保险轮次，避免死循环
- `generation.intents_per_session`：每个 worker 连续处理多少个 intent 后，才结束当前 session 并统一写出本批 intent 的 session/middle_format 文件
- `generation.timeout`：单次 OpenClaw 调用超时
- `paths.tools_cache_file`：完整 tools catalog 缓存文件位置

运行时常用覆盖项采用统一优先级：`ENV > CLI > config`

- `CONFIG_PATH`：覆盖配置文件路径
- `INTENTS_FILE`：覆盖 `paths.intents_file`
- `CONCURRENT_NUM`：覆盖 `openclaw.num_workers`
- `INTENTS_PER_SESSION`：覆盖 `generation.intents_per_session`

模型相关配置默认支持以下环境变量，适合容器批量覆盖：

- `OPENCLAW_MODEL_URL` / `OPENCLAW_MODEL_API_KEY` / `OPENCLAW_MODEL_NAME`：覆盖 OpenClaw provider 模型配置
- `LLM_BASE_URL` / `LLM_API_KEY` / `LLM_MODEL_NAME`：覆盖 user loop 使用的 LLM 配置

像 `INTENTS_FILE`、`CONCURRENT_NUM`、`INTENTS_PER_SESSION` 这类经常在容器里改的控制项，也已经改成直接在 `config.yaml` 中声明 env placeholder，不需要再在 `config.py` 里为每个变量单独写一段 env 覆盖逻辑。

### OpenClaw 搜索配置（可选）

现在推荐直接通过启动脚本 [scripts/start_generation_in_container.sh](scripts/start_generation_in_container.sh) 在 `init_agents` 完成后、gateway 启动前 patch `~/.openclaw/openclaw.json`。这样可以避开 `ensure_agents` 对配置的覆盖，尽量从根源上解决“search 配置刚写进去又被改掉”的问题。只有在你同时提供 `provider + apiKey + baseUrl` 这三个变量时，脚本才会写入对应配置；如果三者没有给全，就直接跳过，不改 OpenClaw 配置。

日常最常用的几项是：

```bash
export OPENCLAW_SEARCH_PROVIDER="kimi"
export OPENCLAW_SEARCH_API_KEY="sk-xxx"
export OPENCLAW_SEARCH_BASE_URL="https://api.moonshot.cn/v1"
```

说明：

- 只有这 3 个变量都提供时，才会修改 OpenClaw 配置
- 一旦触发，会自动写入：`tools.web.fetch.enabled = true`
- 一旦触发，也会自动写入：`tools.web.search = { enabled: true, provider, [provider]: { apiKey, baseUrl } }`
- 如果 3 个变量没有给全，就完全跳过，不改现有 OpenClaw 配置

补充说明：

- Kimi 如果走中国站，`baseUrl` 需要显式设成 `https://api.moonshot.cn/v1`

## 使用方式

### 1. 初始化 agents

创建 agents 并配置工具：

```bash
python scripts/init_agents.py --num-agents 60 --force-recreate --refresh-tools
    # --force-recreate：强制删除并重建所有 worker agents
    # --refresh-tools：刷新所有 agents 的工具列表
```

刷新单个 agent 的工具列表：

```bash
python scripts/init_agents.py --refresh-agent gendata-worker-1
```

初始化脚本还会额外做这些事情：

- 为新创建的 agent workspace 修改 `AGENTS.md`，补充“只在自己 workspace 工作”的约束
- 为新创建的 agent 保存初始 workspace 快照到 [output/worker_snapshots](output/worker_snapshots)
- 后续每条 intent 开始前，worker 会先从快照恢复 workspace，避免上一次任务残留文件污染结果
- 保存运行时 workspace 快照时会排除 `.git`

### 2. 正式运行

```bash
python scripts/run_generation.py --concurrent 30
    # --concurrent：指定并发数
```

按当前仓库默认约定，批量跑容器时更推荐直接用环境变量覆盖常改项；本机临时调试时再用 CLI 覆盖。

只跑前 10 条 intent：

```bash
python scripts/run_generation.py --limit 10
```

刷新工具列表后运行：

```bash
python scripts/run_generation.py --refresh-tools --limit 1
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
   - 从初始快照恢复当前 agent 的 workspace
   - reset 当前 agent 的 main session
   - 调用 LLM 生成下一条 query
   - 调用 OpenClaw 执行一轮交互
   - 重复直到完成或达到 `max_turns`
  - 若当前 session 还会继续复用，只保留内部快照，不立即写出最终产物
  - 当前 session 到达批次上限或 worker 无后续任务时，再统一归档本批 intent 的 session 文件并转换 middle format
  - 最后 reset session

## 输出说明

### Session 归档

归档后的原始 session 文件保存在 [output/sessions](output/sessions)。

文件名示例：

- `intent_123__gendata-worker-1__<session_id>.jsonl`

### Middle Format

转换后的数据保存在 [output/middle_format](output/middle_format)。

每条 intent 对应一个 JSON 文件；当 `generation.intents_per_session > 1` 时，会在该 session 批次结束后统一写出这一批 intent 的文件。

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
- 如果缓存不存在，自动调用 [tools/tool-inspector/dump_tools.mjs](tools/tool-inspector/dump_tools.mjs) 生成
- 如果生成失败，则退回 session 元数据兜底

## 关于 tools 与 skills

### tools

`tools` 字段优先使用完整 catalog，这样能拿到更完整的：

- tool name
- description
- parameters schema

当前 [tools/tool-inspector/dump_tools.mjs](tools/tool-inspector/dump_tools.mjs) 已经改成动态发现当前 OpenClaw 工具，而不是仅依赖固定列表。

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

### 5. 为什么 `web_search (kimi)` 明明配了 key 还是报错

如果你用的是 Kimi / Moonshot 的中国站 key，除了配置 `tools.web.search.kimi.apiKey`，通常还需要把：

- `tools.web.search.provider` 设成 `kimi`
- `tools.web.search.kimi.baseUrl` 设成 `https://api.moonshot.cn/v1`

否则 OpenClaw 会继续使用内置默认值 `https://api.moonshot.ai/v1`。

## 相关文档

- [docs/raw_design.txt](docs/raw_design.txt)：原始设计思路
- [docs/plan.md](docs/plan.md)：历史开发计划
- [data_examples/safety_compliance_audit_middle_format.json](data_examples/safety_compliance_audit_middle_format.json)：middle format 示例

## 当前状态

- 主流程可用
- 自动 tools catalog 已接入主流程
- session 归档与 middle format 转换已接通
- README 与当前实现已基本对齐

如果你接下来要继续完善，优先建议关注：

- `message / cron / web_fetch` 这类运行时动态 schema 工具的进一步补齐
- 更细的错误重试与失败归因
- 生成结果质量评估

## Docker 镜像

如果你更希望直接拿一个可用环境，而不是手动在本机配 Python / Node / OpenClaw，可以使用仓库里的 [Dockerfile](Dockerfile)。

镜像里会提前装好：

- Ubuntu 22.04
- Node.js 24（通过 `nvm`）
- Miniconda + `dev` Python 3.12 环境
- [requirements.txt](requirements.txt) 中的 Python 依赖
- `openclaw`
- 常用开发/排障工具（`git`、`tmux`、`htop`、`tree`、`ssh` 等）

默认会安装固定版本的 `openclaw@2026.3.24`。如果你担心新版本不兼容，可以继续用默认值；如果要切换版本，直接在构建时传 `OPENCLAW_VERSION` 即可。

### 本地构建 arm64 镜像

适合 Apple Silicon 本机或同事的 macOS：

```bash
docker buildx build --platform linux/arm64 -t openclaw-gen-data:arm64 --load .
```

指定 OpenClaw 版本示例：

```bash
docker buildx build \
  --platform linux/arm64 \
  --build-arg OPENCLAW_VERSION=2026.3.24 \
  -t openclaw-gen-data:arm64 --load .
```

### 本地构建 amd64 镜像

适合提前验证要推送到阿里云的版本：

```bash
docker buildx build --platform linux/amd64 -t openclaw-gen-data:amd64 --load .
```

如果构建环境需要代理，可追加：

```bash
docker buildx build \
  --platform linux/amd64 \
  --build-arg http_proxy=http://10.120.6.220:7890 \
  --build-arg https_proxy=http://10.120.6.220:7890 \
  --build-arg HTTP_PROXY=http://10.120.6.220:7890 \
  --build-arg HTTPS_PROXY=http://10.120.6.220:7890 \
  --build-arg OPENCLAW_VERSION=2026.3.24 \
  -t openclaw-gen-data:amd64 --load .
```

### 使用容器跑generation

如果要直接在容器里跑完整流程（初始化 agents + 生成数据），可以直接调用镜像内置脚本：

```bash
docker run --rm -it \
  -v /local_path/to/config.yaml:/tmp/config.yaml:ro \
  -v /local_path/to/intents.jsonl:/tmp/intents.jsonl:ro \
  -v /local_path/to/output:/tmp/output \
  -e CONFIG_PATH=/tmp/config.yaml \
  -e OUTPUT_DIR=/tmp/output \
  -e INTENTS_FILE=/tmp/intents.jsonl \
  -e CONCURRENT_NUM=3 \
  -e MODEL_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1 \
  -e MODEL_API_KEY=sk-xxx \
  -e MODEL_NAME=qwen3.5-plus \
  openclaw-gen-data:amd64 \
  /workspace/scripts/start_generation_in_container.sh
```

### 启动脚本参数说明

脚本 `/workspace/scripts/start_generation_in_container.sh` 支持以下环境变量：

| 变量 | 是否必须 | 说明 |
|------|----------|------|
| `OUTPUT_DIR` | **必须** | 宿主机持久化输出目录，容器内 `output/` 会软链到这里 |
| `CONFIG_PATH` | 可选 | mnt 里 `config.yaml` 的路径，脚本会自动 cp 到 `/workspace/config/config.yaml` |
| `INTENTS_FILE` | 可选 | 覆盖 `config.yaml` 里的 `paths.intents_file`，可指定任意 intents 文件 |
| `CONCURRENT_NUM` | 可选 | 并发数，默认 `3` |
| `INTENTS_PER_SESSION` | 可选 | 覆盖 `generation.intents_per_session` |
| `MODEL_BASE_URL` | 可选 | 同时覆盖 `openclaw.model_url` 和 `llm.base_url` |
| `MODEL_API_KEY` | 可选 | 同时覆盖 `openclaw.model_api_key` 和 `llm.api_key` |
| `MODEL_NAME` | 可选 | 同时覆盖 `openclaw.model` 和 `llm.model` |
| `OPENCLAW_SEARCH_PROVIDER` | 可选 | 搜索 provider；需与下面两个变量一起提供 |
| `OPENCLAW_SEARCH_API_KEY` | 可选 | 搜索 provider 对应的 API Key |
| `OPENCLAW_SEARCH_BASE_URL` | 可选 | 搜索 provider 对应的 Base URL |


> `openclaw.json` 使用镜像构建时自动初始化的配置，无需外部注入。
>
> 运行时参数优先级为 `ENV > CLI > config`。
>
> 脚本内部使用 `conda run --no-capture-output`，日志会实时打印到容器标准输出。

## CI 自动构建镜像

当前工作流只使用阿里云镜像仓库，工作流文件见 [.github/workflows/docker-image.yml](.github/workflows/docker-image.yml)。

触发规则：

- PR：只做 `arm64` + `amd64` 构建校验，不推送
- push 到 `main` / `master`：只做 `arm64` + `amd64` 构建校验，不推送
- `workflow_dispatch`：手动触发后，按你选择把 `arm64`、`amd64` 推送到阿里云

### GitHub Secrets

工作流里阿里云推送依赖这些 Secrets：

- `ALIYUN_REGISTRY`：例如 `registry.cn-hangzhou.aliyuncs.com`
- `ALIYUN_NAMESPACE`：你的命名空间
- `ALIYUN_USERNAME`：阿里云镜像仓库用户名
- `ALIYUN_PASSWORD`：阿里云镜像仓库密码或访问令牌

### 手动推送怎么用

1. 进入 GitHub 仓库的 `Actions`
2. 打开 `docker-image`
3. 点击 `Run workflow`
4. 填写参数：
   - `image_tag`：例如 `v1.0.0`、`test-0327`
  - `openclaw_version`：例如 `2026.3.24`，默认使用当前仓库固定值
   - `push_arm64`：是否推送 `arm64`
   - `push_amd64`：是否推送 `amd64`
5. 运行完成后，从阿里云仓库拉对应标签即可

### 标签与拉取示例

- `<aliyun-registry>/<namespace>/<repo>:manual-arm64`
- `<aliyun-registry>/<namespace>/<repo>:manual-amd64`
- `<aliyun-registry>/<namespace>/<repo>:v1.2.3-arm64`
- `<aliyun-registry>/<namespace>/<repo>:v1.2.3-amd64`

```bash
docker pull <aliyun-registry>/<namespace>/<repo>:v1.0.0-arm64
docker pull <aliyun-registry>/<namespace>/<repo>:v1.0.0-amd64
```

### 推荐做法

- 本地和同事的 macOS：拉阿里云里的 `*-arm64` 标签
- 阿里云或 x86 机器：拉阿里云里的 `*-amd64` 标签
- 手动触发时把 `image_tag` 设成版本号，例如 `v1.2.3`