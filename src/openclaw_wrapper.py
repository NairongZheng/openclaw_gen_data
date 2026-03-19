"""OpenClaw CLI 封装与 agent/session 管理。"""
import json
import logging
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


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


def ensure_agents(
    num_agents: int,
    worker_prefix: str = "gendata-worker",
    workspace_dir: Optional[str] = None,
) -> Dict[str, List[str]]:
    """确保所需数量的 worker agents 存在。"""
    existing_agents = {agent["id"] for agent in list_agents()}
    workspace = workspace_dir or str(Path.home() / ".openclaw/workspace")

    created: List[str] = []
    existing: List[str] = []

    for index in range(1, num_agents + 1):
        agent_id = f"{worker_prefix}-{index}"
        if agent_id in existing_agents:
            existing.append(agent_id)
            continue

        cmd = [
            "openclaw",
            "agents",
            "add",
            agent_id,
            "--non-interactive",
            "--workspace",
            workspace,
            "--json",
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"Failed to create agent {agent_id}: {result.stderr}")
        created.append(agent_id)

    return {"created": created, "existing": existing}


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
