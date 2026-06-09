"""工具函数"""
import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, Optional


def setup_logging(log_dir: str = "output/logs", level: int = logging.INFO):
    """设置日志

    Args:
        log_dir: 日志目录
        level: 日志级别
    """
    Path(log_dir).mkdir(parents=True, exist_ok=True)

    logging.basicConfig(
        level=level,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[
            logging.FileHandler(f"{log_dir}/generation.log"),
            logging.StreamHandler()
        ]
    )


def save_json(data: Any, filepath: str):
    """保存 JSON 文件（原子性写入，避免 Ctrl+C 中断导致文件损坏）"""
    Path(filepath).parent.mkdir(parents=True, exist_ok=True)

    # 先写到临时文件
    temp_file = f"{filepath}.tmp"
    with open(temp_file, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    # 原子性 rename（即使被 Ctrl+C 中断，也不会损坏原文件）
    Path(temp_file).rename(filepath)


def load_json(filepath: str) -> Any:
    """加载 JSON 文件"""
    with open(filepath, 'r', encoding='utf-8') as f:
        return json.load(f)


def ensure_dir(path: str) -> None:
    """确保目录存在。"""
    Path(path).mkdir(parents=True, exist_ok=True)


def resolve_project_root() -> Path:
    """解析项目根目录。"""
    if "__file__" in globals():
        return Path(__file__).parent.parent
    return Path(os.getcwd())


def merge_dicts(base: Optional[Dict[str, Any]], updates: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """递归合并字典，updates 优先。lists 做去重合并，None 输入视为空 dict。"""
    merged: Dict[str, Any] = dict(base or {})
    for key, value in (updates or {}).items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = merge_dicts(merged[key], value)
        elif isinstance(value, list) and isinstance(merged.get(key), list):
            existing = list(merged[key])
            for item in value:
                if item not in existing:
                    existing.append(item)
            merged[key] = existing
        else:
            merged[key] = value
    return merged
