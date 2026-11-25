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
| `image` | Image shown under the text (rounded corners, scaled to fit) |
| `timestamp` | Custom timestamp text (overrides the clock). The default clock follows `CARD_TIMEZONE` (e.g. `Asia/Tokyo`), else UTC |
| `military_time` | 24-hour time in the default timestamp (`Today at 20:44`) |
| `mention_highlight` | Amber row tint and left bar, like a mention |
| `reactions` | e.g. `💀 4, 🔥 2` (Unicode emoji; count defaults to 1) |
| `togif` | Output a GIF (animated if the attached image is animated) |
| `use_username` | Show `user.name` instead of the display name |
| `hide_clantags` | Hide the clan tag |

The card is drawn locally at 2x and downsampled with LANCZOS. Fonts and the
emoji font are bundled in `fonts/`; no network access is needed to render.

## Run

`pip install -r requirements.txt`, set `DISCORD_TOKEN`, then `python main.py`.
`keep_alive.py` serves a tiny page on `PORT` for uptime pingers.

## Owner command

`!sync` (owner only) re-syncs the slash commands. Set `BOT_OWNER_ID`, or the
application owner is used. Needs the **Message Content** intent enabled in the
Developer Portal.

## Redeployment log

On each new Render deploy the bot posts an embed (version, branch, who ran it,
time, updated content) to the channel hard-coded in `config.py`. The bot needs View Channel, Send Messages, Embed Links
and Read Message History there. Each commit is logged once.

## Live stats stage channels

`cogs/stats.py` keeps two stage channels in the hard-coded guild (`config.py`)
named `<n> indiv. users` and `<n> servers`.
They are found again by name after a redeploy, recreated if deleted, and
renamed at most every 6 minutes (Discord allows 2 renames per 10 minutes).
The bot needs Manage Channels there, and the server must be a Community server.
Enable the **Server Members** intent for a de-duplicated user count.
The bot only reuses channels it made itself (it marks them with a permission
overwrite for itself), so other channels are never touched.

## Mentions, clan tags and DMs

Pings in `text` (`<@id>`, `<@&role>`, `<#channel>`) are shown as `@name` /
`#channel` pills (role pills use the role colour). The clan tag is drawn as a
gray chip with the clan badge, like the Discord client. The command also works
in DMs and group DMs; to use it outside servers the bot has to be installed to
your account (Developer Portal > Installation > enable *User Install*).
