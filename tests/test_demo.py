"""AC-10: the keyless, offline demo runs end to end."""

from __future__ import annotations

import subprocess
import sys
import time


def run_demo(lang: str) -> tuple[int, str, float]:
    start = time.monotonic()
    proc = subprocess.run(
        [sys.executable, "-m", "mcp_skillpin", "demo", "--lang", lang],
        capture_output=True, text=True, timeout=120, encoding="utf-8",
    )
    return proc.returncode, proc.stdout + proc.stderr, time.monotonic() - start


def test_demo_english() -> None:
    code, out, seconds = run_demo("en")
    assert code == 0, out
    assert seconds < 60
    assert out.splitlines()[0].startswith("This uses a practice mock MCP server")
    for needle in ("CHANGED", "scripts/collect.sh", "RISK", "digest-mismatch", "(exit code 3)"):
        assert needle in out, needle
    assert "Nothing was written to the target folder." in out


def test_demo_korean() -> None:
    code, out, _ = run_demo("ko")
    assert code == 0, out
    assert out.splitlines()[0].startswith("연습용 목(mock) MCP 서버")
    for needle in ("변경됨", "scripts/collect.sh", "위험", "digest-mismatch"):
        assert needle in out, needle
