# Mock and quote bot

A small Discord bot (Node.js, discord.js 14) that makes image cards from messages. It runs on Render. This replaces the old Roblox outage tracker (REBOOT), which has been removed; it is still in the git history.

## Commands

| Command | Who | What it does |
|---|---|---|
| `/settings mock allow:<on/off>` | anyone | Allow or stop others making labelled mock-ups of you. Everyone is **off** until they turn it on. (Stored for the message commands to check with `context.mockConsent.isAllowed(userId)`.) |
| `/help` | anyone | Lists the commands you can use. |

The message commands are yours to write in `src/commands/message.js`. Ready-made helpers: `src/lib/messageCard.js` (draws a chat-style card), `src/lib/watermark.js` (full-strength tiled watermark), `src/lib/messageRef.js` (reads message links). The earlier `/quote` and `/mock` versions are in the git history (commit `401dccd`).

All commands are owner-only (`BOT_OWNER_ID`) except those in `PUBLIC_COMMANDS` in `index.js`.

## Setup

1. Create a bot in the Discord Developer Portal. Under **Bot > Privileged Gateway Intents** enable **Message Content** (needed by `/quote`). If it is off, the bot still starts but cannot read message text.
2. Invite it with the `bot` and `applications.commands` scopes.
3. On Render: Node 22, build command `npm install`, start command `npm start`, and the environment variables below.

| Variable | Default | Meaning |
|---|---|---|
| `DISCORD_TOKEN` | required | Bot token. Never put it in a file. |
| `BOT_OWNER_ID` | set in code | The user who can run owner-only commands. |
| `MONGO_URI` | none | MongoDB connection string. Without it, `/settings` choices are lost when the host redeploys. |
| `MONGO_DB_NAME` | from the URI | Database name. |
| `PORT` | `8080` | Web server port (Render sets it). The `/health` page lets a pinger keep the service awake. |
| `DATA_DIR` | `./data` | Where local files go when MongoDB is not used. |

## Reliability

- If the connection to Discord is lost for 5 minutes, the bot exits so Render restarts it (`[Watchdog]` in the logs). Disconnects, uncaught errors and invalid sessions are logged.
- Replies are sent straight to Discord first, with discord.js as a fallback, and each command has a 25 second limit so nothing stays on "thinking...".

## Project layout

```
index.js                      start-up, command handling, owner gate
keepAlive.js                  tiny web server for Render pings
src/config.js                 environment variables
src/commands/message.js       empty message module (write your commands here)
src/commands/                 settings.js, help.js
src/services/mockConsent.js   who allowed mock-ups of themselves
src/services/store.js         MongoDB or local JSON storage
src/lib/watermark.js          the watermark system
src/lib/messageCard.js        draws the cards (@napi-rs/canvas, Inter font in fonts/)
src/lib/messageRef.js         reads message links and IDs
src/lib/respond.js, discordRaw.js   reliable replies (with file upload)
src/lib/watchdog.js           restarts the bot if Discord is unreachable
test/                         run with: npm test
```
