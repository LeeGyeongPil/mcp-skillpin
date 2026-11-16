"""The seven sub-commands. Each returns a JSON-able result and prints text through ``Context.say``."""

from __future__ import annotations

import json
import unicodedata
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TextIO

from mcp_skillpin import config, diff, frontmatter, install, lockfile
from mcp_skillpin.client import SkillsClient
from mcp_skillpin.errors import EXIT_CHANGED, EXIT_OK, SkillpinError
from mcp_skillpin.fetch import SkillBundle, fetch_bundle
from mcp_skillpin.i18n import t
from mcp_skillpin.manifest import Entry, parse_entry, set_digest
from mcp_skillpin.transport import HttpTransport, StdioTransport, build_environment


@dataclass
class Context:
    cwd: Path
    home: Path
    environ: Mapping[str, str]
    lock_path: Path
    timeout: float
    as_json: bool
    out: TextIO
    err: TextIO
    results: list[Any] = field(default_factory=list)

    def say(self, text: str = "") -> None:
        if not self.as_json:
            print(text, file=self.out)


_CHECK_KEY = {"added": "check.added", "removed": "check.removed", "changed": "check.changed_files"}


# -- helpers ---------------------------------------------------------------
def _width(text: str) -> int:
    return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in text)


def _table(ctx: Context, header: list[str], rows: list[list[str]]) -> None:
    widths = [max(_width(r[i]) for r in [header, *rows]) for i in range(len(header))]
    for row in [header, *rows]:
        cells = [c + " " * (widths[i] - _width(c)) for i, c in enumerate(row)]
        ctx.say("  ".join(cells).rstrip())


def _human_size(n: int) -> str:
    return f"{n} B" if n < 1024 else f"{n / 1024:.1f} KiB" if n < 1024 * 1024 else f"{n / 1048576:.1f} MiB"


def servers_of(ctx: Context) -> list[config.ServerConfig]:
    return config.discover_servers(ctx.cwd, ctx.home, ctx.environ)


@contextmanager
def open_client(ctx: Context, cfg: config.ServerConfig) -> Iterator[SkillsClient]:
    """Connect, run ``server/discover`` and yield a ready client; always clean up."""
    if cfg.transport == "stdio" and cfg.command:
        env = build_environment(ctx.environ, cfg.env)
        transport: StdioTransport | HttpTransport = StdioTransport(
            cfg.command, cfg.args, env, cfg.cwd or str(ctx.cwd), ctx.timeout
        )
    elif cfg.transport == "http" and cfg.url:
        transport = HttpTransport(cfg.url, cfg.headers, ctx.timeout)
    else:
        raise SkillpinError("unsupported-transport", server=cfg.label, kind=cfg.transport)
    client = SkillsClient(transport)
    try:
        client.discover()
        yield client
    finally:
        client.close()


def _skill_name_from_uri(uri: str) -> str:
    base = uri[: -len("/SKILL.md")] if uri.endswith("/SKILL.md") else uri
    return base.rstrip("/").rsplit("/", 1)[-1]


def _raw_name(raw: Any) -> str:
    if isinstance(raw, dict) and isinstance(raw.get("uri"), str):
        return _skill_name_from_uri(raw["uri"])
    return "?"


def _resolve_entry(client: SkillsClient, ident: str, server: str) -> dict[str, Any]:
    listed = client.list_skills()
    for raw in listed:
        if not isinstance(raw, dict):
            continue
        fm = raw.get("frontmatter") if isinstance(raw.get("frontmatter"), dict) else {}
        if ident in (raw.get("uri"), fm.get("name"), _raw_name(raw)):
            return raw
    if "://" in ident:  # skills/get also serves skills that are not in the listing
        try:
            return client.get_skill(ident)
        except SkillpinError as exc:
            if exc.code != "rpc-error":
                raise
    known = ", ".join(sorted(_raw_name(r) for r in listed)) or "-"
    raise SkillpinError("skill-not-found", name=ident, server=server, known=known)


def _risk_line(risk: diff.Risk) -> str:
    return t(f"risk.{risk.code}", path=risk.path)


def _locked_files(rec: dict[str, Any]) -> dict[str, dict[str, Any]]:
    files = rec.get("files")
    return files if isinstance(files, dict) else {}


def _pending_grants(
    fm: dict[str, Any], locked: dict[str, str], requested: set[str]
) -> tuple[list[str], dict[str, str]]:
    """Return (grants still needed, grants to record)."""
    missing: list[str] = []
    record: dict[str, str] = {}
    for key, value in frontmatter.grant_values(fm).items():
        if key in requested or locked.get(key) == value:
            record[key] = value
        else:
            missing.append(key)
    return missing, record


def _lock_record(
    ctx: Context, cfg: config.ServerConfig, bundle: SkillBundle, dest: Path, grants: dict[str, str]
) -> dict[str, Any]:
    return {
        "server": cfg.label,
        "source": f"{cfg.agent}:{cfg.scope}",
        "uri": bundle.entry.uri,
        "target": lockfile.store_path(dest, ctx.lock_path.parent, ctx.home),
        "files": {
            r.rel: {"uri": r.uri, "digest": r.digest, "size": r.size} for r in bundle.entry.resources
        },
        "setDigest": bundle.set_digest,
        "grants": grants,
        "approvedAt": lockfile.now_iso(dict(ctx.environ)),
    }


# -- servers ---------------------------------------------------------------
def cmd_servers(ctx: Context, probe: bool) -> int:
    servers = servers_of(ctx)
    if not servers:
        raise SkillpinError("no-servers", cwd=str(ctx.cwd))
    rows: list[list[str]] = []
    result = []
    for cfg in servers:
        status = "-"
        if probe:
            try:
                with open_client(ctx, cfg):
                    status = "yes"
            except SkillpinError as exc:
                status = {"no-skills-extension": "no", "legacy-protocol": "legacy"}.get(exc.code, "error")
        note = f" ({cfg.note})" if cfg.note else ""
        rows.append([cfg.label, cfg.agent, cfg.scope, cfg.transport, status, cfg.endpoint() + note])
        result.append({
            "label": cfg.label, "agent": cfg.agent, "scope": cfg.scope, "transport": cfg.transport,
            "skills": status, "endpoint": cfg.endpoint(), "source": cfg.source,
        })
    _table(ctx, [t("col.label"), t("col.agent"), t("col.scope"), t("col.transport"), t("col.skills"),
                 t("col.endpoint")], rows)
    ctx.results.append(result)
    return EXIT_OK


# -- list ------------------------------------------------------------------
def cmd_list(ctx: Context, server: str) -> int:
    cfg = config.find_server(servers_of(ctx), server)
    with open_client(ctx, cfg) as client:
        raws = client.list_skills()
    rows: list[list[str]] = []
    result = []
    for raw in raws:
        name = _raw_name(raw)
        try:
            entry = parse_entry(raw)
        except SkillpinError as exc:
            desc = (raw.get("frontmatter") or {}).get("description", "") if isinstance(raw, dict) else ""
            rows.append([name, "-", "-", f"error[{exc.code}]", str(desc)[:60]])
            result.append({"name": name, "error": exc.code})
            continue
        risks = diff.path_risks_for_listing(entry.resources)
        total = sum(r.size for r in entry.resources)
        desc = str(entry.frontmatter.get("description", ""))
        rows.append([name, str(len(entry.resources)), _human_size(total),
                     ",".join(sorted({r.code for r in risks})) or "-", desc[:60]])
        result.append({
            "name": name, "uri": entry.uri, "files": len(entry.resources), "bytes": total,
            "risks": [{"code": r.code, "path": r.path} for r in risks], "description": desc,
        })
    ctx.say(t("list.title", server=cfg.label, count=len(raws)))
    if rows:
        _table(ctx, [t("col.name"), t("col.files"), t("col.size"), t("col.risk"), t("col.description")], rows)
    ctx.results.append(result)
    return EXIT_OK


# -- add -------------------------------------------------------------------
def cmd_add(
    ctx: Context, server: str, skill: str, target: str, yes: bool, grants: set[str],
    discard_local: bool = False,
) -> int:
    cfg = config.find_server(servers_of(ctx), server)
    with open_client(ctx, cfg) as client:
        entry = parse_entry(_resolve_entry(client, skill, cfg.label))
        bundle = fetch_bundle(client, entry)  # every byte verified before anything is written

    lock = lockfile.load(ctx.lock_path)
    key = lockfile.skill_key(cfg.label, bundle.name)
    old = lock["skills"].get(key)
    root = install.resolve_target(target, ctx.cwd, ctx.home)
    dest = root / bundle.name
    known = lockfile.load_path(old["target"], ctx.lock_path.parent, ctx.home) if old else None
    install.check_collision(dest, known)
    local_edits: list[str] = []
    if old and dest.is_dir():
        edited, _gone, extra = install.verify_installed(dest, _locked_files(old))
        local_edits = edited + extra
    missing, record = _pending_grants(bundle.frontmatter, (old or {}).get("grants", {}), grants)
    risks = diff.content_risks(bundle.files, None, None, bundle.frontmatter)

    ctx.say(t("add.plan_title", name=bundle.name, server=cfg.label, dest=dest))
    for rel in sorted(bundle.files):
        ctx.say(f"  + {rel} ({_human_size(len(bundle.files[rel]))})")
    for risk in risks:
        ctx.say(_risk_line(risk))
    for key_name in missing:
        ctx.say(t("add.grant_needed", key=key_name))
    if local_edits:
        ctx.say(t("update.local_edits", files=", ".join(local_edits)))
    result: dict[str, Any] = {
        "skill": bundle.name, "server": cfg.label, "dest": str(dest), "installed": False,
        "files": sorted(bundle.files), "risks": [{"code": r.code, "path": r.path} for r in risks],
        "grantsNeeded": missing, "localEdits": local_edits,
    }
    if not yes:
        ctx.say(t("add.dry_run"))
        ctx.results.append(result)
        return EXIT_OK
    if missing:
        raise SkillpinError("grant-required", keys=", ".join(missing), name=bundle.name)
    if local_edits and not discard_local:
        # Nothing has been written yet: the folder and the lock file stay as they are.
        raise SkillpinError("local-edits", name=bundle.name, files=", ".join(local_edits))
    install.install(bundle, root, cfg.label)
    lock["skills"][key] = _lock_record(ctx, cfg, bundle, dest, record)
    lockfile.save(ctx.lock_path, lock)
    ctx.say(t("add.installed", name=bundle.name, count=len(bundle.files), dest=dest))
    ctx.say(t("add.locked", lock=ctx.lock_path.name))
    result["installed"] = True
    ctx.results.append(result)
    return EXIT_OK


# -- check / update --------------------------------------------------------
def _select_records(lock: dict[str, Any], names: list[str]) -> list[tuple[str, dict[str, Any]]]:
    items = sorted(lock["skills"].items())
    if not names:
        return items
    chosen = []
    for want in names:
        hits = [(k, r) for k, r in items if want == k or want == k.rsplit("/", 1)[-1]]
        if not hits:
            known = ", ".join(k.rsplit("/", 1)[-1] for k, _ in items) or "-"
            raise SkillpinError("skill-not-found", name=want, server="lock", known=known)
        chosen.extend(h for h in hits if h not in chosen)
    return chosen


def _by_server(records: list[tuple[str, dict[str, Any]]]) -> dict[str, list[tuple[str, dict[str, Any]]]]:
    groups: dict[str, list[tuple[str, dict[str, Any]]]] = {}
    for key, rec in records:
        groups.setdefault(str(rec.get("server")), []).append((key, rec))
    return groups


def _fetch_current(client: SkillsClient, rec: dict[str, Any]) -> Entry | None:
    """The server's current entry for a locked skill, or None if it no longer serves it."""
    try:
        return parse_entry(client.get_skill(rec["uri"]))
    except SkillpinError as exc:
        if exc.code == "rpc-error":
            return None
        raise


def cmd_check(ctx: Context, names: list[str]) -> int:
    lock = lockfile.load(ctx.lock_path, required=True)
    servers = servers_of(ctx)
    exit_code = EXIT_OK
    result = []
    for label, group in _by_server(_select_records(lock, names)).items():
        cfg = config.find_server(servers, label)
        with open_client(ctx, cfg) as client:
            for key, rec in group:
                name = key.rsplit("/", 1)[-1]
                entry = _fetch_current(client, rec)
                if entry is None:
                    ctx.say(t("check.missing", name=name))
                    result.append({"skill": name, "server": label, "status": "missing"})
                    exit_code = EXIT_CHANGED
                    continue
                if set_digest(entry.resources) == rec["setDigest"]:
                    ctx.say(t("check.unchanged", name=name))
                    result.append({"skill": name, "server": label, "status": "unchanged"})
                    continue
                change = diff.compare(_locked_files(rec), entry.resources)
                parts = [t("check.changed", name=name)]
                for word, files in (("added", change.added), ("removed", change.removed), ("changed", change.changed)):
                    if files:
                        parts.append(t(_CHECK_KEY[word], files=", ".join(files)))
                ctx.say("  ".join(parts))
                result.append({"skill": name, "server": label, "status": "changed", "added": change.added,
                               "removed": change.removed, "changed": change.changed})
                exit_code = EXIT_CHANGED
    ctx.results.append(result)
    return exit_code


def cmd_update(
    ctx: Context, names: list[str], yes: bool, grants: set[str], discard_local: bool = False
) -> int:
    lock = lockfile.load(ctx.lock_path, required=True)
    servers = servers_of(ctx)
    result = []
    for label, group in _by_server(_select_records(lock, names)).items():
        cfg = config.find_server(servers, label)
        with open_client(ctx, cfg) as client:
            for key, rec in group:
                name = key.rsplit("/", 1)[-1]
                entry = _fetch_current(client, rec)
                if entry is None:
                    ctx.say(t("check.missing", name=name))
                    result.append({"skill": name, "status": "missing"})
                    continue
                if set_digest(entry.resources) == rec["setDigest"]:
                    ctx.say(t("check.unchanged", name=name))
                    result.append({"skill": name, "status": "unchanged"})
                    continue
                bundle = fetch_bundle(client, entry)
                result.append(_review_and_apply(ctx, cfg, lock, key, rec, bundle, yes, grants, discard_local))
    ctx.results.append(result)
    return EXIT_OK


def _review_and_apply(
    ctx: Context, cfg: config.ServerConfig, lock: dict[str, Any], key: str, rec: dict[str, Any],
    bundle: SkillBundle, yes: bool, grants: set[str], discard_local: bool = False,
) -> dict[str, Any]:
    name = bundle.name
    locked_files = _locked_files(rec)
    dest = lockfile.load_path(rec["target"], ctx.lock_path.parent, ctx.home)
    change = diff.compare(locked_files, bundle.entry.resources)
    # Old bytes come from the installed folder, but only when they still match the lock.
    installed = install.hash_tree(dest)
    old_fm: dict[str, Any] | None = None
    old_bytes: dict[str, bytes] = {}
    for rel, meta in locked_files.items():
        path = dest / rel
        if rel in installed and installed[rel][0] == meta["digest"]:
            old_bytes[rel] = path.read_bytes()
    if "SKILL.md" in old_bytes:
        try:
            old_fm = frontmatter.parse(old_bytes["SKILL.md"])
        except SkillpinError:
            old_fm = None

    edited, _gone, extra = install.verify_installed(dest, locked_files)
    local_edits = edited + extra
    ctx.say(t("update.header", name=name, server=cfg.label))
    if local_edits:
        ctx.say(t("update.local_edits", files=", ".join(local_edits)))
    for word, files in (("added", change.added), ("removed", change.removed), ("changed", change.changed)):
        if files:
            ctx.say("  " + t(_CHECK_KEY[word], files=", ".join(files)))
    for rel in change.changed + change.added:
        lines = diff.unified_diff(rel, old_bytes.get(rel) if rel in change.changed else None, bundle.files[rel])
        if lines:
            ctx.say("\n".join(lines))
        elif not diff.is_text(bundle.files[rel]):
            data = bundle.files[rel]
            ctx.say(t("update.binary", path=rel, size=len(data), digest=bundle.refs[rel].digest))
        if rel in change.changed and rel not in old_bytes:
            ctx.say(t("update.no_old", path=rel))
    risks = diff.content_risks(bundle.files, change, old_fm, bundle.frontmatter)
    for risk in risks:
        ctx.say(_risk_line(risk))
    missing, record = _pending_grants(bundle.frontmatter, rec.get("grants", {}), grants)
    for grant_key in missing:
        ctx.say(t("add.grant_needed", key=grant_key))
    out = {"skill": name, "status": "changed", "applied": False, "added": change.added,
           "removed": change.removed, "changed": change.changed,
           "risks": [{"code": r.code, "path": r.path} for r in risks], "grantsNeeded": missing}
    if not yes:
        ctx.say(t("update.dry_run"))
        return out
    if missing:
        raise SkillpinError("grant-required", keys=", ".join(missing), name=name)
    if local_edits and not discard_local:
        raise SkillpinError("local-edits", name=name, files=", ".join(local_edits))
    install.install(bundle, dest.parent, cfg.label)
    lock["skills"][key] = _lock_record(ctx, cfg, bundle, dest, record)
    lockfile.save(ctx.lock_path, lock)
    ctx.say(t("update.applied", name=name, lock=ctx.lock_path.name))
    out["applied"] = True
    return out


# -- verify ----------------------------------------------------------------
def cmd_verify(ctx: Context) -> int:
    lock = lockfile.load(ctx.lock_path, required=True)
    exit_code = EXIT_OK
    result = []
    for key, rec in sorted(lock["skills"].items()):
        name = key.rsplit("/", 1)[-1]
        dest = lockfile.load_path(rec["target"], ctx.lock_path.parent, ctx.home)
        modified, missing, extra = install.verify_installed(dest, _locked_files(rec))
        if not (modified or missing or extra):
            ctx.say(t("verify.ok", name=name))
            result.append({"skill": name, "status": "ok"})
            continue
        parts = [t("verify.bad", name=name)]
        for word, files in (("modified", modified), ("missing", missing), ("extra", extra)):
            if files:
                parts.append(t(f"verify.{word}", files=", ".join(files)))
        ctx.say("  ".join(parts))
        result.append({"skill": name, "status": "modified", "modified": modified,
                       "missing": missing, "extra": extra})
        exit_code = EXIT_CHANGED
    ctx.results.append(result)
    return exit_code


def json_dump(value: Any) -> str:
    return json.dumps(value, indent=2, ensure_ascii=False)
