"""Posts command and render errors to a Discord channel."""

from __future__ import annotations

import logging
import time
import traceback
from typing import Optional

import discord

from config import ERROR_LOG_CHANNEL_ID

log = logging.getLogger("error_log")

_recent: dict[str, float] = {}
COOLDOWN_SECONDS = 60  # the same error is posted at most once a minute


async def report_error(
    bot: discord.Client,
    where: str,
    error: BaseException,
    *,
    user: Optional[discord.abc.User] = None,
    guild: Optional[discord.Guild] = None,
) -> None:
    """Log the error and post a short embed to the error channel. Never raises."""
    log.error("Error in %s", where, exc_info=error)
    try:
        signature = f"{where}:{type(error).__name__}:{error}"[:200]
        now = time.monotonic()
        if now - _recent.get(signature, -1e9) < COOLDOWN_SECONDS:
            return
        _recent[signature] = now
        if len(_recent) > 200:
            _recent.clear()

        channel = bot.get_channel(ERROR_LOG_CHANNEL_ID) or await bot.fetch_channel(ERROR_LOG_CHANNEL_ID)
        trace = "".join(traceback.format_exception(type(error), error, error.__traceback__))
        embed = discord.Embed(title=f"Error in {where}", colour=discord.Colour.red(),
                              description=f"```py\n{trace[-1500:]}\n```")
        if user is not None:
            embed.add_field(name="User", value=f"{user} (`{user.id}`)", inline=True)
        if guild is not None:
            embed.add_field(name="Server", value=f"{guild.name} (`{guild.id}`)", inline=True)
        await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
    except Exception:
        log.warning("Could not post the error report", exc_info=True)
