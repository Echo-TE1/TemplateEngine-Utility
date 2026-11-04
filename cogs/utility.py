import os
import re
import random
from urllib.parse import unquote
from dataclasses import dataclass, replace
from datetime import datetime, time as dt_time, timedelta, timezone
from typing import Optional

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands, tasks

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
# COG
# -------------------------------------------------------------
class UtilityCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self):
        self.daily_revenue_log.start()

    async def cog_unload(self):
        self.daily_revenue_log.cancel()

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
    # DAILY REVENUE LOG  (every day at 12:00 AM GMT+9)
    # =============================================================
    @tasks.loop(time=DAILY_LOG_TIME)
    async def daily_revenue_log(self):
        channel = self.bot.get_channel(DAILY_LOG_CHANNEL_ID)
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(DAILY_LOG_CHANNEL_ID)
            except Exception as e:
                print(f"[Daily Revenue Log] Could not find channel {DAILY_LOG_CHANNEL_ID}: {e}")
                return

        headers = get_roblox_cookie_header()
        if not headers:
            await channel.send(embed=error_embed(
                "Daily Revenue Log Failed", "The `ROBLOX_COOKIE` environment variable is not set."
            ))
            return

        try:
            async with aiohttp.ClientSession(headers=headers) as session:
                source = await resolve_revenue_source(session)
                day = await fetch_revenue_totals(session, source, "Day")
                week = await fetch_revenue_totals(session, source, "Week")
                month = await fetch_revenue_totals(session, source, "Month")

            now_ts = int(datetime.now(timezone.utc).timestamp())
            embed = discord.Embed(
                title="Daily Revenue Log",
                description=f"Report for <t:{now_ts}:D>\nSource: {source['label']}",
                color=COLOR_GREEN
            )
            embed.add_field(name="Past Day", value=f"`{day['sales']:,}` R$", inline=True)
            embed.add_field(name="Past 7 Days", value=f"`{week['sales']:,}` R$", inline=True)
            embed.add_field(name="Past 30 Days", value=f"`{month['sales']:,}` R$", inline=True)
            embed.add_field(name="Pending", value=f"`{day['pending']:,}` R$", inline=True)
            embed.set_footer(text="Sales from the Roblox Economy API")
            await channel.send(embed=embed)

        except RevenueError as e:
            await channel.send(embed=error_embed("Daily Revenue Log Failed", e.message))
        except Exception as e:
            print(f"[Daily Revenue Log Error] {e}")
            try:
                await channel.send(embed=error_embed("Daily Revenue Log Failed", f"Something went wrong: `{str(e)}`"))
            except Exception:
                pass

    @daily_revenue_log.before_loop
    async def before_daily_revenue_log(self):
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot):
    await bot.add_cog(UtilityCog(bot))
