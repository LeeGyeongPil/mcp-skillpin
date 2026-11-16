"""`mcp-skillpin demo`: the whole flow against the practice mock server, in a temp folder."""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

from mcp_skillpin import i18n
from mcp_skillpin.commands import Context
from mcp_skillpin.i18n import t

PKG_PARENT = str(Path(__file__).resolve().parents[1])  # lets the mock server import this package


def _server(data: str, *extra: str) -> dict[str, object]:
    return {
        "command": sys.executable,
        "args": ["-m", "mcp_skillpin.mockserver", "--data", data, *extra],
        "env": {"PYTHONPATH": PKG_PARENT},
    }


def run_demo(ctx: Context) -> int:
    from mcp_skillpin.cli import main  # imported late: cli imports this module lazily too

    out = ctx.out
    lang = i18n.current_language()
    failures = 0

    def say(text: str = "") -> None:
        print(text, file=out)

    with tempfile.TemporaryDirectory(prefix="mcp-skillpin-demo-") as tmp:
        project = Path(tmp) / "project"
        home = Path(tmp) / "home"
        project.mkdir()
        home.mkdir()
        env = dict(ctx.environ)
        env["HOME"] = str(home)

        def write_config(demo_data: str) -> None:
            servers = {"demo": _server(demo_data), "evil": _server("v1", "--tamper", "digest")}
            (project / ".mcp.json").write_text(json.dumps({"mcpServers": servers}, indent=2), encoding="utf-8")

        def step(title: str, argv: list[str], expect: int) -> None:
            nonlocal failures
            say()
            say(f"=== {title}")
            say("$ mcp-skillpin " + " ".join(argv))
            code = main([*argv, "--lang", lang], cwd=project, home=home, environ=env, stdout=out, stderr=out)
            say(t("demo.exit", code=code))
            if code != expect:
                failures += 1

        say(t("demo.banner"))
        say(t("demo.workdir", path=project))
        write_config("v1")
        step(t("demo.s1"), ["servers", "--probe"], 0)
        step(t("demo.s2"), ["list", "demo"], 0)
        step(t("demo.s3"), ["add", "demo", "release-notes", "--target", "claude"], 0)
        step(t("demo.s4"), ["add", "demo", "release-notes", "--target", "claude", "--yes"], 0)
        step(t("demo.s4b"), ["add", "demo", "refunds", "--target", "claude", "--yes"], 0)
        step(t("demo.s5"), ["verify"], 0)
        say()
        say(t("demo.switch"))
        write_config("v2")
        step(t("demo.s6"), ["check"], 1)
        step(t("demo.s7"), ["update", "release-notes"], 0)
        step(t("demo.s8"), ["update", "release-notes", "--yes"], 0)
        step(t("demo.s9"), ["check"], 0)
        step(t("demo.s10"), ["add", "evil", "release-notes", "--target", "tampered-skills", "--yes"], 3)
        written = (project / "tampered-skills").exists()
        say(t("demo.nothing_written") if not written else t("demo.something_written"))
        if written:
            failures += 1
        say()
        say(t("demo.done"))
    return 1 if failures else 0
