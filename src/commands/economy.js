'use strict';

const { SlashCommandBuilder } = require('discord.js');
const { errorEmbed, marketEmbed, itemEmbed } = require('../embeds');
const { respondWhenReady } = require('../lib/respond');

const unavailable = () => errorEmbed('Economy Tracker Off', 'The economy tracker is disabled on this bot.');

module.exports = {
  data: new SlashCommandBuilder()
    .setName('economy')
    .setDescription('Roblox limited item market: prices and recent moves.')
    .addSubcommand((s) =>
      s
        .setName('item')
        .setDescription('Show RAP, value and price history of a limited item.')
        .addStringOption((o) => o.setName('item').setDescription('Item name or ID').setRequired(true).setAutocomplete(true)))
    .addSubcommand((s) => s.setName('movers').setDescription('Show the latest big price moves and new limiteds.')),

  async autocomplete(interaction, context) {
    const { economy } = context;
    const focused = interaction.options.getFocused();
    const found = economy ? economy.search(focused, 25) : [];
    await interaction.respond(found.map((i) => ({ name: `${i.name}${i.rap ? ` (RAP ${i.rap.toLocaleString('en-US')})` : ''}`.slice(0, 100), value: i.id })));
  },

  async execute(interaction, context) {
    const { economy } = context;
    const sub = interaction.options.getSubcommand();

    const build = async () => {
      if (!economy) return { embeds: [unavailable()] };
      try {
        if (sub === 'movers') {
          const { at, movers, newItems } = economy.lastMovers;
          if (!at) return { embeds: [errorEmbed('No Moves Yet', 'No big price moves have been seen since the bot started watching the market.')] };
          return { embeds: [marketEmbed({ movers, newItems, max: 10, tracked: economy.items.size })] };
        }
        if (!economy.items.size) return { embeds: [errorEmbed('Not Ready', 'The item list has not loaded yet. Try again in a minute.')] };
        const query = interaction.options.getString('item', true);
        const [item] = economy.search(query, 1);
        if (!item) return { embeds: [errorEmbed('Item Not Found', `No limited item matches \`${query.slice(0, 60)}\`.`)] };
        let resale = null;
        try {
          resale = await economy.resale(item.id);
        } catch {
          // sales data is optional; the card says when it is missing
        }
        const [imageUrl] = await Promise.all([economy.thumbnail(item.id)]);
        return { embeds: [itemEmbed({ item, resale, history: resale ? economy.history(resale) : null, imageUrl })] };
      } catch (error) {
        console.error(`[Cmd] /economy ${sub} failed: ${error.stack || error.message}`);
        return { embeds: [errorEmbed('Economy Lookup Failed', `\`${error.message}\``)] };
      }
    };

    await respondWhenReady(interaction, build);
  },
};
