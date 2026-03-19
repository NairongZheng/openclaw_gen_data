"""Agent 初始化脚本。"""
import argparse
import logging
from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.openclaw_wrapper import ensure_agents, resolve_workspace_root

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def init_agents(
    num_agents: int = 30,
    worker_prefix: str = "gendata-worker",
    workspace_root: str = None,
    recreate_mismatched: bool = False,
) -> bool:
    """初始化多个 OpenClaw agent。"""
    logger.info(f"开始检查并创建 {num_agents} 个 agents...")
    root_dir = resolve_workspace_root(workspace_root)
    logger.info("worker workspaces 根目录: %s", root_dir)
    result = ensure_agents(
        num_agents=num_agents,
        worker_prefix=worker_prefix,
        workspace_root=str(root_dir),
        recreate_mismatched=recreate_mismatched,
    )
    logger.info(
        "已存在: %s，新建: %s，重建: %s",
        len(result["existing"]),
        len(result["created"]),
        len(result["recreated"]),
    )
    if result["created"]:
        logger.info("新建 agents: %s", ", ".join(result["created"]))
    if result["recreated"]:
        logger.info("重建 agents: %s", ", ".join(result["recreated"]))
    return True


def main():
    """主函数"""
    parser = argparse.ArgumentParser(description="初始化 OpenClaw agents")
    parser.add_argument(
        "--num-agents",
        type=int,
        default=3,
        help="要创建的 agent 数量（默认: 3）"
    )
    parser.add_argument(
        "--worker-prefix",
        default="gendata-worker",
        help="worker agent 前缀（默认: gendata-worker）",
    )
    parser.add_argument(
        "--workspace-root",
        help="隔离 workspace 根目录，默认使用 ~/.openclaw/workspaces",
    )
    parser.add_argument(
        "--recreate-mismatched",
        action="store_true",
        help="如果已有 agent 的 workspace 不符合预期，则删除并按隔离目录重建",
    )

    args = parser.parse_args()

    success = init_agents(
        args.num_agents,
        args.worker_prefix,
        args.workspace_root,
        args.recreate_mismatched,
    )
    if success:
        logger.info("✓ 所有 agent 初始化成功")
    else:
        logger.error("✗ Agent 初始化失败")
        exit(1)


if __name__ == "__main__":
    main()
