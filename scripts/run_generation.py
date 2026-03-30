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

from src.config import load_config
from src.agent_runtime import cleanup_agents, restore_workspace_snapshot
from src.intent_loader import load_intents
from src.openclaw_wrapper import OpenClawWrapper, ensure_agents
from src.llm_client import LLMClient
from src.converter import DataConverter
from src.runtime_config import apply_runtime_patch_from_env
from src.runtime_recovery import (
    backup_openclaw_config_to_output,
    looks_like_config_corruption_error,
    recover_openclaw_runtime_from_baseline,
    resolve_openclaw_runtime_paths,
)
from src.utils import ensure_dir, load_json, save_json, setup_logging

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


def resolve_project_root() -> Path:
    """解析项目根目录。"""
    if '__file__' in globals():
        return Path(__file__).parent.parent
    return Path(os.getcwd())


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
    )


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


class ProgressTracker:
    """线程安全的进度记录器。"""

    def __init__(self, progress_file: str):
        self.progress_file = Path(progress_file)
        self.lock = threading.Lock()
        if self.progress_file.exists():
            self.data = load_json(str(self.progress_file))
        else:
            self.data = {"items": {}, "summary": {}}

    def is_success(self, intent_id: str) -> bool:
        item = self.data.get("items", {}).get(intent_id)
        return bool(item and item.get("status") == "success")

    def record(self, result: Dict[str, Any]) -> None:
        intent_id = str(result["intent_id"])
        with self.lock:
            self.data.setdefault("items", {})[intent_id] = result
            total = len(self.data["items"])
            success = sum(1 for item in self.data["items"].values() if item.get("status") == "success")
            failed = sum(1 for item in self.data["items"].values() if item.get("status") == "failed")
            self.data["summary"] = {
                "total_recorded": total,
                "success": success,
                "failed": failed,
            }
            save_json(self.data, str(self.progress_file))


def load_agent_tools(cache_file: str, agent_id: str) -> List[Dict[str, Any]]:
    """从缓存文件加载特定 agent 的工具列表。

    Args:
        cache_file: 缓存文件路径
        agent_id: agent 名称

    Returns:
        工具列表（OpenAI format），如果不存在则返回空列表
    """
    cache_path = Path(cache_file)
    if not cache_path.exists():
        logger.warning(f"工具缓存文件不存在: {cache_file}")
        return []

    try:
        data = load_json(str(cache_path))

        # 新格式：{agent_id: [tools...]}
        if isinstance(data, dict) and agent_id in data:
            tools = data[agent_id]
            logger.info(f"已加载 agent {agent_id} 的工具列表，共 {len(tools)} 个工具")
            return tools
        else:
            logger.warning(f"未找到 agent {agent_id} 的工具列表")
            return []
    except Exception as e:
        logger.error(f"加载 agent {agent_id} 的工具列表失败: {e}")
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




def process_intent(
    intent_data: Dict[str, Any],
    agent_name: str,
    config: Dict[str, Any],
    tools_catalog: List[Dict[str, Any]],
    llm: LLMClient,
    converter: DataConverter,
) -> Dict[str, Any]:
    """处理单个 intent。"""
    intent_id = intent_data.get("id", "unknown")
    logger.info(f"[{agent_name}] 开始处理 intent: {intent_id}")

    openclaw = OpenClawWrapper(agent_name)
    try:
        # 在 session 开始前恢复 workspace 快照
        restore_workspace_snapshot(agent_name, config)

        openclaw.reset_main_session()

        conversation_history = []
        max_turns = config["generation"].get("max_turns", 20)
        completed = False
        completion_reason = "reached_max_turns"

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
                logger.info(f"[{agent_name}] 任务完成: {llm_result.get('reason', '')}")
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
                thinking=config["openclaw"].get("thinking"),
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

        paths_config = config["paths"]
        sessions_dir = Path(paths_config["sessions_dir"])
        archived_session_file = sessions_dir / f"intent_{intent_id}__{agent_name}__{session_info['sessionId']}.jsonl"
        archive_meta = openclaw.archive_current_session(str(archived_session_file))

        output_file = Path(paths_config["middle_format_dir"]) / f"intent_{intent_id}.json"
        converter.convert_session_to_middle_format(
            session_file=str(archived_session_file),
            intent_data=intent_data,
            output_file=str(output_file),
            tools_catalog=tools_catalog,
            available_tool_entries=extract_available_tool_entries(archive_meta["session_info"]),
            skills=extract_skills(archive_meta["session_info"]),
            session_metadata=archive_meta["session_info"],
        )

        openclaw.reset_main_session()

        logger.info(f"[{agent_name}] ✓ Intent {intent_id} 处理完成")
        return {
            "intent_id": str(intent_id),
            "status": "success",
            "agent_name": agent_name,
            "output_file": str(output_file),
            "session_file": str(archived_session_file),
            "session_id": archive_meta["session_id"],
            "completed": completed,
            "completion_reason": completion_reason,
            "turns": len(conversation_history) // 2,
        }

    except Exception as e:
        try:
            openclaw.reset_main_session()
        except Exception as reset_error:
            logger.warning(f"[{agent_name}] reset 失败: {reset_error}")
        logger.error(f"[{agent_name}] ✗ Intent {intent_id} 失败: {e}")
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
    tools_cache_file: str,
    progress: ProgressTracker,
) -> List[Dict[str, Any]]:
    """单个 worker 串行消费 intent 队列，但多个 worker 之间并发。

    Args:
        agent_name: worker agent 名称
        task_queue: 任务队列
        config: 配置
        tools_cache_file: 工具缓存文件路径
        progress: 进度跟踪器

    Returns:
        处理结果列表
    """

    # 为当前 worker 的 agent 加载工具列表
    worker_tools = load_agent_tools(tools_cache_file, agent_name)
    llm = create_llm_client(config)
    converter = DataConverter()

    if not worker_tools:
        logger.warning(f"Worker {agent_name} 未找到工具缓存，使用空列表（将退回 session 元数据）")
        worker_tools = []

    results: List[Dict[str, Any]] = []
    while True:
        if _shutdown_requested.is_set() or _runtime_recovery_requested.is_set():
            break

        try:
            intent_data = task_queue.get_nowait()
        except queue.Empty:
            break

        result = process_intent(intent_data, agent_name, config, worker_tools, llm, converter)

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

        progress.record(result)
        results.append(result)
        task_queue.task_done()

        if _runtime_recovery_requested.is_set():
            break

    return results


def main():
    global _active_agents, _active_config, _executor

    parser = argparse.ArgumentParser(description="OpenClaw 数据生成")
    parser.add_argument("--config", default="config/config.yaml", help="配置文件")
    parser.add_argument("--intents-file", help="覆盖配置中的 intents 文件路径")
    parser.add_argument("--limit", type=int, help="限制处理数量")
    parser.add_argument("--concurrent", type=int, help="并发数")
    parser.add_argument("--refresh-tools", action="store_true", help="启动前强制刷新完整 tools catalog")
    args = parser.parse_args()

    config = load_config(args.config)
    paths_config = config["paths"]
    _active_config = config  # 设置全局配置

    # 注册信号处理器
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    setup_logging(paths_config["logs_dir"])
    ensure_dir(paths_config["output_dir"])
    ensure_dir(paths_config["sessions_dir"])
    ensure_dir(paths_config["middle_format_dir"])

    logger.info("=" * 60)
    logger.info("OpenClaw 数据生成开始")
    logger.info("=" * 60)

    num_workers = args.concurrent or config["openclaw"]["num_workers"]
    worker_prefix = config["openclaw"].get("worker_prefix", "gendata-worker")
    workspace_root = config["openclaw"].get("workspace_root")
    ensure_result = ensure_agents(
        num_agents=num_workers,
        worker_prefix=worker_prefix,
        workspace_root=workspace_root,
        add_tools=True,  # 生成数据时启用工具白名单
        tools_allow=config["openclaw"].get("worker_tools_allow"),
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

    # 如果需要刷新工具列表，重新生成所有 agents 的工具
    if args.refresh_tools:
        from scripts.init_agents import generate_all_agents_tools
        project_root = resolve_project_root()
        tools_cache_file = paths_config["tools_cache_file"]
        worker_ids = [f"{worker_prefix}-{i+1}" for i in range(num_workers)]

        logger.info(f"刷新所有 {num_workers} 个 agents 的工具列表...")
        try:
            generate_all_agents_tools(worker_ids, tools_cache_file, project_root)
            logger.info(f"✓ 工具列表已保存到 {tools_cache_file}")
        except Exception as e:
            logger.error(f"刷新工具列表失败: {e}")
            # 继续执行，使用现有缓存或退回 session 元数据

    intents_file = args.intents_file or paths_config["intents_file"]
    logger.info(f"使用 intents 文件: {intents_file}")
    intents = load_intents(intents_file)
    logger.info(f"加载 {len(intents)} 个 intents")

    if args.limit:
        intents = intents[:args.limit]

    progress = ProgressTracker(paths_config["progress_file"])
    max_global_restarts = int(os.environ.get("OPENCLAW_CONFIG_MAX_AUTO_RESTARTS", "20"))
    global_restart_count = 0
    all_results: List[Dict[str, Any]] = []

    logger.info(f"并发数: {num_workers}")

    # 设置活跃的 agent 列表（用于 Ctrl+C 清理）
    _active_agents = [f"{worker_prefix}-{i+1}" for i in range(num_workers)]

    while True:
        pending_intents = [intent for intent in intents if not progress.is_success(str(intent.get("id", "unknown")))]

        if not pending_intents:
            logger.info("没有待处理的 intents，当前任务已全部完成")
            break

        logger.info(
            "开始一轮 generation：待处理 %s，已触发自动恢复 %s/%s 次",
            len(pending_intents),
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
                    paths_config["tools_cache_file"],
                    progress
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
