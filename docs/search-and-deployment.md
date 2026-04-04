# 搜索、容器与部署说明

本文汇总 README 中不适合放在首页的运行细节，包括搜索 provider、Serper 插件、Docker 与 CI。

## 运行时配置

当前推荐通过 [scripts/start_generation_in_container.sh](../scripts/start_generation_in_container.sh) 在 `init_agents` 完成后、gateway 启动前 patch `~/.openclaw/openclaw.json`。

### 搜索配置

只要同时提供以下三个变量，脚本就会写入搜索配置：

- `OPENCLAW_SEARCH_PROVIDER`
- `OPENCLAW_SEARCH_API_KEY`
- `OPENCLAW_SEARCH_BASE_URL`

如果三者没有给全，就跳过搜索配置，不修改现有 search provider。

常见示例：

```bash
export OPENCLAW_SEARCH_PROVIDER="kimi"
export OPENCLAW_SEARCH_API_KEY="sk-xxx"
export OPENCLAW_SEARCH_BASE_URL="https://api.moonshot.cn/v1"
```

### Discovery 配置

容器场景下，启动脚本默认还会写入：

- `discovery.mdns.mode=off`

这是为了避免某些 Docker / Kubernetes 环境里 hostname 过长时，OpenClaw 在 browser control 的 mDNS/Bonjour 广播阶段触发 `Label cannot be longer than 63 bytes` 并导致 gateway 启动后立刻崩溃。

也可以显式覆盖：

- `OPENCLAW_DISCOVERY_MDNS_MODE`（容器内默认 `off`）

## Serper 插件

仓库内已经提供外部插件 [openclaw_plugins/serper](../openclaw_plugins/serper)。

启用方式：

```bash
export OPENCLAW_SEARCH_PROVIDER="serper"
export OPENCLAW_SEARCH_API_KEY="your-serper-api-key"
export OPENCLAW_SEARCH_BASE_URL="https://google.serper.dev"
```

说明：

- 不需要额外的 Serper 专用环境变量
- 启动脚本会自动写入插件加载路径和 `plugins.entries.serper.config.webSearch`
- 不需要修改 OpenClaw 主仓库源码

## Serper 测试

静态检查：

```bash
/Users/zhengnairong/miniconda3/envs/dev/bin/python tests/test_serper_plugin.py
```

或：

```bash
/Users/zhengnairong/miniconda3/envs/dev/bin/python -m pytest -q tests/test_serper_plugin.py
```

live smoke test：

```bash
OPENCLAW_SEARCH_PROVIDER=serper \
OPENCLAW_SEARCH_API_KEY=your-serper-api-key \
OPENCLAW_SEARCH_BASE_URL=https://google.serper.dev \
/Users/zhengnairong/miniconda3/envs/dev/bin/python tests/test_serper_plugin.py --live
```

## Docker

如果你更希望直接拿一个可用环境，而不是手动在本机配 Python / Node / OpenClaw，可以使用仓库里的 [Dockerfile](../Dockerfile)。

镜像会预装：

- Ubuntu 22.04
- Node.js 24
- Miniconda + `dev` Python 3.12 环境
- [requirements.txt](../requirements.txt) 中的 Python 依赖
- `openclaw`
- 常用开发 / 排障工具

### 本地构建 arm64

```bash
docker buildx build --platform linux/arm64 -t openclaw-gen-data:arm64 --load .
```

### 本地构建 amd64

```bash
docker buildx build --platform linux/amd64 -t openclaw-gen-data:amd64 --load .
```

### 容器内运行 generation

```bash
docker run --rm -it \
  --hostname openclaw \
  -v /local_path/to/config.yaml:/tmp/config.yaml:ro \
  -v /local_path/to/intents.jsonl:/tmp/intents.jsonl:ro \
  -v /local_path/to/output:/tmp/output \
  -e CONFIG_PATH=/tmp/config.yaml \
  -e OUTPUT_DIR=/tmp/output \
  -e INTENTS_FILE=/tmp/intents.jsonl \
  -e CONCURRENT_NUM=3 \
  -e OPENCLAW_MODEL_URL=https://dashscope.aliyuncs.com/compatible-mode/v1 \
  -e OPENCLAW_MODEL_API_KEY=sk-xxx \
  -e OPENCLAW_MODEL_NAME=qwen3.5-plus \
  -e LLM_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1 \
  -e LLM_API_KEY=sk-xxx \
  -e LLM_MODEL_NAME=qwen3.5-plus \
  -e OPENCLAW_SEARCH_PROVIDER=serper \
  -e OPENCLAW_SEARCH_API_KEY=your-serper-api-key \
  -e OPENCLAW_SEARCH_BASE_URL=https://google.serper.dev \
  openclaw-gen-data:amd64 \
  /workspace/scripts/start_generation_in_container.sh
```

说明：

- 当前启动脚本默认会写入 `discovery.mdns.mode=off`，因此通常不再需要依赖短 hostname 来规避这个问题
- 如果你是手动运行原生 `openclaw gateway run`，仍然建议显式传 `--hostname openclaw`（或其他较短值）
- OpenClaw 在启动 gateway 时会为本地 browser control 服务做 mDNS/Bonjour 广播，并把 hostname 拼进广播名
- 如果容器 / Pod hostname 太长（例如某些自动生成的 K8s Pod 名），会触发 `Label cannot be longer than 63 bytes`，导致 gateway 刚启动就崩溃；关闭 `discovery.mdns` 可以规避

## CI 镜像构建

当前工作流文件见 [.github/workflows/docker-image.yml](../.github/workflows/docker-image.yml)。

触发规则：

- PR：只做 `arm64` + `amd64` 构建校验，不推送
- push 到 `main` / `master`：只做 `arm64` + `amd64` 构建校验，不推送
- `workflow_dispatch`：手动触发后，直接使用 GitHub UI 选中的分支，并按需推送 `arm64`、`amd64`

### 推荐用法

1. 进入 GitHub 仓库的 `Actions`
2. 打开 `docker-image`
3. 在页面右上角先选好要发布的分支
4. 点击 `Run workflow`
5. 填写：
  - `image_tag`：例如 `test-0403`
  - `openclaw_version`：按需填写
  - `push_arm64` / `push_amd64`：按需选择
6. 运行完成后，从镜像仓库拉取对应 tag

工作流会自动使用当前选中的分支进行 checkout，并把分支名带进镜像 tag。

例如如果你选择的是 `dev_damon`，并填写 `image_tag=test-0403`，则会生成：

- `test-0403-dev_damon-arm64`
- `test-0403-dev_damon-amd64`

依赖的 GitHub Secrets：

- `ALIYUN_REGISTRY`
- `ALIYUN_NAMESPACE`
- `ALIYUN_USERNAME`
- `ALIYUN_PASSWORD`
