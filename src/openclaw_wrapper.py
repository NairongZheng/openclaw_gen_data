"""OpenClaw CLI 封装与 agent/session 管理。"""
import json
import logging
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


DEFAULT_OPENCLAW_CONFIG_PATH = Path.home() / ".openclaw" / "openclaw.json"


# Worker 工具默认 allowlist（配置缺失时兜底）
WORKER_TOOLS_ALLOW = [
    "read", "write", "edit", "apply_patch",
    "exec", "process",
    "web_search", "web_fetch",
    "memory_search", "memory_get",
    "sessions_list", "sessions_history", "sessions_send", "sessions_spawn",
    "session_status", "subagents", "agents_list",
    "image", "tts"
]


def get_openclaw_config_path(config_path: Optional[Path] = None) -> Path:
    """返回 OpenClaw 配置文件路径。"""
    return config_path or DEFAULT_OPENCLAW_CONFIG_PATH


def load_openclaw_config(config_path: Optional[Path] = None, default: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """加载 OpenClaw 配置文件。"""
    resolved_path = get_openclaw_config_path(config_path)
    if resolved_path.exists():
        with open(resolved_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    return dict(default or {})


def save_openclaw_config(config: Dict[str, Any], config_path: Optional[Path] = None) -> None:
    """保存 OpenClaw 配置文件。"""
    resolved_path = get_openclaw_config_path(config_path)
    resolved_path.parent.mkdir(parents=True, exist_ok=True)
    with open(resolved_path, 'w', encoding='utf-8') as f:
        json.dump(config, f, indent=2, ensure_ascii=False)


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
    tools_allow: Optional[List[str]] = None,
    model: Optional[str] = None,
    skills: Optional[List[str]] = None,
    config_path: Optional[Path] = None
) -> None:
    """配置 agent 的 workspace、工具、provider、model 和 skills。

    Args:
        agent_id: agent 名称
        workspace: workspace 路径（可选）
        tools_allow: 工具列表（可选，使用 allow 完全替换）
        model: model 名称（可选）
        skills: skills 列表（可选）
        config_path: 配置文件路径（可选，默认 ~/.openclaw/openclaw.json）
    """
    config = load_openclaw_config(config_path, default={"agents": {"list": []}})

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

    # 5. 配置 tools（使用 allow 完全替换）
    if tools_allow is not None:
        agent_config["tools"] = {"allow": tools_allow}
        logger.info(f"已配置 agent {agent_id} 的工具列表（allow: {len(tools_allow)} 个）")

    # 6. 配置 provider（覆盖已有配置）
    # 注意：OpenClaw 不支持 agent 级别的 providers 配置，跳过
    # if provider is not None:
    #     if "providers" not in agent_config:
    #         agent_config["providers"] = {}
    #     agent_config["providers"]["trajectory_provider"] = provider
    #     logger.info(f"已配置 agent {agent_id} 的 provider: trajectory_provider")

    # 7. 配置 model（覆盖已有配置）
    if model is not None:
        agent_config["model"] = model
        logger.info(f"已配置 agent {agent_id} 的 model: {model}")

    # 8. 配置 skills（覆盖已有配置）
    if skills is not None:
        agent_config["skills"] = skills
        logger.info(f"已配置 agent {agent_id} 的 skills: {len(skills)} 个")

    # 9. 保存配置文件
    save_openclaw_config(config, config_path)


def configure_global_skills(
    extra_dirs: Optional[List[str]] = None,
    allow_bundled: Optional[List[str]] = None,
    config_path: Optional[Path] = None
) -> None:
    """配置全局 skills 设置。

    Args:
        extra_dirs: 额外的 skills 目录列表
        allow_bundled: 允许的内置 skills 列表（空数组表示禁用所有内置 skills）
        config_path: 配置文件路径（可选，默认 ~/.openclaw/openclaw.json）
    """
    config = load_openclaw_config(config_path)

    # 确保 skills 字段存在
    if "skills" not in config:
        config["skills"] = {}

    # 配置 extraDirs
    if extra_dirs is not None:
        if "load" not in config["skills"]:
            config["skills"]["load"] = {}
        config["skills"]["load"]["extraDirs"] = extra_dirs
        logger.info(f"已配置全局 skills.load.extraDirs: {len(extra_dirs)} 个目录")

    # 配置 allowBundled
    if allow_bundled is not None:
        config["skills"]["allowBundled"] = allow_bundled
        logger.info(f"已配置全局 skills.allowBundled: {len(allow_bundled)} 个内置 skill")

    # 保存配置文件
    save_openclaw_config(config, config_path)


def configure_global_provider(
    provider_name: str,
    base_url: str,
    api_key: str,
    model_id: str,
    provider_api: str = "anthropic-messages",
    context_window: int = 200000,
    max_tokens: int = 200000,
    config_path: Optional[Path] = None
) -> None:
    """配置全局 provider。

    Args:
        provider_name: provider 名称
        base_url: API 基础 URL
        api_key: API key
        model_id: 模型 ID
        provider_api: provider API 类型
        context_window: 上下文窗口大小（默认 200000）
        max_tokens: 最大生成 token 数（默认 200000）
        config_path: 配置文件路径（可选，默认 ~/.openclaw/openclaw.json）
    """
    config = load_openclaw_config(config_path)

    # 确保 models.providers 字段存在
    if "models" not in config:
        config["models"] = {}
    if "providers" not in config["models"]:
        config["models"]["providers"] = {}

    # 配置 provider
    config["models"]["providers"][provider_name] = {
        "baseUrl": base_url,
        "apiKey": api_key,
        "api": provider_api,
        "models": [
            {
                "id": model_id,
                "name": model_id,
                "api": provider_api,
                "reasoning": False,
                "input": ["text"],
                "cost": {
                    "input": 0,
                    "output": 0,
                    "cacheRead": 0,
                    "cacheWrite": 0
                },
                "contextWindow": context_window,
                "maxTokens": max_tokens
            }
        ]
    }

    # 保存配置文件
    save_openclaw_config(config, config_path)

    logger.info(f"已配置全局 provider: {provider_name}")


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
    tools_allow: Optional[List[str]] = None,
) -> Dict[str, List[str]]:
    """确保所需数量的 worker agents 存在并使用独立 workspace。

    Args:
        num_agents: 要创建的 agent 数量
        worker_prefix: worker agent 前缀
        workspace_root: workspace 根目录
        force_recreate: 是否强制删除所有 worker agents 重新创建
        add_tools: 是否自动配置工具（默认 True）
        tools_allow: worker 工具 allowlist；为空时使用默认列表

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

    # 配置 workspace 和工具
    if add_tools:
        effective_tools_allow = WORKER_TOOLS_ALLOW if tools_allow is None else tools_allow
        all_agent_ids = [f"{worker_prefix}-{i+1}" for i in range(num_agents)]
        logger.info(f"开始配置 {len(all_agent_ids)} 个 agents...")
        for agent_id in all_agent_ids:
            desired_workspace = expected_agent_workspace(agent_id, str(root_dir))
            configure_agent(
                agent_id,
                workspace=str(desired_workspace),
                tools_allow=effective_tools_allow
            )
        logger.info(
            f"已完成配置（workspace + {len(effective_tools_allow)} 个工具）"
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

        # 添加 --timeout 参数，覆盖 OpenClaw Gateway 的默认 600s 超时
        cmd.extend(["--timeout", str(timeout)])

        logger.info(f"Sending message to agent {self.agent_name} (timeout={timeout}s)")

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
