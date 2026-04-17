"""Agent 初始化脚本。"""
import argparse
import json
import logging
import socket
import shutil
import subprocess
import time
from pathlib import Path
from typing import Dict, List, Any, Optional
from urllib.error import URLError
from urllib.request import urlopen

import os
import sys

# 防御性处理 __file__ (容器环境兼容)
if '__file__' in globals():
    sys.path.insert(0, str(Path(__file__).parent.parent))
else:
    sys.path.insert(0, str(Path(os.getcwd())))

from src.openclaw_wrapper import (
    configure_agent,
    configure_global_provider,
    ensure_agent_state_dirs,
    ensure_agents,
    expected_agent_state_dir,
    expected_agent_workspace,
    load_openclaw_config,
    resolve_workspace_root,
    OpenClawWrapper,
)
from src.config import load_config
from src.fs_utils import ensure_owner_writable, make_tree_owner_writable, remove_path, remove_tree
from src.worker_snapshot import (
    resolve_runtime_snapshot_root,
    resolve_template_snapshot_root,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


SNAPSHOT_EXCLUDE_NAMES = {".git", "BOOTSTRAP.md"}
SHARED_WORKSPACE_SNAPSHOT_NAME = "_template"


def resolve_project_root() -> Path:
    """解析项目根目录。"""
    if '__file__' in globals():
        return Path(__file__).parent.parent
    return Path(os.getcwd())


def resolve_init_settings(
    cli_num_agents: Optional[int],
    cli_worker_prefix: Optional[str],
    cli_workspace_root: Optional[str],
    config: Dict[str, Any],
) -> Dict[str, Any]:
    """合并 CLI 参数和配置文件，得到 init_agents 的有效设置。"""
    openclaw_config = config.get("openclaw", {})
    paths_config = config.get("paths", {})
    return {
        "num_agents": cli_num_agents or openclaw_config.get("num_workers", 3),
        "worker_prefix": cli_worker_prefix or openclaw_config.get("worker_prefix", "gendata-worker"),
        "workspace_root": cli_workspace_root or openclaw_config.get("workspace_root"),
        "tools_output_file": paths_config.get("tools_cache_file", "output/tools/tools_all_agents.json"),
    }


# ============== 工具生成相关函数 ==============

def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        sock.listen(1)
        return int(sock.getsockname()[1])


def _wait_for_proxy_ready(port: int, timeout: float = 10.0) -> None:
    deadline = time.time() + timeout
    url = f"http://127.0.0.1:{port}/health"
    last_error: Optional[Exception] = None
    while time.time() < deadline:
        try:
            with urlopen(url, timeout=1.0) as response:
                payload = json.loads(response.read().decode("utf-8"))
                if payload.get("status") == "ok":
                    return
        except (URLError, TimeoutError, json.JSONDecodeError) as exc:
            last_error = exc
            time.sleep(0.2)
    raise RuntimeError(f"runtime tools proxy 未在预期时间内就绪: {last_error}")


def _delete_agent(agent_id: str, workspace_root: Optional[str] = None) -> None:
    result = subprocess.run(
        ["openclaw", "agents", "delete", agent_id, "--force", "--json"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        logger.warning("删除 probe agent 失败，继续清理本地状态: %s", result.stderr)

    workspace = expected_agent_workspace(agent_id, workspace_root)
    if workspace.exists():
        try:
            remove_tree(workspace)
        except Exception:
            shutil.rmtree(workspace, ignore_errors=True)

    state_dir = expected_agent_state_dir(agent_id)
    if state_dir.exists():
        try:
            remove_tree(state_dir)
        except Exception:
            shutil.rmtree(state_dir, ignore_errors=True)


def _create_probe_agent(agent_id: str, workspace_root: Optional[str]) -> Path:
    _delete_agent(agent_id, workspace_root)
    workspace = expected_agent_workspace(agent_id, workspace_root)
    workspace.mkdir(parents=True, exist_ok=True)

    result = subprocess.run(
        ["openclaw", "agents", "add", agent_id, "--non-interactive", "--workspace", str(workspace), "--json"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"创建 probe agent 失败: {result.stderr}")

    ensure_agent_state_dirs(agent_id)
    return workspace


def _load_tools_from_capture(output_file: Path) -> List[Dict[str, Any]]:
    latest_file = output_file.with_name(output_file.stem + "_latest.json")
    if not latest_file.exists():
        raise FileNotFoundError(f"未找到 probe tools 捕获文件: {latest_file}")

    payload = json.loads(latest_file.read_text(encoding="utf-8"))
    tools = payload.get("tools")
    if not isinstance(tools, list) or not tools:
        raise RuntimeError("probe 请求未捕获到任何 tools")
    return tools


def _load_system_prompt_from_capture(output_file: Path) -> str:
    """从 probe 捕获文件中提取 system prompt。"""
    latest_file = output_file.with_name(output_file.stem + "_latest.json")
    if not latest_file.exists():
        raise FileNotFoundError(f"未找到 probe 捕获文件: {latest_file}")

    payload = json.loads(latest_file.read_text(encoding="utf-8"))
    system_prompt = payload.get("system_prompt")
    if not system_prompt or not isinstance(system_prompt, str):
        raise RuntimeError("probe 请求未捕获到 system prompt")
    return system_prompt


def _wait_for_captured_tools(output_file: Path, timeout: float) -> List[Dict[str, Any]]:
    deadline = time.time() + timeout
    last_error: Optional[Exception] = None
    while time.time() < deadline:
        try:
            return _load_tools_from_capture(output_file)
        except (FileNotFoundError, json.JSONDecodeError, RuntimeError) as exc:
            last_error = exc
            time.sleep(0.2)
    raise RuntimeError(f"在 {timeout:.1f}s 内未捕获到 probe tools: {last_error}")


def _terminate_process(process: subprocess.Popen[str], wait_timeout: float = 5.0) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=wait_timeout)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=wait_timeout)


def _trigger_probe_request(agent_id: str, timeout: int, capture_output_file: Path) -> List[Dict[str, Any]]:
    latest_file = capture_output_file.with_name(capture_output_file.stem + "_latest.json")
    remove_path(capture_output_file)
    remove_path(latest_file)

    cmd = [
        "openclaw",
        "agent",
        "--agent",
        agent_id,
        "--message",
        "Reply with exactly OK. Do not use any tools.",
        "--json",
        "--thinking",
        "off",
        "--timeout",
        str(min(timeout, 10)),
    ]

    logger.info("以后台方式触发 probe 请求: agent=%s", agent_id)
    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    try:
        tools = _wait_for_captured_tools(capture_output_file, timeout=min(timeout, 15))
        logger.info("probe 已捕获 tools，准备结束 CLI 子进程")
        return tools
    finally:
        _terminate_process(process)
        stdout_text, stderr_text = process.communicate()
        if process.returncode not in (0, -15, 143, None):
            logger.debug(
                "probe CLI exited with code=%s stdout=%r stderr=%r",
                process.returncode,
                stdout_text,
                stderr_text,
            )


def _capture_runtime_tools_via_probe(
    source_agent_id: str,
    project_root: Path,
    timeout: int,
) -> List[Dict[str, Any]]:
    config = load_config()
    openclaw_config = config.get("openclaw", {})
    workspace_root = openclaw_config.get("workspace_root")
    model_url = openclaw_config.get("model_url")
    model_api_key = openclaw_config.get("model_api_key")
    model = openclaw_config.get("model")
    provider_api = openclaw_config.get("api", "anthropic-messages")
    context_window = openclaw_config.get("context_window", 200000)
    max_tokens = openclaw_config.get("max_tokens", 200000)
    enable_thinking = openclaw_config.get("enable_thinking", True)

    if not (model_url and model_api_key and model):
        raise RuntimeError("openclaw.model_url/model_api_key/model 缺失，无法 probe 真实 tools")

    openclaw_runtime_config = load_openclaw_config(default={"agents": {"list": []}})
    source_agent_config = next(
        (agent for agent in openclaw_runtime_config.get("agents", {}).get("list", []) if agent.get("id") == source_agent_id),
        None,
    )
    if not source_agent_config:
        raise RuntimeError(f"未找到源 agent 配置: {source_agent_id}")

    probe_agent_id = f"{source_agent_id}-tools-probe"
    capture_output_file = project_root / "output" / "tools" / f"runtime_probe_{source_agent_id}.jsonl"
    port = _find_free_port()
    proxy_cmd = [
        sys.executable,
        "-m",
        "src.runtime_tools_proxy",
        "--port",
        str(port),
        "--upstream-base-url",
        model_url,
        "--output-file",
        str(capture_output_file),
        "--capture-only",
    ]

    proxy_process = subprocess.Popen(
        proxy_cmd,
        cwd=str(project_root),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        text=True,
    )

    try:
        _wait_for_proxy_ready(port)

        local_proxy_url = f"http://127.0.0.1:{port}"
        configure_global_provider(
            provider_name="trajectory_provider",
            base_url=local_proxy_url,
            api_key=model_api_key,
            model_id=model,
            provider_api=provider_api,
            context_window=context_window,
            max_tokens=max_tokens,
            reasoning=enable_thinking,
        )

        probe_workspace = _create_probe_agent(probe_agent_id, workspace_root)
        configure_agent(
            probe_agent_id,
            workspace=str(probe_workspace),
            tools_allow=(source_agent_config.get("tools") or {}).get("allow"),
            model=source_agent_config.get("model"),
            skills=source_agent_config.get("skills"),
        )

        tools = _trigger_probe_request(probe_agent_id, timeout, capture_output_file)
        logger.info("probe 成功捕获真实 tools：agent=%s tools=%s", source_agent_id, len(tools))
        return tools
    finally:
        configure_global_provider(
            provider_name="trajectory_provider",
            base_url=model_url,
            api_key=model_api_key,
            model_id=model,
            provider_api=provider_api,
            context_window=context_window,
            max_tokens=max_tokens,
            reasoning=enable_thinking,
        )
        try:
            _delete_agent(probe_agent_id, workspace_root)
        except Exception as exc:  # pragma: no cover
            logger.warning("清理 probe agent 失败: %s", exc)
        if proxy_process.poll() is None:
            proxy_process.terminate()
            try:
                proxy_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proxy_process.kill()
                proxy_process.wait(timeout=5)


def generate_agent_tools(
    agent_id: str,
    project_root: Path,
    timeout: int = 180
) -> List[Dict[str, Any]]:
    """为单个 agent 生成工具列表。

    Args:
        agent_id: agent 名称
        project_root: 项目根目录
        timeout: 超时时间（秒）

    Returns:
        真实运行时请求中捕获到的 OpenAI format 工具列表
    """
    tools_openai = _capture_runtime_tools_via_probe(agent_id, project_root, timeout)
    logger.info("成功为 agent %s 捕获 %s 个真实工具", agent_id, len(tools_openai))
    return tools_openai


def generate_all_agents_tools(
    agent_ids: List[str],
    output_file: str,
    project_root: Path,
    timeout: int = 180
) -> Dict[str, List[Dict[str, Any]]]:
    """批量生成多个 agent 的工具列表并保存。

    Args:
        agent_ids: agent 名称列表
        output_file: 输出文件路径 (output/tools/tools_all_agents.json)
        project_root: 项目根目录
        timeout: 每个 agent 的超时时间

    Returns:
        按 agent 分组的工具字典: {agent_id: [tools...]}
    """
    if not agent_ids:
        tools_by_agent: Dict[str, List[Dict[str, Any]]] = {}
    else:
        source_agent_id = agent_ids[0]
        logger.info("所有 worker tools 当前共用，使用 %s 提取一次后复用", source_agent_id)
        shared_tools = generate_agent_tools(source_agent_id, project_root, timeout)
        tools_by_agent = {
            agent_id: list(shared_tools)
            for agent_id in agent_ids
        }

    # 保存到文件
    output_path = Path(output_file)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(tools_by_agent, f, indent=2, ensure_ascii=False)

    logger.info("已复用同一份工具列表并保存到 %s（agents=%s）", output_file, len(agent_ids))

    return tools_by_agent


def capture_all_agents_system_prompts(
    agent_ids: List[str],
    output_file: str,
    workspace_root: Optional[str] = None,
    project_root: Optional[Path] = None,
    timeout: int = 180,
) -> Dict[str, str]:
    """捕获所有 agent 的 system prompt 并保存。

    通过 runtime probe 捕获 OpenClaw 实际发给模型的 system prompt。

    Args:
        agent_ids: agent 名称列表
        output_file: 输出文件路径 (output/tools/system_prompts_all_agents.json)
        workspace_root: workspace 根目录
        project_root: 项目根目录
        timeout: 每个 agent 的超时时间

    Returns:
        按 agent 分组的 system prompt 字典: {agent_id: system_prompt_text}
    """
    if project_root is None:
        project_root = resolve_project_root()

    prompts_by_agent: Dict[str, str] = {}

    if not agent_ids:
        logger.warning("没有 agent 需要捕获 system prompt")
    else:
        # 所有 worker 共用同一个 system prompt，只需捕获一次
        source_agent_id = agent_ids[0]
        logger.info("所有 worker system prompts 当前共用，使用 %s 捕获一次后复用", source_agent_id)

        try:
            # 通过 runtime probe 捕获真实的 system prompt
            system_prompt = _capture_system_prompt_via_probe(source_agent_id, project_root, timeout)
            logger.info("已捕获 agent %s 的 system prompt (%s chars)", source_agent_id, len(system_prompt))

            # 复用到所有 agents
            for agent_id in agent_ids:
                prompts_by_agent[agent_id] = system_prompt

        except Exception as exc:
            logger.error("捕获 system prompt 失败: %s", exc)
            # 失败时所有 agents 都设为空字符串
            for agent_id in agent_ids:
                prompts_by_agent[agent_id] = ""

    # 保存到文件
    output_path = Path(output_file)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(prompts_by_agent, f, indent=2, ensure_ascii=False)

    logger.info("已保存所有 agents 的 system prompt 到 %s", output_file)

    return prompts_by_agent


def _capture_system_prompt_via_probe(
    source_agent_id: str,
    project_root: Path,
    timeout: int,
) -> str:
    """通过 runtime probe 捕获真实的 system prompt。"""
    logger.info("正在通过 runtime probe 捕获 agent %s 的 system prompt...", source_agent_id)
    config = load_config()
    openclaw_config = config.get("openclaw", {})
    workspace_root = openclaw_config.get("workspace_root")
    model_url = openclaw_config.get("model_url")
    model_api_key = openclaw_config.get("model_api_key")
    model = openclaw_config.get("model")
    provider_api = openclaw_config.get("api", "anthropic-messages")
    context_window = openclaw_config.get("context_window", 200000)
    max_tokens = openclaw_config.get("max_tokens", 200000)

    if not (model_url and model_api_key and model):
        raise RuntimeError("openclaw.model_url/model_api_key/model 缺失，无法 probe system prompt")

    openclaw_runtime_config = load_openclaw_config(default={"agents": {"list": []}})
    source_agent_config = next(
        (agent for agent in openclaw_runtime_config.get("agents", {}).get("list", []) if agent.get("id") == source_agent_id),
        None,
    )
    if not source_agent_config:
        raise RuntimeError(f"未找到源 agent 配置: {source_agent_id}")

    probe_agent_id = f"{source_agent_id}-prompt-probe"
    capture_output_file = project_root / "output" / "tools" / f"runtime_probe_{source_agent_id}.jsonl"
    port = _find_free_port()
    proxy_cmd = [
        sys.executable,
        "-m",
        "src.runtime_tools_proxy",
        "--port",
        str(port),
        "--upstream-base-url",
        model_url,
        "--output-file",
        str(capture_output_file),
        "--capture-only",
    ]

    proxy_process = subprocess.Popen(
        proxy_cmd,
        cwd=str(project_root),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        text=True,
    )

    try:
        _wait_for_proxy_ready(port)

        local_proxy_url = f"http://127.0.0.1:{port}"
        configure_global_provider(
            provider_name="trajectory_provider",
            base_url=local_proxy_url,
            api_key=model_api_key,
            model_id=model,
            provider_api=provider_api,
            context_window=context_window,
            max_tokens=max_tokens,
        )

        probe_workspace = _create_probe_agent(probe_agent_id, workspace_root)
        logger.info("已创建 probe agent: %s (workspace=%s)", probe_agent_id, probe_workspace)

        # 复制源 agent 的配置到 probe agent
        configure_agent(
            agent_id=probe_agent_id,
            workspace=str(probe_workspace),
            tools_allow=(source_agent_config.get("tools") or {}).get("allow"),
            model=source_agent_config.get("model"),
            skills=source_agent_config.get("skills"),
        )

        # 触发 probe 请求并捕获 system prompt
        system_prompt = _trigger_probe_and_capture_system_prompt(probe_agent_id, timeout, capture_output_file)

        logger.info("✓ 已通过 probe 捕获 system prompt (%s chars)", len(system_prompt))
        return system_prompt

    finally:
        # 恢复 provider 指向真实 model_url（而非 proxy）
        configure_global_provider(
            provider_name="trajectory_provider",
            base_url=model_url,
            api_key=model_api_key,
            model_id=model,
            provider_api=provider_api,
            context_window=context_window,
            max_tokens=max_tokens,
        )
        _terminate_process(proxy_process)
        try:
            _delete_agent(probe_agent_id, workspace_root)
        except Exception as cleanup_error:
            logger.warning("清理 probe agent 失败: %s", cleanup_error)


def _trigger_probe_and_capture_system_prompt(agent_id: str, timeout: int, capture_output_file: Path) -> str:
    """触发 probe 请求并从捕获文件中提取 system prompt。"""
    latest_file = capture_output_file.with_name(capture_output_file.stem + "_latest.json")
    remove_path(capture_output_file)
    remove_path(latest_file)

    cmd = [
        "openclaw",
        "agent",
        "--agent",
        agent_id,
        "--message",
        "Reply with exactly OK. Do not use any tools.",
        "--json",
        "--thinking",
        "off",
        "--timeout",
        str(min(timeout, 10)),
    ]

    logger.info("触发 probe 请求以捕获 system prompt: agent=%s", agent_id)
    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    try:
        # 等待捕获 system prompt
        deadline = time.time() + min(timeout, 15)
        last_error: Optional[Exception] = None
        while time.time() < deadline:
            try:
                system_prompt = _load_system_prompt_from_capture(capture_output_file)
                logger.info("probe 已捕获 system prompt，准备结束 CLI 子进程")
                return system_prompt
            except (FileNotFoundError, json.JSONDecodeError, RuntimeError) as exc:
                last_error = exc
                time.sleep(0.2)

        raise RuntimeError(f"在 {timeout:.1f}s 内未捕获到 system prompt: {last_error}")

    finally:
        _terminate_process(process)
        stdout_text, stderr_text = process.communicate()
        if process.returncode not in (0, -15, 143, None):
            logger.debug(
                "probe CLI exited with code=%s stdout=%r stderr=%r",
                process.returncode,
                stdout_text,
                stderr_text,
            )


def update_agent_tools(
    agent_id: str,
    output_file: str,
    project_root: Path,
    timeout: int = 180
) -> None:
    """更新特定 agent 的工具列表。

    Args:
        agent_id: 要更新的 agent 名称
        output_file: 工具文件路径
        project_root: 项目根目录
        timeout: 超时时间
    """
    # 读取现有文件
    output_path = Path(output_file)
    if output_path.exists():
        with open(output_path, 'r', encoding='utf-8') as f:
            tools_by_agent = json.load(f)
    else:
        tools_by_agent = {}

    # 生成新工具列表
    tools = generate_agent_tools(agent_id, project_root, timeout)
    tools_by_agent[agent_id] = tools

    # 保存回文件
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(tools_by_agent, f, indent=2, ensure_ascii=False)

    logger.info(f"已更新 agent {agent_id} 的工具列表")


# ============== Workspace 管理函数 ==============

def modify_agent_md(agent_id: str, workspace_root: str) -> None:
    """修改 agent workspace 的 AGENTS.md，添加工作区限制指令。

    Args:
        agent_id: agent 名称
        workspace_root: workspace 根目录
    """
    from src.openclaw_wrapper import expected_agent_workspace

    workspace = expected_agent_workspace(agent_id, workspace_root)
    agents_md = workspace / "AGENTS.md"

    if not agents_md.exists():
        logger.warning(f"Agent {agent_id} 的 AGENTS.md 不存在，跳过修改")
        return

    # 读取现有内容
    with open(agents_md, 'r', encoding='utf-8') as f:
        lines = f.readlines()

    # 在第 3 行后插入新指令
    if len(lines) >= 3:
        # 检查是否已经添加过
        if "only work in your workspace" not in ''.join(lines):
            lines.insert(3, "And very important: only work in your workspace!!!\n\n")

            # 写回文件
            with open(agents_md, 'w', encoding='utf-8') as f:
                f.writelines(lines)

            logger.info(f"已修改 agent {agent_id} 的 AGENTS.md")
        else:
            logger.debug(f"Agent {agent_id} 的 AGENTS.md 已包含工作区指令")


def save_workspace_snapshot(agent_id: str, workspace_root: str, snapshot_dir: str) -> None:
    """保存 agent workspace 的快照。

    Args:
        agent_id: agent 名称
        workspace_root: workspace 根目录
        snapshot_dir: 快照保存根目录
    """
    from src.openclaw_wrapper import expected_agent_workspace

    workspace = expected_agent_workspace(agent_id, workspace_root)
    snapshot_path = Path(snapshot_dir) / agent_id

    # 清理旧快照
    if snapshot_path.exists():
        remove_tree(snapshot_path)

    # 复制 workspace 到快照目录（排除 .git）
    snapshot_path.mkdir(parents=True, exist_ok=True)
    for item in workspace.iterdir():
        if item.name in SNAPSHOT_EXCLUDE_NAMES:
            continue
        dest = snapshot_path / item.name
        if item.is_dir():
            shutil.copytree(item, dest, symlinks=False)
            make_tree_owner_writable(dest)
        else:
            shutil.copy2(item, dest)
            ensure_owner_writable(dest)

    logger.info(f"已保存 agent {agent_id} 的 workspace 快照到 {snapshot_path}")


def save_shared_workspace_snapshot(agent_id: str, workspace_root: str, snapshot_dir: str) -> None:
    """保存共享 workspace 模板快照。

    所有新创建的 worker 初始内容一致时，只保留一份模板快照即可。
    """
    from src.openclaw_wrapper import expected_agent_workspace

    workspace = expected_agent_workspace(agent_id, workspace_root)
    snapshot_path = Path(snapshot_dir) / SHARED_WORKSPACE_SNAPSHOT_NAME

    if snapshot_path.exists():
        remove_tree(snapshot_path)

    snapshot_path.mkdir(parents=True, exist_ok=True)
    for item in workspace.iterdir():
        if item.name in SNAPSHOT_EXCLUDE_NAMES:
            continue
        dest = snapshot_path / item.name
        if item.is_dir():
            shutil.copytree(item, dest, symlinks=False)
            make_tree_owner_writable(dest)
        else:
            shutil.copy2(item, dest)
            ensure_owner_writable(dest)

    logger.info("已保存共享 workspace 模板快照到 %s", snapshot_path)


def remove_excluded_workspace_files(agent_id: str, workspace_root: str) -> None:
    """删除 workspace 中不希望保留的文件。"""
    from src.openclaw_wrapper import expected_agent_workspace

    workspace = expected_agent_workspace(agent_id, workspace_root)
    removed_names: List[str] = []

    for name in SNAPSHOT_EXCLUDE_NAMES - {".git"}:
        target = workspace / name
        if not target.exists():
            continue

        if target.is_dir():
            remove_tree(target)
        else:
            remove_path(target)
        removed_names.append(name)

    if removed_names:
        logger.info("已删除 agent %s workspace 中的文件: %s", agent_id, ", ".join(sorted(removed_names)))


# ============== Agent 初始化函数 ==============

def init_agents(
    num_agents: int = 30,
    worker_prefix: str = "gendata-worker",
    workspace_root: Optional[str] = None,
    force_recreate: bool = False,
    refresh_tools: bool = True,
    refresh_agents: Optional[List[str]] = None,
    project_root: Optional[Path] = None,
    tools_output_file: Optional[str] = None,
    add_tools: bool = True,
    config: Optional[Dict[str, Any]] = None,
) -> bool:
    """初始化多个 OpenClaw agent，可选生成工具列表。

    Args:
        num_agents: 要创建的 agent 数量
        worker_prefix: worker agent 前缀
        workspace_root: workspace 根目录
        force_recreate: 是否强制删除所有 worker agents 并重新创建
        refresh_tools: 是否刷新所有 agents 的工具列表
        refresh_agents: 要刷新工具列表的特定 agents
        project_root: 项目根目录
        tools_output_file: 工具输出文件路径
        add_tools: 是否自动配置工具白名单（默认 True）
        config: 已加载的配置字典（可选）

    Returns:
        是否成功
    """
    logger.info(f"开始检查并创建 {num_agents} 个 agents...")
    project_root = project_root or resolve_project_root()
    root_dir = resolve_workspace_root(workspace_root)
    logger.info("worker workspaces 根目录: %s", root_dir)
    config = config or load_config()
    openclaw_config = config.get("openclaw", {})
    paths_config = config.get("paths", {})
    worker_tools_allow = openclaw_config.get("worker_tools_allow")

    # 如果强制重建，先删除旧的 workspace 快照
    if force_recreate:
        snapshot_dir = resolve_template_snapshot_root(paths_config)
        if snapshot_dir.exists():
            logger.info("删除旧的 workspace 快照: %s", snapshot_dir)
            remove_tree(snapshot_dir)
        agent_snapshot_dir = resolve_runtime_snapshot_root(paths_config)
        if agent_snapshot_dir.exists():
            logger.info("删除旧的 worker runtime 快照: %s", agent_snapshot_dir)
            remove_tree(agent_snapshot_dir)

    result = ensure_agents(
        num_agents=num_agents,
        worker_prefix=worker_prefix,
        workspace_root=str(root_dir),
        force_recreate=force_recreate,
        add_tools=add_tools,
        tools_allow=worker_tools_allow,
    )
    logger.info(
        "已存在: %s，新建: %s，已删除: %s",
        len(result["existing"]),
        len(result["created"]),
        len(result.get("deleted", [])),
    )
    if result["created"]:
        logger.info("新建 agents: %s", ", ".join(result["created"]))
    if result.get("deleted"):
        logger.info("删除 agents: %s", ", ".join(result["deleted"]))

    # 只对新创建的 agent 修改 AGENTS.md 并保存快照
    # 已存在的 agent 跳过，避免把 session 产生的脏文件当成快照存进去
    new_agent_ids = result["created"]

    if new_agent_ids:
        logger.info(f"修改 {len(new_agent_ids)} 个新 agents 的 AGENTS.md...")
        for agent_id in new_agent_ids:
            modify_agent_md(agent_id, str(root_dir))
            remove_excluded_workspace_files(agent_id, str(root_dir))

        snapshot_dir = resolve_template_snapshot_root(paths_config)
        snapshot_dir.mkdir(parents=True, exist_ok=True)

        template_agent_id = new_agent_ids[0]
        logger.info(
            "新 agents 初始内容一致，使用 %s 保存一份共享 workspace 模板快照...",
            template_agent_id,
        )
        save_shared_workspace_snapshot(template_agent_id, str(root_dir), str(snapshot_dir))
        logger.info("✓ 已保存共享 workspace 模板快照到 %s", snapshot_dir)
    else:
        logger.info("无新建 agents，跳过 AGENTS.md 修改和快照保存")

    # 配置全局 skills 设置和 provider
    from src.openclaw_wrapper import configure_global_skills, configure_global_provider

    skills_dir_rel = paths_config.get("skills_dir", "tools/skills/skills_collections")
    skills_dir = project_root / skills_dir_rel
    logger.info("配置全局 skills 设置...")
    configure_global_skills(
        extra_dirs=[str(skills_dir)],
        allow_bundled=[]  # 禁用所有内置 skills
    )
    logger.info("✓ 已配置全局 skills 设置")

    # 配置全局 provider
    model_url = openclaw_config.get("model_url")
    model_api_key = openclaw_config.get("model_api_key")
    model = openclaw_config.get("model")
    provider_api = openclaw_config.get("api", "anthropic-messages")
    context_window = openclaw_config.get("context_window", 200000)
    max_tokens = openclaw_config.get("max_tokens", 200000)
    enable_thinking = openclaw_config.get("enable_thinking", True)

    if model_url and model_api_key and model:
        logger.info("配置全局 provider...")
        configure_global_provider(
            provider_name="trajectory_provider",
            base_url=model_url,
            api_key=model_api_key,
            model_id=model,
            provider_api=provider_api,
            context_window=context_window,
            max_tokens=max_tokens,
            reasoning=enable_thinking,
        )
        logger.info("✓ 已配置全局 provider")

    # 配置所有 agents 的 model 和 skills
    all_agent_ids = [f"{worker_prefix}-{i+1}" for i in range(num_agents)]
    model_url = openclaw_config.get("model_url")
    model_api_key = openclaw_config.get("model_api_key")
    model = openclaw_config.get("model")

    if model_url and model_api_key and model:
        logger.info(f"配置 {len(all_agent_ids)} 个 agents 的 model 和 skills...")
        from src.openclaw_wrapper import configure_agent

        # 从配置读取 skills 目录
        skills_dir_rel = paths_config.get("skills_dir", "tools/skills/skills_collections")
        skills_dir = project_root / skills_dir_rel
        agent_skills = []
        if skills_dir.exists():
            agent_skills = [d.name for d in skills_dir.iterdir() if d.is_dir()]
            logger.info(f"从 {skills_dir} 读取到 {len(agent_skills)} 个 skills")

        # model 格式：provider_name/model_name
        model_config = f"trajectory_provider/{model}"

        for agent_id in all_agent_ids:
            configure_agent(
                agent_id=agent_id,
                model=model_config,
                skills=agent_skills,
            )
        logger.info(f"✓ 已配置所有 agents 的 model 和 {len(agent_skills)} 个 skills")
    else:
        logger.warning("配置文件中缺少 model_url/model_api_key/model，跳过配置")

    # 新增：生成工具列表
    if refresh_tools or refresh_agents:
        if not tools_output_file:
            logger.warning("未指定 tools_output_file，跳过工具生成")
            return True

        if refresh_agents:
            # 仅刷新指定 agents
            logger.info(f"刷新 agents 的工具列表: {', '.join(refresh_agents)}")
            for agent_id in refresh_agents:
                try:
                    update_agent_tools(agent_id, tools_output_file, project_root)
                except Exception as e:
                    logger.error(f"刷新 agent {agent_id} 工具列表失败: {e}")
        elif refresh_tools:
            # 刷新所有 agents
            agent_ids = [f"{worker_prefix}-{i+1}" for i in range(num_agents)]
            logger.info(f"生成所有 {num_agents} 个 agents 的工具列表...")
            try:
                generate_all_agents_tools(agent_ids, tools_output_file, project_root)
            except Exception as e:
                logger.error(f"批量生成工具列表失败: {e}")
                return False

    return True


def main():
    """主函数"""
    parser = argparse.ArgumentParser(description="初始化 OpenClaw agents")
    parser.add_argument("--config", default="config/config.yaml", help="配置文件路径")
    parser.add_argument(
        "--num-agents",
        type=int,
        help="要创建的 agent 数量（默认读取 openclaw.num_workers）"
    )
    parser.add_argument(
        "--worker-prefix",
        help="worker agent 前缀（默认读取 openclaw.worker_prefix）",
    )
    parser.add_argument(
        "--workspace-root",
        help="隔离 workspace 根目录，默认读取 openclaw.workspace_root",
    )
    parser.add_argument(
        "--force-recreate",
        action="store_true",
        help="强制删除所有 worker agents 并重新创建（用于数量变化场景）",
    )
    parser.add_argument(
        "--refresh-tools",
        action="store_true",
        help="初始化后生成所有 agent 的工具列表"
    )
    parser.add_argument(
        "--refresh-agent",
        type=str,
        action="append",
        help="仅刷新指定 agent 的工具列表（可多次指定）"
    )
    parser.add_argument(
        "--add-tools",
        action="store_true",
        default=True,
        help="自动配置 worker agent 的工具白名单（默认: true）"
    )

    args = parser.parse_args()

    # 读取配置获取输出路径
    config = load_config(args.config)
    settings = resolve_init_settings(
        args.num_agents,
        args.worker_prefix,
        args.workspace_root,
        config,
    )
    project_root = resolve_project_root()

    success = init_agents(
        settings["num_agents"],
        settings["worker_prefix"],
        settings["workspace_root"],
        args.force_recreate,
        refresh_tools=args.refresh_tools,
        refresh_agents=args.refresh_agent,
        project_root=project_root,
        tools_output_file=settings["tools_output_file"],
        add_tools=args.add_tools,
        config=config,
    )
    if success:
        logger.info("✓ 所有 agent 初始化成功")
        if args.refresh_tools or args.refresh_agent:
            logger.info(f"✓ 工具列表已保存到 {settings['tools_output_file']}")
    else:
        logger.error("✗ Agent 初始化失败")
        exit(1)


if __name__ == "__main__":
    main()
