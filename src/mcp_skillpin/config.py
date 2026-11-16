"""Find MCP servers in Claude Code and Codex configuration files."""

from __future__ import annotations

import json
import re
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from mcp_skillpin.errors import SkillpinError
from mcp_skillpin.i18n import t

_VAR = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")


def expand(value: str, environ: Mapping[str, str]) -> str:
    """Expand ``${VAR}`` and ``${VAR:-default}``. Unset variables without a default stay as written."""

    def repl(match: re.Match[str]) -> str:
        name, default = match.group(1), match.group(2)
        found = environ.get(name)
        if found:
            return found
        if default is not None:
            return default
        return match.group(0)

    return _VAR.sub(repl, value)


@dataclass
class ServerConfig:
    label: str
    name: str
    agent: str  # "claude" | "codex"
    scope: str  # "user" | "project" | "local"
    source: str  # config file path (as a string)
    transport: str  # "stdio" | "http" | other unsupported kinds
    command: str | None = None
    args: list[str] = field(default_factory=list, repr=False)
    env: dict[str, str] = field(default_factory=dict, repr=False)
    cwd: str | None = None
    url: str | None = field(default=None, repr=False)
    headers: dict[str, str] = field(default_factory=dict, repr=False)  # may hold secrets
    note: str | None = None

    def endpoint(self) -> str:
        """Display form without arguments, query strings or credentials."""
        if self.transport == "stdio":
            extra = t("endpoint.args", count=len(self.args)) if self.args else ""
            return f"{self.command}{extra}"
        if self.url:
            parts = urlsplit(self.url)
            host = parts.hostname or ""
            if parts.port:
                host += f":{parts.port}"
            return urlunsplit((parts.scheme, host, parts.path, "", ""))
        return ""


def _read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, json.JSONDecodeError) as exc:
        raise SkillpinError("bad-config", path=str(path), detail=str(exc)) from exc
    if not isinstance(data, dict):
        raise SkillpinError("bad-config", path=str(path), detail="top level is not an object")
    return data


def _read_toml(path: Path) -> dict[str, Any]:
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise SkillpinError("bad-config", path=str(path), detail=str(exc)) from exc


def _str_map(raw: Any, environ: Mapping[str, str]) -> dict[str, str]:
    if not isinstance(raw, dict):
        return {}
    return {str(k): expand(str(v), environ) for k, v in raw.items()}


def _claude_server(
    name: str, raw: Any, scope: str, source: Path, environ: Mapping[str, str]
) -> ServerConfig | None:
    if not isinstance(raw, dict):
        return None
    kind = raw.get("type") or ("stdio" if "command" in raw else "http" if "url" in raw else "")
    if kind == "streamable-http":
        kind = "http"
    cfg = ServerConfig(
        label=name, name=name, agent="claude", scope=scope, source=str(source), transport=str(kind)
    )
    if kind == "stdio":
        if not isinstance(raw.get("command"), str):
            return None
        cfg.command = expand(raw["command"], environ)
        cfg.args = [expand(str(a), environ) for a in raw.get("args", [])]
        cfg.env = _str_map(raw.get("env"), environ)
        cfg.cwd = expand(raw["cwd"], environ) if isinstance(raw.get("cwd"), str) else None
    elif isinstance(raw.get("url"), str):
        cfg.url = expand(raw["url"], environ)
        cfg.headers = _str_map(raw.get("headers"), environ)
    else:
        return None
    return cfg


def _codex_server(
    name: str, raw: Any, scope: str, source: Path, environ: Mapping[str, str]
) -> ServerConfig | None:
    if not isinstance(raw, dict) or raw.get("enabled") is False:
        return None
    note = "project, Codex trust required" if scope == "project" else None
    if isinstance(raw.get("command"), str):
        return ServerConfig(
            label=name, name=name, agent="codex", scope=scope, source=str(source),
            transport="stdio", command=raw["command"],
            args=[str(a) for a in raw.get("args", [])],
            env=_str_map(raw.get("env"), environ),
            cwd=raw["cwd"] if isinstance(raw.get("cwd"), str) else None,
            note=note,
        )
    if isinstance(raw.get("url"), str):
        headers = _str_map(raw.get("http_headers"), environ)
        env_headers = raw.get("env_http_headers")
        if isinstance(env_headers, dict):
            for header, var in env_headers.items():
                if environ.get(str(var)):
                    headers[str(header)] = environ[str(var)]
        token_var = raw.get("bearer_token_env_var")
        if isinstance(token_var, str) and environ.get(token_var):
            headers["Authorization"] = f"Bearer {environ[token_var]}"
        return ServerConfig(
            label=name, name=name, agent="codex", scope=scope, source=str(source),
            transport="http", url=raw["url"], headers=headers, note=note,
        )
    return None


def discover_servers(cwd: Path, home: Path, environ: Mapping[str, str]) -> list[ServerConfig]:
    """Return every configured server. Names that exist for both agents get ``agent:`` labels."""
    claude: dict[str, ServerConfig] = {}
    claude_json = home / ".claude.json"
    data = _read_json(claude_json)
    # Precedence local > project > user: apply user first, then override. Fields are not merged.
    layers: list[tuple[str, Path, Any]] = [("user", claude_json, data.get("mcpServers"))]
    mcp_json = cwd / ".mcp.json"
    layers.append(("project", mcp_json, _read_json(mcp_json).get("mcpServers")))
    projects = data.get("projects")
    if isinstance(projects, dict):
        entry = projects.get(str(cwd)) or projects.get(str(cwd.resolve()))
        if isinstance(entry, dict):
            layers.append(("local", claude_json, entry.get("mcpServers")))
    for scope, path, servers in layers:
        if not isinstance(servers, dict):
            continue
        for name, raw in servers.items():
            cfg = _claude_server(str(name), raw, scope, path, environ)
            if cfg is not None:
                claude[str(name)] = cfg

    codex: dict[str, ServerConfig] = {}
    for scope, path in (("user", home / ".codex" / "config.toml"), ("project", cwd / ".codex" / "config.toml")):
        table = _read_toml(path).get("mcp_servers")
        if not isinstance(table, dict):
            continue
        for name, raw in table.items():
            cfg = _codex_server(str(name), raw, scope, path, environ)
            if cfg is not None:
                codex[str(name)] = cfg

    both = set(claude) & set(codex)
    result: list[ServerConfig] = []
    for name, cfg in sorted(claude.items()):
        if name in both:
            cfg.label = f"claude:{name}"
        result.append(cfg)
    for name, cfg in sorted(codex.items()):
        if name in both:
            cfg.label = f"codex:{name}"
        result.append(cfg)
    return result


def find_server(servers: list[ServerConfig], ident: str) -> ServerConfig:
    for cfg in servers:
        if cfg.label == ident:
            return cfg
    variants = [c.label for c in servers if c.name == ident]
    if variants:
        raise SkillpinError("server-ambiguous", name=ident, labels=", ".join(variants))
    known = ", ".join(c.label for c in servers) or "-"
    raise SkillpinError("server-not-found", name=ident, known=known)
