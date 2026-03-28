"""OpenClaw 运行时恢复相关工具。"""
import difflib
import json
import logging
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

_CONFIG_CORRUPTION_STRONG_HINTS = [
    "openclaw.json",
    "json5 parse failed",
    "run: openclaw doctor --fix",
]

_CONFIG_CORRUPTION_GENERIC_HINTS = [
    "config invalid",
    "invalid configuration",
    "failed to parse",
    "unexpected token",
]


def resolve_openclaw_runtime_paths() -> Dict[str, Path]:
    home_dir = Path(os.environ.get("OPENCLAW_HOME", str(Path.home() / ".openclaw"))).expanduser()
    config_file = Path(os.environ.get("OPENCLAW_CONFIG_FILE", str(home_dir / "openclaw.json"))).expanduser()
    baseline_file = Path(
        os.environ.get("OPENCLAW_CONFIG_BASELINE", str(home_dir / "openclaw.json.baseline"))
    ).expanduser()
    gateway_log = Path(os.environ.get("OPENCLAW_GATEWAY_LOG", str(home_dir / "gateway.log"))).expanduser()
    return {
        "home_dir": home_dir,
        "config_file": config_file,
        "baseline_file": baseline_file,
        "gateway_log": gateway_log,
    }


def is_openclaw_config_json_valid(config_file: Path) -> bool:
    if not config_file.exists():
        return False
    try:
        with open(config_file, "r", encoding="utf-8") as file_obj:
            payload = json.load(file_obj)
        return isinstance(payload, dict)
    except Exception:
        return False


def calculate_config_drift_ratio(config_file: Path, baseline_file: Path) -> Optional[float]:
    """计算当前配置相对 baseline 的变更比例（0~1，越大表示变化越大）。"""
    if not config_file.exists() or not baseline_file.exists():
        return None

    try:
        with open(config_file, "r", encoding="utf-8") as file_obj:
            current_payload = json.load(file_obj)
        with open(baseline_file, "r", encoding="utf-8") as file_obj:
            baseline_payload = json.load(file_obj)

        baseline_text = json.dumps(baseline_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        current_text = json.dumps(current_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        similarity = difflib.SequenceMatcher(a=baseline_text, b=current_text).ratio()
        return 1.0 - similarity
    except Exception:
        return None


def error_message_contains_config_corruption_hint(error_message: str) -> bool:
    normalized = (error_message or "").lower()
    return any(hint in normalized for hint in (_CONFIG_CORRUPTION_STRONG_HINTS + _CONFIG_CORRUPTION_GENERIC_HINTS))


def _count_config_corruption_signals(error_message: str, drift_ratio: Optional[float], drift_threshold: float) -> int:
    normalized = (error_message or "").lower()
    strong_hint_hit = any(hint in normalized for hint in _CONFIG_CORRUPTION_STRONG_HINTS)
    generic_hint_hit = any(hint in normalized for hint in _CONFIG_CORRUPTION_GENERIC_HINTS)
    drift_hit = drift_ratio is not None and drift_ratio >= drift_threshold

    signal_count = 0
    if strong_hint_hit:
        signal_count += 1
    if generic_hint_hit:
        signal_count += 1
    if drift_hit:
        signal_count += 1
    return signal_count


def looks_like_config_corruption_error(error_message: str, config_file: Path, baseline_file: Path) -> bool:
    # 1) JSON 本身无效，直接判定为配置损坏
    if not is_openclaw_config_json_valid(config_file):
        return True

    # 2) 错误信息未命中配置损坏特征时，直接放过，避免普通失败误判
    if not error_message_contains_config_corruption_hint(error_message):
        return False

    # 3) 只有多重信号同时出现时才触发恢复，降低误伤正常错误的概率
    max_drift_ratio = float(os.environ.get("OPENCLAW_CONFIG_MAX_DRIFT_RATIO", "0.35"))
    drift_ratio = calculate_config_drift_ratio(config_file, baseline_file)
    if drift_ratio is not None and drift_ratio >= max_drift_ratio:
        logger.warning(
            "openclaw 配置漂移比例过大（drift=%.3f, threshold=%.3f）",
            drift_ratio,
            max_drift_ratio,
        )

    signal_count = _count_config_corruption_signals(error_message, drift_ratio, max_drift_ratio)
    if signal_count < 2:
        logger.info(
            "跳过全局恢复：配置损坏信号不足（signals=%s, error=%s）",
            signal_count,
            (error_message or "")[:200],
        )
        return False

    return True


def terminate_openclaw_gateway_processes() -> int:
    """终止现有 openclaw gateway 进程，返回匹配到的进程数量。"""
    try:
        import psutil

        matched_processes = []
        for process in psutil.process_iter(["pid", "cmdline"]):
            cmdline = process.info.get("cmdline") or []
            normalized = " ".join(str(part) for part in cmdline).lower()
            if "openclaw" in normalized and "gateway" in normalized and "run" in normalized:
                matched_processes.append(process)

        if not matched_processes:
            return 0

        for process in matched_processes:
            try:
                process.terminate()
            except psutil.NoSuchProcess:
                continue

        _, alive = psutil.wait_procs(matched_processes, timeout=3)

        for process in alive:
            try:
                process.kill()
            except psutil.NoSuchProcess:
                continue

        _, still_alive = psutil.wait_procs(alive, timeout=3)
        if still_alive:
            remaining_pids = [str(process.pid) for process in still_alive if process.is_running()]
            if remaining_pids:
                raise RuntimeError(f"无法停止 gateway 进程: {', '.join(remaining_pids)}")

        return len(matched_processes)
    except ImportError:
        logger.warning("psutil 不可用，退回到系统命令停止 gateway")
    except Exception as exc:
        if os.name == "nt":
            raise RuntimeError(f"Windows 下停止 gateway 失败: {exc}") from exc
        logger.warning("使用 psutil 停止 gateway 失败，尝试 pkill: %s", exc)

    if os.name != "nt":
        result = subprocess.run(
            ["pkill", "-f", "openclaw gateway run"],
            capture_output=True,
            text=True,
        )
        if result.returncode not in (0, 1):
            raise RuntimeError(result.stderr or result.stdout or "pkill 执行失败")
        return 0

    raise RuntimeError("当前环境无法安全停止已有 gateway 进程")


def restart_openclaw_gateway(gateway_log: Path) -> None:
    gateway_log.parent.mkdir(parents=True, exist_ok=True)

    stopped_count = terminate_openclaw_gateway_processes()
    if stopped_count:
        logger.warning("已停止 %s 个旧 gateway 进程", stopped_count)

    with open(gateway_log, "a", encoding="utf-8") as log_file:
        process = subprocess.Popen(
            ["openclaw", "gateway", "run"],
            stdout=log_file,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    time.sleep(2)
    exit_code = process.poll()
    if exit_code is not None:
        raise RuntimeError(f"gateway 启动失败，退出码={exit_code}，详见日志: {gateway_log}")


def recover_openclaw_runtime_from_baseline(reason: str) -> bool:
    """运行时自愈：回滚 openclaw.json 到 baseline，并重启 gateway。"""
    started_at = time.perf_counter()
    paths = resolve_openclaw_runtime_paths()
    config_file = paths["config_file"]
    baseline_file = paths["baseline_file"]
    gateway_log = paths["gateway_log"]

    baseline_usable = baseline_file.exists() and is_openclaw_config_json_valid(baseline_file)
    if not baseline_usable:
        logger.warning("baseline 不可用（不存在或非法 JSON）: %s", baseline_file)
        try:
            doctor_started_at = time.perf_counter()
            doctor_result = subprocess.run(
                ["openclaw", "doctor", "--fix"],
                capture_output=True,
                text=True,
                timeout=120,
            )
            logger.warning("openclaw doctor --fix 耗时 %.2fs", time.perf_counter() - doctor_started_at)
            if doctor_result.returncode != 0:
                logger.error("openclaw doctor --fix 失败: %s", doctor_result.stderr or doctor_result.stdout)
                return False
            if not is_openclaw_config_json_valid(config_file):
                logger.error("openclaw doctor --fix 后配置仍非法: %s", config_file)
                return False
            logger.warning("已通过 openclaw doctor --fix 修复配置")
        except Exception as exc:
            logger.error("自愈失败：执行 openclaw doctor --fix 异常: %s", exc)
            return False
    else:
        try:
            rollback_started_at = time.perf_counter()
            config_file.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(baseline_file, config_file)
            logger.warning("回滚 baseline 配置耗时 %.2fs", time.perf_counter() - rollback_started_at)
            logger.warning("检测到疑似配置污染（%s），已回滚配置: %s <- %s", reason, config_file, baseline_file)
        except Exception as exc:
            logger.error("自愈失败：回滚配置失败: %s", exc)
            return False

    if not is_openclaw_config_json_valid(config_file):
        logger.error("自愈失败：恢复后配置仍非法: %s", config_file)
        return False

    try:
        gateway_started_at = time.perf_counter()
        restart_openclaw_gateway(gateway_log)
        logger.warning("重启 OpenClaw gateway 耗时 %.2fs", time.perf_counter() - gateway_started_at)
        logger.warning("已重启 OpenClaw gateway，log=%s", gateway_log)
    except Exception as exc:
        logger.error("自愈失败：重启 gateway 失败: %s", exc)
        return False

    logger.warning("recover_openclaw_runtime_from_baseline 总耗时 %.2fs", time.perf_counter() - started_at)
    return True


def backup_openclaw_config_to_output(paths_config: Dict[str, Any]) -> Optional[Path]:
    """将当前 openclaw.json 备份到 output 目录，并作为运行时恢复基线。"""
    runtime_paths = resolve_openclaw_runtime_paths()
    config_file = runtime_paths["config_file"]

    backup_dir = Path(paths_config["output_dir"]) / "runtime_recovery"
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup_file = backup_dir / "openclaw.json.baseline"

    if not config_file.exists():
        logger.warning("openclaw 配置不存在，无法备份到 output: %s", config_file)
        return None

    if not is_openclaw_config_json_valid(config_file):
        logger.warning("openclaw 配置当前非法，跳过 baseline 备份: %s", config_file)
        return None

    try:
        shutil.copy2(config_file, backup_file)
        os.environ["OPENCLAW_CONFIG_BASELINE"] = str(backup_file)
        logger.info("已备份 openclaw 配置到 output: %s", backup_file)
        return backup_file
    except Exception as exc:
        logger.warning("备份 openclaw 配置到 output 失败: %s", exc)
        return None
