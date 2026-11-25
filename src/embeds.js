'use strict';

const { EmbedBuilder } = require('discord.js');

const RED = 0xf43f5e;
const GREEN = 0x22c55e;

const errorEmbed = (title, description) => new EmbedBuilder().setTitle(title).setDescription(description).setColor(RED);
const successEmbed = (title, description) => new EmbedBuilder().setTitle(title).setDescription(description).setColor(GREEN);

module.exports = { errorEmbed, successEmbed };
