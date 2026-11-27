"""One clean look for every log message: a coloured card with a title, a thin
line between items, bold labels and a timestamp (same style as the Template
Engine bot's logs). Falls back to a normal embed on old discord.py."""

from __future__ import annotations

import logging
import time
from typing import Optional, Sequence

import discord

log = logging.getLogger("logcard")

HAS_CARD = all(hasattr(discord.ui, n) for n in ("LayoutView", "Container", "TextDisplay", "Separator"))

PURPLE = 0x8B5CF6
GREEN = 0x2ECC71
RED = 0xF43F5E
GOLD = 0xF1C40F
GRAY = 0x95A5A6
BLUE = 0x3498DB

Item = tuple[str, str]


def log_payload(title: str, colour: int, items: Sequence[Item]) -> dict:
    """Keyword arguments for channel.send() that show the log."""
    items = [(label, str(value)) for label, value in items if value not in (None, "")]
    if not HAS_CARD:
        embed = discord.Embed(title=title, colour=colour)
        for label, value in items:
            embed.add_field(name=label, value=value[:1000], inline=False)
        embed.timestamp = discord.utils.utcnow()
        return {"embed": embed}
    container = discord.ui.Container(accent_colour=discord.Colour(colour))
    container.add_item(discord.ui.TextDisplay(f"### {title}"))
    for label, value in items:
        container.add_item(discord.ui.Separator())
        container.add_item(discord.ui.TextDisplay(f"**{label}**\n{value}"[:1200]))
    container.add_item(discord.ui.TextDisplay(f"-# <t:{int(time.time())}:F>"))
    view = discord.ui.LayoutView(timeout=None)
    view.add_item(container)
    return {"view": view}


async def send_log(bot: discord.Client, channel_id: int, title: str, colour: int,
                   items: Sequence[Item]) -> bool:
    """Post a log card. Never raises; returns whether it was sent."""
    try:
        channel = bot.get_channel(channel_id) or await bot.fetch_channel(channel_id)
        await channel.send(allowed_mentions=discord.AllowedMentions.none(), **log_payload(title, colour, items))
        return True
    except Exception as exc:  # a log must never break the feature that wrote it
        log.warning("Could not post log %r to %s: %s", title, channel_id, exc)
        return False
