"""run_generation 共享的生成流程辅助逻辑。"""
import hashlib
import logging
import shutil
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.converter import DataConverter
from src.llm_client import LLMClient
from src.openclaw_wrapper import OpenClawWrapper
from src.runtime_metadata_cache import extract_tools_from_runtime_metadata, load_runtime_metadata_cache
from src.utils import load_json, save_json

logger = logging.getLogger(__name__)


class ProgressTracker:
    """线程安全的进度记录器。"""

    def __init__(self, progress_file: str):
        self.progress_file = Path(progress_file)
        self.lock = threading.Lock()
        if self.progress_file.exists():
            self.data = load_json(str(self.progress_file))
        else:
            self.data = {"items": {}, "summary": {}}
        items = (self.data.get("items") or {}).values()
        self._success_count = sum(1 for item in items if item.get("status") == "success")
        self._failed_count = sum(1 for item in items if item.get("status") == "failed")

    def is_success(self, intent_id: str) -> bool:
        item = self.data.get("items", {}).get(intent_id)
        return bool(item and item.get("status") == "success")

    def record(self, result: Dict[str, Any]) -> None:
        intent_id = str(result["intent_id"])
        with self.lock:
            old = self.data.setdefault("items", {}).get(intent_id)
            old_status = old.get("status") if old else None
            self.data["items"][intent_id] = result
            new_status = result.get("status")
            if old_status == "success":
                self._success_count -= 1
            elif old_status == "failed":
                self._failed_count -= 1
            if new_status == "success":
                self._success_count += 1
            elif new_status == "failed":
                self._failed_count += 1
            self.data["summary"] = {
                "total_recorded": len(self.data["items"]),
                "success": self._success_count,
                "failed": self._failed_count,
            }
            save_json(self.data, str(self.progress_file))


def create_llm_client(config: Dict[str, Any]) -> LLMClient:
    """根据配置创建 LLM 客户端。"""
    llm_config = config["llm"]
    return LLMClient(
        base_url=llm_config["base_url"],
        api_key=llm_config["api_key"],
        model=llm_config["model"],
        temperature=llm_config.get("temperature", 0.7),
        max_tokens=llm_config.get("max_tokens"),
        timeout=llm_config.get("timeout"),
        retry_attempts=llm_config.get("retry_attempts", 3),
        retry_base_delay=llm_config.get("retry_base_delay", 1.0),
        retry_max_delay=llm_config.get("retry_max_delay", 8.0),
        enable_thinking=llm_config.get("enable_thinking", True),
    )


def resolve_openclaw_thinking_mode(config: Dict[str, Any]) -> str:
    """解析 OpenClaw CLI 的 thinking 参数。"""
    openclaw_config = config["openclaw"]
    if not openclaw_config.get("enable_thinking", True):
        return "off"
    return openclaw_config.get("thinking_level", "high")


def resolve_intents_per_session(config: Dict[str, Any]) -> int:
    """解析每个 worker 在重置 session 前连续处理的 intent 数。"""
    raw_value = config.get("generation", {}).get("intents_per_session", 1)
    try:
        value = int(raw_value)
    except (TypeError, ValueError):
        logger.warning("generation.intents_per_session=%r 非法，回退为 1", raw_value)
        return 1
    return max(1, value)


def resolve_append_query_enabled(config: Dict[str, Any]) -> bool:
    """解析 session 收口前是否启用追加 query。"""
    raw_value = config.get("generation", {}).get("append_query_enabled", False)
    if isinstance(raw_value, bool):
        return raw_value
    normalized = str(raw_value).strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off", ""}:
        return False
    logger.warning("generation.append_query_enabled=%r 非法，回退为 false", raw_value)
    return False


def resolve_append_query_file(config: Dict[str, Any]) -> str:
    """解析 session 收口前追加 query 的数据文件路径。"""
    raw_value = config.get("generation", {}).get("append_query_file", "")
    return str(raw_value or "").strip()


def select_append_query_task(query_pool: List[Dict[str, Any]], anchor_id: Any) -> Optional[Dict[str, Any]]:
    """基于 anchor_id 稳定选择一条追加 query。"""
    if not query_pool:
        return None
    digest = hashlib.md5(str(anchor_id).encode("utf-8")).hexdigest()
    index = int(digest[:8], 16) % len(query_pool)
    return dict(query_pool[index])


def summarize_final_results(results: List[Dict[str, Any]]) -> Dict[str, Any]:
    """按 intent 聚合结果，保留每个 intent 的最终状态。"""
    latest_results_by_intent: Dict[str, Dict[str, Any]] = {}
    for result in results:
        intent_id = str(result.get("intent_id", "unknown"))
        latest_results_by_intent[intent_id] = result

    final_results = list(latest_results_by_intent.values())
    success = sum(1 for result in final_results if result.get("status") == "success")
    failed = sum(1 for result in final_results if result.get("status") == "failed")
    return {
        "total": len(final_results),
        "success": success,
        "failed": failed,
        "results": final_results,
        "attempt_total": len(results),
        "attempt_results": results,
    }


def load_agent_tools(cache_file: str, agent_id: str) -> List[Dict[str, Any]]:
    """从缓存文件加载 tools；优先读取共享 runtime metadata，兼容旧 per-agent tools cache。"""
    cache_path = Path(cache_file)
    if not cache_path.exists():
        logger.warning("工具缓存文件不存在: %s", cache_file)
        return []

    try:
        data = load_runtime_metadata_cache(str(cache_path))
        tools = extract_tools_from_runtime_metadata(data)
        if tools:
            logger.info("已加载 agent %s 的工具列表，共 %s 个工具", agent_id, len(tools))
            return tools
        logger.warning("未找到 agent %s 的工具列表", agent_id)
        return []
    except Exception as exc:
        logger.error("加载 agent %s 的工具列表失败: %s", agent_id, exc)
        return []


def extract_skills(session_info: Dict[str, Any]) -> List[Dict[str, Any]]:
    """从 sessions.json 快照中提取 skills。"""
    skills_snapshot = (session_info or {}).get("skillsSnapshot", {})
    resolved_skills = skills_snapshot.get("resolvedSkills")
    if resolved_skills:
        return resolved_skills
    return skills_snapshot.get("skills", [])


def extract_available_tool_entries(session_info: Dict[str, Any]) -> List[Dict[str, Any]]:
    """从 session 元数据中提取当前 agent 可用工具列表。"""
    return (session_info or {}).get("systemPromptReport", {}).get("tools", {}).get("entries", [])


def build_archived_session_path(
    paths_config: Dict[str, Any],
    intent_id: Any,
    agent_name: str,
    session_id: str,
) -> Path:
    sessions_dir = Path(paths_config["sessions_dir"])
    return sessions_dir / f"intent_{intent_id}__{agent_name}__{session_id}.jsonl"


def build_middle_format_path(paths_config: Dict[str, Any], intent_id: Any) -> Path:
    return Path(paths_config["middle_format_dir"]) / f"intent_{intent_id}.json"


def build_session_batch_metadata(
    session_results: List[Dict[str, Any]],
    finalized_by_intent_id: Any,
    appended_query: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    intent_ids = [str(result.get("intent_id")) for result in session_results]
    intent_records = [
        {
            "intent_id": str(result.get("intent_id")),
            "natural_language_intent": (result.get("intent_data") or {}).get("natural_language_intent"),
            "task_type": (result.get("intent_data") or {}).get("task_type", "intent"),
        }
        for result in session_results
    ]
    metadata = {
        "intent_count": len(session_results),
        "intent_ids": intent_ids,
        "finalized_by_intent_id": str(finalized_by_intent_id),
        "intent_records": intent_records,
    }
    if appended_query:
        metadata["appended_query"] = appended_query
    return metadata


def snapshot_session_for_later_materialization(
    openclaw: OpenClawWrapper,
    paths_config: Dict[str, Any],
    intent_result: Dict[str, Any],
    agent_name: str,
) -> Dict[str, Any]:
    """将当前 session 复制到 pending 区，等待 session 收口时再正式产物化。"""
    session_info = intent_result["session_info"]
    pending_dir = Path(paths_config["sessions_dir"]) / ".pending"
    pending_dir.mkdir(parents=True, exist_ok=True)

    pending_session_file = pending_dir / f"intent_{intent_result['intent_id']}__{agent_name}__{session_info['sessionId']}.jsonl"
    openclaw.archive_current_session(str(pending_session_file), move_file=False)

    snapshotted_result = dict(intent_result)
    snapshotted_result["pending_session_file"] = str(pending_session_file)
    snapshotted_result["session_archive_mode"] = "copy"
    return snapshotted_result


def materialize_intent_output(
    intent_result: Dict[str, Any],
    session_file: Path,
    config: Dict[str, Any],
    tools_catalog: List[Dict[str, Any]],
    converter: DataConverter,
    agent_name: str,
    session_archive_mode: str,
) -> Dict[str, Any]:
    """将已归档 session 转成最终 middle format，并产出进度记录。"""
    paths_config = config["paths"]
    output_file = build_middle_format_path(paths_config, intent_result["intent_id"])
    session_info = intent_result["session_info"]
    batch_metadata = intent_result.get("session_batch_metadata", {})
    session_metadata = dict(session_info)
    if batch_metadata:
        session_metadata["batch"] = batch_metadata

    converter.convert_session_to_middle_format(
        session_file=str(session_file),
        intent_data=intent_result["intent_data"],
        output_file=str(output_file),
        tools_catalog=tools_catalog,
        available_tool_entries=extract_available_tool_entries(session_info),
        skills=extract_skills(session_info),
        session_metadata=session_metadata,
        agent_name=agent_name,
        workspace_root=config["openclaw"].get("workspace_root"),
    )

    finalized_result = {
        key: value
        for key, value in intent_result.items()
        if key not in {"intent_data", "session_info", "pending_session_file"}
    }
    finalized_result.update(
        {
            "output_file": str(output_file),
            "session_file": str(session_file),
            "session_id": session_info["sessionId"],
            "session_archive_mode": session_archive_mode,
            "session_batch_metadata": batch_metadata,
        }
    )
    return finalized_result


def materialize_pending_session_results(
    pending_results: List[Dict[str, Any]],
    config: Dict[str, Any],
    tools_catalog: List[Dict[str, Any]],
    converter: DataConverter,
    agent_name: str,
) -> List[Dict[str, Any]]:
    """将 pending 区的 session 快照正式落盘并转换。"""
    paths_config = config["paths"]
    finalized_results: List[Dict[str, Any]] = []

    for pending_result in pending_results:
        pending_session_file = Path(pending_result["pending_session_file"])
        final_session_file = build_archived_session_path(
            paths_config,
            pending_result["intent_id"],
            agent_name,
            pending_result["session_info"]["sessionId"],
        )
        final_session_file.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(pending_session_file), str(final_session_file))
        finalized_results.append(
            materialize_intent_output(
                pending_result,
                final_session_file,
                config,
                tools_catalog,
                converter,
                agent_name,
                session_archive_mode=pending_result.get("session_archive_mode", "copy"),
            )
        )

    return finalized_results
