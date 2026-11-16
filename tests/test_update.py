"""AC-5: silent server-side changes are caught and need re-approval."""

from __future__ import annotations

from conftest import Project, mock


def install_v1(project: Project) -> None:
    for name in ("release-notes", "refunds"):
        assert project.run("add", "demo", name, "--target", "claude", "--yes").code == 0


def switch_to_v2(project: Project) -> None:
    project.write_mcp({"demo": mock(data="v2")})


def test_check_clean_then_changed(project: Project) -> None:
    install_v1(project)
    clean = project.run("check")
    assert clean.code == 0 and "release-notes unchanged" in clean.out
    switch_to_v2(project)
    res = project.run("check")
    assert res.code == 1
    assert "release-notes CHANGED" in res.out
    assert "added: scripts/collect.sh" in res.out
    assert "changed: SKILL.md" in res.out
    assert "refunds unchanged" in res.out


def test_check_json_output(project: Project) -> None:
    import json

    install_v1(project)
    switch_to_v2(project)
    res = project.run("check", "--json")
    data = json.loads(res.out)
    by_name = {d["skill"]: d for d in data}
    assert by_name["release-notes"]["status"] == "changed"
    assert by_name["release-notes"]["added"] == ["scripts/collect.sh"]
    assert by_name["refunds"]["status"] == "unchanged"


def test_update_without_yes_shows_diff_and_changes_nothing(project: Project) -> None:
    install_v1(project)
    switch_to_v2(project)
    before_lock = project.lock_path.read_bytes()
    folder = project.cwd / ".claude" / "skills" / "release-notes"
    before_md = (folder / "SKILL.md").read_bytes()
    res = project.run("update", "release-notes")
    assert res.code == 0
    assert "+Before writing, run" in res.out
    assert "RISK: new script file scripts/collect.sh" in res.out
    assert "Dry run" in res.out
    assert project.lock_path.read_bytes() == before_lock
    assert (folder / "SKILL.md").read_bytes() == before_md
    assert not (folder / "scripts").exists()


def test_update_yes_repins(project: Project) -> None:
    install_v1(project)
    old_digest = project.lock()["skills"]["demo/release-notes"]["setDigest"]
    switch_to_v2(project)
    res = project.run("update", "release-notes", "--yes")
    assert res.code == 0, res.text
    rec = project.lock()["skills"]["demo/release-notes"]
    assert rec["setDigest"] != old_digest
    assert "scripts/collect.sh" in rec["files"]
    folder = project.cwd / ".claude" / "skills" / "release-notes"
    assert (folder / "scripts" / "collect.sh").is_file()
    assert not ((folder / "scripts" / "collect.sh").stat().st_mode & 0o111)
    assert project.run("check").code == 0
    assert project.run("verify").code == 0


def test_check_reports_skill_the_server_dropped(project: Project, tmp_path) -> None:
    install_v1(project)
    empty = tmp_path / "empty"
    empty.mkdir()
    project.write_mcp({"demo": mock(data=str(empty))})
    res = project.run("check")
    assert res.code == 1 and "MISSING" in res.out


def test_check_without_lock_gives_hint(project: Project) -> None:
    res = project.run("check")
    assert res.code == 2 and "error[no-lock]" in res.err and "hint" in res.err


def test_update_unknown_name(project: Project) -> None:
    install_v1(project)
    res = project.run("update", "ghost")
    assert res.code == 2 and "error[skill-not-found]" in res.err


def test_update_warns_and_refuses_to_discard_local_edits(project: Project) -> None:
    assert project.run("add", "demo", "release-notes", "--target", "claude", "--yes").code == 0
    skill_md = project.cwd / ".claude" / "skills" / "release-notes" / "SKILL.md"
    skill_md.write_text(skill_md.read_text() + "my note\n")
    project.write_mcp({"demo": mock(data="v2")})
    dry = project.run("update", "release-notes")
    assert dry.code == 0 and "edited locally" in dry.out and "SKILL.md" in dry.out
    res = project.run("update", "release-notes", "--yes")
    assert res.code == 2 and "error[local-edits]" in res.err
    assert "my note" in skill_md.read_text()
    ok = project.run("update", "release-notes", "--yes", "--discard-local")
    assert ok.code == 0, ok.text
    assert "my note" not in skill_md.read_text()


def test_add_yes_refuses_to_overwrite_local_edits(project: Project) -> None:
    import json

    assert project.run("add", "demo", "release-notes", "--target", "claude", "--yes").code == 0
    skill_md = project.cwd / ".claude" / "skills" / "release-notes" / "SKILL.md"
    skill_md.write_text(skill_md.read_text() + "my note\n")
    lock_before = project.lock_path.read_bytes()
    dry = project.run("add", "demo", "release-notes", "--target", "claude", "--json")
    assert dry.code == 0 and json.loads(dry.out)["localEdits"] == ["SKILL.md"]
    plain = project.run("add", "demo", "release-notes", "--target", "claude")
    assert plain.code == 0 and "edited locally" in plain.out and "SKILL.md" in plain.out
    res = project.run("add", "demo", "release-notes", "--target", "claude", "--yes")
    assert res.code == 2 and "error[local-edits]" in res.err
    assert "my note" in skill_md.read_text()
    assert project.lock_path.read_bytes() == lock_before
    ok = project.run("add", "demo", "release-notes", "--target", "claude", "--yes", "--discard-local")
    assert ok.code == 0, ok.text
    assert "my note" not in skill_md.read_text()
    assert project.run("verify").code == 0


def test_add_yes_reinstall_without_local_edits_unchanged(project: Project) -> None:
    assert project.run("add", "demo", "release-notes", "--target", "claude", "--yes").code == 0
    again = project.run("add", "demo", "release-notes", "--target", "claude", "--yes")
    assert again.code == 0 and "edited locally" not in again.out
