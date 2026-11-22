'use strict';

const fs = require('node:fs');
const path = require('node:path');
const { Client, Collection, Events, GatewayIntentBits, MessageFlags } = require('discord.js');

const config = require('./src/config');
const { keepAlive } = require('./keepAlive');
const { createStore } = require('./src/services/store');
const { Tracker } = require('./src/services/tracker');
const { LogChannels } = require('./src/services/logChannels');
const { ServiceRoles } = require('./src/services/serviceRoles');
const { Notifier } = require('./src/services/notifier');
const { HealthMonitor } = require('./src/services/health');
const { NewsWatcher } = require('./src/services/news');
const { BackupScheduler } = require('./src/services/backup');
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

async function main() {
  config.validate();

  // Start the web server so Render and the cron job can ping the bot.
  keepAlive(config.port);

  const client = new Client({ intents: [GatewayIntentBits.Guilds] });
  const commands = loadCommands();
  const store = await createStore(config);
  const logChannels = new LogChannels({ client, store });
  await logChannels.load();
  const serviceRoles = new ServiceRoles({ store, fallbackRoleId: config.alertRoleId });
  await serviceRoles.load();
  const notifier = new Notifier({ logChannels });
  const tracker = new Tracker({ client, store, config, logChannels, serviceRoles, notifier });
  const health = config.healthEnabled ? new HealthMonitor({ store, config, notifier }) : null;
  const news = config.newsEnabled ? new NewsWatcher({ store, config, notifier }) : null;
  const backups = new BackupScheduler({ store, logChannels, serviceRoles, notifier, config });

  // Everything a command may need.
  const context = { client, store, config, logChannels, serviceRoles, notifier, tracker, health, news, startedAt };

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
    if (health) await health.start();
    if (news) await news.start();
    backups.start();

    // Tell the Bot Updates log that the bot is up (and warn if its database is not permanent).
    const warnings = [];
    if (store.fellBack) warnings.push('MongoDB could not be reached, so local files are used. Settings and history are lost on redeploy.');
    else if (!store.persistent) warnings.push('No MONGO_URI is set, so settings and history are lost on redeploy.');
    const commit = (process.env.RENDER_GIT_COMMIT || '').slice(0, 7);
    await notifier.post('bot_updates', {
      embeds: [
        botNoticeEmbed(
          '🟢 Bot started',
          `Storage: ${store.kind}${commit ? ` • Version ${commit}` : ''}\nRoblox status checked every ${config.pollSeconds}s.` +
            (warnings.length ? `\n\n⚠️ ${warnings.join('\n⚠️ ')}` : ''),
          warnings.length ? 'warn' : 'ok',
        ),
      ],
    });
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
    if (health) health.stop();
    if (news) news.stop();
    backups.stop();
    try {
      await store.close();
    } catch {
      // ignore
    }
    await client.destroy();
    process.exit(0);
  };
  process.on('SIGTERM', () => shutdown('SIGTERM'));
  process.on('SIGINT', () => shutdown('SIGINT'));
  process.on('unhandledRejection', (error) => console.error('[Unhandled Rejection]', error));

  await client.login(config.token);
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
