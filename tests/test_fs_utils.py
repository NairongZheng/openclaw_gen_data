"""文件系统权限兜底工具测试。"""

from __future__ import annotations

import stat
import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.fs_utils import make_tree_owner_writable, remove_path, remove_tree


class FsUtilsTests(unittest.TestCase):
    def test_remove_tree_handles_readonly_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir) / "readonly-tree"
            nested = root / "nested"
            nested.mkdir(parents=True)
            target_file = nested / "artifact.txt"
            target_file.write_text("demo", encoding="utf-8")
            target_file.chmod(stat.S_IRUSR)
            nested.chmod(stat.S_IRUSR | stat.S_IXUSR)

            remove_tree(root)

            self.assertFalse(root.exists())

    def test_make_tree_owner_writable_restores_write_bit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir) / "snapshot"
            root.mkdir()
            child = root / "file.txt"
            child.write_text("demo", encoding="utf-8")
            child.chmod(stat.S_IRUSR)

            make_tree_owner_writable(root)

            self.assertTrue(child.stat().st_mode & stat.S_IWUSR)

    def test_remove_path_handles_readonly_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            target = Path(tmp_dir) / "readonly.txt"
            target.write_text("demo", encoding="utf-8")
            target.chmod(stat.S_IRUSR)

            remove_path(target)

            self.assertFalse(target.exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)