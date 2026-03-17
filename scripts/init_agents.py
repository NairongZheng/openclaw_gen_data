"""Agent 初始化脚本"""
import subprocess
import argparse
import logging
import shutil
from pathlib import Path

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def ensure_skill_installed():
    """确保 create-agent skill 已安装到 openclaw"""
    skill_name = "create-agent"
    openclaw_skills_dir = Path.home() / ".openclaw/workspace/skills"
    target_skill_dir = openclaw_skills_dir / skill_name

    # 检查 skill 是否已存在
    if target_skill_dir.exists():
        logger.info(f"Skill '{skill_name}' 已存在")
        return

    # 复制 skill 到 openclaw
    source_skill_dir = Path(__file__).parent.parent / "skills" / skill_name
    if not source_skill_dir.exists():
        raise FileNotFoundError(f"源 skill 目录不存在: {source_skill_dir}")

    openclaw_skills_dir.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source_skill_dir, target_skill_dir)
    logger.info(f"已安装 skill '{skill_name}' 到 {target_skill_dir}")


def init_agents(num_agents: int = 30):
    """初始化多个 OpenClaw agent

    通过 main agent 创建多个子 agent，每个使用独立的 workspace

    Args:
        num_agents: Agent 数量
    """
    logger.info(f"开始初始化 {num_agents} 个 agent...")

    # 构建创建 agent 的消息
    skill_content = f"""请使用 cerate-agent 这个 skill 帮我创建 {num_agents} 个独立的 agent，命名为 gendata-worker-1 到 gendata-worker-{num_agents}。"""

    try:
        # 通过 main agent 执行创建任务
        cmd = [
            "openclaw", "agent",
            "--agent", "main",
            "--message", skill_content
        ]

        logger.info("执行创建命令...")
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)

        if result.returncode != 0:
            logger.error(f"创建失败: {result.stderr}")
            return False

        logger.info("Agent 初始化完成！")
        logger.info(f"输出: {result.stdout}")
        return True

    except subprocess.TimeoutExpired:
        logger.error("创建超时")
        return False
    except Exception as e:
        logger.error(f"创建失败: {e}")
        return False


def main():
    """主函数"""
    parser = argparse.ArgumentParser(description="初始化 OpenClaw agents")
    parser.add_argument(
        "--num-agents",
        type=int,
        default=30,
        help="要创建的 agent 数量（默认: 30）"
    )

    args = parser.parse_args()

    # 确保 skill 已安装
    try:
        ensure_skill_installed()
    except Exception as e:
        logger.error(f"安装 skill 失败: {e}")
        exit(1)

    success = init_agents(args.num_agents)
    if success:
        logger.info("✓ 所有 agent 初始化成功")
    else:
        logger.error("✗ Agent 初始化失败")
        exit(1)


if __name__ == "__main__":
    main()
