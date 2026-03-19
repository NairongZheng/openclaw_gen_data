"""Agent 初始化脚本。"""
import argparse
import logging
from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.openclaw_wrapper import ensure_agents

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def init_agents(num_agents: int = 30, worker_prefix: str = "gendata-worker") -> bool:
    """初始化多个 OpenClaw agent。"""
    logger.info(f"开始检查并创建 {num_agents} 个 agents...")
    result = ensure_agents(num_agents=num_agents, worker_prefix=worker_prefix)
    logger.info(f"已存在: {len(result['existing'])}，新建: {len(result['created'])}")
    if result["created"]:
        logger.info("新建 agents: %s", ", ".join(result["created"]))
    return True


def main():
    """主函数"""
    parser = argparse.ArgumentParser(description="初始化 OpenClaw agents")
    parser.add_argument(
        "--num-agents",
        type=int,
        default=5,
        help="要创建的 agent 数量（默认: 5）"
    )
    parser.add_argument(
        "--worker-prefix",
        default="gendata-worker",
        help="worker agent 前缀（默认: gendata-worker）",
    )

    args = parser.parse_args()

    success = init_agents(args.num_agents, args.worker_prefix)
    if success:
        logger.info("✓ 所有 agent 初始化成功")
    else:
        logger.error("✗ Agent 初始化失败")
        exit(1)


if __name__ == "__main__":
    main()
