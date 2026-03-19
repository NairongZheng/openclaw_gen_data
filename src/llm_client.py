"""LLM 客户端 - 统一 query 生成逻辑"""
import json
import logging
from typing import Dict, Any, List
from openai import OpenAI

logger = logging.getLogger(__name__)


class LLMClient:
    """LLM 客户端 - 统一处理所有 query 生成"""

    def __init__(self, base_url: str, api_key: str, model: str, temperature: float = 0.7):
        """初始化 LLM 客户端

        Args:
            base_url: API 基础 URL
            api_key: API 密钥
            model: 模型名称
            temperature: 温度参数
        """
        self.client = OpenAI(base_url=base_url, api_key=api_key)
        self.model = model
        self.temperature = temperature

    def generate_next_query(
        self,
        intent: str,
        persona: Dict[str, Any],
        conversation_history: List[Dict[str, str]]
    ) -> Dict[str, Any]:
        """根据当前状态生成下一个 query 或判断完成

        统一处理逻辑：
        - 如果 history 为空，生成初始 query
        - 如果有 history，根据最新响应判断是否完成或生成下一个 query

        Args:
            intent: 用户意图描述
            persona: 用户画像
            conversation_history: 对话历史

        Returns:
            {
                "completed": bool,  # 是否完成
                "query": str,       # 下一个 query（如果未完成）
                "reason": str       # 完成原因或下一步说明
            }
        """
        # 构建 system prompt
        system_prompt = self._build_system_prompt(intent, persona)

        # 构建 messages
        messages = [{"role": "system", "content": system_prompt}]

        messages.extend(conversation_history)
        messages.append({
            "role": "user",
            "content": "请基于当前 intent 与完整历史，判断任务是否已经完成；如果未完成，就生成下一条最合适的用户 query。"
        })

        # 调用 LLM
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=self.temperature,
                response_format={"type": "json_object"}
            )

            result = json.loads(response.choices[0].message.content)
            logger.info(f"LLM decision: completed={result.get('completed', False)}")

            return result

        except Exception as e:
            logger.error(f"LLM API error: {e}")
            raise

    def _build_system_prompt(self, intent: str, persona: Dict[str, Any]) -> str:
        """构建 system prompt

        Args:
            intent: 用户意图
            persona: 用户画像

        Returns:
            System prompt 字符串
        """
        return f"""你是一个智能助手，负责帮助用户通过与 AI 编程助手（OpenClaw）的对话来完成编程任务。

用户意图：{intent}

用户画像：
- 技能水平：{persona.get('skill_level', 'unknown')}
- 偏好风格：{persona.get('communication_style', 'unknown')}

你的任务：
1. 分析当前 intent 与已有对话历史，判断任务是否真的完成
2. 如果任务已完成（代码已实现、问题已解决），返回 completed=true
3. 如果需要继续（需要确认、需要更多信息、需要下一步），生成下一个 query

返回 JSON 格式：
{{
    "completed": true/false,
    "query": "下一个 query 内容（如果 completed=false）",
    "reason": "判断理由"
}}

注意：
- Query 应该自然、符合用户画像
- 不要过早结束，确保任务真正完成
- 每个 query 应该推进任务进展
"""
