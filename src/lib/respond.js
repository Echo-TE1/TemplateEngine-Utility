'use strict';

const EPHEMERAL = 64; // Discord's message flag for "only visible to you" (EPHEMERAL)
const { toBody, raw } = require('./discordRaw');

const trace = (interaction, step) =>
  console.log(`[Cmd] ${interaction.commandName ? "/" + interaction.commandName : interaction.customId} ${step} (+${Date.now() - interaction.createdTimestamp}ms)`);

const withTimeout = (promise, ms, label) => {
  let timer;
  return Promise.race([
    promise,
    new Promise((_, reject) => {
      timer = setTimeout(() => reject(new Error(`${label} timed out after ${ms / 1000}s`)), ms);
    }),
  ]).finally(() => clearTimeout(timer));
};

/**
 * Fills in the "thinking..." message (or sends a follow-up). Tries, in order:
 *   1. a direct request to Discord (independent of discord.js's request queue),
 *   2. interaction.editReply,
 *   3. a follow-up message.
 * Each step has a timeout, so a stuck request can never leave the command on "thinking..." forever.
 * Every outcome is logged, so the Render logs show exactly which step worked or failed.
 */
async function editReply(interaction, payload, { fetchImpl = fetch, log = console } = {}) {
  const started = Date.now();
  const name = interaction.commandName || interaction.customId || 'interaction';
  const base = `/webhooks/${interaction.applicationId}/${interaction.token}`;
  const body = toBody(payload);
  const errors = [];

  try {
    await raw('PATCH', `${base}/messages/@original`, body, { fetchImpl });
    log.log(`[Reply] ${name} delivered (direct) in ${Date.now() - started}ms`);
    return;
  } catch (error) {
    errors.push(`direct: ${error.message}`);
  }
  try {
    await withTimeout(interaction.editReply(payload), 6000, 'editReply');
    log.log(`[Reply] ${name} delivered (editReply) in ${Date.now() - started}ms`);
    return;
  } catch (error) {
    errors.push(`editReply: ${error.message}`);
  }
  try {
    const flags = interaction.ephemeral ? EPHEMERAL : undefined;
    await raw('POST', base, { ...body, ...(flags ? { flags } : {}) }, { fetchImpl });
    log.log(`[Reply] ${name} delivered (follow-up) in ${Date.now() - started}ms`);
    return;
  } catch (error) {
    errors.push(`follow-up: ${error.message}`);
  }
  log.error(`[Reply] ${name} could NOT be delivered after ${Date.now() - started}ms. ${errors.join(' | ')}`);
  throw new Error('Could not deliver the reply to Discord.');
}

/**
 * The first answer to an interaction (a new message, or `update: true` to replace the message a button was on).
 * Sent directly to Discord's callback endpoint, which also supports every message type; if that fails,
 * discord.js's own reply/update is tried.
 */
async function reply(interaction, payload, { update = false, fetchImpl = fetch, log = console } = {}) {
  const started = Date.now();
  const name = interaction.commandName || interaction.customId || 'interaction';
  try {
    await raw('POST', `/interactions/${interaction.id}/${interaction.token}/callback`, { type: update ? 7 : 4, data: toBody(payload) }, { fetchImpl, timeoutMs: 2500 });
    log.log(`[Reply] ${name} answered (direct) in ${Date.now() - started}ms`);
    return;
  } catch (error) {
    log.warn(`[Reply] ${name} direct answer failed (${error.message}); trying discord.js`);
  }
  await withTimeout(update ? interaction.update(payload) : interaction.reply(payload), 2500, 'reply');
  log.log(`[Reply] ${name} answered (discord.js) in ${Date.now() - started}ms`);
}

/**
 * Answers a slash command once its message is ready.
 *
 * build() must return the message payload and must never throw (turn problems into an error embed).
 * If it finishes within quickMs the bot replies directly (one Discord call). Otherwise it shows
 * "thinking..." first so Discord does not expire the command (it must be acknowledged within 3s),
 * then edits that reply when the message is ready.
 */
async function respondWhenReady(interaction, build, { quickMs = 2000, ephemeral = false } = {}) {
  const work = build();
  let timer;
  const early = await Promise.race([
    work,
    new Promise((resolve) => {
      timer = setTimeout(() => resolve(null), quickMs);
    }),
  ]);
  clearTimeout(timer);

  const flags = ephemeral ? EPHEMERAL : undefined;
  const withFlags = (payload) => (flags ? { ...payload, flags: (payload.flags || 0) | flags } : payload);

  if (early) {
    trace(interaction, 'replying');
    await reply(interaction, withFlags(early));
    trace(interaction, 'replied');
    return;
  }

  trace(interaction, 'deferring');
  await interaction.deferReply(flags ? { flags } : undefined);
  trace(interaction, 'deferred');
  const payload = await work;
  trace(interaction, 'editing reply');
  await editReply(interaction, payload);
  trace(interaction, 'edited reply');
}

module.exports = { respondWhenReady, editReply, reply, trace, withTimeout };
