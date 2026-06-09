"""LLM 客户端 - 统一 query 生成逻辑"""
import json
import logging
import random
import time
from pathlib import Path
from typing import Dict, Any, List, Optional

from openai import OpenAI

logger = logging.getLogger(__name__)


def _get_thinking_extra_body(model: str, enable_thinking: bool) -> Optional[Dict[str, Any]]:
    """根据模型名称获取 thinking 配置的 extra_body 参数

    Args:
        model: 模型名称
        enable_thinking: 是否启用 thinking

    Returns:
        extra_body 字典或 None
    """
    model_lower = model.lower()

    # DeepSeek-R1 系列：使用 thinking.type 格式
    if "deepseek-r1" in model_lower:
        return {"thinking": {"type": "enabled" if enable_thinking else "disabled"}}

    # DeepSeek / Qwen / GLM：使用 enable_thinking 参数
    if "deepseek" in model_lower or "qwen" in model_lower or "glm" in model_lower:
        return {"enable_thinking": enable_thinking}

    # Claude/Anthropic：不需要 extra_body，使用原生支持
    if "claude" in model_lower or "anthropic" in model_lower:
        return None

    # OpenAI o1/o3 系列：内置推理，不需要额外参数
    if "o1" in model_lower or "o3" in model_lower:
        return None

    # 默认：尝试使用 enable_thinking
    return {"enable_thinking": enable_thinking}


class LLMClient:
    """LLM 客户端 - 统一处理所有 query 生成"""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        temperature: float = 0.7,
        max_tokens:  Optional[int] = None,
        timeout:  Optional[float] = None,
        retry_attempts: int = 3,
        retry_base_delay: float = 1.0,
        retry_max_delay: float = 8.0,
        enable_thinking: bool = True,
    ):
        """初始化 LLM 客户端

        Args:
            base_url: API 基础 URL
            api_key: API 密钥
            model: 模型名称
            temperature: 温度参数
            max_tokens: 最大生成 token 数
            timeout: 请求超时时间（秒）
            retry_attempts: 最大重试次数（包含首次请求）
            retry_base_delay: 重试基础退避时间（秒）
            retry_max_delay: 重试最大等待时间（秒）
            enable_thinking: 是否开启推理模式（默认 True）
        """
        client_kwargs = {"base_url": base_url, "api_key": api_key}
        if timeout is not None:
            client_kwargs["timeout"] = timeout

        self.client = OpenAI(**client_kwargs)
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.retry_attempts = max(1, retry_attempts)
        self.retry_base_delay = max(0.0, retry_base_delay)
        self.retry_max_delay = max(self.retry_base_delay, retry_max_delay)
        self.enable_thinking = enable_thinking
        prompt_file = Path(__file__).parent.parent / "prompts" / "user_model_system_prompt.txt"
        self._system_prompt_template: str = prompt_file.read_text(encoding="utf-8")

    def generate_next_query(
        self,
        intent: str,
        persona: Dict[str, Any],
        conversation_history: List[Dict[str, str]],
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
            "content": (
                "Based on the full conversation history and the original intent above, "
                "decide whether the task has been fully completed. "
                "If not, generate the single best next user message to advance the task."
            ),
        })

        last_error: Exception | None = None

        for attempt in range(1, self.retry_attempts + 1):
            try:
                # 根据模型获取对应的 thinking 配置
                extra_body = _get_thinking_extra_body(self.model, self.enable_thinking)

                request_kwargs = {
                    "model": self.model,
                    "messages": messages,
                    "temperature": self.temperature,
                    "max_tokens": self.max_tokens,
                    "response_format": {"type": "json_object"},
                }
                if extra_body is not None:
                    request_kwargs["extra_body"] = extra_body

                logger.debug(
                    "LLM request thinking=%s model=%s extra_body=%s",
                    self.enable_thinking,
                    self.model,
                    extra_body,
                )

                response = self.client.chat.completions.create(**request_kwargs)

                content = response.choices[0].message.content
                if not content:
                    raise ValueError("LLM 返回空响应内容")

                result = json.loads(content)
                logger.info(
                    "LLM decision: completed=%s (attempt %s/%s)",
                    result.get("completed", False),
                    attempt,
                    self.retry_attempts,
                )
                return result

            except Exception as error:
                last_error = error
                if attempt >= self.retry_attempts:
                    break

                sleep_seconds = self._compute_retry_delay(attempt)
                logger.warning(
                    "LLM 请求失败，准备重试 (%s/%s)，%.2f 秒后重试: %s",
                    attempt,
                    self.retry_attempts,
                    sleep_seconds,
                    error,
                )
                time.sleep(sleep_seconds)

        logger.error("LLM API error after %s attempts: %s", self.retry_attempts, last_error)
        raise last_error if last_error is not None else RuntimeError("LLM 调用失败")

    def _compute_retry_delay(self, attempt: int) -> float:
        """计算指数退避重试等待时间。"""
        backoff = self.retry_base_delay * (2 ** (attempt - 1))
        bounded_backoff = min(backoff, self.retry_max_delay)
        jitter = random.uniform(0, min(0.5, bounded_backoff * 0.1))
        return bounded_backoff + jitter

    def _build_system_prompt(self, intent: str, persona: Dict[str, Any]) -> str:
        """构建 system prompt

        从外部文件加载 prompt 模板并填充变量

        Args:
            intent: 用户意图
            persona: 用户画像

        Returns:
            System prompt 字符串
        """
        # 读取 prompt 模板文件
        template = self._system_prompt_template

        # 提取 persona 字段
        name             = persona.get("name", "the user")
        role             = persona.get("role", "professional")
        industry         = persona.get("industry", "technology")
        experience_level = persona.get("experience_level", "intermediate")
        comm_style       = persona.get("communication_style", "direct")
        work_context     = persona.get("work_context", "")
        expertise_list   = persona.get("expertise", [])
        expertise_str    = ", ".join(expertise_list) if expertise_list else "general software development"

        # 格式化模板
        return template.format(
            name=name,
            experience_level=experience_level,
            role=role,
            industry=industry,
            expertise_str=expertise_str,
            comm_style=comm_style,
            work_context=work_context if work_context else "standard professional environment",
            intent=intent
        )
