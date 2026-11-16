"""AC-4: dry run by default, verified bytes on --yes, stable lock file, target folders, collisions."""

from __future__ import annotations

import hashlib
import json
import os
import stat

from conftest import DATA, Project


def sha(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def test_dry_run_writes_nothing(project: Project) -> None:
    res = project.run("add", "demo", "release-notes", "--target", "claude")
    assert res.code == 0
    assert "SKILL.md" in res.out and "templates/notes.md" in res.out
    assert "Dry run" in res.out
    assert not (project.cwd / ".claude").exists()
    assert not project.lock_path.exists()


def test_yes_installs_exact_bytes_and_locks(project: Project) -> None:
    res = project.run("add", "demo", "release-notes", "--target", "claude", "--yes")
    assert res.code == 0, res.text
    folder = project.cwd / ".claude" / "skills" / "release-notes"
    for rel in ("SKILL.md", "templates/notes.md"):
        assert (folder / rel).read_bytes() == (DATA / "v1" / "release-notes" / rel).read_bytes()
    lock = project.lock()
    rec = lock["skills"]["demo/release-notes"]
    assert rec["files"]["SKILL.md"]["digest"] == sha((folder / "SKILL.md").read_bytes())
    assert rec["target"] == ".claude/skills/release-notes"
    origin = json.loads((folder / ".mcp-origin.json").read_text())
    assert origin["server"] == "demo" and origin["setDigest"] == rec["setDigest"]
    # no execute bits anywhere
    assert all(not (p.stat().st_mode & stat.S_IXUSR) for p in folder.rglob("*") if p.is_file())


def test_lock_is_sorted_and_byte_stable(project: Project) -> None:
    project.run("add", "demo", "release-notes", "--target", "claude", "--yes")
    project.run("add", "demo", "refunds", "--target", "claude", "--yes")
    first = project.lock_path.read_bytes()
    text = first.decode()
    assert text.endswith("}\n") and '\n  "skills": {' in text
    data = json.loads(text)
    assert text == json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    # adding the same skill again rewrites identical bytes
    project.run("add", "demo", "release-notes", "--target", "claude", "--yes")
    assert project.lock_path.read_bytes() == first


def test_target_aliases(project: Project) -> None:
    cases = {
        "codex": project.cwd / ".agents" / "skills",
        "codex-user": project.home / ".agents" / "skills",
        "claude-user": project.home / ".claude" / "skills",
        "custom/dir": project.cwd / "custom" / "dir",
    }
    for target, root in cases.items():
        res = project.run("add", "demo", "refunds", "--target", target, "--yes",
                          "--lock", str(project.root / f"{target.replace('/', '_')}.lock.json"))
        assert res.code == 0, res.text
        assert (root / "refunds" / "SKILL.md").is_file(), target


def test_nested_uri_prefix_is_not_a_folder(project: Project) -> None:
    project.run("add", "demo", "skill://acme/billing/refunds/SKILL.md", "--target", "claude", "--yes")
    assert (project.cwd / ".claude" / "skills" / "refunds" / "SKILL.md").is_file()
    assert not (project.cwd / ".claude" / "skills" / "acme").exists()


def test_local_collision_leaves_files_alone(project: Project) -> None:
    mine = project.cwd / ".claude" / "skills" / "release-notes"
    mine.mkdir(parents=True)
    (mine / "SKILL.md").write_text("my own skill\n")
    res = project.run("add", "demo", "release-notes", "--target", "claude", "--yes")
    assert res.code == 2 and "error[local-collision]" in res.err
    assert (mine / "SKILL.md").read_text() == "my own skill\n"
    assert not project.lock_path.exists()
    # no staging leftovers next to it
    assert sorted(p.name for p in mine.parent.iterdir()) == ["release-notes"]


def test_unknown_skill_and_server(project: Project) -> None:
    res = project.run("add", "demo", "nope", "--target", "claude", "--yes")
    assert res.code == 2 and "error[skill-not-found]" in res.err and "refunds" in res.err
    res = project.run("list", "ghost")
    assert res.code == 2 and "error[server-not-found]" in res.err and "demo" in res.err


def test_install_replaces_atomically_and_removes_old_files(project: Project) -> None:
    project.run("add", "demo", "release-notes", "--target", "claude", "--yes")
    folder = project.cwd / ".claude" / "skills" / "release-notes"
    (folder / "stray.txt").write_text("x")
    assert project.run("add", "demo", "release-notes", "--target", "claude", "--yes", "--discard-local").code == 0
    assert not (folder / "stray.txt").exists()
    assert os.listdir(folder.parent) == ["release-notes"]


def test_second_target_is_refused_not_silently_untracked(project: Project) -> None:
    assert project.run("add", "demo", "release-notes", "--target", "claude", "--yes").code == 0
    before = project.lock()
    res = project.run("add", "demo", "release-notes", "--target", "codex", "--yes")
    assert res.code == 2 and "error[target-changed]" in res.err
    assert ".claude/skills/release-notes" in res.err and "hint:" in res.err
    assert project.lock() == before
    assert not (project.cwd / ".agents").exists()
    ko = project.run("add", "demo", "release-notes", "--target", "codex", "--yes", lang="ko")
    assert ko.code == 2 and "error[target-changed]" in ko.err
