# Roblox Outage Tracker (Discord bot)

A small Node.js / discord.js bot that watches Roblox and tells your server when something breaks, changes or recovers, and shares Roblox news.

## What it does

| Log (`/setup log`) | What gets posted there |
| --- | --- |
| **Platform Status** | One live status message that is edited in place, plus an alert when an incident starts, is updated or resolves (updates reply to the original alert). Role pings go here. |
| **Website Status** | Website incidents and changes from Roblox's status page, plus our own direct check of `roblox.com`. |
| **API Status** | Incidents and changes for every other Roblox service, plus direct checks of the login, users and games APIs. |
| **Roblox Updates** | Roblox-wide news: platform updates, events and announcements (Roblox Blog and DevForum Updates feeds). |
| **Bot Updates** | Only two things: a "Bot redeployed" notice each time the bot starts, and the message mirror (below). |

Nothing is posted to a log until you set its channel.

### Reaction roles and private alerts

**Roles are created automatically**: on every start (and whenever you run `/setup log`, `/reactionrole set` or `post`) the bot creates a `<Log> Alerts` role for every log that lacks one, reusing a role with that name if it exists and recreating one that was deleted. New logs get theirs the same way. (The bot needs **Manage Roles**.) `/reactionrole set channel_id:<id>` (owner) posts the reaction message (one reaction per log). When someone reacts they get that log's role and the bot **DMs them, mentioning them, whenever something is posted to that log**. Removing the reaction removes the role and the DMs. Nobody gets pinged in the channel for this.

**It survives redeploys:** the channel, message and subscribers are saved. On every start the bot finds the message, refreshes its text, re-adds its own reactions, re-posts it if it was deleted, and catches up on reactions added or removed while it was offline. This needs `MONGO_URI`; without it Render wipes the saved data on redeploy and you must run `/reactionrole set` again.

### Roblox profile lookup

`/profile tracker panel channel:#channel` posts a permanent panel with a **Search Roblox User** button. Anyone can press it; a pop-up asks for **one thing only, the Roblox username**. The result is a private (ephemeral) profile card with the avatar on the right: username, display name, Verified, followers / following / friends, description, Roblox badges (each name links to its icon), created date, user ID, status, last online, RAP, value, and **View Profile** and **Refresh** buttons.
The panel is saved and re-posted if deleted, and its buttons keep working after redeploys.

Where each value comes from, and when it says **Unavailable** (nothing is ever guessed):
- **Roblox public APIs**: name, display name, verified, description, created date, ID, avatar, followers / following / friends, Roblox badges.
- **Rolimons** (`api.rolimons.com`, a community site): **RAP, value** and a "last online" time. Only for accounts Rolimons tracks with a public inventory. Rolimons' last-online is when *it* last saw the account, so it is labelled "(Rolimons)" and can be old.
- **Online status**: Roblox hides this from logged-out requests, so it shows **Unavailable** unless you set `ROBLOX_COOKIE` (an optional `.ROBLOSECURITY` cookie, ideally from a throwaway account; it is only sent to Roblox and never logged or stored). Users with privacy settings still show Offline.
- Invalid names, unknown accounts, rate limits and Roblox outages each get a clear private message. Results are cached for 30 seconds and each person can search or refresh once every 5 seconds, to stay under Roblox's rate limits.

### Message mirror

The bot watches channel `1573743378368176148` for messages from user `1219266886143967245` and copies each one into the **Bot Updates** channel (author, text, attachments). When the original is sent the bot reacts with a check mark. If you **edit** the original the copy is edited (shown as "Edited"); if you **delete** it the copy is deleted.
Needs: the **Message Content Intent** switched on in the Discord Developer Portal (Bot > Privileged Gateway Intents), and in the watched channel the bot needs View Channel, Read Message History and Add Reactions. If Discord refuses the intent the bot still starts, with the mirror off (`/botstatus` shows why). The copy links are saved (last 500), so edits and deletes still work after a restart. Change the channel/user with `MIRROR_CHANNEL_ID` / `MIRROR_USER_ID`, or turn it off with `MIRROR_ENABLED=false`.

## Commands

| Command | Who | What it does |
| --- | --- | --- |
| `/robloxstatus` | anyone | Current status. `view: Incident history` lists recent incidents. `view: Uptime` shows incident-based uptime per service (`days` 1-90). When something is down it lists the broken services and the working ones. |
| `/setup log action channel_id` | owner | Choose the channel for a log. The bot posts a notice there first, so a channel it cannot write to is rejected. |
| `/setup view` | owner | Show which channel each log uses and the alert roles. |
| `/setup clear action` | owner | Stop posting a log. |
| `/setup role service [role]` | owner | Role pinged for incidents on a service (or "All alerts"). Leave `role` empty to remove it. |
| `/reactionrole set channel_id` | owner | Post the reaction message in a channel (kept up to date automatically). |
| `/profile tracker panel channel` | owner | Post the permanent **Search Roblox User** panel in a channel (see below). |
| `/channel create` | owner | Create a chat, voice, announcement, stage or forum channel (`name`, `action`, `type`; optional `category`, `allowed_roles`, `duration` and 19 allow/deny permission options). `type: Temporary` needs a `duration` (`1m`, `1h`, `1d`, `1w`, `1mo`, `1year`) and the bot deletes the channel when it is up, also after a redeploy (needs `MONGO_URI`). With `allowed_roles` the channel is hidden from everyone else. Needs Manage Channels. |
| `/botstatus` | owner | Uptime, Discord ping, database, last good Roblox check, direct checks, news feeds, logs set up. |

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
3. Deploy. The bot registers its commands (this replaces the old `/ad` command; `/channel` was ported).
4. In Discord, run `/setup log` for each log you want. Give the bot **View Channel, Send Messages, Embed Links, Read Message History** in those channels. Platform Status shows the live message right away.
5. Optional: `/setup role` to ping a role per service. Pings only work if the role is mentionable or the bot has **Mention @everyone, @here and All Roles**.

Without `MONGO_URI` the bot uses local files, which Render's free plan wipes on redeploy (you would have to run `/setup log` again). `/botstatus` warns about this.

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
| `ROBLOX_COOKIE` | | Optional. Lets the profile lookup show online status. |
| `MIRROR_ENABLED` | `true` | Turn the message mirror on/off. |
| `MIRROR_CHANNEL_ID` | `1573743378368176148` | Channel to watch. |
| `MIRROR_USER_ID` | `1219266886143967245` | Only this user's messages are copied. |
| `MONGO_DB_NAME`, `DATA_DIR`, `HISTORY_LIMIT`, `ROBLOX_STATUS_API_URL`, `ROBLOX_STATUS_PAGE_URL` | | Advanced, see `.env.example`. |

Old Python-bot variables (`ROBLOX_GROUP_ID`, `PREMIUM_DESIGN_CHANNEL_ID`, `NON_PREMIUM_DESIGN_CHANNEL_ID`) and `STATUS_CHANNEL_ID` / `ALERT_CHANNEL_ID` are no longer used and can be deleted.

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
- If Roblox's status API is unreachable, nothing is announced; the live message gets a warning after 3 failed checks. Problems are shown by `/botstatus`, not posted anywhere.

## Project layout

```
index.js                      entry point (client, commands, startup, shutdown)
keepAlive.js                  tiny web server for Render pings
src/config.js                 all environment variables
src/commands/                 slash commands (drop a file here to add one)
src/services/tracker.js       status polling, alerts, live message, routing to logs
src/services/health.js        direct checks of roblox.com and the APIs
src/services/news.js          Roblox news feeds
src/services/reactionRoles.js reaction roles + DM subscribers
src/services/robloxProfile.js  Roblox profile lookup (Roblox APIs + Rolimons)
src/services/profilePanel.js   the permanent Search Roblox User panel
src/services/tempChannels.js  temporary channels from /channel create
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
