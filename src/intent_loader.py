"""Intent 加载模块"""
import json
from typing import List, Dict, Any


def load_intents(filepath: str) -> List[Dict[str, Any]]:
    """加载 intents 文件

    Args:
        filepath: JSONL 文件路径

    Returns:
        Intent 列表
    """
    intents = []
    with open(filepath, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if line:
                intents.append(json.loads(line))
    return intents
