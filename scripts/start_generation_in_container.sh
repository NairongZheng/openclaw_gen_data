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
# INTENTS_FILE         : intents.jsonl 路径（mnt 挂载进来的路径），用于覆盖 config 里的 paths.intents_file
# CONCURRENT_NUM       : 并发数，默认 3
# OPENCLAW_SEARCH_PROVIDER : search provider
# OPENCLAW_SEARCH_API_KEY  : 当前 provider 的 apiKey
# OPENCLAW_SEARCH_BASE_URL : 当前 provider 的 baseUrl
# 只有这三个变量都提供时，才会自动开启 web.fetch/web.search 并写入 OpenClaw 配置
# ====================================================
CONFIG_PATH="${CONFIG_PATH:-}"
INTENTS_FILE="${INTENTS_FILE:-}"
CONCURRENT_NUM="${CONCURRENT_NUM:-3}"
OPENCLAW_SEARCH_PROVIDER="${OPENCLAW_SEARCH_PROVIDER:-}"
OPENCLAW_SEARCH_API_KEY="${OPENCLAW_SEARCH_API_KEY:-}"
OPENCLAW_SEARCH_BASE_URL="${OPENCLAW_SEARCH_BASE_URL:-}"

export OPENCLAW_SEARCH_PROVIDER OPENCLAW_SEARCH_API_KEY
export OPENCLAW_SEARCH_BASE_URL

CONDA_DIR="${CONDA_DIR:-/opt/miniconda3}"
CONDA_ENV_NAME="${CONDA_ENV_NAME:-dev}"
GATEWAY_LOG="${OPENCLAW_GATEWAY_LOG:-/root/.openclaw/gateway.log}"
WORK_DIR="/workspace"

# 让 Python stdout/stderr 立即刷新，便于容器日志实时查看
export PYTHONUNBUFFERED=1

ensure_openclaw_runtime_config() {
  local runtime_output

  if [[ -z "${OPENCLAW_SEARCH_PROVIDER}" || -z "${OPENCLAW_SEARCH_API_KEY}" || -z "${OPENCLAW_SEARCH_BASE_URL}" ]]; then
    echo "[start] search config incomplete, skip OpenClaw runtime config"
    return 1
  fi

  echo "[start] ensuring OpenClaw runtime config (fetch/search default enabled; provider/key/baseUrl auto-applied)"

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
if [[ -n "${CONFIG_PATH}" ]] && [[ -f "${CONFIG_PATH}" ]]; then
  cp -f "${CONFIG_PATH}" ./config/config.yaml
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
  --concurrent "${CONCURRENT_NUM}"
)

if [[ -n "${INTENTS_FILE}" ]]; then
  RUN_GENERATION_CMD+=(--intents-file "${INTENTS_FILE}")
  echo "[start] intents file override: ${INTENTS_FILE}"
fi

echo "[start] run_generation started"
"${RUN_GENERATION_CMD[@]}"
