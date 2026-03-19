"""工具函数"""
import json
import logging
from pathlib import Path
from typing import Any


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
    """保存 JSON 文件"""
    Path(filepath).parent.mkdir(parents=True, exist_ok=True)
    with open(filepath, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def load_json(filepath: str) -> Any:
    """加载 JSON 文件"""
    with open(filepath, 'r', encoding='utf-8') as f:
        return json.load(f)


def ensure_dir(path: str) -> None:
    """确保目录存在。"""
    Path(path).mkdir(parents=True, exist_ok=True)
