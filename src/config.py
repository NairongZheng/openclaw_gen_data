"""配置加载模块。"""
import os
import re
from typing import Any, Dict, Mapping, Optional

import yaml

DEFAULT_CONFIG_PATH = "config/config.yaml"
ENV_VAR_PATTERN = re.compile(r"^\$\{([A-Z0-9_]+)(?::-(.*))?\}$")
CLI_OVERRIDE_PATHS = {
    "intents_file": ("paths", "intents_file"),
    "concurrent": ("openclaw", "num_workers"),
    "intents_per_session": ("generation", "intents_per_session"),
}


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

    config = apply_runtime_overrides(config, cli_args=cli_args)

    # 环境变量替换
    return _replace_env_vars(config)


def apply_runtime_overrides(
    config: Dict[str, Any],
    cli_args: Optional[Any] = None,
) -> Dict[str, Any]:
    """统一应用运行时参数覆盖，优先级：ENV > CLI > config。"""
    if not cli_args:
        return config

    for cli_name, path in CLI_OVERRIDE_PATHS.items():
        cli_value = getattr(cli_args, cli_name, None)
        if cli_value is None:
            continue

        current_value = _get_nested_value(config, path)
        if _env_placeholder_has_value(current_value):
            continue
        _set_nested_value(config, path, cli_value)

    return config


def _replace_env_vars(obj: Any) -> Any:
    """递归替换配置中的环境变量"""
    if isinstance(obj, dict):
        return {k: _replace_env_vars(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_replace_env_vars(item) for item in obj]
    if isinstance(obj, str):
        match = ENV_VAR_PATTERN.fullmatch(obj)
        if match:
            env_var, default_value = match.groups()
            env_value = os.getenv(env_var)
            if env_value not in (None, ""):
                return env_value
            if default_value is not None:
                return _replace_env_vars(default_value)
            return obj
    return obj


def _env_placeholder_has_value(value: Any) -> bool:
    if not isinstance(value, str):
        return False

    match = ENV_VAR_PATTERN.fullmatch(value)
    if not match:
        return False

    env_var, _ = match.groups()
    env_value = os.getenv(env_var)
    return env_value not in (None, "")


def _get_nested_value(config: Dict[str, Any], path: tuple[str, ...]) -> Any:
    current: Any = config
    for key in path:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _set_nested_value(config: Dict[str, Any], path: tuple[str, ...], value: Any) -> None:
    current = config
    for key in path[:-1]:
        current = current.setdefault(key, {})
    current[path[-1]] = value
