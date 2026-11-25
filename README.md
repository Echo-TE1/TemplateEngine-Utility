# Discord message card cog

A discord.py cog (`cogs/message.py`, `MessageCog`) that renders a Discord-style
chat card image with Pillow and sends it as a `discord.File`.
Image rendering only: no webhooks, no impersonation.

## Command

`/generate discord message`

| Option | Meaning |
|---|---|
| `user` | The user shown on the card |
| `text` | Message text (1-1000 chars) |
| `theme` | `dark` (default) or `light` |
| `display_font` | Inter (default), DejaVu Sans, DejaVu Serif, DejaVu Mono (bundled in `fonts/`) |
| `image` | Image shown under the message |
| `timestamp` | Show the timestamp (default yes) |
| `mention_highlight` | Highlight `@mentions` (default yes) |
| `reactions` | e.g. `👍 3 🔥 2` (custom emoji like `<:name:id>` work) |
| `togif` | Send a GIF instead of a PNG (single frame) |
| `use_username` | Show the username instead of the display name |
| `hide_clantags` | Hide the user's clan tag |

Emoji images for `reactions` are downloaded from the Twemoji CDN (Unicode) or
Discord's CDN (custom emoji).

## Run

`pip install -r requirements.txt`, set `DISCORD_TOKEN`, then `python main.py`.
`keep_alive.py` serves a tiny page on `PORT` for uptime pingers.
