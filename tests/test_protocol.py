"""AC-1: the 2026-07-28 stateless protocol, against the mock and a real FastMCP server."""

from __future__ import annotations

import sys
from pathlib import Path

from conftest import DATA, Project, mock, read_log


def test_discover_meta_on_every_request(project: Project, tmp_path: Path) -> None:
    log = tmp_path / "requests.jsonl"
    project.write_mcp({"demo": mock("--log", str(log))})
    assert project.run("add", "demo", "release-notes", "--target", "claude", "--yes").code == 0
    entries = read_log(log)
    methods = [e["method"] for e in entries]
    assert methods[0] == "server/discover"
    assert "skills/list" in methods and "resources/read" in methods
    assert len(entries) >= 4
    assert [e for e in entries if not e["meta_ok"]] == []  # no request without _meta


def test_list_paginates_page_size_1(project: Project, tmp_path: Path) -> None:
    log = tmp_path / "requests.jsonl"
    project.write_mcp({"demo": mock("--page-size", "1", "--log", str(log))})
    res = project.run("list", "demo")
    assert res.code == 0
    assert "refunds" in res.out and "release-notes" in res.out
    assert [e["method"] for e in read_log(log)].count("skills/list") == 2


def test_fastmcp_interop_list_and_add(project: Project) -> None:
    server = {
        "command": sys.executable,
        "args": [str(Path(__file__).with_name("fastmcp_server.py")), str(DATA / "v1")],
    }
    project.write_mcp({"real": server})
    probe = project.run("servers", "--probe", "--timeout", "60")
    assert probe.code == 0 and "yes" in probe.out
    listed = project.run("list", "real", "--timeout", "60")
    assert listed.code == 0, listed.text
    assert "refunds" in listed.out and "release-notes" in listed.out
    for name, files in (("refunds", 1), ("release-notes", 2)):
        res = project.run("add", "real", name, "--target", "claude", "--yes", "--timeout", "60")
        assert res.code == 0, res.text
        folder = project.cwd / ".claude" / "skills" / name
        assert len([p for p in folder.rglob("*") if p.is_file() and p.name != ".mcp-origin.json"]) == files
    assert project.run("check", "--timeout", "60").code == 0


def test_legacy_server_exit5(project: Project) -> None:
    project.write_mcp({"old": mock("--legacy")})
    res = project.run("list", "old")
    assert res.code == 5
    assert "error[legacy-protocol]" in res.err
    assert "2026-07-28" in res.text
    assert "{" not in res.text and "}" not in res.text
