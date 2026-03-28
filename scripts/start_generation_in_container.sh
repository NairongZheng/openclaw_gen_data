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
# CONCURRENT_NUM       : 并发数，默认 10
# ====================================================
CONFIG_PATH="${CONFIG_PATH:-}"
CONCURRENT_NUM="${CONCURRENT_NUM:-10}"

CONDA_DIR="${CONDA_DIR:-/opt/miniconda3}"
CONDA_ENV_NAME="${CONDA_ENV_NAME:-dev}"
GATEWAY_LOG="${OPENCLAW_GATEWAY_LOG:-/root/.openclaw/gateway.log}"
WORK_DIR="/workspace"

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

# 3) 确保 gateway 在运行
mkdir -p "$(dirname "${GATEWAY_LOG}")"
if ! pgrep -fa "openclaw gateway run" >/dev/null 2>&1; then
  nohup openclaw gateway run >"${GATEWAY_LOG}" 2>&1 &
  echo "[start] gateway started, log=${GATEWAY_LOG}"
  sleep 2
else
  echo "[start] gateway already running"
fi

# 4) 初始化 agents
"${CONDA_DIR}/bin/conda" run -n "${CONDA_ENV_NAME}" \
  python scripts/init_agents.py \
    --num-agents "${CONCURRENT_NUM}" \
    --force-recreate \
    --refresh-tools

# 5) 运行数据生成
"${CONDA_DIR}/bin/conda" run -n "${CONDA_ENV_NAME}" \
  python scripts/run_generation.py \
    --concurrent "${CONCURRENT_NUM}"
