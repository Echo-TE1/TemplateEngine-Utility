"""Entry point: loads cogs.message and runs the bot.

Env vars:
  DISCORD_TOKEN  required, the bot token
  PORT           optional, keep-alive web server port (default 8080)
"""

from __future__ import annotations

import asyncio
import logging
import os

import discord
from aiohttp import web
from discord.ext import commands

from keep_alive import start_keep_alive

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("bot")


class QuoteBot(commands.Bot):
    def __init__(self) -> None:
        super().__init__(
            command_prefix=commands.when_mentioned,
            intents=discord.Intents.default(),
        )
        self._keep_alive: web.AppRunner | None = None

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


async def main() -> None:
    token = os.environ.get("DISCORD_TOKEN")
    if not token:
        raise SystemExit("DISCORD_TOKEN is not set")
    async with QuoteBot() as bot:
        await bot.start(token)


if __name__ == "__main__":
    asyncio.run(main())
