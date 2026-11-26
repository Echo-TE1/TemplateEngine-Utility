"""MessageCog: /generate discord message

Renders a Discord-style chat card for a selected user and sends it as an image.
Image rendering only (see cardrender.py): no webhooks, no impersonation.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
from datetime import datetime, timezone
from typing import Literal, Optional, Union

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands

from cardrender import RGB, custom_emoji_ids, parse_reactions, render_message_card

log = logging.getLogger(__name__)

MAX_TEXT_LENGTH = 1000
MAX_AVATAR_BYTES = 8 * 1024 * 1024
MAX_IMAGE_BYTES = 8 * 1024 * 1024
MAX_EMOJI_BYTES = 512 * 1024
MAX_CUSTOM_EMOJI = 20

ThemeName = Literal["dark", "ash", "onyx", "light"]

USER_PING_RE = re.compile(r"<@!?(\d+)>")
ROLE_PING_RE = re.compile(r"<@&(\d+)>")
CHANNEL_PING_RE = re.compile(r"<#(\d+)>")
NBSP = " "

CLOCK_12 = {"12", "12h", "12hr", "12-hour", "12 hour", "normal", "regular", "am/pm", "ampm"}
CLOCK_24 = {"24", "24h", "24hr", "24-hour", "24 hour", "military", "24-hour clock (military)"}


# --------------------------------------------------------------- timestamps


def default_timestamp(military: bool = False) -> str:
    """'Today at 8:44 PM' (or 'Today at 20:44' in 24-hour time).
    The clock follows the CARD_TIMEZONE env var (an IANA name, default UTC)."""
    try:
        from zoneinfo import ZoneInfo

        now = datetime.now(ZoneInfo(os.environ.get("CARD_TIMEZONE", "UTC")))
    except Exception:
        now = datetime.now(timezone.utc)
    if military:
        return f"Today at {now.strftime('%H:%M')}"
    return f"Today at {now.strftime('%I:%M %p').lstrip('0')}"


def resolve_timestamp(value: Optional[str]) -> str:
    """Empty or 12h keyword -> 12-hour clock; 24h/military -> 24-hour clock;
    anything else is shown exactly as typed."""
    text = (value or "").strip()
    key = text.lower()
    if not text or key in CLOCK_12:
        return default_timestamp(False)
    if key in CLOCK_24:
        return default_timestamp(True)
    return text[:60]


# ---------------------------------------------------------------------- cog


class MessageCog(commands.Cog):
    """Slash command group: /generate discord message"""

    # Usable in servers, in the bot's DMs, and (when the user installs the app)
    # in any DM or group DM.
    generate = app_commands.Group(
        name="generate",
        description="Generate images",
        allowed_installs=app_commands.AppInstallationType(guild=True, user=True),
        allowed_contexts=app_commands.AppCommandContext(
            guild=True, dm_channel=True, private_channel=True
        ),
    )
    discord_group = app_commands.Group(
        name="discord", description="Discord-style images", parent=generate
    )

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.session: Optional[aiohttp.ClientSession] = None

    async def cog_load(self) -> None:
        self.session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10))

    async def cog_unload(self) -> None:
        if self.session is not None:
            await self.session.close()
            self.session = None

    # ------------------------------------------------------------ downloads

    async def _fetch_bytes(self, url: str, limit: int) -> bytes:
        """Download a URL fully, enforcing a size cap."""
        if self.session is None:
            raise RuntimeError("HTTP session is not ready")
        async with self.session.get(url) as resp:
            resp.raise_for_status()
            # StreamReader.read(n) may return only the first chunk (or
            # nothing), so read the whole body in a loop with a size cap.
            data = bytearray()
            async for chunk in resp.content.iter_chunked(64 * 1024):
                data.extend(chunk)
                if len(data) > limit:
                    raise ValueError("Download is too large.")
        if not data:
            raise ValueError("Download was empty.")
        return bytes(data)

    async def _fetch_custom_emoji(self, text: str, reactions: str) -> dict[str, bytes]:
        ids = custom_emoji_ids(text, reactions)[:MAX_CUSTOM_EMOJI]

        async def one(emoji_id: str) -> tuple[str, Optional[bytes]]:
            try:
                data = await self._fetch_bytes(
                    f"https://cdn.discordapp.com/emojis/{emoji_id}.png?size=64", MAX_EMOJI_BYTES
                )
                return emoji_id, data
            except (aiohttp.ClientError, asyncio.TimeoutError, ValueError):
                return emoji_id, None

        results = await asyncio.gather(*(one(i) for i in ids))
        return {emoji_id: data for emoji_id, data in results if data}

    async def _clan_info(self, target) -> tuple[Optional[str], Optional[bytes]]:
        """The user's clan (server) tag and its badge image, if they show one."""
        guild_info = getattr(target, "primary_guild", None)
        if getattr(guild_info, "tag", None) is None:
            try:  # interaction data can omit it; a full user fetch includes it
                full = await self.bot.fetch_user(target.id)
                guild_info = getattr(full, "primary_guild", None)
            except discord.HTTPException:
                guild_info = None
        tag = getattr(guild_info, "tag", None)
        if not tag or getattr(guild_info, "identity_enabled", True) is False:
            return None, None

        badge_bytes = None
        badge = getattr(guild_info, "badge", None)
        if badge is not None:
            try:
                badge_bytes = await self._fetch_bytes(
                    badge.replace(size=64, format="png").url, MAX_EMOJI_BYTES
                )
            except (aiohttp.ClientError, asyncio.TimeoutError, ValueError):
                badge_bytes = None
        return tag, badge_bytes

    # -------------------------------------------------------------- mentions

    async def _resolve_mentions(
        self, interaction: discord.Interaction, text: str
    ) -> tuple[str, dict[str, RGB]]:
        """Turn <@id>, <@&id> and <#id> into readable @name / #channel text.
        Names use no-break spaces so each mention stays one pill."""
        guild = interaction.guild
        colors: dict[str, RGB] = {}
        names: dict[str, str] = {}

        async def user_name(user_id: int) -> str:
            member = guild.get_member(user_id) if guild else None
            if member is not None:
                return member.display_name
            user = self.bot.get_user(user_id)
            if user is None:
                try:
                    user = await self.bot.fetch_user(user_id)
                except discord.HTTPException:
                    return "unknown-user"
            return user.display_name

        for kind, pattern in (("u", USER_PING_RE), ("r", ROLE_PING_RE), ("c", CHANNEL_PING_RE)):
            for raw_id in list(dict.fromkeys(pattern.findall(text)))[:15]:
                key = f"{kind}{raw_id}"
                ident = int(raw_id)
                if kind == "u":
                    names[key] = "@" + (await user_name(ident)).replace(" ", NBSP)
                elif kind == "r":
                    role = guild.get_role(ident) if guild else None
                    names[key] = "@" + (role.name if role else "deleted-role").replace(" ", NBSP)
                    if role is not None and role.color.value:
                        colors[names[key]] = (role.color.r, role.color.g, role.color.b)
                else:
                    channel = (guild.get_channel(ident) if guild else None) or self.bot.get_channel(ident)
                    names[key] = "#" + (getattr(channel, "name", None) or "deleted-channel").replace(" ", NBSP)

        text = USER_PING_RE.sub(lambda m: names.get(f"u{m.group(1)}", "@unknown-user"), text)
        text = ROLE_PING_RE.sub(lambda m: names.get(f"r{m.group(1)}", "@deleted-role"), text)
        text = CHANNEL_PING_RE.sub(lambda m: names.get(f"c{m.group(1)}", "#deleted-channel"), text)
        return text, colors

    # --------------------------------------------------------------- command

    @discord_group.command(
        name="message",
        description="Generate a Discord-style message image.",
    )
    @app_commands.describe(
        text="The message content (markdown, emoji and pings work)",
        user="Who the message is shown as (defaults to you)",
        theme="Card theme (default: dark)",
        image="An image to show under the message",
        timestamp="Pick 12h or 24h (military) time, or type your own text (default: 12h)",
        mention_highlight="Highlight the row like a mention (amber tint and bar)",
        reactions="Reactions, e.g. '💀 4, 🔥 2' (custom emoji work too)",
        togif="Output a GIF instead of a PNG",
        use_username="Show the @username instead of the display name (default: display name)",
        hide_clantags="Hide the clan tag next to the name",
    )
    async def message(
        self,
        interaction: discord.Interaction,
        text: app_commands.Range[str, 1, MAX_TEXT_LENGTH],
        user: Optional[Union[discord.Member, discord.User]] = None,
        theme: ThemeName = "dark",
        image: Optional[discord.Attachment] = None,
        timestamp: Optional[str] = None,
        mention_highlight: bool = False,
        reactions: Optional[str] = None,
        togif: bool = False,
        use_username: Optional[bool] = None,
        hide_clantags: bool = False,
    ) -> None:
        await interaction.response.defer(thinking=True)
        target = user or interaction.user
        reactions = (reactions or "")[:300]

        if image is not None:
            if not (image.content_type or "").startswith("image/"):
                await interaction.followup.send("The attachment must be an image.", ephemeral=True)
                return
            if image.size > MAX_IMAGE_BYTES:
                await interaction.followup.send("That image is too large (max 8 MB).", ephemeral=True)
                return

        try:
            avatar_url = target.display_avatar.replace(size=128, format="png").url
            avatar_bytes = await self._fetch_bytes(avatar_url, MAX_AVATAR_BYTES)
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError):
            await interaction.followup.send(
                "Couldn't download that user's avatar. Please try again.", ephemeral=True
            )
            return

        attachment_bytes = None
        if image is not None:
            try:
                attachment_bytes = await self._fetch_bytes(image.url, MAX_IMAGE_BYTES)
            except (aiohttp.ClientError, asyncio.TimeoutError, ValueError):
                await interaction.followup.send(
                    "Couldn't download the attached image. Please try again.", ephemeral=True
                )
                return

        colour = getattr(target, "color", None)
        name_color: Optional[RGB] = None
        if colour is not None and getattr(colour, "value", 0) != 0:
            name_color = (colour.r, colour.g, colour.b)

        clan_tag, clan_badge = (None, None)
        if not hide_clantags:
            clan_tag, clan_badge = await self._clan_info(target)

        custom_emoji = await self._fetch_custom_emoji(text, reactions)
        text, mention_colors = await self._resolve_mentions(interaction, text)

        try:
            buffer, ext = await asyncio.to_thread(
                render_message_card,
                avatar_bytes=avatar_bytes,
                name=target.name if use_username is True else target.display_name,
                name_color=name_color,
                clan_tag=clan_tag,
                clan_badge=clan_badge,
                is_app=bool(getattr(target, "bot", False)),
                timestamp=resolve_timestamp(timestamp),
                text=text,
                theme_name=theme,
                mention_colors=mention_colors,
                custom_emoji=custom_emoji,
                attachment_bytes=attachment_bytes,
                reactions=parse_reactions(reactions),
                row_highlight=mention_highlight,
                as_gif=togif,
            )
        except Exception:
            log.exception("Rendering the message card failed")
            await interaction.followup.send(
                "Something went wrong while rendering the image.", ephemeral=True
            )
            return

        await interaction.followup.send(file=discord.File(buffer, filename=f"message.{ext}"))

    @message.autocomplete("timestamp")
    async def timestamp_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        twelve, twenty_four = default_timestamp(False), default_timestamp(True)
        choices = [
            app_commands.Choice(name=f"12-hour clock - {twelve}", value="12h"),
            app_commands.Choice(name=f"24-hour clock (military) - {twenty_four}", value="24h"),
        ]
        typed = current.strip()
        if typed and typed.lower() not in CLOCK_12 | CLOCK_24:
            choices.insert(0, app_commands.Choice(name=f"Custom: {typed[:80]}", value=typed[:100]))
        return choices


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(MessageCog(bot))
