'use strict';

// Pure logic for the direct health checks: turns a stream of "answered / did not answer" results
// into rare, stable "went down" / "recovered" events (no flapping, no duplicate alerts).

const RECOVER_AFTER = 2; // good checks in a row before a down target counts as recovered

function emptyTarget() {
  return { status: 'unknown', fails: 0, oks: 0, downSince: null };
}

/** A check is "ok" if the server answered with anything below HTTP 500 (401/403/405 still prove it is up). */
const isOk = (httpStatus) => Number.isInteger(httpStatus) && httpStatus < 500;

/**
 * @param prev       previous target state (or undefined for a new target)
 * @param ok         did this check succeed?
 * @param failAfter  failed checks in a row before reporting "down"
 * @param now        ISO time of this check
 * @returns { next, change }  change is null, 'down' or 'up'
 */
function evaluate(prev, ok, failAfter, now) {
  const next = { ...emptyTarget(), ...prev };
  let change = null;

  if (ok) {
    next.fails = 0;
    if (next.status === 'down') {
      next.oks += 1;
      if (next.oks >= RECOVER_AFTER) {
        change = 'up';
        next.status = 'up';
        next.oks = 0;
      }
    } else {
      next.status = 'up'; // first good check is a silent baseline
      next.oks = 0;
    }
  } else {
    next.oks = 0;
    next.fails += 1;
    if (next.status !== 'down' && next.fails >= failAfter) {
      change = 'down';
      next.status = 'down';
      next.downSince = now;
    }
  }

  if (change === 'up') {
    next.lastDownSince = next.downSince; // so the "recovered" message can say how long it was down
    next.downSince = null;
  }
  return { next, change };
}

/** Which /setup log a target reports to. */
const logFor = (name) => (String(name).trim().toLowerCase() === 'website' ? 'website_status' : 'api_status');

module.exports = { evaluate, isOk, emptyTarget, logFor, RECOVER_AFTER };
