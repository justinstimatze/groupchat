# Feature request: shared MCP transport (stop spawning one meme server per Claude session)

Origin: conversation 2026-07-23, diagnosing sustained RAM/swap pressure (14GB box,
12GB used, actively swapping per `vmstat`) with 8 concurrent Claude Code sessions
open. Every MCP server in `~/.claude.json` is wired as `"type": "stdio"`, which is
a 1:1 pipe by protocol — 8 sessions means 8 processes per server. Same request
filed in sibling repos: `../plancheck`, `../buddy`, `../mcp-dispatch`.

## Why

This one's the clearest win of the four checked. `~/.claude.json`'s `meme` entry
runs `uv run --no-project --with mcp>=1.26.0 python meme_server.py`, and
`pgrep -af meme_server.py` with 8 sessions open shows **two** processes per
session — the `uv run` wrapper plus the actual Python interpreter it launches —
each pointed at its own fresh `~/.cache/uv/builds-v0/.tmpXXXXXX` directory. That's
16 processes for 8 sessions, and `uv` isn't reusing a resolved environment across
launches; it's building one from scratch every time (consistent with
`--no-project`, which opts out of the project-level cache reuse `uv` would
otherwise do).

There's no meaningful per-session state to protect here — `meme_server.py` reads
a fixed local roster (`meme/`, per the module layout) and writes nothing back
per call. The entire 16-process, 8x-rebuilt-environment cost is pure duplication
of something that could be one long-lived interpreter.

## Feature

- `meme_server.py:22,31,359` already uses `mcp.server.fastmcp.FastMCP` and calls
  bare `mcp.run()`. The `mcp>=1.26.0` SDK's `FastMCP.run()` accepts
  `transport="streamable-http"` (or `"sse"`) plus `host`/`port` kwargs directly —
  no new dependency, just a different call at line 359, gated behind an env var
  or CLI arg so a bare `python meme_server.py` still defaults to stdio.
- Run it once as a persistent process (a systemd `--user` unit, or a supervised
  background process) instead of relaunching it per Claude session; point
  `~/.claude.json`'s `meme` entry at `"type": "http", "url": "http://localhost:PORT"`.
- Independent of the transport question: consider dropping `--no-project` (or
  pre-warming a cached `uv` environment) even if the multiplexing change is
  deferred — the fresh-build-per-launch cost exists today regardless of how many
  sessions are open, it's just multiplied by session count right now.

## Risks / non-goals

- If a future version of this server grows per-call state (a running tally, a
  cooldown timer per caller), check that it isn't accidentally keyed by
  wall-clock/process lifetime in a way that silently breaks once one process
  serves multiple concurrent callers.
- Don't reach for an external proxy (`mcp-proxy`, `supergateway`) here — neither
  is installed on this box, and FastMCP's own transport switch makes a proxy
  hop unnecessary for a server this project already owns.

## Related

Same request, same day, three other repos:

- `../plancheck` — same shape, smallest individual memory footprint of the
  four, lowest urgency.
- `../buddy` — SQLite-backed state (`~/.buddy/buddy.db`), moderate memory win,
  arguably fixes a correctness question too (one companion vs. 8 uncoordinated
  ones).
- `../mcp-dispatch` — the one exception. Agent identity there is generated
  per-process (`<project-dir-name>-<pid>`), which is real per-session state.
  Needs a multi-tenant redesign, not a transport swap.
