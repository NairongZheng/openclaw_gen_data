import json
import re
from typing import Any, List, Dict, Optional


class SessionParser:
    """OpenClaw Session文件解析器"""

    @staticmethod
    def parse_jsonl_file(file_path: str) -> List[Dict]:
        """解析JSONL文件，返回所有消息对象

        Args:
            file_path: session文件路径

        Returns:
            List[Dict]: 消息对象列表，每个对象包含完整的message数据

        Raises:
            FileNotFoundError: 文件不存在
            json.JSONDecodeError: JSON解析错误
        """
        messages = []
        with open(file_path, 'r', encoding='utf-8') as f:
            for line_num, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                    # 只保留type为"message"的对象
                    if obj.get("type") == "message":
                        messages.append(obj)
                except json.JSONDecodeError as e:
                    print(f"警告: 第{line_num}行JSON解析失败: {e}")
                    continue
        return messages

    @staticmethod
    def extract_text_from_content(content: List[Dict]) -> str:
        """从content数组中提取纯文本内容，并清理OpenClaw CLI添加的时间戳前缀

        Args:
            content: message.content数组

        Returns:
            str: 提取的文本内容（多个text部分用空格连接）

        Note:
            OpenClaw CLI 在通过 --message 发送消息时会自动添加时间戳前缀，
            格式如: [Day YYYY-MM-DD HH:MM TIMEZONE] 或 [Day HH:MM TIMEZONE]
            例如: [Fri 2026-03-20 17:45 GMT+8] 或 [Mon 09:30 UTC]
            此方法会自动移除这些前缀，以保证训练数据的干净性。
        """
        # 定义时间戳前缀的正则表达式
        # 格式: [Day YYYY-MM-DD HH:MM TIMEZONE] 或 [Day HH:MM TIMEZONE]
        # 其中 Day 为英文缩写（Mon/Tue/Wed/Thu/Fri/Sat/Sun）
        # TIMEZONE 格式为 [A-Z]{3}[+-]?\d* (如 GMT+8, UTC, EST-5)
        timestamp_pattern = r'^\[(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)\s+(?:\d{4}-\d{2}-\d{2}\s+)?\d{2}:\d{2}\s+[A-Z]{3}[+-]?\d*\]\s*'

        text_parts = []
        for item in content:
            if item.get("type") == "text":
                text = item.get("text", "")
                if text:
                    # 清理 OpenClaw CLI 自动添加的时间戳前缀
                    text = re.sub(timestamp_pattern, '', text)
                    text_parts.append(text)
        return " ".join(text_parts).strip()

    @staticmethod
    def extract_tool_calls(assistant_content: List[Dict]) -> List[Dict]:
        """从assistant消息的content中提取工具调用

        Args:
            assistant_content: assistant消息的content数组

        Returns:
            List[Dict]: 工具调用列表，每个元素包含:
                - id: 工具调用ID
                - name: 工具名称
                - arguments: 工具参数（优先保留结构化对象；若原始值是无法解析的字符串，则原样返回）
        """
        tool_calls: List[Dict[str, Any]] = []
        for item in assistant_content:
            if item.get("type") == "toolCall":
                arguments = item.get("arguments", {})
                if isinstance(arguments, str):
                    try:
                        arguments = json.loads(arguments)
                    except json.JSONDecodeError:
                        pass
                tool_calls.append({
                    "id": item.get("id"),
                    "name": item.get("name"),
                    "arguments": arguments
                })
        return tool_calls

    @staticmethod
    def extract_tool_result(tool_result_message: Dict) -> Dict:
        """提取单个工具结果

        Args:
            tool_result_message: role为toolResult的消息对象

        Returns:
            Dict: 工具结果，包含:
                - tool_call_id: 对应的工具调用ID
                - name: 工具名称
                - content: 结果内容（str）
                - success: 是否成功（bool）
        """
        message = tool_result_message.get("message", {})
        content = message.get("content", [])

        # 提取文本内容
        result_text = SessionParser.extract_text_from_content(content)

        return {
            "tool_call_id": message.get("toolCallId"),
            "name": message.get("toolName"),
            "content": result_text,
            "success": not message.get("isError", False)
        }

    @staticmethod
    def extract_agent_response(messages: List[Dict]) -> Optional[Dict]:
        """从消息列表中提取最新的完整Agent回复

        一个完整的Agent回复包括：
        1. Assistant消息（可能包含toolCall）
        2. 若干个ToolResult消息（如果有工具调用）
        3. 最终的Assistant消息（stopReason="stop"）

        Args:
            messages: 消息对象列表（按时间排序）

        Returns:
            Dict or None: Agent回复信息，包含:
                - agent_text: Agent的文本回复
                - tool_calls: 工具调用列表
                - tool_results: 工具结果列表
                - timestamp: 最终回复的时间戳
                - completed: 是否完成（stopReason="stop"）
            如果没有找到完整回复，返回None
        """
        if not messages:
            return None

        # 从后往前查找最后一个assistant消息（stopReason="stop"）
        final_assistant_idx = -1
        for i in range(len(messages) - 1, -1, -1):
            msg = messages[i].get("message", {})
            if msg.get("role") == "assistant" and msg.get("stopReason") == "stop":
                final_assistant_idx = i
                break

        if final_assistant_idx == -1:
            # 没有找到完整的assistant回复
            return None

        final_assistant_msg = messages[final_assistant_idx].get("message", {})
        agent_text = SessionParser.extract_text_from_content(
            final_assistant_msg.get("content", [])
        )

        # 向前查找tool calls和tool results
        tool_calls = []
        tool_results = []

        # 从final_assistant往前找，直到遇到user消息或开头
        for i in range(final_assistant_idx - 1, -1, -1):
            msg = messages[i].get("message", {})
            role = msg.get("role")

            if role == "user":
                # 遇到user消息，停止
                break
            elif role == "toolResult":
                # 提取tool result
                tool_results.insert(0, SessionParser.extract_tool_result(messages[i]))
            elif role == "assistant":
                # 提取tool calls
                content = msg.get("content", [])
                calls = SessionParser.extract_tool_calls(content)
                tool_calls = calls + tool_calls  # 插入到前面

        return {
            "agent_text": agent_text,
            "tool_calls": tool_calls,
            "tool_results": tool_results,
            "timestamp": final_assistant_msg.get("timestamp", 0),
            "completed": True
        }

    @staticmethod
    def get_last_user_message(messages: List[Dict]) -> Optional[str]:
        """获取最后一条用户消息

        Args:
            messages: 消息对象列表

        Returns:
            str or None: 最后一条用户消息的文本内容
        """
        for i in range(len(messages) - 1, -1, -1):
            msg = messages[i].get("message", {})
            if msg.get("role") == "user":
                return SessionParser.extract_text_from_content(msg.get("content", []))
        return None

    @staticmethod
    def count_turns(messages: List[Dict]) -> int:
        """统计对话轮次数

        Args:
            messages: 消息对象列表

        Returns:
            int: 对话轮次数（user消息的数量）
        """
        count = 0
        for msg_obj in messages:
            msg = msg_obj.get("message", {})
            if msg.get("role") == "user":
                count += 1
        return count


if __name__ == "__main__":
    # 测试解析器
    import sys

    if len(sys.argv) > 1:
        session_file = sys.argv[1]
    else:
        # 使用默认示例文件
        session_file = "/Users/luosiyuan/.openclaw/agents/main/sessions/5b2cbb1d-bc5d-46b0-93c6-15a0a4ebd03a.jsonl"

    print(f"=== 解析Session文件: {session_file} ===\n")

    try:
        # 解析文件
        messages = SessionParser.parse_jsonl_file(session_file)
        print(f"✅ 成功解析 {len(messages)} 条消息\n")

        # 统计轮次
        turns = SessionParser.count_turns(messages)
        print(f"对话轮次: {turns}\n")

        # 提取最新的Agent回复
        agent_response = SessionParser.extract_agent_response(messages)
        if agent_response:
            print("=== 最新Agent回复 ===")
            print(f"文本: {agent_response['agent_text'][:200]}...")
            print(f"工具调用数: {len(agent_response['tool_calls'])}")
            print(f"工具结果数: {len(agent_response['tool_results'])}")
            print(f"完成状态: {agent_response['completed']}")
        else:
            print("⚠️ 未找到完整的Agent回复")

        # 提取最后一条用户消息
        last_user_msg = SessionParser.get_last_user_message(messages)
        if last_user_msg:
            print(f"\n=== 最后一条用户消息 ===\n{last_user_msg[:200]}...")

    except FileNotFoundError:
        print(f"❌ 文件不存在: {session_file}")
    except Exception as e:
        print(f"❌ 解析失败: {e}")
        import traceback
        traceback.print_exc()
