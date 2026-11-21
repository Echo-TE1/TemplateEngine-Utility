'use strict';

function trim(text, max) {
  const s = String(text ?? '');
  return s.length <= max ? s : `${s.slice(0, Math.max(0, max - 1)).trimEnd()}…`;
}

function unix(iso) {
  const ms = Date.parse(iso);
  return Number.isFinite(ms) ? Math.floor(ms / 1000) : null;
}

/** Discord timestamp markup, e.g. ts(iso, 'R') -> "3 minutes ago". */
function ts(iso, style = 'F') {
  const seconds = unix(iso);
  return seconds === null ? 'Unknown' : `<t:${seconds}:${style}>`;
}

function duration(ms) {
  if (!Number.isFinite(ms) || ms < 0) return 'unknown';
  const s = Math.round(ms / 1000);
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ${m % 60}m`;
  const d = Math.floor(h / 24);
  return `${d}d ${h % 24}h`;
}

// Incident message states used by Status.io.
const STATE_LABELS = { 100: 'Investigating', 200: 'Identified', 300: 'Monitoring', 400: 'Resolved' };

function stateLabel(state) {
  if (STATE_LABELS[state]) return STATE_LABELS[state];
  if (typeof state === 'string' && state && Number.isNaN(Number(state))) {
    return state[0].toUpperCase() + state.slice(1);
  }
  return 'Update';
}

module.exports = { trim, ts, unix, duration, stateLabel };
