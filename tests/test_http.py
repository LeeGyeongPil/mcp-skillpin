"""AC-8: Streamable HTTP with strict headers, JSON and SSE replies."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from conftest import Project, read_log

from mcp_skillpin.transport import decode_header_value, header_value, parse_sse


def tree_hashes(root: Path) -> dict[str, str]:
    return {
        p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*"))
        if p.is_file() and p.name != ".mcp-origin.json"
    }


def test_json_and_sse_give_identical_installs(project: Project, http_server, tmp_path: Path) -> None:
    log = tmp_path / "http.jsonl"
    json_url = http_server("--log", str(log))
    sse_url = http_server("--sse")
    installs = {}
    for label, url in (("plain", json_url), ("sse", sse_url)):
        project.write_mcp({"web": {"type": "http", "url": url}})
        target = f"out-{label}"
        res = project.run("add", "web", "release-notes", "--target", target, "--yes",
                          "--lock", str(tmp_path / f"{label}.lock.json"))
        assert res.code == 0, res.text
        installs[label] = tree_hashes(project.cwd / target / "release-notes")
    assert installs["plain"] == installs["sse"]
    assert set(installs["plain"]) == {"SKILL.md", "templates/notes.md"}
    entries = read_log(log)
    assert entries and all(e["meta_ok"] for e in entries)
    assert not [e for e in entries if e.get("header_errors")]  # no required header was missing
    assert {"server/discover", "skills/list", "resources/read"} <= {e["method"] for e in entries}


def test_blob_files_survive_http(project: Project, http_server, tmp_path: Path) -> None:
    url = http_server(data="v2")
    project.write_mcp({"web": {"type": "http", "url": url}})
    res = project.run("add", "web", "release-notes", "--target", "claude", "--yes")
    assert res.code == 0, res.text
    script = project.cwd / ".claude/skills/release-notes/scripts/collect.sh"
    assert script.read_bytes().startswith(b"#!/bin/sh")


def test_non_ascii_uri_uses_base64_name_header(project: Project, http_server, tmp_path: Path) -> None:
    data = tmp_path / "data"
    (data / "kor" / "docs").mkdir(parents=True)
    (data / "kor" / "SKILL.md").write_text("---\nname: kor\ndescription: Korean file names.\n---\n\nHi\n")
    (data / "kor" / "docs" / "참고.md").write_text("한국어 문서\n", encoding="utf-8")
    url = http_server(data=str(data))  # the mock answers 400 if Mcp-Name is missing or not decodable
    project.write_mcp({"web": {"type": "http", "url": url}})
    res = project.run("add", "web", "kor", "--target", "claude", "--yes")
    assert res.code == 0, res.text
    assert (project.cwd / ".claude/skills/kor/docs/참고.md").read_text(encoding="utf-8") == "한국어 문서\n"


def test_unreachable_server_is_exit_5(project: Project) -> None:
    project.write_mcp({"web": {"type": "http", "url": "http://127.0.0.1:9/mcp"}})
    res = project.run("list", "web", "--timeout", "3")
    assert res.code == 5 and "error[connect-failed]" in res.err


def test_sse_transports_are_refused_with_hint(project: Project) -> None:
    project.write_mcp({"old": {"type": "sse", "url": "https://example.test/sse"}})
    res = project.run("list", "old")
    assert res.code == 5 and "error[unsupported-transport]" in res.err and "hint" in res.err


@pytest.mark.parametrize("plain", ["skill://a/SKILL.md", "x y"])
def test_header_value_plain_is_untouched(plain: str) -> None:
    assert header_value(plain) == plain


def test_header_value_roundtrip_for_non_ascii() -> None:
    uri = "skill://kor/docs/참고.md"
    encoded = header_value(uri)
    assert encoded.startswith("=?base64?") and encoded.endswith("?=") and encoded.isascii()
    assert decode_header_value(encoded) == uri


def test_parse_sse_skips_priming_and_other_ids() -> None:
    body = (
        ": comment\n\nid: 1\ndata:\n\n"
        'event: message\ndata: {"jsonrpc":"2.0","id":9,"result":{"x":1}}\n\n'
        'event: message\ndata: {"jsonrpc":"2.0","id":2,\ndata: "result":{"ok":true}}\n\n'
    )
    assert parse_sse(body, 2) == {"jsonrpc": "2.0", "id": 2, "result": {"ok": True}}
    assert parse_sse(body, 3) is None
