# Prompt changes

A log of edits to what groupchat puts into a Claude session's context, separate
from CHANGELOG.md, which tracks the tool. Each entry records what moved, why,
and what it cost or saved per turn.

## 2026-07-25 — roster and selection guidance moved out of CLAUDE.md into the tools

`CLAUDE.md` went from 11,802 bytes to 1,245. Because `meme --install` copies
this file verbatim into `~/.claude/CLAUDE.md`, that block was loading on every
turn of every project on the machine — roughly 2,950 tokens, a quarter of the
entire global CLAUDE.md, for one MCP tool.

What moved where:

- The 66-line roster table, the column legend, the mechanism cheatsheet, and the
  worked good-drop/bad-drop examples now come back from `list_memes()`. It
  returns 8,757 bytes (~2,190 tokens) and is called only when a caller can't
  place a name or wants to calibrate what a precise fit means.
- The deploy / do-not-deploy criteria and the cooldown semantics moved into the
  `drop_meme` docstring, which is the decision the caller is making at the
  moment it reads them.
- `CLAUDE.md` keeps only the ambient part: the tool exists, scan after every
  response, which of the three tools to reach for, and `meme --add <url>`.

`_build_roster_md`, `cmd_regen`, `--regen`, and the
`<!-- BEGIN meme-roster-generated -->` markers are gone from the `meme` CLI. The
roster is now built from `memes.json` at call time inside `meme_server.py`
(`_roster_table`), so there is nothing to regenerate and no second copy to drift.
`cmd_add` no longer calls `cmd_regen` after adding a meme. The
`<!-- BEGIN meme-server-guidance -->` markers and `--install` / `--uninstall`
are untouched.

### Why

Anthropic's July 2026 post on context engineering for Claude 5 models, saved at
`~/.claude/references/context-engineering-claude-5.md`, is direct about this
case: "We found we could delete these repeat examples and put instructions on
how to use tools in the tool descriptions rather than the system prompt." The
same post reports cutting over 80% of Claude Code's own system prompt with no
measurable loss on their coding evals, and names overconstraint — conflicting
directives arriving from system prompt, skills, and CLAUDE.md at once — as the
failure it was causing.

### Measured effect

`~/.claude/CLAUDE.md` fell from 43,766 to 33,212 bytes, so every turn in every
project on this machine carries about 2,640 fewer tokens. Nothing was deleted:
the same content is one `list_memes()` call away, and the tool already existed
for exactly that purpose.

### Verify after changing this

Run the tools under the environment the MCP server actually launches with,
since the repo venv does not carry `mcp`:

    uv run --no-project --with 'mcp>=2,<3' python -c "import meme_server"

A tool-description change only reaches Claude on the next session start, because
MCP schemas are read at server launch.
