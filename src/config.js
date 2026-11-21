'use strict';

// All settings come from environment variables (on Render: Environment tab).

const DEFAULT_TRACKED = ['Website', 'Game Join', 'Avatar', 'Marketplace', 'Data Store', 'Authentication'];

// Roblox's status page runs on Status.io. This is the public JSON endpoint behind status.roblox.com.
const DEFAULT_STATUS_API =
  'https://4277980205320394.hostedstatus.com/1.0/status/59db90dbcdeb2f04dadcf16d';

function str(name, fallback = '') {
  const value = process.env[name];
  if (value === undefined || value.trim() === '') return fallback;
  return value.trim().replace(/^["']|["']$/g, '');
}

function int(name, fallback, min = 0) {
  const n = Number.parseInt(str(name), 10);
  return Number.isFinite(n) && n >= min ? n : fallback;
}

function bool(name, fallback = false) {
  const value = str(name).toLowerCase();
  if (!value) return fallback;
  return ['1', 'true', 'yes', 'on'].includes(value);
}

function list(name, fallback) {
  const items = str(name).split(',').map((s) => s.trim()).filter(Boolean);
  return items.length ? items : fallback;
}

const config = {
  token: str('DISCORD_TOKEN'),
  port: int('PORT', 8080, 1),

  // Only this user can run /setup.
  ownerId: str('BOT_OWNER_ID', '1219266886143967245'),

  // Channels are not configured here: the owner sets them with /setup log.
  alertRoleId: str('ALERT_ROLE_ID'),

  statusUrl: str('ROBLOX_STATUS_API_URL', DEFAULT_STATUS_API),
  statusPageUrl: str('ROBLOX_STATUS_PAGE_URL', 'https://status.roblox.com'),

  trackedServices: list('TRACKED_SERVICES', DEFAULT_TRACKED),
  alertScope: str('ALERT_SCOPE', 'tracked').toLowerCase() === 'all' ? 'all' : 'tracked',

  pollSeconds: int('POLL_INTERVAL_SECONDS', 60, 15),
  boardRefreshSeconds: int('STATUS_REFRESH_SECONDS', 300, 30),
  resolveConfirmPolls: int('RESOLVE_CONFIRM_POLLS', 2, 1),
  notifyOnStartup: bool('NOTIFY_ON_STARTUP', false),

  mongoUri: str('MONGO_URI'),
  mongoDb: str('MONGO_DB_NAME'),
  dataDir: str('DATA_DIR', './data'),
  historyLimit: int('HISTORY_LIMIT', 200, 10),

  validate() {
    if (!this.token) {
      throw new Error('DISCORD_TOKEN is missing. Set it in the Render environment variables.');
    }
  },
};

module.exports = config;
