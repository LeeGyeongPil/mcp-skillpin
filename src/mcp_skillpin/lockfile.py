"""mcp-skills.lock.json: the set of skills (and exact file digests) a user approved."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from mcp_skillpin.errors import SkillpinError

LOCK_VERSION = 1
DEFAULT_NAME = "mcp-skills.lock.json"


def empty() -> dict[str, Any]:
    return {"version": LOCK_VERSION, "skills": {}}


def load(path: Path, *, required: bool = False) -> dict[str, Any]:
    if not path.exists():
        if required:
            raise SkillpinError("no-lock", path=str(path))
        return empty()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SkillpinError("bad-lock", path=str(path), detail=str(exc)) from exc
    if (
        not isinstance(data, dict)
        or data.get("version") != LOCK_VERSION
        or not isinstance(data.get("skills"), dict)
    ):
        raise SkillpinError(
            "bad-lock", path=str(path), detail=f"expected version {LOCK_VERSION} with a skills object"
        )
    return data


def dumps(data: dict[str, Any]) -> str:
    """Sorted keys, two-space indent, trailing newline: stable under git diff."""
    return json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def save(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(dumps(data), encoding="utf-8")
    os.replace(tmp, path)


def now_iso(environ: dict[str, str] | None = None) -> str:
    """UTC timestamp; ``SOURCE_DATE_EPOCH`` makes it reproducible."""
    env = environ if environ is not None else dict(os.environ)
    epoch = env.get("SOURCE_DATE_EPOCH")
    stamp = float(epoch) if epoch and epoch.isdigit() else time.time()
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(stamp))


def skill_key(label: str, name: str) -> str:
    return f"{label}/{name}"


def store_path(path: Path, base: Path, home: Path) -> str:
    """Prefer a path relative to the lock file, then ``~/``, then absolute."""
    try:
        return path.relative_to(base).as_posix()
    except ValueError:
        pass
    try:
        return "~/" + path.relative_to(home).as_posix()
    except ValueError:
        return path.as_posix()


def load_path(stored: str, base: Path, home: Path) -> Path:
    if stored.startswith("~/"):
        return home / stored[2:]
    p = Path(stored)
    return p if p.is_absolute() else base / p
