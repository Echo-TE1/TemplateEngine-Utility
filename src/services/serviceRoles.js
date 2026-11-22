'use strict';

const { norm } = require('../lib/match');

const ALL = '*'; // the "all alerts" role

/**
 * Which Discord role gets pinged for incidents on which service (set with /setup role).
 * ALERT_ROLE_ID (if set) is the fallback "all alerts" role.
 */
class ServiceRoles {
  constructor({ store, fallbackRoleId = '' }) {
    this.store = store;
    this.fallbackRoleId = fallbackRoleId;
    this.map = {}; // key (normalized service name, or "*") -> { service, roleId }
  }

  async load() {
    this.map = (await this.store.getDoc('service_roles')) || {};
  }

  #key(service) {
    return service === ALL ? ALL : norm(service);
  }

  async set(service, roleId) {
    const next = { ...this.map, [this.#key(service)]: { service, roleId } };
    await this.store.setDoc('service_roles', next); // save first, then remember
    this.map = next;
  }

  /** Returns true if there was a role to remove. */
  async clear(service) {
    const key = this.#key(service);
    const had = Boolean(this.map[key]);
    const next = { ...this.map };
    delete next[key];
    await this.store.setDoc('service_roles', next);
    this.map = next;
    return had;
  }

  get(service) {
    const entry = this.map[this.#key(service)];
    return entry ? entry.roleId : null;
  }

  /** Every saved role: [{ service, roleId }] */
  list() {
    return Object.values(this.map);
  }

  /** The roles to ping for an event that touches these services (no duplicates, "all" role included). */
  rolesFor(services = []) {
    const ids = [];
    for (const service of services) {
      const id = this.get(service);
      if (id) ids.push(id);
    }
    const all = this.get(ALL) || this.fallbackRoleId;
    if (all) ids.push(all);
    return [...new Set(ids)];
  }
}

module.exports = { ServiceRoles, ALL };
