# groupchat

## Meme drops

You have a `mcp__meme__drop_meme` tool. After every response, scan the exchange
for a meme opportunity. Most won't have one — that's correct and expected. When
one is present, act. This is a required post-response check, not optional
background awareness.

The meme renders as braille art to the top-right corner of the terminal via
`/dev/tty`. It is instant and does not appear in your context. Do not echo the
render in your response text.

Call `drop_meme(name)` directly when you know the name — you know these memes
from training. `meme_info(name)` returns one template's deploy/don't-deploy
metadata when you're unsure of an edge case. `list_memes()` returns the whole
roster with the column legend, the mechanism cheatsheet, and worked examples;
reach for it when you can't place a name or want to calibrate what counts as a
precise fit.

`drop_meme` returns cooldown state. `tight` means one landed recently and the
bar is higher, `moderate` still passes a good fit, and no message means clear.

### Adding memes

If the user shares a link (KYM, Tenor, Reddit) or says "add this", run
`meme --add <url>` in the terminal. It is interactive.
