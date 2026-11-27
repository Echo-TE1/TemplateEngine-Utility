"""MessageCog: /generate discord message

Renders a Discord-style chat card for a selected user and sends it as an image.
Image rendering only (see cardrender.py): no webhooks, no impersonation.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import logging
import os
import re
from typing import Literal, Optional, Union

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands

from cardrender import (
    RGB, EmbedSpec, MessageSpec, ReplySpec, custom_emoji_ids, parse_reactions, render_card,
)
from error_log import report_error
from timeutil import valid_timezone, CLOCK_12, CLOCK_24, conversation_timestamps, default_timestamp, resolve_timestamp

log = logging.getLogger(__name__)

MAX_TEXT_LENGTH = 1000
MAX_AVATAR_BYTES = 8 * 1024 * 1024
MAX_IMAGE_BYTES = 8 * 1024 * 1024
MAX_EMOJI_BYTES = 512 * 1024
MAX_CUSTOM_EMOJI = 20
MAX_SCRIPT_LINES = 12
MAX_SCRIPT_USERS = 6
LABEL_DEFAULT = os.environ.get("CARD_LABEL_DEFAULT", "").strip().lower() in {"1", "true", "yes", "on"}

ThemeName = Literal["dark", "ash", "onyx", "light"]

USER_PING_RE = re.compile(r"<@!?(\d+)>")
ROLE_PING_RE = re.compile(r"<@&(\d+)>")
CHANNEL_PING_RE = re.compile(r"<#(\d+)>")
NBSP = "\u00a0"
SCRIPT_LINE_RE = re.compile(r"^\s*(<@!?\d+>|[^:\n]{1,32}?)\s*:\s*(.+)$")
HEX_RE = re.compile(r"^#?([0-9a-fA-F]{6})$")


def parse_color(value: Optional[str]) -> Optional[RGB]:
    match = HEX_RE.match((value or "").strip())
    if not match:
        return None
    raw = match.group(1)
    return int(raw[0:2], 16), int(raw[2:4], 16), int(raw[4:6], 16)


def placeholder_avatar(name: str) -> bytes:
    """A flat coloured square for lines of a conversation with no real user."""
    from PIL import Image

    digest = hashlib.md5(name.lower().encode()).digest()
    palette = [(88, 101, 242), (87, 242, 135), (254, 231, 92), (235, 69, 158), (237, 66, 69), (59, 165, 93)]
    out = io.BytesIO()
    Image.new("RGB", (128, 128), palette[digest[0] % len(palette)]).save(out, "PNG")
    return out.getvalue()


def parse_script(script: str) -> list[tuple[str, str]]:
    """'Name: text' / '<@id>: text' lines -> [(who, text)]. A line with no
    'who:' continues the previous message."""
    rows: list[tuple[str, str]] = []
    for line in script.replace("\\n", "\n").splitlines():
        if not line.strip():
            continue
        match = SCRIPT_LINE_RE.match(line)
        if match:
            rows.append((match.group(1).strip(), match.group(2).strip()))
        elif rows:
            rows[-1] = (rows[-1][0], rows[-1][1] + "\n" + line.strip())
    return rows


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

    # -------------------------------------------------------------- helpers

    async def _defaults(self, user_id: int) -> dict:
        store = getattr(self.bot, "settings", None)
        if store is None:
            return {}
        try:
            await store.load()
        except Exception:
            log.warning("Could not load defaults", exc_info=True)
        return store.get(user_id)

    async def _avatar(self, target) -> bytes:
        url = target.display_avatar.replace(size=128, format="png").url
        return await self._fetch_bytes(url, MAX_AVATAR_BYTES)

    @staticmethod
    def _name_color(target) -> Optional[RGB]:
        colour = getattr(target, "color", None)
        if colour is not None and getattr(colour, "value", 0) != 0:
            return colour.r, colour.g, colour.b
        return None

    async def _role_icon(self, target) -> tuple[Optional[bytes], Optional[str]]:
        """The icon of the member's top role (custom image or Unicode emoji)."""
        role = getattr(target, "top_role", None)
        if role is None or getattr(role, "is_default", lambda: False)():
            return None, None
        if role.unicode_emoji:
            return None, role.unicode_emoji
        if role.icon is not None:
            try:
                return await self._fetch_bytes(role.icon.replace(size=64, format="png").url, MAX_EMOJI_BYTES), None
            except (aiohttp.ClientError, asyncio.TimeoutError, ValueError):
                pass
        return None, None

    async def _download_attachment(self, attachment: Optional[discord.Attachment], what: str) -> Optional[bytes]:
        """Validate + fetch an image attachment. Raises UserError on problems."""
        if attachment is None:
            return None
        if not (attachment.content_type or "").startswith("image/"):
            raise UserError(f"The {what} must be an image.")
        if attachment.size > MAX_IMAGE_BYTES:
            raise UserError(f"That {what} is too large (max 8 MB).")
        try:
            return await self._fetch_bytes(attachment.url, MAX_IMAGE_BYTES)
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError):
            raise UserError(f"Couldn't download the {what}. Please try again.")

    async def _limited(self, interaction: discord.Interaction) -> bool:
        """True (and tells the user) when generation is paused to protect the hosting bandwidth limit."""
        usage = self.bot.get_cog("UsageCog")
        if usage is None or await self.bot.is_owner(interaction.user):
            return False
        allowed, text = usage.render_allowed()
        if allowed:
            return False
        await interaction.followup.send(text, ephemeral=True)
        return True

    async def _send_card(self, interaction: discord.Interaction, messages: list[MessageSpec], *,
                         theme: str, custom: dict[str, bytes], as_gif: bool, label: bool, filename: str) -> None:
        try:
            buffer, ext = await asyncio.to_thread(
                render_card, messages, theme_name=theme, custom_emoji=custom, as_gif=as_gif, label=label
            )
        except Exception as exc:
            await report_error(self.bot, f"rendering {filename}", exc, user=interaction.user, guild=interaction.guild)
            await interaction.followup.send("Something went wrong while rendering the image.", ephemeral=True)
            return
        usage = self.bot.get_cog("UsageCog")
        if usage is not None:
            usage.record_render(buffer.getbuffer().nbytes, ext == "gif")
        await interaction.followup.send(file=discord.File(buffer, filename=f"{filename}.{ext}"))

    # --------------------------------------------------------------- command

    @discord_group.command(name="message", description="Generate a Discord-style message image.")
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
        reply_to="Show a 'replying to' line for this user",
        reply_text="Text of the message being replied to",
        edited="Add the (edited) tag",
        embed_title="Add an embed with this title",
        embed_description="Embed description text",
        embed_color="Embed colour as hex, e.g. #5865F2",
        sticker="An image shown as a sticker under the message",
        label="Add a small 'generated image' footer to the card",
        role_icon="Show the user's role icon next to the name (default: on)",
    )
    async def message(
        self,
        interaction: discord.Interaction,
        text: app_commands.Range[str, 1, MAX_TEXT_LENGTH],
        user: Optional[Union[discord.Member, discord.User]] = None,
        theme: Optional[ThemeName] = None,
        image: Optional[discord.Attachment] = None,
        timestamp: Optional[str] = None,
        mention_highlight: bool = False,
        reactions: Optional[str] = None,
        togif: bool = False,
        use_username: Optional[bool] = None,
        hide_clantags: Optional[bool] = None,
        reply_to: Optional[Union[discord.Member, discord.User]] = None,
        reply_text: Optional[app_commands.Range[str, 1, 200]] = None,
        edited: bool = False,
        embed_title: Optional[app_commands.Range[str, 1, 100]] = None,
        embed_description: Optional[app_commands.Range[str, 1, 400]] = None,
        embed_color: Optional[str] = None,
        sticker: Optional[discord.Attachment] = None,
        label: Optional[bool] = None,
        role_icon: bool = True,
    ) -> None:
        await interaction.response.defer(thinking=True)
        if await self._limited(interaction):
            return
        try:
            await self._message(
                interaction, text, user, theme, image, timestamp, mention_highlight, reactions, togif,
                use_username, hide_clantags, reply_to, reply_text, edited, embed_title, embed_description,
                embed_color, sticker, label, role_icon,
            )
        except UserError as exc:
            await interaction.followup.send(str(exc), ephemeral=True)

    async def _message(self, interaction, text, user, theme, image, timestamp, mention_highlight, reactions,
                       togif, use_username, hide_clantags, reply_to, reply_text, edited, embed_title,
                       embed_description, embed_color, sticker, label, role_icon) -> None:
        defaults = await self._defaults(interaction.user.id)
        theme = theme or defaults.get("theme", "dark")
        timestamp = timestamp if timestamp is not None else defaults.get("timestamp")
        use_username = use_username if use_username is not None else defaults.get("use_username", False)
        hide_clantags = hide_clantags if hide_clantags is not None else defaults.get("hide_clantags", False)
        label = label if label is not None else defaults.get("label", LABEL_DEFAULT)

        target = user or interaction.user
        reactions = (reactions or "")[:300]

        try:
            avatar_bytes = await self._avatar(target)
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError):
            raise UserError("Couldn't download that user's avatar. Please try again.")
        attachment_bytes = await self._download_attachment(image, "image")
        sticker_bytes = await self._download_attachment(sticker, "sticker")

        clan_tag, clan_badge = (None, None)
        if not hide_clantags:
            clan_tag, clan_badge = await self._clan_info(target)
        icon_bytes, icon_emoji = await self._role_icon(target) if role_icon else (None, None)

        reply = None
        if reply_to is not None or reply_text:
            who = reply_to or target
            try:
                reply_avatar = await self._avatar(who)
            except (aiohttp.ClientError, asyncio.TimeoutError, ValueError):
                reply_avatar = None
            reply_body, _ = await self._resolve_mentions(interaction, reply_text or "")
            reply = ReplySpec(
                name=who.name if use_username else who.display_name,
                text=reply_body,
                name_color=self._name_color(who),
                avatar_bytes=reply_avatar,
            )

        embed = None
        if embed_title or embed_description:
            embed_body, _ = await self._resolve_mentions(interaction, embed_description or "")
            embed = EmbedSpec(title=embed_title or "", description=embed_body,
                              color=parse_color(embed_color) or (88, 101, 242))

        custom = await self._fetch_custom_emoji(text + " " + (embed_description or ""), reactions)
        body, mention_colors = await self._resolve_mentions(interaction, text)

        spec = MessageSpec(
            avatar_bytes=avatar_bytes,
            name=target.name if use_username else target.display_name,
            text=body,
            name_color=self._name_color(target),
            clan_tag=clan_tag,
            clan_badge=clan_badge,
            role_icon=icon_bytes,
            role_emoji=icon_emoji,
            is_app=bool(getattr(target, "bot", False)),
            timestamp=resolve_timestamp(timestamp, defaults.get("timezone")),
            edited=edited,
            mention_colors=mention_colors,
            attachment_bytes=attachment_bytes,
            sticker_bytes=sticker_bytes,
            embed=embed,
            reactions=parse_reactions(reactions),
            reply=reply,
            row_highlight=mention_highlight,
        )
        await self._send_card(interaction, [spec], theme=theme, custom=custom, as_gif=togif,
                              label=bool(label), filename="message")

    @message.autocomplete("timestamp")
    async def timestamp_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        return timestamp_choices(current)

    # ---------------------------------------------------------- conversation

    @discord_group.command(name="conversation", description="Generate a multi-message conversation image.")
    @app_commands.describe(
        script="One message per line: 'Name: text' or '@mention: text'. Same speaker twice in a row groups them",
        theme="Card theme (default: dark)",
        timestamp="12h, 24h (military) or your own text (default: 12h)",
        use_username="Show @usernames instead of display names",
        hide_clantags="Hide clan tags",
        label="Add a small 'generated image' footer to the card",
    )
    async def conversation(
        self,
        interaction: discord.Interaction,
        script: app_commands.Range[str, 1, 3000],
        theme: Optional[ThemeName] = None,
        timestamp: Optional[str] = None,
        use_username: Optional[bool] = None,
        hide_clantags: Optional[bool] = None,
        label: Optional[bool] = None,
    ) -> None:
        await interaction.response.defer(thinking=True)
        if await self._limited(interaction):
            return
        try:
            await self._conversation(interaction, script, theme, timestamp, use_username, hide_clantags, label)
        except UserError as exc:
            await interaction.followup.send(str(exc), ephemeral=True)

    @conversation.autocomplete("timestamp")
    async def conversation_timestamp_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        return timestamp_choices(current)

    async def _conversation(self, interaction, script, theme, timestamp, use_username, hide_clantags, label) -> None:
        rows = parse_script(script)
        if not rows:
            raise UserError("Write at least one line like `Alex: hello` or `<@123456789>: hello`.")
        if len(rows) > MAX_SCRIPT_LINES:
            raise UserError(f"Too many lines (max {MAX_SCRIPT_LINES}).")
        speakers = list(dict.fromkeys(who.lower() for who, _ in rows))
        if len(speakers) > MAX_SCRIPT_USERS:
            raise UserError(f"Too many different speakers (max {MAX_SCRIPT_USERS}).")

        defaults = await self._defaults(interaction.user.id)
        theme = theme or defaults.get("theme", "dark")
        timestamp = timestamp if timestamp is not None else defaults.get("timestamp")
        use_username = use_username if use_username is not None else defaults.get("use_username", False)
        hide_clantags = hide_clantags if hide_clantags is not None else defaults.get("hide_clantags", False)
        label = label if label is not None else defaults.get("label", LABEL_DEFAULT)

        # One profile per distinct speaker.
        profiles: dict[str, dict] = {}
        for who, _ in rows:
            key = who.lower()
            if key in profiles:
                continue
            ping = re.fullmatch(r"<@!?(\d+)>", who)
            member = None
            if ping:
                uid = int(ping.group(1))
                member = (interaction.guild.get_member(uid) if interaction.guild else None) or self.bot.get_user(uid)
                if member is None:
                    try:
                        member = await self.bot.fetch_user(uid)
                    except discord.HTTPException:
                        member = None
            elif interaction.guild:
                lowered = who.lower()
                member = discord.utils.find(
                    lambda m: lowered in (m.display_name.lower(), m.name.lower()), interaction.guild.members
                )
            if member is not None:
                try:
                    avatar = await self._avatar(member)
                except (aiohttp.ClientError, asyncio.TimeoutError, ValueError):
                    avatar = placeholder_avatar(who)
                tag, badge = (None, None) if hide_clantags else await self._clan_info(member)
                icon_bytes, icon_emoji = await self._role_icon(member)
                profiles[key] = dict(
                    avatar_bytes=avatar,
                    name=member.name if use_username else member.display_name,
                    name_color=self._name_color(member), clan_tag=tag, clan_badge=badge,
                    role_icon=icon_bytes, role_emoji=icon_emoji, is_app=bool(getattr(member, "bot", False)),
                )
            else:
                profiles[key] = dict(avatar_bytes=placeholder_avatar(who), name=who.lstrip("@")[:32])

        groups = 0
        previous = None
        for who, _ in rows:
            if who.lower() != previous:
                groups += 1
            previous = who.lower()
        stamps = conversation_timestamps(groups, timestamp, defaults.get("timezone"))

        all_text = "\n".join(t for _, t in rows)
        custom = await self._fetch_custom_emoji(all_text, "")
        specs: list[MessageSpec] = []
        mention_colors: dict[str, RGB] = {}
        previous, group_index = None, -1
        for who, line in rows:
            body, colors = await self._resolve_mentions(interaction, line)
            mention_colors.update(colors)
            continued = who.lower() == previous
            if not continued:
                group_index += 1
            previous = who.lower()
            specs.append(MessageSpec(
                text=body, timestamp=stamps[group_index], continued=continued,
                mention_colors=dict(colors), **profiles[who.lower()],
            ))
        await self._send_card(interaction, specs, theme=theme, custom=custom, as_gif=False,
                              label=bool(label), filename="conversation")

    # -------------------------------------------------------------- defaults

    defaults_group = app_commands.Group(
        name="defaults",
        description="Your default options for generated images",
        allowed_installs=app_commands.AppInstallationType(guild=True, user=True),
        allowed_contexts=app_commands.AppCommandContext(guild=True, dm_channel=True, private_channel=True),
    )

    @defaults_group.command(name="set", description="Save default options (only the ones you fill in change).")
    @app_commands.describe(
        theme="Default theme",
        timestamp="Default timestamp: 12h, 24h or your own text",
        timezone="Your timezone for 12h/24h times, e.g. Asia/Tokyo or America/New_York",
        hide_clantags="Hide clan tags by default",
        use_username="Show usernames instead of display names by default",
        label="Add the 'generated image' footer by default",
    )
    async def defaults_set(
        self,
        interaction: discord.Interaction,
        theme: Optional[ThemeName] = None,
        timestamp: Optional[str] = None,
        timezone: Optional[str] = None,
        hide_clantags: Optional[bool] = None,
        use_username: Optional[bool] = None,
        label: Optional[bool] = None,
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        if timezone is not None and not valid_timezone(timezone):
            await interaction.followup.send(
                "Unknown timezone. Use a name like `Asia/Tokyo`, `Europe/London` or `America/New_York`.",
                ephemeral=True)
            return
        store = getattr(self.bot, "settings", None)
        if store is None:
            await interaction.followup.send("Saved defaults are not available right now.", ephemeral=True)
            return
        values = {"theme": theme, "timezone": timezone, "timestamp": timestamp, "hide_clantags": hide_clantags,
                  "use_username": use_username, "label": label}
        if all(v is None for v in values.values()):
            await interaction.followup.send("Fill in at least one option to save.", ephemeral=True)
            return
        saved = await store.update(interaction.user.id, values)
        note = "" if store.persistent else "\n-# These will be forgotten when the bot restarts (no storage channel)."
        await interaction.followup.send("Saved.\n" + describe_defaults(saved) + note, ephemeral=True)

    @defaults_set.autocomplete("timestamp")
    async def defaults_timestamp_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        return timestamp_choices(current)

    @defaults_set.autocomplete("timezone")
    async def defaults_timezone_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        from zoneinfo import available_timezones

        typed = current.strip().lower().replace(" ", "_")
        names = sorted(n for n in available_timezones() if typed in n.lower())
        return [app_commands.Choice(name=n, value=n) for n in names[:25]]

    @defaults_group.command(name="show", description="Show your saved defaults.")
    async def defaults_show(self, interaction: discord.Interaction) -> None:
        saved = await self._defaults(interaction.user.id)
        await interaction.response.send_message(describe_defaults(saved), ephemeral=True)

    @defaults_group.command(name="reset", description="Forget your saved defaults.")
    async def defaults_reset(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        store = getattr(self.bot, "settings", None)
        if store is not None:
            await store.reset(interaction.user.id)
        await interaction.followup.send("Your defaults were reset.", ephemeral=True)


class UserError(Exception):
    """A problem to show to the user as-is."""


def timestamp_choices(current: str) -> list[app_commands.Choice[str]]:
    twelve, twenty_four = default_timestamp(False), default_timestamp(True)
    choices = [
        app_commands.Choice(name=f"12-hour clock - {twelve}", value="12h"),
        app_commands.Choice(name=f"24-hour clock (military) - {twenty_four}", value="24h"),
    ]
    typed = current.strip()
    if typed and typed.lower() not in CLOCK_12 | CLOCK_24:
        choices.insert(0, app_commands.Choice(name=f"Custom: {typed[:80]}", value=typed[:100]))
    return choices


def describe_defaults(saved: dict) -> str:
    if not saved:
        return "You have no saved defaults (theme dark, 12h time, display names, clan tags shown, no label)."
    return "\n".join(f"- **{key}**: {value}" for key, value in saved.items())


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(MessageCog(bot))
