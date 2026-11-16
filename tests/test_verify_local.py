"""AC-7: `verify` re-hashes installed folders against the lock file."""

from __future__ import annotations

from conftest import Project


def test_verify_clean_then_tampered(project: Project) -> None:
    project.run("add", "demo", "release-notes", "--target", "claude", "--yes")
    res = project.run("verify")
    assert res.code == 0 and "release-notes ok" in res.out  # .mcp-origin.json is not compared
    skill_md = project.cwd / ".claude" / "skills" / "release-notes" / "SKILL.md"
    data = bytearray(skill_md.read_bytes())
    data[-2] ^= 0x01  # flip one bit
    skill_md.write_bytes(bytes(data))
    res = project.run("verify")
    assert res.code == 1
    assert "MODIFIED" in res.out and "SKILL.md" in res.out


def test_verify_reports_missing_and_extra(project: Project) -> None:
    project.run("add", "demo", "release-notes", "--target", "claude", "--yes")
    folder = project.cwd / ".claude" / "skills" / "release-notes"
    (folder / "templates" / "notes.md").unlink()
    (folder / "scripts").mkdir()
    (folder / "scripts" / "evil.sh").write_text("echo hi")
    res = project.run("verify")
    assert res.code == 1
    assert "missing: templates/notes.md" in res.out
    assert "extra: scripts/evil.sh" in res.out


def test_verify_ignores_origin_file(project: Project) -> None:
    project.run("add", "demo", "refunds", "--target", "claude", "--yes")
    origin = project.cwd / ".claude" / "skills" / "refunds" / ".mcp-origin.json"
    origin.write_text("{}")
    assert project.run("verify").code == 0


def test_verify_without_lock(project: Project) -> None:
    res = project.run("verify")
    assert res.code == 2 and "error[no-lock]" in res.err
