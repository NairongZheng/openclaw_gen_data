"""配置加载模块。"""
import os
from typing import Any, Dict, Mapping, Optional

import yaml

DEFAULT_CONFIG_PATH = "config/config.yaml"


def resolve_config_path(
    cli_config_path: Optional[str] = None,
    default_path: str = DEFAULT_CONFIG_PATH,
) -> str:
    env_config_path = (os.getenv("CONFIG_PATH") or "").strip()
    if env_config_path:
        return env_config_path
    if cli_config_path:
        return cli_config_path
    return default_path


def load_config(config_path: Optional[str] = None, cli_args: Optional[Any] = None) -> Dict[str, Any]:
    """加载配置文件

    Args:
        config_path: 配置文件路径
        cli_args: 命令行参数对象，用于统一应用运行时覆盖

    Returns:
        配置字典
    """
    resolved_config_path = resolve_config_path(config_path)
    with open(resolved_config_path, 'r', encoding='utf-8') as f:
        config = yaml.safe_load(f)

    # 环境变量替换
    config = _replace_env_vars(config)
    return apply_runtime_overrides(config, cli_args=cli_args)


def apply_runtime_overrides(
    config: Dict[str, Any],
    cli_args: Optional[Any] = None,
    env: Optional[Mapping[str, str]] = None,
) -> Dict[str, Any]:
    """统一应用运行时参数覆盖，优先级：ENV > CLI > config。"""
    env_map = env or os.environ

    config.setdefault("paths", {})["intents_file"] = _resolve_runtime_override(
        cli_value=getattr(cli_args, "intents_file", None) if cli_args else None,
        env_name="INTENTS_FILE",
        config_value=config.get("paths", {}).get("intents_file"),
        caster=str,
        env=env_map,
    )
    config.setdefault("openclaw", {})["num_workers"] = _resolve_runtime_override(
        cli_value=getattr(cli_args, "concurrent", None) if cli_args else None,
        env_name="CONCURRENT_NUM",
        config_value=config.get("openclaw", {}).get("num_workers"),
        caster=int,
        env=env_map,
    )
    config.setdefault("generation", {})["intents_per_session"] = _resolve_runtime_override(
        cli_value=getattr(cli_args, "intents_per_session", None) if cli_args else None,
        env_name="INTENTS_PER_SESSION",
        config_value=config.get("generation", {}).get("intents_per_session"),
        caster=int,
        env=env_map,
    )

    return config


def _replace_env_vars(obj: Any) -> Any:
    """递归替换配置中的环境变量"""
    if isinstance(obj, dict):
        return {k: _replace_env_vars(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [_replace_env_vars(item) for item in obj]
    elif isinstance(obj, str) and obj.startswith("${") and obj.endswith("}"):
        env_var = obj[2:-1]
        return os.getenv(env_var, obj)
    return obj


def _resolve_runtime_override(
    cli_value: Any,
    env_name: str,
    config_value: Any,
    caster=None,
    env: Optional[Mapping[str, str]] = None,
) -> Any:
    """解析单个运行参数，优先级：ENV > CLI > config。"""
    env_map = env or os.environ
    env_value = (env_map.get(env_name) or "").strip()
    if env_value:
        return caster(env_value) if caster else env_value

    if cli_value is not None:
        return caster(cli_value) if caster else cli_value

    return config_value
