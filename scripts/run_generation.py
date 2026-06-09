"""主生成脚本 - 生成、归档、转换、resume 一体化。"""
import argparse
import logging
import queue
import signal
import os
import sys
import threading
import time
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Optional

# 防御性处理 __file__ (容器环境兼容)
if '__file__' in globals():
    sys.path.insert(0, str(Path(__file__).parent.parent))
else:
    sys.path.insert(0, str(Path(os.getcwd())))

from src.config import load_config, resolve_config_path
from src.agent_runtime import cleanup_agents, restore_workspace_snapshot
from src.intent_loader import load_intents
from src.openclaw_wrapper import OpenClawWrapper, ensure_agents
from src.llm_client import LLMClient
from src.converter import DataConverter
from src.generation_support import (
    ProgressTracker,
    build_archived_session_path,
    build_session_batch_metadata,
    create_llm_client,
    load_agent_tools,
    materialize_intent_output,
    resolve_append_query_enabled,
    resolve_append_query_file,
    resolve_intents_per_session,
    resolve_openclaw_thinking_mode,
    select_append_query_task,
    summarize_final_results,
)
from src.runtime_config import apply_runtime_patch_from_env
from src.runtime_metadata_cache import resolve_runtime_metadata_cache_file
from src.runtime_recovery import (
    backup_openclaw_config_to_output,
    looks_like_config_corruption_error,
    recover_openclaw_runtime_from_baseline,
    resolve_openclaw_runtime_paths,
)
from src.utils import ensure_dir, resolve_project_root, save_json, setup_logging
from src.worker_snapshot import (
    clear_worker_runtime_snapshot,
    list_pending_snapshot_intent_ids,
    resolve_worker_snapshot_root,
    restore_worker_runtime_snapshot,
    save_worker_runtime_snapshot,
)

logger = logging.getLogger(__name__)

# 全局变量用于优雅退出
_shutdown_requested = threading.Event()
_active_agents: List[str] = []
_active_config: Optional[Dict[str, Any]] = None
_executor: Optional[ThreadPoolExecutor] = None
_runtime_recovery_requested = threading.Event()
_runtime_recovery_reason_lock = threading.Lock()
_runtime_recovery_reason: str = ""
_runtime_recovery_requested_at: Optional[float] = None


def _task_type(task_data: Dict[str, Any]) -> str:
    return str(task_data.get("task_type") or "intent")


def _task_id(task_data: Dict[str, Any]) -> str:
    return str(task_data.get("id", "unknown"))


def _task_prompt(task_data: Dict[str, Any]) -> str:
    return str(task_data.get("query") or task_data.get("natural_language_intent") or "").strip()

def request_runtime_recovery(reason: str) -> None:
    """请求全局运行时恢复（停止当前批次，回滚配置后重跑）。"""
    global _runtime_recovery_reason, _runtime_recovery_requested_at
    with _runtime_recovery_reason_lock:
        if not _runtime_recovery_reason:
            _runtime_recovery_reason = reason
        if _runtime_recovery_requested_at is None:
            _runtime_recovery_requested_at = time.perf_counter()
    _runtime_recovery_requested.set()


def consume_runtime_recovery_state() -> tuple[str, Optional[float]]:
    global _runtime_recovery_reason, _runtime_recovery_requested_at
    with _runtime_recovery_reason_lock:
        reason = _runtime_recovery_reason
        requested_at = _runtime_recovery_requested_at
        _runtime_recovery_reason = ""
        _runtime_recovery_requested_at = None
    return reason, requested_at


def signal_handler(signum, frame):
    """信号处理器 - 捕获 Ctrl+C 和 SIGTERM。"""
    signal_name = "SIGINT" if signum == signal.SIGINT else "SIGTERM"
    logger.warning(f"\n收到 {signal_name} 信号，正在优雅退出...")

    _shutdown_requested.set()

    # 终止线程池
    if _executor:
        logger.info("正在终止所有 worker 线程...")
        _executor.shutdown(wait=False, cancel_futures=True)

    # 清理所有 agents
    if _active_agents and _active_config:
        cleanup_agents(_active_agents, _active_config)

    logger.info("清理完成，强制退出程序")
    # 使用 os._exit() 强制退出，不等待线程
    os._exit(0)


def _build_interrupted_result(
    intent_id: Any,
    agent_name: str,
    conversation_history: List[Dict[str, Any]],
    reason: str,
) -> Dict[str, Any]:
    return {
        "intent_id": str(intent_id),
        "status": "failed",
        "agent_name": agent_name,
        "error": f"中断：{reason}",
        "completed": False,
        "completion_reason": reason,
        "turns": len(conversation_history) // 2,
        "recovery_interrupted": True,
    }


def maybe_append_query_before_finalize(
    openclaw: OpenClawWrapper,
    config: Dict[str, Any],
    query_pool: List[Dict[str, Any]],
    session_results: List[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    """在 session 收口前可选追加一条 search query。失败只记日志，不中断主流程。"""
    if not resolve_append_query_enabled(config) or not query_pool or not session_results:
        return None

    anchor_result = session_results[-1]
    selected_task = select_append_query_task(query_pool, anchor_result.get("intent_id"))
    if not selected_task:
        return None

    query = _task_prompt(selected_task)
    if not query:
        return {
            "status": "skipped",
            "query_task_id": _task_id(selected_task),
            "reason": "empty_query",
        }

    try:
        response = openclaw.send_message(
            query,
            timeout=config["generation"]["timeout"],
            thinking=resolve_openclaw_thinking_mode(config),
        )
        latest_session_info = openclaw.get_current_session_info()
        if latest_session_info:
            anchor_result["session_info"] = latest_session_info

        assistant_text = OpenClawWrapper.extract_assistant_text(response)
        return {
            "status": "success",
            "query_task_id": _task_id(selected_task),
            "query": query,
            "query_task_type": _task_type(selected_task),
            "assistant_preview": assistant_text[:200],
        }
    except Exception as exc:
        logger.warning("session 收口前追加 query 失败（task=%s）: %s", _task_id(selected_task), exc)
        return {
            "status": "failed",
            "query_task_id": _task_id(selected_task),
            "query": query,
            "query_task_type": _task_type(selected_task),
            "error": str(exc),
        }


def log_runtime_config_summary(args: argparse.Namespace, config: Dict[str, Any], config_path: str) -> None:
    """统一打印本次运行生效的关键配置。"""
    paths_config = config["paths"]
    generation_config = config.get("generation", {})
    openclaw_config = config.get("openclaw", {})
    llm_config = config.get("llm", {})

    logger.info("本次运行配置摘要：")
    logger.info("  num_workers=%s", openclaw_config.get("num_workers"))
    logger.info("  openclaw_model_url=%s", openclaw_config.get("model_url"))
    logger.info("  openclaw_model=%s", openclaw_config.get("model"))
    logger.info("  openclaw_enable_thinking=%s", openclaw_config.get("enable_thinking"))
    logger.info("  openclaw_thinking_level=%s", openclaw_config.get("thinking_level"))
    logger.info("  llm_base_url=%s", llm_config.get("base_url"))
    logger.info("  llm_model=%s", llm_config.get("model"))
    logger.info("  output_dir=%s", paths_config.get("output_dir"))
    logger.info("  intents_file=%s", paths_config.get("intents_file"))
    logger.info("  intents_per_session=%s", generation_config.get("intents_per_session"))
    logger.info("  append_query_enabled=%s", generation_config.get("append_query_enabled", False))
    logger.info("  append_query_file=%s", generation_config.get("append_query_file"))
    logger.info("  limit=%s", args.limit if args.limit is not None else "all")


def process_intent(
    intent_data: Dict[str, Any],
    agent_name: str,
    config: Dict[str, Any],
    llm: LLMClient,
    start_new_session: bool,
) -> Dict[str, Any]:
    """处理单个 task（兼容 intent 与 direct_query）。"""
    intent_id = _task_id(intent_data)
    task_type = _task_type(intent_data)
    logger.info(f"[{agent_name}] 开始处理 {task_type}: {intent_id}")

    openclaw = OpenClawWrapper(agent_name)
    try:
        if start_new_session:
            restore_workspace_snapshot(agent_name, config)
            openclaw.reset_main_session()

        if task_type == "direct_query":
            query = _task_prompt(intent_data)
            if not query:
                raise RuntimeError("direct_query 缺少 query")

            logger.info(f"[{agent_name}] Direct query: {query[:100]}...")
            openclaw.send_message(
                query,
                timeout=config["generation"]["timeout"],
                thinking=resolve_openclaw_thinking_mode(config),
            )

            session_info = openclaw.get_current_session_info()
            if not session_info:
                raise RuntimeError(f"[{agent_name}] 未找到 session 信息，无法归档")

            logger.info(f"[{agent_name}] ✓ Direct query {intent_id} 处理完成")
            return {
                "intent_id": str(intent_id),
                "status": "success",
                "agent_name": agent_name,
                "intent_data": intent_data,
                "session_info": session_info,
                "started_new_session": start_new_session,
                "finalized_session_after": False,
                "completed": True,
                "completion_reason": "direct_query_completed",
                "turns": 1,
                "task_type": task_type,
            }

        conversation_history = []
        max_turns = config["generation"].get("max_turns", 20)
        completed = False
        completion_reason = "reached_max_turns"
        is_success = True  # 默认为成功，只有 user model 明确标记 is_success=false 时才改为 False

        for turn in range(max_turns):
            if _shutdown_requested.is_set() or _runtime_recovery_requested.is_set():
                logger.info(f"[{agent_name}] 在 Turn {turn + 1} 前检测到恢复/退出请求，提前结束")
                return _build_interrupted_result(intent_id, agent_name, conversation_history, "runtime_recovery_requested")

            logger.info(f"[{agent_name}] Turn {turn + 1}/{max_turns}")

            llm_result = llm.generate_next_query(
                intent=intent_data["natural_language_intent"],
                persona=intent_data.get("metadata", {}).get("persona", {}),
                conversation_history=conversation_history,
            )

            if _shutdown_requested.is_set() or _runtime_recovery_requested.is_set():
                logger.info(f"[{agent_name}] LLM 返回后检测到恢复/退出请求，跳过后续发送")
                return _build_interrupted_result(intent_id, agent_name, conversation_history, "runtime_recovery_requested")

            if llm_result.get("completed", False):
                is_success = llm_result.get("is_success", True)  # 从 user model 读取
                status_label = "成功" if is_success else "失败"
                logger.info(f"[{agent_name}] 任务完成({status_label}): {llm_result.get('reason', '')}")
                completed = True
                completion_reason = llm_result.get("reason", "completed")
                break

            query = llm_result.get("query", "").strip()
            if not query:
                logger.warning(f"[{agent_name}] LLM 未生成 query")
                completion_reason = llm_result.get("reason", "empty_query")
                break

            logger.info(f"[{agent_name}] Query: {query[:100]}...")

            response = openclaw.send_message(
                query,
                timeout=config["generation"]["timeout"],
                thinking=resolve_openclaw_thinking_mode(config),
            )

            if _shutdown_requested.is_set() or _runtime_recovery_requested.is_set():
                logger.info(f"[{agent_name}] OpenClaw 返回后检测到恢复/退出请求，提前结束当前 intent")
                return _build_interrupted_result(intent_id, agent_name, conversation_history, "runtime_recovery_requested")

            assistant_text = OpenClawWrapper.extract_assistant_text(response)

            conversation_history.append({"role": "user", "content": query})
            conversation_history.append({"role": "assistant", "content": assistant_text})

        # 只有当 LLM 明确完成流程时，才进行 session 归档和中间格式转换
        if not completed:
            logger.warning(f"[{agent_name}] Intent {intent_id} 未完全完成（completion_reason={completion_reason}），跳过归档和转换")
            try:
                openclaw.reset_main_session()
            except Exception as reset_error:
                logger.warning(f"[{agent_name}] reset 失败: {reset_error}")
            return {
                "intent_id": str(intent_id),
                "status": "failed",
                "agent_name": agent_name,
                "error": f"未完成：{completion_reason}",
                "completed": completed,
                "completion_reason": completion_reason,
                "turns": len(conversation_history) // 2,
            }

        session_info = openclaw.get_current_session_info()
        if not session_info:
            raise RuntimeError(f"[{agent_name}] 未找到 session 信息，无法归档")

        # 根据 is_success 决定最终状态
        final_status = "success" if is_success else "failed"

        logger.info(f"[{agent_name}] ✓ Intent {intent_id} 处理完成 (status={final_status})")
        return {
            "intent_id": str(intent_id),
            "status": final_status,
            "agent_name": agent_name,
            "intent_data": intent_data,
            "session_info": session_info,
            "started_new_session": start_new_session,
            "finalized_session_after": False,
            "completed": completed,
            "completion_reason": completion_reason,
            "turns": len(conversation_history) // 2,
            "task_type": task_type,
            "is_success": is_success,
        }

    except Exception as e:
        try:
            openclaw.reset_main_session()
        except Exception as reset_error:
            logger.warning(f"[{agent_name}] reset 失败: {reset_error}")
        logger.error(f"[{agent_name}] ✗ Task {intent_id} 失败: {e}")
        return {
            "intent_id": str(intent_id),
            "status": "failed",
            "agent_name": agent_name,
            "error": str(e),
        }


def worker_loop(
    agent_name: str,
    task_queue: "queue.Queue[Dict[str, Any]]",
    config: Dict[str, Any],
    progress: ProgressTracker,
    append_query_pool: List[Dict[str, Any]],
    worker_tools: List[Dict[str, Any]],
    llm: LLMClient,
    converter: DataConverter,
) -> List[Dict[str, Any]]:
    """单个 worker 串行消费 intent 队列，但多个 worker 之间并发。

    Args:
        agent_name: worker agent 名称
        task_queue: 任务队列
        config: 配置
        progress: 进度跟踪器

    Returns:
        处理结果列表
    """

    openclaw = OpenClawWrapper(agent_name)

    if not worker_tools:
        logger.warning(f"Worker {agent_name} 未找到工具缓存，使用空列表（将退回 session 元数据）")
        worker_tools = []

    results: List[Dict[str, Any]] = []
    intents_per_session = resolve_intents_per_session(config)
    intents_in_current_session = 0
    pending_session_results: List[Dict[str, Any]] = []

    try:
        restored_snapshot = restore_worker_runtime_snapshot(agent_name, config, openclaw)
    except Exception as exc:
        logger.warning("[%s] 恢复 worker snapshot 失败，回退为全新 session: %s", agent_name, exc)
        clear_worker_runtime_snapshot(agent_name, config)
        restored_snapshot = None

    if restored_snapshot:
        pending_session_results = list(restored_snapshot.get("pending_results", []))
        intents_in_current_session = int(restored_snapshot.get("intents_in_current_session", 0))

    while True:
        if _shutdown_requested.is_set() or _runtime_recovery_requested.is_set():
            break

        try:
            intent_data = task_queue.get_nowait()
        except queue.Empty:
            break

        start_new_session = intents_in_current_session == 0
        finalize_session_after = intents_in_current_session + 1 >= intents_per_session

        result = process_intent(
            intent_data,
            agent_name,
            config,
            llm,
            start_new_session=start_new_session,
        )

        if result.get("status") == "success":
            intents_in_current_session += 1
            should_finalize_session = finalize_session_after or task_queue.empty()

            if should_finalize_session:
                session_results = [*pending_session_results, result]
                appended_query_meta = maybe_append_query_before_finalize(
                    openclaw,
                    config,
                    append_query_pool,
                    session_results,
                )
                session_batch_metadata = build_session_batch_metadata(
                    session_results,
                    result["intent_id"],
                    appended_query=appended_query_meta,
                )
                final_session_file = build_archived_session_path(
                    config["paths"],
                    result["intent_id"],
                    agent_name,
                    result["session_info"]["sessionId"],
                )
                archive_meta = openclaw.archive_current_session(str(final_session_file), move_file=True)

                for pending_result in pending_session_results:
                    pending_result.update(
                        {
                            "session_id": archive_meta.get("session_id"),
                            "session_archive_mode": "final-intent-only",
                            "materialized_output": False,
                            "finalized_session_after": False,
                            "session_finalized_by_intent_id": result["intent_id"],
                        }
                    )
                    progress.record(pending_result)
                    results.append(pending_result)

                result["finalized_session_after"] = True
                result["session_batch_metadata"] = session_batch_metadata
                finalized_result = materialize_intent_output(
                    result,
                    final_session_file,
                    config,
                    worker_tools,
                    converter,
                    agent_name,
                    session_archive_mode=archive_meta.get("archive_mode", "move"),
                )
                finalized_result["materialized_output"] = True
                finalized_result["session_member_count"] = len(pending_session_results) + 1

                try:
                    openclaw.reset_main_session()
                except Exception as reset_error:
                    logger.warning(f"[{agent_name}] finalize 后 reset 失败: {reset_error}")

                progress.record(finalized_result)
                results.append(finalized_result)

                clear_worker_runtime_snapshot(agent_name, config)
                pending_session_results = []
                intents_in_current_session = 0
            else:
                pending_session_results.append(result)
                try:
                    save_worker_runtime_snapshot(
                        agent_name,
                        config,
                        openclaw,
                        pending_session_results,
                        intents_in_current_session,
                    )
                except Exception as exc:
                    logger.warning("[%s] 保存 worker snapshot 失败，将无法续跑当前 pending session: %s", agent_name, exc)
        else:
            if pending_session_results:
                logger.warning(
                    "[%s] 当前 session 中途失败，失败 intent 记为 failed；恢复最近成功快照并继续后续 intents，pending=%s",
                    agent_name,
                    len(pending_session_results),
                )
                try:
                    restored_snapshot = restore_worker_runtime_snapshot(agent_name, config, openclaw)
                except Exception as exc:
                    logger.warning("[%s] 恢复 worker snapshot 失败，pending session 将被清空: %s", agent_name, exc)
                    clear_worker_runtime_snapshot(agent_name, config)
                    restored_snapshot = None
                if restored_snapshot:
                    pending_session_results = list(restored_snapshot.get("pending_results", []))
                    intents_in_current_session = int(restored_snapshot.get("intents_in_current_session", 0))
                else:
                    pending_session_results = []
                    intents_in_current_session = 0
            else:
                logger.warning(
                    "[%s] intent=%s 失败且无 pending session，直接记 failed",
                    agent_name,
                    intent_data.get("id"),
                )
                clear_worker_runtime_snapshot(agent_name, config)
                intents_in_current_session = 0

        # 运行时全局恢复：若失败像是 openclaw.json 被污染，通知主线程停止当前批次并重跑
        if result.get("status") == "failed" and not result.get("recovery_interrupted"):
            runtime_paths = resolve_openclaw_runtime_paths()
            error_message = str(result.get("error", ""))
            if looks_like_config_corruption_error(
                error_message,
                runtime_paths["config_file"],
                runtime_paths["baseline_file"],
            ):
                intent_id = str(intent_data.get("id", "unknown"))
                logger.warning("[%s] intent=%s 检测到疑似配置污染，触发全局恢复", agent_name, intent_id)
                request_runtime_recovery(error_message)
                result["triggered_global_recovery"] = True

        if result.get("status") != "success":
            progress.record(result)
            results.append(result)
        task_queue.task_done()

        if _runtime_recovery_requested.is_set():
            break

    if pending_session_results:
        if not _shutdown_requested.is_set() and not _runtime_recovery_requested.is_set() and task_queue.empty():
            logger.info("[%s] 无更多待处理 intent，收口当前 pending session（count=%s）", agent_name, len(pending_session_results))

            final_pending_result = pending_session_results[-1]
            previous_pending_results = pending_session_results[:-1]
            appended_query_meta = maybe_append_query_before_finalize(
                openclaw,
                config,
                append_query_pool,
                pending_session_results,
            )
            session_batch_metadata = build_session_batch_metadata(
                pending_session_results,
                final_pending_result["intent_id"],
                appended_query=appended_query_meta,
            )
            final_session_file = build_archived_session_path(
                config["paths"],
                final_pending_result["intent_id"],
                agent_name,
                final_pending_result["session_info"]["sessionId"],
            )
            archive_meta = openclaw.archive_current_session(str(final_session_file), move_file=True)

            for pending_result in previous_pending_results:
                pending_result.update(
                    {
                        "session_id": archive_meta.get("session_id"),
                        "session_archive_mode": "final-intent-only",
                        "materialized_output": False,
                        "finalized_session_after": False,
                        "session_finalized_by_intent_id": final_pending_result["intent_id"],
                    }
                )
                progress.record(pending_result)
                results.append(pending_result)

            final_pending_result["finalized_session_after"] = True
            final_pending_result["session_batch_metadata"] = session_batch_metadata
            finalized_result = materialize_intent_output(
                final_pending_result,
                final_session_file,
                config,
                worker_tools,
                converter,
                agent_name,
                session_archive_mode=archive_meta.get("archive_mode", "move"),
            )
            finalized_result["materialized_output"] = True
            finalized_result["session_member_count"] = len(pending_session_results)
            progress.record(finalized_result)
            results.append(finalized_result)

            clear_worker_runtime_snapshot(agent_name, config)
            try:
                openclaw.reset_main_session()
            except Exception as reset_error:
                logger.warning(f"[{agent_name}] worker 收尾 finalize 后 reset 失败: {reset_error}")
        else:
            logger.warning(
                "[%s] worker 结束时仍有 %s 个未完成 session 收口的 intents；已保留 worker snapshot，后续可续跑",
                agent_name,
                len(pending_session_results),
            )
            try:
                openclaw.reset_main_session()
            except Exception as reset_error:
                logger.warning(f"[{agent_name}] worker 收尾 reset 失败: {reset_error}")

    return results


def main():
    global _active_agents, _active_config, _executor

    parser = argparse.ArgumentParser(description="OpenClaw 数据生成")
    parser.add_argument("--config", help="配置文件")
    parser.add_argument("--intents-file", help="覆盖配置中的 intents 文件路径")
    parser.add_argument("--limit", type=int, help="限制处理数量")
    parser.add_argument("--concurrent", type=int, help="并发数")
    parser.add_argument("--intents-per-session", type=int, help="每个 worker 连续处理多少个 intent 后再重置 session")
    parser.add_argument("--refresh-tools", action="store_true", help="启动前强制刷新完整 tools catalog")
    args = parser.parse_args()

    # 注册信号处理器
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    config_path = resolve_config_path(args.config)
    config = load_config(args.config, cli_args=args)
    paths_config = config["paths"]
    _active_config = config  # 设置全局配置

    setup_logging(paths_config["logs_dir"])
    ensure_dir(paths_config["output_dir"])
    ensure_dir(paths_config["sessions_dir"])
    ensure_dir(paths_config["middle_format_dir"])
    ensure_dir(str(resolve_worker_snapshot_root(paths_config)))

    logger.info("=" * 60)
    logger.info("OpenClaw 数据生成开始")
    logger.info("=" * 60)

    log_runtime_config_summary(args, config, config_path)

    num_workers = int(config["openclaw"]["num_workers"])
    intents_per_session = resolve_intents_per_session(config)
    worker_prefix = config["openclaw"].get("worker_prefix", "gendata-worker")
    workspace_root = config["openclaw"].get("workspace_root")
    ensure_result = ensure_agents(
        num_agents=num_workers,
        worker_prefix=worker_prefix,
        workspace_root=workspace_root,
    )
    logger.info(
        "worker agents 就绪，已存在 %s 个，新建 %s 个，已删除 %s 个",
        len(ensure_result["existing"]),
        len(ensure_result["created"]),
        len(ensure_result.get("deleted", [])),
    )

    if (ensure_result["created"] or ensure_result.get("deleted")) and apply_runtime_patch_from_env():
        logger.info("已在 run_generation.ensure_agents 后恢复 OpenClaw runtime config")

    # 注意：在 ensure_agents 完成后再备份 baseline，避免把异常状态保存进去。
    backup_openclaw_config_to_output(paths_config)

    from scripts.init_agents import capture_all_agents_runtime_metadata
    project_root = resolve_project_root()
    runtime_metadata_cache_file = resolve_runtime_metadata_cache_file(paths_config=paths_config)
    worker_ids = [f"{worker_prefix}-{i+1}" for i in range(num_workers)]

    # 如果需要刷新运行时 metadata，重新捕获所有 agents 的 tools/system prompt
    if args.refresh_tools:
        logger.info("刷新所有 %s 个 agents 的运行时 metadata...", num_workers)
        try:
            capture_all_agents_runtime_metadata(worker_ids, runtime_metadata_cache_file, project_root)
            logger.info("✓ 运行时 metadata 已保存到 %s", runtime_metadata_cache_file)
        except Exception as e:
            logger.error(f"刷新运行时 metadata 失败: {e}")

    intents_file = paths_config["intents_file"]
    logger.info(f"使用 intents 文件: {intents_file}")
    intents = load_intents(intents_file)
    logger.info(f"加载 {len(intents)} 个 tasks")

    append_query_file = resolve_append_query_file(config)
    append_query_pool: List[Dict[str, Any]] = []
    if append_query_file:
        logger.info("加载 session 收口追加 query 文件: %s", append_query_file)
        append_query_pool = [
            task for task in load_intents(append_query_file)
            if _task_type(task) == "direct_query"
        ]
        logger.info("追加 query 池大小: %s", len(append_query_pool))

    if args.limit:
        intents = intents[:args.limit]

    progress = ProgressTracker(paths_config["progress_file"])
    max_global_restarts = int(os.environ.get("OPENCLAW_CONFIG_MAX_AUTO_RESTARTS", "20"))
    global_restart_count = 0
    all_results: List[Dict[str, Any]] = []

    # 设置活跃的 agent 列表（用于 Ctrl+C 清理）
    _active_agents = [f"{worker_prefix}-{i+1}" for i in range(num_workers)]

    shared_tools = load_agent_tools(runtime_metadata_cache_file, "shared")
    shared_llm = create_llm_client(config)
    shared_converter = DataConverter(runtime_metadata_cache_file=runtime_metadata_cache_file)

    while True:
        snapshot_pending_intent_ids = list_pending_snapshot_intent_ids(paths_config)
        pending_intents = [
            intent
            for intent in intents
            if not progress.is_success(str(intent.get("id", "unknown")))
            and str(intent.get("id", "unknown")) not in snapshot_pending_intent_ids
        ]

        if not pending_intents and not snapshot_pending_intent_ids:
            logger.info("没有待处理的 intents，当前任务已全部完成")
            break

        logger.info(
            "开始一轮 generation：待处理 %s，snapshot 挂起 %s，已触发自动恢复 %s/%s 次",
            len(pending_intents),
            len(snapshot_pending_intent_ids),
            global_restart_count,
            max_global_restarts,
        )

        _runtime_recovery_requested.clear()
        consume_runtime_recovery_state()

        task_queue: "queue.Queue[Dict[str, Any]]" = queue.Queue()
        for intent in pending_intents:
            task_queue.put(intent)

        round_results: List[Dict[str, Any]] = []
        with ThreadPoolExecutor(max_workers=num_workers) as executor:
            _executor = executor  # 设置全局 executor（用于 Ctrl+C 终止）
            futures = []
            for worker_index in range(1, num_workers + 1):
                agent_name = f"{worker_prefix}-{worker_index}"
                future = executor.submit(
                    worker_loop,
                    agent_name,
                    task_queue,
                    config,
                    progress,
                    append_query_pool,
                    shared_tools,
                    shared_llm,
                    shared_converter,
                )
                futures.append(future)

            for future in as_completed(futures):
                worker_results = future.result()
                round_results.extend(worker_results)

        all_results.extend(round_results)

        if not _runtime_recovery_requested.is_set():
            logger.info("当前轮次未触发全局恢复")
            break

        reason, requested_at = consume_runtime_recovery_state()
        reason = reason or "unknown"
        if requested_at is not None:
            logger.warning(
                "全局恢复触发后等待本轮 worker 收口耗时 %.2fs",
                time.perf_counter() - requested_at,
            )
        logger.warning("检测到配置污染风险，准备停止并恢复后重跑。原因: %s", reason)

        if global_restart_count >= max_global_restarts:
            logger.error("已达到自动恢复上限（%s 次），停止自动重启", max_global_restarts)
            break

        recovery_started_at = time.perf_counter()
        recovered = recover_openclaw_runtime_from_baseline(reason)
        logger.warning("运行时恢复阶段耗时 %.2fs", time.perf_counter() - recovery_started_at)
        if not recovered:
            logger.error("自动恢复失败，停止自动重启")
            break

        cleanup_started_at = time.perf_counter()
        cleanup_agents(_active_agents, config)
        logger.warning("恢复后的 cleanup 阶段耗时 %.2fs", time.perf_counter() - cleanup_started_at)
        global_restart_count += 1
        logger.warning("自动恢复完成，准备重新运行 generation（第 %s 次）", global_restart_count)

    final_summary = summarize_final_results(all_results)
    logger.info(
        "完成: 最终成功 %s, 最终失败 %s, 总尝试 %s",
        final_summary["success"],
        final_summary["failed"],
        final_summary["attempt_total"],
    )

    save_json(
        {
            "total": final_summary["total"],
            "success": final_summary["success"],
            "failed": final_summary["failed"],
            "results": final_summary["results"],
            "attempt_total": final_summary["attempt_total"],
            "attempt_results": final_summary["attempt_results"],
            "global_auto_restarts": global_restart_count,
            "global_auto_restart_limit": max_global_restarts,
        },
        f"{paths_config['output_dir']}/summary.json",
    )

    # 正常退出时清理所有 agents
    logger.info("正常退出，清理 agents 资源...")
    cleanup_agents(_active_agents, config)


if __name__ == "__main__":
    main()
