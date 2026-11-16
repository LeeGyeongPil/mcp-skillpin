# Changelog

All notable changes to this project are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses [Semantic Versioning](https://semver.org/).

## [0.1.0] - 2026-11-16

### Added
- `servers`, `list`, `add`, `check`, `update`, `verify` and `demo` commands.
- Client for the stateless MCP 2026-07-28 protocol (`server/discover`, per-request `_meta`) and the Skills Extension (SEP-2640): `skills/list` with cursors, `skills/get`, `resources/read` (text and blob).
- stdio and Streamable HTTP transports (JSON and SSE replies, `Mcp-Method` / `Mcp-Name` headers).
- Server discovery in `.mcp.json`, `~/.claude.json`, `~/.codex/config.toml` and `.codex/config.toml`.
- Verification of file size, SHA-256, frontmatter, name, path safety and the 512 files / 16 MiB limits before anything is written.
- Atomic install into Claude Code and Codex skill folders, `mcp-skills.lock.json` pinning, diff and risk signals on change, explicit `--grant` for `allowed-tools` and `hooks`.
- English and Korean screens (`--lang`, `MCP_SKILLPIN_LANG`, `LC_ALL` / `LC_MESSAGES` / `LANG`).
- Practice mock MCP server (`python -m mcp_skillpin.mockserver`) with stdio, HTTP, SSE, pagination, legacy and tamper modes.
- Install-time safety: files that differ only by letter case or Unicode form, a file that is also a folder, two spellings of one folder and the reserved `.mcp-origin.json` are refused with `error[unsafe-path]` before anything is written; staging files are created with `O_EXCL`.
- `add` into a second target stops with `error[target-changed]`; `update --yes` refuses to overwrite local edits unless `--discard-local` is given.
- Fully translated Korean `--help` and usage errors (`error[usage]`), and `error[io-error]` instead of a traceback when the local disk refuses access.
