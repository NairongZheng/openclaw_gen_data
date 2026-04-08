"""Runtime tools proxy helpers 测试。"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.runtime_tools_proxy import append_capture_record, build_capture_record


class RuntimeToolsProxyTests(unittest.TestCase):
    def test_build_capture_record_keeps_exact_tools(self) -> None:
        payload = {
            "model": "demo-model",
            "messages": [{"role": "user", "content": "hi"}],
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": "read",
                        "description": "Read file",
                        "parameters": {"type": "object", "properties": {"path": {"type": "string"}}},
                    },
                }
            ],
        }

        record = build_capture_record("POST", "/v1/chat/completions", payload)

        self.assertIsNotNone(record)
        self.assertEqual(record["tool_count"], 1)
        self.assertEqual(record["tool_names"], ["read"])
        self.assertEqual(record["tools"], payload["tools"])

    def test_append_capture_record_writes_jsonl_and_latest(self) -> None:
        record = {
            "captured_at": "2026-01-01T00:00:00+00:00",
            "tool_names": ["read"],
            "tool_count": 1,
            "tools": [],
        }

        with tempfile.TemporaryDirectory() as tmp_dir:
            output_file = Path(tmp_dir) / "runtime_probe.jsonl"
            append_capture_record(output_file, record)

            jsonl_lines = output_file.read_text(encoding="utf-8").strip().splitlines()
            latest_file = Path(tmp_dir) / "runtime_probe_latest.json"

            self.assertEqual(len(jsonl_lines), 1)
            self.assertEqual(json.loads(jsonl_lines[0])["tool_names"], ["read"])
            self.assertTrue(latest_file.exists())
            self.assertEqual(json.loads(latest_file.read_text(encoding="utf-8"))["tool_count"], 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)