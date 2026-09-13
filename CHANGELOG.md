# Changelog

## [0.2.0] - 2026-09-12

### Fixed
- Migrated to the `mcp` 2.0 API (`FastMCP` → `MCPServer`) and pinned `mcp>=2,<3`. The prior unbounded `mcp>=1.26.0` constraint had silently resolved onto `mcp` 2.0 and left the server dead.
- Removed a dead local-scope MCP registration that pointed at a bare `python3` invocation with no way to see the `mcp` package.
- `meme_server.py` now declares its dependency via a PEP 723 inline script block instead of a launcher `--with` flag, so `uv run` reuses a stable, cached environment instead of rebuilding one on every launch.
- `meme --install` resolves the server's actual interpreter path and registers Claude Code's MCP config against it directly, instead of routing every session through a `uv run` wrapper process. Re-running `--install` re-resolves the path, so a future dependency-pin bump self-heals instead of leaving a stale registration behind.

### Added
- A parent-death signal (`PR_SET_PDEATHSIG` on Linux) so an orphaned server process exits with the session that spawned it instead of leaking.
- A rotating diagnostics log at `~/.cache/groupchat/meme.log`.

### Changed
- Moved the meme roster and selection guidance out of the global `CLAUDE.md` injection and into the MCP tools themselves (`list_memes`, `meme_info`), cutting the always-loaded context cost.

## [0.1.0] - 2026-05-28

Initial public release.
