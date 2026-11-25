# Discord message card cog

A discord.py cog (`cogs/message.py`, `MessageCog`) that renders a Discord-style
chat card image with Pillow and sends it as a `discord.File`.
Image rendering only: no webhooks, no impersonation.

## Command

`/generate discord message`

| Option | Meaning |
|---|---|
| `text` | Message content (required, 1-1000 chars) |
| `user` | Who the message is shown as (defaults to you) |
| `theme` | `dark` (default), `ash`, `onyx` or `light` |
| `display_font` | `default`, `sakura`, `jellybean`, `modern`, `medieval`, `8bit`, `vampyre` |
| `image` | Image shown under the text (rounded corners, scaled to fit) |
| `timestamp` | Custom text; default is `Today at <current time>` (set `CARD_TIMEZONE`, e.g. `Asia/Tokyo`) |
| `mention_highlight` | Amber row tint and left bar, like a mention |
| `reactions` | e.g. `💀 4, 🔥 2` (Unicode emoji; count defaults to 1) |
| `togif` | Output a GIF (animated if the attached image is animated) |
| `use_username` | Show `user.name` instead of the display name |
| `hide_clantags` | Hide the clan tag badge |

The card is drawn locally at 2x and downsampled with LANCZOS. Fonts and the
emoji font are bundled in `fonts/`; no network access is needed to render.

## Run

`pip install -r requirements.txt`, set `DISCORD_TOKEN`, then `python main.py`.
`keep_alive.py` serves a tiny page on `PORT` for uptime pingers.

## Owner command

`!sync` (owner only) re-syncs the slash commands. Set `BOT_OWNER_ID`, or the
application owner is used. Needs the **Message Content** intent enabled in the
Developer Portal.
