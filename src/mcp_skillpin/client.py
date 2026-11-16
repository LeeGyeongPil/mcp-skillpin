"""Skills Extension client for the stateless 2026-07-28 MCP protocol."""

from __future__ import annotations

import base64
import binascii
from typing import Any

from mcp_skillpin import __version__
from mcp_skillpin.errors import SkillpinError
from mcp_skillpin.transport import PROTOCOL_VERSION, Transport

EXT_ID = "io.modelcontextprotocol/skills"
_META_PREFIX = "io.modelcontextprotocol/"
METHOD_NOT_FOUND = -32601


class SkillsClient:
    def __init__(self, transport: Transport) -> None:
        self._transport = transport
        self._next_id = 0
        self.discovered: dict[str, Any] = {}

    # -- plumbing ---------------------------------------------------------
    def _meta(self) -> dict[str, Any]:
        return {
            _META_PREFIX + "protocolVersion": PROTOCOL_VERSION,
            _META_PREFIX + "clientInfo": {"name": "mcp-skillpin", "version": __version__},
            _META_PREFIX + "clientCapabilities": {"extensions": {EXT_ID: {}}},
        }

    def call(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """Send one request (always carrying ``_meta``) and return its ``result``."""
        self._next_id += 1
        body = dict(params or {})
        body["_meta"] = self._meta()
        reply = self._transport.request(
            {"jsonrpc": "2.0", "id": self._next_id, "method": method, "params": body}
        )
        if "error" in reply:
            err = reply["error"] if isinstance(reply["error"], dict) else {}
            code, message = err.get("code"), err.get("message", "")
            exc = SkillpinError("rpc-error", method=method, rpc_code=code, text=message)
            exc.rpc_code = code  # type: ignore[attr-defined]
            raise exc
        result = reply.get("result")
        if not isinstance(result, dict):
            raise SkillpinError("protocol-error", detail=f"{method} returned no result object")
        return result

    # -- protocol ---------------------------------------------------------
    def discover(self) -> dict[str, Any]:
        try:
            result = self.call("server/discover")
        except SkillpinError as exc:
            if getattr(exc, "rpc_code", None) == METHOD_NOT_FOUND:
                raise SkillpinError("legacy-protocol", version=PROTOCOL_VERSION) from exc
            raise
        versions = result.get("supportedVersions")
        if isinstance(versions, list) and PROTOCOL_VERSION not in versions:
            raise SkillpinError("legacy-protocol", version=PROTOCOL_VERSION)
        caps = result.get("capabilities")
        extensions = caps.get("extensions") if isinstance(caps, dict) else None
        if not isinstance(extensions, dict) or EXT_ID not in extensions:
            raise SkillpinError("no-skills-extension", ext=EXT_ID)
        self.discovered = result
        return result

    def list_skills(self) -> list[dict[str, Any]]:
        entries: list[dict[str, Any]] = []
        cursor: str | None = None
        seen: set[str] = set()
        while True:
            params: dict[str, Any] = {"cursor": cursor} if cursor else {}
            result = self.call("skills/list", params)
            skills = result.get("skills")
            if not isinstance(skills, list):
                raise SkillpinError("protocol-error", detail="skills/list result has no skills array")
            entries.extend(skills)
            cursor = result.get("nextCursor")
            if not cursor:
                return entries
            if not isinstance(cursor, str) or cursor in seen:
                raise SkillpinError("protocol-error", detail="skills/list cursor did not advance")
            seen.add(cursor)

    def get_skill(self, uri: str) -> dict[str, Any]:
        result = self.call("skills/get", {"uri": uri})
        skill = result.get("skill")
        if not isinstance(skill, dict):
            raise SkillpinError("protocol-error", detail="skills/get result has no skill object")
        return skill

    def read_resource(self, uri: str) -> list[tuple[str | None, bytes]]:
        """Read a resource; returns (uri, raw bytes) for each content item."""
        result = self.call("resources/read", {"uri": uri})
        contents = result.get("contents")
        if not isinstance(contents, list) or not contents:
            raise SkillpinError("protocol-error", detail=f"resources/read {uri} returned no contents")
        out: list[tuple[str | None, bytes]] = []
        for item in contents:
            if not isinstance(item, dict):
                raise SkillpinError("protocol-error", detail="content item is not an object")
            if isinstance(item.get("text"), str):
                data = item["text"].encode("utf-8")
            elif isinstance(item.get("blob"), str):
                try:
                    data = base64.b64decode(item["blob"], validate=True)
                except (binascii.Error, ValueError) as exc:
                    raise SkillpinError("protocol-error", detail=f"bad base64 blob for {uri}") from exc
            else:
                raise SkillpinError("protocol-error", detail=f"content item for {uri} has no text/blob")
            out.append((item.get("uri"), data))
        return out

    def close(self) -> None:
        self._transport.close()
