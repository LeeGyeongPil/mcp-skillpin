"""AC-2: finding servers in Claude Code and Codex config files, without leaking secrets."""

from __future__ import annotations

import json
from pathlib import Path

from conftest import Project, mock

from mcp_skillpin.config import discover_servers, expand

SECRET_HEADER = "Bearer SECRET-HEADER-123"
SECRET_ENV = "SECRET-ENV-456"
SECRET_BEARER = "SECRET-BEARER-789"


def write_claude_json(project: Project, data: dict) -> None:
    (project.home / ".claude.json").write_text(json.dumps(data), encoding="utf-8")


def labels(project: Project) -> dict[str, dict]:
    res = project.run("servers", "--json")
    assert res.code == 0, res.text
    return {s["label"]: s for s in json.loads(res.out)}


def test_expand_variables() -> None:
    env = {"A": "alpha", "EMPTY": ""}
    assert expand("${A}/x", env) == "alpha/x"
    assert expand("${MISSING:-dflt}", env) == "dflt"
    assert expand("${EMPTY:-dflt}", env) == "dflt"
    assert expand("${MISSING}", env) == "${MISSING}"
    assert expand("no vars", env) == "no vars"


def test_claude_precedence_local_over_project_over_user(project: Project) -> None:
    cwd = str(project.cwd.resolve())
    write_claude_json(project, {
        "mcpServers": {"s": {"command": "user-cmd"}, "only-user": {"command": "u"}},
        "projects": {cwd: {"mcpServers": {"s": {"command": "local-cmd"}}}},
    })
    project.write_mcp({"s": {"command": "project-cmd", "env": {"X": "1"}}, "only-project": {"command": "p"}})
    found = {c.name: c for c in discover_servers(project.cwd.resolve(), project.home, project.env)}
    assert found["s"].command == "local-cmd" and found["s"].scope == "local"
    assert found["s"].env == {}  # fields are not merged
    assert found["only-user"].scope == "user" and found["only-project"].scope == "project"
    # without the local entry, project beats user
    write_claude_json(project, {"mcpServers": {"s": {"command": "user-cmd"}}})
    found = {c.name: c for c in discover_servers(project.cwd.resolve(), project.home, project.env)}
    assert found["s"].command == "project-cmd" and found["s"].scope == "project"


def test_variable_expansion_in_servers(project: Project) -> None:
    project.write_mcp({
        "v": {"command": "${BIN:-fallback-bin}", "args": ["--root", "${ROOT}"], "env": {"K": "${KEY:-k0}"}},
        "h": {"type": "http", "url": "https://${HOST:-example.test}/mcp"},
    })
    found = {c.name: c for c in discover_servers(project.cwd, project.home, {**project.env, "ROOT": "/r"})}
    assert found["v"].command == "fallback-bin" and found["v"].args == ["--root", "/r"]
    assert found["v"].env == {"K": "k0"}
    assert found["h"].url == "https://example.test/mcp" and found["h"].transport == "http"


def test_codex_servers_and_trust_note(project: Project) -> None:
    (project.home / ".codex").mkdir()
    (project.home / ".codex" / "config.toml").write_text(
        '[mcp_servers.user-one]\ncommand = "npx"\nargs = ["-y", "x"]\n'
        '[mcp_servers.remote]\nurl = "https://example.test/mcp"\nbearer_token_env_var = "REMOTE_TOKEN"\n'
        '[mcp_servers.off]\ncommand = "x"\nenabled = false\n'
    )
    (project.cwd / ".codex").mkdir()
    (project.cwd / ".codex" / "config.toml").write_text('[mcp_servers.proj]\ncommand = "node"\n')
    found = labels(project)
    assert {"user-one", "remote", "proj", "demo"} <= set(found) and "off" not in found
    assert found["proj"]["agent"] == "codex" and found["proj"]["scope"] == "project"
    res = project.run("servers")
    assert "Codex trust required" in res.out


def test_same_name_in_both_agents_gets_prefixed_labels(project: Project) -> None:
    project.write_mcp({"shared": mock()})
    (project.home / ".codex").mkdir()
    (project.home / ".codex" / "config.toml").write_text('[mcp_servers.shared]\ncommand = "codex-cmd"\n')
    found = labels(project)
    assert "claude:shared" in found and "codex:shared" in found and "shared" not in found
    res = project.run("list", "shared")
    assert res.code == 2 and "error[server-ambiguous]" in res.err and "claude:shared" in res.err
    assert project.run("list", "claude:shared").code == 0


def test_no_servers_message(tmp_path: Path) -> None:
    empty = Project(tmp_path)
    res = empty.run("servers")
    assert res.code == 2 and "error[no-servers]" in res.err


def test_broken_config_is_reported(project: Project) -> None:
    (project.cwd / ".mcp.json").write_text("{not json")
    res = project.run("servers")
    assert res.code == 2 and "error[bad-config]" in res.err


def test_secrets_never_printed_or_locked(project: Project, http_server) -> None:
    url = http_server("--require-bearer", SECRET_BEARER)
    (project.home / ".codex").mkdir()
    (project.home / ".codex" / "config.toml").write_text(
        f'[mcp_servers.remote]\nurl = "{url}"\nbearer_token_env_var = "REMOTE_TOKEN"\n'
    )
    stdio = mock()
    stdio["env"] = {"API_KEY": SECRET_ENV}
    project.write_mcp({
        "withenv": stdio,
        "hdr": {"type": "http", "url": "https://example.test/mcp?token=QUERYSECRET",
                "headers": {"Authorization": SECRET_HEADER}},
    })
    env = {"REMOTE_TOKEN": SECRET_BEARER}
    secrets = (SECRET_HEADER, SECRET_ENV, SECRET_BEARER, "QUERYSECRET", "SECRET-HEADER")
    outputs = [
        project.run("servers", env=env),
        project.run("servers", "--json", env=env),
        project.run("list", "remote", env=env),  # the bearer token really is sent: server demands it
        project.run("add", "remote", "refunds", "--target", "claude", "--yes", env=env),
        project.run("add", "withenv", "release-notes", "--target", "claude", "--yes", env=env),
    ]
    assert [o.code for o in outputs] == [0, 0, 0, 0, 0], [o.text for o in outputs]
    blob = "".join(o.text for o in outputs) + project.lock_path.read_text()
    blob += "".join(p.read_text() for p in (project.cwd / ".claude").rglob("*") if p.is_file())
    for secret in secrets:
        assert secret not in blob
    no_token = project.run("list", "remote")  # without REMOTE_TOKEN the server answers 401
    assert no_token.code == 5 and "error[connect-failed]" in no_token.err
