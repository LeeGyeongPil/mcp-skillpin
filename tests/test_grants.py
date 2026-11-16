"""AC-6: allowed-tools / hooks need an explicit --grant, and a changed value asks again."""

from __future__ import annotations

from pathlib import Path

from conftest import Project, mock


def make_skill(root: Path, extra_frontmatter: str) -> Path:
    folder = root / "powerful"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "SKILL.md").write_text(
        f"---\nname: powerful\ndescription: A skill that wants more power.\n{extra_frontmatter}---\n\nBody.\n"
    )
    return root


def point_at(project: Project, root: Path) -> None:
    project.write_mcp({"srv": mock(data=str(root))})


def test_allowed_tools_needs_grant(project: Project, tmp_path: Path) -> None:
    root = make_skill(tmp_path / "data", "allowed-tools: Bash(git status)\n")
    point_at(project, root)
    dry = project.run("add", "srv", "powerful", "--target", "claude")
    assert dry.code == 0 and "--grant allowed-tools" in dry.out and "RISK" in dry.out
    res = project.run("add", "srv", "powerful", "--target", "claude", "--yes")
    assert res.code == 4 and "error[grant-required]" in res.err
    assert not (project.cwd / ".claude").exists()
    ok = project.run("add", "srv", "powerful", "--target", "claude", "--yes", "--grant", "allowed-tools")
    assert ok.code == 0, ok.text
    grants = project.lock()["skills"]["srv/powerful"]["grants"]
    assert list(grants) == ["allowed-tools"] and "git status" in grants["allowed-tools"]


def test_changed_value_asks_again(project: Project, tmp_path: Path) -> None:
    root = make_skill(tmp_path / "data", "allowed-tools: Bash(git status)\n")
    point_at(project, root)
    assert project.run("add", "srv", "powerful", "--target", "claude", "--yes",
                       "--grant", "allowed-tools").code == 0
    make_skill(root, "allowed-tools: Bash(rm -rf /tmp/x)\n")  # server widens the permission
    res = project.run("update", "powerful", "--yes")
    assert res.code == 4 and "error[grant-required]" in res.err
    assert "git status" in (project.cwd / ".claude/skills/powerful/SKILL.md").read_text()
    ok = project.run("update", "powerful", "--yes", "--grant", "allowed-tools")
    assert ok.code == 0, ok.text
    assert "rm -rf" in project.lock()["skills"]["srv/powerful"]["grants"]["allowed-tools"]


def test_body_only_change_keeps_existing_grant(project: Project, tmp_path: Path) -> None:
    root = make_skill(tmp_path / "data", "allowed-tools: Bash(git status)\n")
    point_at(project, root)
    project.run("add", "srv", "powerful", "--target", "claude", "--yes", "--grant", "allowed-tools")
    skill = root / "powerful" / "SKILL.md"
    skill.write_text(skill.read_text() + "More words.\n")
    res = project.run("update", "powerful", "--yes")
    assert res.code == 0, res.text


def test_hooks_need_grant_too(project: Project, tmp_path: Path) -> None:
    root = make_skill(tmp_path / "data", "hooks:\n  PreToolUse: echo hi\n")
    point_at(project, root)
    res = project.run("add", "srv", "powerful", "--target", "claude", "--yes")
    assert res.code == 4 and "hooks" in res.err
    ok = project.run("add", "srv", "powerful", "--target", "claude", "--yes", "--grant", "hooks")
    assert ok.code == 0
    assert "hooks" in project.lock()["skills"]["srv/powerful"]["grants"]


def test_granting_unneeded_key_is_harmless(project: Project) -> None:
    res = project.run("add", "demo", "refunds", "--target", "claude", "--yes", "--grant", "hooks")
    assert res.code == 0
    assert project.lock()["skills"]["demo/refunds"]["grants"] == {}
