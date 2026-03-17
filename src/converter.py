"""格式转换模块 - 转换为 OpenAI 完整格式"""
import json
import logging
from typing import Dict, Any, List
from pathlib import Path
from src.session_parser import SessionParser

logger = logging.getLogger(__name__)


class DataConverter:
    """数据格式转换器 - 生成符合 middle_format 规范的数据"""

    def __init__(self):
        """初始化转换器"""
        self.parser = SessionParser()

    def convert_session_to_middle_format(
        self,
        session_file: str,
        intent_data: Dict[str, Any],
        output_file: str
    ) -> Dict[str, Any]:
        """将 session 文件转换为 middle format（OpenAI 完整格式）

        Args:
            session_file: Session 文件路径
            intent_data: Intent 数据
            output_file: 输出文件路径

        Returns:
            转换后的数据
        """
        logger.info(f"Converting session file: {session_file}")

        if not Path(session_file).exists():
            logger.error(f"Session file not found: {session_file}")
            raise FileNotFoundError(f"Session file not found: {session_file}")

        try:
            messages = self.parser.parse_jsonl_file(session_file)
        except Exception as e:
            logger.error(f"Failed to parse session file: {e}")
            raise

        # 构建符合规范的 middle format
        middle_format = {
            "status": "completed",
            "session_id": Path(session_file).stem,
            "intent": intent_data.get("natural_language_intent"),
            "total_steps": self._count_tool_calls(messages),
            "enable_thinking": True,
            "messages": self._extract_messages_openai_format(messages),
            "tools": self._extract_tools(messages),
            "final_output": self._extract_final_output(messages),
            "intent_id": intent_data.get("id"),
            "metadata": intent_data.get("metadata", {})
        }

        Path(output_file).parent.mkdir(parents=True, exist_ok=True)
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(middle_format, f, ensure_ascii=False, indent=2)

        logger.info(f"Conversion completed: {output_file}")
        return middle_format

    def _extract_messages_openai_format(self, messages: List[Dict]) -> List[Dict[str, Any]]:
        """提取 OpenAI 格式的完整消息列表"""
        openai_messages = []

        for msg_obj in messages:
            msg = msg_obj.get("message", {})
            role = msg.get("role")
            content = msg.get("content", [])

            if role == "user":
                openai_messages.append({
                    "role": "user",
                    "content": self.parser.extract_text_from_content(content)
                })

            elif role == "assistant":
                text = self.parser.extract_text_from_content(content)
                tool_calls_list = self.parser.extract_tool_calls(content)

                # 转换为 OpenAI 格式的 tool_calls
                tool_calls = [
                    {
                        "id": tc["id"],
                        "type": "function",
                        "function": {
                            "name": tc["name"],
                            "arguments": json.dumps(tc["arguments"])
                        }
                    }
                    for tc in tool_calls_list
                ]

                assistant_msg = {
                    "role": "assistant",
                    "content": text
                }
                if tool_calls:
                    assistant_msg["tool_calls"] = tool_calls

                # 添加 reasoning_content（如果有）
                reasoning = self._extract_reasoning(content)
                if reasoning:
                    assistant_msg["reasoning_content"] = reasoning

                openai_messages.append(assistant_msg)

            elif role == "toolResult":
                tool_result = self.parser.extract_tool_result(msg_obj)
                openai_messages.append({
                    "role": "tool",
                    "name": tool_result["name"],
                    "tool_call_id": tool_result["tool_call_id"],
                    "content": tool_result["content"]
                })

        return openai_messages

    def _extract_reasoning(self, content: List[Dict]) -> str:
        """提取 reasoning 内容"""
        for item in content:
            if item.get("type") == "thinking":
                return item.get("text", "")
        return ""

    def _extract_tools(self, messages: List[Dict]) -> List[Dict[str, Any]]:
        """从 session 中提取工具定义"""
        tool_names = set()
        tool_schemas = {}

        for msg_obj in messages:
            msg = msg_obj.get("message", {})
            if msg.get("role") == "assistant":
                tool_calls = self.parser.extract_tool_calls(msg.get("content", []))
                for tc in tool_calls:
                    name = tc["name"]
                    tool_names.add(name)
                    if name not in tool_schemas:
                        tool_schemas[name] = tc.get("arguments", {})

        tools = []
        for name in sorted(tool_names):
            tools.append({
                "type": "function",
                "function": {
                    "name": name,
                    "description": f"Tool: {name}",
                    "parameters": {
                        "type": "object",
                        "properties": tool_schemas.get(name, {}),
                        "required": []
                    }
                }
            })

        return tools

    def _count_tool_calls(self, messages: List[Dict]) -> int:
        """统计工具调用总数"""
        count = 0
        for msg_obj in messages:
            msg = msg_obj.get("message", {})
            if msg.get("role") == "assistant":
                tool_calls = self.parser.extract_tool_calls(msg.get("content", []))
                count += len(tool_calls)
        return count

    def _extract_final_output(self, messages: List[Dict]) -> str:
        """提取最终输出（最后一条 assistant 消息）"""
        for i in range(len(messages) - 1, -1, -1):
            msg = messages[i].get("message", {})
            if msg.get("role") == "assistant":
                return self.parser.extract_text_from_content(msg.get("content", []))
        return ""
