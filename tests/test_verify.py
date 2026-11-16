"""AC-3: verification failures close the door; nothing is written."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from conftest import Project, mock

from mcp_skillpin import install
from mcp_skillpin.errors import SkillpinError
from mcp_skillpin.fetch import SkillBundle
from mcp_skillpin.manifest import Entry, ResourceRef, check_path_tree, parse_entry, safe_rel_path
from mcp_skillpin.mockserver import TAMPERS as MOCK_TAMPERS

TAMPERS = [
    ("digest", "digest-mismatch"),
    ("size", "size-mismatch"),
    ("unlisted", "unlisted-resource"),
    ("frontmatter", "frontmatter-mismatch"),
    ("name", "name-mismatch"),
    ("traversal", "unsafe-path"),
    ("limit", "limit-exceeded"),
    ("dynamic", "dynamic-refused"),
    # paths that overlap on disk: case twins, NFC/NFD twins, file vs folder, folder spelled twice
    ("casefold", "unsafe-path"),
    ("unicode", "unsafe-path"),
    ("file-dir", "unsafe-path"),
    ("file-dir-case", "unsafe-path"),
    ("dir-case", "unsafe-path"),
    ("reserved", "unsafe-path"),
]


def test_every_mock_tamper_is_tested() -> None:
    assert {name for name, _ in TAMPERS} | {"limit-size"} == set(MOCK_TAMPERS)


@pytest.mark.parametrize(("tamper", "code"), TAMPERS)
def test_tamper_fails_closed(project: Project, tamper: str, code: str) -> None:
    project.write_mcp({"bad": mock("--tamper", tamper)})
    res = project.run("add", "bad", "release-notes", "--target", "claude", "--yes")
    assert res.code == 3, res.text
    assert f"error[{code}]" in res.err
    assert "Traceback" not in res.text
    assert not (project.cwd / ".claude").exists()  # not even the skills root was created
    assert not project.lock_path.exists()


@pytest.mark.parametrize(
    ("tamper", "other", "hint_en", "hint_ko"),
    [
        ("casefold", "collides with SKILL.md", "rename one of the two", "둘 중 하나의 이름"),
        ("unicode", "Unicode form", "rename one of the two", "둘 중 하나의 이름"),
        ("dir-case", "collides with ref/", "rename one of the two", "둘 중 하나의 이름"),
        ("file-dir", "but docs/x.md needs a folder", "rename the file or the folder", "파일이나 폴더의 이름"),
        ("file-dir-case", "skill://release-notes/Docs is listed as a file", "rename the file or the folder",
         "파일이나 폴더의 이름"),
        ("reserved", ".mcp-origin.json", "server owner", "서버 관리자"),
    ],
)
def test_path_collision_explains_itself(project: Project, tamper: str, other: str, hint_en: str, hint_ko: str) -> None:
    """Q-3: the message names the other path and the hint says what to do (en and ko)."""
    project.write_mcp({"bad": mock("--tamper", tamper)})
    en = project.run("add", "bad", "release-notes", "--target", "claude", "--yes", lang="en")
    assert other in en.err and "Nothing was written" in en.err
    assert en.err.splitlines()[-1].startswith("hint: ") and hint_en in en.err
    ko = project.run("add", "bad", "release-notes", "--target", "claude", "--yes", lang="ko")
    assert ko.code == 3 and "error[unsafe-path]" in ko.err and "아무것도 쓰지 않았습니다" in ko.err
    assert ko.err.splitlines()[-1].startswith("힌트: ") and hint_ko in ko.err
    assert not (project.cwd / ".claude").exists()


def test_list_marks_overlapping_paths(project: Project) -> None:
    project.write_mcp({"bad": mock("--tamper", "dir-case")})
    res = project.run("list", "bad")
    assert res.code == 0 and "error[unsafe-path]" in res.out


def test_size_limit(project: Project) -> None:
    project.write_mcp({"bad": mock("--tamper", "limit-size")})
    res = project.run("add", "bad", "refunds", "--target", "claude", "--yes")
    assert res.code == 3 and "error[limit-exceeded]" in res.err


@pytest.mark.parametrize(
    ("tamper", "code"), [("digest", "digest-mismatch"), ("dir-case", "unsafe-path"), ("file-dir", "unsafe-path")]
)
def test_failed_update_keeps_installed_files(project: Project, tamper: str, code: str) -> None:
    assert project.run("add", "demo", "release-notes", "--target", "claude", "--yes").code == 0
    skill_md = project.cwd / ".claude" / "skills" / "release-notes" / "SKILL.md"
    before = skill_md.read_bytes()
    lock_before = project.lock_path.read_bytes()
    project.write_mcp({"demo": mock("--tamper", tamper, data="v2")})
    res = project.run("update", "release-notes", "--yes")
    assert res.code == 3 and f"error[{code}]" in res.err
    assert skill_md.read_bytes() == before and project.lock_path.read_bytes() == lock_before
    assert project.run("verify").code == 0  # the pinned copy is still intact


def _entry(**changes: object) -> dict[str, object]:
    digest = "sha256:" + "a" * 64
    base = {
        "uri": "skill://x/SKILL.md",
        "frontmatter": {"name": "x", "description": "d"},
        "resources": [{"uri": "skill://x/SKILL.md", "digest": digest, "size": 1}],
    }
    base.update(changes)
    return base


def test_valid_entry_parses() -> None:
    entry = parse_entry(_entry())
    assert entry.name == "x" and entry.resources[0].rel == "SKILL.md"


@pytest.mark.parametrize(
    ("changes", "code"),
    [
        ({"resources": "dynamic"}, "dynamic-refused"),
        ({"resources": None}, "bad-entry"),
        ({"uri": "skill://x/README.md"}, "bad-entry"),
        ({"frontmatter": {"name": "y"}}, "name-mismatch"),
        (
            {"resources": [{"uri": "skill://x/SKILL.md", "digest": "md5:zz", "size": 1}]},
            "bad-entry",
        ),
        (
            {"resources": [{"uri": "skill://other/SKILL.md", "digest": "sha256:" + "a" * 64, "size": 1}]},
            "unsafe-path",
        ),
        (
            {"resources": [{"uri": "skill://x/a.md", "digest": "sha256:" + "a" * 64, "size": 1}]},
            "bad-entry",  # SKILL.md missing from resources
        ),
    ],
)
def test_entry_rejections(changes: dict[str, object], code: str) -> None:
    with pytest.raises(SkillpinError) as info:
        parse_entry(_entry(**changes))
    assert info.value.code == code


@pytest.mark.parametrize("rel", ["../x", "a/../b", "/abs", "a//b", "a\\b", "a/%2e%2e/b", "", "./a", "a\x00b"])
def test_unsafe_relative_paths(rel: str) -> None:
    assert not safe_rel_path(rel)


@pytest.mark.parametrize("rel", ["SKILL.md", "templates/notes.md", "a/b/c.txt"])
def test_safe_relative_paths(rel: str) -> None:
    assert safe_rel_path(rel)


def test_case_and_unicode_twins_are_rejected() -> None:
    base = "skill://x/demo"
    d = "sha256:" + "a" * 64

    def entry(*names: str) -> dict[str, object]:
        return {
            "uri": f"{base}/SKILL.md", "frontmatter": {"name": "demo"},
            "resources": [{"uri": f"{base}/{n}", "digest": d, "size": 1} for n in names],
        }

    for twins in (("SKILL.md", "skill.md"), ("SKILL.md", "a/B", "A/b"), ("SKILL.md", "café", "café")):
        with pytest.raises(SkillpinError) as exc:
            parse_entry(entry(*twins))
        assert exc.value.code == "unsafe-path"


@pytest.mark.parametrize(
    ("rels", "key"),
    [
        (["SKILL.md", "skill.md"], "err.path-collision"),
        (["SKILL.md", "café.md", "café.md"], "err.path-collision"),
        (["SKILL.md", "ref/a.md", "REF/b.md"], "err.path-collision"),
        (["SKILL.md", "a/b/x.md", "a/B/y.md"], "err.path-collision"),  # deeper folder level
        (["SKILL.md", "docs", "docs/x.md"], "err.path-file-dir"),
        (["SKILL.md", "docs/x.md", "docs"], "err.path-file-dir"),  # order does not matter
        (["SKILL.md", "Docs", "docs/x.md"], "err.path-file-dir"),
        (["SKILL.md", "a/b/c.md", "a/B"], "err.path-file-dir"),  # file vs a deeper folder
        (["SKILL.md", "skill.md/x.md"], "err.path-file-dir"),  # SKILL.md also used as a folder
        (["SKILL.md", ".mcp-origin.json"], "err.path-reserved"),
        (["SKILL.md", ".MCP-Origin.json"], "err.path-reserved"),
        (["SKILL.md", ".mcp-origin.json/x"], "err.path-reserved"),
    ],
)
def test_path_tree_overlaps_are_refused(rels: list[str], key: str) -> None:
    with pytest.raises(SkillpinError) as exc:
        check_path_tree("skill://x", rels)
    assert exc.value.code == "unsafe-path" and exc.value.message_key == key
    assert exc.value.exit_code == 3


def test_path_tree_accepts_a_normal_skill() -> None:
    check_path_tree("skill://x", ["SKILL.md", "scripts/a.sh", "scripts/b.sh", "templates/notes.md",
                                  "templates/deep/one.md", "Templates.md", "README.md"])


def test_installer_refuses_overlap_even_if_the_manifest_check_is_skipped(tmp_path: Path) -> None:
    """Defense in depth: the disk error becomes error[unsafe-path] and the disk is left clean."""
    files = {"SKILL.md": b"---\nname: x\n---\n", "docs": b"file\n", "docs/x.md": b"x\n"}
    refs = [ResourceRef(f"skill://x/{rel}", "sha256:" + "0" * 64, len(data), rel) for rel, data in files.items()]
    entry = Entry(uri="skill://x/SKILL.md", base="skill://x", name="x", frontmatter={"name": "x"}, resources=refs)
    root = tmp_path / "project" / ".claude" / "skills"
    with pytest.raises(SkillpinError) as exc:
        install.install(SkillBundle(entry=entry, files=files, set_digest="sha256:" + "0" * 64), root, "srv")
    assert exc.value.code == "unsafe-path" and exc.value.exit_code == 3
    assert not (tmp_path / "project").exists()  # folders created for this install were removed again


def test_unwritable_target_is_a_clean_error(project: Project) -> None:
    """A local disk refusal is error[io-error] (exit 2), not a traceback with exit 1."""
    (project.cwd / "a-file").write_text("not a folder\n", encoding="utf-8")
    for lang, word in (("en", "cannot read or write"), ("ko", "읽거나 쓸 수 없습니다")):
        res = project.run("add", "demo", "refunds", "--target", "a-file/skills", "--yes", lang=lang)
        assert res.code == 2, res.text
        assert re.search(r"^error\[io-error\]: ", res.err, re.M) and word in res.err
        assert "Traceback" not in res.text
    assert not project.lock_path.exists()
