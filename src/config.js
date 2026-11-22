'use strict';

// All settings come from environment variables (on Render: Environment tab).

const DEFAULT_TRACKED = ['Website', 'Game Join', 'Avatar', 'Marketplace', 'Data Store', 'Authentication'];

// Roblox's status page runs on Status.io. This is the public JSON endpoint behind status.roblox.com.
const DEFAULT_STATUS_API =
  'https://4277980205320394.hostedstatus.com/1.0/status/59db90dbcdeb2f04dadcf16d';

// Direct checks of Roblox itself (any answer below HTTP 500 counts as "up").
// Website* goes to the Website Status log; everything else goes to the API Status log.
const DEFAULT_HEALTH_TARGETS = [
  { name: 'Website', url: 'https://www.roblox.com/' },
  { name: 'Authentication', url: 'https://auth.roblox.com/v2/login' },
  { name: 'Users API', url: 'https://users.roblox.com/v1/users/1' },
  { name: 'Games API', url: 'https://games.roblox.com/v1/games?universeIds=1' },
];

// Roblox-wide news for the Game Updates log (platform updates, events, announcements).
// These are the standard feed paths of the Roblox blog (WordPress) and the DevForum Updates
// category (Discourse); override them with NEWS_FEEDS if either has moved.
const DEFAULT_NEWS_FEEDS = [
  { name: 'Roblox Blog', url: 'https://blog.roblox.com/feed/' },
  { name: 'DevForum Updates', url: 'https://devforum.roblox.com/c/updates/45.rss' },
];

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

// "Name=https://url,Name2=https://url2" -> [{ name, url }]. Bad entries are ignored.
function targets(name, fallback) {
  const raw = str(name);
  if (!raw) return fallback;
  const parsed = raw
    .split(',')
    .map((part) => {
      const at = part.indexOf('=');
      if (at < 1) return null;
      const entry = { name: part.slice(0, at).trim(), url: part.slice(at + 1).trim() };
      return entry.name && /^https?:\/\//i.test(entry.url) ? entry : null;
    })
    .filter(Boolean);
  return parsed.length ? parsed : fallback;
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

  // Direct health checks (Website Status / API Status logs).
  healthEnabled: bool('HEALTH_CHECKS', true),
  healthIntervalSeconds: int('HEALTH_INTERVAL_SECONDS', 120, 30),
  healthFailThreshold: int('HEALTH_FAIL_THRESHOLD', 2, 1), // failed checks in a row before "down"
  healthTimeoutSeconds: int('HEALTH_TIMEOUT_SECONDS', 8, 2),
  healthTargets: targets('HEALTH_TARGETS', DEFAULT_HEALTH_TARGETS),

  // Roblox news (Game Updates log).
  newsEnabled: bool('NEWS_ENABLED', true),
  newsPollMinutes: int('NEWS_POLL_MINUTES', 30, 5),
  newsFeeds: targets('NEWS_FEEDS', DEFAULT_NEWS_FEEDS),

  // Bot-problem alerts and backups (Bot Updates log).
  botAlertAfterMinutes: int('BOT_ALERT_AFTER_MINUTES', 10, 1),
  backupDaily: bool('BACKUP_DAILY', true),

  // Message mirror: copies this user's messages in this channel to the Bot Updates log.
  mirrorEnabled: bool('MIRROR_ENABLED', true),
  mirrorChannelId: str('MIRROR_CHANNEL_ID', '1573743378368176148'),
  mirrorUserId: str('MIRROR_USER_ID', '1219266886143967245'),

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
