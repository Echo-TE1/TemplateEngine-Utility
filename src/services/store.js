'use strict';

// Remembers small named documents (for example who allowed mock-ups of themselves).
// MongoDB when MONGO_URI is set, otherwise a JSON file in DATA_DIR (lost when a free host redeploys).

const fs = require('node:fs/promises');
const path = require('node:path');

class JsonStore {
  constructor(dir) {
    this.kind = 'local files';
    this.persistent = false;
    this.fellBack = false; // true when MongoDB was configured but could not be used
    this.docsFile = path.join(dir, 'documents.json');
  }

  async #read() {
    try {
      return JSON.parse(await fs.readFile(this.docsFile, 'utf8'));
    } catch (error) {
      if (error.code !== 'ENOENT') console.warn(`[Store] Could not read ${this.docsFile}: ${error.message}`);
      return {};
    }
  }

  async getDoc(name) {
    return (await this.#read())[name] || {};
  }

  async setDoc(name, data) {
    const all = await this.#read();
    all[name] = data;
    await fs.mkdir(path.dirname(this.docsFile), { recursive: true });
    const temp = `${this.docsFile}.tmp`;
    await fs.writeFile(temp, JSON.stringify(all, null, 2));
    await fs.rename(temp, this.docsFile); // atomic, so a crash never leaves half a file
  }

  async close() {}
}

function dbNameFromUri(uri) {
  const found = /^mongodb(?:\+srv)?:\/\/[^/]+\/([^?]+)/.exec(uri);
  return found ? decodeURIComponent(found[1]) : null;
}

class MongoStore {
  constructor(uri, dbName) {
    this.kind = 'MongoDB';
    this.persistent = true;
    this.fellBack = false;
    this.uri = uri;
    this.dbName = dbName || dbNameFromUri(uri) || 'utility_bot';
  }

  async init() {
    const { MongoClient } = require('mongodb'); // loaded only when MONGO_URI is used
    this.client = new MongoClient(this.uri, { serverSelectionTimeoutMS: 8000 });
    await this.client.connect();
    this.settingsCol = this.client.db(this.dbName).collection('bot_settings');
  }

  async getDoc(name) {
    const doc = await this.settingsCol.findOne({ _id: `doc:${name}` });
    if (!doc) return {};
    const { _id, ...rest } = doc;
    return rest;
  }

  async setDoc(name, data) {
    const _id = `doc:${name}`;
    await this.settingsCol.replaceOne({ _id }, { ...data, _id }, { upsert: true });
  }

  async close() {
    await this.client.close();
  }
}

async function createStore(config) {
  let fellBack = false;
  if (config.mongoUri) {
    try {
      const store = new MongoStore(config.mongoUri, config.mongoDb);
      await store.init();
      console.log(`[Store] Using MongoDB (database "${store.dbName}").`);
      return store;
    } catch (error) {
      fellBack = true;
      console.error(`[Store] MongoDB failed, using local files instead: ${error.message}`);
    }
  } else {
    console.warn('[Store] MONGO_URI is not set. Using local files; settings are lost when the host redeploys.');
  }
  const store = new JsonStore(config.dataDir);
  store.fellBack = fellBack;
  return store;
}

module.exports = { createStore, JsonStore, MongoStore };
