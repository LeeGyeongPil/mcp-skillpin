"""AC-9: English and Korean screens, selectable, with fixed error code tokens."""

from __future__ import annotations

import io
import re
import string
from pathlib import Path

import pytest
from conftest import Project, mock

from mcp_skillpin import i18n
from mcp_skillpin.cli import main
from mcp_skillpin.errors import CODE_EXIT

HANGUL = re.compile(r"[가-힣]")
SRC = Path(__file__).resolve().parents[1] / "src" / "mcp_skillpin"


def placeholders(text: str) -> set[str]:
    return {name for _, name, _, _ in string.Formatter().parse(text) if name}


def test_message_tables_have_identical_keys_and_placeholders() -> None:
    en, ko = i18n.load_messages("en"), i18n.load_messages("ko")
    assert set(en) == set(ko)
    for key in en:
        assert placeholders(en[key]) == placeholders(ko[key]), key
        assert en[key].strip() and ko[key].strip()


def test_every_key_used_in_code_exists() -> None:
    en = i18n.load_messages("en")
    used: set[str] = set()
    for path in SRC.glob("*.py"):
        source = path.read_text(encoding="utf-8")
        used |= set(re.findall(r'\bt\("([a-z0-9_.\-]+)"', source))
        used |= set(re.findall(r'message_key="([a-z0-9_.\-]+)"', source))  # specific error messages
        used |= set(re.findall(r'"(usage\.[a-z_]+)"', source))  # argparse message table in cli.py
    assert used, "scan found no keys"
    assert {"err.path-collision", "err.path-file-dir", "usage.required"} <= used
    assert used <= set(en), sorted(used - set(en))
    # dynamically built keys
    for code in ("script-new", "script-changed", "executable", "shebang", "nested-skill", "grant-key"):
        assert f"risk.{code}" in en
    for code in CODE_EXIT:
        assert f"err.{code}" in en


def test_english_output_has_no_hangul_and_korean_does(project: Project) -> None:
    project.run("add", "demo", "refunds", "--target", "claude", "--yes")
    en = project.run("check", lang="en")
    ko = project.run("check", lang="ko")
    assert en.code == ko.code == 0
    assert not HANGUL.search(en.text) and "unchanged" in en.out
    assert HANGUL.search(ko.text) and "변경 없음" in ko.out
    for lang in ("en", "ko"):
        listed = project.run("list", "demo", lang=lang)
        assert bool(HANGUL.search(listed.out)) == (lang == "ko")


def test_help_in_both_languages(capsys: pytest.CaptureFixture[str]) -> None:
    from mcp_skillpin.cli import main

    for lang in ("en", "ko"):
        assert main(["--help", "--lang", lang], environ={}) == 0
        shown = capsys.readouterr().out
        assert "check" in shown and "mcp-skillpin" in shown
        assert bool(HANGUL.search(shown)) == (lang == "ko")
    assert main(["add", "--help"], environ={"MCP_SKILLPIN_LANG": "ko"}) == 0
    assert HANGUL.search(capsys.readouterr().out)


@pytest.mark.parametrize(
    ("cli", "env", "expected"),
    [
        ("ko", {"MCP_SKILLPIN_LANG": "en", "LANG": "en_US.UTF-8"}, "ko"),
        ("en", {"MCP_SKILLPIN_LANG": "ko", "LANG": "ko_KR.UTF-8"}, "en"),
        (None, {"MCP_SKILLPIN_LANG": "ko", "LC_ALL": "en_US.UTF-8"}, "ko"),
        (None, {"MCP_SKILLPIN_LANG": "en", "LANG": "ko_KR.UTF-8"}, "en"),
        (None, {"LC_ALL": "ko_KR.UTF-8", "LC_MESSAGES": "en_US", "LANG": "en_US"}, "ko"),
        (None, {"LC_ALL": "C", "LANG": "ko_KR.UTF-8"}, "en"),
        (None, {"LC_MESSAGES": "ko_KR.UTF-8", "LANG": "en_US"}, "ko"),
        (None, {"LANG": "ko_KR.UTF-8"}, "ko"),
        (None, {"LANG": "fr_FR.UTF-8"}, "en"),
        (None, {}, "en"),
    ],
)
def test_language_precedence(cli: str | None, env: dict[str, str], expected: str) -> None:
    assert i18n.resolve_language(cli, env) == expected


def test_env_var_switches_cli_output(project: Project) -> None:
    project.run("add", "demo", "refunds", "--target", "claude", "--yes")
    res = project.run("check", lang=None, env={"MCP_SKILLPIN_LANG": "ko"})
    assert "변경 없음" in res.out
    res = project.run("check", lang=None, env={"LANG": "ko_KR.UTF-8"})
    assert "변경 없음" in res.out
    res = project.run("check", lang="en", env={"MCP_SKILLPIN_LANG": "ko"})
    assert "unchanged" in res.out


def test_error_code_tokens_are_the_same_in_both_languages(project: Project) -> None:
    project.write_mcp({"bad": mock("--tamper", "digest")})
    en = project.run("add", "bad", "release-notes", "--target", "claude", "--yes", lang="en")
    ko = project.run("add", "bad", "release-notes", "--target", "claude", "--yes", lang="ko")
    assert en.code == ko.code == 3
    assert "error[digest-mismatch]" in en.err and "error[digest-mismatch]" in ko.err
    assert HANGUL.search(ko.err) and not HANGUL.search(en.err)


def test_korean_risk_and_diff_screens(project: Project) -> None:
    project.run("add", "demo", "release-notes", "--target", "claude", "--yes")
    project.write_mcp({"demo": mock(data="v2")})
    ko = project.run("update", "release-notes", lang="ko")
    assert "위험: 새 스크립트 파일 scripts/collect.sh" in ko.out
    assert "+Before writing, run" in ko.out  # the diff body is data, not translated


@pytest.mark.parametrize("argv", [["--help"], ["add", "--help"], ["update", "--help"]])
def test_korean_help_has_no_english_argparse_boilerplate(
    argv: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    assert main([*argv, "--lang", "ko"], environ={}) == 0
    shown = capsys.readouterr().out
    for english in ("show this help message", "positional arguments", "usage:", "options:", "show program"):
        assert english not in shown
    assert "사용법:" in shown


def test_korean_servers_table_has_no_english_args_suffix(project: Project) -> None:
    res = project.run("servers", lang="ko")
    assert "args)" not in res.out


# Q-2 (fix round 2): argparse's own error lines must follow the UI language too.
ARGPARSE_ENGLISH = (
    "error: the following", "the following arguments", "invalid choice", "unrecognized arguments",
    "expected one argument", "ignored explicit", "ambiguous option", "usage:", "error:",
)
USAGE_MISTAKES = [
    (["add"], "다음 인자가 필요합니다: server, skill, --target", "the following arguments are required"),
    (["bogus"], "허용되지 않는 값 'bogus'", "invalid choice: 'bogus'"),
    (["check", "--nope"], "알 수 없는 인자: --nope", "unrecognized arguments: --nope"),
    (["add", "demo", "x", "--target"], "인자 --target: 값이 하나 필요합니다", "argument --target: expected one argument"),
    (["add", "a", "b", "--target", "claude", "--grant", "nope"], "허용되지 않는 값 'nope'", "invalid choice: 'nope'"),
    (["list", "x", "--timeout", "abc"], "인자 --timeout:", "invalid float value: 'abc'"),
    (["verify", "--json=1"], "이 옵션은 값을 받지 않습니다", "ignored explicit argument"),
    (["add", "a", "b", "--t", "x"], "여러 옵션에 해당합니다", "ambiguous option: --t"),
]


@pytest.mark.parametrize(("argv", "ko_text", "en_text"), USAGE_MISTAKES)
def test_usage_errors_follow_the_ui_language(argv: list[str], ko_text: str, en_text: str) -> None:
    for lang, expected in (("ko", ko_text), ("en", en_text)):
        out, err = io.StringIO(), io.StringIO()
        code = main([*argv, "--lang", lang], environ={}, stdout=out, stderr=err)
        shown = out.getvalue() + err.getvalue()
        assert code == 2, shown
        assert "error[usage]: " in err.getvalue() and expected in err.getvalue(), shown
        if lang == "ko":
            assert "사용법:" in shown and "힌트: " in shown
            # 'error[usage]' is the fixed token; argparse's English phrases must be gone
            for english in ARGPARSE_ENGLISH:
                assert english not in shown.replace("error[usage]:", ""), (english, shown)
        else:
            assert "usage:" in shown and "hint: " in shown and not HANGUL.search(shown)


def test_usage_errors_by_environment_variable_and_streams() -> None:
    """MCP_SKILLPIN_LANG alone switches the error language; nothing leaks to the real stdout/stderr."""
    out, err = io.StringIO(), io.StringIO()
    assert main(["add"], environ={"MCP_SKILLPIN_LANG": "ko"}, stdout=out, stderr=err) == 2
    assert "다음 인자가 필요합니다" in err.getvalue() and out.getvalue() == ""


def test_unknown_argparse_message_is_passed_through() -> None:
    from mcp_skillpin.cli import translate_argparse_message

    i18n.set_language("ko")
    try:
        assert translate_argparse_message("something argparse added later") == "something argparse added later"
        assert translate_argparse_message("argument --x: something new") == "인자 --x: something new"
    finally:
        i18n.set_language("en")
