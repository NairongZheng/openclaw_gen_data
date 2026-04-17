"""短生命周期 OpenAI-compatible proxy，用于捕获初始化 probe 请求里的真实 runtime metadata。"""

from __future__ import annotations

import argparse
import json
import logging
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import urlsplit

import requests


logger = logging.getLogger(__name__)

_LOCK = threading.Lock()
_HOP_BY_HOP_HEADERS = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
    "host",
    "content-length",
}


def build_capture_record(method: str, path: str, payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    tools = payload.get("tools")
    if not isinstance(tools, list):
        return None

    tool_names = []
    for tool in tools:
        name = (tool or {}).get("function", {}).get("name")
        if name:
            tool_names.append(name)

    messages = payload.get("messages")

    # 提取 system prompt（第一条 system 消息）
    system_prompt = None
    if isinstance(messages, list) and len(messages) > 0:
        first_msg = messages[0]
        if isinstance(first_msg, dict) and first_msg.get("role") == "system":
            system_prompt = first_msg.get("content")

    return {
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "method": method,
        "path": path,
        "model": payload.get("model"),
        "tool_choice": payload.get("tool_choice"),
        "tool_count": len(tools),
        "tool_names": tool_names,
        "message_count": len(messages) if isinstance(messages, list) else None,
        "tools": tools,
        "system_prompt": system_prompt,  # 新增：捕获 system prompt
    }


def append_capture_record(output_file: Path, record: Dict[str, Any]) -> None:
    output_file.parent.mkdir(parents=True, exist_ok=True)
    latest_file = output_file.with_name(output_file.stem + "_latest.json")

    with _LOCK:
        with output_file.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        latest_file.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


class RuntimeToolsProxyHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    upstream_base_url: str = ""
    output_file: Path = Path("runtime_probe_metadata.jsonl")
    request_timeout: float = 600.0
    capture_only: bool = False

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/health":
            payload = json.dumps({"status": "ok"}, ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        self._forward_request()

    def do_POST(self) -> None:  # noqa: N802
        self._forward_request()

    def do_PUT(self) -> None:  # noqa: N802
        self._forward_request()

    def do_PATCH(self) -> None:  # noqa: N802
        self._forward_request()

    def do_DELETE(self) -> None:  # noqa: N802
        self._forward_request()

    def do_OPTIONS(self) -> None:  # noqa: N802
        self._forward_request()

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
        logger.debug("proxy %s - %s", self.address_string(), format % args)

    def _forward_request(self) -> None:
        try:
            body = self._read_body()
            payload = self._parse_json_body(body)
            self._capture_tools_if_present(payload)

            if self.capture_only and isinstance(payload, dict) and isinstance(payload.get("tools"), list):
                self._write_capture_only_response(payload)
                return

            upstream_url = self._build_upstream_url()
            headers = self._filtered_request_headers()

            response = requests.request(
                method=self.command,
                url=upstream_url,
                headers=headers,
                data=body or None,
                stream=True,
                timeout=self.request_timeout,
            )

            self.send_response(response.status_code)
            for header_name, header_value in response.headers.items():
                if header_name.lower() in _HOP_BY_HOP_HEADERS:
                    continue
                self.send_header(header_name, header_value)
            self.end_headers()

            for chunk in response.iter_content(chunk_size=65536):
                if not chunk:
                    continue
                self.wfile.write(chunk)
                self.wfile.flush()
        except requests.RequestException as exc:
            logger.exception("转发 probe 请求失败: %s", exc)
            self._write_error(502, f"upstream request failed: {exc}")
        except Exception as exc:  # pragma: no cover
            logger.exception("runtime tools proxy 未处理异常: %s", exc)
            self._write_error(500, f"proxy error: {exc}")

    def _read_body(self) -> bytes:
        content_length = int(self.headers.get("Content-Length", "0") or "0")
        if content_length <= 0:
            return b""
        return self.rfile.read(content_length)

    def _parse_json_body(self, body: bytes) -> Optional[Dict[str, Any]]:
        if not body:
            return None
        if "json" not in self.headers.get("Content-Type", "").lower():
            return None
        try:
            payload = json.loads(body.decode("utf-8"))
        except Exception:
            return None
        if not isinstance(payload, dict):
            return None
        return payload

    def _capture_tools_if_present(self, payload: Optional[Dict[str, Any]]) -> None:
        if not isinstance(payload, dict):
            return
        record = build_capture_record(self.command, self.path, payload)
        if record is not None:
            append_capture_record(self.output_file, record)

    def _write_capture_only_response(self, payload: Dict[str, Any]) -> None:
        response_payload = {
            "id": "chatcmpl-tools-probe",
            "object": "chat.completion",
            "created": int(datetime.now(timezone.utc).timestamp()),
            "model": payload.get("model") or "tools-probe-model",
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": "OK",
                    },
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
            },
        }
        body = json.dumps(response_payload, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _build_upstream_url(self) -> str:
        parts = urlsplit(self.path)
        base = self.upstream_base_url.rstrip("/")
        path = parts.path if parts.path.startswith("/") else f"/{parts.path}"
        query = f"?{parts.query}" if parts.query else ""
        return f"{base}{path}{query}"

    def _filtered_request_headers(self) -> Dict[str, str]:
        headers: Dict[str, str] = {}
        for key, value in self.headers.items():
            if key.lower() in _HOP_BY_HOP_HEADERS:
                continue
            headers[key] = value
        return headers

    def _write_error(self, status_code: int, message: str) -> None:
        payload = json.dumps({"error": message}, ensure_ascii=False).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


def run_proxy(listen_host: str, port: int, upstream_base_url: str, output_file: Path, request_timeout: float) -> None:
    handler = type(
        "ConfiguredRuntimeToolsProxyHandler",
        (RuntimeToolsProxyHandler,),
        {
            "upstream_base_url": upstream_base_url,
            "output_file": output_file,
            "request_timeout": request_timeout,
            "capture_only": False,
        },
    )
    server = ThreadingHTTPServer((listen_host, port), handler)
    logger.info("runtime probe proxy listening on http://%s:%s -> %s", listen_host, port, upstream_base_url)
    server.serve_forever()


def main() -> None:
    parser = argparse.ArgumentParser(description="Capture real OpenClaw runtime metadata for a probe request")
    parser.add_argument("--listen-host", default="127.0.0.1")
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--upstream-base-url", required=True)
    parser.add_argument("--output-file", required=True)
    parser.add_argument("--request-timeout", type=float, default=600.0)
    parser.add_argument("--capture-only", action="store_true")
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, str(args.log_level).upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    handler = type(
        "ConfiguredRuntimeToolsProxyHandler",
        (RuntimeToolsProxyHandler,),
        {
            "upstream_base_url": args.upstream_base_url,
            "output_file": Path(args.output_file),
            "request_timeout": args.request_timeout,
            "capture_only": args.capture_only,
        },
    )
    server = ThreadingHTTPServer((args.listen_host, args.port), handler)
    logger.info(
        "runtime probe proxy listening on http://%s:%s -> %s (capture_only=%s)",
        args.listen_host,
        args.port,
        args.upstream_base_url,
        args.capture_only,
    )
    server.serve_forever()


if __name__ == "__main__":
    main()