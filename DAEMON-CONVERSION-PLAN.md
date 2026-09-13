# Daemon conversion plan: one persistent meme server instead of one per session

**Status: superseded, no daemon built.** This plan's RSS numbers overstated
the real cost — six Python processes share `libpython`/`cryptography`/
`pydantic_core`'s `.so` text pages, so RSS double-counted memory that isn't
duplicated. A `/proc/PID/smaps_rollup` (PSS) re-measurement found the actual
per-session server cost was ~4.5MB, not ~96MB; the `uv run` wrapper carried
the real weight, at ~25MB private. Fixed in commit `acd4ae7` by registering
Claude Code's MCP config against the resolved venv interpreter directly,
skipping the wrapper — no daemon, shim, or rewrite needed. The `/dev/tty`
analysis in §3 below is unaffected by this and still holds if a shared
daemon ever becomes necessary for some other reason.

Research-and-plan only — no code changed, nothing restarted. Written 2026-09-12
while 5 Claude Code sessions were live on this host (5GB into swap), as the
#1-ranked conversion target of 10 MCP servers surveyed in parallel (this server
measured heaviest per-session: ~96MB RSS, `uv run` wrapper + child interpreter).
Siblings covering mcp-dispatch, plancheck, onsetter, bmg, gozim, lexicon, buddy,
hindcast, winze are separate research threads — not touched here.

This supersedes the transport-mechanics half of `FEATURE_REQUEST_shared-mcp-transport.md`
(2026-07-23), which is still directionally correct but predates the mcp 2.0
rename (`FastMCP` → `MCPServer`, `mcp.server.fastmcp` → `mcp.server.mcpserver`,
fixed in `meme_server.py:22-28` on 2026-08-05) and — more importantly — never
looked at the `/dev/tty` rendering path at all. That turns out to be the actual
blocker, not the transport swap.

## 1. Current architecture

`meme_server.py` is a single-file MCP server on `mcp>=2,<3`'s `MCPServer`
(`mcp.server.mcpserver.MCPServer`, still the FastMCP-lineage API — decorator
and `run()` shape unchanged from the 1.x `FastMCP` class it was renamed from).
It's launched per Claude Code session as a stdio child:

```
uv run --no-project --with mcp>=2,<3 python /home/gas6amus/Documents/groupchat/meme_server.py
```

Two processes per session (confirmed live, 5 sessions running now):
the `uv run` wrapper (30-53MB RSS observed) plus the actual interpreter
`uv` execs under a fresh `~/.cache/uv/builds-v0/.tmpXXXXXX` env (10-47MB
RSS observed). `uv run --no-project` re-resolves the environment from
scratch on every launch — no cross-launch cache reuse.

Three tools (`meme_server.py:353-442`):

- `drop_meme(name)` — looks up the meme, checks/records cooldown, fetches
  and caches the GIF, spawns a daemon thread that sleeps 6s then writes
  braille art to `/dev/tty`, returns immediately (text response only,
  never the render itself — that's the whole point: invisible to Claude's
  context).
- `meme_info(name)` — one meme's metadata, ~100 tokens.
- `list_memes()` — full roster + legend + worked examples, ~2k tokens.

### State inventory — what's per-process vs. already-shared

| State | Location | Scope today | Scope as daemon |
|---|---|---|---|
| `_db_cache` (meme roster) | in-memory list, `meme_server.py:61-71` | per-process, invalidated only by process restart | per-daemon — same semantics, just one invalidation point instead of N |
| `_db_lock` | `threading.Lock`, per-process | trivially uncontended today (1 thread of interest per process) | now actually load-bearing — guards concurrent tool calls from different sessions hitting the same daemon; the lock is already correct for this, no code change needed |
| cooldown timestamps | `~/.cache/groupchat/drops.json` | **already global** — every stdio process reads/writes the same file today, so cooldown state is already shared across all concurrent sessions | unchanged; daemon just becomes the one writer instead of N racing writers (a strict improvement — no code change needed) |
| GIF cache | `~/.cache/groupchat/memes/*.gif` | already global (shared disk path) | unchanged |
| log file | `~/.cache/groupchat/meme.log` | already global; the rotate-on-overflow read/truncate/rewrite at `meme_server.py:45-57` has an unguarded race today across N processes | unchanged risk profile, but with 1 writer instead of N the race window closes for free |

So the `FEATURE_REQUEST` doc's stated risk ("if this server grows per-call
state... check it isn't keyed by process lifetime") turns out to be a
non-issue: the only genuinely per-process state is the roster cache, and
that degrades gracefully (worst case: a `meme --add` needs the daemon
restarted once, vs. today needing every open Claude Code session restarted
to pick up a new meme).

## 2. Transport conversion — confirmed against the actual installed SDK

Checked directly rather than assumed (`uv run --no-project --with 'mcp>=2,<3' python3` +
`inspect.signature`, since the repo's own `.venv` doesn't have `mcp` installed
and the server only ever runs under `uv`'s ephemeral env):

```
MCPServer.run(self, transport: Literal['stdio','sse','streamable-http'] = 'stdio', **kwargs) -> None
```

`transport="streamable-http"` dispatches to `run_streamable_http_async(host="127.0.0.1", port=8000, streamable_http_path="/mcp", ...)`
— all keyword, all with defaults, already present in the pinned SDK version.
No new dependency. The only change needed is at `meme_server.py:445-447`:

```python
if __name__ == "__main__":
    _install_parent_death_signal()
    mcp.run()
```

gate this on an env var or CLI flag (e.g. `MEME_TRANSPORT=streamable-http`)
so bare `python meme_server.py` still defaults to stdio for local dev/testing,
and the daemon's supervised launch command passes the flag.

**Gotcha found while reading the source, not guessed:** `_install_parent_death_signal()`
(`meme_server.py:236-264`) exists specifically for the stdio-per-session
model — it arms `PR_SET_PDEATHSIG` against `uv`'s pid so an orphaned stdio
child dies with its session. In daemon mode this rationale doesn't hold:
if the daemon's immediate parent ends up being some ephemeral launching
shell rather than a persistent supervisor (systemd `--user` manager, or
an init/subreaper), the daemon will SIGTERM itself the moment that shell
exits — a "why did the shared daemon just die" bug that only reproduces
under specific launch conditions. Either skip this call in HTTP-transport
mode, or make sure whatever supervises the daemon is itself the tracked
immediate parent.

### Config changes — two places, not one

Checked both rather than assuming the top-level file is the only registration
(the "check the full state" habit — this project has a project-scoped
override that a narrower grep would miss):

- `~/.claude.json` → `mcpServers.meme` (used by every session everywhere):
  `{"type": "stdio", "command": "uv", "args": [...]}` → `{"type": "http", "url": "http://127.0.0.1:PORT/mcp"}`,
  same shape the `runpod` entry already uses (confirmed: `{"type": "http", "url": "https://mcp.getrunpod.io/"}`).
- `~/.claude.json` → `projects["/home/gas6amus/Documents/groupchat"].mcpServers.meme`
  (a **separate** stdio registration scoped to sessions opened in the
  groupchat repo itself, currently `{"command": "python3", "args": [".../meme_server.py"], "type": "stdio"}`,
  no `uv` wrapper — presumably the dev-loop registration). Needs the same
  `http`/`url` swap or every dev session in this repo keeps spawning its
  own extra copy alongside the daemon.

Run it as a systemd `--user` unit (matches the existing per-login-service
pattern already in use for the OOM-protection fix) rather than a bare
backgrounded process, so it survives logout/login and restarts on crash
without a manual "is it still running" check.

## 3. The actual risk: `/dev/tty` has no notion of "which session"

This is the one place "swap stdio for HTTP" is not sufficient, and it's
worth being concrete about the mechanism rather than asserting it either way.

`/dev/tty` is not "whichever fd got inherited" — opening it is a kernel
special-case that always resolves to the **calling process's own
controlling terminal**, looked up via that process's session. Today,
`meme_server.py` is a stdio child spawned directly in the process tree of
one specific Claude Code session's shell/terminal (`Claude Code → uv run →
python`), so it inherits that session's controlling tty for free — the
`open("/dev/tty", "w")` at `meme_server.py:184` and the `os.path.exists("/dev/tty")`
guard at `meme_server.py:175` correctly resolve to the right terminal with
zero code written to make that happen. It's an accident of the process
topology, not a designed mechanism — but it works.

A single shared daemon breaks this, in one of two ways depending on how
it's launched, and neither is acceptable to ship silently:

- **Launched via systemd `--user` (the right way to run a persistent
  daemon)**: a systemd user service has no controlling terminal at all.
  `os.path.exists("/dev/tty")` returns `False` for every call from every
  session, hitting the existing skip branch (`meme_server.py:176`,
  `"render skip [{name}]: no /dev/tty"`) — the entire visual feature goes
  silently dead for everyone, logged but never surfaced to any session.
- **Launched interactively from one terminal (e.g. hand-started, not
  supervised)**: the daemon inherits *that one* terminal as its ctty. Every
  drop from every connected session — regardless of which terminal actually
  triggered it — renders into the daemon's launch terminal. That's the
  cross-session bleed scenario named in the task: session A's meme shows up
  in session B's (or nobody's) terminal.

The MCP protocol itself gives no way out of this. Stdio, SSE, and
streamable-http alike are JSON-RPC-shaped tool calls with no fd-passing and
no notion of "the caller's terminal path" in the wire format, and Claude
Code's MCP client doesn't inject one. Streamable-http does have a
per-connection session id (established at `initialize`), which is the one
protocol-level handle that could theoretically be used to look up "which
terminal," but nothing populates that mapping today, and nothing in the
MCP spec obligates a client to send terminal identity.

**Options for a future session to choose among (design decision, not
implemented here):**

1. **Split architecture — thin per-session shim + shared daemon.** Keep a
   tiny per-session process (not a full `uv`-resolved environment — a
   small script with no heavy deps) that does only the `/dev/tty` write,
   using its own free, correctly-inherited controlling terminal. It calls
   the shared daemon (over HTTP or a unix socket) for everything else —
   roster lookup, GIF cache, cooldown state, braille generation. This is
   real design work (a small wire protocol between shim and daemon) but
   keeps the feature intact and still eliminates the expensive part (no
   more per-session `uv`-resolved Python + GIF/braille logic duplicated
   N times — just a small proxy per session).
2. **Session registration over streamable-http.** Have each session report
   its own tty path to the daemon at connect time (e.g. a `SessionStart`
   hook writes `$(tty)` somewhere the daemon can read, keyed by the
   streamable-http session id), and have the daemon look up the right
   path per call. Cleaner in principle, more protocol plumbing in practice,
   and still needs *something* per-session to discover and report the tty
   — there's no automatic source for it.
3. **Ship the daemon for state/cache only, degrade the render step.** Move
   DB/cooldown/GIF-cache logic to the daemon now, and either disable the
   visual render entirely in daemon mode or fall back to the existing
   screen-reader plain-text path (`_SCREEN_READER` branch, `meme_server.py:190-194`,
   already writes `alt_text` instead of braille) until option 1 or 2 is
   built. Smallest incremental step, but it's a known, visible regression
   to the ambient-meme feature — worth flagging to Justin explicitly rather
   than shipping quietly, per the flag-gated-feature discipline (a
   degraded-but-shipped daemon is a different state than a fully-solved one,
   and the gap should be tracked as its own item, not implied by "the
   daemon is done").

None of these are implemented or chosen here — that choice affects the file
set and is exactly the kind of thing that should be decided explicitly
before code gets written, not discovered mid-implementation.

## 4. Effort estimate

**Transport + config mechanics alone: small.** Everything needed
(`transport="streamable-http"`, host/port kwargs, the `"type": "http"`
config shape) already exists in the pinned SDK and has a working precedent
in this exact `.claude.json` (`runpod`). Concretely: an env-gated branch in
`__main__`, a systemd unit file, flipping two config entries, one round of
manual smoke-testing (curl or the reference `mcp` Python client against the
streamable-http endpoint, then a real Claude Code session). Call it low
single-digit hours of hands-on work.

**What actually blocks calling the whole thing small: the render path.**
The tty problem above isn't a corner case to patch after the fact — it's
the feature this server exists to serve (per this project's own README:
"the key architectural decision" of the whole system is rendering to
`/dev/tty` outside Claude's context). Shipping the daemon without deciding
among options 1-3 above means shipping a silent regression to that feature.
Designing and building the shim (option 1, the one that actually preserves
the feature) is bounded but real: a new small script, a protocol between
it and the daemon, and testing against 2+ concurrent terminals to confirm
no bleed and no silent no-op.

**Net: medium.** Small transport work, gated by a genuinely separate
render-path redesign that's bounded but not trivial. Recommend option 3 as
a staged first cut (ship daemon for the expensive part now, track the
render-preservation work as its own explicit follow-up item — not an
implied consequence of "daemon done") if the memory pressure is urgent
enough to want partial relief before the shim is built.

## 5. Payoff

Measured live, 2026-09-12, 5 concurrent Claude Code sessions
(`ps -eo pid,ppid,rss,cmd`, summed per session's `uv run` + child
interpreter pair):

| Session | uv run RSS | interpreter RSS | pair total |
|---|---|---|---|
| 1 | 53.5 MB | 47.2 MB | 100.7 MB |
| 2 | 51.9 MB | 47.2 MB | 99.2 MB |
| 3 | 41.4 MB | 45.0 MB | 86.3 MB |
| 4 | 30.4 MB | 36.6 MB | 67.0 MB |
| 5 | 32.2 MB | 10.6 MB (still warming up) | 42.7 MB |
| **Total** | | | **~396 MB** |

Consistent with the task's own ~96MB/session estimate (5 × 96MB ≈ 480MB;
the measured 396MB is lower mostly because session 5's interpreter hadn't
finished warming up at measurement time). Call it **400-480MB today, for 5
sessions, for a server with no meaningful per-session state to justify it.**

As one daemon: a single persistent interpreter, no `uv run` wrapper
overhead repeated per launch (the daemon can run against a real venv
directly instead of `uv run --no-project` re-resolving an environment from
scratch each time) — roughly the size of one of today's interpreter
processes alone, **~40-50MB total**, regardless of session count.

**Net savings at today's N=5: ~350-430MB.** But the more durable framing is
the *slope*, not the intercept: marginal cost per additional concurrent
session drops from ~96MB to ~0, since a new session just opens another
streamable-http connection to the process that's already running. Given
Justin's session count is the variable thing here (5 today, was 8 when the
original FEATURE_REQUEST was filed on 2026-07-23, and the recurring failure
mode is exactly "N concurrent sessions push this box into swap"), that
slope is the actual fix — a fixed one-time 400MB reclaim helps today, but
flattening the per-session marginal cost to zero is what stops this
specific server from being back on the "heaviest MCP server" list the next
time session count climbs.

## 6. Suggested entry point for whoever picks this up

1. Decide among the three render-path options in §3 — this is a product
   decision (is a temporary degraded meme feature acceptable) as much as a
   technical one, and changes the file set.
2. Add the transport-gate branch to `meme_server.py`'s `__main__`, test
   `streamable-http` manually before touching any `.claude.json`.
3. Write the systemd `--user` unit; confirm it survives a logout/login
   cycle and a manual crash-kill before pointing any session at it.
4. Flip both `.claude.json` registrations (top-level `mcpServers.meme` and
   the groupchat-project-scoped one) together — flipping one and not the
   other leaves a dev session in this repo running a redundant extra copy.
5. Verify with 2+ concurrent sessions open before declaring done: confirm
   a drop from session A renders in session A's terminal (or degrades the
   way option 3 intends), not session B's and not nowhere.
