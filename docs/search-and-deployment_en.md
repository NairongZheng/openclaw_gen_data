# Search, Container, and Deployment Notes

This document collects the runtime details from the README that are not suitable for the front page, including the search provider, the Serper plugin, Docker, and CI.

## Runtime Configuration

The current recommendation is to use [scripts/start_generation_in_container.sh](../scripts/start_generation_in_container.sh) to patch `~/.openclaw/openclaw.json` before `init_agents` runs, ensuring that what `--refresh-tools` captures is the tool definitions under the latest configuration.

### Refreshing runtime metadata

When you run:

```bash
python scripts/init_agents.py --num-agents 4 --force-recreate --refresh-tools
```

the current implementation will:

- Start a short-lived local proxy
- Create a probe agent and issue a minimal real request
- Capture the shared runtime metadata (`tools` + `system_prompt`) from OpenClaw's **final outgoing request**
- Write the aggregated result to [output/worker_snapshots/runtime_metadata/runtime_metadata.json](../output/worker_snapshots/runtime_metadata/runtime_metadata.json)
- Write the probe debug snapshots as `runtime_probe_*` under [output/worker_snapshots/runtime_metadata/probe](../output/worker_snapshots/runtime_metadata/probe)

This flow has replaced the old main path that "relied solely on static scanning to export tools"; `dump_tools.mjs` can still be used for offline inspection and reconciliation, but it is no longer the sole source for `--refresh-tools`.

### Search Configuration

As long as the following three variables are all provided, the script will write the search configuration:

- `OPENCLAW_SEARCH_PROVIDER`
- `OPENCLAW_SEARCH_API_KEY`
- `OPENCLAW_SEARCH_BASE_URL`

If not all three are given, the search configuration is skipped and the existing search provider is not modified.

A common example:

```bash
export OPENCLAW_SEARCH_PROVIDER="kimi"
export OPENCLAW_SEARCH_API_KEY="sk-xxx"
export OPENCLAW_SEARCH_BASE_URL="https://api.moonshot.cn/v1"
```

### Discovery Configuration

In the container scenario, the startup script by default also writes:

- `discovery.mdns.mode=off`

This is to avoid a situation in some Docker / Kubernetes environments where, when the hostname is too long, OpenClaw triggers `Label cannot be longer than 63 bytes` during the mDNS/Bonjour broadcast phase of browser control, causing the gateway to crash immediately after startup.

You can also override it explicitly:

- `OPENCLAW_DISCOVERY_MDNS_MODE` (defaults to `off` inside the container)

## Serper Plugin

The Serper plugin is automatically installed and enabled when the Docker image is built (see `Dockerfile`).

When the container starts, you only need to inject the API credentials via environment variables:

```bash
export OPENCLAW_SEARCH_PROVIDER="serper"
export OPENCLAW_SEARCH_API_KEY="your-serper-api-key"
export OPENCLAW_SEARCH_BASE_URL="https://google.serper.dev"
```

The startup script will automatically write the credentials into `plugins.entries.serper.config.webSearch`.

Notes:

- The Serper plugin is installed during the image build stage via `openclaw plugins install`, and is no longer loaded from a local path
- Local development (non-container) requires manually running `openclaw plugins install openclaw_plugins/serper` and `openclaw plugins enable serper`
- There is no need to modify the OpenClaw main repository source code

## Serper Testing

Static check:

```bash
pytest -q tests/test_serper_plugin.py
```

live smoke test:

```bash
OPENCLAW_SEARCH_PROVIDER=serper \
OPENCLAW_SEARCH_API_KEY=your-serper-api-key \
OPENCLAW_SEARCH_BASE_URL=https://google.serper.dev \
python tests/test_serper_plugin.py --live
```

## Docker

If you would rather just get a ready-to-use environment instead of manually setting up Python / Node / OpenClaw on your own machine, you can use the [Dockerfile](../Dockerfile) in the repository.

The image comes pre-installed with:

- Ubuntu 22.04
- Node.js 24
- Miniconda + a `dev` Python 3.12 environment
- The Python dependencies in [requirements.txt](../requirements.txt)
- `openclaw`
- Common development / troubleshooting tools

### Building arm64 locally

```bash
docker buildx build --platform linux/arm64 -t openclaw-gen-data:arm64 --load .
```

### Building amd64 locally

```bash
docker buildx build --platform linux/amd64 -t openclaw-gen-data:amd64 --load .
```

### Running generation inside the container

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

Notes:

- The current startup script by default writes `discovery.mdns.mode=off`, so you usually no longer need to rely on a short hostname to work around this issue
- If you are manually running the native `openclaw gateway run`, it is still recommended to explicitly pass `--hostname openclaw` (or another shorter value)
- When OpenClaw starts the gateway, it performs an mDNS/Bonjour broadcast for the local browser control service and concatenates the hostname into the broadcast name
- If the container / Pod hostname is too long (for example, some auto-generated K8s Pod names), it will trigger `Label cannot be longer than 63 bytes`, causing the gateway to crash right after startup; disabling `discovery.mdns` can work around this

## CI Image Build

The current workflow file is at [.github/workflows/docker-image.yml](../.github/workflows/docker-image.yml).

Trigger rules:

- PR: only performs `arm64` + `amd64` build validation, without pushing
- push to `main` / `master`: only performs `arm64` + `amd64` build validation, without pushing
- `workflow_dispatch`: after manual triggering, directly uses the branch selected in the GitHub UI, and pushes `arm64`, `amd64` as needed

### Recommended Usage

1. Go to `Actions` in the GitHub repository
2. Open `docker-image`
3. First select the branch to publish in the top-right corner of the page
4. Click `Run workflow`
5. Fill in:
  - `image_tag`: for example `test-0403`
  - `openclaw_version`: fill in as needed
  - `push_arm64` / `push_amd64`: select as needed
6. After the run completes, pull the corresponding tag from the image registry

The workflow will automatically use the currently selected branch for checkout, and embed the branch name into the image tag.

For example, if you select `dev_damon` and fill in `image_tag=test-0403`, it will generate:

- `test-0403-dev_damon-arm64`
- `test-0403-dev_damon-amd64`

Dependent GitHub Secrets:

- `ALIYUN_REGISTRY`
- `ALIYUN_NAMESPACE`
- `ALIYUN_USERNAME`
- `ALIYUN_PASSWORD`
