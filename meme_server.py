#!/usr/bin/env python3
"""
MCP server for ambient meme drops.
Exposes drop_meme, meme_info, and list_memes tools to Claude Code.

Register with:
    claude mcp add meme -- python3 /path/to/groupchat/meme_server.py
"""

import difflib
import json
import math
import os
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path

from mcp.server.fastmcp import FastMCP

DB = Path(__file__).parent / "memes.json"
FRAMES_DIR = Path(__file__).parent / "frames"
CHAFA = "chafa"
GIF_CACHE = Path.home() / ".cache" / "groupchat" / "memes"
DROPS_FILE = Path.home() / ".cache" / "groupchat" / "drops.json"
LOG_FILE = Path.home() / ".cache" / "groupchat" / "meme.log"

mcp = FastMCP("meme")

# Linux prctl option: deliver a signal to this process when its parent dies.
_PR_SET_PDEATHSIG = 1

_LOG_MAX_BYTES = 256 * 1024  # 256 KB


def _log(msg: str) -> None:
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}\n"
    try:
        if LOG_FILE.exists() and LOG_FILE.stat().st_size > _LOG_MAX_BYTES:
            # Keep the last half on overflow
            text = LOG_FILE.read_text(errors="replace")
            LOG_FILE.write_text(text[len(text) // 2 :])
        with open(LOG_FILE, "a") as f:
            f.write(line)
    except OSError:
        pass


# in-process DB cache — invalidated only on server restart (which meme --add triggers)
_db_cache: list | None = None
_db_lock = threading.Lock()


def _load_db() -> list:
    global _db_cache
    with _db_lock:
        if _db_cache is None:
            with open(DB) as f:
                _db_cache = json.load(f)
        return _db_cache


# ---------------------------------------------------------------------------
# Adaptive cooldown — decaying threshold
# θ(t) = e^(-λ * elapsed_min)  where λ=1.5 → half-life ~28s, tight lifts ~14s, moderate lifts ~55s
# Set MEME_COOLDOWN_LAMBDA=99 to effectively disable cooldown during dev/testing.
# ---------------------------------------------------------------------------
_COOLDOWN_LAMBDA = float(os.environ.get("MEME_COOLDOWN_LAMBDA", "1.5"))


def _load_drops() -> list[float]:
    if not DROPS_FILE.exists():
        return []
    try:
        return json.loads(DROPS_FILE.read_text())
    except Exception:
        return []


def _record_drop() -> None:
    DROPS_FILE.parent.mkdir(parents=True, exist_ok=True)
    drops = _load_drops()
    drops.insert(0, time.time())
    tmp = DROPS_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(drops[:20]))
    tmp.rename(DROPS_FILE)


def _cooldown_state() -> dict:
    """Return current cooldown threshold (0=clear, 1=very tight)."""
    drops = _load_drops()
    if not drops:
        return {"threshold": 0.0, "state": "clear", "last_drop_secs": None}
    elapsed_min = (time.time() - drops[0]) / 60
    threshold = math.exp(-_COOLDOWN_LAMBDA * elapsed_min)
    if threshold > 0.7:
        state = "tight"
    elif threshold > 0.25:
        state = "moderate"
    else:
        state = "clear"
    return {
        "threshold": round(threshold, 2),
        "state": state,
        "last_drop_secs": int(time.time() - drops[0]),
    }


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def _resolve_img_url(m: dict) -> str | None:
    """Return the pinned img_url for a meme. All memes in the corpus must have img_url set.
    Use `meme --preview-all` to find and pin a URL for any entry that lacks one."""
    return m.get("img_url") or None


_TRUSTED_GIF_HOSTS = (
    ".tenor.com",
    ".giphy.com",
    "media.giphy.com",
    "i.imgur.com",
    "i.kym-cdn.com",
    "i.imgflip.com",
    "p1.hiclipart.com",
)


def _cached_gif(name: str, gif_url: str) -> str:
    """Return path to a locally cached GIF, downloading if needed."""
    parsed = urllib.parse.urlparse(gif_url)
    trusted = parsed.scheme == "https" and any(
        parsed.netloc == h.lstrip(".") or parsed.netloc.endswith(h) for h in _TRUSTED_GIF_HOSTS
    )
    if not trusted:
        raise ValueError(f"Refusing to fetch GIF from untrusted host: {parsed.netloc}")
    GIF_CACHE.mkdir(parents=True, exist_ok=True)
    dest = GIF_CACHE / f"{name}.gif"
    if dest.exists():
        return str(dest)
    tmp = dest.with_suffix(".tmp")
    req = urllib.request.Request(gif_url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=10) as r, open(tmp, "wb") as f:
        f.write(r.read())
    tmp.rename(dest)
    return str(dest)


_RENDER_DELAY = float(os.environ.get("MEME_DELAY_SECS", "6"))

# Screen-reader mode: auto-detected via AT-SPI bus (Orca/Linux) or explicit env var.
# When active, skip braille and write alt_text as plain text instead.
_SCREEN_READER = bool(os.environ.get("MEME_NO_BRAILLE") or os.environ.get("AT_SPI_BUS_ADDRESS"))


def _render_delayed(name: str, gif_path: str) -> None:
    """Sleep then render meme — fires after Claude's response finishes streaming.
    Uses pre-baked braille from frames/{name}.braille if available; falls back to
    running chafa on the downloaded GIF.
    Screen-reader mode (MEME_NO_BRAILLE=1 or AT_SPI_BUS_ADDRESS set): writes alt_text
    as plain text instead of braille art."""
    if not os.path.exists("/dev/tty"):
        _log(f"render skip [{name}]: no /dev/tty")
        return
    time.sleep(_RENDER_DELAY)
    _log(f"render start [{name}]")
    try:
        memes = _load_db()
        m = next((x for x in memes if x["name"] == name), None)
        alt = m.get("alt_text", "") if m else ""
        with open("/dev/tty", "w") as tty:
            try:
                size = os.get_terminal_size(tty.fileno())
                cols, rows = size.columns, size.lines
            except OSError:
                cols, rows = 80, 24
            if _SCREEN_READER:
                # Plain-text fallback: name + alt description, no braille
                tty.write(f"\n[{name}] {alt}\n")
                tty.flush()
                return
            braille_file = FRAMES_DIR / f"{name}.braille"
            if braille_file.exists():
                raw = braille_file.read_text(encoding="utf-8")
            else:
                result = subprocess.run(
                    [
                        CHAFA,
                        "--animate",
                        "off",
                        "--symbols",
                        "braille",
                        "--colors",
                        "none",
                        f"--size={cols // 2}x{rows // 2}",
                        gif_path,
                    ],
                    capture_output=True,
                    timeout=10,
                )
                raw = result.stdout.decode("utf-8", errors="replace")
            lines = raw.rstrip("\n").split("\n")
            actual_width = max((len(line) for line in lines if line), default=cols // 2)
            start_col = max(1, cols - actual_width + 1)
            start_row = max(1, (rows - len(lines)) // 2)
            render = "".join(
                f"\033[{start_row + i};{start_col}H{line}" for i, line in enumerate(lines)
            )
            tty.write(render)
            if alt:
                label = f"\033[2m[{name}] {alt}\033[0m"
                tty.write(f"\033[{rows - 1};1H{label}")
            tty.write(f"\033[{rows};1H")
            tty.flush()
        _log(
            f"render ok [{name}] cols={cols} rows={rows} braille={'yes' if (FRAMES_DIR / f'{name}.braille').exists() else 'no'}"
        )
    except OSError as e:
        _log(f"render fail [{name}]: {e}")
        pass


def _install_parent_death_signal() -> bool:
    """Ask the kernel to SIGTERM us when our immediate parent dies (Linux only).

    Claude Code launches this via `uv run`, so our parent is the `uv` process.
    If `uv` is killed while the session's stdin pipe stays open, the normal
    EOF-on-stdin shutdown never arrives and we linger as an orphan. PR_SET_PDEATHSIG
    closes that gap: when `uv` dies we get SIGTERM and exit. The common path
    (Claude Code closing stdin) is already handled by mcp.run() returning on EOF.

    No-op on non-Linux (prctl unavailable); returns False so callers can tell.
    """
    if not sys.platform.startswith("linux"):
        return False
    try:
        import ctypes

        parent = os.getppid()
        libc = ctypes.CDLL("libc.so.6", use_errno=True)
        # prctl(PR_SET_PDEATHSIG=1, signal=SIGTERM=15)
        if libc.prctl(_PR_SET_PDEATHSIG, 15, 0, 0, 0) != 0:
            return False
        # Race guard: if the parent died between getppid() and prctl, the signal
        # was already missed. Reparenting may land on init OR a subreaper
        # (systemd --user), so compare against the original pid, not just 1.
        if os.getppid() != parent:
            os._exit(0)
        return True
    except (OSError, AttributeError, ValueError):
        return False


def _find(memes, name):
    for m in memes:
        if m["name"] == name:
            return m
    close = difflib.get_close_matches(name, [m["name"] for m in memes], n=1, cutoff=0.5)
    if close:
        return next(m for m in memes if m["name"] == close[0])
    return None


# ---------------------------------------------------------------------------
# MCP tools
# ---------------------------------------------------------------------------


_MECH_ABBR = {"incongruity": "I", "benign_violation": "B", "superiority": "S", "relief": "R"}
_PLAT_ABBR = {
    "reddit": "rd",
    "twitter": "tw",
    "tiktok": "tt",
    "tumblr": "tm",
    "youtube": "yt",
    "discord": "dc",
}


def _roster_table(memes: list) -> str:
    """Compact one-line-per-meme roster. Lives here rather than in CLAUDE.md so
    it costs tokens only when a caller actually needs to browse."""
    lines = []
    for m in memes:
        td = "t" if m["text_dependent"] else "f"
        mechs = "".join(_MECH_ABBR.get(x, "?") for x in m.get("mechanism", []))
        plat = _PLAT_ABBR.get(m.get("native_platform", ""), "??")
        key = m.get("key", "")
        vitality = m.get("vitality", "evergreen")
        vdate = m.get("vitality_date", "")
        if vitality == "somewhat dated" and vdate:
            key = f"{key} [dated:{vdate}]"
        elif vitality == "retro" and vdate:
            key = f"{key} [retro:{vdate}]"
        lines.append(f"{m['name']:<30} {td}  {mechs:<4}  {plat}  {key}")
    return "\n".join(lines)


_ROSTER_LEGEND = """Columns: name | td (text-dependent t/f) | mech | plat | key

The key is the distinguishing fingerprint. When two memes feel similar, the key
decides. spark-joy and spark-joy-not are two crops of one template.

mech: I incongruity (violated expectation, most memes) · B benign_violation
(norm broken but safe, adds warmth over pure mockery) · S superiority (laughing
at someone, including self) · R relief (cathartic release, fits wins after
struggle). Prefer I+B for self-directed drops. S-heavy templates (grumpy-cat,
picard-facepalm) need the user clearly in on the joke. When the S target is the
user's own mistake the bar is much higher — surprised-pikachu in particular
reads as "you should have seen this coming, dummy."

plat is origin dialect: rd Reddit (broad, reads naturally in dev contexts) ·
tw Twitter (punchy, viral-format) · tt TikTok (Gen Z register) · dc Discord
(server-culture insular; niche fits that feel cringe on Reddit can land here) ·
tm Tumblr · yt YouTube.

[dated:YYYY] needs a 3-sigma fit — the template must be unmistakably precise.
[retro:YYYY] has crossed back through the cringe valley, so the oldness is part
of the joke; deployable when ironic meta-awareness is readable, too much if the
nostalgia register doesn't land."""

_WORKED_EXAMPLES = """Drops that work, and why the reason matters more than the match:

  CI green and tests pass, then prod breaks -> this-is-fine
    active performance of normalcy amid disaster, not merely "bad thing happened"
  three hours on a regex that now works -> success-kid
    relief from disproportionate struggle; small victory inflated, not a big win
  "microservices" turns out to be 50 cron jobs -> tuxedo-winnie-the-pooh
    identical thing rebranded in an elevated register
  I make an opinionated call the user might push back on -> deal-with-it
    requires the tension of potential objection to land

Non-drops, which are the majority:

  "fix this bug" -> nothing. Task request, no situational charge.
  a PR rejected with substantive feedback -> nothing. Wrong register, don't pile on.
  "this approach seems slow" -> nothing. No clear template, could read as dismissive."""


@mcp.tool()
def drop_meme(name: str) -> str:
    """
    Render a meme to the user's terminal as braille art in the top-right corner,
    after a short delay so it lands once Claude finishes responding. It does not
    appear in Claude's context, and the render must not be echoed in response text.
    Returns immediately; rendering happens ~6s later on a background thread.

    Returns cooldown state: `tight` means a drop landed recently and the bar is
    higher, `moderate` still passes a good fit, no message means clear.

    Deploy when the situation fits a known template with high confidence rather
    than loose resemblance, the ironic register matches (a dark template in a
    warm moment is worse than nothing), and the conversation has enough rapport
    to absorb a non-sequitur.

    Do not deploy when uncertain — a wrong meme is worse than none — nor when the
    user is frustrated, rushed, or in a high-stakes moment, nor on a routine task
    question with no situational charge, nor when the template is text_dependent
    (text is unreadable at terminal resolution).

    Call meme_info(name) first if unsure about a specific template's edge cases,
    or list_memes() to browse the roster.
    """
    memes = _load_db()
    m = _find(memes, name)
    if not m:
        _log(f"drop [{name}]: not found")
        return f"'{name}' not found — call meme_info to check spelling or list_memes to browse"
    cooldown = _cooldown_state()
    _log(f"drop [{m['name']}] cooldown={cooldown['state']} threshold={cooldown['threshold']:.2f}")
    img_url = _resolve_img_url(m)
    if not img_url:
        _log(f"drop [{m['name']}]: no img_url")
        return f"could not fetch '{name}' from Tenor"
    try:
        path = _cached_gif(name, img_url)
    except Exception as e:
        _log(f"drop [{m['name']}]: fetch failed: {e}")
        return f"fetch failed: {e}"
    _record_drop()
    threading.Thread(target=_render_delayed, args=(m["name"], path), daemon=True).start()
    alt = m.get("alt_text", "")
    suffix = f"\n{alt}" if alt else ""
    if cooldown["state"] == "tight":
        return f"[{m['name']}]{suffix}\n⚠️ cooldown={cooldown['threshold']:.2f} (dropped {cooldown['last_drop_secs']}s ago)"
    if cooldown["state"] == "moderate":
        return f"[{m['name']}]{suffix}\n(cooldown={cooldown['threshold']:.2f})"
    return f"[{m['name']}]{suffix}"


@mcp.tool()
def meme_info(name: str) -> str:
    """Get full metadata for one meme: key, deploy_when, too_much_if, mechanism, affect, irony_modes, alt_text.
    Use before drop_meme when uncertain about edge cases. ~100 tokens vs ~3k for list_memes."""
    memes = _load_db()
    m = _find(memes, name)
    if not m:
        return f"'{name}' not found"
    return json.dumps(
        {
            "name": m["name"],
            "key": m.get("key", ""),
            "deploy_when": m["deploy_when"],
            "too_much_if": m["too_much_if"],
            "affect": m["affect"],
            "mechanism": m.get("mechanism", []),
            "irony_modes": m.get("irony_modes", []),
            "text_dependent": m["text_dependent"],
            "vitality": m.get("vitality", "evergreen"),
            "alt_text": m.get("alt_text", ""),
        },
        indent=2,
    )


@mcp.tool()
def list_memes() -> str:
    """The full roster: every meme as one line of name, text-dependence, mechanism,
    origin platform, and the key that distinguishes it from its neighbours — plus
    how to read those columns and worked examples of drops that land and drops
    that don't. ~2k tokens.

    Call this when you can't place a name, or when calibrating what counts as a
    precise fit. Use meme_info(name) instead for one template's edge cases."""
    memes = _load_db()
    return (
        f"{len(memes)} memes.\n\n{_ROSTER_LEGEND}\n\n"
        f"```\n{_roster_table(memes)}\n```\n\n{_WORKED_EXAMPLES}"
    )


if __name__ == "__main__":
    _install_parent_death_signal()
    mcp.run()
