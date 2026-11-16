"""Resolve install targets, write verified skills atomically, re-hash installed folders."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path

from mcp_skillpin.errors import SkillpinError
from mcp_skillpin.fetch import SkillBundle, sha256_digest
from mcp_skillpin.manifest import ORIGIN_FILE

TARGETS = ("claude", "claude-user", "codex", "codex-user")


def resolve_target(target: str, cwd: Path, home: Path) -> Path:
    """Map ``--target`` to a skills root folder. Anything else is taken as a path."""
    if target == "claude":
        return cwd / ".claude" / "skills"
    if target == "claude-user":
        return home / ".claude" / "skills"
    if target == "codex":
        return cwd / ".agents" / "skills"
    if target == "codex-user":
        return home / ".agents" / "skills"
    if not target.strip():
        raise SkillpinError("bad-target", target=target)
    path = Path(target).expanduser()
    return path if path.is_absolute() else cwd / path


def check_collision(dest: Path, known_dest: Path | None) -> None:
    """Refuse to overwrite a folder that this lock file does not own."""
    if known_dest is not None and known_dest != dest and known_dest.exists():
        raise SkillpinError("target-changed", path=str(known_dest))
    if dest.exists() and (known_dest is None or known_dest != dest):
        raise SkillpinError("local-collision", path=str(dest))


def _write_new(path: Path, data: bytes, rel: str) -> None:
    """Create ``path`` with ``data``; never overwrite, never follow a file where a folder is needed.

    parse_entry already refuses file lists that overlap on disk. This is the second line of
    defense: if the disk still disagrees (another case-insensitive file system, a race), the
    OS error becomes ``error[unsafe-path]`` (exit 3) instead of a Python traceback.
    """
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        # O_EXCL: a file that already exists (e.g. a case-insensitive twin) is never overwritten.
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    except (FileExistsError, NotADirectoryError, IsADirectoryError) as exc:
        raise SkillpinError("unsafe-path", message_key="err.path-on-disk", path=rel) from exc
    with os.fdopen(fd, "wb") as handle:
        handle.write(data)
    os.chmod(path, 0o644)  # never grant the execute bit


def install(bundle: SkillBundle, root: Path, server_label: str) -> Path:
    """Write the skill into ``root/<name>`` via a staging folder next to it."""
    dest = root / bundle.name
    # Folders this call creates (e.g. ".claude" and ".claude/skills"); removed again on failure.
    created = [folder for folder in (root, *root.parents) if not folder.exists()]
    root.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=f".{bundle.name}.stage-", dir=root))
    backup: Path | None = None
    done = False
    try:
        stage_real = stage.resolve()
        for rel, data in bundle.files.items():
            out = (stage / rel).resolve()
            if stage_real not in out.parents:
                raise SkillpinError("unsafe-path", path=rel)
            _write_new(out, data, rel)
        origin = {
            "tool": "mcp-skillpin",
            "server": server_label,
            "uri": bundle.entry.uri,
            "setDigest": bundle.set_digest,
        }
        origin_bytes = (json.dumps(origin, indent=2, sort_keys=True) + "\n").encode("utf-8")
        _write_new(stage / ORIGIN_FILE, origin_bytes, ORIGIN_FILE)
        os.chmod(stage, 0o755)
        for sub in stage.rglob("*"):
            if sub.is_dir():
                os.chmod(sub, 0o755)
        if dest.exists():
            backup = root / f".{bundle.name}.old-{os.getpid()}"
            os.replace(dest, backup)
        try:
            os.replace(stage, dest)
        except OSError:
            if backup is not None:
                os.replace(backup, dest)
                backup = None
            raise
        done = True
    finally:
        if stage.exists():
            shutil.rmtree(stage, ignore_errors=True)
        if backup is not None and backup.exists():
            shutil.rmtree(backup, ignore_errors=True)
        if not done:
            # Leave the disk as we found it: drop the (now empty) folders this call created.
            for folder in created:
                try:
                    folder.rmdir()
                except OSError:
                    break
    return dest


def hash_tree(dest: Path) -> dict[str, tuple[str, int]]:
    """rel path -> (digest, size) for every file below ``dest`` except the origin marker."""
    out: dict[str, tuple[str, int]] = {}
    if not dest.is_dir():
        return out
    for path in sorted(dest.rglob("*")):
        if path.is_file() and not path.is_symlink():
            rel = path.relative_to(dest).as_posix()
            if rel == ORIGIN_FILE:
                continue
            data = path.read_bytes()
            out[rel] = (sha256_digest(data), len(data))
    return out


def verify_installed(
    dest: Path, locked_files: dict[str, dict[str, object]]
) -> tuple[list[str], list[str], list[str]]:
    """Compare an installed folder with the lock. Returns (modified, missing, extra)."""
    actual = hash_tree(dest)
    modified = sorted(r for r, (d, _) in actual.items() if r in locked_files and locked_files[r]["digest"] != d)
    missing = sorted(r for r in locked_files if r not in actual)
    extra = sorted(r for r in actual if r not in locked_files)
    return modified, missing, extra
