"""Agent 运行时清理与 workspace 快照恢复。"""
import logging
import os
import shutil
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.openclaw_wrapper import OpenClawWrapper, expected_agent_workspace, resolve_workspace_root
from src.worker_snapshot import resolve_template_snapshot_root

logger = logging.getLogger(__name__)

SHARED_WORKSPACE_SNAPSHOT_NAME = "_template"


def resolve_project_root() -> Path:
    """解析项目根目录。"""
    if "__file__" in globals():
        return Path(__file__).parent.parent
    return Path(os.getcwd())


def get_workspace_snapshot_dir(project_root: Optional[Path] = None) -> Path:
    """返回 workspace 快照根目录。"""
    root = project_root or resolve_project_root()
    return root / "output" / "worker_snapshots" / "template_workspace"


def restore_workspace_snapshot(agent_name: str, config: Dict[str, Any]) -> None:
    """从快照恢复 agent workspace。"""
    workspace_root = config["openclaw"].get("workspace_root")
    root_dir = resolve_workspace_root(workspace_root)
    workspace = expected_agent_workspace(agent_name, str(root_dir))
    paths_config = config.get("paths", {})
    snapshot_root = resolve_template_snapshot_root(paths_config)
    shared_snapshot_path = snapshot_root / SHARED_WORKSPACE_SNAPSHOT_NAME
    agent_snapshot_path = snapshot_root / agent_name
    snapshot_path = shared_snapshot_path if shared_snapshot_path.exists() else agent_snapshot_path

    if not snapshot_path.exists():
        logger.warning("Agent %s 的快照不存在，跳过恢复: %s", agent_name, snapshot_path)
        return

    if workspace.exists():
        for item in workspace.iterdir():
            if item.name == ".git":
                continue
            try:
                if item.is_dir():
                    shutil.rmtree(item)
                else:
                    item.unlink()
            except Exception as exc:
                logger.warning("删除 %s 失败: %s", item, exc)

    for item in snapshot_path.iterdir():
        if item.name == ".git":
            continue
        destination = workspace / item.name
        try:
            if item.is_dir():
                shutil.copytree(item, destination, symlinks=False)
            else:
                shutil.copy2(item, destination)
        except Exception as exc:
            logger.warning("恢复 %s 失败: %s", item, exc)

    logger.info("已从快照 %s 恢复 agent %s 的 workspace", snapshot_path.name, agent_name)


def cleanup_agents(agent_ids: List[str], config: Dict[str, Any]) -> None:
    """清理所有 agents 的 session、锁文件，并恢复 workspace 快照。"""
    logger.info("开始清理 agents 资源...")
    started_at = time.perf_counter()

    try:
        import psutil

        process_cleanup_started_at = time.perf_counter()
        current_process = psutil.Process()
        children = current_process.children(recursive=True)
        for child in children:
            try:
                if "openclaw" in " ".join(child.cmdline()).lower():
                    logger.info("终止 openclaw 子进程 %s", child.pid)
                    child.terminate()
            except Exception:
                pass

        time.sleep(0.5)
        for child in children:
            try:
                if child.is_running():
                    child.kill()
            except Exception:
                pass
        logger.info("openclaw 子进程清理耗时 %.2fs", time.perf_counter() - process_cleanup_started_at)
    except ImportError:
        logger.warning("psutil 不可用，跳过子进程清理")
    except Exception as exc:
        logger.warning("清理子进程失败: %s", exc)

    for agent_name in agent_ids:
        agent_started_at = time.perf_counter()
        try:
            wrapper = OpenClawWrapper(agent_name)
            wrapper.reset_main_session()
            logger.debug("已重置 %s 的 session", agent_name)
        except Exception as exc:
            logger.warning("重置 %s session 失败: %s", agent_name, exc)

        try:
            agent_dir = Path.home() / ".openclaw" / "agents" / agent_name / "sessions"
            if agent_dir.exists():
                lock_files = list(agent_dir.glob("*.lock"))
                for lock_file in lock_files:
                    try:
                        lock_file.unlink()
                        logger.debug("已删除锁文件: %s", lock_file.name)
                    except Exception as exc:
                        logger.warning("删除锁文件失败 %s: %s", lock_file, exc)
        except Exception as exc:
            logger.warning("清理 %s 锁文件失败: %s", agent_name, exc)

        try:
            restore_workspace_snapshot(agent_name, config)
        except Exception as exc:
            logger.warning("恢复 %s workspace 快照失败: %s", agent_name, exc)
        logger.info("agent %s cleanup 耗时 %.2fs", agent_name, time.perf_counter() - agent_started_at)

    logger.info("✓ 已清理 %s 个 agents", len(agent_ids))
    logger.info("cleanup_agents 总耗时 %.2fs", time.perf_counter() - started_at)
