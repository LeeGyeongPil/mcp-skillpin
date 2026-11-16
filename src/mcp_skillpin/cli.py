"""Command line entry point: argument parsing, language selection, exit codes."""

from __future__ import annotations

import argparse
import os
import re
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, TextIO

from mcp_skillpin import __version__, i18n, lockfile
from mcp_skillpin.commands import (
    Context, cmd_add, cmd_check, cmd_list, cmd_servers, cmd_update, cmd_verify, json_dump,
)
from mcp_skillpin.errors import SkillpinError
from mcp_skillpin.frontmatter import GRANT_KEYS
from mcp_skillpin.i18n import t

DEFAULT_TIMEOUT = 30.0


def _prescan_lang(argv: Sequence[str]) -> str | None:
    """Find ``--lang`` before the real parser exists (help text must already be translated)."""
    for i, arg in enumerate(argv):
        if arg == "--lang" and i + 1 < len(argv):
            return argv[i + 1]
        if arg.startswith("--lang="):
            return arg.split("=", 1)[1]
    return None


class _Formatter(argparse.HelpFormatter):
    """Help formatter whose 'usage:' prefix comes from the message table."""

    def add_usage(self, usage, actions, groups, prefix=None):  # type: ignore[no-untyped-def]
        return super().add_usage(usage, actions, groups, t("help.usage") if prefix is None else prefix)


# argparse writes its error text in English from fixed templates (its gettext msgids, see
# argparse.py). Each pattern below matches one template; the message key renders it in the
# UI language. "argument X: ..." wraps another message, which is translated in turn.
# Anything not listed here (rare argparse messages) is shown as argparse wrote it.
_ARGPARSE_MESSAGES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"the following arguments are required: (?P<names>.+)"), "usage.required"),
    (re.compile(r"unrecognized arguments: (?P<args>.+)"), "usage.unrecognized"),
    (re.compile(r"ambiguous option: (?P<option>\S+) could match (?P<matches>.+)"), "usage.ambiguous"),
    # Python 3.14+ may add a suggestion: "invalid choice: 'lsit', maybe you meant 'list'? (...)"
    (re.compile(r"invalid choice: (?P<value>.+?), maybe you meant (?P<closest>.+?)\? \(choose from (?P<choices>.+)\)"),
     "usage.invalid_choice_closest"),
    (re.compile(r"invalid choice: (?P<value>.+?) \(choose from (?P<choices>.+)\)"), "usage.invalid_choice"),
    (re.compile(r"expected one argument"), "usage.expected_one"),
    (re.compile(r"invalid (?P<type>\S+) value: (?P<value>.+)"), "usage.invalid_value"),
    (re.compile(r"ignored explicit argument (?P<value>.+)"), "usage.ignored_value"),
    (re.compile(r"argument (?P<name>.+?): (?P<detail>.+)"), "usage.argument"),
]


def translate_argparse_message(message: str) -> str:
    """Render one argparse error message in the current UI language."""
    for pattern, key in _ARGPARSE_MESSAGES:
        match = pattern.fullmatch(message)
        if match:
            params = match.groupdict()
            if key == "usage.argument":
                params["detail"] = translate_argparse_message(params["detail"])
            return t(key, **params)
    return message


class _Parser(argparse.ArgumentParser):
    """ArgumentParser with translated built-in strings (-h text, group titles, errors).

    ``out``/``err`` are the streams main() was given, so help and usage errors land where
    the rest of the output goes (tests pass StringIO objects).
    """

    def __init__(self, *, out: TextIO | None = None, err: TextIO | None = None, **kwargs: Any) -> None:
        kwargs.setdefault("formatter_class", _Formatter)
        super().__init__(add_help=False, **kwargs)
        self._out = out
        self._err = err
        self._positionals.title = t("help.positional")
        self._optionals.title = t("help.optional")
        self.add_argument("-h", "--help", action="help", default=argparse.SUPPRESS, help=t("help.help"))

    def print_help(self, file: TextIO | None = None) -> None:  # type: ignore[override]
        super().print_help(file or self._out or sys.stdout)

    def error(self, message: str) -> Any:  # type: ignore[override]
        """Show the usage line, then raise ``error[usage]`` (exit 2) in the UI language."""
        self.print_usage(self._err or sys.stderr)
        raise SkillpinError("usage", detail=translate_argparse_message(message), prog=self.prog)


def build_parser(out: TextIO | None = None, err: TextIO | None = None) -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    group = common.add_argument_group(t("help.common"))
    group.add_argument("--lang", choices=i18n.LANGUAGES, default=argparse.SUPPRESS, help=t("help.lang"))
    group.add_argument("--lock", metavar="PATH", default=argparse.SUPPRESS, help=t("help.lock"))
    group.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help=t("help.json"))
    group.add_argument("--timeout", type=float, metavar="SECONDS", default=argparse.SUPPRESS,
                       help=t("help.timeout"))

    parser = _Parser(
        prog="mcp-skillpin", description=t("help.description"), epilog=t("help.epilog"), parents=[common],
        out=out, err=err,
    )
    parser.add_argument("--version", action="version", version=f"mcp-skillpin {__version__}",
                        help=t("help.version"))
    sub = parser.add_subparsers(
        dest="command", metavar="<command>", title=t("help.commands"), parser_class=_Parser
    )

    def add(name: str, help_key: str) -> argparse.ArgumentParser:
        # extra keyword arguments reach _Parser(...), so sub-commands report errors the same way
        return sub.add_parser(name, help=t(help_key), description=t(help_key), parents=[common],
                              out=out, err=err)

    p = add("servers", "help.servers")
    p.add_argument("--probe", action="store_true", help=t("help.probe"))
    p = add("list", "help.list")
    p.add_argument("server", help=t("help.server"))
    p = add("add", "help.add")
    p.add_argument("server", help=t("help.server"))
    p.add_argument("skill", help=t("help.skill"))
    p.add_argument("--target", required=True, metavar="T", help=t("help.target"))
    p.add_argument("--yes", action="store_true", help=t("help.yes"))
    p.add_argument("--grant", action="append", choices=GRANT_KEYS, default=[], help=t("help.grant"))
    p = add("check", "help.check")
    p.add_argument("names", nargs="*", metavar="name", help=t("help.names"))
    p = add("update", "help.update")
    p.add_argument("names", nargs="*", metavar="name", help=t("help.names"))
    p.add_argument("--yes", action="store_true", help=t("help.yes_update"))
    p.add_argument("--grant", action="append", choices=GRANT_KEYS, default=[], help=t("help.grant"))
    p.add_argument("--discard-local", action="store_true", help=t("help.discard_local"))
    add("verify", "help.verify")
    add("demo", "help.demo")
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    cwd: Path | None = None,
    home: Path | None = None,
    environ: Mapping[str, str] | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    """Run the CLI and return the exit code (never raises for expected failures)."""
    args_in = list(sys.argv[1:] if argv is None else argv)
    env = dict(os.environ if environ is None else environ)
    out = stdout or sys.stdout
    err = stderr or sys.stderr
    i18n.set_language(i18n.resolve_language(_prescan_lang(args_in), env))

    parser = build_parser(out, err)
    try:
        ns = parser.parse_args(args_in)
    except SkillpinError as exc:  # error[usage]: _Parser.error already printed the usage line
        return _report(exc, err)
    except SystemExit as exc:  # --help / --version printed their text
        code = exc.code
        return code if isinstance(code, int) else 2
    if not ns.command:
        parser.print_help(out)
        return 2

    work = (cwd or Path.cwd()).resolve()
    user_home = (home or Path(env.get("HOME") or Path.home())).resolve()
    lock_arg = getattr(ns, "lock", None)
    lock_path = Path(lock_arg).expanduser() if lock_arg else work / lockfile.DEFAULT_NAME
    if not lock_path.is_absolute():
        lock_path = work / lock_path
    ctx = Context(
        cwd=work, home=user_home, environ=env, lock_path=lock_path,
        timeout=float(getattr(ns, "timeout", DEFAULT_TIMEOUT)),
        as_json=bool(getattr(ns, "json", False)), out=out, err=err,
    )
    try:
        if ns.command == "servers":
            code = cmd_servers(ctx, ns.probe)
        elif ns.command == "list":
            code = cmd_list(ctx, ns.server)
        elif ns.command == "add":
            code = cmd_add(ctx, ns.server, ns.skill, ns.target, ns.yes, set(ns.grant))
        elif ns.command == "check":
            code = cmd_check(ctx, ns.names)
        elif ns.command == "update":
            code = cmd_update(ctx, ns.names, ns.yes, set(ns.grant), ns.discard_local)
        elif ns.command == "verify":
            code = cmd_verify(ctx)
        else:  # demo
            from mcp_skillpin.demo import run_demo

            code = run_demo(ctx)
    except SkillpinError as exc:
        return _report(exc, err)
    except OSError as exc:
        # The local disk said no (permission denied, read-only, full, ...). Report it like every
        # other expected failure instead of a traceback with Python's exit code 1 ("changes found").
        where = exc.filename if exc.filename is not None else "-"
        return _report(SkillpinError("io-error", path=str(where), detail=exc.strerror or str(exc)), err)
    except KeyboardInterrupt:
        return 130
    if ctx.as_json and ctx.results:
        print(json_dump(ctx.results[-1]), file=out)
    return code


def _report(exc: SkillpinError, err: TextIO) -> int:
    """Print ``error[code]: message`` and the hint line (if any); return the exit code."""
    print(str(exc), file=err)
    hint = exc.hint()
    if hint:
        print(hint, file=err)
    return exc.exit_code


def main_entry() -> None:
    sys.exit(main())
