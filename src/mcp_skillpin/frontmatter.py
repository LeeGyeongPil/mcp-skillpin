"""SKILL.md frontmatter parsing."""

from __future__ import annotations

import json
import re
from typing import Any

import yaml

from mcp_skillpin.errors import SkillpinError

# Frontmatter keys that widen what a skill may do. They need an explicit --grant.
GRANT_KEYS = ("allowed-tools", "hooks")

_FM = re.compile(r"\A﻿?---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|\Z)", re.DOTALL)


def parse(data: bytes) -> dict[str, Any]:
    """Parse the YAML frontmatter of a SKILL.md given as raw bytes."""
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SkillpinError("frontmatter-mismatch", detail="SKILL.md is not valid UTF-8") from exc
    match = _FM.match(text)
    if not match:
        raise SkillpinError("frontmatter-mismatch", detail="SKILL.md has no YAML frontmatter")
    try:
        loaded = yaml.safe_load(match.group(1))
    except yaml.YAMLError as exc:
        raise SkillpinError("frontmatter-mismatch", detail=f"invalid YAML: {exc}") from exc
    if not isinstance(loaded, dict):
        raise SkillpinError("frontmatter-mismatch", detail="frontmatter is not a mapping")
    return loaded


def canonical(value: Any) -> str:
    """Stable string form used to compare two frontmatter mappings field by field."""
    return json.dumps(value, sort_keys=True, ensure_ascii=False, default=str, separators=(",", ":"))


def grant_values(frontmatter: dict[str, Any]) -> dict[str, str]:
    """Return {key: canonical value} for each grant-requiring key that is present."""
    return {k: canonical(frontmatter[k]) for k in GRANT_KEYS if k in frontmatter}
