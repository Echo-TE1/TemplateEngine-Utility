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
| `timestamp` | `12h` (default, `Today at 8:44 PM`), `24h` / `military` (`Today at 20:44`), or any text you type. The clock follows `CARD_TIMEZONE` (e.g. `Asia/Tokyo`), else UTC |
| `mention_highlight` | Amber row tint and left bar, like a mention |
| `reactions` | e.g. `💀 4, 🔥 2`, `💀4 🔥2`, `💀 x4`, `4 💀`. Unicode and custom emoji; no number = 1 |
| `togif` | Output a GIF (animated if the attached image is animated) |
| `use_username` | Show the username instead of the display name (default: display name) |
| `hide_clantags` | Hide the clan tag |

The card is drawn locally at 2x and downsampled with LANCZOS (`cardrender.py`).
Fonts are bundled in `fonts/`.

In `text` you can use markdown (`**bold**`, `*italic*`, `__underline__`,
`~~strike~~`, `` `code` ``, code blocks, `> quotes`, `||spoilers||`), links,
pings, Unicode and custom emoji, and Japanese text. A message that is only
emoji is drawn large, like in the client.

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

`!stats` (owner only) updates the stats channels right now and replies with what
happened, including why they could not be created (not a Community server,
missing Manage Channels, bot not in the server).
