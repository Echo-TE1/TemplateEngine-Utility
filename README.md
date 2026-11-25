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

## Owner command

`!sync` (owner only) re-syncs the slash commands. Set `BOT_OWNER_ID`, or the
application owner is used. Needs the **Message Content** intent enabled in the
Developer Portal.

## Redeployment log

On each new Render deploy the bot posts an embed (version, branch, who ran it,
time, updated content) to channel `1575116831281254425` (override with
`DEPLOY_LOG_CHANNEL_ID`). The bot needs View Channel, Send Messages, Embed Links
and Read Message History there. Each commit is logged once.

## Live stats stage channels

`cogs/stats.py` keeps two stage channels in guild `1573644874077708438`
(override with `STATS_GUILD_ID`) named `<n> indiv. users` and `<n> servers`.
They are found again by name after a redeploy, recreated if deleted, and
renamed at most every 6 minutes (Discord allows 2 renames per 10 minutes).
The bot needs Manage Channels there, and the server must be a Community server.
Enable the **Server Members** intent for a de-duplicated user count.
Optional env: `STATS_CATEGORY_ID`, `STATS_USERS_CHANNEL_ID`, `STATS_SERVERS_CHANNEL_ID`.
