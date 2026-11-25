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
const { editReply, withTimeout } = require('./src/lib/respond');
const { MemberCountNote } = require('./src/services/memberCount');
const { TempChannels } = require('./src/services/tempChannels');
const { RobloxProfiles } = require('./src/services/robloxProfile');
const { BadgeEmojis } = require('./src/services/badgeEmojis');
const { ProfilePanel } = require('./src/services/profilePanel');
const { EconomyTracker } = require('./src/services/economy');
const { MockConsent } = require('./src/services/mockConsent');
const { MessageMirror } = require('./src/services/mirror');
const { startWatchdog } = require('./src/lib/watchdog');
const { botNoticeEmbed } = require('./src/embeds');

const COMMAND_TIMEOUT_MS = 25_000;

// Slash commands are for the owner only. Everyone else can use just the two public features, which are
// not commands: the reaction role message (reactions) and the Roblox profile lookup (button + modal).
const PUBLIC_COMMANDS = new Set(['settings']); // a person's own opt-in/out switch
const canUse = (interaction) => interaction.user.id === config.ownerId || PUBLIC_COMMANDS.has(interaction.commandName);
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
  const major = Number.parseInt(process.versions.node, 10);
  console.log(`[Boot] Node ${process.version}, discord.js ${require('discord.js').version}`);
  if (major % 2 === 1 || major > 24) console.warn(`[Boot] Node ${process.version} is not an LTS release; discord.js is tested on Node 20/22/24. package.json pins 22.x.`);

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
  const badgeEmojis = new BadgeEmojis({ client });
  const profiles = new RobloxProfiles({ cookie: config.robloxCookie });
  const profilePanel = new ProfilePanel({ store, client, buildPayload: () => commands.get('profile').panelPayload() });
  await profilePanel.load();
  const reactionRoles = new ReactionRoles({ store, client });
  await reactionRoles.load();
  const notifier = new Notifier({ logChannels, subscribers: reactionRoles });
  const tracker = new Tracker({ client, store, config, logChannels, serviceRoles, notifier });
  const health = config.healthEnabled ? new HealthMonitor({ store, config, notifier }) : null;
  const news = config.newsEnabled ? new NewsWatcher({ store, config, notifier }) : null;
  const mockConsent = new MockConsent({ store });
  await mockConsent.load();
  const economy = config.economyEnabled ? new EconomyTracker({ store, config, notifier }) : null;
  const mirror = messageContent ? new MessageMirror({ store, logChannels, config }) : null;
  if (mirror) {
    await mirror.load();
    mirror.register(client, { Events });
  }

  // Everything a command may need.
  const context = { client, store, config, logChannels, serviceRoles, notifier, tracker, health, news, economy, mockConsent, mirror, memberCount, reactionRoles, tempChannels, profiles, badgeEmojis, profilePanel, mirrorIntentRefused, startedAt };

  // A startup step that can never crash or hang the bot: it is timed, logged and limited.
  const step = async (name, fn, ms = 20_000) => {
    const started = Date.now();
    try {
      await withTimeout(Promise.resolve().then(fn), ms, name);
    } catch (error) {
      console.warn(`[Startup] ${name}: ${error.message}`);
      return;
    }
    const took = Date.now() - started;
    if (took > 2000) console.log(`[Startup] ${name} took ${(took / 1000).toFixed(1)}s`);
  };

  client.once(Events.ClientReady, async (ready) => {
    const booted = Date.now();
    console.log(`[Ready] Logged in as ${ready.user.tag} (${ready.user.id})`);

    await step('Sync commands', async () => {
      const synced = await ready.application.commands.set(commands.map((c) => c.data.toJSON()));
      console.log(`[Sync] Synced ${synced.size} slash commands.`);
    });

    if (!logChannels.get('platform_status')) {
      console.warn('[Tracker] No Platform Status channel yet. Run /setup log action:Platform Status to start posting.');
    }

    // The monitors start first, so a slow Discord lookup below can never delay them.
    await step('Tracker', () => tracker.start(), 30_000);
    notifier.start();
    tempChannels.start();
    if (memberCount) memberCount.start();
    if (health) await step('Health checks', () => health.start());
    if (news) await step('News', () => news.start());
    if (economy) await step('Economy', () => economy.start());

    // Restore things that live in Discord, side by side: log roles, the profile panel and the reaction message.
    await Promise.all([
      step('Reaction roles', async () => {
        await reactionRoles.ensureRoles(logChannels, config); // creates any missing log role
        await reactionRoles.ensureMessage(); // re-posts the message if deleted, catches up on reactions
      }),
      step('Profile panel', () => profilePanel.ensure()),
    ]);

    // Bot Updates only gets redeploy notices and mirrored messages.
    await step('Redeploy notice', () => notifier.post('bot_updates', { embeds: [botNoticeEmbed('Bot redeployed', 'The bot restarted and is running.', 'ok')] }));
    console.log(`[Ready] Startup finished in ${((Date.now() - booted) / 1000).toFixed(1)}s`);
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

  // Autocomplete (item names for /economy item).
  client.on(Events.InteractionCreate, async (interaction) => {
    if (!interaction.isAutocomplete || !interaction.isAutocomplete()) return;
    if (!canUse(interaction)) {
      await interaction.respond([]).catch(() => {});
      return;
    }
    try {
      const command = commands.get(interaction.commandName);
      if (command && command.autocomplete) await command.autocomplete(interaction, context);
    } catch (error) {
      console.error(`[Autocomplete Error] /${interaction.commandName}: ${error.message}`);
      await interaction.respond([]).catch(() => {});
    }
  });

  client.on(Events.InteractionCreate, async (interaction) => {
    if (!interaction.isChatInputCommand()) return;
    const command = commands.get(interaction.commandName);
    if (!command) return;
    if (!canUse(interaction)) {
      console.log(`[Command] /${interaction.commandName} refused for ${interaction.user.id} (owner only)`);
      await interaction.reply({ content: 'This command can only be used by the bot owner.', flags: MessageFlags.Ephemeral }).catch(() => {});
      return;
    }

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
        // Fill in "thinking..." with the error (this has its own fallbacks), or reply if nothing was sent yet.
        if (interaction.deferred) await editReply(interaction, { content: reply.content });
        else if (interaction.replied) await interaction.followUp(reply);
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
  client.on('shardDisconnect', (event, id) => console.warn(`[Discord] Shard ${id} disconnected (code ${event && event.code}).`));
  client.on('shardReconnecting', (id) => console.log(`[Discord] Shard ${id} reconnecting.`));
  client.on('shardResume', (id) => console.log(`[Discord] Shard ${id} resumed.`));
  client.on('invalidated', () => {
    console.error('[Discord] Session invalidated. Restarting.');
    process.exit(1);
  });

  // If the connection to Discord is gone for 5 minutes, exit so the host restarts the bot.
  const watchdog = startWatchdog({ isReady: () => client.isReady(), onStuck: () => process.exit(1) });
  // Memory check: the host kills the process when it runs out, which looks like "offline" with no error.
  const memTimer = setInterval(() => {
    const mb = Math.round(process.memoryUsage().rss / 1_048_576);
    if (mb > 400) console.warn(`[Memory] ${mb} MB in use (Render free plans allow 512 MB).`);
  }, 10 * 60_000);
  memTimer.unref();

  const shutdown = async (signal) => {
    console.log(`[Shutdown] ${signal} received.`);
    watchdog.stop();
    tracker.stop();
    notifier.stop();
    tempChannels.stop();
    if (memberCount) memberCount.stop();
    if (health) health.stop();
    if (news) news.stop();
    if (economy) economy.stop();
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
  process.on('uncaughtException', (error) => {
    console.error('[Uncaught Exception]', error);
    process.exit(1);
  });
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
