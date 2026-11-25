'use strict';

const fs = require('node:fs');
const path = require('node:path');
const { Client, Collection, Events, GatewayIntentBits, MessageFlags } = require('discord.js');

const config = require('./src/config');
const { keepAlive } = require('./keepAlive');
const { createStore } = require('./src/services/store');
const { MockConsent } = require('./src/services/mockConsent');
const { editReply } = require('./src/lib/respond');
const { startWatchdog } = require('./src/lib/watchdog');

const COMMAND_TIMEOUT_MS = 25_000;
const startedAt = Date.now();

// Slash commands are for the owner only, except these.
const PUBLIC_COMMANDS = new Set(['settings', 'help']);
const canUse = (interaction) => interaction.user.id === config.ownerId || PUBLIC_COMMANDS.has(interaction.commandName);

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

// /quote reads message text, which needs the privileged Message Content intent (Developer Portal > Bot).
// If Discord refuses it, boot() is run again without it so the rest of the bot keeps working.
async function main() {
  config.validate();
  console.log(`[Boot] Node ${process.version}, discord.js ${require('discord.js').version}`);
  keepAlive(config.port);
  try {
    await boot({ messageContent: true });
  } catch (error) {
    if (/disallowed intents/i.test(error.message)) {
      console.error('[Boot] Discord refused the Message Content intent. Enable it in the Developer Portal (Bot > Privileged Gateway Intents) so /quote can read text. Starting without it.');
      await boot({ messageContent: false });
    } else {
      throw error;
    }
  }
}

async function boot({ messageContent }) {
  const intents = [GatewayIntentBits.Guilds];
  if (messageContent) intents.push(GatewayIntentBits.GuildMessages, GatewayIntentBits.MessageContent);
  const client = new Client({ intents });
  const commands = loadCommands();
  const store = await createStore(config);
  const mockConsent = new MockConsent({ store });
  await mockConsent.load();
  const context = { client, store, config, mockConsent, messageContent, startedAt };

  client.once(Events.ClientReady, async (ready) => {
    console.log(`[Ready] Logged in as ${ready.user.tag} (${ready.user.id})`);
    try {
      const synced = await ready.application.commands.set(commands.map((c) => c.data.toJSON()));
      console.log(`[Sync] Synced ${synced.size} slash commands.`);
    } catch (error) {
      console.warn(`[Sync] Could not sync commands: ${error.message}`);
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
  client.on('shardResume', (id) => console.log(`[Discord] Shard ${id} resumed.`));
  client.on('invalidated', () => {
    console.error('[Discord] Session invalidated. Restarting.');
    process.exit(1);
  });

  // If the connection to Discord is gone for 5 minutes, exit so the host restarts the bot.
  const watchdog = startWatchdog({ isReady: () => client.isReady(), onStuck: () => process.exit(1) });

  const shutdown = async (signal) => {
    console.log(`[Shutdown] ${signal} received.`);
    watchdog.stop();
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
    watchdog.stop();
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
