# Roblox Outage Tracker (Discord bot)

A small Node.js / discord.js bot that watches Roblox and tells your server when something breaks, changes or recovers, and shares Roblox news.

## What it does

| Log (`/setup log`) | What gets posted there |
| --- | --- |
| **Platform Status** | One live status message that is edited in place, plus an alert when an incident starts, is updated or resolves (updates reply to the original alert). Role pings go here. |
| **Website Status** | Website incidents and changes from Roblox's status page, plus our own direct check of `roblox.com`. |
| **API Status** | Incidents and changes for every other Roblox service, plus direct checks of the login, users and games APIs. |
| **Game Updates** | Roblox-wide news: platform updates, events and announcements (Roblox Blog and DevForum Updates feeds). |
| **Bot Updates** | Bot started, Roblox status API unreachable / back, database warnings, and a daily backup file. |

Nothing is posted to a log until you set its channel.

### Reaction roles and private alerts

`/reactionrole set` (owner) opens a private panel with one role picker per log. `/reactionrole post channel_id:<id>` posts the reaction message (📡 Platform Status, 🌐 Website Status, ⚙️ API Status, 🎮 Game Updates, 🤖 Bot Updates). When someone reacts they get that log's role (if you picked one) and the bot **DMs them, mentioning them, whenever something is posted to that log** (new alerts, updates, recoveries, news). Removing the reaction removes the role and the DMs. Nobody gets pinged in the channel for this.
Needs: Manage Roles with the bot's role above the roles you pick, Add Reactions in the channel, and people must have DMs from server members open. The daily backup file is never DMed, and the live status message edits are not DMed (only new posts). Re-run `/reactionrole post` after changing roles to refresh the text; the old message stops working.

### Message mirror

The bot watches channel `1573743378368176148` for messages from user `1219266886143967245` and copies each one into the **Bot Updates** channel (author, text, attachments, link to the original). When the original is sent the bot reacts with ✅. If you **edit** the original the copy is edited (shown as "Edited"); if you **delete** it the copy is deleted.
Needs: the **Message Content Intent** switched on in the Discord Developer Portal (Bot > Privileged Gateway Intents), and in the watched channel the bot needs View Channel, Read Message History and Add Reactions. If Discord refuses the intent the bot still starts, with the mirror off and a warning in Bot Updates and `/botstatus`. The copy links are saved (last 500), so edits and deletes still work after a restart. Change the channel/user with `MIRROR_CHANNEL_ID` / `MIRROR_USER_ID`, or turn it off with `MIRROR_ENABLED=false`.

## Commands

| Command | Who | What it does |
| --- | --- | --- |
| `/robloxstatus` | anyone | Current status. `view: Incident history` lists recent incidents. `view: Uptime` shows incident-based uptime per service (`days` 1-90). When something is down it lists the broken services and the working ones. |
| `/setup log action channel_id` | owner | Choose the channel for a log. The bot posts a notice there first, so a channel it cannot write to is rejected. |
| `/setup view` | owner | Show which channel each log uses and the alert roles. |
| `/setup clear action` | owner | Stop posting a log. |
| `/setup role service [role]` | owner | Role pinged for incidents on a service (or "All alerts"). Leave `role` empty to remove it. |
| `/reactionrole set` / `post` | owner | Choose a role per log, then post the reaction message that subscribes people to DMs. |
| `/botstatus` | owner | Uptime, Discord ping, database, last good Roblox check, direct checks, news feeds, logs set up. |
| `/backup` | owner | Download everything the bot remembers as a JSON file (no tokens or secrets in it). |

"Owner" is the user in `BOT_OWNER_ID` (default `1219266886143967245`). Everyone else gets a private "Permission Denied". Owner commands reply privately.

## Where the data comes from

1. **Roblox's status page** (status.roblox.com runs on Status.io; the bot reads its public JSON). Services are grouped as User, Player and Creator, and the tracked services below are matched in every group.
   Default tracked services: `Website, Game Join, Avatar, Marketplace, Data Store, Authentication`.
   > **Authentication is not a component on Roblox's status page**, so it is flagged only when an incident's text mentions authentication, login, sign in/up, 2-step or 2FA.
2. **Direct checks of Roblox** (`HEALTH_TARGETS`): `www.roblox.com`, `auth.roblox.com`, `users.roblox.com`, `games.roblox.com`. Any answer below HTTP 500 counts as up. A target is reported **down after 2 failed checks in a row** and **recovered after 2 good ones**, so one slow answer does not cause an alert. These can notice trouble before Roblox's own page does, but they only see what the bot's host can reach.
3. **News feeds** (`NEWS_FEEDS`): the Roblox Blog and the DevForum "Updates" category, as standard RSS feeds. The first read of a feed only remembers what is already there (no flood); after that, new items are posted once each (at most 5 per check).
   > The default feed URLs are the standard paths for those sites and were **not verified from the build environment**. If one is wrong, the bot logs it once and `/botstatus` shows it as failing; set `NEWS_FEEDS` to the right URL.

## Setup (Render)

1. **Build Command:** `npm install` **Start Command:** `node index.js`.
2. Keep `DISCORD_TOKEN` and `MONGO_URI`. No channel variables are needed.
3. Deploy. The bot registers its commands (this replaces the old `/ad` and `/channel` commands).
4. In Discord, run `/setup log` for each log you want. Give the bot **View Channel, Send Messages, Embed Links, Read Message History** in those channels. Platform Status shows the live message right away.
5. Optional: `/setup role` to ping a role per service. Pings only work if the role is mentionable or the bot has **Mention @everyone, @here and All Roles**.

Without `MONGO_URI` the bot uses local files, which Render's free plan wipes on redeploy (you would have to run `/setup log` again). The bot warns about this in Bot Updates.

## Environment variables

| Variable | Default | Purpose |
| --- | --- | --- |
| `DISCORD_TOKEN` | required | Bot token. |
| `MONGO_URI` | | Keeps settings, history and state permanently. |
| `PORT` | `8080` | Render sets this. |
| `BOT_OWNER_ID` | `1219266886143967245` | The user allowed to run owner commands. |
| `ALERT_ROLE_ID` | | Fallback "all alerts" role (a role saved with `/setup role` replaces it). |
| `TRACKED_SERVICES` | see above | Comma separated; case and spaces ignored. |
| `ALERT_SCOPE` | `tracked` | `tracked` = alerts only for tracked services, `all` = every Roblox service. |
| `POLL_INTERVAL_SECONDS` | `60` | Status page check frequency (min 15). |
| `STATUS_REFRESH_SECONDS` | `300` | Live message is edited at least this often (sooner if something changes). |
| `RESOLVE_CONFIRM_POLLS` | `2` | Checks an incident must be gone before it counts as resolved. |
| `NOTIFY_ON_STARTUP` | `false` | Announce incidents already running on the very first start. |
| `HEALTH_CHECKS` | `true` | Turn the direct checks on/off. |
| `HEALTH_INTERVAL_SECONDS` | `120` | Direct check frequency (min 30). |
| `HEALTH_FAIL_THRESHOLD` | `2` | Failed checks in a row before "down". |
| `HEALTH_TIMEOUT_SECONDS` | `8` | Give up on one check after this long. |
| `HEALTH_TARGETS` | see above | `Name=https://url,Name2=https://url2`. A target named `Website` reports to Website Status, all others to API Status. |
| `NEWS_ENABLED` | `true` | Turn Roblox news on/off. |
| `NEWS_POLL_MINUTES` | `30` | News check frequency (min 5). |
| `NEWS_FEEDS` | Roblox Blog, DevForum Updates | `Name=https://feed-url,...` |
| `BOT_ALERT_AFTER_MINUTES` | `10` | Minutes the status API must be unreachable before Bot Updates is told. |
| `MIRROR_ENABLED` | `true` | Turn the message mirror on/off. |
| `MIRROR_CHANNEL_ID` | `1573743378368176148` | Channel to watch. |
| `MIRROR_USER_ID` | `1219266886143967245` | Only this user's messages are copied. |
| `BACKUP_DAILY` | `true` | Post a backup file to Bot Updates once a day (after midnight JST). |
| `MONGO_DB_NAME`, `DATA_DIR`, `HISTORY_LIMIT`, `ROBLOX_STATUS_API_URL`, `ROBLOX_STATUS_PAGE_URL` | | Advanced, see `.env.example`. |

Old Python-bot variables (`ROBLOX_COOKIE`, `ROBLOX_GROUP_ID`, `PREMIUM_DESIGN_CHANNEL_ID`, `NON_PREMIUM_DESIGN_CHANNEL_ID`) and `STATUS_CHANNEL_ID` / `ALERT_CHANNEL_ID` are no longer used and can be deleted.

## Bandwidth (rough estimates, not measured on a live host)

| Source | Per month, approx. |
| --- | --- |
| Roblox status page (5.6 KB per check, every 60 s) | ~0.25 GB |
| Direct checks (4 targets, every 120 s, only headers are kept) | ~0.1-0.2 GB |
| News feeds (every 30 min; unchanged feeds answer "not modified", at most 150 KB read) | ~0.05 GB with "not modified" support, up to ~0.4 GB without |

To cut it: raise `POLL_INTERVAL_SECONDS` (300 = 5x less), `HEALTH_INTERVAL_SECONDS`, `NEWS_POLL_MINUTES`, or set `HEALTH_CHECKS=false`.

## How duplicates and false alarms are prevented

- State is saved **before** anything is announced, so a crash can lose an alert but never repeat one (this also holds for health checks and news).
- Each incident update is keyed by its timestamp and announced once.
- An incident is only resolved after it has been missing for `RESOLVE_CONFIRM_POLLS` checks.
- A service change is not announced separately when an incident that covers it starts in the same check.
- Direct checks need 2 failures to go down and 2 successes to recover.
- Only one status check runs at a time; failed posts are retried up to 3 times.
- If Roblox's status API is unreachable, nothing is announced; the live message gets a warning after 3 failed checks, and Bot Updates is told after `BOT_ALERT_AFTER_MINUTES`.

## Project layout

```
index.js                      entry point (client, commands, startup, shutdown)
keepAlive.js                  tiny web server for Render pings
src/config.js                 all environment variables
src/commands/                 slash commands (drop a file here to add one)
src/services/tracker.js       status polling, alerts, live message, routing to logs
src/services/health.js        direct checks of roblox.com and the APIs
src/services/news.js          Roblox news feeds
src/services/backup.js        /backup and the daily backup
src/services/reactionRoles.js reaction roles + DM subscribers
src/services/mirror.js        message mirror (copy / edit / delete to Bot Updates)
src/services/notifier.js      posts to the log channels (with retry)
src/services/logChannels.js   which channel each log uses
src/services/serviceRoles.js  which role is pinged per service
src/services/store.js         MongoDB / JSON storage
src/lib/                      pure logic (unit tested): diff, normalize, health, feed, stats, ...
src/embeds.js                 all embeds
test/                         npm test
```

## Local development

```
npm install
cp .env.example .env   # fill in DISCORD_TOKEN, then run /setup log in Discord
npm run dev            # needs Node 20.6+; or export the variables and run npm start
npm test
```
