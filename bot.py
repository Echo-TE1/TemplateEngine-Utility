"""Entry point: loads MessageCog and runs the bot.

Env vars:
  DISCORD_TOKEN  required, the bot token
  PORT           optional; if set (e.g. on a Render Web Service), a tiny
                 /health server is started so the service passes port checks
"""

from __future__ import annotations

import asyncio
import logging
import os

import discord
from aiohttp import web
from discord.ext import commands

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("bot")


class QuoteBot(commands.Bot):
    def __init__(self) -> None:
        super().__init__(command_prefix=commands.when_mentioned, intents=discord.Intents.default())
        self._health_runner: web.AppRunner | None = None

    async def setup_hook(self) -> None:
        await self.load_extension("message")
        synced = await self.tree.sync()
        log.info("Synced %d slash command(s)", len(synced))

        port = os.environ.get("PORT")
        if port:
            app = web.Application()
            app.router.add_get("/health", lambda _: web.Response(text="ok"))
            app.router.add_get("/", lambda _: web.Response(text="ok"))
            self._health_runner = web.AppRunner(app)
            await self._health_runner.setup()
            await web.TCPSite(self._health_runner, "0.0.0.0", int(port)).start()
            log.info("Health server listening on port %s", port)

    async def close(self) -> None:
        if self._health_runner is not None:
            await self._health_runner.cleanup()
        await super().close()

    async def on_ready(self) -> None:
        log.info("Logged in as %s (%s)", self.user, self.user.id if self.user else "?")


async def main() -> None:
    token = os.environ.get("DISCORD_TOKEN")
    if not token:
        raise SystemExit("DISCORD_TOKEN is not set")
    async with QuoteBot() as bot:
        await bot.start(token)


if __name__ == "__main__":
    asyncio.run(main())
