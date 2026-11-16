"""Error type with stable, language-independent codes and exit codes."""

from __future__ import annotations

from typing import Any

from mcp_skillpin import i18n

# Exit codes (documented in the README).
EXIT_OK = 0
EXIT_CHANGED = 1
EXIT_USAGE = 2
EXIT_VERIFY = 3
EXIT_GRANT = 4
EXIT_PROTOCOL = 5

# error code token -> exit code. The tokens are the same in every UI language.
CODE_EXIT: dict[str, int] = {
    # verification failures (SEP-2640 "Integrity and verification")
    "digest-mismatch": EXIT_VERIFY,
    "size-mismatch": EXIT_VERIFY,
    "unlisted-resource": EXIT_VERIFY,
    "frontmatter-mismatch": EXIT_VERIFY,
    "name-mismatch": EXIT_VERIFY,
    "unsafe-path": EXIT_VERIFY,
    "limit-exceeded": EXIT_VERIFY,
    "dynamic-refused": EXIT_VERIFY,
    "bad-entry": EXIT_VERIFY,
    # approval
    "grant-required": EXIT_GRANT,
    # connection / protocol
    "legacy-protocol": EXIT_PROTOCOL,
    "no-skills-extension": EXIT_PROTOCOL,
    "connect-failed": EXIT_PROTOCOL,
    "protocol-error": EXIT_PROTOCOL,
    "rpc-error": EXIT_PROTOCOL,
    "timeout": EXIT_PROTOCOL,
    "unsupported-transport": EXIT_PROTOCOL,
    # usage / configuration
    "local-collision": EXIT_USAGE,
    "target-changed": EXIT_USAGE,
    "local-edits": EXIT_USAGE,
    "server-not-found": EXIT_USAGE,
    "server-ambiguous": EXIT_USAGE,
    "skill-not-found": EXIT_USAGE,
    "no-lock": EXIT_USAGE,
    "no-servers": EXIT_USAGE,
    "bad-config": EXIT_USAGE,
    "bad-target": EXIT_USAGE,
    "bad-lock": EXIT_USAGE,
    "bad-grant": EXIT_USAGE,
    "usage": EXIT_USAGE,  # wrong command line (missing argument, unknown command, ...)
    "io-error": EXIT_USAGE,  # the local disk refused a read or write (permissions, full disk, ...)
}


class SkillpinError(Exception):
    """An expected failure. ``code`` is a fixed English token such as ``digest-mismatch``.

    ``message_key`` picks a more specific message than ``err.<code>`` while the token
    (and so the exit code and scripts that grep for it) stays the same.
    """

    def __init__(self, code: str, *, message_key: str | None = None, **params: Any) -> None:
        if code not in CODE_EXIT:
            raise ValueError(f"unknown error code: {code}")
        self.code = code
        self.params = params
        self.exit_code = CODE_EXIT[code]
        self.message_key = message_key or f"err.{code}"
        super().__init__(code)

    def message(self) -> str:
        return i18n.t(self.message_key, **self.params)

    def hint(self) -> str:
        """The hint line for this error ('' when there is none).

        A specific message ``err.x`` uses ``hint.x`` when it exists, otherwise ``hint.<code>``.
        """
        table = i18n.load_messages(i18n.current_language())
        for key in ("hint." + self.message_key.removeprefix("err."), f"hint.{self.code}"):
            if key in table:
                return i18n.t(key, **self.params)
        return ""

    def __str__(self) -> str:
        return f"error[{self.code}]: {self.message()}"
