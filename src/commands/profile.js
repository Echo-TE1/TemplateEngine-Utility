'use strict';

const {
  SlashCommandBuilder,
  MessageFlags,
  ChannelType,
  ActionRowBuilder,
  ButtonBuilder,
  ButtonStyle,
  ModalBuilder,
  TextInputBuilder,
  TextInputStyle,
} = require('discord.js');
const { errorEmbed, successEmbed, profileEmbed, profilePanelEmbed } = require('../embeds');
const { ProfileError } = require('../services/robloxProfile');
const { editReply, respondWhenReady } = require('../lib/respond');

const PREFIX = 'rp:';
const SEARCH = `${PREFIX}search`;
const MODAL = `${PREFIX}modal`;
const REFRESH = `${PREFIX}refresh:`;
const LOOKUP_TIMEOUT_MS = 20_000;
const COOLDOWN_MS = 5_000;

const cooldowns = new Map(); // user id -> time of last lookup

function onCooldown(userId) {
  const now = Date.now();
  const last = cooldowns.get(userId) || 0;
  if (now - last < COOLDOWN_MS) return Math.ceil((COOLDOWN_MS - (now - last)) / 1000);
  cooldowns.set(userId, now);
  if (cooldowns.size > 500) cooldowns.delete(cooldowns.keys().next().value);
  return 0;
}

/** The message under the panel embed: a single button. */
function panelPayload() {
  return {
    embeds: [profilePanelEmbed()],
    components: [
      new ActionRowBuilder().addComponents(
        new ButtonBuilder().setCustomId(SEARCH).setLabel('Search Roblox User').setStyle(ButtonStyle.Primary),
      ),
    ],
    allowedMentions: { parse: [] },
  };
}

// The result card: View Profile (link) + Refresh.
function resultPayload(profile) {
  return {
    embeds: [profileEmbed(profile)],
    components: [
      new ActionRowBuilder().addComponents(
        new ButtonBuilder().setLabel('View Profile').setStyle(ButtonStyle.Link).setURL(profile.url),
        new ButtonBuilder().setCustomId(`${REFRESH}${profile.id}`).setLabel('Refresh').setStyle(ButtonStyle.Secondary),
      ),
    ],
    allowedMentions: { parse: [] },
  };
}

const TITLES = {
  invalid: 'Invalid username',
  notfound: 'User not found',
  ratelimited: 'Slow down',
  error: 'Roblox is not answering',
};

function problem(error) {
  if (error instanceof ProfileError) return { embeds: [errorEmbed(TITLES[error.kind] || 'Lookup failed', error.message)], components: [] };
  console.error(`[Profile] ${error.stack || error.message}`);
  return { embeds: [errorEmbed('Lookup failed', 'Something went wrong while looking that up. Please try again.')], components: [] };
}

// Runs a lookup but never takes longer than LOOKUP_TIMEOUT_MS, so the reply can never hang.
async function withTimeout(promise) {
  let timer;
  const timeout = new Promise((_, reject) => {
    timer = setTimeout(() => reject(new ProfileError('error', 'Roblox took too long to answer. Please try again.')), LOOKUP_TIMEOUT_MS);
  });
  try {
    return await Promise.race([promise, timeout]);
  } finally {
    clearTimeout(timer);
  }
}

// Runs a lookup and returns the message to show (the profile card or a friendly error). Never throws.
async function lookupPayload(run) {
  try {
    return resultPayload(await withTimeout(run()));
  } catch (error) {
    return problem(error);
  }
}

async function finish(interaction, payload) {
  try {
    await editReply(interaction, payload);
  } catch (error) {
    console.error(`[Profile] Could not deliver the result: ${error.message}`);
  }
}

module.exports = {
  panelPayload,

  data: new SlashCommandBuilder()
    .setName('profile')
    .setDescription('Bot owner only: Roblox profile lookup.')
    .addSubcommandGroup((group) =>
      group
        .setName('tracker')
        .setDescription('Roblox profile tracker.')
        .addSubcommand((sub) =>
          sub
            .setName('panel')
            .setDescription('Post the permanent "Search Roblox User" panel in a channel.')
            .addChannelOption((o) =>
              o
                .setName('channel')
                .setDescription('Channel for the panel')
                .setRequired(true)
                .addChannelTypes(ChannelType.GuildText, ChannelType.GuildAnnouncement),
            ),
        ),
    ),

  async execute(interaction, context) {
    if (interaction.user.id !== context.config.ownerId) {
      await interaction.reply({
        embeds: [errorEmbed('Permission Denied', 'This command can only be used by the bot owner.')],
        flags: MessageFlags.Ephemeral,
      });
      return;
    }
    const channel = interaction.options.getChannel('channel', true);
    await interaction.deferReply({ flags: MessageFlags.Ephemeral });
    try {
      const target = await interaction.client.channels.fetch(channel.id);
      await context.profilePanel.publish(target);
      await finish(interaction, { embeds: [successEmbed('Panel posted', `The lookup panel is now in <#${channel.id}>. It is kept in place across redeploys.`)] });
    } catch (error) {
      await finish(interaction, {
        embeds: [errorEmbed('Cannot Post There', `${error.message}\nThe bot needs View Channel, Send Messages and Embed Links in <#${channel.id}>.`)],
      });
    }
  },

  /** Buttons: "Search Roblox User" (opens the modal) and "Refresh". */
  async handleButton(interaction, context) {
    if (interaction.customId === SEARCH) {
      // The modal has exactly one field: the username. Nothing else can be typed or sent.
      const modal = new ModalBuilder()
        .setCustomId(MODAL)
        .setTitle('Search Roblox User')
        .addComponents(
          new ActionRowBuilder().addComponents(
            new TextInputBuilder()
              .setCustomId('username')
              .setLabel('Roblox username')
              .setStyle(TextInputStyle.Short)
              .setMinLength(3)
              .setMaxLength(20)
              .setRequired(true),
          ),
        );
      await interaction.showModal(modal);
      return;
    }

    if (interaction.customId.startsWith(REFRESH)) {
      const id = interaction.customId.slice(REFRESH.length);
      if (!/^\d{1,20}$/.test(id)) return interaction.deferUpdate();
      const wait = onCooldown(interaction.user.id);
      if (wait) {
        return interaction.reply({ embeds: [errorEmbed('Slow down', `Please wait ${wait}s before refreshing again.`)], flags: MessageFlags.Ephemeral });
      }
      // Fast path: if Roblox answers within 2.5s the card replaces the old one in a single step.
      const work = lookupPayload(() => context.profiles.byId(id, { force: true }));
      let timer;
      const early = await Promise.race([work, new Promise((resolve) => { timer = setTimeout(() => resolve(null), 2500); })]);
      clearTimeout(timer);
      if (early) return interaction.update(early);
      await interaction.deferUpdate();
      return finish(interaction, await work);
    }
  },

  /** The modal: look the username up and show the result privately. */
  async handleModal(interaction, context) {
    if (interaction.customId !== MODAL) return;
    const wait = onCooldown(interaction.user.id);
    if (wait) {
      return interaction.reply({ embeds: [errorEmbed('Slow down', `Please wait ${wait}s before searching again.`)], flags: MessageFlags.Ephemeral });
    }
    // Fast path: reply in one step when Roblox answers quickly, otherwise "thinking..." then the result.
    const username = interaction.fields.getTextInputValue('username');
    await respondWhenReady(interaction, () => lookupPayload(() => context.profiles.lookup(username)), {
      quickMs: 2500,
      ephemeral: true,
    });
  },
};
