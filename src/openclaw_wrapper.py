"""OpenClaw CLI 封装与 agent/session 管理。"""
import json
import logging
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)




# Worker 沙箱配置
# mode: "all" - 所有 session 都进 Docker 沙箱
# scope: "session" - 每个 session 独立容器，结束后销毁
# workspaceAccess: "ro" - 只读挂载 workspace（可读 skills，写操作走容器临时目录）
WORKER_SANDBOX_CONFIG = {
    "mode": "all",
    "scope": "session",
    "workspaceAccess": "ro"
}

# Worker 工具列表（使用 allow 完全替换，独立配置）
# 包含 exec 和 process，在沙箱内安全
WORKER_TOOLS_ALLOW = [
    "read", "write", "edit", "apply_patch",
    "exec", "process",  # 沙箱内安全
    "web_search", "web_fetch",
    "memory_search", "memory_get",
    "sessions_list", "sessions_history", "sessions_send", "sessions_spawn",
    "session_status", "subagents", "agents_list",
    "image", "tts"
]


def _extract_json_payload(raw_text: str) -> Any:
    """从混杂日志的输出中提取首个 JSON 对象或数组。"""
    if not raw_text:
        raise json.JSONDecodeError("Empty OpenClaw output", raw_text, 0)

    decoder = json.JSONDecoder()
    for index, char in enumerate(raw_text):
        if char not in "[{":
            continue
        try:
            payload, _ = decoder.raw_decode(raw_text[index:])
            return payload
        except json.JSONDecodeError:
            continue

    raise json.JSONDecodeError("No JSON payload found in OpenClaw output", raw_text, 0)


def list_agents() -> List[Dict[str, Any]]:
    """列出当前已存在的 OpenClaw agents。"""
    result = subprocess.run(
        ["openclaw", "agents", "list", "--json"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Failed to list agents: {result.stderr}")
    return _extract_json_payload(result.stdout)


def resolve_workspace_root(workspace_root: Optional[str] = None) -> Path:
    """解析 worker workspaces 的根目录。"""
    return Path(workspace_root).expanduser() if workspace_root else Path.home() / ".openclaw" / "workspaces"


def expected_agent_workspace(agent_id: str, workspace_root: Optional[str] = None) -> Path:
    """获取指定 agent 的预期隔离 workspace 路径。"""
    return resolve_workspace_root(workspace_root) / agent_id


def configure_agent(
    agent_id: str,
    workspace: Optional[str] = None,
    sandbox: Optional[Dict[str, str]] = None,
    tools_allow: Optional[List[str]] = None,
    config_path: Optional[Path] = None
) -> None:
    """配置 agent 的 workspace、sandbox 和工具。

    Args:
        agent_id: agent 名称
        workspace: workspace 路径（可选）
        sandbox: 沙箱配置（可选）
        tools_allow: 工具列表（可选，使用 allow 完全替换）
        config_path: 配置文件路径（可选，默认 ~/.openclaw/openclaw.json）
    """
    if config_path is None:
        config_path = Path.home() / ".openclaw" / "openclaw.json"

    # 1. 读取配置文件
    if config_path.exists():
        with open(config_path, 'r', encoding='utf-8') as f:
            config = json.load(f)
    else:
        config = {"agents": {"list": []}}

    # 2. 确保 agents.list 存在
    if "agents" not in config:
        config["agents"] = {}
    if "list" not in config["agents"]:
        config["agents"]["list"] = []

    # 3. 查找或创建 agent 配置
    agent_list = config["agents"]["list"]
    agent_config = None
    for agent in agent_list:
        if agent.get("id") == agent_id:
            agent_config = agent
            break

    if agent_config is None:
        agent_config = {"id": agent_id}
        agent_list.append(agent_config)

    # 4. 配置 workspace
    if workspace is not None:
        agent_config["workspace"] = workspace
        logger.info(f"已配置 agent {agent_id} 的 workspace: {workspace}")

    # 5. 配置 sandbox
    if sandbox is not None:
        agent_config["sandbox"] = sandbox
        logger.info(f"已配置 agent {agent_id} 的 sandbox: {sandbox}")

    # 6. 配置 tools（使用 allow 完全替换）
    if tools_allow is not None:
        agent_config["tools"] = {"allow": tools_allow}
        logger.info(f"已配置 agent {agent_id} 的工具列表（allow: {len(tools_allow)} 个）")

    # 7. 保存配置文件
    with open(config_path, 'w', encoding='utf-8') as f:
        json.dump(config, f, indent=2, ensure_ascii=False)


def configure_sandbox(
    agent_ids: List[str],
    sandbox_config: Dict[str, str],
    config_path: Optional[Path] = None
) -> None:
    """为多个 agents 批量配置沙箱。

    Args:
        agent_ids: agent ID 列表
        sandbox_config: 沙箱配置字典
        config_path: 配置文件路径（可选，默认 ~/.openclaw/openclaw.json）
    """
    if config_path is None:
        config_path = Path.home() / ".openclaw" / "openclaw.json"

    # 读取配置
    if config_path.exists():
        with open(config_path, 'r', encoding='utf-8') as f:
            config = json.load(f)
    else:
        logger.warning(f"配置文件不存在: {config_path}")
        return

    # 为每个 agent 配置 sandbox
    agent_list = config.get("agents", {}).get("list", [])
    configured_count = 0

    for agent_config in agent_list:
        if agent_config.get("id") in agent_ids:
            agent_config["sandbox"] = sandbox_config
            configured_count += 1

    # 保存配置
    with open(config_path, 'w', encoding='utf-8') as f:
        json.dump(config, f, indent=2, ensure_ascii=False)

    logger.info(f"已为 {configured_count}/{len(agent_ids)} 个 agents 配置沙箱")


def delete_worker_agents(worker_prefix: str = "gendata-worker") -> List[str]:
    """删除所有指定前缀的 worker agents。

    Args:
        worker_prefix: worker agent 前缀

    Returns:
        已删除的 agent ID 列表
    """
    existing_agents = list_agents()
    worker_agents = [
        agent["id"] for agent in existing_agents
        if agent["id"].startswith(worker_prefix)
    ]

    deleted = []
    for agent_id in worker_agents:
        logger.info(f"删除 agent: {agent_id}")
        result = subprocess.run(
            ["openclaw", "agents", "delete", agent_id, "--force", "--json"],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            logger.error(f"删除 agent {agent_id} 失败: {result.stderr}")
            continue
        deleted.append(agent_id)

    return deleted


def ensure_agents(
    num_agents: int,
    worker_prefix: str = "gendata-worker",
    workspace_root: Optional[str] = None,
    force_recreate: bool = False,
    add_tools: bool = True,
) -> Dict[str, List[str]]:
    """确保所需数量的 worker agents 存在并使用独立 workspace。

    Args:
        num_agents: 要创建的 agent 数量
        worker_prefix: worker agent 前缀
        workspace_root: workspace 根目录
        force_recreate: 是否强制删除所有 worker agents 重新创建
        add_tools: 是否自动配置 sandbox 和工具（默认 True）

    Returns:
        包含 created、existing、deleted 列表的字典
    """
    root_dir = resolve_workspace_root(workspace_root)
    root_dir.mkdir(parents=True, exist_ok=True)

    deleted: List[str] = []

    # 强制重置：删除所有 worker agents
    if force_recreate:
        logger.info(f"强制删除所有 {worker_prefix} agents...")
        deleted = delete_worker_agents(worker_prefix)
        if deleted:
            logger.info(f"已删除 {len(deleted)} 个 agents: {', '.join(deleted)}")

    # 获取当前 agents
    existing_agents = {agent["id"]: agent for agent in list_agents()}

    created: List[str] = []
    existing: List[str] = []

    # 创建所需数量的 agents（简化逻辑，不检查 workspace）
    for index in range(1, num_agents + 1):
        agent_id = f"{worker_prefix}-{index}"
        desired_workspace = expected_agent_workspace(agent_id, str(root_dir))
        desired_workspace.mkdir(parents=True, exist_ok=True)

        # 如果 agent 已存在，跳过创建
        if agent_id in existing_agents:
            existing.append(agent_id)
            continue

        # 创建新 agent
        cmd = [
            "openclaw", "agents", "add", agent_id,
            "--non-interactive",
            "--workspace", str(desired_workspace),
            "--json",
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"Failed to create agent {agent_id}: {result.stderr}")
        created.append(agent_id)

    # 配置 workspace 和工具（不配置 sandbox）
    if add_tools:
        all_agent_ids = [f"{worker_prefix}-{i+1}" for i in range(num_agents)]
        logger.info(f"开始配置 {len(all_agent_ids)} 个 agents...")
        for agent_id in all_agent_ids:
            desired_workspace = expected_agent_workspace(agent_id, str(root_dir))
            configure_agent(
                agent_id,
                workspace=str(desired_workspace),
                sandbox=None,  # 不配置 sandbox
                tools_allow=WORKER_TOOLS_ALLOW
            )
        logger.info(
            f"已完成配置（workspace + {len(WORKER_TOOLS_ALLOW)} 个工具）"
        )

    return {"created": created, "existing": existing, "deleted": deleted}


class OpenClawWrapper:
    """OpenClaw CLI 封装。"""

    def __init__(self, agent_name: str = "gendata-worker-1", state_dir: Optional[str] = None):
        """初始化。"""
        self.agent_name = agent_name
        self.state_dir = Path(state_dir) if state_dir else Path.home() / ".openclaw"

    @property
    def session_key(self) -> str:
        return f"agent:{self.agent_name}:main"

    @property
    def sessions_dir(self) -> Path:
        return self.state_dir / "agents" / self.agent_name / "sessions"

    @property
    def session_store_path(self) -> Path:
        return self.sessions_dir / "sessions.json"

    def _load_session_store(self) -> Dict[str, Any]:
        if not self.session_store_path.exists():
            return {}
        with open(self.session_store_path, "r", encoding="utf-8") as file_obj:
            return json.load(file_obj)

    def _write_session_store(self, store: Dict[str, Any]) -> None:
        self.session_store_path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self.session_store_path.with_suffix(".json.tmp")
        with open(temp_path, "w", encoding="utf-8") as file_obj:
            json.dump(store, file_obj, ensure_ascii=False, indent=2)
        temp_path.replace(self.session_store_path)

    def get_current_session_info(self) -> Optional[Dict[str, Any]]:
        return self._load_session_store().get(self.session_key)

    def get_current_session_id(self) -> Optional[str]:
        session_info = self.get_current_session_info()
        if not session_info:
            return None
        return session_info.get("sessionId")

    def reset_main_session(self) -> Dict[str, Optional[str]]:
        """清空当前 worker 的 main session 映射，并让下一轮对话自动创建新 session。"""
        store = self._load_session_store()
        session_info = store.pop(self.session_key, None)

        archived_reset_file: Optional[Path] = None
        session_id = session_info.get("sessionId") if session_info else None
        if session_id:
            session_file = Path(self.get_session_file(session_id))
            if session_file.exists():
                timestamp = (
                    datetime.now(timezone.utc)
                    .isoformat(timespec="milliseconds")
                    .replace("+00:00", "Z")
                    .replace(":", "-")
                )
                archived_reset_file = session_file.with_name(f"{session_file.name}.reset.{timestamp}")
                session_file.rename(archived_reset_file)

        self._write_session_store(store)
        return {
            "previous_session_id": session_id,
            "reset_file": str(archived_reset_file) if archived_reset_file else None,
        }

    def send_message(
        self,
        message: str,
        timeout: int = 600,
        thinking: Optional[str] = None,
    ) -> Dict[str, Any]:
        """发送消息到 OpenClaw。"""
        cmd = ["openclaw", "agent", "--agent", self.agent_name, "--message", message, "--json"]
        if thinking:
            cmd.extend(["--thinking", thinking])

        logger.info(f"Sending message to agent {self.agent_name}")

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)

            if result.returncode != 0:
                logger.error(f"OpenClaw command failed: {result.stderr}")
                raise RuntimeError(f"OpenClaw error: {result.stderr}")

            return _extract_json_payload(result.stdout)

        except subprocess.TimeoutExpired:
            logger.error(f"OpenClaw command timeout after {timeout}s")
            raise
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse OpenClaw response: {e}; stdout={result.stdout!r}; stderr={result.stderr!r}")
            raise

    @staticmethod
    def extract_assistant_text(response: Dict[str, Any]) -> str:
        """从 CLI JSON 响应中提取 assistant 文本。"""
        texts: List[str] = []
        for payload in response.get("result", {}).get("payloads", []):
            if not isinstance(payload, dict):
                continue
            text = payload.get("text")
            if text:
                texts.append(text)
        return "\n".join(texts).strip()

    def get_session_file(self, session_id: str) -> str:
        """获取 session 文件路径。"""
        return str(self.sessions_dir / f"{session_id}.jsonl")

    def archive_current_session(self, destination_file: str) -> Dict[str, Any]:
        """将当前 main session 的 jsonl 移动到项目输出目录。"""
        session_info = self.get_current_session_info()
        if not session_info:
            raise RuntimeError(f"No active session found for {self.agent_name}")

        session_id = session_info.get("sessionId")
        if not session_id:
            raise RuntimeError(f"Session metadata missing sessionId for {self.agent_name}")

        source_path = Path(self.get_session_file(session_id))
        if not source_path.exists():
            raise FileNotFoundError(f"Session file not found: {source_path}")

        destination_path = Path(destination_file)
        destination_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(source_path), str(destination_path))

        return {
            "agent_name": self.agent_name,
            "session_id": session_id,
            "session_key": self.session_key,
            "session_info": session_info,
            "source_path": str(source_path),
            "archived_path": str(destination_path),
        }
