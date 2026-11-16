"""Static checks of a SEP-2640 skill entry (no file bytes involved)."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import unquote

from mcp_skillpin.errors import SkillpinError

MAX_FILES = 512
MAX_BYTES = 16 * 1024 * 1024  # 16 MiB
# Written by the installer into every skill folder; a server file may not use this name.
ORIGIN_FILE = ".mcp-origin.json"
_DIGEST = re.compile(r"\Asha256:[0-9a-f]{64}\Z")
_NAME = re.compile(r"\A[a-z0-9]+(?:-[a-z0-9]+)*\Z")


@dataclass(frozen=True)
class ResourceRef:
    uri: str
    digest: str
    size: int
    rel: str  # path relative to the skill folder, "/" separated


@dataclass
class Entry:
    uri: str  # the skill's SKILL.md URI
    base: str  # URI of the skill folder (no trailing slash)
    name: str
    frontmatter: dict[str, Any]
    resources: list[ResourceRef] = field(default_factory=list)


def safe_rel_path(rel: str) -> bool:
    """True when ``rel`` is a plain relative path that cannot leave its folder."""
    if not rel or "\\" in rel or "\x00" in rel or rel.startswith("/"):
        return False
    for candidate in (rel, unquote(rel)):
        if "\\" in candidate or "\x00" in candidate:
            return False
        for seg in candidate.split("/"):
            if seg in ("", ".", ".."):
                return False
    return True


def _fold(rel: str) -> str:
    """Key under which two paths name the same file on a case/normalization-insensitive disk."""
    return unicodedata.normalize("NFC", unicodedata.normalize("NFC", rel).casefold())


def check_path_tree(base: str, rels: list[str]) -> None:
    """Refuse file lists that cannot be written as separate files on every disk.

    macOS (APFS) and Windows ignore letter case and Unicode form (NFC/NFD), so the check
    compares folded names. It looks at the whole tree, not only whole file paths:

    * two files with the same folded path            (``SKILL.md`` / ``skill.md``)
    * one folder spelled two ways                    (``ref/a.md`` / ``REF/b.md``)
    * a file whose path is also a folder of another  (``docs`` / ``docs/x.md``, ``Docs`` / ``docs/x.md``)
    * the installer's own marker file                (``.mcp-origin.json``)

    Raises ``error[unsafe-path]`` with a message that names the other path.
    """
    files: dict[str, str] = {}  # folded file path -> path as the server spelled it
    folders: dict[str, tuple[str, str]] = {}  # folded folder -> (spelling, first file inside it)
    reserved = _fold(ORIGIN_FILE)
    for rel in rels:
        parts = rel.split("/")
        if _fold(parts[0]) == reserved:
            raise SkillpinError("unsafe-path", message_key="err.path-reserved", path=f"{base}/{rel}",
                                other=ORIGIN_FILE)
        key = _fold(rel)
        if key in files:
            raise SkillpinError("unsafe-path", message_key="err.path-collision", path=f"{base}/{rel}",
                                other=files[key])
        files[key] = rel
        # Every folder on the way to the file ("a", "a/b" for "a/b/c.md") must keep one spelling.
        for depth in range(1, len(parts)):
            folder = "/".join(parts[:depth])
            spelling, _first = folders.setdefault(_fold(folder), (folder, rel))
            if spelling != folder:
                raise SkillpinError("unsafe-path", message_key="err.path-collision",
                                    path=f"{base}/{folder}/", other=f"{spelling}/")
    # A path cannot be a file and a folder at the same time (checked after the loop: order-free).
    for key, rel in files.items():
        if key in folders:
            raise SkillpinError("unsafe-path", message_key="err.path-file-dir", path=f"{base}/{rel}",
                                other=folders[key][1])


def parse_entry(raw: Any) -> Entry:
    """Validate the shape, limits, names and paths of one skill entry."""
    if not isinstance(raw, dict):
        raise SkillpinError("bad-entry", detail="entry is not an object")
    uri = raw.get("uri")
    if not isinstance(uri, str) or not uri.endswith("/SKILL.md") or "://" not in uri:
        raise SkillpinError("bad-entry", detail=f"uri must end with /SKILL.md: {uri!r}")
    base = uri[: -len("/SKILL.md")]
    path = base.split("://", 1)[1]
    segments = path.split("/")
    if any(seg in ("", ".", "..") or "\\" in seg or "\x00" in seg for seg in segments):
        raise SkillpinError("unsafe-path", path=uri)
    name = segments[-1]
    fm = raw.get("frontmatter")
    if not isinstance(fm, dict) or not isinstance(fm.get("name"), str):
        raise SkillpinError("bad-entry", detail="frontmatter.name is missing")
    if fm["name"] != name:
        raise SkillpinError("name-mismatch", expected=name, actual=fm["name"], uri=uri)
    if not _NAME.match(name) or len(name) > 64:
        raise SkillpinError("unsafe-path", path=name)

    resources = raw.get("resources")
    if resources == "dynamic":
        raise SkillpinError("dynamic-refused", uri=uri)
    if not isinstance(resources, list):
        raise SkillpinError("bad-entry", detail="resources must be an array or \"dynamic\"")
    if len(resources) > MAX_FILES:
        raise SkillpinError("limit-exceeded", detail=f"{len(resources)} files > {MAX_FILES}")

    refs: list[ResourceRef] = []
    seen: set[str] = set()
    total = 0
    for item in resources:
        if not isinstance(item, dict):
            raise SkillpinError("bad-entry", detail="resource is not an object")
        r_uri, digest, size = item.get("uri"), item.get("digest"), item.get("size")
        if not isinstance(r_uri, str):
            raise SkillpinError("bad-entry", detail="resource uri is missing")
        if not isinstance(digest, str) or not _DIGEST.match(digest):
            raise SkillpinError("bad-entry", detail=f"bad digest for {r_uri}")
        if not isinstance(size, int) or isinstance(size, bool) or size < 0:
            raise SkillpinError("bad-entry", detail=f"bad size for {r_uri}")
        if not r_uri.startswith(base + "/"):
            raise SkillpinError("unsafe-path", path=r_uri)
        rel = r_uri[len(base) + 1 :]
        if not safe_rel_path(rel):
            raise SkillpinError("unsafe-path", path=r_uri)
        if rel in seen:
            raise SkillpinError("bad-entry", detail=f"duplicate resource {rel}")
        seen.add(rel)
        total += size
        refs.append(ResourceRef(r_uri, digest, size, rel))
    if total > MAX_BYTES:
        raise SkillpinError("limit-exceeded", detail=f"{total} bytes > {MAX_BYTES}")
    if "SKILL.md" not in seen:
        raise SkillpinError("bad-entry", detail="resources does not list SKILL.md")
    # APFS/NTFS treat names differing only in case or Unicode form as one file or folder.
    check_path_tree(base, [r.rel for r in refs])
    return Entry(uri=uri, base=base, name=name, frontmatter=fm, resources=refs)


def set_digest(resources: list[ResourceRef]) -> str:
    """Digest of the approved resource set (uri + digest of every file)."""
    pairs = sorted([r.uri, r.digest] for r in resources)
    blob = json.dumps(pairs, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return "sha256:" + hashlib.sha256(blob).hexdigest()
