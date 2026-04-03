"""Task 输入模式与 append query 辅助逻辑测试。"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.generation_support import (
    build_session_batch_metadata,
    resolve_append_query_enabled,
    select_append_query_task,
)
from src.intent_loader import load_intents, normalize_task_record


class TaskModeTests(unittest.TestCase):
    def test_normalize_intent_record(self) -> None:
        normalized = normalize_task_record(
            {
                "id": "intent_1",
                "natural_language_intent": "帮我总结今天的科技新闻",
            },
            1,
        )

        self.assertEqual(normalized["id"], "intent_1")
        self.assertEqual(normalized["task_type"], "intent")
        self.assertEqual(normalized["natural_language_intent"], "帮我总结今天的科技新闻")

    def test_normalize_question_record_to_direct_query(self) -> None:
        normalized = normalize_task_record(
            {
                "question": "2025 年最值得关注的 AI Agent 产品有哪些？",
                "answer": "示例答案",
            },
            2,
        )

        self.assertEqual(normalized["task_type"], "direct_query")
        self.assertTrue(normalized["id"].startswith("query_"))
        self.assertEqual(normalized["query"], "2025 年最值得关注的 AI Agent 产品有哪些？")
        self.assertEqual(normalized["natural_language_intent"], normalized["query"])
        self.assertEqual(normalized["metadata"]["reference_answer"], "示例答案")
        self.assertEqual(normalized["metadata"]["source_question"], normalized["query"])

    def test_load_intents_supports_mixed_tasks(self) -> None:
        records = [
            {"id": "intent_a", "natural_language_intent": "搜索上海今天的天气"},
            {"id": "query_a", "query": "上海今天适合穿什么？"},
        ]
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".jsonl", delete=False) as handle:
            path = Path(handle.name)
            for record in records:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")

        try:
            tasks = load_intents(str(path))
        finally:
            path.unlink(missing_ok=True)

        self.assertEqual([task["task_type"] for task in tasks], ["intent", "direct_query"])
        self.assertEqual(tasks[1]["natural_language_intent"], tasks[1]["query"])

    def test_load_intents_skips_invalid_empty_intent_rows(self) -> None:
        records = [
            {"id": "intent_a", "natural_language_intent": "搜索上海今天的天气"},
            {"id": "intent_bad", "natural_language_intent": ""},
            {"id": "query_a", "question": "上海明天会下雨吗？"},
        ]
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".jsonl", delete=False) as handle:
            path = Path(handle.name)
            for record in records:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")

        try:
            tasks = load_intents(str(path))
        finally:
            path.unlink(missing_ok=True)

        self.assertEqual(len(tasks), 2)
        self.assertEqual([task["id"] for task in tasks], ["intent_a", "query_a"])

    def test_select_append_query_task_is_stable(self) -> None:
        pool = [
            {"id": "query_1", "task_type": "direct_query", "query": "q1"},
            {"id": "query_2", "task_type": "direct_query", "query": "q2"},
            {"id": "query_3", "task_type": "direct_query", "query": "q3"},
        ]

        selected_a = select_append_query_task(pool, "intent_anchor")
        selected_b = select_append_query_task(pool, "intent_anchor")

        self.assertEqual(selected_a, selected_b)
        self.assertIn(selected_a["id"], {"query_1", "query_2", "query_3"})

    def test_resolve_append_query_enabled_supports_bool_and_string(self) -> None:
        self.assertTrue(resolve_append_query_enabled({"generation": {"append_query_enabled": True}}))
        self.assertTrue(resolve_append_query_enabled({"generation": {"append_query_enabled": "true"}}))
        self.assertTrue(resolve_append_query_enabled({"generation": {"append_query_enabled": "on"}}))
        self.assertFalse(resolve_append_query_enabled({"generation": {"append_query_enabled": False}}))
        self.assertFalse(resolve_append_query_enabled({"generation": {"append_query_enabled": "false"}}))

    def test_build_session_batch_metadata_includes_appended_query(self) -> None:
        metadata = build_session_batch_metadata(
            session_results=[
                {
                    "intent_id": "intent_1",
                    "intent_data": {
                        "task_type": "intent",
                        "natural_language_intent": "先完成一个普通 intent",
                    },
                },
                {
                    "intent_id": "query_1",
                    "intent_data": {
                        "task_type": "direct_query",
                        "natural_language_intent": "补一条搜索 query",
                    },
                },
            ],
            finalized_by_intent_id="query_1",
            appended_query={
                "status": "success",
                "query_task_id": "append_1",
                "query": "2025 年最新 AI 搜索产品",
            },
        )

        self.assertEqual(metadata["intent_count"], 2)
        self.assertEqual(metadata["finalized_by_intent_id"], "query_1")
        self.assertEqual(metadata["intent_records"][0]["task_type"], "intent")
        self.assertEqual(metadata["intent_records"][1]["task_type"], "direct_query")
        self.assertEqual(metadata["appended_query"]["query_task_id"], "append_1")


if __name__ == "__main__":
    unittest.main(verbosity=2)
