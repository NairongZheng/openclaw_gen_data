"""OpenClaw 运行时配置生成与应用。

这个模块的定位是：
1. 在任务启动前，按需修改 OpenClaw 的运行时配置（`~/.openclaw/openclaw.json`）
2. 只在配置实际发生变化时写盘，避免无意义重启依赖它的进程
3. 当前默认入口只处理 search/fetch 相关配置

也就是说，它本身是一个更通用的 runtime config patch 能力；
只是当前项目里，暂时只拿它来处理 `web.search` / `web.fetch`。
后续如果初始化阶段还需要补别的 OpenClaw 任务配置，也可以继续复用这里。
"""
import logging
import os
from pathlib import Path
from typing import Any, Dict, Optional

from src.openclaw_wrapper import load_openclaw_config, save_openclaw_config


logger = logging.getLogger(__name__)


RUNTIME_CONFIG_STATUS_PREFIX = "OPENCLAW_RUNTIME_CONFIG_CHANGED="
SERPER_PLUGIN_ID = "serper"
SERPER_PLUGIN_DIR = Path(__file__).resolve().parent.parent / "openclaw_plugins" / SERPER_PLUGIN_ID


def build_search_patch(provider: str, api_key: str, base_url: str) -> Dict[str, Any]:
    """根据 provider/apiKey/baseUrl 构造 OpenClaw web 配置 patch。"""
    return {
        "tools": {
            "web": {
                "fetch": {
                    "enabled": True,
                },
                "search": {
                    "enabled": True,
                    "provider": provider,
                    provider: {
                        "apiKey": api_key,
                        "baseUrl": base_url,
                    },
                },
            }
        }
    }


def build_serper_search_patch(api_key: str, base_url: str) -> Dict[str, Any]:
    """为 Serper 外部插件构造 OpenClaw runtime patch。"""
    return {
        "tools": {
            "web": {
                "fetch": {
                    "enabled": True,
                },
                "search": {
                    "enabled": True,
                    "provider": SERPER_PLUGIN_ID,
                },
            }
        },
        "plugins": {
            "load": {
                "paths": [str(SERPER_PLUGIN_DIR)],
            },
            "entries": {
                SERPER_PLUGIN_ID: {
                    "enabled": True,
                    "config": {
                        "webSearch": {
                            "apiKey": api_key,
                            "baseUrl": base_url,
                        }
                    },
                }
            },
        },
    }


def merge_dicts(base: Dict[str, Any], updates: Dict[str, Any]) -> Dict[str, Any]:
    """递归合并字典，updates 优先。"""
    merged: Dict[str, Any] = dict(base)
    for key, value in updates.items():
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


def resolve_search_env_config() -> Optional[Dict[str, str]]:
    """解析 search runtime 环境变量。"""
    provider = os.getenv("OPENCLAW_SEARCH_PROVIDER", "").strip()
    api_key = os.getenv("OPENCLAW_SEARCH_API_KEY", "").strip()
    base_url = os.getenv("OPENCLAW_SEARCH_BASE_URL", "").strip()

    if not (provider and api_key and base_url):
        return None

    return {
        "provider": provider,
        "api_key": api_key,
        "base_url": base_url,
    }



def apply_patch_if_changed(patch: Dict[str, Any]) -> bool:
    """应用 patch；仅在配置实际变化时写盘。"""
    current_config = load_openclaw_config()
    target_config = merge_dicts(current_config, patch)

    if target_config == current_config:
        logger.info("OpenClaw runtime config 无变化")
        return False

    save_openclaw_config(target_config)
    logger.info("已应用 OpenClaw runtime patch")
    return True


def apply_runtime_patch_from_env() -> bool:
    """从环境变量生成并应用 runtime patch。"""
    search_env = resolve_search_env_config()
    if search_env is None:
        logger.info("未提供完整的 search 配置，跳过 runtime config")
        return False

    if search_env["provider"] == SERPER_PLUGIN_ID:
        if not SERPER_PLUGIN_DIR.exists():
            raise FileNotFoundError(f"Serper plugin directory not found: {SERPER_PLUGIN_DIR}")
        patch = build_serper_search_patch(
            api_key=search_env["api_key"],
            base_url=search_env["base_url"],
        )
    else:
        patch = build_search_patch(
            provider=search_env["provider"],
            api_key=search_env["api_key"],
            base_url=search_env["base_url"],
        )
    return apply_patch_if_changed(patch)


def main() -> int:
    logging.basicConfig(level=logging.INFO)
    changed = apply_runtime_patch_from_env()
    print(f"{RUNTIME_CONFIG_STATUS_PREFIX}{'1' if changed else '0'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())