'use strict';

// Remembers the tracker state (so nothing is announced twice after a restart), the incident history,
// and the log channels set with /setup log.
// MongoDB when MONGO_URI is set, otherwise JSON files in DATA_DIR.

const fs = require('node:fs/promises');
const path = require('node:path');
const { emptyState } = require('../lib/diff');

// ---------------------------------------------------------------------------------------
// JSON files (fallback)
// ---------------------------------------------------------------------------------------
class JsonStore {
  constructor(dir, historyLimit) {
    this.persistent = false; // on hosts with a temporary disk (Render free plan) this is lost on redeploy
    this.historyLimit = historyLimit;
    this.stateFile = path.join(dir, 'roblox-state.json');
    this.historyFile = path.join(dir, 'roblox-history.json');
    this.logChannelsFile = path.join(dir, 'log-channels.json');
  }

  async #read(file, fallback) {
    try {
      return JSON.parse(await fs.readFile(file, 'utf8'));
    } catch (error) {
      if (error.code !== 'ENOENT') console.warn(`[Store] Could not read ${file}: ${error.message}`);
      return fallback;
    }
  }

  async #write(file, data) {
    await fs.mkdir(path.dirname(file), { recursive: true });
    const temp = `${file}.tmp`;
    await fs.writeFile(temp, JSON.stringify(data, null, 2));
    await fs.rename(temp, file); // atomic, so a crash never leaves half a file
  }

  async loadState() {
    return { ...emptyState(), ...(await this.#read(this.stateFile, {})) };
  }

  async saveState(state) {
    await this.#write(this.stateFile, state);
  }

  async addHistory(record) {
    const all = await this.#read(this.historyFile, []);
    const rest = all.filter((r) => r.id !== record.id); // same incident twice -> keep one
    await this.#write(this.historyFile, [record, ...rest].slice(0, this.historyLimit));
  }

  async getHistory(limit) {
    const all = await this.#read(this.historyFile, []);
    return all
      .sort((a, b) => String(b.resolvedAt).localeCompare(String(a.resolvedAt)))
      .slice(0, limit);
  }

  async getLogChannels() {
    return this.#read(this.logChannelsFile, {});
  }

  async setLogChannel(type, record) {
    const all = await this.#read(this.logChannelsFile, {});
    all[type] = record;
    await this.#write(this.logChannelsFile, all);
  }

  async close() {}
}

// ---------------------------------------------------------------------------------------
// MongoDB
// ---------------------------------------------------------------------------------------
function dbNameFromUri(uri) {
  const found = /^mongodb(?:\+srv)?:\/\/[^/]+\/([^?]+)/.exec(uri);
  return found ? decodeURIComponent(found[1]) : null;
}

class MongoStore {
  constructor(uri, dbName) {
    this.persistent = true;
    this.uri = uri;
    this.dbName = dbName || dbNameFromUri(uri) || 'utility_bot';
  }

  async init() {
    const { MongoClient } = require('mongodb'); // loaded only when MONGO_URI is used
    this.client = new MongoClient(this.uri, { serverSelectionTimeoutMS: 8000 });
    await this.client.connect();
    const db = this.client.db(this.dbName);
    this.stateCol = db.collection('roblox_status');
    this.historyCol = db.collection('roblox_incident_history');
    this.settingsCol = db.collection('bot_settings');
  }

  async loadState() {
    const doc = await this.stateCol.findOne({ _id: 'state' });
    if (!doc) return emptyState();
    const { _id, ...rest } = doc;
    return { ...emptyState(), ...rest };
  }

  async saveState(state) {
    await this.stateCol.replaceOne({ _id: 'state' }, { ...state, _id: 'state' }, { upsert: true });
  }

  async addHistory(record) {
    // _id = incident id, so saving the same incident twice never creates a duplicate.
    await this.historyCol.replaceOne({ _id: record.id }, { ...record, _id: record.id }, { upsert: true });
  }

  async getHistory(limit) {
    const docs = await this.historyCol.find({}).sort({ resolvedAt: -1 }).limit(limit).toArray();
    return docs.map(({ _id, ...rest }) => rest);
  }

  async getLogChannels() {
    const doc = await this.settingsCol.findOne({ _id: 'log_channels' });
    if (!doc) return {};
    const { _id, ...rest } = doc;
    return rest;
  }

  async setLogChannel(type, record) {
    await this.settingsCol.updateOne({ _id: 'log_channels' }, { $set: { [type]: record } }, { upsert: true });
  }

  async close() {
    await this.client.close();
  }
}

async function createStore(config) {
  if (config.mongoUri) {
    try {
      const store = new MongoStore(config.mongoUri, config.mongoDb);
      await store.init();
      console.log(`[Store] Using MongoDB (database "${store.dbName}").`);
      return store;
    } catch (error) {
      console.error(`[Store] MongoDB failed, using local files instead: ${error.message}`);
    }
  } else {
    console.warn('[Store] MONGO_URI is not set. Using local files; history and log channels may be lost when the host redeploys.');
  }
  return new JsonStore(config.dataDir, config.historyLimit);
}

module.exports = { createStore, JsonStore, MongoStore };
