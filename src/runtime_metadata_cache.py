"""运行时 metadata 缓存读写辅助。"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


DEFAULT_RUNTIME_METADATA_CACHE_FILE = "output/worker_snapshots/runtime_metadata/runtime_metadata.json"


def resolve_runtime_metadata_cache_file(
    paths_config: Optional[Dict[str, Any]] = None,
    cache_file: Optional[str] = None,
) -> str:
    """解析运行时 metadata 缓存文件路径。

    优先级：显式传入 > paths.runtime_metadata_cache_file > 默认值
    """
    if cache_file:
        return str(cache_file)

    paths_config = paths_config or {}
    runtime_metadata_cache_file = paths_config.get("runtime_metadata_cache_file")
    if runtime_metadata_cache_file:
        return str(runtime_metadata_cache_file)

    return DEFAULT_RUNTIME_METADATA_CACHE_FILE


def resolve_runtime_probe_capture_file(
    agent_id: str,
    paths_config: Optional[Dict[str, Any]] = None,
    cache_file: Optional[str] = None,
) -> Path:
    """解析 runtime probe 调试快照文件路径。

    调试快照与共享 runtime metadata 放在同一体系下，避免继续混在旧的 tools 目录。
    默认会落在 `output/worker_snapshots/runtime_metadata/probe/` 下。
    """
    metadata_cache_file = Path(resolve_runtime_metadata_cache_file(paths_config=paths_config, cache_file=cache_file))
    probe_dir = metadata_cache_file.parent / "probe"
    return probe_dir / f"runtime_probe_{agent_id}.jsonl"


def build_runtime_metadata_payload(
    tools: List[Dict[str, Any]],
    system_prompt: str,
) -> Dict[str, Any]:
    """构造共享运行时 metadata。"""
    return {
        "schema_version": 1,
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "tools": list(tools),
        "system_prompt": system_prompt or "",
    }


def load_runtime_metadata_cache(cache_file: str) -> Dict[str, Any]:
    """读取运行时 metadata 缓存文件。"""
    cache_path = Path(cache_file)
    if not cache_path.exists():
        return {}

    payload = json.loads(cache_path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {}


def save_runtime_metadata_cache(cache_file: str, payload: Dict[str, Any]) -> None:
    """保存运行时 metadata 缓存文件。"""
    cache_path = Path(cache_file)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def extract_tools_from_runtime_metadata(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    """从 runtime metadata 中提取 tools。"""
    tools = payload.get("tools")
    if isinstance(tools, list):
        return tools

    return []


def extract_system_prompt_from_runtime_metadata(payload: Dict[str, Any]) -> str:
    """从 runtime metadata 中提取 system prompt。"""
    system_prompt = payload.get("system_prompt")
    if isinstance(system_prompt, str) and system_prompt:
        return system_prompt

    return ""
