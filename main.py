import os
import asyncio

import discord
from discord.ext import commands

from keep_alive import keep_alive

TOKEN = os.getenv("DISCORD_TOKEN")
BOT_OWNER_ID = 1219266886143967245

# Add more cogs here if you make them later
COGS = [
    "cogs.utility",
]

intents = discord.Intents.default()


class UtilityBot(commands.Bot):
    def __init__(self):
        super().__init__(
            command_prefix=commands.when_mentioned_or("!"),
            intents=intents,
            owner_id=BOT_OWNER_ID,
        )

    async def setup_hook(self):
        # Load every cog
        for cog in COGS:
            try:
                await self.load_extension(cog)
                print(f"[Cog Loaded] {cog}")
            except Exception as e:
                print(f"[Cog Error] Failed to load {cog}: {e}")

        # Sync slash commands (global)
        try:
            synced = await self.tree.sync()
            print(f"[Sync] Synced {len(synced)} slash commands.")
        except Exception as e:
            print(f"[Sync Error] {e}")

    async def on_ready(self):
        print(f"[Ready] Logged in as {self.user} ({self.user.id})")


bot = UtilityBot()


async def main():
    if not TOKEN:
        raise RuntimeError("DISCORD_TOKEN is missing. Set it in the Render environment variables.")

    # Start the web server so Render and the cron job can ping the bot
    keep_alive()

    async with bot:
        await bot.start(TOKEN)


if __name__ == "__main__":
    asyncio.run(main())
