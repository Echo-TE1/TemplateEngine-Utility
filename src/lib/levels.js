'use strict';

// Severity levels, from best to worst.
const ORDER = ['operational', 'maintenance', 'degraded', 'partial', 'outage', 'security'];

const META = {
  operational: { mark: '✓', label: 'Operational', color: 0x2ecc71 },
  maintenance: { mark: '~', label: 'Maintenance', color: 0x3b82f6 },
  degraded: { mark: '!', label: 'Degraded', color: 0xf1c40f },
  partial: { mark: '!', label: 'Partial Outage', color: 0xe67e22 },
  outage: { mark: '✕', label: 'Major Outage', color: 0xe74c3c },
  security: { mark: '!', label: 'Security Event', color: 0x8b5cf6 },
};

// Status.io status codes.
const BY_CODE = {
  100: 'operational',
  200: 'maintenance',
  300: 'degraded',
  400: 'partial',
  500: 'outage',
  600: 'security',
};

function levelFromStatus(code, text = '') {
  const known = BY_CODE[Number(code)];
  if (known) return known;

  // Unknown code: fall back to the status text.
  const t = String(text).toLowerCase();
  if (t.includes('operational')) return 'operational';
  if (t.includes('maint')) return 'maintenance';
  if (t.includes('degrad')) return 'degraded';
  if (t.includes('partial')) return 'partial';
  if (t.includes('security')) return 'security';
  if (t.includes('disrupt') || t.includes('outage') || t.includes('down')) return 'outage';
  return 'degraded';
}

function rank(level) {
  const index = ORDER.indexOf(level);
  return index === -1 ? 0 : index;
}

function worst(levels) {
  return levels.reduce((a, b) => (rank(b) > rank(a) ? b : a), 'operational');
}

const meta = (level) => META[level] || META.degraded;

module.exports = { ORDER, META, meta, levelFromStatus, rank, worst };
