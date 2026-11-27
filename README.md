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
| `timestamp` | `12h` (default, `Today at 8:44 PM`), `24h` / `military` (`Today at 20:44`), or any text you type. The clock follows your `/defaults set timezone`, else `CARD_TIMEZONE` (optional), else UTC |
| `mention_highlight` | Amber row tint and left bar, like a mention |
| `reactions` | e.g. `💀 4, 🔥 2`, `💀4 🔥2`, `💀 x4`, `4 💀`. Unicode and custom emoji; no number = 1 |
| `togif` | Output a GIF (animated if the attached image is animated) |
| `use_username` | Show the username instead of the display name (default: display name) |
| `hide_clantags` | Hide the clan tag |
| `reply_to` / `reply_text` | Adds a "replying to" line above the message |
| `edited` | Adds the `(edited)` tag |
| `embed_title` / `embed_description` / `embed_color` | Adds an embed (colour as hex, e.g. `#5865F2`) |
| `sticker` | An image shown as a sticker |
| `label` | Small "Generated image - not a real Discord message" footer (off by default) |
| `role_icon` | Show the top role's icon next to the name (default on) |

The user's top-role icon (image or emoji) is drawn next to the name automatically.

### `/generate discord conversation`

`script` takes one message per line, `Name: text` or `<@id>: text` (mentions use the real
profile). The same speaker twice in a row is grouped like the client does. Max 12 lines, 6 speakers.
Options: `theme`, `timestamp`, `use_username`, `hide_clantags`, `label`.

### `/defaults set | show | reset`

Saves your own default `timezone` (e.g. `Asia/Tokyo`; used for 12h/24h times), `theme`, `timestamp`, `hide_clantags`, `use_username` and `label`. They are
stored as messages in a private `bot-data` channel the bot creates in the home server (so they
survive redeploys; needs Manage Channels). Without it they are kept in memory only.

### `/help`

Shows these commands inside Discord.

The card is drawn locally at 2x and downsampled with LANCZOS (`cardrender.py`).
Fonts are bundled in `fonts/`.

In `text` you can use markdown (`**bold**`, `*italic*`, `__underline__`,
`~~strike~~`, `` `code` ``, code blocks, `> quotes`, `||spoilers||`), links,
pings, Unicode and custom emoji, and Japanese text. A message that is only
emoji is drawn large, like in the client.

## Run

`pip install -r requirements.txt`, set `DISCORD_TOKEN`, then `python main.py`.
`keep_alive.py` serves a tiny page on `PORT` for uptime pingers.

## Owner commands

`!reload [message|stats|admin]` reloads cogs without a redeploy. `!sync` (owner only) re-syncs the slash commands. Set `BOT_OWNER_ID`, or the
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

## Giveaways

`/giveaway create | edit | end | reroll | delete | list | status`, modelled on the Template Engine bot
(servers only, Administrator by default; admins can change who may use it under Server Settings >
Integrations). The giveaway is one purple card with a gift button and an **Entries (n)** button inside it. The
gift button enters you, pressing it again leaves; **Entries** lists everyone who joined. When it ends the card
turns grey and the bot replies with a winner card.

`create` options: `prize`, `duration` (`1s` to `12mo`, any mix: `45s`, `30m`, `2h`, `1d12h`, `2w`, `6mo`; a month is 30 days), `winners`, `channel`,
`luck` + `luck_role` / `luck_role_2` / `luck_role_3` (those roles are 2x/3x/5x/10x as likely to win),
`ping_everyone`, `host_message` and `required_role`. `edit`, `end`, `reroll`, `delete` and `status` take the
giveaway's message ID or link (autocomplete lists them). `reroll` never picks an earlier winner again.
Entries are stored in a private `giveaway-data` channel the bot creates in the home server (needs Manage
Channels), so giveaways survive redeploys; ended ones stay rerollable for 30 days.

## Usage tracking and the bandwidth guard

Render suspends a free service that goes over its 5 GB bandwidth, so `cogs/usage.py` keeps a monthly tally
(images made, GIFs, average size, outbound/inbound network, uptime) in a private `usage-data` channel.
`!usage` (owner) shows it. The bandwidth figure is an estimate from the container's network counters; run
`!usage calibrate <MB>` once with the number from Render's usage page to line it up. Warnings go to the error-log
channel at 70%, 90% and 95%, and from `GUARD_PERCENT` (default 95) image generation pauses for everyone but
the owner until the month resets. Optional env vars: `BANDWIDTH_LIMIT_MB` (default 5120), `GUARD_PERCENT`, and
`RENDER_API_KEY` (adds pipeline minutes and the service count to `!usage`; `RENDER_SERVICE_ID` is set by Render).
Months are calendar months (UTC); Render's own cycle may start on another day.

## Log channels

All logs use one clean card style (`logcard.py`: title, thin lines between items, bold labels, timestamp). IDs
are in `config.py`:
- **Usage log:** posted at the start of every hour (UTC clock): bandwidth bar, last hour and month totals, uptime,
  guard status, and pipeline minutes/services when `RENDER_API_KEY` is set.
- **Bot data log:** server joined / left (with totals) and when someone saves or resets their `/defaults`.
  Generated text and images are never logged.
- **Giveaway log:** started, edited (with what changed), deleted, rerolled and ended (also ended early), showing the
  prize, host, who did it, winners (mention, username, ID), entries, channel, end time and a jump link.
- **Error log** and **deploy log** are separate channels, as before.

## Errors, tests and CI

Command and render errors are posted (rate-limited) as embeds to `ERROR_LOG_CHANNEL_ID` in `config.py` (separate from the deploy log channel).
`CARD_LABEL_DEFAULT=1` turns the "generated image" footer on for everyone by default.
`python -m pytest` runs the tests; `.github/workflows/ci.yml` runs them on every push.
