"""Serper 插件测试。

用法：
1. 静态检查（不访问外网）
   python tests/test_serper_plugin.py
   pytest -q tests/test_serper_plugin.py

2. 实网 smoke test（验证 Serper API 与 runtime patch）
   OPENCLAW_SEARCH_PROVIDER=serper \
   OPENCLAW_SEARCH_API_KEY=你的key \
   OPENCLAW_SEARCH_BASE_URL=https://google.serper.dev \
   python tests/test_serper_plugin.py --live
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from typing import Dict, Iterator

import requests


PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src import openclaw_wrapper, runtime_config


PLUGIN_DIR = PROJECT_ROOT / "openclaw_plugins" / "serper"
PLUGIN_ENTRY = PLUGIN_DIR / "index.js"
PLUGIN_MANIFEST = PLUGIN_DIR / "openclaw.plugin.json"
PLUGIN_PACKAGE = PLUGIN_DIR / "package.json"


@contextmanager
def temporary_env(updates: Dict[str, str | None]) -> Iterator[None]:
    original = {key: os.environ.get(key) for key in updates}
    try:
        for key, value in updates.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        yield
    finally:
        for key, value in original.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


@contextmanager
def temporary_openclaw_config_path() -> Iterator[Path]:
    original = openclaw_wrapper.DEFAULT_OPENCLAW_CONFIG_PATH
    with tempfile.TemporaryDirectory(prefix="serper-plugin-test-") as tmpdir:
        config_path = Path(tmpdir) / ".openclaw" / "openclaw.json"
        openclaw_wrapper.DEFAULT_OPENCLAW_CONFIG_PATH = config_path
        try:
            yield config_path
        finally:
            openclaw_wrapper.DEFAULT_OPENCLAW_CONFIG_PATH = original


class SerperPluginStaticTests(unittest.TestCase):
    def test_plugin_files_exist(self) -> None:
        self.assertTrue(PLUGIN_DIR.exists(), f"plugin dir missing: {PLUGIN_DIR}")
        self.assertTrue(PLUGIN_ENTRY.exists(), f"plugin entry missing: {PLUGIN_ENTRY}")
        self.assertTrue(PLUGIN_MANIFEST.exists(), f"plugin manifest missing: {PLUGIN_MANIFEST}")
        self.assertTrue(PLUGIN_PACKAGE.exists(), f"plugin package missing: {PLUGIN_PACKAGE}")

    def test_manifest_declares_web_search_provider(self) -> None:
        manifest = json.loads(PLUGIN_MANIFEST.read_text(encoding="utf-8"))
        self.assertEqual(manifest["id"], "serper")
        self.assertIn("serper", manifest["contracts"]["webSearchProviders"])
        self.assertIn("webSearch", manifest["configSchema"]["properties"])

    def test_package_declares_openclaw_extension(self) -> None:
        package = json.loads(PLUGIN_PACKAGE.read_text(encoding="utf-8"))
        manifest = json.loads(PLUGIN_MANIFEST.read_text(encoding="utf-8"))
        self.assertEqual(package["name"], manifest["id"])
        self.assertIn("openclaw", package)
        self.assertEqual(package["openclaw"]["extensions"], ["./index.js"])

    def test_resolve_search_env_config_uses_original_three_variables(self) -> None:
        with temporary_env(
            {
                "OPENCLAW_SEARCH_PROVIDER": "serper",
                "OPENCLAW_SEARCH_API_KEY": "test-key",
                "OPENCLAW_SEARCH_BASE_URL": "https://google.serper.dev",
            }
        ):
            resolved = runtime_config.resolve_search_env_config()

        self.assertEqual(
            resolved,
            {
                "provider": "serper",
                "api_key": "test-key",
                "base_url": "https://google.serper.dev",
            },
        )

    def test_resolve_search_env_config_requires_provider_key_and_url(self) -> None:
        with temporary_env(
            {
                "OPENCLAW_SEARCH_PROVIDER": "serper",
                "OPENCLAW_SEARCH_API_KEY": "test-key",
                "OPENCLAW_SEARCH_BASE_URL": None,
            }
        ):
            resolved = runtime_config.resolve_search_env_config()

        self.assertIsNone(resolved)

    def test_build_serper_search_patch_contains_plugin_load_path(self) -> None:
        patch = runtime_config.build_serper_search_patch(
            api_key="test-key",
            base_url="https://google.serper.dev",
        )

        self.assertEqual(patch["tools"]["web"]["search"]["provider"], "serper")
        self.assertTrue(patch["tools"]["web"]["fetch"]["enabled"])
        self.assertEqual(patch["plugins"]["load"]["paths"], [str(runtime_config.SERPER_PLUGIN_DIR)])
        self.assertTrue(patch["plugins"]["entries"]["serper"]["enabled"])
        self.assertEqual(
            patch["plugins"]["entries"]["serper"]["config"]["webSearch"]["apiKey"],
            "test-key",
        )

    def test_apply_runtime_patch_writes_serper_config(self) -> None:
        env = {
            "OPENCLAW_SEARCH_PROVIDER": "serper",
            "OPENCLAW_SEARCH_API_KEY": "test-key",
            "OPENCLAW_SEARCH_BASE_URL": "https://google.serper.dev",
        }
        with temporary_env(env), temporary_openclaw_config_path() as config_path:
            changed = runtime_config.apply_runtime_patch_from_env()
            self.assertTrue(changed)

            saved = openclaw_wrapper.load_openclaw_config(config_path=config_path)

        self.assertEqual(saved["tools"]["web"]["search"]["provider"], "serper")
        self.assertEqual(saved["plugins"]["load"]["paths"], [str(runtime_config.SERPER_PLUGIN_DIR)])
        self.assertEqual(
            saved["plugins"]["entries"]["serper"]["config"]["webSearch"]["baseUrl"],
            "https://google.serper.dev",
        )


def run_live_serper_smoke_test() -> None:
    provider = os.getenv("OPENCLAW_SEARCH_PROVIDER", "").strip()
    api_key = os.getenv("OPENCLAW_SEARCH_API_KEY", "").strip()
    base_url = os.getenv("OPENCLAW_SEARCH_BASE_URL", "").strip()

    if not (provider and api_key and base_url):
        raise SystemExit(
            "live 模式需要设置 OPENCLAW_SEARCH_PROVIDER / OPENCLAW_SEARCH_API_KEY / OPENCLAW_SEARCH_BASE_URL"
        )
    if provider != "serper":
        raise SystemExit("live 模式要求 OPENCLAW_SEARCH_PROVIDER=serper")

    with temporary_openclaw_config_path() as config_path:
        changed = runtime_config.apply_runtime_patch_from_env()
        saved = openclaw_wrapper.load_openclaw_config(config_path=config_path)

    print(f"[live] runtime patch applied: changed={int(changed)}")
    print(f"[live] config provider: {saved['tools']['web']['search']['provider']}")
    print(f"[live] plugin path: {saved['plugins']['load']['paths'][0]}")

    response = requests.post(
        f"{base_url.rstrip('/')}/search",
        headers={
            "X-API-KEY": api_key,
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        json={"q": "OpenClaw", "num": 1},
        timeout=30,
    )
    response.raise_for_status()
    payload = response.json()

    organic = payload.get("organic")
    answer_box = payload.get("answerBox")
    knowledge_graph = payload.get("knowledgeGraph")
    if not organic and not answer_box and not knowledge_graph:
        raise AssertionError("Serper 返回结构里没有 organic / answerBox / knowledgeGraph，无法确认插件输入输出契约")

    print("[live] Serper API reachable and payload shape looks valid")


def main() -> int:
    parser = argparse.ArgumentParser(description="Serper plugin tests")
    parser.add_argument("--live", action="store_true", help="额外执行一次 Serper 实网 smoke test")
    args = parser.parse_args()

    suite = unittest.defaultTestLoader.loadTestsFromTestCase(SerperPluginStaticTests)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if not result.wasSuccessful():
        return 1

    if args.live:
        run_live_serper_smoke_test()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())