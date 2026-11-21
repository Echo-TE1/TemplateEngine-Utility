'use strict';

const { MessageFlags } = require('discord.js');

const trace = (interaction, step) =>
  console.log(`[Cmd] /${interaction.commandName} ${step} (+${Date.now() - interaction.createdTimestamp}ms)`);

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

  const flags = ephemeral ? MessageFlags.Ephemeral : undefined;

  if (early) {
    trace(interaction, 'replying');
    await interaction.reply(flags ? { ...early, flags } : early);
    trace(interaction, 'replied');
    return;
  }

  trace(interaction, 'deferring');
  await interaction.deferReply(flags ? { flags } : undefined);
  trace(interaction, 'deferred');
  const payload = await work;
  trace(interaction, 'editing reply');
  await interaction.editReply(payload);
  trace(interaction, 'edited reply');
}

module.exports = { respondWhenReady, trace };
