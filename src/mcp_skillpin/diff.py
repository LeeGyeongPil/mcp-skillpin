"""Change detection between a locked skill and a server entry, plus risk signals."""

from __future__ import annotations

import difflib
from dataclasses import dataclass, field
from typing import Any

from mcp_skillpin.frontmatter import GRANT_KEYS, grant_values
from mcp_skillpin.manifest import ResourceRef

SCRIPT_EXTENSIONS = {
    ".sh", ".bash", ".zsh", ".py", ".js", ".mjs", ".ts", ".rb", ".pl", ".ps1", ".bat", ".cmd", ".exe", ".command",
}
MAX_DIFF_LINES = 200


@dataclass
class Change:
    added: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    changed: list[str] = field(default_factory=list)

    @property
    def any(self) -> bool:
        return bool(self.added or self.removed or self.changed)


@dataclass(frozen=True)
class Risk:
    code: str  # script-new | script-changed | executable | shebang | nested-skill | grant-key
    path: str


def compare(locked_files: dict[str, dict[str, Any]], refs: list[ResourceRef]) -> Change:
    """Compare locked files (rel -> {digest, ...}) with the entry's resource list."""
    new = {r.rel: r for r in refs}
    return Change(
        added=sorted(r for r in new if r not in locked_files),
        removed=sorted(r for r in locked_files if r not in new),
        changed=sorted(r for r in new if r in locked_files and locked_files[r]["digest"] != new[r].digest),
    )


def is_text(data: bytes) -> bool:
    if b"\x00" in data:
        return False
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return True


def _path_risks(rel: str, status: str) -> list[Risk]:
    risks: list[Risk] = []
    parts = rel.split("/")
    if "scripts" in parts[:-1]:
        risks.append(Risk("script-new" if status == "added" else "script-changed", rel))
    elif "." + parts[-1].rsplit(".", 1)[-1].lower() in SCRIPT_EXTENSIONS and "." in parts[-1]:
        risks.append(Risk("executable", rel))
    if parts[-1] == "SKILL.md" and rel != "SKILL.md":
        risks.append(Risk("nested-skill", rel))
    return risks


def path_risks_for_listing(refs: list[ResourceRef]) -> list[Risk]:
    """Signals visible from file names alone (used by ``list`` before anything is downloaded)."""
    risks: list[Risk] = []
    for ref in refs:
        risks.extend(_path_risks(ref.rel, "added"))
    return risks


def content_risks(
    files: dict[str, bytes],
    change: Change | None,
    old_frontmatter: dict[str, Any] | None,
    new_frontmatter: dict[str, Any],
) -> list[Risk]:
    """Risk signals for files that are new or changed (everything, when ``change`` is None)."""
    risks: list[Risk] = []
    status_of: dict[str, str] = {}
    if change is None:
        status_of = {rel: "added" for rel in files}
    else:
        status_of.update({rel: "added" for rel in change.added})
        status_of.update({rel: "changed" for rel in change.changed})
    for rel, status in sorted(status_of.items()):
        risks.extend(_path_risks(rel, status))
        data = files.get(rel, b"")
        if data.startswith(b"#!") and not any(r.path == rel for r in risks):
            risks.append(Risk("shebang", rel))
    old = grant_values(old_frontmatter) if old_frontmatter is not None else {}
    new = grant_values(new_frontmatter)
    for key in GRANT_KEYS:
        if new.get(key) != old.get(key) and key in new:
            risks.append(Risk("grant-key", key))
    return risks


def unified_diff(rel: str, old: bytes | None, new: bytes) -> list[str]:
    """Unified diff lines for a text file; empty list for binary data."""
    if (old is not None and not is_text(old)) or not is_text(new):
        return []
    old_lines = (old or b"").decode("utf-8").splitlines(keepends=True)
    new_lines = new.decode("utf-8").splitlines(keepends=True)
    lines = list(
        difflib.unified_diff(
            old_lines, new_lines,
            fromfile=f"a/{rel}" if old is not None else "/dev/null",
            tofile=f"b/{rel}",
        )
    )
    lines = [ln.rstrip("\n") for ln in lines]
    if len(lines) > MAX_DIFF_LINES:
        lines = lines[:MAX_DIFF_LINES] + ["... (truncated)"]
    return lines
