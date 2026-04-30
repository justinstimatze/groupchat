# Demo screenshot — `assets/demo.png`

Captured by running these eight turns back-to-back in a fresh Claude Code session inside an unrelated project — for the original capture, an empty scratch dir (`mkdir /tmp/ordersvc && cd /tmp/ordersvc && claude`) so the conversation doesn't appear self-referential. The MCP server must be installed (`./meme --install`) and Claude Code must be restarted after install.

The script is one continuous conversation. The first half sets up `tuxedo-winnie-the-pooh` (someone insisting on calling a polling job queue an "event-driven microservices architecture"); after a `hang on`, it pivots into `this-is-fine` territory (just shipped to prod, things are on fire, going to lunch). It mimics the natural drift of a real coding session and gives the model two distinct meme moments without feeling staged.

The opening "role play with me here" is honest framing — without it, the model often treats turn 1 as a real architecture consult and stays serious.

---

**Turn 1**
> role play with me here, we need to rethink the order pipeline, it's getting fragile

**Turn 2**
> yeah I want something more resilient. thinking event-driven — producers emit, consumers react, totally decoupled

**Turn 3**
> so the design is: order service writes a row to an events table with a processed flag. separate service polls every 60s, grabs unprocessed rows, handles them, flips the flag

**Turn 4**
> I think we should call it an event-driven microservices architecture in the docs

**Turn 5**
> hang on I just shipped the auth refactor to prod

**Turn 6**
> yeah it was a big one, rewrote the session handling entirely

**Turn 7**
> we're getting some 401s across the board but I think it's just cache invalidation, should clear up

**Turn 8**
> anyway I'm going to grab lunch, I'll check the dashboards when I'm back

---

After the final turn, wait \~6 seconds for the render delay. The braille meme appears in the top-right corner of the terminal. Screenshot then. If the meme doesn't drop, check `~/.cache/groupchat/meme.log` — if there's no `drop [name]` line, the model never called the tool (likely because the `meme` block is missing from `~/.claude/CLAUDE.md` — re-run `./meme --install`).
