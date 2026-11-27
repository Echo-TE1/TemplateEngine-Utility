"""BotLogCog: logs bot data events (servers joined / left, saved defaults)
to the bot-data log channel. Generated text and images are never logged."""

from __future__ import annotations

import discord
from discord.ext import commands

from config import BOT_DATA_LOG_CHANNEL_ID
from logcard import BLUE, GOLD, GREEN, RED, send_log


def totals(bot: commands.Bot) -> tuple[int, int]:
    users = (sum(1 for u in bot.users if not u.bot) if bot.intents.members
             else sum(g.member_count or 0 for g in bot.guilds))
    return len(bot.guilds), users


def guild_items(bot: commands.Bot, guild: discord.Guild) -> list[tuple[str, str]]:
    servers, users = totals(bot)
    owner = f"<@{guild.owner_id}>" if guild.owner_id else "Unknown"
    return [
        ("Server", f"{guild.name}\n`{guild.id}`"),
        ("Owner", owner),
        ("Members", f"`{guild.member_count or 0:,}`"),
        ("Created", f"<t:{int(guild.created_at.timestamp())}:D>"),
        ("Bot Totals", f"`{servers:,}` servers • `{users:,}` individual users"),
    ]


class BotLogCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @commands.Cog.listener()
    async def on_guild_join(self, guild: discord.Guild) -> None:
        await send_log(self.bot, BOT_DATA_LOG_CHANNEL_ID, "Server Joined", GREEN, guild_items(self.bot, guild))

    @commands.Cog.listener()
    async def on_guild_remove(self, guild: discord.Guild) -> None:
        await send_log(self.bot, BOT_DATA_LOG_CHANNEL_ID, "Server Left", RED, guild_items(self.bot, guild))

    @commands.Cog.listener()
    async def on_defaults_changed(self, user_id: int, action: str, values: dict) -> None:
        if action == "reset":
            await send_log(self.bot, BOT_DATA_LOG_CHANNEL_ID, "Defaults Reset", GOLD, [("User", f"<@{user_id}>\n`{user_id}`")])
            return
        lines = "\n".join(f"`{k}`: {v}" for k, v in values.items()) or "None"
        await send_log(self.bot, BOT_DATA_LOG_CHANNEL_ID, "Defaults Saved", BLUE,
                       [("User", f"<@{user_id}>\n`{user_id}`"), ("Saved Options", lines)])


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(BotLogCog(bot))
