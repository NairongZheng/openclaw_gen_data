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
        output_file: str,
        tools_catalog: List[Dict[str, Any]] = None,
        available_tool_entries: List[Dict[str, Any]] = None,
        skills: List[Dict[str, Any]] = None,
        session_metadata: Dict[str, Any] = None,
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

        openai_messages = self._extract_messages_openai_format(messages)
        enable_thinking = any(msg.get("reasoning_content") for msg in openai_messages)

        # 构建符合规范的 middle format
        middle_format = {
            "status": "completed",
            "session_id": Path(session_file).stem,
            "intent": intent_data.get("natural_language_intent"),
            "total_steps": self._count_tool_calls(messages),
            "enable_thinking": enable_thinking,
            "messages": openai_messages,
            "tools": self._extract_tools(messages, tools_catalog, available_tool_entries),
            "skills": skills or [],
            "final_output": self._extract_final_output(messages),
            "intent_id": intent_data.get("id"),
            "metadata": {
                **intent_data.get("metadata", {}),
                "session": session_metadata or {},
            },
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

                # 过滤完全空白的消息（既无文本也无工具调用）
                # 这些通常是 OpenClaw 内部的模型切换或重试产生的中间状态
                if not text and not tool_calls_list:
                    logger.debug(f"Skipping empty assistant message at timestamp {msg.get('timestamp')}")
                    continue

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
                    "content": tool_result["content"],
                    "success": tool_result["success"],
                })

        return openai_messages

    def _extract_reasoning(self, content: List[Dict]) -> str:
        """提取 reasoning 内容"""
        reasoning_parts: List[str] = []
        for item in content:
            if item.get("type") == "thinking":
                reasoning_text = item.get("thinking") or item.get("text") or ""
                if reasoning_text:
                    reasoning_parts.append(reasoning_text)
        return "\n\n".join(reasoning_parts).strip()

    def _extract_tools(
        self,
        messages: List[Dict],
        tools_catalog: List[Dict[str, Any]] = None,
        available_tool_entries: List[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        """提取工具定义。优先使用预生成 catalog，否则退回 session 元数据 + 实际调用参数。"""
        tool_names = set()
        tool_schemas: Dict[str, Dict[str, Any]] = {}

        for msg_obj in messages:
            msg = msg_obj.get("message", {})
            if msg.get("role") == "assistant":
                tool_calls = self.parser.extract_tool_calls(msg.get("content", []))
                for tc in tool_calls:
                    name = tc["name"]
                    tool_names.add(name)
                    if name not in tool_schemas:
                        tool_schemas[name] = tc.get("arguments", {})

        available_entries = available_tool_entries or []
        available_names = [entry.get("name") for entry in available_entries if entry.get("name")]

        if tools_catalog:
            if available_names:
                filtered_catalog = [
                    tool for tool in tools_catalog
                    if tool.get("function", {}).get("name") in set(available_names)
                ]
            else:
                filtered_catalog = list(tools_catalog)

            catalog_names = {tool.get("function", {}).get("name") for tool in filtered_catalog}
            missing_names = [name for name in sorted(tool_names) if name not in catalog_names]
            fallback_tools = [self._build_fallback_tool(name, tool_schemas.get(name, {})) for name in missing_names]
            return filtered_catalog + fallback_tools

        tools = []
        merged_names = []
        seen_names = set()
        for name in available_names + sorted(tool_names):
            if name and name not in seen_names:
                merged_names.append(name)
                seen_names.add(name)

        entry_by_name = {entry.get("name"): entry for entry in available_entries if entry.get("name")}
        for name in merged_names:
            entry = entry_by_name.get(name, {})
            observed_arguments = tool_schemas.get(name, {})
            description = self._build_available_tool_description(name, entry, observed_arguments)
            tools.append({
                "type": "function",
                "function": {
                    "name": name,
                    "description": description,
                    "parameters": {
                        "type": "object",
                        "properties": self._to_json_schema_properties(observed_arguments),
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

    def _to_json_schema_properties(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """将一次工具调用的参数样本转换为兜底 JSON Schema。"""
        properties: Dict[str, Any] = {}
        for key, value in arguments.items():
            if isinstance(value, bool):
                value_type = "boolean"
            elif isinstance(value, int):
                value_type = "integer"
            elif isinstance(value, float):
                value_type = "number"
            elif isinstance(value, list):
                value_type = "array"
            elif isinstance(value, dict):
                value_type = "object"
            else:
                value_type = "string"

            properties[key] = {
                "type": value_type,
                "description": f"Observed argument for {key}",
            }
        return properties

    def _build_fallback_tool(self, name: str, observed_arguments: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": name,
                "description": f"Available tool: {name}",
                "parameters": {
                    "type": "object",
                    "properties": self._to_json_schema_properties(observed_arguments),
                    "required": [],
                },
            },
        }

    def _build_available_tool_description(
        self,
        name: str,
        entry: Dict[str, Any],
        observed_arguments: Dict[str, Any],
    ) -> str:
        if observed_arguments:
            return f"Available tool: {name}. Parameter schema inferred from observed calls."

        properties_count = entry.get("propertiesCount")
        if properties_count is not None:
            return f"Available tool: {name}. OpenClaw reported {properties_count} parameter fields."

        return f"Available tool: {name}"
