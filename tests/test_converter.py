"""Converter 回归测试。"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.converter import DataConverter


class ConverterTests(unittest.TestCase):
    def test_tool_call_arguments_remain_structured(self) -> None:
        converter = DataConverter()

        messages = [
            {
                "message": {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "toolCall",
                            "id": "call_1",
                            "name": "read",
                            "arguments": {
                                "path": "/tmp/demo.txt",
                                "limit": 20,
                            },
                        }
                    ],
                }
            }
        ]

        converted = converter._extract_messages_openai_format(messages)

        self.assertEqual(len(converted), 1)
        tool_calls = converted[0]["tool_calls"]
        self.assertEqual(tool_calls[0]["function"]["name"], "read")
        self.assertEqual(
            tool_calls[0]["function"]["arguments"],
            {"path": "/tmp/demo.txt", "limit": 20},
        )
        self.assertIsInstance(tool_calls[0]["function"]["arguments"], dict)

    def test_invalid_json_tool_call_arguments_remain_raw_string(self) -> None:
        converter = DataConverter()

        messages = [
            {
                "message": {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "toolCall",
                            "id": "call_1",
                            "name": "read",
                            "arguments": '{"path": "/tmp/demo.txt",}',
                        }
                    ],
                }
            }
        ]

        converted = converter._extract_messages_openai_format(messages)

        self.assertEqual(len(converted), 1)
        tool_calls = converted[0]["tool_calls"]
        self.assertEqual(tool_calls[0]["function"]["name"], "read")
        self.assertEqual(
            tool_calls[0]["function"]["arguments"],
            '{"path": "/tmp/demo.txt",}',
        )
        self.assertIsInstance(tool_calls[0]["function"]["arguments"], str)


if __name__ == "__main__":
    unittest.main(verbosity=2)