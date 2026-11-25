'use strict';

const DOC = 'mockconsent';

/**
 * Who has agreed to appear in /mock images. Everyone is OFF until they turn it on themselves with /settings,
 * and they can turn it off again at any time. Saved in the store, so it survives restarts.
 */
class MockConsent {
  constructor({ store }) {
    this.store = store;
    this.allowed = new Set();
  }

  async load() {
    const doc = (await this.store.getDoc(DOC)) || {};
    this.allowed = new Set(Array.isArray(doc.users) ? doc.users : []);
  }

  isAllowed(userId) {
    return this.allowed.has(userId);
  }

  async set(userId, on) {
    const next = new Set(this.allowed);
    if (on) next.add(userId);
    else next.delete(userId);
    await this.store.setDoc(DOC, { users: [...next] }); // save first: memory only changes if saving worked
    this.allowed = next;
  }
}

module.exports = { MockConsent };
