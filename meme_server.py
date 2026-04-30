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

mcp = FastMCP("meme")

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
        return
    time.sleep(_RENDER_DELAY)
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
    except OSError:
        pass


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


@mcp.tool()
def drop_meme(name: str) -> str:
    """
    Render a meme GIF to the user's terminal after a short delay (so it
    appears after Claude finishes responding, not during).
    Call this unprompted when something is genuinely meme-worthy.
    Use list_memes first if you need to check what's available.
    Returns immediately; rendering happens ~6s later via background thread.
    """
    memes = _load_db()
    m = _find(memes, name)
    if not m:
        return f"'{name}' not found — call meme_info to check spelling or list_memes to browse"
    cooldown = _cooldown_state()
    img_url = _resolve_img_url(m)
    if not img_url:
        return f"could not fetch '{name}' from Tenor"
    try:
        path = _cached_gif(name, img_url)
    except Exception as e:
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
    """Returns name, deploy_when, text_dependent, mechanism, native_platform, and vitality for all memes (~3k tokens).
    Last resort only — the meme roster is already in CLAUDE.md. Use meme_info(name) for
    targeted lookup of a single meme's edge-case metadata."""
    memes = _load_db()
    rows = [
        {
            "name": m["name"],
            "deploy_when": m["deploy_when"],
            "text_dependent": m["text_dependent"],
            "mechanism": m.get("mechanism", []),
            "native_platform": m.get("native_platform", ""),
            "vitality": m.get("vitality", "evergreen"),
        }
        for m in memes
    ]
    return json.dumps(rows, indent=2)


if __name__ == "__main__":
    mcp.run()
