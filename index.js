'use strict';

const fs = require('node:fs');
const path = require('node:path');
const { Client, Collection, Events, GatewayIntentBits, MessageFlags } = require('discord.js');

const config = require('./src/config');
const { keepAlive } = require('./keepAlive');
const { createStore } = require('./src/services/store');
const { Tracker } = require('./src/services/tracker');
const { LogChannels } = require('./src/services/logChannels');

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
  const tracker = new Tracker({ client, store, config });
  const logChannels = new LogChannels({ client, store });
  await logChannels.load();

  client.once(Events.ClientReady, async (ready) => {
    console.log(`[Ready] Logged in as ${ready.user.tag} (${ready.user.id})`);

    try {
      const synced = await ready.application.commands.set(commands.map((c) => c.data.toJSON()));
      console.log(`[Sync] Synced ${synced.size} slash commands.`);
    } catch (error) {
      console.error(`[Sync Error] ${error.message}`);
    }

    if (!config.statusChannelId) {
      console.warn('[Tracker] STATUS_CHANNEL_ID is not set: tracking history only, nothing will be posted.');
    }
    await tracker.start();
  });

  client.on(Events.InteractionCreate, async (interaction) => {
    if (!interaction.isChatInputCommand()) return;
    const command = commands.get(interaction.commandName);
    if (!command) return;

    try {
      await command.execute(interaction, { tracker, config, logChannels });
    } catch (error) {
      console.error(`[Command Error] /${interaction.commandName}: ${error.stack || error.message}`);
      const reply = { content: 'Something went wrong while running that command.', flags: MessageFlags.Ephemeral };
      try {
        if (interaction.deferred || interaction.replied) await interaction.followUp(reply);
        else await interaction.reply(reply);
      } catch {
        // the interaction expired: nothing more to do
      }
    }
  });

  const shutdown = async (signal) => {
    console.log(`[Shutdown] ${signal} received.`);
    tracker.stop();
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
