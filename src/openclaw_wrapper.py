"""OpenClaw CLI 封装"""
import subprocess
import json
import logging
from pathlib import Path
from typing import Dict, Any

logger = logging.getLogger(__name__)


class OpenClawWrapper:
    """OpenClaw CLI 封装"""

    def __init__(self, agent_name: str = "gendata-worker-1"):
        """初始化

        Args:
            agent_name: Agent 名称
        """
        self.agent_name = agent_name
        self.current_session_id = None  # 存储真实的 session ID

    def clear_session(self):
        """清空 session 对话历史"""
        if not self.current_session_id:
            return
        session_file = Path(self.get_session_file(self.current_session_id))
        if session_file.exists():
            session_file.unlink()
            logger.info(f"已清空 session: {self.current_session_id}")
        self.current_session_id = None

    def send_message(self, message: str, timeout: int = 600) -> Dict[str, Any]:
        """发送消息到 OpenClaw

        Args:
            message: 消息内容
            timeout: 超时时间（秒）

        Returns:
            JSON 响应字典
        """
        cmd = ["openclaw", "agent", "--agent", self.agent_name, "--message", message, "--json"]

        # 如果有当前 session，使用它
        if self.current_session_id:
            cmd.extend(["--session-id", self.current_session_id])

        logger.info(f"Sending message to agent {self.agent_name}")

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)

            if result.returncode != 0:
                logger.error(f"OpenClaw command failed: {result.stderr}")
                raise RuntimeError(f"OpenClaw error: {result.stderr}")

            response = json.loads(result.stdout)

            # 从响应中提取真实的 session ID
            if "result" in response and "meta" in response["result"]:
                self.current_session_id = response["result"]["meta"]["agentMeta"]["sessionId"]
                logger.debug(f"Session ID: {self.current_session_id}")

            return response

        except subprocess.TimeoutExpired:
            logger.error(f"OpenClaw command timeout after {timeout}s")
            raise
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse OpenClaw response: {e}")
            raise

    def get_session_file(self, session_id: str) -> str:
        """获取 session 文件路径

        Args:
            session_id: Session ID

        Returns:
            Session 文件路径
        """
        return str(Path.home() / f".openclaw/agents/{self.agent_name}/sessions/{session_id}.jsonl")
