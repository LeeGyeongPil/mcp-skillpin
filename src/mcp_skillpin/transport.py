"""JSON-RPC transports: stdio (newline-delimited) and Streamable HTTP (JSON or SSE replies)."""

from __future__ import annotations

import base64
import json
import queue
import subprocess
import threading
import urllib.error
import urllib.request
from collections.abc import Mapping
from typing import Any, Protocol

from mcp_skillpin.errors import SkillpinError

PROTOCOL_VERSION = "2026-07-28"


class Transport(Protocol):
    def request(self, message: dict[str, Any]) -> dict[str, Any]: ...
    def close(self) -> None: ...


class StdioTransport:
    """Runs the server command and talks newline-delimited JSON-RPC over its pipes."""

    def __init__(
        self,
        command: str,
        args: list[str],
        env: Mapping[str, str],
        cwd: str | None,
        timeout: float,
    ) -> None:
        self._timeout = timeout
        try:
            self._proc = subprocess.Popen(
                [command, *args],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                env=dict(env),
                cwd=cwd or None,
                text=True,
                encoding="utf-8",
                bufsize=1,
            )
        except (OSError, ValueError) as exc:
            raise SkillpinError("connect-failed", detail=f"cannot start {command!r}: {exc}") from exc
        self._lines: queue.Queue[str | None] = queue.Queue()
        self._reader = threading.Thread(target=self._pump, daemon=True)
        self._reader.start()

    def _pump(self) -> None:
        assert self._proc.stdout is not None
        try:
            for line in self._proc.stdout:
                self._lines.put(line)
        except (OSError, ValueError):
            pass
        self._lines.put(None)

    def request(self, message: dict[str, Any]) -> dict[str, Any]:
        assert self._proc.stdin is not None
        try:
            self._proc.stdin.write(json.dumps(message) + "\n")
            self._proc.stdin.flush()
        except (OSError, ValueError) as exc:
            raise SkillpinError("connect-failed", detail=f"server closed its input: {exc}") from exc
        while True:
            try:
                line = self._lines.get(timeout=self._timeout)
            except queue.Empty:
                raise SkillpinError("timeout", seconds=self._timeout) from None
            if line is None:
                raise SkillpinError("connect-failed", detail="server exited before replying")
            line = line.strip()
            if not line:
                continue
            try:
                reply = json.loads(line)
            except json.JSONDecodeError:
                continue  # stray log line on stdout
            if isinstance(reply, dict) and reply.get("id") == message.get("id"):
                return reply

    def close(self) -> None:
        proc = self._proc
        try:
            if proc.stdin:
                proc.stdin.close()
        except OSError:
            pass
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=3)
        if proc.stdout:
            proc.stdout.close()
        self._reader.join(timeout=2)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: Any, **kwargs: Any) -> None:  # type: ignore[override]
        return None


def header_value(value: str) -> str:
    """Encode a header value with the base64 sentinel when it is not plain ASCII."""
    plain = value.isascii() and value.isprintable() and value == value.strip()
    if plain:
        return value
    return "=?base64?" + base64.b64encode(value.encode("utf-8")).decode("ascii") + "?="


def decode_header_value(value: str) -> str:
    if value.startswith("=?base64?") and value.endswith("?="):
        return base64.b64decode(value[len("=?base64?") : -2]).decode("utf-8")
    return value


def parse_sse(body: str, want_id: Any) -> dict[str, Any] | None:
    """Return the JSON-RPC reply with ``want_id`` from a text/event-stream body."""
    for block in body.replace("\r\n", "\n").split("\n\n"):
        data_lines = [ln[5:].lstrip(" ") for ln in block.split("\n") if ln.startswith("data:")]
        if not data_lines:
            continue
        try:
            payload = json.loads("\n".join(data_lines))
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict) and payload.get("id") == want_id and (
            "result" in payload or "error" in payload
        ):
            return payload
    return None


class HttpTransport:
    """Streamable HTTP client: one POST per request, JSON or SSE response."""

    def __init__(self, url: str, headers: Mapping[str, str], timeout: float) -> None:
        if not url.startswith(("http://", "https://")):
            raise SkillpinError("bad-config", detail=f"unsupported URL scheme in {url!r}")
        self._url = url
        self._headers = dict(headers)
        self._timeout = timeout
        self._opener = urllib.request.build_opener(_NoRedirect)

    def request(self, message: dict[str, Any]) -> dict[str, Any]:
        method = str(message.get("method", ""))
        headers = {
            **self._headers,
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "MCP-Protocol-Version": PROTOCOL_VERSION,
            "Mcp-Method": method,
        }
        if method == "resources/read":
            headers["Mcp-Name"] = header_value(str(message["params"]["uri"]))
        req = urllib.request.Request(
            self._url, data=json.dumps(message).encode("utf-8"), headers=headers, method="POST"
        )
        try:
            with self._opener.open(req, timeout=self._timeout) as resp:
                ctype = resp.headers.get("Content-Type", "")
                body = resp.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", "replace")
            if exc.code in (401, 403):
                raise SkillpinError(
                    "connect-failed", detail=f"HTTP {exc.code}: the server rejected the credentials in your MCP config"
                ) from exc
            try:
                reply = json.loads(body)
            except json.JSONDecodeError:
                reply = None
            if isinstance(reply, dict) and "error" in reply:
                return reply
            raise SkillpinError("connect-failed", detail=f"HTTP {exc.code} from server") from exc
        except TimeoutError as exc:
            raise SkillpinError("timeout", seconds=self._timeout) from exc
        except (urllib.error.URLError, OSError) as exc:
            raise SkillpinError("connect-failed", detail=str(getattr(exc, "reason", exc))) from exc
        if "text/event-stream" in ctype:
            reply = parse_sse(body, message.get("id"))
            if reply is None:
                raise SkillpinError("protocol-error", detail="SSE stream had no reply for the request")
            return reply
        try:
            reply = json.loads(body)
        except json.JSONDecodeError as exc:
            raise SkillpinError("protocol-error", detail="response is not JSON") from exc
        if not isinstance(reply, dict):
            raise SkillpinError("protocol-error", detail="response is not a JSON object")
        return reply

    def close(self) -> None:
        return None


def build_environment(base: Mapping[str, str], extra: Mapping[str, str]) -> dict[str, str]:
    env = {k: v for k, v in base.items() if isinstance(v, str)}
    env.update(extra)
    return env

