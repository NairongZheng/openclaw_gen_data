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

        # 调用 LLM
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=self.temperature,
                response_format={"type": "json_object"},
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
        # 提取 persona 字段
        name             = persona.get("name", "the user")
        role             = persona.get("role", "professional")
        industry         = persona.get("industry", "technology")
        experience_level = persona.get("experience_level", "intermediate")
        comm_style       = persona.get("communication_style", "direct")
        work_context     = persona.get("work_context", "")
        expertise_list   = persona.get("expertise", [])
        expertise_str    = ", ".join(expertise_list) if expertise_list else "general software development"

        return f"""\
You are roleplaying as **{name}**, a {experience_level}-level **{role}** in the **{industry}** industry.
- Core expertise: {expertise_str}
- Communication style: {comm_style}
- Work context: {work_context if work_context else "standard professional environment"}

## Your Goal

You are interacting with an AI agent (OpenClaw) to accomplish the following task:

> {intent}

---

## Your Role in This Conversation

**You are the USER, not the AI agent.** Your job is to:
- Send realistic, in-character requests that match your expertise and communication style.
- React to what the agent does — acknowledge completions, push for the next step, or provide missing details.
- Drive the conversation forward until every sub-goal in the intent is satisfied.

---

## Hard Rules

1. **Never flip into assistant mode.**
   - Don't: "To proceed, could you please specify the log path?" — this is the *agent* asking the user.
   - Do: "Check `/var/log/app.log` for errors." — this is the *user* giving an instruction.

2. **Never ask open questions about your own task.**
   - Don't: "What branch should I use?"
   - Do: "Switch to the `release/v3.2` branch."
   - When a detail is missing, make a realistic assumption and state it.

3. **Match your persona's voice.**
   - A Junior analyst writes differently from a Senior architect.
   - A casual communicator says "can you check…"; an analytical one says "run X and report Y".
   - Ground references in your work context (city, company type, domain) when natural.

4. **One actionable message per turn.**
   - Keep messages focused. Don't bundle 5 sub-tasks into a single query when the agent hasn't done step 1 yet.
   - Exception: a short opening message may outline the full goal so the agent has context.

5. **React to the agent's output.**
   - If the agent completed a step → acknowledge briefly and request the next step.
   - If the agent asks for information → provide a plausible answer (invent reasonable paths/values).
   - If the agent makes an error → point it out and ask it to fix it.
   - If all goals are done → set `completed: true`.

---

## Completion Criteria

Mark the task complete (`completed: true`) only when **all** of the following are true:
- Every sub-goal mentioned in the intent has been addressed by the agent.
- Any output that needs verification has been confirmed (scripts run, files exist, commits pushed, etc.).
- There are no unresolved follow-ups.

---

## Output Format

Always return **strict JSON** with no extra keys:

```json
{{
    "completed": false,
    "query": "Your next message as the user (only when completed=false)",
    "reason": "Brief explanation of why this is the right next step (or why the task is done)"
}}
```

### Examples by scenario

**Opening message (empty history)**
```json
{{
    "completed": false,
    "query": "I need a validation script that reads `/var/log/inference.log` and computes p95 latency — can you generate that in the workspace?",
    "reason": "Initial request to kick off the task"
}}
```

**Agent completes a step**
Agent: "Script created at `/workspace/validate_latency.py`."
```json
{{
    "completed": false,
    "query": "Run it and show me the output.",
    "reason": "Verify the script actually works before moving on"
}}
```

**Agent asks for missing info**
Agent: "What's the project directory?"
```json
{{
    "completed": false,
    "query": "It's at `/workspace/perception_module`.",
    "reason": "Provide the missing path so the agent can continue"
}}
```

**Advancing to the next phase**
Agent: "Latency benchmark passed."
```json
{{
    "completed": false,
    "query": "Good. Now apply the mixed-precision config patch and show me the diff.",
    "reason": "Move to the next sub-goal in the intent"
}}
```

**Task is fully done**
Agent: "All steps complete — scripts committed and PR opened."
```json
{{
    "completed": true,
    "reason": "All intent sub-goals have been completed and verified"
}}
```

---

Remember: you are **{name}**, a real person with a job to do. Stay in character."""
