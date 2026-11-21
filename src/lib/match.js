'use strict';

// Lowercase and strip everything but letters/digits, so "Data Store" == "DataStore".
const norm = (s) => String(s ?? '').toLowerCase().replace(/[^a-z0-9]/g, '');

const uniq = (items) => [...new Set(items.filter(Boolean))];

// Words that count as a mention of a tracked service that is NOT a component on Roblox's status page.
const ALIASES = {
  authentication: [
    'authentication', 'log in', 'login', 'sign in', 'sign-in', 'signin',
    'sign up', 'signup', '2-step', 'two-step', '2fa',
  ],
};

const aliasesFor = (term) => ALIASES[norm(term)] || [String(term).toLowerCase()];

/**
 * Marks which services / incidents / maintenance belong to the tracked list.
 *  - A tracked term that equals a Roblox service name tracks that service (in every group).
 *  - A tracked term with no matching service ("virtual", e.g. Authentication) is matched
 *    against the text of incidents instead.
 * Adds `tracked` flags and `snapshot.tracking`. Returns the same snapshot.
 */
function annotate(snapshot, terms) {
  const keys = terms.map((term) => ({ term, key: norm(term) }));

  const matches = {};
  for (const { term, key } of keys) {
    matches[term] = snapshot.services.filter((s) => norm(s.name) === key).map((s) => s.key);
  }
  const virtual = keys.filter(({ term }) => matches[term].length === 0).map(({ term }) => term);

  for (const service of snapshot.services) {
    service.tracked = keys.some(({ key }) => key === norm(service.name));
  }

  const tag = (item) => {
    const byService = keys
      .filter(({ key }) => item.services.some((name) => norm(name) === key))
      .map(({ term }) => term);
    const text = `${item.name} ${item.messages.map((m) => m.details).join(' ')}`.toLowerCase();
    const byText = virtual.filter((term) => aliasesFor(term).some((alias) => text.includes(alias)));
    item.trackedTerms = uniq([...byService, ...byText]);
    // If Roblox does not say what is affected, we cannot rule it out, so treat it as tracked.
    item.tracked = item.trackedTerms.length > 0 || (item.services.length === 0 && item.groups.length === 0);
  };

  snapshot.incidents.forEach(tag);
  snapshot.maintenance.active.forEach(tag);
  snapshot.maintenance.upcoming.forEach(tag);

  snapshot.tracking = { terms, matches, virtual };
  return snapshot;
}

module.exports = { norm, uniq, annotate, aliasesFor };
