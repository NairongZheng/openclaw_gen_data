# 搜索、容器与部署说明

本文汇总 README 中不适合放在首页的运行细节，包括搜索 provider、Serper 插件、Docker 与 CI。

## 搜索配置

当前推荐通过 [scripts/start_generation_in_container.sh](../scripts/start_generation_in_container.sh) 在 `init_agents` 完成后、gateway 启动前 patch `~/.openclaw/openclaw.json`。

只要同时提供以下三个变量，脚本就会写入搜索配置：

- `OPENCLAW_SEARCH_PROVIDER`
- `OPENCLAW_SEARCH_API_KEY`
- `OPENCLAW_SEARCH_BASE_URL`

如果三者没有给全，就完全跳过，不修改现有 OpenClaw 配置。

常见示例：

```bash
export OPENCLAW_SEARCH_PROVIDER="kimi"
export OPENCLAW_SEARCH_API_KEY="sk-xxx"
export OPENCLAW_SEARCH_BASE_URL="https://api.moonshot.cn/v1"
```

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

## CI 镜像构建

当前工作流文件见 [.github/workflows/docker-image.yml](../.github/workflows/docker-image.yml)。

触发规则：

- PR：只做 `arm64` + `amd64` 构建校验，不推送
- push 到任意分支：只做 `arm64` + `amd64` 构建校验，不推送
- `workflow_dispatch`：手动触发后，可选择推送 `arm64`、`amd64`

### 推荐用法

1. 进入 GitHub 仓库的 `Actions`
2. 打开 `docker-image`
3. 点击 `Run workflow`
4. 填写：
  - `image_tag`：例如 `test-0403`
  - `openclaw_version`：按需填写
  - `push_arm64` / `push_amd64`：按需选择
5. 运行完成后，从镜像仓库拉取对应 tag

依赖的 GitHub Secrets：

- `ALIYUN_REGISTRY`
- `ALIYUN_NAMESPACE`
- `ALIYUN_USERNAME`
- `ALIYUN_PASSWORD`
