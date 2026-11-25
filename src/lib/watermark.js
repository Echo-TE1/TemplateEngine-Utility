'use strict';

// The watermark system for generated images. Kept apart from the card drawing so it can be reused by any
// image the bot makes. It is fully opaque and tiled across the whole picture, so it cannot be cropped out or
// painted over without wrecking the image.

const DEFAULT_TEXT = 'MOCK';
const COLOR = '#ff2d55';

/**
 * Where to draw the repeated label: a diagonal grid that covers the whole image (and a margin beyond it,
 * so rotated rows leave no gaps). Pure, so it can be tested.
 * @returns {{x:number,y:number}[]}
 */
function tilePositions(width, height, stepX, stepY) {
  const out = [];
  const reach = Math.max(width, height);
  let row = 0;
  for (let y = -reach; y <= height + reach; y += stepY, row++) {
    const offset = (row % 2) * (stepX / 2);
    for (let x = -reach + offset; x <= width + reach; x += stepX) out.push({ x, y });
  }
  return out;
}

/**
 * Draws the watermark over everything already on the canvas context:
 * a tiled, rotated label at full strength plus a solid banner along the top and the bottom.
 */
function applyWatermark(ctx, width, height, { text = DEFAULT_TEXT, banner = 'MOCK-UP - NOT A REAL MESSAGE', font = 'Inter' } = {}) {
  const size = Math.max(22, Math.round(Math.min(width, height) / 9));
  ctx.save();
  ctx.translate(width / 2, height / 2);
  ctx.rotate(-Math.PI / 7);
  ctx.translate(-width / 2, -height / 2);
  ctx.font = `bold ${size}px ${font}`;
  ctx.textAlign = 'center';
  ctx.textBaseline = 'middle';
  ctx.lineJoin = 'round';
  ctx.lineWidth = Math.max(3, size / 8);
  const stepX = Math.round(ctx.measureText(text).width + size * 3.2);
  const stepY = Math.round(size * 3.6);
  for (const { x, y } of tilePositions(width, height, stepX, stepY)) {
    ctx.strokeStyle = '#ffffff';
    ctx.strokeText(text, x, y);
    ctx.fillStyle = COLOR;
    ctx.fillText(text, x, y);
  }
  ctx.restore();

  const barHeight = Math.max(26, Math.round(size * 0.9));
  ctx.fillStyle = COLOR;
  ctx.fillRect(0, 0, width, barHeight);
  ctx.fillRect(0, height - barHeight, width, barHeight);
  ctx.fillStyle = '#ffffff';
  ctx.font = `bold ${Math.round(barHeight * 0.55)}px ${font}`;
  ctx.textAlign = 'center';
  ctx.textBaseline = 'middle';
  ctx.fillText(banner, width / 2, barHeight / 2);
  ctx.fillText(banner, width / 2, height - barHeight / 2);
}

module.exports = { applyWatermark, tilePositions, DEFAULT_TEXT };
