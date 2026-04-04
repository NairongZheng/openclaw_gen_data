#!/usr/bin/env bash
set -euo pipefail

# ====================================================
# 必须配置（必须通过环境变量传入）
# OUTPUT_DIR   : 宿主机持久化输出目录，容器内 ./output 会软链到这里
# ====================================================
OUTPUT_DIR="${OUTPUT_DIR:?请设置 OUTPUT_DIR 环境变量，指向输出目录（如 /mnt/output）}"

# ====================================================
# 可选配置（不传则跳过）
# CONFIG_PATH          : 项目 config.yaml 的路径（mnt 挂载进来后直接 cp）
# CONCURRENT_NUM       : 并发数，默认 3
# OPENCLAW_MODEL_URL       : 覆盖 openclaw.model_url
# OPENCLAW_MODEL_API_KEY   : 覆盖 openclaw.model_api_key
# OPENCLAW_MODEL_NAME      : 覆盖 openclaw.model
# LLM_BASE_URL             : 覆盖 llm.base_url
# LLM_API_KEY              : 覆盖 llm.api_key
# LLM_MODEL_NAME           : 覆盖 llm.model
# INTENTS_PER_SESSION      : 每个 worker 连续处理多少个 intent 后再重置一次 session/workspace
# INTENTS_FILE             : intents.jsonl 路径（mnt 挂载进来的路径），用于覆盖 config 里的 paths.intents_file
# APPEND_QUERY_ENABLED     : 是否在每次正常 session 收口前追加一条 query
# APPEND_QUERY_FILE        : 追加 query 池文件路径
# OPENCLAW_SEARCH_PROVIDER : search provider
# OPENCLAW_SEARCH_API_KEY  : 当前 provider 的 apiKey
# OPENCLAW_SEARCH_BASE_URL : 当前 provider 的 baseUrl
# OPENCLAW_DISCOVERY_MDNS_MODE : OpenClaw discovery.mdns.mode；容器里默认 off，避免长 hostname 触发 mDNS label 超长崩溃
# 注意：search 相关配置需要 provider/apiKey/baseUrl 三者齐全；discovery.mdns.mode 可独立生效
# ====================================================
CONFIG_PATH="${CONFIG_PATH:-}"
CONCURRENT_NUM="${CONCURRENT_NUM:-3}"
OPENCLAW_MODEL_URL="${OPENCLAW_MODEL_URL:-}"
OPENCLAW_MODEL_API_KEY="${OPENCLAW_MODEL_API_KEY:-}"
OPENCLAW_MODEL_NAME="${OPENCLAW_MODEL_NAME:-}"
LLM_BASE_URL="${LLM_BASE_URL:-}"
LLM_API_KEY="${LLM_API_KEY:-}"
LLM_MODEL_NAME="${LLM_MODEL_NAME:-}"
INTENTS_PER_SESSION="${INTENTS_PER_SESSION:-}"
INTENTS_FILE="${INTENTS_FILE:-}"
APPEND_QUERY_ENABLED="${APPEND_QUERY_ENABLED:-}"
APPEND_QUERY_FILE="${APPEND_QUERY_FILE:-}"
OPENCLAW_SEARCH_PROVIDER="${OPENCLAW_SEARCH_PROVIDER:-}"
OPENCLAW_SEARCH_API_KEY="${OPENCLAW_SEARCH_API_KEY:-}"
OPENCLAW_SEARCH_BASE_URL="${OPENCLAW_SEARCH_BASE_URL:-}"
OPENCLAW_DISCOVERY_MDNS_MODE="${OPENCLAW_DISCOVERY_MDNS_MODE:-off}"

export CONFIG_PATH CONCURRENT_NUM
export OPENCLAW_MODEL_URL OPENCLAW_MODEL_API_KEY OPENCLAW_MODEL_NAME
export LLM_BASE_URL LLM_API_KEY LLM_MODEL_NAME
export INTENTS_PER_SESSION INTENTS_FILE
export APPEND_QUERY_ENABLED APPEND_QUERY_FILE
export OPENCLAW_SEARCH_PROVIDER OPENCLAW_SEARCH_API_KEY
export OPENCLAW_SEARCH_BASE_URL
export OPENCLAW_DISCOVERY_MDNS_MODE

CONDA_DIR="${CONDA_DIR:-/opt/miniconda3}"
CONDA_ENV_NAME="${CONDA_ENV_NAME:-dev}"
GATEWAY_LOG="${OPENCLAW_GATEWAY_LOG:-/root/.openclaw/gateway.log}"
WORK_DIR="/workspace"

# 让 Python stdout/stderr 立即刷新，便于容器日志实时查看
export PYTHONUNBUFFERED=1

ensure_openclaw_runtime_config() {
  local runtime_output

  echo "[start] ensuring OpenClaw runtime config (search/discovery patches auto-applied when configured)"

  runtime_output=$("${CONDA_DIR}/bin/conda" run --no-capture-output -n "${CONDA_ENV_NAME}" \
    python -m src.runtime_config 2>&1)
  printf '%s\n' "${runtime_output}"

  if printf '%s\n' "${runtime_output}" | grep -q '^OPENCLAW_RUNTIME_CONFIG_CHANGED=1$'; then
      echo "[start] runtime config updated"
      return 0
  fi

  if printf '%s\n' "${runtime_output}" | grep -q '^OPENCLAW_RUNTIME_CONFIG_CHANGED=0$'; then
      echo "[start] runtime config unchanged"
      return 1
  fi

  echo "[start] runtime config failed: missing status marker" >&2
  return 1
}

cd "${WORK_DIR}"

# 1) 注入项目配置（可选）
if [[ -n "${CONFIG_PATH}" ]]; then
  if [[ ! -f "${CONFIG_PATH}" ]]; then
    echo "[start] CONFIG_PATH does not exist or is not a file: ${CONFIG_PATH}" >&2
    exit 1
  fi

  cp -f "${CONFIG_PATH}" ./config/config.yaml
  CONFIG_PATH="./config/config.yaml"
  export CONFIG_PATH
  echo "[start] config.yaml injected from ${CONFIG_PATH}"
fi

# 2) 输出目录软链到宿主机持久化路径
mkdir -p "${OUTPUT_DIR}"
rm -rf ./output
ln -s "${OUTPUT_DIR}" ./output
echo "[start] output -> ${OUTPUT_DIR}"

# 3) 初始化 agents
"${CONDA_DIR}/bin/conda" run --no-capture-output -n "${CONDA_ENV_NAME}" \
  python scripts/init_agents.py \
    --num-agents "${CONCURRENT_NUM}" \
    --force-recreate \
    --refresh-tools

RUNTIME_CONFIG_CHANGED=0
if ensure_openclaw_runtime_config; then
  RUNTIME_CONFIG_CHANGED=1
fi

# 4) 确保 gateway 在运行
mkdir -p "$(dirname "${GATEWAY_LOG}")"
if ! pgrep -fa "openclaw gateway run" >/dev/null 2>&1; then
  nohup openclaw gateway run >"${GATEWAY_LOG}" 2>&1 &
  echo "[start] gateway started, log=${GATEWAY_LOG}"
  sleep 2
else
  if [[ "${RUNTIME_CONFIG_CHANGED}" == "1" ]]; then
    pkill -f "openclaw gateway run" || true
    nohup openclaw gateway run >"${GATEWAY_LOG}" 2>&1 &
    echo "[start] gateway restarted to apply runtime config, log=${GATEWAY_LOG}"
    sleep 2
  else
    echo "[start] gateway already running"
  fi
fi

# 5) 运行数据生成
RUN_GENERATION_CMD=(
  "${CONDA_DIR}/bin/conda" run --no-capture-output -n "${CONDA_ENV_NAME}"
  python scripts/run_generation.py
)

echo "[start] run_generation started"
"${RUN_GENERATION_CMD[@]}"
