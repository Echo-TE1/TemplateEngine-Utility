# Roblox Outage Tracker (Discord bot)

A small Node.js / discord.js bot that watches Roblox's official status page and tells your server when something breaks, changes or recovers.

- **Live status message**: one embed that the bot edits in place (no spam).
- **Alerts**: a new embed when an incident starts, is updated, or is resolved, plus one when tracked services go down or recover. Updates and the resolution reply to the original alert.
- **No duplicates**: every incident and update is remembered (MongoDB or a local file), so restarts and repeat checks never re-announce anything.
- **`/robloxstatus`**: current status, or `view: Incident history` for recent finished incidents.
- **`/setup log`** (owner only): choose the channel for Platform Status, Website Status, API Status, Game Updates and Bot Updates logs.

## `/setup log` (owner only)

`/setup log action:<log> channel_id:<id>` picks the channel for each log category:
**Platform Status, Website Status, API Status, Game Updates, Bot Updates.**

- Only the user in `BOT_OWNER_ID` (default `1219266886143967245`) can run it; everyone else gets a private "Permission Denied".
- The bot posts a short notice in the channel first, so a channel it cannot write to is rejected instead of saved.
- Settings are saved in MongoDB (or `data/log-channels.json`) and survive restarts. Running the command again for the same log replaces the channel.
- A channel ID or a `#channel` mention both work. Get an ID with Developer Mode on: right-click the channel, **Copy Channel ID**.
- This sets *where* each log goes. Code can post to one with `logChannels.send('bot_updates', { embeds: [...] })` (see `src/services/logChannels.js`); the Roblox alerts still use `STATUS_CHANNEL_ID` / `ALERT_CHANNEL_ID`, and nothing posts to the five logs automatically yet.

## Where the data comes from

status.roblox.com is hosted on Status.io. The bot reads its public JSON endpoint (the `ROBLOX_STATUS_API_URL` default in `.env.example`). Services are grouped as **User**, **Player** and **Creator**; the bot tracks the services below in every group they appear in.

Default tracked services: `Website, Game Join, Avatar, Marketplace, Data Store, Authentication`.

> **Authentication is not a component on Roblox's status page.** The bot cannot see it go down directly. Instead it flags any incident whose text mentions authentication, login, sign in/up, 2-step or 2FA, and shows "No incidents reported" otherwise.

## Setup (Render)

1. **Build Command:** `npm install` **Start Command:** `node index.js` (the old Python start command no longer applies).
2. Keep your existing `DISCORD_TOKEN` and `MONGO_URI`. Add `STATUS_CHANNEL_ID`.
3. Give the bot these permissions in that channel: **View Channel, Send Messages, Embed Links, Read Message History**.
4. Deploy. The first start posts the live status message and registers `/robloxstatus`.

Registering commands replaces the bot's global commands, so the old `/ad` and `/channel` commands disappear on first start.

## Environment variables

| Variable | Required | Default | Purpose |
| --- | --- | --- | --- |
| `DISCORD_TOKEN` | yes | | Bot token (already on Render). |
| `STATUS_CHANNEL_ID` | yes* | | Channel for the live message and alerts. *Without it the bot only records history. |
| `MONGO_URI` | no | | Stores state + history. Without it a local file is used (lost on redeploy on Render's free plan). |
| `PORT` | no | `8080` | Render sets this. |
| `BOT_OWNER_ID` | no | `1219266886143967245` | The only user allowed to run `/setup`. |
| `ALERT_CHANNEL_ID` | no | `STATUS_CHANNEL_ID` | Send alerts to a different channel. |
| `ALERT_ROLE_ID` | no | | Role pinged when an incident starts and when it is resolved. |
| `TRACKED_SERVICES` | no | see above | Comma separated; case and spaces are ignored. |
| `ALERT_SCOPE` | no | `tracked` | `tracked` = alert only for tracked services, `all` = every Roblox service. |
| `POLL_INTERVAL_SECONDS` | no | `60` | Check frequency (minimum 15). |
| `STATUS_REFRESH_SECONDS` | no | `300` | Edit the live message at least this often. It is edited sooner when something changes. |
| `RESOLVE_CONFIRM_POLLS` | no | `2` | Checks in a row an incident must be gone before it counts as resolved. |
| `NOTIFY_ON_STARTUP` | no | `false` | Announce incidents that are already running on the very first start. |
| `MONGO_DB_NAME`, `DATA_DIR`, `HISTORY_LIMIT`, `ROBLOX_STATUS_API_URL`, `ROBLOX_STATUS_PAGE_URL` | no | | Advanced, see `.env.example`. |

The old Python-bot variables (`ROBLOX_COOKIE`, `ROBLOX_GROUP_ID`, `PREMIUM_DESIGN_CHANNEL_ID`, `NON_PREMIUM_DESIGN_CHANNEL_ID`) are no longer used and can be deleted.

## How duplicates are prevented

- State is saved **before** alerts are sent, so a crash can lose an alert but never repeat one.
- Each incident update is keyed by its timestamp and announced once.
- An incident only counts as resolved after it has been missing for `RESOLVE_CONFIRM_POLLS` checks, so one bad API answer cannot cause a "resolved" followed by a new "started".
- A service change is not announced separately when an incident that covers it starts in the same check.
- Only one check runs at a time, and failed alerts are retried up to 3 times.
- If Roblox's API is unreachable, nothing is announced; after 3 failed checks the live message gets a warning.

## Project layout

```
index.js                  entry point (client, commands, startup)
keepAlive.js              tiny web server for Render pings
src/config.js             all environment variables
src/commands/             slash commands (drop a file here to add one)
src/services/tracker.js   polling loop, alerts, live message
src/services/store.js     MongoDB / JSON storage
src/services/statusClient.js
src/lib/                  pure logic: normalize, diff, matching (unit tested)
src/embeds.js             all embeds
test/                     npm test
```

## Local development

```
npm install
cp .env.example .env   # fill in DISCORD_TOKEN and STATUS_CHANNEL_ID
npm run dev            # needs Node 20.6+; or export the variables and run npm start
npm test
```
