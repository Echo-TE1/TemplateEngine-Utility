"""Entry point: loads cogs.message and runs the bot.

Env vars:
  DISCORD_TOKEN  required, the bot token
  BOT_OWNER_ID   optional, the user allowed to run !sync (defaults to the
                 Discord application's owner)
  DEPLOY_LOG_CHANNEL_ID  optional, channel for redeployment logs
  PORT           optional, keep-alive web server port (default 8080)

Note: the "!" prefix needs the Message Content intent enabled in the
Discord Developer Portal (Bot > Privileged Gateway Intents).
"""

from __future__ import annotations

import asyncio
import logging
import os

import discord
from aiohttp import web
from discord.ext import commands

from deploy_log import post_deploy_log
from keep_alive import start_keep_alive

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("bot")


@commands.command(name="sync")
@commands.is_owner()
async def sync(ctx: commands.Context) -> None:
    """!sync: re-sync slash commands with Discord (owner only)."""
    synced = await ctx.bot.tree.sync()
    log.info("!sync by %s synced %d command(s)", ctx.author, len(synced))
    await ctx.send(f"Synced {len(synced)} slash command(s).")


class QuoteBot(commands.Bot):
    def __init__(self) -> None:
        intents = discord.Intents.default()
        intents.message_content = True  # needed for the "!" prefix

        owner_id = os.environ.get("BOT_OWNER_ID", "").strip()
        super().__init__(
            command_prefix=commands.when_mentioned_or("!"),
            intents=intents,
            owner_id=int(owner_id) if owner_id.isdigit() else None,
        )
        self.add_command(sync)
        self._keep_alive: web.AppRunner | None = None
        self._deploy_logged = False

    async def setup_hook(self) -> None:
        await self.load_extension("cogs.message")
        synced = await self.tree.sync()
        log.info("Synced %d slash command(s)", len(synced))
        self._keep_alive = await start_keep_alive()

    async def close(self) -> None:
        if self._keep_alive is not None:
            await self._keep_alive.cleanup()
        await super().close()

    async def on_ready(self) -> None:
        log.info("Logged in as %s", self.user)
        if not self._deploy_logged:  # on_ready can fire again on reconnects
            self._deploy_logged = True
            await post_deploy_log(self)

    async def on_command_error(self, ctx: commands.Context, error: Exception) -> None:
        # Stay silent for non-owners and unknown commands.
        if isinstance(error, (commands.NotOwner, commands.CommandNotFound)):
            return
        log.error("Command error in %s", ctx.command, exc_info=error)


async def main() -> None:
    token = os.environ.get("DISCORD_TOKEN")
    if not token:
        raise SystemExit("DISCORD_TOKEN is not set")
    async with QuoteBot() as bot:
        await bot.start(token)


if __name__ == "__main__":
    asyncio.run(main())
