'use strict';

// All settings come from environment variables (on Render: Environment tab).

function str(name, fallback = '') {
  const value = process.env[name];
  if (value === undefined || value.trim() === '') return fallback;
  return value.trim().replace(/^["']|["']$/g, '');
}

function int(name, fallback, min = 0) {
  const n = Number.parseInt(str(name), 10);
  return Number.isFinite(n) && n >= min ? n : fallback;
}

const config = {
  token: str('DISCORD_TOKEN'),
  port: int('PORT', 8080, 1),

  // Only this user can run owner-only commands (everything except PUBLIC_COMMANDS in index.js).
  ownerId: str('BOT_OWNER_ID', '1219266886143967245'),

  mongoUri: str('MONGO_URI'),
  mongoDb: str('MONGO_DB_NAME'),
  dataDir: str('DATA_DIR', './data'),

  validate() {
    if (!this.token) throw new Error('DISCORD_TOKEN is missing. Set it in the Render environment variables.');
  },
};

module.exports = config;
