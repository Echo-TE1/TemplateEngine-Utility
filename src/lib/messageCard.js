'use strict';

const path = require('node:path');

// Draws a simple chat-style message card (own colours and layout, not a copy of any app's screen).
// Used by /quote (a real message, no watermark) and /mock (made-up message, always watermarked).

let canvasLib = null;
function canvas() {
  if (!canvasLib) {
    canvasLib = require('@napi-rs/canvas');
    const dir = path.join(__dirname, '..', '..', 'fonts');
    canvasLib.GlobalFonts.registerFromPath(path.join(dir, 'Inter-Regular.otf'), 'Inter');
    canvasLib.GlobalFonts.registerFromPath(path.join(dir, 'Inter-Bold.otf'), 'Inter');
  }
  return canvasLib;
}

/** Splits text into lines no wider than maxWidth. measure(text) -> width. Long words are broken. Pure. */
function wrapText(measure, text, maxWidth, maxLines = 12) {
  const lines = [];
  for (const paragraph of String(text).split('\n')) {
    let line = '';
    for (const word of paragraph.split(/\s+/).filter(Boolean)) {
      let piece = word;
      while (measure(piece) > maxWidth && piece.length > 1) {
        let cut = piece.length - 1;
        while (cut > 1 && measure(piece.slice(0, cut)) > maxWidth) cut--;
        if (line) {
          lines.push(line);
          line = '';
        }
        lines.push(piece.slice(0, cut));
        piece = piece.slice(cut);
      }
      const candidate = line ? `${line} ${piece}` : piece;
      if (measure(candidate) <= maxWidth) line = candidate;
      else {
        lines.push(line);
        line = piece;
      }
    }
    lines.push(line);
  }
  if (lines.length > maxLines) {
    const kept = lines.slice(0, maxLines);
    kept[maxLines - 1] = `${kept[maxLines - 1].replace(/\s+$/, '').slice(0, -1)}...`;
    return kept;
  }
  return lines;
}

const PALETTE = { red: '#ed4245', blue: '#5865f2', green: '#3ba55d', orange: '#f0883e', purple: '#a35bd6', teal: '#1abc9c' };

/**
 * @param {{ name:string, text:string, color?:string, avatar?:Buffer|null, time?:string, footer?:string }} card
 * @param {{ watermark?:boolean }} options
 * @returns {Promise<Buffer>} a PNG
 */
async function renderMessageCard(card, { watermark = false } = {}) {
  const { createCanvas, loadImage } = canvas();
  const width = 720;
  const pad = 28;
  const textX = pad + 72;
  const maxText = width - textX - pad;
  const top = watermark ? 56 : 0;

  const probe = createCanvas(10, 10).getContext('2d');
  probe.font = '22px Inter';
  const lines = wrapText((s) => probe.measureText(s).width, card.text, maxText);
  const footerLines = card.footer ? 1 : 0;
  const height = top + pad + 34 + lines.length * 32 + 20 + footerLines * 30 + pad + (watermark ? 56 : 0);

  const c = createCanvas(width, height);
  const ctx = c.getContext('2d');
  ctx.fillStyle = '#2b2d31';
  ctx.fillRect(0, 0, width, height);

  // Avatar: the supplied picture, or a coloured circle with the first letter.
  const cx = pad + 28;
  const cy = top + pad + 28;
  ctx.save();
  ctx.beginPath();
  ctx.arc(cx, cy, 28, 0, Math.PI * 2);
  ctx.closePath();
  ctx.clip();
  let drawn = false;
  if (card.avatar) {
    try {
      ctx.drawImage(await loadImage(card.avatar), cx - 28, cy - 28, 56, 56);
      drawn = true;
    } catch {
      drawn = false;
    }
  }
  if (!drawn) {
    ctx.fillStyle = PALETTE[card.color] || PALETTE.blue;
    ctx.fillRect(cx - 28, cy - 28, 56, 56);
    ctx.fillStyle = '#ffffff';
    ctx.font = 'bold 26px Inter';
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.fillText(([...String(card.name)][0] || '?').toUpperCase(), cx, cy + 1);
  }
  ctx.restore();

  ctx.textAlign = 'left';
  ctx.textBaseline = 'alphabetic';
  ctx.font = 'bold 22px Inter';
  ctx.fillStyle = PALETTE[card.color] || '#f2f3f5';
  ctx.fillText(card.name, textX, top + pad + 22);
  if (card.time) {
    const nameWidth = ctx.measureText(card.name).width;
    ctx.font = '15px Inter';
    ctx.fillStyle = '#949ba4';
    ctx.fillText(card.time, textX + nameWidth + 12, top + pad + 22);
  }
  ctx.font = '22px Inter';
  ctx.fillStyle = '#dbdee1';
  lines.forEach((line, i) => ctx.fillText(line, textX, top + pad + 34 + 24 + i * 32));
  if (card.footer) {
    ctx.font = '15px Inter';
    ctx.fillStyle = '#949ba4';
    ctx.fillText(card.footer, textX, height - (watermark ? 56 : 0) - pad + 2);
  }

  if (watermark) require('./watermark').applyWatermark(ctx, width, height);
  return c.toBuffer('image/png');
}

module.exports = { renderMessageCard, wrapText, PALETTE };
