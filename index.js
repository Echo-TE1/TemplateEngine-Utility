'use strict';

const fs = require('node:fs');
const path = require('node:path');
const { Client, Collection, Events, GatewayIntentBits, MessageFlags, Partials } = require('discord.js');

const config = require('./src/config');
const { keepAlive } = require('./keepAlive');
const { createStore } = require('./src/services/store');
const { Tracker } = require('./src/services/tracker');
const { LogChannels } = require('./src/services/logChannels');
const { ServiceRoles } = require('./src/services/serviceRoles');
const { Notifier } = require('./src/services/notifier');
const { HealthMonitor } = require('./src/services/health');
const { NewsWatcher } = require('./src/services/news');
const { ReactionRoles } = require('./src/services/reactionRoles');
const { MemberCountNote } = require('./src/services/memberCount');
const { TempChannels } = require('./src/services/tempChannels');
const { RobloxProfiles } = require('./src/services/robloxProfile');
const { ProfilePanel } = require('./src/services/profilePanel');
const { MessageMirror } = require('./src/services/mirror');
const { botNoticeEmbed } = require('./src/embeds');

const COMMAND_TIMEOUT_MS = 25_000;
const startedAt = Date.now();

// Every file in src/commands exports { data, execute }. Add more commands by dropping files there.
function loadCommands() {
  const commands = new Collection();
  const dir = path.join(__dirname, 'src', 'commands');
  for (const file of fs.readdirSync(dir).filter((f) => f.endsWith('.js'))) {
    try {
      const command = require(path.join(dir, file));
      commands.set(command.data.name, command);
      console.log(`[Command Loaded] /${command.data.name}`);
    } catch (error) {
      console.error(`[Command Error] Failed to load ${file}: ${error.message}`);
    }
  }
  return commands;
}

// Reading message text needs the privileged Message Content intent (Developer Portal > Bot).
// If Discord refuses it, boot() is run again without it so the rest of the bot keeps working.
async function main() {
  config.validate();

  // Start the web server so Render and the cron job can ping the bot.
  keepAlive(config.port);

  try {
    await boot({ messageContent: config.mirrorEnabled });
  } catch (error) {
    if (config.mirrorEnabled && /disallowed intents/i.test(error.message)) {
      console.error('[Mirror] Discord refused the Message Content intent. Enable it in the Developer Portal (Bot > Privileged Gateway Intents). Starting without the message mirror.');
      await boot({ messageContent: false, mirrorIntentRefused: true });
    } else {
      throw error;
    }
  }
}

async function boot({ messageContent, mirrorIntentRefused = false }) {
  const intents = [GatewayIntentBits.Guilds, GatewayIntentBits.GuildMessageReactions];
  if (messageContent) intents.push(GatewayIntentBits.GuildMessages, GatewayIntentBits.MessageContent);
  const client = new Client({
    intents,
    partials: [Partials.Message, Partials.Channel, Partials.Reaction, Partials.User],
  });
  const commands = loadCommands();
  const store = await createStore(config);
  const logChannels = new LogChannels({ client, store });
  await logChannels.load();
  const serviceRoles = new ServiceRoles({ store, fallbackRoleId: config.alertRoleId });
  await serviceRoles.load();
  const memberCount = config.memberCountEnabled ? new MemberCountNote({ client, guildId: config.memberCountGuildId }) : null;
  const tempChannels = new TempChannels({ client, store });
  await tempChannels.load();
  const profiles = new RobloxProfiles({ cookie: config.robloxCookie });
  const profilePanel = new ProfilePanel({ store, client, buildPayload: () => commands.get('profile').panelPayload() });
  await profilePanel.load();
  const reactionRoles = new ReactionRoles({ store, client });
  await reactionRoles.load();
  const notifier = new Notifier({ logChannels, subscribers: reactionRoles });
  const tracker = new Tracker({ client, store, config, logChannels, serviceRoles, notifier });
  const health = config.healthEnabled ? new HealthMonitor({ store, config, notifier }) : null;
  const news = config.newsEnabled ? new NewsWatcher({ store, config, notifier }) : null;
  const mirror = messageContent ? new MessageMirror({ store, logChannels, config }) : null;
  if (mirror) {
    await mirror.load();
    mirror.register(client, { Events });
  }

  // Everything a command may need.
  const context = { client, store, config, logChannels, serviceRoles, notifier, tracker, health, news, mirror, memberCount, reactionRoles, tempChannels, profiles, profilePanel, mirrorIntentRefused, startedAt };

  client.once(Events.ClientReady, async (ready) => {
    console.log(`[Ready] Logged in as ${ready.user.tag} (${ready.user.id})`);

    try {
      const synced = await ready.application.commands.set(commands.map((c) => c.data.toJSON()));
      console.log(`[Sync] Synced ${synced.size} slash commands.`);
    } catch (error) {
      console.error(`[Sync Error] ${error.message}`);
    }

    if (!logChannels.get('platform_status')) {
      console.warn('[Tracker] No Platform Status channel yet. Run /setup log action:Platform Status to start posting.');
    }
    await tracker.start();
    notifier.start();
    tempChannels.start();
    if (memberCount) memberCount.start();
    if (health) await health.start();
    if (news) await news.start();

    // Make sure every log has its reaction role (creates any that are missing).
    await reactionRoles.ensureRoles(logChannels, config).catch((e) => console.warn(`[ReactionRoles] ${e.message}`));
    await profilePanel.ensure().catch((e) => console.warn(`[Profile] ${e.message}`));
    // Restore the reaction message (re-post if deleted) and catch up on reactions made while offline.
    await reactionRoles.ensureMessage().catch((e) => console.warn(`[ReactionRoles] ${e.message}`));

    // Bot Updates only gets redeploy notices and mirrored messages.
    await notifier.post('bot_updates', { embeds: [botNoticeEmbed('Bot redeployed', 'The bot restarted and is running.', 'ok')] });
  });

  client.on(Events.MessageReactionAdd, (reaction, user) =>
    reactionRoles.handleAdd(reaction, user).catch((e) => console.error(`[ReactionRoles] add failed: ${e.message}`)));
  client.on(Events.MessageReactionRemove, (reaction, user) =>
    reactionRoles.handleRemove(reaction, user).catch((e) => console.error(`[ReactionRoles] remove failed: ${e.message}`)));

  // Buttons and the modal of the Roblox profile lookup.
  client.on(Events.InteractionCreate, async (interaction) => {
    const isButton = interaction.isButton && interaction.isButton();
    const isModal = interaction.isModalSubmit && interaction.isModalSubmit();
    if (!(isButton || isModal) || !interaction.customId.startsWith('rp:')) return;
    try {
      const profile = commands.get('profile');
      if (isButton) await profile.handleButton(interaction, context);
      else await profile.handleModal(interaction, context);
    } catch (error) {
      console.error(`[Component Error] ${interaction.customId}: ${error.stack || error.message}`);
    }
  });

  client.on(Events.InteractionCreate, async (interaction) => {
    if (!interaction.isChatInputCommand()) return;
    const command = commands.get(interaction.commandName);
    if (!command) return;

    const started = Date.now();
    console.log(`[Command] /${interaction.commandName} started by ${interaction.user.id}`);
    let timer;
    try {
      // If a command takes too long, reply with an error instead of leaving Discord on "thinking...".
      const timeout = new Promise((_, reject) => {
        timer = setTimeout(() => reject(new Error(`Timed out after ${COMMAND_TIMEOUT_MS / 1000}s`)), COMMAND_TIMEOUT_MS);
      });
      await Promise.race([command.execute(interaction, context), timeout]);
      console.log(`[Command] /${interaction.commandName} finished in ${Date.now() - started}ms`);
    } catch (error) {
      console.error(`[Command Error] /${interaction.commandName}: ${error.stack || error.message}`);
      const reply = { content: 'Something went wrong while running that command.', flags: MessageFlags.Ephemeral };
      try {
        if (interaction.deferred || interaction.replied) await interaction.followUp(reply);
        else await interaction.reply(reply);
      } catch (replyError) {
        console.error(`[Command Error] Could not send the error reply: ${replyError.message}`);
      }
    } finally {
      clearTimeout(timer);
    }
  });

  client.on('error', (error) => console.error(`[Discord Error] ${error.message}`));
  client.on('shardError', (error) => console.error(`[Discord Connection Error] ${error.message}`));

  const shutdown = async (signal) => {
    console.log(`[Shutdown] ${signal} received.`);
    tracker.stop();
    notifier.stop();
    tempChannels.stop();
    if (memberCount) memberCount.stop();
    if (health) health.stop();
    if (news) news.stop();
    try {
      await store.close();
    } catch {
      // ignore
    }
    await client.destroy();
    process.exit(0);
  };

  try {
    await client.login(config.token);
  } catch (error) {
    await client.destroy().catch(() => {});
    throw error;
  }
  process.on('SIGTERM', () => shutdown('SIGTERM'));
  process.on('SIGINT', () => shutdown('SIGINT'));
  process.on('unhandledRejection', (error) => console.error('[Unhandled Rejection]', error));
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
