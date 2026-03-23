"""主生成脚本 - 生成、归档、转换、resume 一体化。"""
import argparse
import json
import logging
import queue
import shutil
import subprocess
import sys
import threading
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.config import load_config
from src.intent_loader import load_intents
from src.openclaw_wrapper import OpenClawWrapper, ensure_agents
from src.llm_client import LLMClient
from src.converter import DataConverter
from src.utils import ensure_dir, load_json, save_json, setup_logging

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


def load_tools_catalog(cache_file: str) -> List[Dict[str, Any]]:
    """加载预生成的工具定义（兼容旧格式）。

    注意：此函数用于加载所有 agent 共享的工具列表（旧格式）
    新格式应使用 load_agent_tools()
    """
    cache_path = Path(cache_file)
    try:
        if cache_path.exists():
            return load_json(str(cache_path))
        logger.info("未发现 tools catalog")
        return []
    except Exception as exc:
        logger.warning("加载 tools catalog 失败，将退回到 session 元数据兜底: %s", exc)
        return []


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


def generate_tools_catalog(project_root: Path, cache_file: str) -> List[Dict[str, Any]]:
    """调用本地 dump_tools 脚本生成完整 tools catalog。"""
    cache_path = Path(cache_file)
    ensure_dir(str(cache_path.parent))

    script_path = project_root / "tools" / "fetch_tools" / "dump_tools.mjs"
    if not script_path.exists():
        raise FileNotFoundError(f"Tools dump script not found: {script_path}")

    result = subprocess.run(
        ["node", str(script_path)],
        cwd=str(project_root),
        capture_output=True,
        text=True,
        timeout=180,
    )

    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "tools catalog generation failed")

    tools_catalog = json.loads(result.stdout)
    save_json(tools_catalog, str(cache_path))
    return tools_catalog


def ensure_tools_catalog(project_root: Path, cache_file: str, refresh: bool = False) -> List[Dict[str, Any]]:
    """确保完整 tools catalog 可用；失败时退回 session 元数据兜底。"""
    cache_path = Path(cache_file)
    if refresh and cache_path.exists():
        logger.info("按请求刷新 tools catalog: %s", cache_path)
        cache_path.unlink()

    tools_catalog = load_tools_catalog(cache_file)
    if tools_catalog:
        logger.info("已加载 tools catalog，共 %s 个工具", len(tools_catalog))
        return tools_catalog

    try:
        logger.info("开始自动生成完整 tools catalog...")
        tools_catalog = generate_tools_catalog(project_root, cache_file)
        logger.info("tools catalog 生成完成，共 %s 个工具", len(tools_catalog))
        return tools_catalog
    except Exception as exc:
        logger.warning("自动生成 tools catalog 失败，将退回到 session 元数据兜底: %s", exc)
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


def restore_workspace_snapshot(agent_name: str, config: Dict[str, Any]) -> None:
    """从快照恢复 agent workspace。

    Args:
        agent_name: agent 名称
        config: 配置字典
    """
    from src.openclaw_wrapper import expected_agent_workspace, resolve_workspace_root

    workspace_root = config["openclaw"].get("workspace_root")
    root_dir = resolve_workspace_root(workspace_root)
    workspace = expected_agent_workspace(agent_name, str(root_dir))

    # 快照路径
    project_root = Path(__file__).parent.parent
    snapshot_path = project_root / "output" / "workspace_snapshots" / agent_name

    if not snapshot_path.exists():
        logger.warning(f"Agent {agent_name} 的快照不存在，跳过恢复: {snapshot_path}")
        return

    # 删除当前 workspace 的内容（保留 .git）
    if workspace.exists():
        for item in workspace.iterdir():
            if item.name == '.git':
                continue
            try:
                if item.is_dir():
                    shutil.rmtree(item)
                else:
                    item.unlink()
            except Exception as e:
                logger.warning(f"删除 {item} 失败: {e}")

    # 从快照恢复（排除 .git）
    for item in snapshot_path.iterdir():
        if item.name == '.git':
            continue
        dest = workspace / item.name
        try:
            if item.is_dir():
                shutil.copytree(item, dest, symlinks=False)
            else:
                shutil.copy2(item, dest)
        except Exception as e:
            logger.warning(f"恢复 {item} 失败: {e}")

    logger.info(f"已从快照恢复 agent {agent_name} 的 workspace")


def process_intent(
    intent_data: Dict[str, Any],
    agent_name: str,
    config: Dict[str, Any],
    tools_catalog: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """处理单个 intent。"""
    intent_id = intent_data.get("id", "unknown")
    logger.info(f"[{agent_name}] 开始处理 intent: {intent_id}")

    openclaw = OpenClawWrapper(agent_name)
    try:
        # 在 session 开始前恢复 workspace 快照
        restore_workspace_snapshot(agent_name, config)

        openclaw.reset_main_session()

        llm = LLMClient(
            base_url=config["llm"]["base_url"],
            api_key=config["llm"]["api_key"],
            model=config["llm"]["model"],
            temperature=config["llm"]["temperature"]
        )
        converter = DataConverter()

        conversation_history = []
        max_turns = config["generation"].get("max_turns", 20)
        completed = False
        completion_reason = "reached_max_turns"

        for turn in range(max_turns):
            logger.info(f"[{agent_name}] Turn {turn + 1}/{max_turns}")

            llm_result = llm.generate_next_query(
                intent=intent_data["natural_language_intent"],
                persona=intent_data.get("metadata", {}).get("persona", {}),
                conversation_history=conversation_history,
            )

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

            assistant_text = OpenClawWrapper.extract_assistant_text(response)

            conversation_history.append({"role": "user", "content": query})
            conversation_history.append({"role": "assistant", "content": assistant_text})

        session_info = openclaw.get_current_session_info()
        if not session_info:
            raise RuntimeError(f"[{agent_name}] 未找到 session 信息，无法归档")

        sessions_dir = Path(config["paths"]["sessions_dir"])
        archived_session_file = sessions_dir / f"intent_{intent_id}__{agent_name}__{session_info['sessionId']}.jsonl"
        archive_meta = openclaw.archive_current_session(str(archived_session_file))

        output_file = Path(config["paths"]["middle_format_dir"]) / f"intent_{intent_id}.json"
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
        return {"intent_id": str(intent_id), "status": "failed", "agent_name": agent_name, "error": str(e)}


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

    if not worker_tools:
        logger.warning(f"Worker {agent_name} 未找到工具缓存，使用空列表（将退回 session 元数据）")
        worker_tools = []

    results: List[Dict[str, Any]] = []
    while True:
        try:
            intent_data = task_queue.get_nowait()
        except queue.Empty:
            break

        result = process_intent(intent_data, agent_name, config, worker_tools)
        progress.record(result)
        results.append(result)
        task_queue.task_done()

    return results


def main():
    parser = argparse.ArgumentParser(description="OpenClaw 数据生成")
    parser.add_argument("--config", default="config/config.yaml", help="配置文件")
    parser.add_argument("--limit", type=int, help="限制处理数量")
    parser.add_argument("--concurrent", type=int, help="并发数")
    parser.add_argument("--refresh-tools", action="store_true", help="启动前强制刷新完整 tools catalog")
    parser.add_argument("--enable-sandbox",action="store_true",default=False,help="为 worker agents 启用沙箱模式（默认: false）")
    args = parser.parse_args()

    config = load_config(args.config)
    setup_logging(config["paths"]["logs_dir"])
    ensure_dir(config["paths"]["output_dir"])
    ensure_dir(config["paths"]["sessions_dir"])
    ensure_dir(config["paths"]["middle_format_dir"])

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
    )
    logger.info(
        "worker agents 就绪，已存在 %s 个，新建 %s 个，已删除 %s 个",
        len(ensure_result["existing"]),
        len(ensure_result["created"]),
        len(ensure_result.get("deleted", [])),
    )

    # 如果启用沙箱，为所有 worker agents 配置
    if args.enable_sandbox:
        from src.openclaw_wrapper import configure_sandbox, WORKER_SANDBOX_CONFIG
        worker_ids = [f"{worker_prefix}-{i+1}" for i in range(num_workers)]
        logger.info(f"启用沙箱模式，配置 {len(worker_ids)} 个 worker agents...")
        configure_sandbox(worker_ids, WORKER_SANDBOX_CONFIG)
        logger.info("✓ 沙箱配置完成")

    # 如果需要刷新工具列表，重新生成所有 agents 的工具
    if args.refresh_tools:
        from scripts.init_agents import generate_all_agents_tools
        project_root = Path(__file__).parent.parent
        tools_cache_file = config["paths"]["tools_cache_file"]
        worker_ids = [f"{worker_prefix}-{i+1}" for i in range(num_workers)]

        logger.info(f"刷新所有 {num_workers} 个 agents 的工具列表...")
        try:
            generate_all_agents_tools(worker_ids, tools_cache_file, project_root)
            logger.info(f"✓ 工具列表已保存到 {tools_cache_file}")
        except Exception as e:
            logger.error(f"刷新工具列表失败: {e}")
            # 继续执行，使用现有缓存或退回 session 元数据

    intents = load_intents(config["paths"]["intents_file"])
    logger.info(f"加载 {len(intents)} 个 intents")

    if args.limit:
        intents = intents[:args.limit]

    progress = ProgressTracker(config["paths"]["progress_file"])
    pending_intents = [intent for intent in intents if not progress.is_success(str(intent.get("id", "unknown")))]

    if not pending_intents:
        logger.info("没有待处理的 intents，当前任务已全部完成")
        return

    logger.info(f"并发数: {num_workers}")
    logger.info(f"待处理 intents: {len(pending_intents)}")

    task_queue: "queue.Queue[Dict[str, Any]]" = queue.Queue()
    for intent in pending_intents:
        task_queue.put(intent)

    results: List[Dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=num_workers) as executor:
        futures = []
        for worker_index in range(1, num_workers + 1):
            agent_name = f"{worker_prefix}-{worker_index}"
            # 传递 tools_cache_file 而不是预加载的 tools_catalog
            future = executor.submit(
                worker_loop,
                agent_name,
                task_queue,
                config,
                config["paths"]["tools_cache_file"],
                progress
            )
            futures.append(future)

        for future in as_completed(futures):
            worker_results = future.result()
            results.extend(worker_results)
            logger.info(f"当前累计完成: {len(results)}/{len(pending_intents)}")

    success = sum(1 for r in results if r["status"] == "success")
    logger.info(f"完成: 成功 {success}, 失败 {len(results) - success}")

    save_json(
        {
            "total": len(results),
            "success": success,
            "failed": len(results) - success,
            "results": results,
        },
        f"{config['paths']['output_dir']}/summary.json",
    )


if __name__ == "__main__":
    main()
