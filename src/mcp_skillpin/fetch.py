"""Download every file of a skill and verify it against its SEP-2640 entry."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any

from mcp_skillpin import frontmatter
from mcp_skillpin.client import SkillsClient
from mcp_skillpin.errors import SkillpinError
from mcp_skillpin.manifest import Entry, ResourceRef, set_digest


@dataclass
class SkillBundle:
    """A skill whose every byte matched the digests the server listed."""

    entry: Entry
    files: dict[str, bytes]  # relative path -> raw bytes
    set_digest: str

    @property
    def name(self) -> str:
        return self.entry.name

    @property
    def frontmatter(self) -> dict[str, Any]:
        return self.entry.frontmatter

    @property
    def refs(self) -> dict[str, ResourceRef]:
        return {r.rel: r for r in self.entry.resources}


def sha256_digest(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def fetch_bundle(client: SkillsClient, entry: Entry) -> SkillBundle:
    """Read all listed files. Nothing is returned unless every check passes."""
    listed = {r.uri for r in entry.resources}
    files: dict[str, bytes] = {}
    for ref in entry.resources:
        items = client.read_resource(ref.uri)
        for item_uri, _ in items:
            if item_uri is not None and item_uri not in listed:
                raise SkillpinError("unlisted-resource", uri=item_uri, skill=entry.name)
        match = [data for item_uri, data in items if item_uri in (None, ref.uri)]
        if len(match) != 1:
            raise SkillpinError("protocol-error", detail=f"expected one content item for {ref.uri}")
        data = match[0]
        if len(data) != ref.size:
            raise SkillpinError(
                "size-mismatch", uri=ref.uri, expected=ref.size, actual=len(data)
            )
        actual = sha256_digest(data)
        if actual != ref.digest:
            raise SkillpinError(
                "digest-mismatch", uri=ref.uri, expected=ref.digest, actual=actual
            )
        files[ref.rel] = data

    parsed = frontmatter.parse(files["SKILL.md"])
    if frontmatter.canonical(parsed) != frontmatter.canonical(entry.frontmatter):
        raise SkillpinError("frontmatter-mismatch", detail="entry frontmatter differs from SKILL.md")
    return SkillBundle(entry=entry, files=files, set_digest=set_digest(entry.resources))
