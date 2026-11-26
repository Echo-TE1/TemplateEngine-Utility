"""Entry point: loads cogs.message and runs the bot.

Env vars:
  DISCORD_TOKEN  required, the bot token
  BOT_OWNER_ID   optional, the user allowed to run !sync (defaults to the
                 Discord application's owner)
  CARD_LABEL_DEFAULT  optional, 1 = add a "generated image" footer by default (off by default)
  PORT           optional, keep-alive web server port (default 8080)

Note: the "!" prefix needs the Message Content intent, and the unique user
count needs the Server Members intent, both enabled in the Discord Developer
Portal (Bot > Privileged Gateway Intents). Without Server Members the bot still
starts and falls back to an approximate count.
"""

from __future__ import annotations

import asyncio
import logging
import os

import discord
from aiohttp import web
from discord.ext import commands

from config import GUILD_ID
from deploy_log import post_deploy_log
from error_log import report_error
from keep_alive import start_keep_alive
from settings_store import SettingsStore

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("bot")


@commands.command(name="sync")
@commands.is_owner()
async def sync(ctx: commands.Context) -> None:
    """!sync: re-sync slash commands with Discord (owner only)."""
    count = await ctx.bot.sync_commands()
    log.info("!sync by %s synced %d command(s)", ctx.author, count)
    await ctx.send(f"Synced {count} slash command(s) globally.")


class QuoteBot(commands.Bot):
    def __init__(self, members_intent: bool = True, message_content: bool = True) -> None:
        intents = discord.Intents.default()
        intents.message_content = message_content  # needed for the "!" prefix
        # Needed to count *unique* users across servers (StatsCog).
        intents.members = members_intent

        owner_id = os.environ.get("BOT_OWNER_ID", "").strip()
        super().__init__(
            command_prefix=commands.when_mentioned_or("!"),
            intents=intents,
            owner_id=int(owner_id) if owner_id.isdigit() else None,
        )
        self.add_command(sync)
        self.settings = SettingsStore(self)
        self._keep_alive: web.AppRunner | None = None
        self._deploy_logged = False

    async def setup_hook(self) -> None:
        await self.load_extension("cogs.message")
        await self.load_extension("cogs.stats")
        await self.load_extension("cogs.admin")
        await self.load_extension("cogs.giveaway")
        self.tree.on_error = self.on_app_command_error
        await self.sync_commands()
        self._keep_alive = await start_keep_alive()

    async def sync_commands(self) -> int:
        """Sync slash commands globally. Guild-scoped copies of the same
        commands (from older versions) are removed, otherwise they would show
        up twice in that server."""
        synced = await self.tree.sync()
        guild = discord.Object(id=GUILD_ID)
        try:
            self.tree.clear_commands(guild=guild)
            await self.tree.sync(guild=guild)
        except discord.HTTPException as exc:
            log.warning("Could not clear guild commands for %s: %s", GUILD_ID, exc)
        log.info("Synced %d global slash command(s)", len(synced))
        return len(synced)

    async def close(self) -> None:
        if self._keep_alive is not None:
            await self._keep_alive.cleanup()
        await super().close()

    async def on_ready(self) -> None:
        log.info("Logged in as %s", self.user)
        if not self._deploy_logged:  # on_ready can fire again on reconnects
            self._deploy_logged = True
            await post_deploy_log(self)
            try:
                await self.settings.load()
            except Exception as exc:
                await report_error(self, "loading saved defaults", exc)

    async def on_command_error(self, ctx: commands.Context, error: Exception) -> None:
        # Stay silent for non-owners and unknown commands.
        if isinstance(error, (commands.NotOwner, commands.CommandNotFound)):
            return
        await report_error(self, f"!{ctx.command}", error, user=ctx.author, guild=ctx.guild)

    async def on_app_command_error(
        self, interaction: discord.Interaction, error: discord.app_commands.AppCommandError
    ) -> None:
        name = interaction.command.qualified_name if interaction.command else "unknown"
        original = getattr(error, "original", error)
        await report_error(self, f"/{name}", original, user=interaction.user, guild=interaction.guild)
        message = "Something went wrong. The error was logged."
        try:
            if interaction.response.is_done():
                await interaction.followup.send(message, ephemeral=True)
            else:
                await interaction.response.send_message(message, ephemeral=True)
        except discord.HTTPException:
            pass


async def main() -> None:
    token = os.environ.get("DISCORD_TOKEN")
    if not token:
        raise SystemExit("DISCORD_TOKEN is not set")
    # Privileged intents must be enabled in the Developer Portal. If they are
    # not, fall back instead of crashing: first without Server Members (the
    # user count is then approximate), then without Message Content too (the
    # "!" commands stop working, slash commands are unaffected).
    attempts = (
        {"members_intent": True, "message_content": True},
        {"members_intent": False, "message_content": True},
        {"members_intent": False, "message_content": False},
    )
    for index, options in enumerate(attempts):
        try:
            async with QuoteBot(**options) as bot:
                await bot.start(token)
            return
        except discord.PrivilegedIntentsRequired:
            if index == len(attempts) - 1:
                raise
            log.warning("A privileged intent is not enabled in the Developer Portal; retrying with %s",
                        attempts[index + 1])


if __name__ == "__main__":
    asyncio.run(main())
