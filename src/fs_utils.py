"""文件系统辅助工具：兜底处理只读文件/目录，确保删除与恢复流程稳定。"""

from __future__ import annotations

import shutil
import stat
from pathlib import Path
from typing import Callable


def ensure_owner_writable(path: Path) -> None:
    """确保当前用户对路径可写；目录额外保证可遍历。"""
    if not path.exists() or path.is_symlink():
        return

    current_mode = path.stat().st_mode
    target_mode = current_mode | stat.S_IRUSR | stat.S_IWUSR
    if path.is_dir():
        target_mode |= stat.S_IXUSR

    if target_mode != current_mode:
        path.chmod(target_mode)


def make_tree_owner_writable(root: Path) -> None:
    """递归将目录树调整为 owner-writable，避免复制后保留只读位。"""
    if not root.exists():
        return

    ensure_owner_writable(root)
    if not root.is_dir() or root.is_symlink():
        return

    for path in root.rglob("*"):
        try:
            ensure_owner_writable(path)
        except FileNotFoundError:
            continue


def _retry_after_chmod(func: Callable[..., None], path: str, _exc_info) -> None:
    target = Path(path)
    try:
        ensure_owner_writable(target.parent)
    except FileNotFoundError:
        pass
    try:
        ensure_owner_writable(target)
    except FileNotFoundError:
        pass
    func(path)


def remove_tree(path: Path) -> None:
    """删除目录树；若遇到只读文件则先修正权限再重试。"""
    if not path.exists():
        return
    make_tree_owner_writable(path)
    shutil.rmtree(path, onerror=_retry_after_chmod)


def remove_path(path: Path) -> None:
    """删除单个文件或目录；遇到只读场景时自动修复权限。"""
    if not path.exists() and not path.is_symlink():
        return

    if path.is_dir() and not path.is_symlink():
        remove_tree(path)
        return

    try:
        ensure_owner_writable(path.parent)
    except FileNotFoundError:
        pass
    try:
        ensure_owner_writable(path)
    except FileNotFoundError:
        pass
    path.unlink(missing_ok=True)