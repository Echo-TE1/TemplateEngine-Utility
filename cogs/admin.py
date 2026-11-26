"""AdminCog: /help for everyone, !reload for the owner."""

from __future__ import annotations

import logging
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

log = logging.getLogger(__name__)

EXTENSIONS = ("cogs.message", "cogs.stats", "cogs.admin", "cogs.giveaway")


class AdminCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="help", description="How to use this bot.")
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channel=True)
    async def help(self, interaction: discord.Interaction) -> None:
        embed = discord.Embed(
            title="Image generator",
            colour=discord.Colour.blurple(),
            description="Makes Discord-style chat images. Everything is drawn locally as a picture; "
            "nothing is sent as another account.",
        )
        embed.add_field(
            name="/generate discord message",
            value="One message. Options: `user`, `theme`, `image`, `timestamp` (12h / 24h / custom), "
            "`reactions`, `reply_to` + `reply_text`, `edited`, `embed_title` / `embed_description` / "
            "`embed_color`, `sticker`, `mention_highlight`, `use_username`, `hide_clantags`, `togif`, `label`.",
            inline=False,
        )
        embed.add_field(
            name="/generate discord conversation",
            value="Several messages from several people. One line each: `Name: text` or `@mention: text`. "
            "The same speaker twice in a row is grouped.",
            inline=False,
        )
        embed.add_field(
            name="/defaults set | show | reset",
            value="Save your preferred theme, timestamp style, clan tags, username mode and footer label.",
            inline=False,
        )
        embed.add_field(
            name="/giveaway start | edit | delete | list",
            value="Giveaways with a Join button inside the embed (needs Manage Server). `edit` and `delete` "
            "take the giveaway's message ID or link.",
            inline=False,
        )
        embed.add_field(
            name="Text formatting",
            value="`**bold**` `*italic*` `__underline__` `~~strike~~` `` `code` `` `||spoiler||` `> quote`, "
            "pings, emoji and custom emoji all work.",
            inline=False,
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @commands.command(name="reload")
    @commands.is_owner()
    async def reload(self, ctx: commands.Context, extension: Optional[str] = None) -> None:
        """!reload [cog]: reload one cog (message, stats, admin, giveaway) or all of them (owner only)."""
        names = EXTENSIONS
        if extension:
            wanted = extension if extension.startswith("cogs.") else f"cogs.{extension}"
            if wanted not in EXTENSIONS:
                await ctx.send(f"Unknown cog. Choose from: {', '.join(e.split('.')[1] for e in EXTENSIONS)}")
                return
            names = (wanted,)
        done, failed = [], []
        for name in names:
            try:
                await self.bot.reload_extension(name)
                done.append(name)
            except Exception as exc:
                log.exception("Reload of %s failed", name)
                failed.append(f"{name}: {exc}")
        if any(n in done for n in ("cogs.message", "cogs.admin")):
            await self.bot.sync_commands()
        lines = [f"Reloaded: {', '.join(done) or 'nothing'}"]
        if failed:
            lines.append("Failed:\n" + "\n".join(failed))
        await ctx.send("\n".join(lines)[:1900])


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(AdminCog(bot))
