import os
import re
import asyncio
import random
from urllib.parse import unquote
from dataclasses import dataclass, replace
from datetime import datetime, time as dt_time, timedelta, timezone
from typing import Optional

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands, tasks
from pymongo import MongoClient

# -------------------------------------------------------------
# CONSTANTS
# -------------------------------------------------------------
BOT_OWNER_ID = 1219266886143967245

# Daily revenue log: every day at 12:00 AM (GMT+9)
DAILY_LOG_CHANNEL_ID = 1567018889961807952
JST = timezone(timedelta(hours=9))
DAILY_LOG_TIME = dt_time(hour=0, minute=0, tzinfo=JST)

COLOR_PURPLE = 0x8B5CF6
COLOR_GREEN = 0x2ECC71
COLOR_RED = 0xF43F5E
COLOR_GREY = 0x6B7280

# Template Engine design channels (same variable names as the main bot). Used for the daily report.
PREMIUM_DESIGN_CHANNEL_ID = int(os.getenv("PREMIUM_DESIGN_CHANNEL_ID", "1563544108696477802"))
NON_PREMIUM_DESIGN_CHANNEL_ID = int(os.getenv("NON_PREMIUM_DESIGN_CHANNEL_ID", "1563524101522464900"))

# Default border color of the advertisement embed (dark grey)
DEFAULT_AD_COLOR = 0x2B2D31

URL_PATTERN = re.compile(r"^https?://[^\s/$.?#][^\s]*$", re.IGNORECASE)


# -------------------------------------------------------------
# HELPERS
# -------------------------------------------------------------
def error_embed(title: str, description: str) -> discord.Embed:
    return discord.Embed(title=title, description=description, color=COLOR_RED)


def code(text: str) -> str:
    """Wraps a value in backticks so it is easy to copy."""
    return "`" + str(text).replace("`", "'")[:900] + "`"


def clean_url(text: Optional[str]) -> Optional[str]:
    """Returns a valid http(s) URL, adds https:// when it is missing, or None if invalid."""
    value = (text or "").strip().strip("<>").strip()
    if not value or any(ch.isspace() for ch in value):
        return None
    if not re.match(r"^https?://", value, re.IGNORECASE):
        value = "https://" + value
    if not URL_PATTERN.match(value):
        return None
    host = value.split("://", 1)[1].split("/", 1)[0]
    if "." not in host or len(value) > 512:
        return None
    return value


def parse_hex_color(text: Optional[str]) -> int:
    """Parses #RRGGBB / RRGGBB / #RGB. Falls back to dark grey if empty or invalid."""
    raw = (text or "").strip().lstrip("#")
    if re.fullmatch(r"[0-9a-fA-F]{6}", raw):
        return int(raw, 16)
    if re.fullmatch(r"[0-9a-fA-F]{3}", raw):
        return int("".join(ch * 2 for ch in raw), 16)
    return DEFAULT_AD_COLOR


# -------------------------------------------------------------
# ADVERTISEMENT DATA, TEXT AND VIEWS (Components V2 layout)
# -------------------------------------------------------------
# Discord allows 4000 characters of text in total inside one Components V2 message.
MAX_TEXT_LENGTH = 4000
MAX_DESCRIPTION_LENGTH = 1900
NO_MENTIONS = discord.AllowedMentions.none()


@dataclass
class AdData:
    ad_id: int
    type_title: str
    embed_title: str
    description: str
    server_link: str
    button_name: str
    button_link: str
    mention: str = ""
    color: int = DEFAULT_AD_COLOR
    image_link: str = ""


def short_text(text: str, limit: int) -> str:
    text = str(text)
    return text if len(text) <= limit else text[: limit - 3] + "..."


def image_file_name(url: str) -> str:
    """The file name shown for the image link, e.g. banner.png."""
    path = unquote(re.split(r"[?#]", url, maxsplit=1)[0])
    path = re.sub(r"^https?://[^/]+", "", path).rstrip("/")
    name = path.rsplit("/", 1)[-1].replace("[", "").replace("]", "").strip()
    return short_text(name, 40) if name else "image"


def ad_header_text(data: AdData) -> str:
    """Big header, then a small line with the mention and the ad ID."""
    info = f"{data.mention} — ID: {data.ad_id}" if data.mention else f"ID: {data.ad_id}"
    return f"# {data.type_title}\n-# {info}"


def ad_body_text(data: AdData) -> str:
    """Headline, description and the call-to-action line with the server link."""
    return f"## {data.embed_title}\n{data.description}\n🚀 Join here: {data.server_link}"


def ad_image_text(data: AdData) -> str:
    """The image link shown as an attachment-style line. Empty if there is no image."""
    if not data.image_link:
        return ""
    link = data.image_link.replace("(", "%28").replace(")", "%29")
    return f"📎 [{image_file_name(data.image_link)}]({link})"


def control_text(data: AdData, mode: str) -> str:
    """Text of the private control card (preview or edit menu)."""
    if mode == "edit":
        return "## Edit Advertisement\nChoose what you want to change. Nothing is posted until you press Publish."

    ping = code(data.mention) if data.mention else "`None`"
    color = code(f"#{data.color:06X}")
    return "\n".join([
        "## Advertisement Preview",
        "Only you can see this. Nothing has been posted and nobody has been pinged yet.",
        "",
        f"**Ping On Publish:** {ping}",
        f"**Embed Color:** {color}",
        f"**Button Name:** {code(data.button_name)}",
        f"**Button Link:** {code(short_text(data.button_link, 80))}",
    ])


def total_text_length(data: AdData) -> int:
    """Text length of the biggest message we send: the preview (ad card + control card)."""
    return (
        len(ad_header_text(data)) + len(ad_body_text(data)) + len(ad_image_text(data))
        + len(control_text(data, "preview"))
    )


def text_too_long_error(data: AdData) -> Optional[discord.Embed]:
    extra = total_text_length(data) - MAX_TEXT_LENGTH
    if extra > 0:
        return error_embed(
            "Text Too Long",
            f"Discord allows {MAX_TEXT_LENGTH} characters of text in one advertisement. Please shorten it by `{extra}` characters."
        )
    return None


def build_ad_container(data: AdData) -> discord.ui.Container:
    """The advertisement card exactly as it will look when published."""
    children = [
        discord.ui.TextDisplay(ad_header_text(data)),
        discord.ui.Separator(),
        discord.ui.TextDisplay(ad_body_text(data)),
    ]
    image_text = ad_image_text(data)
    if image_text:
        children.append(discord.ui.TextDisplay(image_text))
    children.append(discord.ui.Separator())
    children.append(discord.ui.ActionRow(
        discord.ui.Button(label=data.button_name, url=data.button_link, style=discord.ButtonStyle.link)
    ))
    return discord.ui.Container(*children, accent_colour=data.color)


def notice_view(text: str, color: int) -> discord.ui.LayoutView:
    view = discord.ui.LayoutView(timeout=None)
    view.add_item(discord.ui.Container(discord.ui.TextDisplay(text), accent_colour=color))
    return view


class AdLayoutView(discord.ui.LayoutView):
    """The private (ephemeral) message: the ad on top, the control card with buttons below it."""

    def __init__(self, data: AdData, mode: str = "preview"):
        super().__init__(timeout=None)
        self.data = data
        self.add_item(build_ad_container(data))
        actions = AdPreviewActions(data) if mode == "preview" else AdEditActions(data)
        self.add_item(discord.ui.Container(
            discord.ui.TextDisplay(control_text(data, mode)),
            discord.ui.Separator(),
            actions,
            accent_colour=COLOR_PURPLE
        ))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == BOT_OWNER_ID:
            return True
        try:
            await interaction.response.send_message(
                embed=error_embed("Permission Denied", "Only the bot owner can use this."), ephemeral=True
            )
        except Exception:
            pass
        return False


async def publish_ad(interaction: discord.Interaction, data: AdData):
    """Posts the ad publicly (with the ping), then replaces the preview with a 'published' card."""
    await interaction.response.defer()

    public_view = discord.ui.LayoutView(timeout=None)
    public_view.add_item(build_ad_container(data))
    mentions = discord.AllowedMentions(everyone=True, roles=True, users=True)

    sent = None
    channel = interaction.channel
    if channel is not None:
        try:
            sent = await channel.send(view=public_view, allowed_mentions=mentions)
        except discord.HTTPException:
            sent = None

    if sent is None:
        # The bot cannot post in this channel (for example a user-install). Post as a public reply instead.
        try:
            sent = await interaction.followup.send(
                view=public_view, allowed_mentions=mentions, ephemeral=False, wait=True
            )
        except discord.HTTPException as e:
            await interaction.followup.send(
                embed=error_embed("Could Not Publish", f"Discord returned: `{str(e)[:500]}`\nThe preview is still active."),
                ephemeral=True
            )
            return

    where = f"<#{interaction.channel_id}>" if interaction.guild_id else "this chat"
    done = discord.ui.LayoutView(timeout=None)
    done.add_item(discord.ui.Container(
        discord.ui.TextDisplay(f"## Advertisement Published\nPosted in {where}.\n[Jump to message]({sent.jump_url})"),
        accent_colour=COLOR_GREEN
    ))
    done.add_item(build_ad_container(data))
    await interaction.edit_original_response(view=done, allowed_mentions=NO_MENTIONS)


class AdPreviewActions(discord.ui.ActionRow):
    """Publish / Edit / Cancel."""

    def __init__(self, data: AdData):
        super().__init__()
        self.data = data

    @discord.ui.button(label="Publish", style=discord.ButtonStyle.success)
    async def publish_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await publish_ad(interaction, self.data)

    @discord.ui.button(label="Edit", style=discord.ButtonStyle.primary)
    async def edit_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(view=AdLayoutView(self.data, "edit"), allowed_mentions=NO_MENTIONS)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.danger)
    async def cancel_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(
            view=notice_view("## Advertisement Cancelled\nNothing was posted and nobody was pinged.", COLOR_GREY),
            allowed_mentions=NO_MENTIONS
        )


class AdEditActions(discord.ui.ActionRow):
    """Discord allows only 5 inputs per window, so editing is split into two windows."""

    def __init__(self, data: AdData):
        super().__init__()
        self.data = data

    @discord.ui.button(label="Edit Text & Server Link", style=discord.ButtonStyle.primary)
    async def text_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(AdTextModal(self.data))

    @discord.ui.button(label="Edit Button, Mention, Color & Image", style=discord.ButtonStyle.primary)
    async def style_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(AdStyleModal(self.data))

    @discord.ui.button(label="Back", style=discord.ButtonStyle.secondary)
    async def back_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(view=AdLayoutView(self.data, "preview"), allowed_mentions=NO_MENTIONS)


class AdModal(discord.ui.Modal):
    """Base class for the edit windows. Shows an error instead of failing silently."""

    async def on_error(self, interaction: discord.Interaction, error: Exception):
        print(f"[Ad Modal Error] {error}")
        try:
            await interaction.response.send_message(
                embed=error_embed("Something Went Wrong", f"`{str(error)[:500]}`"), ephemeral=True
            )
        except Exception:
            pass


class AdTextModal(AdModal, title="Edit Text and Server Link"):
    def __init__(self, data: AdData):
        super().__init__()
        self.data = data
        self.type_title_input = discord.ui.TextInput(
            label="Type title (top header)", default=data.type_title, max_length=100, required=True
        )
        self.embed_title_input = discord.ui.TextInput(
            label="Embed title (big headline)", default=data.embed_title, max_length=150, required=True
        )
        self.description_input = discord.ui.TextInput(
            label="Description", style=discord.TextStyle.paragraph, default=data.description,
            max_length=MAX_DESCRIPTION_LENGTH, required=True
        )
        self.server_link_input = discord.ui.TextInput(
            label="Server link", default=data.server_link, max_length=512, required=True
        )
        self.button_name_input = discord.ui.TextInput(
            label="Button name", default=data.button_name, max_length=80, required=True
        )
        for item in (self.type_title_input, self.embed_title_input, self.description_input,
                     self.server_link_input, self.button_name_input):
            self.add_item(item)

    async def on_submit(self, interaction: discord.Interaction):
        values = {
            "type_title": self.type_title_input.value.strip(),
            "embed_title": self.embed_title_input.value.strip(),
            "description": self.description_input.value.strip(),
            "button_name": self.button_name_input.value.strip(),
        }
        if not all(values.values()):
            await interaction.response.send_message(
                embed=error_embed("Missing Text", "Type title, embed title, description and button name cannot be empty."),
                ephemeral=True
            )
            return

        server_link = clean_url(self.server_link_input.value)
        if server_link is None:
            await interaction.response.send_message(
                embed=error_embed("Invalid Server Link", "Please enter a valid link, for example `https://discord.gg/example`."),
                ephemeral=True
            )
            return

        new_data = replace(self.data, server_link=server_link, **values)
        too_long = text_too_long_error(new_data)
        if too_long:
            await interaction.response.send_message(embed=too_long, ephemeral=True)
            return

        await interaction.response.edit_message(view=AdLayoutView(new_data), allowed_mentions=NO_MENTIONS)


class AdStyleModal(AdModal, title="Edit Button, Mention, Color, Image"):
    def __init__(self, data: AdData):
        super().__init__()
        self.data = data
        self.button_link_input = discord.ui.TextInput(
            label="Button redirect link", default=data.button_link, max_length=512, required=True
        )
        self.mention_input = discord.ui.TextInput(
            label="Mention (optional, e.g. @everyone)", default=data.mention, max_length=100, required=False
        )
        self.hex_color_input = discord.ui.TextInput(
            label="Hex color (optional, e.g. #FF0000)",
            default=f"#{data.color:06X}" if data.color != DEFAULT_AD_COLOR else "",
            max_length=7, required=False
        )
        self.image_link_input = discord.ui.TextInput(
            label="Image link (optional)", default=data.image_link, max_length=512, required=False
        )
        for item in (self.button_link_input, self.mention_input, self.hex_color_input, self.image_link_input):
            self.add_item(item)

    async def on_submit(self, interaction: discord.Interaction):
        button_link = clean_url(self.button_link_input.value)
        if button_link is None:
            await interaction.response.send_message(
                embed=error_embed("Invalid Button Link", "Please enter a valid link for the button, for example `https://example.com`."),
                ephemeral=True
            )
            return

        image_link = ""
        if self.image_link_input.value.strip():
            image_link = clean_url(self.image_link_input.value)
            if image_link is None:
                await interaction.response.send_message(
                    embed=error_embed("Invalid Image Link", "Please enter a valid image link or leave it empty."),
                    ephemeral=True
                )
                return

        new_data = replace(
            self.data,
            button_link=button_link,
            mention=self.mention_input.value.strip(),
            color=parse_hex_color(self.hex_color_input.value),
            image_link=image_link
        )
        too_long = text_too_long_error(new_data)
        if too_long:
            await interaction.response.send_message(embed=too_long, ephemeral=True)
            return

        await interaction.response.edit_message(view=AdLayoutView(new_data), allowed_mentions=NO_MENTIONS)


# -------------------------------------------------------------
# ROBLOX REVENUE HELPERS (used by the daily revenue log)
# -------------------------------------------------------------
def get_roblox_cookie_header() -> dict:
    """Builds the .ROBLOSECURITY cookie header from the ROBLOX_COOKIE environment variable."""
    raw_cookie = (os.getenv("ROBLOX_COOKIE") or "").strip().strip('"\'')
    if not raw_cookie:
        return {}
    if not raw_cookie.startswith(".ROBLOSECURITY="):
        raw_cookie = f".ROBLOSECURITY={raw_cookie}"
    return {"Cookie": raw_cookie}


class RevenueError(Exception):
    def __init__(self, title: str, message: str):
        super().__init__(message)
        self.title = title
        self.message = message


async def resolve_revenue_source(session: aiohttp.ClientSession) -> dict:
    """Uses ROBLOX_GROUP_ID if set, otherwise the account that owns ROBLOX_COOKIE."""
    group_id = os.getenv("ROBLOX_GROUP_ID", "").strip()
    if group_id:
        return {"kind": "group", "id": group_id, "label": f"Group `{group_id}`"}

    async with session.get("https://users.roblox.com/v1/users/authenticated") as resp:
        if resp.status != 200:
            raise RevenueError(
                "Cookie Invalid",
                "Roblox did not accept `ROBLOX_COOKIE`. The cookie may be expired. Please log in again and update it."
            )
        me = await resp.json()

    return {"kind": "user", "id": me.get("id"), "label": f"{me.get('name', 'Unknown')} (`{me.get('id')}`)"}


async def fetch_revenue_totals(session: aiohttp.ClientSession, source: dict, time_frame: str) -> dict:
    """Returns sales / extra / pending Robux for one time frame (Day, Week, Month, Year)."""
    if source["kind"] == "group":
        url = f"https://economy.roblox.com/v1/groups/{source['id']}/revenue/summary/{time_frame}"
    else:
        url = (
            f"https://economy.roblox.com/v2/users/{source['id']}/transaction-totals"
            f"?timeFrame={time_frame}&transactionType=summary"
        )

    async with session.get(url) as resp:
        if resp.status in (401, 403):
            raise RevenueError(
                "Access Denied",
                "Roblox rejected the cookie. Make sure `ROBLOX_COOKIE` is valid and has permission to view this revenue."
            )
        if resp.status != 200:
            raise RevenueError("Roblox API Error", f"Roblox returned status `{resp.status}`.")
        data = await resp.json()

    if source["kind"] == "group":
        return {
            "sales": int(data.get("itemSaleRobux", 0)),
            "extra_name": "Group Payouts",
            "extra": int(data.get("groupPayoutRobux", 0)),
            "pending": int(data.get("pendingRobux", 0)),
        }
    return {
        "sales": int(data.get("salesTotal", 0)),
        "extra_name": "Affiliate Sales",
        "extra": int(data.get("affiliateSalesTotal", 0)),
        "pending": int(data.get("pendingRobuxTotal", 0)),
    }


# -------------------------------------------------------------
# DAILY REPORT AND /channel HELPERS
# -------------------------------------------------------------
def format_member_change(current: Optional[int], previous: Optional[int]) -> str:
    """Text for the 'New Group Members' field."""
    if current is None:
        return "`N/A`"
    if previous is None:
        return f"`N/A` (first report)\nTotal: `{current:,}`"
    return f"`{current - previous:+,}`\nTotal: `{current:,}`"


def parse_channel_duration(text: Optional[str]) -> Optional[timedelta]:
    """Parses 1m, 1h, 1d, 1w, 1mo, 1year (also longer words). Returns None if invalid."""
    match = re.fullmatch(
        r"(\d+)\s*(years?|yrs?|y|months?|mos?|mo|weeks?|w|days?|d|hours?|hrs?|h|minutes?|mins?|m)",
        (text or "").strip().lower()
    )
    if not match:
        return None
    amount, unit = int(match.group(1)), match.group(2)
    if amount < 1:
        return None
    if unit.startswith("y"):
        delta = timedelta(days=365 * amount)
    elif unit.startswith("mo"):
        delta = timedelta(days=30 * amount)
    elif unit.startswith("w"):
        delta = timedelta(weeks=amount)
    elif unit.startswith("d"):
        delta = timedelta(days=amount)
    elif unit.startswith("h"):
        delta = timedelta(hours=amount)
    else:
        delta = timedelta(minutes=amount)
    return delta if delta <= timedelta(days=3650) else None


def parse_role_ids(text: str):
    """Reads role mentions (<@&id>) or raw role IDs. Returns (ids, unreadable_parts)."""
    ids, bad = [], []
    for token in re.split(r"[\s,]+", (text or "").strip()):
        if not token:
            continue
        found = re.fullmatch(r"<@&(\d+)>|(\d{15,25})", token)
        if found:
            ids.append(int(found.group(1) or found.group(2)))
        else:
            bad.append(token)
    return list(dict.fromkeys(ids)), bad


# Discord allows at most 25 options in one slash command. name, action, type, category, allowed_roles and
# duration use 6, so 19 permissions fit. Only basic permissions are offered, nothing admin-like.
# Format: option name -> (label shown in replies, PermissionOverwrite attribute).
CHANNEL_PERMISSION_OPTIONS = {
    "view_channel": ("View Channel", "view_channel"),
    "send_messages": ("Send Messages", "send_messages"),
    "read_message_history": ("Read Message History", "read_message_history"),
    "embed_links": ("Embed Links", "embed_links"),
    "attach_files": ("Attach Files", "attach_files"),
    "add_reactions": ("Add Reactions", "add_reactions"),
    "use_external_emojis": ("Use External Emojis", "use_external_emojis"),
    "use_external_stickers": ("Use External Stickers", "use_external_stickers"),
    "create_public_threads": ("Create Public Threads", "create_public_threads"),
    "send_messages_in_threads": ("Send Messages In Threads", "send_messages_in_threads"),
    "use_application_commands": ("Use Application Commands", "use_application_commands"),
    "send_polls": ("Send Polls", "send_polls"),
    "send_voice_messages": ("Send Voice Messages", "send_voice_messages"),
    "connect": ("Connect", "connect"),
    "speak": ("Speak", "speak"),
    "stream": ("Stream", "stream"),
    "use_voice_activation": ("Use Voice Activity", "use_voice_activation"),
    "use_soundboard": ("Use Soundboard", "use_soundboard"),
    "request_to_speak": ("Request To Speak", "request_to_speak"),
}

# What every allowed role gets by default: basic permissions only (see, read, write, talk).
# Permissions you set in the command win over these.
DEFAULT_ROLE_PERMISSIONS = {
    "view_channel": True,
    "send_messages": True,
    "read_message_history": True,
    "add_reactions": True,
    "embed_links": True,
    "attach_files": True,
    "connect": True,
    "speak": True,
    "use_voice_activation": True,
}


class TempChannelStore:
    """Remembers when temporary channels must be deleted. Uses MongoDB when MONGO_URI is set,
    so the timers survive restarts and redeploys. Without MONGO_URI it only lives in memory."""

    def __init__(self):
        self.mem = {}
        self.col = None
        uri = os.getenv("MONGO_URI")
        if uri:
            try:
                client = MongoClient(uri, serverSelectionTimeoutMS=8000)
                try:
                    db = client.get_default_database()
                except Exception:
                    db = client.get_database("utility_bot")
                self.col = db["temporary_channels"]
                print("[Temp Channels] Using MongoDB storage.")
            except Exception as e:
                print(f"[Temp Channels] MongoDB error, using memory storage: {e}")
        else:
            print("[Temp Channels] MONGO_URI not set. Timers are lost when the bot restarts.")

    @property
    def persistent(self) -> bool:
        return self.col is not None

    def _add(self, doc):
        if self.col is not None:
            self.col.replace_one({"_id": doc["_id"]}, doc, upsert=True)
        else:
            self.mem[doc["_id"]] = doc

    def _remove(self, _id):
        if self.col is not None:
            self.col.delete_one({"_id": _id})
        else:
            self.mem.pop(_id, None)

    def _due(self, now):
        if self.col is not None:
            return list(self.col.find({"delete_at": {"$lte": now}}))
        return [d for d in self.mem.values() if d["delete_at"] <= now]

    async def add(self, doc):
        await asyncio.to_thread(self._add, doc)

    async def remove(self, _id):
        await asyncio.to_thread(self._remove, _id)

    async def due(self, now):
        return await asyncio.to_thread(self._due, now)


# -------------------------------------------------------------
# COG
# -------------------------------------------------------------
class UtilityCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.temp_store = TempChannelStore()

    async def cog_load(self):
        self.daily_revenue_log.start()
        self.temp_channel_checker.start()

    async def cog_unload(self):
        self.daily_revenue_log.cancel()
        self.temp_channel_checker.cancel()

    # OWNER LOCK: every slash command in this cog is hard-coded to BOT_OWNER_ID.
    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == BOT_OWNER_ID:
            return True
        try:
            await interaction.response.send_message(
                embed=error_embed("Permission Denied", f"This command can only be used by the bot owner (`{BOT_OWNER_ID}`)."),
                ephemeral=True
            )
        except Exception as e:
            print(f"[Owner Check Error] {e}")
        return False

    async def cog_app_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        # The owner check already replied, so ignore that failure quietly.
        if isinstance(error, app_commands.CheckFailure):
            return
        print(f"[App Command Error] {error}")

    # =============================================================
    # /ad  (private preview -> Publish / Edit / Cancel)
    # =============================================================
    @app_commands.command(name="ad", description="Create an advertisement embed with a private preview.")
    @app_commands.describe(
        type_title="Header title at the top of the embed body.",
        embed_title="Large bold headline at the top of the description.",
        description="Main promotional text (max 1900 characters). Type \\n for a new line.",
        server_link="Invite link shown as a call-to-action line.",
        button_name="Text on the button under the embed.",
        button_redirect_link="Link opened when the button is clicked.",
        mention="Ping text shown under the header, e.g. @everyone or @here.",
        hex_color="Embed border color, e.g. #FF0000. Default is dark grey.",
        image_link="Link of a banner or screenshot image."
    )
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    async def ad_cmd(
        self,
        interaction: discord.Interaction,
        type_title: app_commands.Range[str, 1, 100],
        embed_title: app_commands.Range[str, 1, 150],
        description: app_commands.Range[str, 1, MAX_DESCRIPTION_LENGTH],
        server_link: app_commands.Range[str, 1, 300],
        button_name: app_commands.Range[str, 1, 80],
        button_redirect_link: app_commands.Range[str, 1, 512],
        mention: Optional[app_commands.Range[str, 1, 100]] = None,
        hex_color: Optional[str] = None,
        image_link: Optional[app_commands.Range[str, 1, 500]] = None
    ):
        clean_server = clean_url(server_link)
        if clean_server is None:
            await interaction.response.send_message(
                embed=error_embed("Invalid Server Link", "Please enter a valid link, for example `https://discord.gg/example`."),
                ephemeral=True
            )
            return

        clean_button = clean_url(button_redirect_link)
        if clean_button is None:
            await interaction.response.send_message(
                embed=error_embed("Invalid Button Link", "Please enter a valid link for the button, for example `https://example.com`."),
                ephemeral=True
            )
            return

        clean_image = ""
        if image_link:
            clean_image = clean_url(image_link)
            if clean_image is None:
                await interaction.response.send_message(
                    embed=error_embed("Invalid Image Link", "Please enter a valid image link or leave it empty."),
                    ephemeral=True
                )
                return

        data = AdData(
            ad_id=random.randint(10_000_000, 99_999_999),
            type_title=str(type_title).strip(),
            embed_title=str(embed_title).strip(),
            description=str(description).replace("\\n", "\n").strip(),
            server_link=clean_server,
            button_name=str(button_name).strip(),
            button_link=clean_button,
            mention=(mention or "").strip(),
            color=parse_hex_color(hex_color),
            image_link=clean_image
        )
        if not (data.type_title and data.embed_title and data.description and data.button_name):
            await interaction.response.send_message(
                embed=error_embed("Missing Text", "Type title, embed title, description and button name cannot be empty."),
                ephemeral=True
            )
            return

        too_long = text_too_long_error(data)
        if too_long:
            await interaction.response.send_message(embed=too_long, ephemeral=True)
            return

        # Ephemeral preview: only the person who ran the command can see it, and nobody is pinged.
        await interaction.response.send_message(
            view=AdLayoutView(data),
            ephemeral=True,
            allowed_mentions=NO_MENTIONS
        )

    # =============================================================
    # /channel create  (normal or temporary channels)
    # =============================================================
    channel_group = app_commands.Group(name="channel", description="Create channels.", guild_only=True)

    @channel_group.command(name="create", description="Create a channel. Permission options: true = allow, false = deny.")
    @app_commands.describe(
        name="Channel name.",
        action="What kind of channel to create.",
        channel_type="Normal stays forever. Temporary is deleted automatically.",
        category="Category to put the channel in (optional).",
        allowed_roles="Roles that get access (mention them or paste IDs). Empty = permissions apply to @everyone.",
        duration="Temporary only: 1m, 1h, 1d, 1w, 1mo or 1year.",
        **{key: f"{label}: true = allow, false = deny, empty = default." for key, (label, _) in CHANNEL_PERMISSION_OPTIONS.items()}
    )
    @app_commands.rename(channel_type="type")
    @app_commands.choices(
        action=[
            app_commands.Choice(name="Chat", value="chat"),
            app_commands.Choice(name="Voice", value="voice"),
            app_commands.Choice(name="Announcement", value="announcement"),
            app_commands.Choice(name="Stage", value="stage"),
            app_commands.Choice(name="Forum", value="forum"),
        ],
        channel_type=[
            app_commands.Choice(name="Normal", value="normal"),
            app_commands.Choice(name="Temporary", value="temporary"),
        ]
    )
    async def channel_create_cmd(
        self,
        interaction: discord.Interaction,
        name: app_commands.Range[str, 1, 100],
        action: app_commands.Choice[str],
        channel_type: app_commands.Choice[str],
        category: Optional[discord.CategoryChannel] = None,
        allowed_roles: Optional[str] = None,
        duration: Optional[str] = None,
        view_channel: Optional[bool] = None,
        send_messages: Optional[bool] = None,
        read_message_history: Optional[bool] = None,
        embed_links: Optional[bool] = None,
        attach_files: Optional[bool] = None,
        add_reactions: Optional[bool] = None,
        use_external_emojis: Optional[bool] = None,
        use_external_stickers: Optional[bool] = None,
        create_public_threads: Optional[bool] = None,
        send_messages_in_threads: Optional[bool] = None,
        use_application_commands: Optional[bool] = None,
        send_polls: Optional[bool] = None,
        send_voice_messages: Optional[bool] = None,
        connect: Optional[bool] = None,
        speak: Optional[bool] = None,
        stream: Optional[bool] = None,
        use_voice_activation: Optional[bool] = None,
        use_soundboard: Optional[bool] = None,
        request_to_speak: Optional[bool] = None
    ):
        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild

        async def fail(title: str, text: str):
            await interaction.followup.send(embed=error_embed(title, text), ephemeral=True)

        if not guild.me.guild_permissions.manage_channels:
            await fail("Missing Permissions", "I need the **Manage Channels** permission in this server.")
            return

        clean_name = str(name).strip()
        if not clean_name:
            await fail("Invalid Name", "The channel name cannot be empty.")
            return

        # Temporary channels need a duration
        temporary = channel_type.value == "temporary"
        delete_after = None
        if temporary:
            if not duration:
                await fail("Duration Missing", "Temporary channels need a `duration`, for example `1h`, `1d`, `1w`, `1mo` or `1year`.")
                return
            delete_after = parse_channel_duration(duration)
            if delete_after is None:
                await fail("Invalid Duration", "Use a number with one unit: `1m`, `1h`, `1d`, `1w`, `1mo` or `1year`. The maximum is 10 years.")
                return

        # Allowed roles (mentions or IDs)
        roles = []
        if allowed_roles and allowed_roles.strip():
            role_ids, unreadable = parse_role_ids(allowed_roles)
            if unreadable or not role_ids:
                await fail(
                    "Invalid Roles",
                    "I could not read: " + (", ".join(code(x) for x in unreadable) or "`nothing`")
                    + "\nMention the roles (for example `@Staff @Members`) or paste role IDs."
                )
                return
            if len(role_ids) > 20:
                await fail("Too Many Roles", "Please use 20 roles or fewer.")
                return
            missing = []
            for role_id in role_ids:
                role = guild.get_role(role_id)
                if role is None:
                    missing.append(code(role_id))
                elif role.is_default():
                    await fail("Invalid Role", "`@everyone` cannot be an allowed role. Leave `allowed_roles` empty to set permissions for everyone.")
                    return
                else:
                    roles.append(role)
            if missing:
                await fail("Roles Not Found", "These roles are not in this server: " + ", ".join(missing))
                return

        # The permissions the user chose: true = allow, false = deny, empty = leave as default
        chosen = {
            key: value for key, value in {
                "view_channel": view_channel,
                "send_messages": send_messages,
                "read_message_history": read_message_history,
                "embed_links": embed_links,
                "attach_files": attach_files,
                "add_reactions": add_reactions,
                "use_external_emojis": use_external_emojis,
                "use_external_stickers": use_external_stickers,
                "create_public_threads": create_public_threads,
                "send_messages_in_threads": send_messages_in_threads,
                "use_application_commands": use_application_commands,
                "send_polls": send_polls,
                "send_voice_messages": send_voice_messages,
                "connect": connect,
                "speak": speak,
                "stream": stream,
                "use_voice_activation": use_voice_activation,
                "use_soundboard": use_soundboard,
                "request_to_speak": request_to_speak,
            }.items() if value is not None
        }
        permission_kwargs = {CHANNEL_PERMISSION_OPTIONS[key][1]: value for key, value in chosen.items()}

        # With allowed roles the channel is private: hidden from everyone, and the chosen permissions go to those roles.
        # Without allowed roles the chosen permissions apply to @everyone.
        overwrites = {}
        if roles:
            overwrites[guild.default_role] = discord.PermissionOverwrite(view_channel=False)
            role_kwargs = {**DEFAULT_ROLE_PERMISSIONS, **permission_kwargs}  # your choices win over the basic defaults
            for role in roles:
                overwrites[role] = discord.PermissionOverwrite(**role_kwargs)
        elif permission_kwargs:
            overwrites[guild.default_role] = discord.PermissionOverwrite(**permission_kwargs)
        if overwrites:
            # Keep the bot able to see and delete the channel
            overwrites[guild.me] = discord.PermissionOverwrite(view_channel=True, manage_channels=True, send_messages=True, connect=True)

        reason = f"Created by {interaction.user} ({interaction.user.id})"
        try:
            if action.value == "chat":
                channel = await guild.create_text_channel(clean_name, category=category, overwrites=overwrites, reason=reason)
            elif action.value == "announcement":
                channel = await guild.create_text_channel(clean_name, news=True, category=category, overwrites=overwrites, reason=reason)
            elif action.value == "voice":
                channel = await guild.create_voice_channel(clean_name, category=category, overwrites=overwrites, reason=reason)
            elif action.value == "stage":
                channel = await guild.create_stage_channel(clean_name, category=category, overwrites=overwrites, reason=reason)
            else:
                channel = await guild.create_forum(clean_name, category=category, overwrites=overwrites, reason=reason)
        except discord.Forbidden:
            await fail(
                "Missing Permissions",
                "I could not create the channel. I need **Manage Channels**, and I can only give permissions that I have myself."
            )
            return
        except discord.HTTPException as e:
            hint = ""
            if action.value in ("announcement", "stage", "forum"):
                hint = "\nThis channel type needs **Community** to be enabled in the server settings."
            await fail("Could Not Create Channel", f"Discord returned: `{str(e)[:400]}`{hint}")
            return

        embed = discord.Embed(title="Channel Created", description=f"{channel.mention} was created.", color=COLOR_GREEN)
        embed.add_field(name="Name", value=code(channel.name), inline=True)
        embed.add_field(name="Kind", value=code(action.name), inline=True)
        embed.add_field(name="Type", value=code(channel_type.name), inline=True)
        embed.add_field(name="Channel ID", value=code(channel.id), inline=True)
        embed.add_field(name="Category", value=code(category.name) if category else "`None`", inline=True)
        embed.add_field(
            name="Allowed Roles",
            value=" ".join(role.mention for role in roles) if roles else "`Everyone`",
            inline=True
        )
        if chosen:
            allowed_names = [CHANNEL_PERMISSION_OPTIONS[key][0] for key, value in chosen.items() if value]
            denied_names = [CHANNEL_PERMISSION_OPTIONS[key][0] for key, value in chosen.items() if not value]
            target = " ".join(role.mention for role in roles) if roles else "`@everyone`"
            embed.add_field(name="Permissions Apply To", value=target, inline=False)
            embed.add_field(name="Allowed", value=" ".join(code(n) for n in allowed_names) or "`None`", inline=False)
            embed.add_field(name="Denied", value=" ".join(code(n) for n in denied_names) or "`None`", inline=False)

        if temporary:
            delete_ts = datetime.now(timezone.utc).timestamp() + delete_after.total_seconds()
            try:
                await self.temp_store.add({
                    "_id": channel.id, "guild_id": guild.id, "name": channel.name, "delete_at": delete_ts
                })
                embed.add_field(name="Deletes", value=f"<t:{int(delete_ts)}:R> (<t:{int(delete_ts)}:F>)", inline=False)
                if not self.temp_store.persistent:
                    embed.add_field(
                        name="Warning",
                        value="`MONGO_URI` is not set, so this timer is lost if the bot restarts. Add `MONGO_URI` on Render.",
                        inline=False
                    )
            except Exception as e:
                print(f"[Temp Channel] Could not save timer for {channel.id}: {e}")
                embed.add_field(
                    name="Warning",
                    value="The channel was created, but I could not save its timer. It will not be deleted automatically.",
                    inline=False
                )
        elif duration:
            embed.add_field(name="Note", value="`duration` is only used for temporary channels, so it was ignored.", inline=False)

        await interaction.followup.send(embed=embed, ephemeral=True)

    @tasks.loop(seconds=30)
    async def temp_channel_checker(self):
        try:
            now = datetime.now(timezone.utc).timestamp()
            for doc in await self.temp_store.due(now):
                channel = self.bot.get_channel(doc["_id"])
                if channel is None:
                    try:
                        channel = await self.bot.fetch_channel(doc["_id"])
                    except discord.NotFound:
                        channel = None  # already deleted by someone
                    except discord.HTTPException as e:
                        print(f"[Temp Channel] Cannot reach channel {doc['_id']}: {e}")
                        channel = None

                if channel is not None:
                    try:
                        await channel.delete(reason="Temporary channel expired")
                        print(f"[Temp Channel] Deleted expired channel {doc['_id']}")
                    except discord.HTTPException as e:
                        print(f"[Temp Channel] Could not delete channel {doc['_id']}: {e}")

                # Remove the record in every case so it never loops forever
                await self.temp_store.remove(doc["_id"])
        except Exception as e:
            print(f"[Temp Channel Checker Error] {e}")

    @temp_channel_checker.before_loop
    async def before_temp_channel_checker(self):
        await self.bot.wait_until_ready()

    # =============================================================
    # DAILY REPORT  (every day at 12:00 AM GMT+9)
    # Earnings, new group members, total designs, total built servers, cookie check
    # =============================================================
    async def get_channel_any(self, channel_id: int):
        channel = self.bot.get_channel(channel_id)
        if channel is None:
            channel = await self.bot.fetch_channel(channel_id)
        return channel

    async def count_json_posts(self, channel_id: int) -> Optional[int]:
        """Counts the design posts (messages with a .json file) in a channel. None if the channel cannot be read."""
        try:
            channel = await self.get_channel_any(channel_id)
            count = 0
            async for message in channel.history(limit=None):
                if any(a.filename.lower().endswith(".json") for a in message.attachments):
                    count += 1
            return count
        except Exception as e:
            print(f"[Daily Report] Could not count designs in {channel_id}: {e}")
            return None

    async def previous_member_count(self, channel) -> Optional[int]:
        """Reads yesterday's group member count from the last daily report in the channel."""
        try:
            async for message in channel.history(limit=100):
                if message.author.id != self.bot.user.id:
                    continue
                for embed in message.embeds:
                    if embed.title == "Daily Report" and embed.footer and embed.footer.text:
                        found = re.search(r"Group members: ([\d,]+)", embed.footer.text)
                        if found:
                            return int(found.group(1).replace(",", ""))
        except Exception as e:
            print(f"[Daily Report] Could not read the previous report: {e}")
        return None

    async def send_daily_report(self, channel):
        headers = get_roblox_cookie_header()
        cookie_ok = False
        cookie_text = "`Missing` - `ROBLOX_COOKIE` is not set"
        earnings_text = "`N/A`"
        current_members = None

        async with aiohttp.ClientSession(headers=headers) as session:
            # 1. Cookie check
            if headers:
                try:
                    async with session.get("https://users.roblox.com/v1/users/authenticated") as resp:
                        if resp.status == 200:
                            me = await resp.json()
                            cookie_ok = True
                            cookie_text = f"`Valid` - `{me.get('name', 'Unknown')}`"
                        else:
                            cookie_text = f"`Invalid or expired` (status `{resp.status}`)"
                except Exception as e:
                    cookie_text = f"`Could not check` - `{str(e)[:100]}`"

            # 2. Earnings of the day
            if cookie_ok:
                try:
                    source = await resolve_revenue_source(session)
                    totals = await fetch_revenue_totals(session, source, "Day")
                    earnings_text = f"`{totals['sales']:,}` R$\nPending: `{totals['pending']:,}` R$"
                except RevenueError as e:
                    earnings_text = f"`N/A` - {e.message[:150]}"
                except Exception as e:
                    print(f"[Daily Report] Earnings error: {e}")
                    earnings_text = f"`N/A` - `{str(e)[:100]}`"
            else:
                earnings_text = "`N/A` - the cookie is not valid"

            # 3. Group members
            group_id = os.getenv("ROBLOX_GROUP_ID", "").strip()
            if group_id:
                try:
                    async with session.get(f"https://groups.roblox.com/v1/groups/{group_id}") as resp:
                        if resp.status == 200:
                            current_members = int((await resp.json()).get("memberCount", 0))
                except Exception as e:
                    print(f"[Daily Report] Group error: {e}")

        previous_members = await self.previous_member_count(channel)
        members_text = format_member_change(current_members, previous_members)

        # 4. Designs and built servers (design posts in the two Template Engine design channels)
        premium_count, normal_count = await asyncio.gather(
            self.count_json_posts(PREMIUM_DESIGN_CHANNEL_ID),
            self.count_json_posts(NON_PREMIUM_DESIGN_CHANNEL_ID)
        )
        if premium_count is None or normal_count is None:
            designs_text = "`N/A` - cannot read the design channels"
        else:
            designs_text = f"`{premium_count + normal_count:,}`"
        built_text = f"`{premium_count:,}`\n*premium auto-builds*" if premium_count is not None else "`N/A`"

        now = datetime.now(timezone.utc)
        embed = discord.Embed(
            title="Daily Report",
            description=f"Report for <t:{int(now.timestamp())}:D>",
            color=COLOR_GREEN if cookie_ok else COLOR_RED,
            timestamp=now
        )
        embed.add_field(name="Earnings Today", value=earnings_text, inline=True)
        embed.add_field(name="New Group Members", value=members_text, inline=True)
        embed.add_field(name="Cookie Check", value=cookie_text, inline=True)
        embed.add_field(name="Total Designs", value=designs_text, inline=True)
        embed.add_field(name="Total Built Servers", value=built_text, inline=True)
        embed.set_footer(text=f"Group members: {current_members:,}" if current_members is not None else "Daily report")

        await channel.send(
            content=None if cookie_ok else f"<@{BOT_OWNER_ID}> Your Roblox cookie needs attention.",
            embed=embed,
            allowed_mentions=discord.AllowedMentions(users=[discord.Object(id=BOT_OWNER_ID)])
        )

    @tasks.loop(time=DAILY_LOG_TIME)
    async def daily_revenue_log(self):
        try:
            channel = await self.get_channel_any(DAILY_LOG_CHANNEL_ID)
        except Exception as e:
            print(f"[Daily Report] Could not find channel {DAILY_LOG_CHANNEL_ID}: {e}")
            return

        try:
            await self.send_daily_report(channel)
        except Exception as e:
            print(f"[Daily Report Error] {e}")
            try:
                await channel.send(embed=error_embed("Daily Report Failed", f"Something went wrong: `{str(e)[:500]}`"))
            except Exception:
                pass

    @daily_revenue_log.before_loop
    async def before_daily_revenue_log(self):
        await self.bot.wait_until_ready()

async def setup(bot: commands.Bot):
    await bot.add_cog(UtilityCog(bot))
