"""Intent / query 任务加载模块。"""
import hashlib
import json
from typing import List, Dict, Any


def _build_generated_id(record: Dict[str, Any], task_type: str, line_number: int) -> str:
    raw = json.dumps(record, ensure_ascii=False, sort_keys=True)
    digest = hashlib.md5(raw.encode("utf-8")).hexdigest()[:12]
    prefix = "intent" if task_type == "intent" else "query"
    return f"{prefix}_{line_number:06d}_{digest}"


def normalize_task_record(record: Dict[str, Any], line_number: int) -> Dict[str, Any]:
    """将输入记录规范化为统一 task 结构。"""
    if not isinstance(record, dict):
        raise ValueError(f"JSONL 第 {line_number} 行不是对象")

    explicit_task_type = str(record.get("task_type") or "").strip().lower()
    has_intent = isinstance(record.get("natural_language_intent"), str) and record.get("natural_language_intent", "").strip()
    has_query = isinstance(record.get("query"), str) and record.get("query", "").strip()
    has_question = isinstance(record.get("question"), str) and record.get("question", "").strip()

    if explicit_task_type == "direct_query" or ((has_query or has_question) and not has_intent):
        query = (record.get("query") or record.get("question") or "").strip()
        if not query:
            raise ValueError(f"JSONL 第 {line_number} 行 direct_query 缺少 query/question")

        metadata = dict(record.get("metadata") or {})
        if record.get("answer") is not None and "reference_answer" not in metadata:
            metadata["reference_answer"] = record.get("answer")
        if has_question and "source_question" not in metadata:
            metadata["source_question"] = record.get("question")

        task_id = str(
            record.get("id")
            or record.get("task_id")
            or record.get("query_id")
            or _build_generated_id(record, "direct_query", line_number)
        )
        normalized = dict(record)
        normalized.update(
            {
                "id": task_id,
                "task_type": "direct_query",
                "query": query,
                "natural_language_intent": query,
                "metadata": metadata,
            }
        )
        return normalized

    intent = str(record.get("natural_language_intent") or "").strip()
    if not intent:
        raise ValueError(f"JSONL 第 {line_number} 行缺少 natural_language_intent / query / question")

    task_id = str(
        record.get("id")
        or record.get("task_id")
        or record.get("intent_id")
        or _build_generated_id(record, "intent", line_number)
    )
    normalized = dict(record)
    normalized.update(
        {
            "id": task_id,
            "task_type": "intent",
            "natural_language_intent": intent,
        }
    )
    return normalized


def load_intents(filepath: str) -> List[Dict[str, Any]]:
    """加载任务文件，兼容 intent 与 direct_query 两类输入。

    Args:
        filepath: JSONL 文件路径

    Returns:
        标准化后的 task 列表
    """
    intents = []
    with open(filepath, 'r', encoding='utf-8') as f:
        for index, line in enumerate(f, start=1):
            line = line.strip()
            if line:
                intents.append(normalize_task_record(json.loads(line), index))
    return intents
