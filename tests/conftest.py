"""Shared helpers: an isolated HOME + project folder and a way to run the CLI in-process."""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from mcp_skillpin.cli import main

DATA = Path(__file__).resolve().parents[1] / "src" / "mcp_skillpin" / "demo_data"


@dataclass
class Result:
    code: int
    out: str
    err: str

    @property
    def text(self) -> str:
        return self.out + self.err


def mock(*extra: str, data: str = "v1") -> dict[str, Any]:
    """A stdio server entry that launches the practice mock server."""
    return {"command": sys.executable, "args": ["-m", "mcp_skillpin.mockserver", "--data", data, *extra]}


class Project:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.cwd = root / "project"
        self.home = root / "home"
        self.cwd.mkdir()
        self.home.mkdir()
        self.env: dict[str, str] = {
            "HOME": str(self.home),
            "PATH": os.environ.get("PATH", ""),
            "SOURCE_DATE_EPOCH": "1700000000",
        }

    def write_mcp(self, servers: dict[str, Any]) -> None:
        (self.cwd / ".mcp.json").write_text(json.dumps({"mcpServers": servers}), encoding="utf-8")

    def run(self, *args: str, lang: str | None = "en", env: dict[str, str] | None = None) -> Result:
        out, err = io.StringIO(), io.StringIO()
        argv = list(args) + (["--lang", lang] if lang else [])
        merged = {**self.env, **(env or {})}
        code = main(argv, cwd=self.cwd, home=self.home, environ=merged, stdout=out, stderr=err)
        return Result(code, out.getvalue(), err.getvalue())

    @property
    def lock_path(self) -> Path:
        return self.cwd / "mcp-skills.lock.json"

    def lock(self) -> dict[str, Any]:
        return json.loads(self.lock_path.read_text(encoding="utf-8"))


@pytest.fixture()
def project(tmp_path: Path) -> Project:
    proj = Project(tmp_path)
    proj.write_mcp({"demo": mock()})
    return proj


@pytest.fixture()
def http_server():
    """Start the mock server over HTTP; ``start(*extra)`` returns its URL."""
    procs: list[subprocess.Popen[str]] = []

    def start(*extra: str, data: str = "v1") -> str:
        proc = subprocess.Popen(
            [sys.executable, "-m", "mcp_skillpin.mockserver", "--data", data, "--http", "0", *extra],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
        )
        procs.append(proc)
        assert proc.stdout is not None
        line = proc.stdout.readline().strip()
        assert line.startswith("LISTENING "), line
        return line.split(" ", 1)[1]

    yield start
    for proc in procs:
        proc.terminate()
        proc.wait(timeout=5)
        if proc.stdout:
            proc.stdout.close()


def read_log(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
