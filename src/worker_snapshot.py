"""worker 级运行时快照，用于多-intent session 的恢复与续跑。"""
import logging
import shutil
from pathlib import Path
from typing import Any, Dict, Optional

from src.fs_utils import ensure_owner_writable, make_tree_owner_writable, remove_path, remove_tree
from src.openclaw_wrapper import OpenClawWrapper, expected_agent_workspace
from src.utils import load_json, save_json

logger = logging.getLogger(__name__)

_RUNTIME_SNAPSHOT_EXCLUDE_NAMES = {".git"}


def resolve_worker_snapshot_root(paths_config: Dict[str, Any]) -> Path:
    return Path(paths_config.get("worker_snapshot_dir", "output/worker_snapshots"))


def resolve_template_snapshot_root(paths_config: Dict[str, Any]) -> Path:
    return resolve_worker_snapshot_root(paths_config) / "template_workspace"


def resolve_runtime_snapshot_root(paths_config: Dict[str, Any]) -> Path:
    return resolve_worker_snapshot_root(paths_config) / "runtime"


def get_worker_agent_snapshot_dir(paths_config: Dict[str, Any], agent_name: str) -> Path:
    return resolve_runtime_snapshot_root(paths_config) / agent_name


def get_worker_workspace_snapshot_dir(paths_config: Dict[str, Any], agent_name: str) -> Path:
    return get_worker_agent_snapshot_dir(paths_config, agent_name) / "workspace"


def get_worker_pending_state_file(paths_config: Dict[str, Any], agent_name: str) -> Path:
    return get_worker_agent_snapshot_dir(paths_config, agent_name) / "pending_state.json"


def _copy_directory_contents(source_dir: Path, target_dir: Path) -> None:
    if target_dir.exists():
        remove_tree(target_dir)
    target_dir.mkdir(parents=True, exist_ok=True)

    for item in source_dir.iterdir():
        if item.name in _RUNTIME_SNAPSHOT_EXCLUDE_NAMES:
            continue
        destination = target_dir / item.name
        if item.is_dir():
            shutil.copytree(item, destination, symlinks=False)
            make_tree_owner_writable(destination)
        else:
            shutil.copy2(item, destination)
            ensure_owner_writable(destination)


def snapshot_worker_workspace(agent_name: str, config: Dict[str, Any]) -> Path:
    paths_config = config["paths"]
    workspace_root = config["openclaw"].get("workspace_root")
    workspace = expected_agent_workspace(agent_name, workspace_root)
    snapshot_dir = get_worker_workspace_snapshot_dir(paths_config, agent_name)
    _copy_directory_contents(workspace, snapshot_dir)
    return snapshot_dir


def restore_worker_workspace(agent_name: str, config: Dict[str, Any]) -> Optional[Path]:
    paths_config = config["paths"]
    snapshot_dir = get_worker_workspace_snapshot_dir(paths_config, agent_name)
    if not snapshot_dir.exists():
        return None

    workspace_root = config["openclaw"].get("workspace_root")
    workspace = expected_agent_workspace(agent_name, workspace_root)
    if workspace.exists():
        for item in workspace.iterdir():
            if item.name in _RUNTIME_SNAPSHOT_EXCLUDE_NAMES:
                continue
            if item.is_dir():
                remove_tree(item)
            else:
                remove_path(item)
    else:
        workspace.mkdir(parents=True, exist_ok=True)

    for item in snapshot_dir.iterdir():
        destination = workspace / item.name
        if item.is_dir():
            shutil.copytree(item, destination, symlinks=False)
            make_tree_owner_writable(destination)
        else:
            shutil.copy2(item, destination)
            ensure_owner_writable(destination)

    return snapshot_dir


def save_worker_runtime_snapshot(
    agent_name: str,
    config: Dict[str, Any],
    openclaw: OpenClawWrapper,
    pending_results: list[Dict[str, Any]],
    intents_in_current_session: int,
) -> Dict[str, Any]:
    paths_config = config["paths"]
    agent_snapshot_dir = get_worker_agent_snapshot_dir(paths_config, agent_name)
    sessions_snapshot_dir = agent_snapshot_dir / "sessions"
    session_info = openclaw.get_current_session_info()
    if not session_info:
        raise RuntimeError(f"Agent {agent_name} 当前没有可保存的 session")

    if agent_snapshot_dir.exists():
        remove_tree(agent_snapshot_dir)
    sessions_snapshot_dir.mkdir(parents=True, exist_ok=True)

    workspace_snapshot_dir = snapshot_worker_workspace(agent_name, config)
    session_snapshot_file = sessions_snapshot_dir / f"{session_info['sessionId']}.jsonl"
    openclaw.archive_current_session(str(session_snapshot_file), move_file=False)

    metadata = {
        "agent_name": agent_name,
        "intents_in_current_session": intents_in_current_session,
        "pending_results": pending_results,
        "session_info": session_info,
        "session_snapshot_file": str(session_snapshot_file),
        "workspace_snapshot_dir": str(workspace_snapshot_dir),
    }
    save_json(metadata, str(get_worker_pending_state_file(paths_config, agent_name)))
    return metadata


def load_worker_runtime_snapshot(agent_name: str, config: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    pending_state_file = get_worker_pending_state_file(config["paths"], agent_name)
    if not pending_state_file.exists():
        return None
    return load_json(str(pending_state_file))


def list_pending_snapshot_intent_ids(paths_config: Dict[str, Any]) -> set[str]:
    pending_intent_ids: set[str] = set()
    agents_root = resolve_runtime_snapshot_root(paths_config)
    if not agents_root.exists():
        return pending_intent_ids

    for pending_state_file in agents_root.glob("*/pending_state.json"):
        try:
            metadata = load_json(str(pending_state_file))
        except Exception as exc:
            logger.warning("读取 worker pending state 失败 %s: %s", pending_state_file, exc)
            continue

        for pending_result in metadata.get("pending_results", []):
            intent_id = pending_result.get("intent_id")
            if intent_id is not None:
                pending_intent_ids.add(str(intent_id))

    return pending_intent_ids


def restore_worker_runtime_snapshot(agent_name: str, config: Dict[str, Any], openclaw: OpenClawWrapper) -> Optional[Dict[str, Any]]:
    metadata = load_worker_runtime_snapshot(agent_name, config)
    if not metadata:
        return None

    restore_worker_workspace(agent_name, config)
    session_snapshot_file = metadata.get("session_snapshot_file")
    session_info = metadata.get("session_info")
    if not session_snapshot_file or not session_info:
        raise RuntimeError(f"Agent {agent_name} 的 worker snapshot 缺少 session 元数据")

    openclaw.restore_main_session(session_snapshot_file, session_info)
    logger.info(
        "已恢复 worker snapshot: agent=%s, pending_intents=%s, session_id=%s",
        agent_name,
        metadata.get("intents_in_current_session", 0),
        session_info.get("sessionId"),
    )
    return metadata


def clear_worker_runtime_snapshot(agent_name: str, config: Dict[str, Any]) -> None:
    paths_config = config["paths"]
    workspace_dir = get_worker_workspace_snapshot_dir(paths_config, agent_name)
    agent_dir = get_worker_agent_snapshot_dir(paths_config, agent_name)
    for path in (workspace_dir, agent_dir):
        if path.exists():
            remove_tree(path)
