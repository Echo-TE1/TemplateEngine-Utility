import os
import re
import json
import math
import time
import uuid
import unicodedata
import random
import asyncio
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

GIVEAWAY_BANNER_URL = "https://res.cloudinary.com/dtz0urit6/image/upload/f_png,q_auto/cloudinary-tools-uploads/doob9hmutuwfzw6aloj5.png"

# Roblox time frame names used by the economy APIs
REVENUE_PERIODS = {
    "day": ("Day", "Past Day"),
    "week": ("Week", "Past 7 Days"),
    "month": ("Month", "Past 30 Days"),
    "year": ("Year", "Past Year"),
}

COLOR_PURPLE = 0x8B5CF6
COLOR_GREEN = 0x2ECC71
COLOR_RED = 0xF43F5E
COLOR_GREY = 0x6B7280
COLOR_GOLD = 0xF1C40F

# Default Roblox place used by /gameinfo and /game (Template Engine). Override with ROBLOX_PLACE_ID.
ROBLOX_DEFAULT_PLACE_ID = "6094950399"

DISCORD_EPOCH = 1420070400000

# Everyone else is owner-only. These commands can also be used by server administrators.
ADMIN_COMMANDS = {
    "slowmode", "nick", "role add", "role remove", "say", "embed", "announce",
    "warn", "warnings", "clear", "timeout", "untimeout", "kick", "ban", "unban",
    "lock", "unlock", "modlog",
}

LANGUAGES = {
    "english": "en", "japanese": "ja", "spanish": "es", "french": "fr", "german": "de",
    "italian": "it", "portuguese": "pt", "russian": "ru", "chinese (simplified)": "zh-CN",
    "chinese (traditional)": "zh-TW", "korean": "ko", "arabic": "ar", "hindi": "hi",
    "turkish": "tr", "dutch": "nl", "polish": "pl", "swedish": "sv", "norwegian": "no",
    "danish": "da", "finnish": "fi", "greek": "el", "hebrew": "iw", "indonesian": "id",
    "thai": "th", "vietnamese": "vi", "ukrainian": "uk", "czech": "cs", "romanian": "ro",
    "hungarian": "hu", "filipino": "tl", "malay": "ms", "bengali": "bn", "persian": "fa",
    "urdu": "ur", "swahili": "sw", "tamil": "ta", "bulgarian": "bg", "croatian": "hr",
    "serbian": "sr", "slovak": "sk", "catalan": "ca", "latin": "la",
}
LANGUAGE_CODES = {code.lower(): code for code in LANGUAGES.values()}
CODE_TO_LANGUAGE = {code.lower(): name.title() for name, code in LANGUAGES.items()}


# -------------------------------------------------------------
# HELPERS
# -------------------------------------------------------------
def get_roblox_cookie_header() -> dict:
    """Builds the .ROBLOSECURITY cookie header from the ROBLOX_COOKIE environment variable."""
    raw_cookie = (os.getenv("ROBLOX_COOKIE") or "").strip().strip('"\'')
    if not raw_cookie:
        return {}
    if not raw_cookie.startswith(".ROBLOSECURITY="):
        raw_cookie = f".ROBLOSECURITY={raw_cookie}"
    return {"Cookie": raw_cookie}


def error_embed(title: str, description: str) -> discord.Embed:
    return discord.Embed(title=title, description=description, color=COLOR_RED)


def parse_duration_seconds(text: str) -> Optional[int]:
    """Parses durations like 30s, 10m, 2h, 1d, 1w. Returns seconds or None if invalid."""
    cleaned = (text or "").lower().replace(" ", "")
    if not re.fullmatch(r"(\d+[smhdw])+", cleaned):
        return None
    multipliers = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}
    return sum(int(n) * multipliers[u] for n, u in re.findall(r"(\d+)([smhdw])", cleaned))


def parse_message_id(text: str) -> Optional[int]:
    """Accepts a raw message ID or a full message link and returns the message ID."""
    found = re.findall(r"\d{15,25}", text or "")
    return int(found[-1]) if found else None


def parse_roblox_date(text: str) -> Optional[datetime]:
    try:
        clean = text.rstrip("Z").split(".")[0]
        return datetime.fromisoformat(clean).replace(tzinfo=timezone.utc)
    except Exception:
        return None


async def owner_only(interaction: discord.Interaction) -> bool:
    """Returns True if the user is the bot owner. Otherwise replies with a denied embed."""
    if interaction.user.id == BOT_OWNER_ID:
        return True
    await interaction.response.send_message(
        embed=error_embed("Permission Denied", "Only the bot owner can use this command."),
        ephemeral=True
    )
    return False


# -------------------------------------------------------------
# ROBLOX REVENUE HELPERS
# -------------------------------------------------------------
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
# GIVEAWAY STORAGE (MongoDB if MONGO_URI is set, otherwise memory)
# -------------------------------------------------------------
class GiveawayStore:
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
                self.col = db["giveaways"]
                print("[Giveaway] Using MongoDB storage.")
            except Exception as e:
                print(f"[Giveaway] MongoDB error, using memory storage: {e}")
        else:
            print("[Giveaway] MONGO_URI not set. Using memory storage (giveaways are lost on restart).")

    # ---- sync helpers (run in a thread) ----
    def _insert(self, doc):
        if self.col is not None:
            self.col.replace_one({"_id": doc["_id"]}, doc, upsert=True)
        else:
            self.mem[doc["_id"]] = doc

    def _get(self, mid):
        if self.col is not None:
            return self.col.find_one({"_id": mid})
        return self.mem.get(mid)

    def _update(self, mid, fields):
        if self.col is not None:
            self.col.update_one({"_id": mid}, {"$set": fields})
        elif mid in self.mem:
            self.mem[mid].update(fields)

    def _toggle(self, mid, uid):
        """Adds the user if missing (returns True), removes if present (returns False)."""
        if self.col is not None:
            doc = self.col.find_one({"_id": mid}, {"entries": 1}) or {}
            if uid in doc.get("entries", []):
                self.col.update_one({"_id": mid}, {"$pull": {"entries": uid}})
                return False
            self.col.update_one({"_id": mid}, {"$addToSet": {"entries": uid}})
            return True
        doc = self.mem.get(mid)
        if doc is None:
            return False
        if uid in doc["entries"]:
            doc["entries"].remove(uid)
            return False
        doc["entries"].append(uid)
        return True

    def _delete(self, mid):
        if self.col is not None:
            self.col.delete_one({"_id": mid})
        else:
            self.mem.pop(mid, None)

    def _active(self):
        if self.col is not None:
            return list(self.col.find({"ended": False}))
        return [d for d in self.mem.values() if not d["ended"]]

    # ---- async wrappers ----
    async def insert(self, doc):
        await asyncio.to_thread(self._insert, doc)

    async def get(self, mid):
        return await asyncio.to_thread(self._get, mid)

    async def update(self, mid, fields):
        await asyncio.to_thread(self._update, mid, fields)

    async def toggle(self, mid, uid):
        return await asyncio.to_thread(self._toggle, mid, uid)

    async def delete(self, mid):
        await asyncio.to_thread(self._delete, mid)

    async def active(self):
        return await asyncio.to_thread(self._active)


class GiveawayView(discord.ui.View):
    """Persistent view with the Enter button. Registered once in cog_load."""

    def __init__(self, cog, ended: bool = False):
        super().__init__(timeout=None)
        self.cog = cog
        if ended:
            # Only the Enter button is disabled. The Entries button keeps working.
            for child in self.children:
                if getattr(child, "custom_id", None) == "utility_giveaway_enter":
                    child.disabled = True

    @discord.ui.button(label="Enter Giveaway", emoji="🎉", style=discord.ButtonStyle.primary, custom_id="utility_giveaway_enter")
    async def enter_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.handle_entry(interaction)

    @discord.ui.button(label="Entries", emoji="👥", style=discord.ButtonStyle.secondary, custom_id="utility_giveaway_entries")
    async def entries_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.show_entries(interaction, interaction.message.id, 0, edit=False)


class EntriesPageButton(
    discord.ui.DynamicItem[discord.ui.Button],
    template=r"utility_gw_entries:(?P<mid>\d+):(?P<page>\d+):(?P<dir>[pn])"
):
    """Previous / Next buttons for the entries list.
    The giveaway ID and target page are stored inside the custom_id, so these buttons
    keep working after the bot restarts or redeploys."""

    def __init__(self, mid: int, page: int, label: str, direction: str, disabled: bool = False):
        super().__init__(
            discord.ui.Button(
                label=label,
                style=discord.ButtonStyle.secondary,
                custom_id=f"utility_gw_entries:{mid}:{page}:{direction}",
                disabled=disabled
            )
        )
        self.mid = mid
        self.page = page
        self.direction = direction

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: discord.ui.Button, match, /):
        return cls(int(match["mid"]), int(match["page"]), item.label or "", match["dir"], item.disabled)

    async def callback(self, interaction: discord.Interaction):
        cog = interaction.client.get_cog("UtilityCog")
        if cog is None:
            await interaction.response.send_message(
                embed=error_embed("Unavailable", "Please try again in a moment."), ephemeral=True
            )
            return
        await cog.show_entries(interaction, self.mid, self.page, edit=True)


# -------------------------------------------------------------
# GENERAL DATABASE, PAGE BUTTONS, POLLS AND MODALS
# -------------------------------------------------------------
class Database:
    """Small storage helper. Uses MongoDB when MONGO_URI is set, otherwise memory."""

    def __init__(self):
        self.mem = {}
        self.db = None
        uri = os.getenv("MONGO_URI")
        if uri:
            try:
                client = MongoClient(uri, serverSelectionTimeoutMS=8000)
                try:
                    self.db = client.get_default_database()
                except Exception:
                    self.db = client.get_database("utility_bot")
                print("[Database] Using MongoDB storage.")
            except Exception as e:
                print(f"[Database] MongoDB error, using memory storage: {e}")
        else:
            print("[Database] MONGO_URI not set. Using memory storage (data is lost on restart).")

    def _coll(self, name):
        return self.db[f"utility_{name}"]

    @staticmethod
    def _match(doc, query):
        for key, cond in query.items():
            value = doc.get(key)
            if isinstance(cond, dict):
                if "$lte" in cond and (value is None or value > cond["$lte"]):
                    return False
            elif value != cond:
                return False
        return True

    def _insert(self, name, doc):
        if self.db is not None:
            self._coll(name).replace_one({"_id": doc["_id"]}, doc, upsert=True)
        else:
            self.mem.setdefault(name, {})[doc["_id"]] = doc

    def _get(self, name, _id):
        if self.db is not None:
            return self._coll(name).find_one({"_id": _id})
        return self.mem.get(name, {}).get(_id)

    def _update(self, name, _id, fields):
        if self.db is not None:
            self._coll(name).update_one({"_id": _id}, {"$set": fields})
        elif _id in self.mem.get(name, {}):
            self.mem[name][_id].update(fields)

    def _delete(self, name, _id):
        if self.db is not None:
            self._coll(name).delete_one({"_id": _id})
        else:
            self.mem.get(name, {}).pop(_id, None)

    def _find(self, name, query, sort_key, descending):
        if self.db is not None:
            cursor = self._coll(name).find(query)
            if sort_key:
                cursor = cursor.sort(sort_key, -1 if descending else 1)
            return list(cursor)
        docs = [d for d in self.mem.get(name, {}).values() if self._match(d, query)]
        if sort_key:
            docs.sort(key=lambda d: d.get(sort_key, 0), reverse=descending)
        return docs

    def _count(self, name, query):
        if self.db is not None:
            return self._coll(name).count_documents(query)
        return len([d for d in self.mem.get(name, {}).values() if self._match(d, query)])

    async def insert(self, name, doc):
        await asyncio.to_thread(self._insert, name, doc)

    async def get(self, name, _id):
        return await asyncio.to_thread(self._get, name, _id)

    async def update(self, name, _id, fields):
        await asyncio.to_thread(self._update, name, _id, fields)

    async def delete(self, name, _id):
        await asyncio.to_thread(self._delete, name, _id)

    async def find(self, name, query=None, sort_key=None, descending=False):
        return await asyncio.to_thread(self._find, name, query or {}, sort_key, descending)

    async def count(self, name, query=None):
        return await asyncio.to_thread(self._count, name, query or {})


class PageButton(
    discord.ui.DynamicItem[discord.ui.Button],
    template=r"utility_pg:(?P<kind>[a-z]+):(?P<a>\d+):(?P<b>\d+):(?P<page>\d+):(?P<dir>[pn])"
):
    """Previous / Next buttons for /rolelist, /modlog and /warnings.
    Everything needed is stored in the custom_id, so these keep working after a redeploy."""

    def __init__(self, kind: str, a: int, b: int, page: int, label: str, direction: str, disabled: bool = False):
        super().__init__(
            discord.ui.Button(
                label=label,
                style=discord.ButtonStyle.secondary,
                custom_id=f"utility_pg:{kind}:{a}:{b}:{page}:{direction}",
                disabled=disabled
            )
        )
        self.kind = kind
        self.a = a
        self.b = b
        self.page = page

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: discord.ui.Button, match, /):
        return cls(match["kind"], int(match["a"]), int(match["b"]), int(match["page"]), item.label or "", match["dir"], item.disabled)

    async def callback(self, interaction: discord.Interaction):
        cog = interaction.client.get_cog("UtilityCog")
        if cog is None:
            await interaction.response.send_message(
                embed=error_embed("Unavailable", "Please try again in a moment."), ephemeral=True
            )
            return
        await cog.show_page(interaction, self.kind, self.a, self.b, self.page, edit=True)


def build_poll_embed(doc: dict) -> discord.Embed:
    votes = doc.get("votes", {})
    counts = [0] * len(doc["options"])
    for idx in votes.values():
        if 0 <= idx < len(counts):
            counts[idx] += 1
    total = sum(counts)

    lines = []
    for i, (option, count) in enumerate(zip(doc["options"], counts)):
        pct = round(count / total * 100) if total else 0
        filled = round(pct / 10)
        bar = "█" * filled + "░" * (10 - filled)
        lines.append(f"**{i + 1}. {option}**\n{bar} `{pct}%` (`{count}`)")

    embed = discord.Embed(title=f"📊 {doc['question']}", description="\n\n".join(lines), color=COLOR_PURPLE)
    embed.set_footer(text=f"{total} vote(s) • Click a button to vote, click again to remove your vote")
    return embed


class PollButton(discord.ui.DynamicItem[discord.ui.Button], template=r"utility_poll:(?P<idx>\d)"):
    """One option button of a poll. Polls keep working after a redeploy."""

    def __init__(self, idx: int, label: str):
        super().__init__(
            discord.ui.Button(
                label=label[:80] or str(idx + 1),
                style=discord.ButtonStyle.primary,
                custom_id=f"utility_poll:{idx}"
            )
        )
        self.idx = idx

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: discord.ui.Button, match, /):
        return cls(int(match["idx"]), item.label or "")

    async def callback(self, interaction: discord.Interaction):
        cog = interaction.client.get_cog("UtilityCog")
        if cog is None:
            await interaction.response.send_message(
                embed=error_embed("Unavailable", "Please try again in a moment."), ephemeral=True
            )
            return
        await cog.handle_poll_vote(interaction, self.idx)


class EmbedModal(discord.ui.Modal, title="Create Embed"):
    embed_title = discord.ui.TextInput(label="Title (optional)", required=False, max_length=256)
    embed_desc = discord.ui.TextInput(label="Description", style=discord.TextStyle.paragraph, required=True, max_length=4000)
    color_input = discord.ui.TextInput(label="Color hex (optional, e.g. 8B5CF6)", required=False, max_length=7)
    image_input = discord.ui.TextInput(label="Image URL (optional)", required=False, max_length=500)
    footer_input = discord.ui.TextInput(label="Footer (optional)", required=False, max_length=200)

    def __init__(self, channel: discord.TextChannel):
        super().__init__()
        self.channel = channel

    async def on_submit(self, interaction: discord.Interaction):
        color = COLOR_PURPLE
        raw_color = self.color_input.value.strip().lstrip("#")
        if raw_color:
            try:
                color = int(raw_color, 16)
            except ValueError:
                await interaction.response.send_message(
                    embed=error_embed("Invalid Color", "Use a hex color like `8B5CF6`."), ephemeral=True
                )
                return

        embed = discord.Embed(description=self.embed_desc.value, color=color)
        if self.embed_title.value.strip():
            embed.title = self.embed_title.value.strip()
        image = self.image_input.value.strip()
        if image:
            if not image.startswith(("http://", "https://")):
                await interaction.response.send_message(
                    embed=error_embed("Invalid Image URL", "The image URL must start with `http://` or `https://`."),
                    ephemeral=True
                )
                return
            embed.set_image(url=image)
        if self.footer_input.value.strip():
            embed.set_footer(text=self.footer_input.value.strip())

        try:
            await self.channel.send(embed=embed)
        except Exception as e:
            await interaction.response.send_message(
                embed=error_embed("Could Not Send", f"Something went wrong: `{str(e)}`"), ephemeral=True
            )
            return

        await interaction.response.send_message(
            embed=discord.Embed(title="Embed Sent", description=f"Posted in {self.channel.mention}.", color=COLOR_GREEN),
            ephemeral=True
        )


class AnnounceModal(discord.ui.Modal, title="Create Announcement"):
    heading = discord.ui.TextInput(label="Title", required=True, max_length=200)
    body = discord.ui.TextInput(label="Message", style=discord.TextStyle.paragraph, required=True, max_length=4000)
    image_input = discord.ui.TextInput(label="Image URL (optional)", required=False, max_length=500)

    def __init__(self, channel: discord.TextChannel, ping_everyone: bool, ping_role: Optional[discord.Role]):
        super().__init__()
        self.channel = channel
        self.ping_everyone = ping_everyone
        self.ping_role = ping_role

    async def on_submit(self, interaction: discord.Interaction):
        embed = discord.Embed(
            title=f"📢 {self.heading.value.strip()}",
            description=self.body.value,
            color=COLOR_GOLD,
            timestamp=datetime.now(timezone.utc)
        )
        image = self.image_input.value.strip()
        if image:
            if not image.startswith(("http://", "https://")):
                await interaction.response.send_message(
                    embed=error_embed("Invalid Image URL", "The image URL must start with `http://` or `https://`."),
                    ephemeral=True
                )
                return
            embed.set_image(url=image)
        embed.set_footer(text="Announcement")

        pings = []
        if self.ping_everyone:
            pings.append("@everyone")
        if self.ping_role:
            pings.append(self.ping_role.mention)

        try:
            await self.channel.send(
                content=" ".join(pings) or None,
                embed=embed,
                allowed_mentions=discord.AllowedMentions(
                    everyone=self.ping_everyone,
                    roles=[self.ping_role] if self.ping_role else False,
                    users=False
                )
            )
        except Exception as e:
            await interaction.response.send_message(
                embed=error_embed("Could Not Send", f"Something went wrong: `{str(e)}`"), ephemeral=True
            )
            return

        await interaction.response.send_message(
            embed=discord.Embed(title="Announcement Sent", description=f"Posted in {self.channel.mention}.", color=COLOR_GREEN),
            ephemeral=True
        )


class GameCodeModal(discord.ui.Modal, title="Run Luau Code"):
    code = discord.ui.TextInput(
        label="Luau code (max 4000 characters)",
        style=discord.TextStyle.paragraph,
        required=True,
        max_length=4000,
        placeholder="print('Hello from Discord')"
    )

    def __init__(self, cog):
        super().__init__()
        self.cog = cog

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        await self.cog.run_luau(interaction, self.code.value)


# -------------------------------------------------------------
# COG
# -------------------------------------------------------------
class UtilityCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.started_at = datetime.now(timezone.utc)
        self.store = GiveawayStore()
        self.db = Database()
        self.afk = {}

    async def cog_load(self):
        # Persistent buttons: these are re-registered on every start, so they survive redeploys.
        self.bot.add_view(GiveawayView(self))
        self.bot.add_dynamic_items(EntriesPageButton, PageButton, PollButton)

        # Load AFK users so /afk survives restarts
        try:
            for doc in await self.db.find("afk"):
                self.afk[doc["_id"]] = doc
        except Exception as e:
            print(f"[AFK] Could not load AFK users: {e}")

        self.daily_revenue_log.start()
        self.giveaway_checker.start()
        self.reminder_checker.start()

    async def cog_unload(self):
        self.daily_revenue_log.cancel()
        self.giveaway_checker.cancel()
        self.reminder_checker.cancel()

    # OWNER LOCK: every slash command in this cog (including /giveaway subcommands)
    # is hard-coded to BOT_OWNER_ID. Buttons (Enter / Entries) stay public on purpose.
    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == BOT_OWNER_ID:
            return True

        command_name = interaction.command.qualified_name if interaction.command else ""
        is_admin_command = command_name in ADMIN_COMMANDS
        if is_admin_command and interaction.guild is not None and interaction.permissions.administrator:
            return True

        if is_admin_command:
            description = "You need the **Administrator** permission in this server to use this command."
        else:
            description = f"This command can only be used by the bot owner (`{BOT_OWNER_ID}`)."
        try:
            await interaction.response.send_message(
                embed=error_embed("Permission Denied", description),
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
    # /ping
    # =============================================================
    @app_commands.command(name="ping", description="Check the bot's latency and uptime.")
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    async def ping_cmd(self, interaction: discord.Interaction):
        start = time.perf_counter()
        await interaction.response.send_message(
            embed=discord.Embed(title="Pinging...", color=COLOR_PURPLE)
        )
        round_trip = (time.perf_counter() - start) * 1000

        embed = discord.Embed(title="Pong!", color=COLOR_GREEN)
        embed.add_field(name="Gateway Latency", value=f"`{round(self.bot.latency * 1000)} ms`", inline=True)
        embed.add_field(name="API Round Trip", value=f"`{round(round_trip)} ms`", inline=True)
        embed.add_field(name="Online Since", value=f"<t:{int(self.started_at.timestamp())}:R>", inline=True)
        await interaction.edit_original_response(embed=embed)

    # =============================================================
    # /roblox-user
    # =============================================================
    @app_commands.command(name="roblox-user", description="Look up a Roblox user by username.")
    @app_commands.describe(username="The exact Roblox username.")
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    async def roblox_user_cmd(self, interaction: discord.Interaction, username: str):
        await interaction.response.defer()

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    "https://users.roblox.com/v1/usernames/users",
                    json={"usernames": [username.strip()], "excludeBannedUsers": False}
                ) as resp:
                    if resp.status != 200:
                        await interaction.followup.send(
                            embed=error_embed("Roblox API Error", f"Roblox returned status `{resp.status}`.")
                        )
                        return
                    found = (await resp.json()).get("data", [])

                if not found:
                    await interaction.followup.send(
                        embed=error_embed("User Not Found", f"No Roblox user named `{username.strip()}` was found.")
                    )
                    return

                user_id = found[0]["id"]

                async with session.get(f"https://users.roblox.com/v1/users/{user_id}") as resp:
                    if resp.status != 200:
                        await interaction.followup.send(
                            embed=error_embed("Roblox API Error", f"Roblox returned status `{resp.status}`.")
                        )
                        return
                    profile = await resp.json()

                async def get_count(path: str) -> str:
                    try:
                        async with session.get(f"https://friends.roblox.com/v1/users/{user_id}/{path}/count") as r:
                            if r.status == 200:
                                return f"{(await r.json()).get('count', 0):,}"
                    except Exception:
                        pass
                    return "N/A"

                friends, followers, following = await asyncio.gather(
                    get_count("friends"), get_count("followers"), get_count("followings")
                )

                avatar_url = None
                try:
                    async with session.get(
                        "https://thumbnails.roblox.com/v1/users/avatar-headshot",
                        params={"userIds": user_id, "size": "420x420", "format": "Png", "isCircular": "false"}
                    ) as r:
                        if r.status == 200:
                            thumb = (await r.json()).get("data", [])
                            if thumb and thumb[0].get("imageUrl"):
                                avatar_url = thumb[0]["imageUrl"]
                except Exception:
                    pass

            name = profile.get("name", username)
            display = profile.get("displayName", name)
            created = parse_roblox_date(profile.get("created", ""))
            bio = (profile.get("description") or "").strip() or "No bio."
            if len(bio) > 500:
                bio = bio[:497] + "..."

            embed = discord.Embed(
                title=f"{display} (@{name})",
                url=f"https://www.roblox.com/users/{user_id}/profile",
                description=bio,
                color=COLOR_PURPLE
            )
            embed.add_field(name="User ID", value=f"`{user_id}`", inline=True)
            embed.add_field(name="Username", value=f"`{name}`", inline=True)
            embed.add_field(name="Banned", value="`Yes`" if profile.get("isBanned") else "`No`", inline=True)
            if created:
                embed.add_field(name="Created", value=f"<t:{int(created.timestamp())}:D> (<t:{int(created.timestamp())}:R>)", inline=False)
            embed.add_field(name="Friends", value=f"`{friends}`", inline=True)
            embed.add_field(name="Followers", value=f"`{followers}`", inline=True)
            embed.add_field(name="Following", value=f"`{following}`", inline=True)
            if avatar_url:
                embed.set_thumbnail(url=avatar_url)
            await interaction.followup.send(embed=embed)

        except Exception as e:
            print(f"[Roblox User Error] {e}")
            await interaction.followup.send(embed=error_embed("Lookup Error", f"Something went wrong: `{str(e)}`"))

    # =============================================================
    # /cookie-check  (owner only)
    # =============================================================
    @app_commands.command(name="cookie-check", description="Check if your ROBLOX_COOKIE is still valid.")
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    async def cookie_check_cmd(self, interaction: discord.Interaction):
        if not await owner_only(interaction):
            return
        await interaction.response.defer(ephemeral=True)

        headers = get_roblox_cookie_header()
        if not headers:
            await interaction.followup.send(
                embed=error_embed("Cookie Missing", "The `ROBLOX_COOKIE` environment variable is not set."),
                ephemeral=True
            )
            return

        try:
            async with aiohttp.ClientSession(headers=headers) as session:
                async with session.get("https://users.roblox.com/v1/users/authenticated") as resp:
                    status = resp.status
                    me = await resp.json() if status == 200 else {}

                if status != 200:
                    await interaction.followup.send(
                        embed=error_embed(
                            "Cookie Invalid",
                            f"Roblox returned status `{status}`. The cookie is expired or blocked. Please log in again and update `ROBLOX_COOKIE`."
                        ),
                        ephemeral=True
                    )
                    return

                robux = "N/A"
                try:
                    async with session.get(f"https://economy.roblox.com/v1/users/{me['id']}/currency") as r:
                        if r.status == 200:
                            robux = f"{(await r.json()).get('robux', 0):,}"
                except Exception:
                    pass

            embed = discord.Embed(title="Cookie Is Valid", color=COLOR_GREEN)
            embed.add_field(name="Username", value=f"`{me.get('name')}`", inline=True)
            embed.add_field(name="User ID", value=f"`{me.get('id')}`", inline=True)
            embed.add_field(name="Robux Balance", value=f"`{robux}` R$", inline=True)
            await interaction.followup.send(embed=embed, ephemeral=True)

        except Exception as e:
            print(f"[Cookie Check Error] {e}")
            await interaction.followup.send(
                embed=error_embed("Cookie Check Error", f"Something went wrong: `{str(e)}`"),
                ephemeral=True
            )

    # =============================================================
    # /revenue  (owner only)
    # =============================================================
    @app_commands.command(name="revenue", description="Check your Roblox Robux revenue for a time period.")
    @app_commands.describe(period="Time period to check.")
    @app_commands.choices(period=[
        app_commands.Choice(name="Past Day", value="day"),
        app_commands.Choice(name="Past 7 Days", value="week"),
        app_commands.Choice(name="Past 30 Days", value="month"),
        app_commands.Choice(name="Past Year", value="year"),
    ])
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    async def revenue_cmd(self, interaction: discord.Interaction, period: app_commands.Choice[str]):
        if not await owner_only(interaction):
            return
        await interaction.response.defer(ephemeral=True)

        headers = get_roblox_cookie_header()
        if not headers:
            await interaction.followup.send(
                embed=error_embed(
                    "Cookie Missing",
                    "The `ROBLOX_COOKIE` environment variable is not set. Add it in your Render settings and redeploy."
                ),
                ephemeral=True
            )
            return

        time_frame, period_label = REVENUE_PERIODS[period.value]

        try:
            async with aiohttp.ClientSession(headers=headers) as session:
                source = await resolve_revenue_source(session)
                totals = await fetch_revenue_totals(session, source, time_frame)

            embed = discord.Embed(
                title=f"Robux Revenue - {period_label}",
                description=f"Source: {source['label']}",
                color=COLOR_GREEN
            )
            embed.add_field(name="Sales", value=f"`{totals['sales']:,}` R$", inline=True)
            embed.add_field(name=totals["extra_name"], value=f"`{totals['extra']:,}` R$", inline=True)
            embed.add_field(name="Pending", value=f"`{totals['pending']:,}` R$", inline=True)
            embed.set_footer(text="Data from the Roblox Economy API")
            await interaction.followup.send(embed=embed, ephemeral=True)

        except RevenueError as e:
            await interaction.followup.send(embed=error_embed(e.title, e.message), ephemeral=True)
        except Exception as e:
            print(f"[Revenue Error] {e}")
            await interaction.followup.send(
                embed=error_embed("Revenue Error", f"Something went wrong: `{str(e)}`"),
                ephemeral=True
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

    # =============================================================
    # GIVEAWAYS (Dyno style)
    # =============================================================
    giveaway = app_commands.Group(
        name="giveaway",
        description="Create, edit and delete giveaways.",
        guild_only=True
    )

    @staticmethod
    def can_manage(interaction: discord.Interaction) -> bool:
        return interaction.user.id == BOT_OWNER_ID

    def build_giveaway_embed(self, doc: dict, ended: bool = False, winner_ids: Optional[list] = None) -> discord.Embed:
        end_ts = int(doc["end_ts"])
        end_dt = datetime.fromtimestamp(doc["end_ts"], tz=timezone.utc)
        winners_count = doc["winners"]

        if not ended:
            lines = [
                "Click the button below to enter!",
                "",
                f"**Ends:** <t:{end_ts}:R> (<t:{end_ts}:f>)",
                f"**Hosted by:** <@{doc['host_id']}>",
                f"**Winners:** `{winners_count}`",
            ]
            if doc.get("role_id"):
                lines.append(f"**Required Role:** <@&{doc['role_id']}>")
            embed = discord.Embed(
                title=f"🎉 {doc['prize']}",
                description="\n".join(lines),
                color=COLOR_PURPLE,
                timestamp=end_dt
            )
            embed.set_footer(text=f"{winners_count} winner(s) • Ends at")
        else:
            winner_text = ", ".join(f"<@{w}>" for w in winner_ids) if winner_ids else "No valid entries."
            lines = [
                f"**Ended:** <t:{end_ts}:R>",
                f"**Hosted by:** <@{doc['host_id']}>",
                f"**Entries:** `{len(doc.get('entries', []))}`",
                f"**Winner(s):** {winner_text}",
            ]
            embed = discord.Embed(
                title=f"🎉 {doc['prize']}",
                description="\n".join(lines),
                color=COLOR_GREY,
                timestamp=end_dt
            )
            embed.set_footer(text="Giveaway ended • Ended at")

        embed.set_image(url=GIVEAWAY_BANNER_URL)
        return embed

    async def handle_entry(self, interaction: discord.Interaction):
        """Called when someone clicks the Enter Giveaway button."""
        doc = await self.store.get(interaction.message.id)
        if not doc or doc.get("ended"):
            await interaction.response.send_message(
                embed=error_embed("Giveaway Ended", "This giveaway is no longer active."),
                ephemeral=True
            )
            return

        role_id = doc.get("role_id")
        if role_id:
            member_roles = getattr(interaction.user, "roles", [])
            if not any(r.id == role_id for r in member_roles):
                await interaction.response.send_message(
                    embed=error_embed("Role Required", f"You need the <@&{role_id}> role to enter this giveaway."),
                    ephemeral=True
                )
                return

        entered = await self.store.toggle(doc["_id"], interaction.user.id)
        if entered:
            embed = discord.Embed(
                title="You Are In!",
                description=f"You entered the giveaway for **{doc['prize']}**.\nClick the button again if you want to leave.",
                color=COLOR_GREEN
            )
        else:
            embed = discord.Embed(
                title="You Left",
                description=f"You left the giveaway for **{doc['prize']}**.\nClick the button again to re-enter.",
                color=COLOR_GREY
            )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    async def show_entries(self, interaction: discord.Interaction, mid: int, page: int, edit: bool):
        """Shows who entered: one mention per line, 10 lines per page."""
        doc = await self.store.get(mid)
        if not doc:
            embed = error_embed("Giveaway Not Found", "This giveaway no longer exists.")
            if edit:
                await interaction.response.edit_message(embed=embed, view=None)
            else:
                await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        entries = list(doc.get("entries", []))
        per_page = 10
        total_pages = max(1, math.ceil(len(entries) / per_page))
        page = max(0, min(page, total_pages - 1))
        chunk = entries[page * per_page:(page + 1) * per_page]

        embed = discord.Embed(
            title=f"Entries - {doc['prize']}",
            description="\n".join(f"<@{uid}>" for uid in chunk) if chunk else "No one has entered yet.",
            color=COLOR_PURPLE
        )
        embed.set_footer(text=f"Page {page + 1}/{total_pages} • {len(entries)} entries")

        kwargs = {"embed": embed}
        if total_pages > 1:
            view = discord.ui.View(timeout=None)
            view.add_item(EntriesPageButton(mid, max(page - 1, 0), "◀ Previous", "p", disabled=(page == 0)))
            view.add_item(EntriesPageButton(mid, min(page + 1, total_pages - 1), "Next ▶", "n", disabled=(page >= total_pages - 1)))
            kwargs["view"] = view
        elif edit:
            kwargs["view"] = None

        if edit:
            await interaction.response.edit_message(**kwargs)
        else:
            await interaction.response.send_message(ephemeral=True, **kwargs)

    async def get_giveaway_message(self, doc: dict) -> Optional[discord.Message]:
        channel = self.bot.get_channel(doc["channel_id"])
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(doc["channel_id"])
            except Exception:
                return None
        try:
            return await channel.fetch_message(doc["_id"])
        except Exception:
            return None

    async def end_giveaway(self, doc: dict):
        entries = list(doc.get("entries", []))
        winner_ids = random.sample(entries, min(doc["winners"], len(entries))) if entries else []

        # Mark as ended first so the checker never ends it twice
        await self.store.update(doc["_id"], {"ended": True, "winner_ids": winner_ids, "all_winner_ids": winner_ids})

        message = await self.get_giveaway_message(doc)
        if message is None:
            print(f"[Giveaway] Message {doc['_id']} not found, skipping announcement.")
            return

        try:
            await message.edit(
                embed=self.build_giveaway_embed(doc, ended=True, winner_ids=winner_ids),
                view=GiveawayView(self, ended=True)
            )
        except Exception as e:
            print(f"[Giveaway] Could not edit ended giveaway {doc['_id']}: {e}")

        try:
            if winner_ids:
                mentions = " ".join(f"<@{w}>" for w in winner_ids)
                announce = discord.Embed(
                    title="🎉 Giveaway Ended",
                    description=f"Congratulations {mentions}!\nYou won **{doc['prize']}**.",
                    color=COLOR_GREEN
                )
                await message.reply(content=mentions, embed=announce)
            else:
                announce = discord.Embed(
                    title="Giveaway Ended",
                    description=f"No one entered the giveaway for **{doc['prize']}**, so there are no winners.",
                    color=COLOR_GREY
                )
                await message.reply(embed=announce)
        except Exception as e:
            print(f"[Giveaway] Could not announce winners for {doc['_id']}: {e}")

    @tasks.loop(seconds=15)
    async def giveaway_checker(self):
        try:
            now = datetime.now(timezone.utc).timestamp()
            for doc in await self.store.active():
                if doc["end_ts"] <= now:
                    await self.end_giveaway(doc)
        except Exception as e:
            print(f"[Giveaway Checker Error] {e}")

    @giveaway_checker.before_loop
    async def before_giveaway_checker(self):
        await self.bot.wait_until_ready()

    # ---- /giveaway create ----
    @giveaway.command(name="create", description="Start a new giveaway.")
    @app_commands.describe(
        prize="What you are giving away.",
        duration="How long it runs, e.g. 30m, 2h, 1d, 1w.",
        winners="Number of winners (default 1).",
        channel="Channel to post in (default: this channel).",
        required_role="Optional role members must have to enter."
    )
    async def giveaway_create(
        self,
        interaction: discord.Interaction,
        prize: app_commands.Range[str, 1, 200],
        duration: str,
        winners: app_commands.Range[int, 1, 20] = 1,
        channel: Optional[discord.TextChannel] = None,
        required_role: Optional[discord.Role] = None
    ):
        if not self.can_manage(interaction):
            await interaction.response.send_message(
                embed=error_embed("Permission Denied", "Only the bot owner can use giveaway commands."),
                ephemeral=True
            )
            return

        seconds = parse_duration_seconds(duration)
        if seconds is None or seconds < 10 or seconds > 90 * 86400:
            await interaction.response.send_message(
                embed=error_embed(
                    "Invalid Duration",
                    "Use a number with `s`, `m`, `h`, `d` or `w`, for example `30m`, `2h` or `1d`.\nMinimum is `10s`, maximum is `90d`."
                ),
                ephemeral=True
            )
            return

        target = channel or interaction.channel
        perms = target.permissions_for(interaction.guild.me)
        if not (perms.send_messages and perms.embed_links):
            await interaction.response.send_message(
                embed=error_embed("Missing Permissions", f"I need **Send Messages** and **Embed Links** in {target.mention}."),
                ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)

        doc = {
            "_id": 0,
            "guild_id": interaction.guild.id,
            "channel_id": target.id,
            "prize": str(prize),
            "winners": int(winners),
            "end_ts": datetime.now(timezone.utc).timestamp() + seconds,
            "host_id": interaction.user.id,
            "role_id": required_role.id if required_role else None,
            "entries": [],
            "ended": False,
        }

        try:
            message = await target.send(embed=self.build_giveaway_embed(doc), view=GiveawayView(self))
            doc["_id"] = message.id
            await self.store.insert(doc)
        except Exception as e:
            print(f"[Giveaway Create Error] {e}")
            await interaction.followup.send(
                embed=error_embed("Could Not Create Giveaway", f"Something went wrong: `{str(e)}`"),
                ephemeral=True
            )
            return

        confirm = discord.Embed(
            title="Giveaway Created",
            description=f"Posted in {target.mention}.\n\nMessage ID: `{message.id}`\n[Jump to giveaway]({message.jump_url})",
            color=COLOR_GREEN
        )
        await interaction.followup.send(embed=confirm, ephemeral=True)

    # ---- /giveaway edit ----
    @giveaway.command(name="edit", description="Edit an active giveaway.")
    @app_commands.describe(
        message_id="The giveaway message ID (or message link).",
        prize="New prize.",
        winners="New number of winners.",
        duration="New time remaining from now, e.g. 30m, 2h, 1d."
    )
    async def giveaway_edit(
        self,
        interaction: discord.Interaction,
        message_id: str,
        prize: Optional[app_commands.Range[str, 1, 200]] = None,
        winners: Optional[app_commands.Range[int, 1, 20]] = None,
        duration: Optional[str] = None
    ):
        if not self.can_manage(interaction):
            await interaction.response.send_message(
                embed=error_embed("Permission Denied", "Only the bot owner can use giveaway commands."),
                ephemeral=True
            )
            return

        mid = parse_message_id(message_id)
        if mid is None:
            await interaction.response.send_message(
                embed=error_embed("Invalid Message ID", "Please enter a valid message ID or message link."),
                ephemeral=True
            )
            return

        if prize is None and winners is None and duration is None:
            await interaction.response.send_message(
                embed=error_embed("Nothing To Edit", "Please set at least one of: `prize`, `winners` or `duration`."),
                ephemeral=True
            )
            return

        new_seconds = None
        if duration is not None:
            new_seconds = parse_duration_seconds(duration)
            if new_seconds is None or new_seconds < 10 or new_seconds > 90 * 86400:
                await interaction.response.send_message(
                    embed=error_embed(
                        "Invalid Duration",
                        "Use a number with `s`, `m`, `h`, `d` or `w`, for example `30m`, `2h` or `1d`.\nMinimum is `10s`, maximum is `90d`."
                    ),
                    ephemeral=True
                )
                return

        await interaction.response.defer(ephemeral=True)

        doc = await self.store.get(mid)
        if not doc or doc["guild_id"] != interaction.guild.id:
            await interaction.followup.send(
                embed=error_embed("Giveaway Not Found", f"No giveaway with message ID `{mid}` was found in this server."),
                ephemeral=True
            )
            return
        if doc.get("ended"):
            await interaction.followup.send(
                embed=error_embed("Giveaway Ended", "This giveaway already ended, so it cannot be edited."),
                ephemeral=True
            )
            return

        fields = {}
        if prize is not None:
            fields["prize"] = str(prize)
        if winners is not None:
            fields["winners"] = int(winners)
        if new_seconds is not None:
            fields["end_ts"] = datetime.now(timezone.utc).timestamp() + new_seconds

        await self.store.update(mid, fields)
        doc.update(fields)

        message = await self.get_giveaway_message(doc)
        if message is None:
            await interaction.followup.send(
                embed=error_embed("Message Not Found", "I saved the changes, but I could not find the giveaway message to update."),
                ephemeral=True
            )
            return

        try:
            await message.edit(embed=self.build_giveaway_embed(doc), view=GiveawayView(self))
        except Exception as e:
            print(f"[Giveaway Edit Error] {e}")
            await interaction.followup.send(
                embed=error_embed("Could Not Update Message", f"Something went wrong: `{str(e)}`"),
                ephemeral=True
            )
            return

        changed = ", ".join(f"`{k}`" for k in fields.keys())
        await interaction.followup.send(
            embed=discord.Embed(
                title="Giveaway Updated",
                description=f"Updated {changed}.\n[Jump to giveaway]({message.jump_url})",
                color=COLOR_GREEN
            ),
            ephemeral=True
        )

    # ---- /giveaway delete ----
    @giveaway.command(name="delete", description="Delete a giveaway without picking winners.")
    @app_commands.describe(message_id="The giveaway message ID (or message link).")
    async def giveaway_delete(self, interaction: discord.Interaction, message_id: str):
        if not self.can_manage(interaction):
            await interaction.response.send_message(
                embed=error_embed("Permission Denied", "Only the bot owner can use giveaway commands."),
                ephemeral=True
            )
            return

        mid = parse_message_id(message_id)
        if mid is None:
            await interaction.response.send_message(
                embed=error_embed("Invalid Message ID", "Please enter a valid message ID or message link."),
                ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)

        doc = await self.store.get(mid)
        if not doc or doc["guild_id"] != interaction.guild.id:
            await interaction.followup.send(
                embed=error_embed("Giveaway Not Found", f"No giveaway with message ID `{mid}` was found in this server."),
                ephemeral=True
            )
            return

        message = await self.get_giveaway_message(doc)
        if message is not None:
            try:
                await message.delete()
            except Exception as e:
                print(f"[Giveaway Delete Error] Could not delete message {mid}: {e}")

        await self.store.delete(mid)

        await interaction.followup.send(
            embed=discord.Embed(
                title="Giveaway Deleted",
                description=f"The giveaway for **{doc['prize']}** (`{mid}`) was deleted. No winners were picked.",
                color=COLOR_GREEN
            ),
            ephemeral=True
        )

    # ---- /giveaway end ----
    @giveaway.command(name="end", description="End a giveaway now and pick winners.")
    @app_commands.describe(message_id="The giveaway message ID (or message link).")
    async def giveaway_end(self, interaction: discord.Interaction, message_id: str):
        if not self.can_manage(interaction):
            await interaction.response.send_message(
                embed=error_embed("Permission Denied", "Only the bot owner can use giveaway commands."),
                ephemeral=True
            )
            return

        mid = parse_message_id(message_id)
        if mid is None:
            await interaction.response.send_message(
                embed=error_embed("Invalid Message ID", "Please enter a valid message ID or message link."),
                ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)

        doc = await self.store.get(mid)
        if not doc or doc["guild_id"] != interaction.guild.id:
            await interaction.followup.send(
                embed=error_embed("Giveaway Not Found", f"No giveaway with message ID `{mid}` was found in this server."),
                ephemeral=True
            )
            return
        if doc.get("ended"):
            await interaction.followup.send(
                embed=error_embed("Already Ended", "This giveaway already ended. Use `/giveaway reroll` to pick a new winner."),
                ephemeral=True
            )
            return

        await self.end_giveaway(doc)

        await interaction.followup.send(
            embed=discord.Embed(
                title="Giveaway Ended",
                description=f"The giveaway for **{doc['prize']}** (`{mid}`) was ended and the winners were picked.",
                color=COLOR_GREEN
            ),
            ephemeral=True
        )

    # ---- /giveaway reroll ----
    @giveaway.command(name="reroll", description="Pick new winner(s) for an ended giveaway.")
    @app_commands.describe(
        message_id="The giveaway message ID (or message link).",
        winners="How many new winners to pick (default 1)."
    )
    async def giveaway_reroll(
        self,
        interaction: discord.Interaction,
        message_id: str,
        winners: app_commands.Range[int, 1, 20] = 1
    ):
        if not self.can_manage(interaction):
            await interaction.response.send_message(
                embed=error_embed("Permission Denied", "Only the bot owner can use giveaway commands."),
                ephemeral=True
            )
            return

        mid = parse_message_id(message_id)
        if mid is None:
            await interaction.response.send_message(
                embed=error_embed("Invalid Message ID", "Please enter a valid message ID or message link."),
                ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)

        doc = await self.store.get(mid)
        if not doc or doc["guild_id"] != interaction.guild.id:
            await interaction.followup.send(
                embed=error_embed("Giveaway Not Found", f"No giveaway with message ID `{mid}` was found in this server."),
                ephemeral=True
            )
            return
        if not doc.get("ended"):
            await interaction.followup.send(
                embed=error_embed("Still Running", "This giveaway has not ended yet. Use `/giveaway end` first."),
                ephemeral=True
            )
            return

        # Do not pick anyone who already won in this giveaway
        previous = list(doc.get("all_winner_ids", doc.get("winner_ids", [])))
        candidates = [e for e in doc.get("entries", []) if e not in previous]
        if not candidates:
            await interaction.followup.send(
                embed=error_embed("No Entries Left", "There is no one left to pick. Everyone who entered already won."),
                ephemeral=True
            )
            return

        new_winners = random.sample(candidates, min(int(winners), len(candidates)))
        await self.store.update(mid, {"winner_ids": new_winners, "all_winner_ids": previous + new_winners})
        doc["winner_ids"] = new_winners

        message = await self.get_giveaway_message(doc)
        if message is None:
            await interaction.followup.send(
                embed=error_embed("Message Not Found", "I could not find the giveaway message, so I could not announce the new winner(s)."),
                ephemeral=True
            )
            return

        try:
            await message.edit(
                embed=self.build_giveaway_embed(doc, ended=True, winner_ids=new_winners),
                view=GiveawayView(self, ended=True)
            )
        except Exception as e:
            print(f"[Giveaway Reroll] Could not edit message {mid}: {e}")

        mentions = " ".join(f"<@{w}>" for w in new_winners)
        try:
            await message.reply(
                content=mentions,
                embed=discord.Embed(
                    title="🎉 Giveaway Rerolled",
                    description=f"New winner(s): {mentions}\nCongratulations! You won **{doc['prize']}**.",
                    color=COLOR_GREEN
                )
            )
        except Exception as e:
            print(f"[Giveaway Reroll] Could not announce for {mid}: {e}")

        await interaction.followup.send(
            embed=discord.Embed(
                title="Giveaway Rerolled",
                description=f"Picked {len(new_winners)} new winner(s) for **{doc['prize']}**.",
                color=COLOR_GREEN
            ),
            ephemeral=True
        )

    # =============================================================
    # PAGINATION (used by /rolelist, /modlog and /warnings)
    # =============================================================
    async def build_page(self, kind: str, a: int, b: int, page: int):
        """Returns (embed, page, total_pages) or None if the data is gone."""
        if kind == "roles":
            guild = self.bot.get_guild(a)
            if guild is None:
                return None
            roles = [r for r in sorted(guild.roles, key=lambda r: r.position, reverse=True) if not r.is_default()]
            per_page = 20
            total_pages = max(1, math.ceil(len(roles) / per_page))
            page = max(0, min(page, total_pages - 1))
            chunk = roles[page * per_page:(page + 1) * per_page]
            lines = [f"{r.mention} (`{r.id}`)" for r in chunk] or ["No roles."]
            embed = discord.Embed(title=f"Roles in {guild.name}", description="\n".join(lines), color=COLOR_PURPLE)
            embed.set_footer(text=f"Page {page + 1}/{total_pages} • {len(roles)} roles")
            return embed, page, total_pages

        if kind in ("modlog", "warns"):
            query = {"guild_id": a, "user_id": b}
            if kind == "warns":
                query["action"] = "warn"
            logs = await self.db.find("modlogs", query, sort_key="ts", descending=True)
            per_page = 10
            total_pages = max(1, math.ceil(len(logs) / per_page))
            page = max(0, min(page, total_pages - 1))
            chunk = logs[page * per_page:(page + 1) * per_page]

            title = "Warnings" if kind == "warns" else "Moderation Log"
            embed = discord.Embed(
                title=title,
                description=f"User: <@{b}> (`{b}`)\nTotal entries: `{len(logs)}`" if logs else f"User: <@{b}> (`{b}`)\nNo entries found.",
                color=COLOR_PURPLE
            )
            for entry in chunk:
                extra = f"\n**Duration:** `{entry['duration_text']}`" if entry.get("duration_text") else ""
                embed.add_field(
                    name=f"#{entry['case']} • {entry['action'].title()}",
                    value=(
                        f"<t:{int(entry['ts'])}:f>\n"
                        f"**Reason:** {entry['reason']}{extra}\n"
                        f"**Moderator:** <@{entry['moderator_id']}>"
                    ),
                    inline=False
                )
            embed.set_footer(text=f"Page {page + 1}/{total_pages}")
            return embed, page, total_pages

        return None

    async def show_page(self, interaction: discord.Interaction, kind: str, a: int, b: int, page: int,
                        edit: bool, ephemeral: bool = True, deferred: bool = False):
        result = await self.build_page(kind, a, b, page)
        if result is None:
            embed = error_embed("Not Available", "I could not load this list anymore.")
            if edit:
                await interaction.response.edit_message(embed=embed, view=None)
            elif deferred:
                await interaction.followup.send(embed=embed, ephemeral=True)
            else:
                await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        embed, page, total_pages = result
        kwargs = {"embed": embed}
        if total_pages > 1:
            view = discord.ui.View(timeout=None)
            view.add_item(PageButton(kind, a, b, max(page - 1, 0), "◀ Previous", "p", disabled=(page == 0)))
            view.add_item(PageButton(kind, a, b, min(page + 1, total_pages - 1), "Next ▶", "n", disabled=(page >= total_pages - 1)))
            kwargs["view"] = view
        elif edit:
            kwargs["view"] = None

        if edit:
            await interaction.response.edit_message(**kwargs)
        elif deferred:
            await interaction.followup.send(ephemeral=ephemeral, **kwargs)
        else:
            await interaction.response.send_message(ephemeral=ephemeral, **kwargs)

    # =============================================================
    # /poll
    # =============================================================
    @app_commands.command(name="poll", description="Create a quick poll with buttons.")
    @app_commands.describe(
        question="The poll question.",
        option1="First option.",
        option2="Second option.",
        option3="Third option (optional).",
        option4="Fourth option (optional).",
        option5="Fifth option (optional)."
    )
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    async def poll_cmd(
        self,
        interaction: discord.Interaction,
        question: app_commands.Range[str, 1, 200],
        option1: app_commands.Range[str, 1, 80],
        option2: app_commands.Range[str, 1, 80],
        option3: Optional[app_commands.Range[str, 1, 80]] = None,
        option4: Optional[app_commands.Range[str, 1, 80]] = None,
        option5: Optional[app_commands.Range[str, 1, 80]] = None
    ):
        options = [str(o) for o in (option1, option2, option3, option4, option5) if o]
        doc = {"_id": 0, "question": str(question), "options": options, "votes": {}, "creator_id": interaction.user.id}

        view = discord.ui.View(timeout=None)
        for i, option in enumerate(options):
            view.add_item(PollButton(i, option))

        await interaction.response.send_message(embed=build_poll_embed(doc), view=view)
        message = await interaction.original_response()
        doc["_id"] = message.id
        await self.db.insert("polls", doc)

    async def handle_poll_vote(self, interaction: discord.Interaction, idx: int):
        doc = await self.db.get("polls", interaction.message.id)
        if not doc or idx >= len(doc["options"]):
            await interaction.response.send_message(
                embed=error_embed("Poll Not Found", "This poll is no longer available."), ephemeral=True
            )
            return

        votes = dict(doc.get("votes", {}))
        key = str(interaction.user.id)
        if votes.get(key) == idx:
            votes.pop(key)
        else:
            votes[key] = idx
        doc["votes"] = votes
        await self.db.update("polls", doc["_id"], {"votes": votes})
        await interaction.response.edit_message(embed=build_poll_embed(doc))

    # =============================================================
    # /remind
    # =============================================================
    @app_commands.command(name="remind", description="Set a reminder.")
    @app_commands.describe(time="When to remind you, e.g. 30m, 2h, 1d, 1h30m.", message="What to remind you about.")
    @app_commands.rename(time="when")
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    async def remind_cmd(self, interaction: discord.Interaction, time: str, message: app_commands.Range[str, 1, 500]):
        seconds = parse_duration_seconds(time)
        if seconds is None or seconds < 10 or seconds > 365 * 86400:
            await interaction.response.send_message(
                embed=error_embed(
                    "Invalid Time",
                    "Use numbers with `s`, `m`, `h`, `d` or `w`, for example `30m`, `2h`, `1d` or `1h30m`.\nMinimum is `10s`, maximum is `365d`."
                ),
                ephemeral=True
            )
            return

        now_ts = datetime.now(timezone.utc).timestamp()
        due_ts = now_ts + seconds
        await self.db.insert("reminders", {
            "_id": uuid.uuid4().hex,
            "user_id": interaction.user.id,
            "channel_id": interaction.channel_id,
            "message": str(message),
            "due_ts": due_ts,
            "created_ts": now_ts,
        })

        embed = discord.Embed(
            title="Reminder Set",
            description=f"I will remind you <t:{int(due_ts)}:R> (<t:{int(due_ts)}:f>).",
            color=COLOR_GREEN
        )
        embed.add_field(name="Message", value=f"`{str(message)[:900]}`", inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @tasks.loop(seconds=15)
    async def reminder_checker(self):
        try:
            now_ts = datetime.now(timezone.utc).timestamp()
            for doc in await self.db.find("reminders", {"due_ts": {"$lte": now_ts}}):
                # Delete first so a slow send can never trigger twice
                await self.db.delete("reminders", doc["_id"])

                embed = discord.Embed(title="⏰ Reminder", description=doc["message"], color=COLOR_PURPLE)
                embed.add_field(name="Set", value=f"<t:{int(doc['created_ts'])}:R>", inline=True)

                delivered = False
                try:
                    user = await self.bot.fetch_user(doc["user_id"])
                    await user.send(embed=embed)
                    delivered = True
                except Exception:
                    pass

                if not delivered and doc.get("channel_id"):
                    try:
                        channel = self.bot.get_channel(doc["channel_id"]) or await self.bot.fetch_channel(doc["channel_id"])
                        await channel.send(content=f"<@{doc['user_id']}>", embed=embed)
                    except Exception as e:
                        print(f"[Reminder] Could not deliver reminder {doc['_id']}: {e}")
        except Exception as e:
            print(f"[Reminder Checker Error] {e}")

    @reminder_checker.before_loop
    async def before_reminder_checker(self):
        await self.bot.wait_until_ready()

    # =============================================================
    # /translate
    # =============================================================
    @app_commands.command(name="translate", description="Translate text into another language.")
    @app_commands.describe(language="Target language, e.g. Japanese, Spanish, French.", text="The text to translate.")
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    async def translate_cmd(self, interaction: discord.Interaction, language: str, text: app_commands.Range[str, 1, 800]):
        wanted = language.strip().lower()
        target = LANGUAGES.get(wanted) or LANGUAGE_CODES.get(wanted)
        if target is None:
            await interaction.response.send_message(
                embed=error_embed("Unknown Language", "Pick a language from the list that appears while you type (for example `Japanese`) or use a code like `ja`."),
                ephemeral=True
            )
            return

        await interaction.response.defer()
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    "https://translate.googleapis.com/translate_a/single",
                    params={"client": "gtx", "sl": "auto", "tl": target, "dt": "t", "q": str(text)}
                ) as resp:
                    if resp.status != 200:
                        await interaction.followup.send(
                            embed=error_embed("Translate Error", f"The translation service returned status `{resp.status}`.")
                        )
                        return
                    data = await resp.json(content_type=None)

            translated = "".join(part[0] for part in data[0] if part and part[0])
            detected = str(data[2]).lower() if len(data) > 2 and data[2] else "auto"
            detected_name = CODE_TO_LANGUAGE.get(detected, detected)
            target_name = CODE_TO_LANGUAGE.get(target.lower(), target)

            original_text = str(text).replace("```", "'''")
            translated = translated.replace("```", "'''")

            embed = discord.Embed(title="Translation", color=COLOR_PURPLE)
            embed.add_field(name=f"Original ({detected_name})", value=f"```{original_text[:1000]}```", inline=False)
            embed.add_field(name=f"Translation ({target_name})", value=f"```{translated[:1000]}```", inline=False)
            await interaction.followup.send(embed=embed)
        except Exception as e:
            print(f"[Translate Error] {e}")
            await interaction.followup.send(embed=error_embed("Translate Error", f"Something went wrong: `{str(e)}`"))

    @translate_cmd.autocomplete("language")
    async def translate_language_autocomplete(self, interaction: discord.Interaction, current: str):
        current = current.lower()
        return [
            app_commands.Choice(name=name.title(), value=name)
            for name in LANGUAGES if current in name
        ][:25]

    # =============================================================
    # /afk  (+ listener)
    # =============================================================
    @app_commands.command(name="afk", description="Set yourself AFK. People who ping you get your reason.")
    @app_commands.describe(reason="Why you are away.")
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    async def afk_cmd(self, interaction: discord.Interaction, reason: app_commands.Range[str, 1, 200] = "AFK"):
        doc = {"_id": interaction.user.id, "reason": str(reason), "since": datetime.now(timezone.utc).timestamp()}
        self.afk[interaction.user.id] = doc
        await self.db.insert("afk", doc)

        embed = discord.Embed(
            title="You Are Now AFK",
            description=f"Reason: `{doc['reason']}`\nI will remove your AFK when you send a message.",
            color=COLOR_GREEN
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or message.guild is None or not self.afk:
            return

        no_pings = discord.AllowedMentions.none()

        # The AFK user wrote a message: remove AFK
        if message.author.id in self.afk:
            data = self.afk.pop(message.author.id)
            await self.db.delete("afk", message.author.id)
            try:
                await message.reply(
                    embed=discord.Embed(
                        title="Welcome Back",
                        description=f"I removed your AFK. You were away since <t:{int(data['since'])}:R>.",
                        color=COLOR_GREEN
                    ),
                    mention_author=False,
                    allowed_mentions=no_pings,
                    delete_after=15
                )
            except Exception as e:
                print(f"[AFK] Could not send welcome back message: {e}")

        # Someone pinged an AFK user: send the reason
        lines = []
        for user in message.mentions:
            data = self.afk.get(user.id)
            if data and user.id != message.author.id:
                lines.append(f"{user.mention} is AFK: `{data['reason']}` (since <t:{int(data['since'])}:R>)")
        if lines:
            try:
                await message.reply(
                    embed=discord.Embed(title="AFK", description="\n".join(lines), color=COLOR_GREY),
                    mention_author=False,
                    allowed_mentions=no_pings
                )
            except Exception as e:
                print(f"[AFK] Could not send AFK notice: {e}")

    # =============================================================
    # /timestamp
    # =============================================================
    @app_commands.command(name="timestamp", description="Make Discord timestamps you can copy.")
    @app_commands.describe(
        when="Date like 2026-12-25 18:00, a duration like 2h30m (from now), or a unix time. Empty = now.",
        utc_offset="Your UTC offset for dates, e.g. +9 or -5 (default +9)."
    )
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    async def timestamp_cmd(self, interaction: discord.Interaction, when: Optional[str] = None, utc_offset: str = "+9"):
        offset_match = re.fullmatch(r"([+-]?)(\d{1,2})(?::?(\d{2}))?", utc_offset.strip())
        if not offset_match:
            await interaction.response.send_message(
                embed=error_embed("Invalid Offset", "Use an offset like `+9`, `-5` or `+5:30`."), ephemeral=True
            )
            return
        sign = -1 if offset_match.group(1) == "-" else 1
        tz = timezone(sign * timedelta(hours=int(offset_match.group(2)), minutes=int(offset_match.group(3) or 0)))

        moment = None
        raw = (when or "").strip()
        if not raw:
            moment = datetime.now(timezone.utc)
        elif raw.isdigit() and len(raw) >= 9:
            moment = datetime.fromtimestamp(int(raw), tz=timezone.utc)
        else:
            relative = parse_duration_seconds(raw.lower().removeprefix("in ").strip())
            if relative is not None:
                moment = datetime.now(timezone.utc) + timedelta(seconds=relative)
            else:
                for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d", "%Y/%m/%d %H:%M", "%Y/%m/%d"):
                    try:
                        moment = datetime.strptime(raw, fmt).replace(tzinfo=tz)
                        break
                    except ValueError:
                        continue

        if moment is None:
            await interaction.response.send_message(
                embed=error_embed(
                    "Invalid Date",
                    "Use `2026-12-25 18:00`, a duration like `2h30m`, or a unix time like `1767225600`."
                ),
                ephemeral=True
            )
            return

        ts = int(moment.timestamp())
        styles = [
            ("t", "Short Time"), ("T", "Long Time"), ("d", "Short Date"), ("D", "Long Date"),
            ("f", "Short Date/Time"), ("F", "Long Date/Time"), ("R", "Relative"),
        ]
        embed = discord.Embed(title="Discord Timestamp", description=f"Unix time: `{ts}`", color=COLOR_PURPLE)
        for style, label in styles:
            embed.add_field(name=label, value=f"<t:{ts}:{style}>\n`<t:{ts}:{style}>`", inline=True)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # =============================================================
    # /snowflake
    # =============================================================
    @app_commands.command(name="snowflake", description="Decode a Discord ID (snowflake).")
    @app_commands.describe(id="The Discord ID to decode.")
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    async def snowflake_cmd(self, interaction: discord.Interaction, id: str):
        digits = re.sub(r"\D", "", id)
        if not digits:
            await interaction.response.send_message(
                embed=error_embed("Invalid ID", "Please enter a Discord ID (numbers only)."), ephemeral=True
            )
            return

        try:
            sid = int(digits)
            ms = (sid >> 22) + DISCORD_EPOCH
            created = datetime.fromtimestamp(ms / 1000, tz=timezone.utc)
        except (ValueError, OverflowError, OSError):
            await interaction.response.send_message(
                embed=error_embed("Invalid ID", "That number is not a valid Discord ID."), ephemeral=True
            )
            return

        ts = int(created.timestamp())
        embed = discord.Embed(title="Snowflake Decoded", color=COLOR_PURPLE)
        embed.add_field(name="ID", value=f"`{sid}`", inline=False)
        embed.add_field(name="Created", value=f"<t:{ts}:F> (<t:{ts}:R>)", inline=False)
        embed.add_field(name="Unix Time (ms)", value=f"`{ms}`", inline=True)
        embed.add_field(name="Worker ID", value=f"`{(sid & 0x3E0000) >> 17}`", inline=True)
        embed.add_field(name="Process ID", value=f"`{(sid & 0x1F000) >> 12}`", inline=True)
        embed.add_field(name="Increment", value=f"`{sid & 0xFFF}`", inline=True)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # =============================================================
    # /permissions
    # =============================================================
    @app_commands.command(name="permissions", description="Show the permissions of a member.")
    @app_commands.describe(user="The member to check.")
    @app_commands.guild_only()
    async def permissions_cmd(self, interaction: discord.Interaction, user: discord.Member):
        def format_perms(perms: discord.Permissions) -> str:
            if perms.administrator:
                return "`Administrator` (all permissions)"
            names = sorted(name.replace("_", " ").title() for name, value in perms if value)
            text = ", ".join(f"`{n}`" for n in names) or "None"
            return text if len(text) <= 1000 else text[:997] + "..."

        embed = discord.Embed(title=f"Permissions - {user.display_name}", color=COLOR_PURPLE)
        embed.set_thumbnail(url=user.display_avatar.url)
        embed.add_field(name="Server Permissions", value=format_perms(user.guild_permissions), inline=False)

        channel = interaction.channel
        if isinstance(channel, (discord.abc.GuildChannel, discord.Thread)):
            embed.add_field(name=f"In #{channel.name}", value=format_perms(channel.permissions_for(user)), inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # =============================================================
    # /whois
    # =============================================================
    @app_commands.command(name="whois", description="Show information about a member.")
    @app_commands.describe(user="The member to look up.")
    @app_commands.guild_only()
    async def whois_cmd(self, interaction: discord.Interaction, user: discord.Member):
        embed = discord.Embed(title=f"{user.display_name} (@{user.name})", color=user.color if user.color.value else COLOR_PURPLE)
        embed.set_thumbnail(url=user.display_avatar.url)
        embed.add_field(name="User ID", value=f"`{user.id}`", inline=True)
        embed.add_field(name="Bot", value="`Yes`" if user.bot else "`No`", inline=True)
        embed.add_field(name="Top Role", value=user.top_role.mention if not user.top_role.is_default() else "`None`", inline=True)
        embed.add_field(name="Account Created", value=f"<t:{int(user.created_at.timestamp())}:F> (<t:{int(user.created_at.timestamp())}:R>)", inline=False)
        if user.joined_at:
            embed.add_field(name="Joined Server", value=f"<t:{int(user.joined_at.timestamp())}:F> (<t:{int(user.joined_at.timestamp())}:R>)", inline=False)
        if user.premium_since:
            embed.add_field(name="Boosting Since", value=f"<t:{int(user.premium_since.timestamp())}:R>", inline=True)
        if user.is_timed_out():
            embed.add_field(name="Timed Out Until", value=f"<t:{int(user.timed_out_until.timestamp())}:R>", inline=True)

        roles = [r.mention for r in reversed(user.roles) if not r.is_default()]
        roles_text = " ".join(roles) or "None"
        if len(roles_text) > 1000:
            roles_text = roles_text[:997] + "..."
        embed.add_field(name=f"Roles ({len(roles)})", value=roles_text, inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # =============================================================
    # /emojiinfo
    # =============================================================
    @app_commands.command(name="emojiinfo", description="Show information about an emoji.")
    @app_commands.describe(emoji="A custom emoji, an emoji ID, or a normal emoji.")
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    async def emojiinfo_cmd(self, interaction: discord.Interaction, emoji: str):
        raw = emoji.strip()
        custom = re.fullmatch(r"<(a?):([A-Za-z0-9_]+):(\d+)>", raw)

        if custom or raw.isdigit():
            animated = bool(custom and custom.group(1))
            name = custom.group(2) if custom else "Unknown"
            emoji_id = int(custom.group(3)) if custom else int(raw)
            known = self.bot.get_emoji(emoji_id)
            if known:
                name = known.name
                animated = known.animated
            ext = "gif" if animated else "png"
            url = f"https://cdn.discordapp.com/emojis/{emoji_id}.{ext}?size=256"
            created = datetime.fromtimestamp(((emoji_id >> 22) + DISCORD_EPOCH) / 1000, tz=timezone.utc)

            embed = discord.Embed(title=f"Emoji - {name}", url=url, color=COLOR_PURPLE)
            embed.set_thumbnail(url=url)
            embed.add_field(name="Name", value=f"`{name}`", inline=True)
            embed.add_field(name="ID", value=f"`{emoji_id}`", inline=True)
            embed.add_field(name="Animated", value="`Yes`" if animated else "`No`", inline=True)
            embed.add_field(name="Created", value=f"<t:{int(created.timestamp())}:F>", inline=False)
            embed.add_field(name="Usage", value=f"`<{'a' if animated else ''}:{name}:{emoji_id}>`", inline=False)
            embed.add_field(name="Image URL", value=f"[Open image]({url})", inline=False)
            if known and known.guild:
                embed.add_field(name="Server", value=f"`{known.guild.name}`", inline=True)
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        chars = [c for c in raw if c not in ("️", "‍")]
        if not chars:
            await interaction.response.send_message(
                embed=error_embed("Invalid Emoji", "Please enter a custom emoji, an emoji ID or a normal emoji."),
                ephemeral=True
            )
            return

        lines = [f"`U+{ord(c):04X}` {unicodedata.name(c, 'Unknown character').title()}" for c in chars[:10]]
        embed = discord.Embed(title="Emoji Info", color=COLOR_PURPLE)
        embed.add_field(name="Emoji", value=raw[:50], inline=True)
        embed.add_field(name="Characters", value=f"`{len(chars)}`", inline=True)
        embed.add_field(name="Unicode", value="\n".join(lines), inline=False)
        embed.add_field(name="Copy", value=f"`{raw[:50]}`", inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # =============================================================
    # /rolelist
    # =============================================================
    @app_commands.command(name="rolelist", description="List all roles in this server (one per line).")
    @app_commands.guild_only()
    async def rolelist_cmd(self, interaction: discord.Interaction):
        await self.show_page(interaction, "roles", interaction.guild.id, 0, 0, edit=False, ephemeral=False)

    # =============================================================
    # /channelstats
    # =============================================================
    @app_commands.command(name="channelstats", description="Show channel counts and info about a channel.")
    @app_commands.describe(channel="Channel to inspect (default: this channel).")
    @app_commands.guild_only()
    async def channelstats_cmd(self, interaction: discord.Interaction, channel: Optional[discord.abc.GuildChannel] = None):
        guild = interaction.guild
        target = channel or interaction.channel

        embed = discord.Embed(title=f"Channel Stats - {guild.name}", color=COLOR_PURPLE)
        embed.add_field(name="Total Channels", value=f"`{len(guild.channels)}`", inline=True)
        embed.add_field(name="Text", value=f"`{len(guild.text_channels)}`", inline=True)
        embed.add_field(name="Voice", value=f"`{len(guild.voice_channels)}`", inline=True)
        embed.add_field(name="Stage", value=f"`{len(guild.stage_channels)}`", inline=True)
        embed.add_field(name="Forum", value=f"`{len(guild.forums)}`", inline=True)
        embed.add_field(name="Categories", value=f"`{len(guild.categories)}`", inline=True)
        embed.add_field(name="Active Threads", value=f"`{len(guild.threads)}`", inline=True)

        if target is not None:
            lines = [
                f"Channel: {target.mention}",
                f"ID: `{target.id}`",
                f"Type: `{str(target.type).replace('_', ' ').title()}`",
                f"Created: <t:{int(target.created_at.timestamp())}:R>",
            ]
            category = getattr(target, "category", None)
            if category:
                lines.append(f"Category: `{category.name}`")
            topic = getattr(target, "topic", None)
            if topic:
                lines.append(f"Topic: {topic[:200]}")
            slowmode = getattr(target, "slowmode_delay", None)
            if slowmode is not None:
                lines.append(f"Slowmode: `{slowmode}s`" if slowmode else "Slowmode: `Off`")
            nsfw = getattr(target, "nsfw", None)
            if nsfw is not None:
                lines.append(f"NSFW: `{'Yes' if nsfw else 'No'}`")
            embed.add_field(name="Selected Channel", value="\n".join(lines), inline=False)

        await interaction.response.send_message(embed=embed, ephemeral=True)

    # =============================================================
    # ROBLOX: /groupcheck
    # =============================================================
    @app_commands.command(name="groupcheck", description="Check if a Roblox user is in your Roblox group.")
    @app_commands.describe(user="The Roblox username to check.")
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    async def groupcheck_cmd(self, interaction: discord.Interaction, user: str):
        group_id = os.getenv("ROBLOX_GROUP_ID", "").strip()
        if not group_id:
            await interaction.response.send_message(
                embed=error_embed("Group Missing", "The `ROBLOX_GROUP_ID` environment variable is not set."), ephemeral=True
            )
            return
        headers = get_roblox_cookie_header()
        if not headers:
            await interaction.response.send_message(
                embed=error_embed("Cookie Missing", "The `ROBLOX_COOKIE` environment variable is not set."), ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)
        try:
            async with aiohttp.ClientSession(headers=headers) as session:
                async with session.post(
                    "https://users.roblox.com/v1/usernames/users",
                    json={"usernames": [user.strip()], "excludeBannedUsers": False}
                ) as resp:
                    found = (await resp.json()).get("data", []) if resp.status == 200 else []
                if not found:
                    await interaction.followup.send(
                        embed=error_embed("User Not Found", f"No Roblox user named `{user.strip()}` was found."), ephemeral=True
                    )
                    return
                roblox_id = found[0]["id"]
                roblox_name = found[0].get("name", user.strip())

                group_name = f"Group {group_id}"
                member_count = None
                async with session.get(f"https://groups.roblox.com/v1/groups/{group_id}") as resp:
                    if resp.status == 200:
                        info = await resp.json()
                        group_name = info.get("name", group_name)
                        member_count = info.get("memberCount")

                membership = None
                async with session.get(f"https://groups.roblox.com/v1/users/{roblox_id}/groups/roles") as resp:
                    if resp.status != 200:
                        await interaction.followup.send(
                            embed=error_embed("Roblox API Error", f"Roblox returned status `{resp.status}`."), ephemeral=True
                        )
                        return
                    for item in (await resp.json()).get("data", []):
                        if str(item.get("group", {}).get("id")) == group_id:
                            membership = item
                            break

                pending = False
                if membership is None:
                    # Needs the cookie: checks for a pending join request
                    async with session.get(f"https://groups.roblox.com/v1/groups/{group_id}/join-requests/users/{roblox_id}") as resp:
                        pending = resp.status == 200

            if membership:
                color = COLOR_GREEN
                status = "`Member`"
            elif pending:
                color = COLOR_GOLD
                status = "`Pending join request`"
            else:
                color = COLOR_RED
                status = "`Not a member`"

            embed = discord.Embed(title="Group Check", color=color)
            embed.add_field(name="Roblox User", value=f"`{roblox_name}` (`{roblox_id}`)", inline=False)
            embed.add_field(name="Group", value=f"`{group_name}` (`{group_id}`)", inline=False)
            embed.add_field(name="Status", value=status, inline=True)
            if membership:
                embed.add_field(name="Role", value=f"`{membership['role'].get('name')}`", inline=True)
                embed.add_field(name="Rank", value=f"`{membership['role'].get('rank')}`", inline=True)
            if member_count is not None:
                embed.add_field(name="Group Members", value=f"`{member_count:,}`", inline=True)
            await interaction.followup.send(embed=embed, ephemeral=True)

        except Exception as e:
            print(f"[Group Check Error] {e}")
            await interaction.followup.send(embed=error_embed("Group Check Error", f"Something went wrong: `{str(e)}`"), ephemeral=True)

    # =============================================================
    # ROBLOX: /gameinfo
    # =============================================================
    async def resolve_universe_id(self, session: aiohttp.ClientSession, query: str) -> Optional[int]:
        q = (query or "").strip() or ROBLOX_DEFAULT_PLACE_ID
        link = re.search(r"roblox\.com/games/(\d+)", q)
        if link:
            q = link.group(1)

        if q.isdigit():
            try:
                async with session.get(f"https://apis.roblox.com/universes/v1/places/{q}/universe") as resp:
                    if resp.status == 200:
                        universe = (await resp.json()).get("universeId")
                        if universe:
                            return int(universe)
            except Exception:
                pass
            return int(q)  # maybe it is already a universe ID

        try:
            async with session.get(
                "https://apis.roblox.com/search-api/omni-search",
                params={"searchQuery": q, "pageType": "all", "sessionId": str(uuid.uuid4())}
            ) as resp:
                if resp.status == 200:
                    for group in (await resp.json()).get("searchResults", []):
                        for item in group.get("contents", []):
                            if item.get("universeId"):
                                return int(item["universeId"])
        except Exception:
            pass
        return None

    @app_commands.command(name="gameinfo", description="Show information about a Roblox game.")
    @app_commands.describe(game="Game name, place ID, universe ID or game link (default: Template Engine).")
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    async def gameinfo_cmd(self, interaction: discord.Interaction, game: Optional[str] = None):
        await interaction.response.defer(ephemeral=True)
        try:
            async with aiohttp.ClientSession() as session:
                universe_id = await self.resolve_universe_id(session, game or "")
                if universe_id is None:
                    await interaction.followup.send(
                        embed=error_embed("Game Not Found", "I could not find that game. Try a place ID or a game link."), ephemeral=True
                    )
                    return

                async with session.get("https://games.roblox.com/v1/games", params={"universeIds": universe_id}) as resp:
                    details = (await resp.json()).get("data", []) if resp.status == 200 else []
                if not details:
                    await interaction.followup.send(
                        embed=error_embed("Game Not Found", f"No game was found for ID `{universe_id}`."), ephemeral=True
                    )
                    return
                info = details[0]

                votes = {}
                async with session.get("https://games.roblox.com/v1/games/votes", params={"universeIds": universe_id}) as resp:
                    if resp.status == 200:
                        vote_data = (await resp.json()).get("data", [])
                        votes = vote_data[0] if vote_data else {}

                icon = None
                async with session.get(
                    "https://thumbnails.roblox.com/v1/games/icons",
                    params={"universeIds": universe_id, "size": "256x256", "format": "Png", "isCircular": "false"}
                ) as resp:
                    if resp.status == 200:
                        thumbs = (await resp.json()).get("data", [])
                        if thumbs and thumbs[0].get("imageUrl"):
                            icon = thumbs[0]["imageUrl"]

            place_id = info.get("rootPlaceId")
            description = (info.get("description") or "").strip() or "No description."
            if len(description) > 300:
                description = description[:297] + "..."

            embed = discord.Embed(
                title=info.get("name", "Unknown Game"),
                url=f"https://www.roblox.com/games/{place_id}" if place_id else None,
                description=description,
                color=COLOR_PURPLE
            )
            if icon:
                embed.set_thumbnail(url=icon)
            creator = info.get("creator", {})
            embed.add_field(name="Playing", value=f"`{info.get('playing', 0):,}`", inline=True)
            embed.add_field(name="Visits", value=f"`{info.get('visits', 0):,}`", inline=True)
            embed.add_field(name="Favorites", value=f"`{info.get('favoritedCount', 0):,}`", inline=True)
            embed.add_field(name="Likes", value=f"`{votes.get('upVotes', 0):,}`", inline=True)
            embed.add_field(name="Dislikes", value=f"`{votes.get('downVotes', 0):,}`", inline=True)
            embed.add_field(name="Max Players", value=f"`{info.get('maxPlayers', 0)}`", inline=True)
            embed.add_field(name="Creator", value=f"`{creator.get('name', 'Unknown')}` ({creator.get('type', 'Unknown')})", inline=True)
            embed.add_field(name="Genre", value=f"`{info.get('genre', 'Unknown')}`", inline=True)
            created = parse_roblox_date(info.get("created", ""))
            updated = parse_roblox_date(info.get("updated", ""))
            if created:
                embed.add_field(name="Created", value=f"<t:{int(created.timestamp())}:D>", inline=True)
            if updated:
                embed.add_field(name="Last Updated", value=f"<t:{int(updated.timestamp())}:R>", inline=True)
            embed.add_field(name="Universe ID", value=f"`{universe_id}`", inline=True)
            if place_id:
                embed.add_field(name="Place ID", value=f"`{place_id}`", inline=True)
            await interaction.followup.send(embed=embed, ephemeral=True)

        except Exception as e:
            print(f"[Game Info Error] {e}")
            await interaction.followup.send(embed=error_embed("Game Info Error", f"Something went wrong: `{str(e)}`"), ephemeral=True)

    # =============================================================
    # ROBLOX: /game  (run Luau code with Open Cloud)
    # =============================================================
    @app_commands.command(name="game", description="Run Luau code on your Roblox game with Open Cloud.")
    @app_commands.describe(file="Optional .lua / .luau / .txt file with the code. Leave empty to type the code in a window.")
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    async def game_cmd(self, interaction: discord.Interaction, file: Optional[discord.Attachment] = None):
        if file is None:
            await interaction.response.send_modal(GameCodeModal(self))
            return

        if not file.filename.lower().endswith((".lua", ".luau", ".txt")):
            await interaction.response.send_message(
                embed=error_embed("Wrong File Type", "Please upload a `.lua`, `.luau` or `.txt` file."), ephemeral=True
            )
            return
        if file.size > 4_000_000:
            await interaction.response.send_message(
                embed=error_embed("File Too Large", "Roblox accepts scripts up to 4 MB."), ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)
        try:
            code = (await file.read()).decode("utf-8")
        except Exception:
            await interaction.followup.send(
                embed=error_embed("Could Not Read File", "The file must be a UTF-8 text file."), ephemeral=True
            )
            return
        await self.run_luau(interaction, code)

    async def run_luau(self, interaction: discord.Interaction, code: str):
        """Runs Luau code through the Roblox Open Cloud Luau Execution API. The interaction must already be deferred."""
        api_key = os.getenv("ROBLOX_OPEN_CLOUD_KEY", "").strip()
        if not api_key:
            await interaction.followup.send(
                embed=error_embed("API Key Missing", "The `ROBLOX_OPEN_CLOUD_KEY` environment variable is not set."), ephemeral=True
            )
            return

        place_id = os.getenv("ROBLOX_PLACE_ID", "").strip() or ROBLOX_DEFAULT_PLACE_ID
        universe_id = os.getenv("ROBLOX_UNIVERSE_ID", "").strip()

        try:
            async with aiohttp.ClientSession(headers={"x-api-key": api_key}) as session:
                if not universe_id:
                    resolved = await self.resolve_universe_id(session, place_id)
                    universe_id = str(resolved) if resolved else ""
                if not universe_id:
                    await interaction.followup.send(
                        embed=error_embed("Universe Not Found", "Set `ROBLOX_UNIVERSE_ID` (and `ROBLOX_PLACE_ID`) in your environment variables."),
                        ephemeral=True
                    )
                    return

                create_url = f"https://apis.roblox.com/cloud/v2/universes/{universe_id}/places/{place_id}/luau-execution-session-tasks"
                async with session.post(create_url, json={"script": code, "timeout": "60s"}) as resp:
                    status = resp.status
                    raw = await resp.text()
                try:
                    task = json.loads(raw)
                except ValueError:
                    task = {}

                if status in (401, 403):
                    await interaction.followup.send(
                        embed=error_embed(
                            "API Key Rejected",
                            "Roblox rejected `ROBLOX_OPEN_CLOUD_KEY`. The key needs the `universe.place.luau-execution-session:write` permission for this experience."
                        ),
                        ephemeral=True
                    )
                    return
                if status == 429:
                    await interaction.followup.send(
                        embed=error_embed("Rate Limited", "Roblox allows 5 runs per minute and 10 unfinished runs per place. Please wait and try again."),
                        ephemeral=True
                    )
                    return
                if status >= 300 or not task.get("path"):
                    message = task.get("message") if isinstance(task, dict) else None
                    await interaction.followup.send(
                        embed=error_embed("Roblox API Error", f"Roblox returned status `{status}`.\n{message or raw[:500]}"),
                        ephemeral=True
                    )
                    return

                path = task["path"]
                state = task.get("state", "QUEUED")
                for _ in range(45):
                    if state in ("COMPLETE", "FAILED", "CANCELLED"):
                        break
                    await asyncio.sleep(2)
                    async with session.get(f"https://apis.roblox.com/cloud/v2/{path}") as resp:
                        if resp.status == 200:
                            task = await resp.json()
                            state = task.get("state", state)

                logs_text = ""
                if state in ("COMPLETE", "FAILED"):
                    async with session.get(f"https://apis.roblox.com/cloud/v2/{path}/logs", params={"maxPageSize": 200}) as resp:
                        if resp.status == 200:
                            messages = []
                            for entry in (await resp.json()).get("luauExecutionSessionTaskLogs", []):
                                messages.extend(entry.get("messages", []))
                            logs_text = "\n".join(str(m) for m in messages)

            output = task.get("output")
            results = output.get("results") if isinstance(output, dict) else output
            return_text = json.dumps(results, indent=2) if results not in (None, [], {}) else ""
            error = task.get("error")

            if state == "COMPLETE":
                title, color = "Luau Run Complete", COLOR_GREEN
            elif state == "FAILED":
                title, color = "Luau Run Failed", COLOR_RED
            elif state == "CANCELLED":
                title, color = "Luau Run Cancelled", COLOR_GREY
            else:
                title, color = "Luau Run Still Running", COLOR_GOLD

            parts = []
            if logs_text:
                parts.append("**Output**\n```\n" + logs_text.replace("```", "'''")[-1800:] + "\n```")
            if return_text:
                parts.append("**Return values**\n```json\n" + return_text.replace("```", "'''")[:1200] + "\n```")
            if error:
                parts.append("**Error**\n```\n" + json.dumps(error, indent=2).replace("```", "'''")[:800] + "\n```")
            if state not in ("COMPLETE", "FAILED", "CANCELLED"):
                parts.append("The script did not finish in 90 seconds. It may still be running on Roblox.")
            if not parts:
                parts.append("The script finished with no output.")

            embed = discord.Embed(title=title, description="\n\n".join(parts), color=color)
            embed.add_field(name="Task ID", value=f"`{path.split('/')[-1]}`", inline=True)
            embed.add_field(name="State", value=f"`{state}`", inline=True)
            embed.add_field(name="Script Size", value=f"`{len(code):,}` chars", inline=True)
            embed.add_field(name="Place", value=f"`{place_id}`", inline=True)
            await interaction.followup.send(embed=embed, ephemeral=True)

        except Exception as e:
            print(f"[Luau Run Error] {e}")
            await interaction.followup.send(embed=error_embed("Luau Run Error", f"Something went wrong: `{str(e)}`"), ephemeral=True)

    # =============================================================
    # SERVER TOOLS: /slowmode, /nick, /role, /say, /embed, /announce
    # =============================================================
    def hierarchy_error(self, interaction: discord.Interaction, target: discord.Member, allow_self: bool = False) -> Optional[str]:
        """Returns a message if the target cannot be moderated, otherwise None."""
        guild = interaction.guild
        invoker = interaction.user
        if target.id == invoker.id and not allow_self:
            return "You cannot do this to yourself."
        if target.id == guild.owner_id:
            return "You cannot moderate the server owner."
        if target.id == guild.me.id:
            return "I cannot do this to myself."
        if target.top_role >= guild.me.top_role:
            return "That member's highest role is equal to or above mine. Please move my role higher."
        if (
            invoker.id not in (guild.owner_id, BOT_OWNER_ID)
            and isinstance(invoker, discord.Member)
            and target.id != invoker.id
            and target.top_role >= invoker.top_role
        ):
            return "That member's highest role is equal to or above yours."
        return None

    @app_commands.command(name="slowmode", description="Set the slowmode of a channel.")
    @app_commands.describe(duration="Slowmode like 5s, 30s, 2m, 1h or off (max 6h).", channel="Channel (default: this channel).")
    @app_commands.guild_only()
    async def slowmode_cmd(self, interaction: discord.Interaction, duration: str, channel: Optional[discord.TextChannel] = None):
        target = channel or interaction.channel
        if not isinstance(target, discord.TextChannel):
            await interaction.response.send_message(
                embed=error_embed("Invalid Channel", "Slowmode can only be set on text channels."), ephemeral=True
            )
            return

        text = duration.strip().lower()
        seconds = 0 if text in ("0", "off", "none", "disable") else parse_duration_seconds(text)
        if seconds is None or seconds > 21600:
            await interaction.response.send_message(
                embed=error_embed("Invalid Duration", "Use `off`, or a time like `5s`, `30s`, `2m` or `1h`. The maximum is `6h`."),
                ephemeral=True
            )
            return

        try:
            await target.edit(slowmode_delay=seconds, reason=f"Slowmode set by {interaction.user} ({interaction.user.id})")
        except discord.Forbidden:
            await interaction.response.send_message(
                embed=error_embed("Missing Permissions", f"I need the **Manage Channels** permission in {target.mention}."), ephemeral=True
            )
            return

        await interaction.response.send_message(
            embed=discord.Embed(
                title="Slowmode Updated",
                description=f"{target.mention} slowmode is now `{str(seconds) + 's' if seconds else 'Off'}`.",
                color=COLOR_GREEN
            ),
            ephemeral=True
        )

    @app_commands.command(name="nick", description="Change or reset a member's nickname.")
    @app_commands.describe(user="The member.", nickname="New nickname. Leave empty to reset.")
    @app_commands.guild_only()
    async def nick_cmd(self, interaction: discord.Interaction, user: discord.Member, nickname: Optional[app_commands.Range[str, 1, 32]] = None):
        error = self.hierarchy_error(interaction, user, allow_self=True)
        if error:
            await interaction.response.send_message(embed=error_embed("Cannot Change Nickname", error), ephemeral=True)
            return

        try:
            await user.edit(nick=str(nickname) if nickname else None, reason=f"Nickname changed by {interaction.user} ({interaction.user.id})")
        except discord.Forbidden:
            await interaction.response.send_message(
                embed=error_embed("Missing Permissions", "I need the **Manage Nicknames** permission."), ephemeral=True
            )
            return

        description = f"{user.mention}'s nickname is now `{nickname}`." if nickname else f"{user.mention}'s nickname was reset."
        await interaction.response.send_message(
            embed=discord.Embed(title="Nickname Updated", description=description, color=COLOR_GREEN), ephemeral=True
        )

    role_group = app_commands.Group(name="role", description="Add or remove roles.", guild_only=True)

    def role_error(self, interaction: discord.Interaction, role: discord.Role) -> Optional[str]:
        guild = interaction.guild
        if role.is_default():
            return "You cannot use the @everyone role."
        if role.managed:
            return "That role is managed by an integration and cannot be given manually."
        if role >= guild.me.top_role:
            return "That role is equal to or above my highest role. Please move my role higher."
        invoker = interaction.user
        if (
            invoker.id not in (guild.owner_id, BOT_OWNER_ID)
            and isinstance(invoker, discord.Member)
            and role >= invoker.top_role
        ):
            return "That role is equal to or above your highest role."
        return None

    @role_group.command(name="add", description="Give a role to a member.")
    @app_commands.describe(user="The member.", role="The role to give.")
    async def role_add_cmd(self, interaction: discord.Interaction, user: discord.Member, role: discord.Role):
        error = self.role_error(interaction, role)
        if error:
            await interaction.response.send_message(embed=error_embed("Cannot Add Role", error), ephemeral=True)
            return
        if role in user.roles:
            await interaction.response.send_message(
                embed=error_embed("Already Has Role", f"{user.mention} already has {role.mention}."), ephemeral=True
            )
            return
        try:
            await user.add_roles(role, reason=f"Role added by {interaction.user} ({interaction.user.id})")
        except discord.Forbidden:
            await interaction.response.send_message(
                embed=error_embed("Missing Permissions", "I need the **Manage Roles** permission."), ephemeral=True
            )
            return
        await interaction.response.send_message(
            embed=discord.Embed(title="Role Added", description=f"Gave {role.mention} to {user.mention}.", color=COLOR_GREEN),
            ephemeral=True
        )

    @role_group.command(name="remove", description="Remove a role from a member.")
    @app_commands.describe(user="The member.", role="The role to remove.")
    async def role_remove_cmd(self, interaction: discord.Interaction, user: discord.Member, role: discord.Role):
        error = self.role_error(interaction, role)
        if error:
            await interaction.response.send_message(embed=error_embed("Cannot Remove Role", error), ephemeral=True)
            return
        if role not in user.roles:
            await interaction.response.send_message(
                embed=error_embed("Does Not Have Role", f"{user.mention} does not have {role.mention}."), ephemeral=True
            )
            return
        try:
            await user.remove_roles(role, reason=f"Role removed by {interaction.user} ({interaction.user.id})")
        except discord.Forbidden:
            await interaction.response.send_message(
                embed=error_embed("Missing Permissions", "I need the **Manage Roles** permission."), ephemeral=True
            )
            return
        await interaction.response.send_message(
            embed=discord.Embed(title="Role Removed", description=f"Removed {role.mention} from {user.mention}.", color=COLOR_GREEN),
            ephemeral=True
        )

    @app_commands.command(name="say", description="Send a message as the bot.")
    @app_commands.describe(message="The message to send.", channel="Channel (default: this channel).")
    @app_commands.guild_only()
    async def say_cmd(self, interaction: discord.Interaction, message: app_commands.Range[str, 1, 2000], channel: Optional[discord.TextChannel] = None):
        target = channel or interaction.channel
        if not isinstance(target, discord.TextChannel):
            await interaction.response.send_message(
                embed=error_embed("Invalid Channel", "Please choose a text channel."), ephemeral=True
            )
            return
        try:
            await target.send(
                str(message),
                allowed_mentions=discord.AllowedMentions(everyone=False, roles=False, users=True)
            )
        except discord.Forbidden:
            await interaction.response.send_message(
                embed=error_embed("Missing Permissions", f"I cannot send messages in {target.mention}."), ephemeral=True
            )
            return
        await interaction.response.send_message(
            embed=discord.Embed(title="Message Sent", description=f"Posted in {target.mention}.", color=COLOR_GREEN), ephemeral=True
        )

    @app_commands.command(name="embed", description="Create an embed and send it to a channel.")
    @app_commands.describe(channel="Channel (default: this channel).")
    @app_commands.guild_only()
    async def embed_cmd(self, interaction: discord.Interaction, channel: Optional[discord.TextChannel] = None):
        target = channel or interaction.channel
        if not isinstance(target, discord.TextChannel):
            await interaction.response.send_message(
                embed=error_embed("Invalid Channel", "Please choose a text channel."), ephemeral=True
            )
            return
        await interaction.response.send_modal(EmbedModal(target))

    @app_commands.command(name="announce", description="Post an announcement embed.")
    @app_commands.describe(
        channel="Channel (default: this channel).",
        ping_everyone="Ping @everyone.",
        ping_role="Ping a role."
    )
    @app_commands.guild_only()
    async def announce_cmd(
        self,
        interaction: discord.Interaction,
        channel: Optional[discord.TextChannel] = None,
        ping_everyone: bool = False,
        ping_role: Optional[discord.Role] = None
    ):
        target = channel or interaction.channel
        if not isinstance(target, discord.TextChannel):
            await interaction.response.send_message(
                embed=error_embed("Invalid Channel", "Please choose a text channel."), ephemeral=True
            )
            return
        await interaction.response.send_modal(AnnounceModal(target, ping_everyone, ping_role))

    # =============================================================
    # MODERATION (server administrators)
    # =============================================================
    async def add_modlog(self, guild_id: int, user_id: int, action: str, reason: str, moderator_id: int,
                         duration_text: Optional[str] = None) -> int:
        case = await self.db.count("modlogs", {"guild_id": guild_id}) + 1
        await self.db.insert("modlogs", {
            "_id": uuid.uuid4().hex,
            "guild_id": guild_id,
            "user_id": user_id,
            "action": action,
            "reason": reason,
            "moderator_id": moderator_id,
            "duration_text": duration_text,
            "ts": datetime.now(timezone.utc).timestamp(),
            "case": case,
        })
        return case

    async def dm_moderated(self, user: discord.abc.User, guild: discord.Guild, title: str, reason: Optional[str],
                           color: int, duration_text: Optional[str] = None) -> bool:
        """DMs the moderated user. The moderator is hidden for privacy."""
        embed = discord.Embed(title=title, description=f"Server: **{guild.name}**", color=color)
        if reason:
            embed.add_field(name="Reason", value=reason[:1000], inline=False)
        if duration_text:
            embed.add_field(name="Duration", value=f"`{duration_text}`", inline=True)
        embed.set_footer(text="Moderator details are hidden for privacy.")
        try:
            await user.send(embed=embed)
            return True
        except Exception:
            return False

    @staticmethod
    def dm_status(sent: bool) -> str:
        return "`Delivered`" if sent else "`Could not DM (DMs closed)`"

    def audit_reason(self, interaction: discord.Interaction, reason: str) -> str:
        return f"{reason} | by {interaction.user} ({interaction.user.id})"[:512]

    # ---- /warn ----
    @app_commands.command(name="warn", description="Warn a member.")
    @app_commands.describe(user="The member to warn.", reason="Why they are warned.")
    @app_commands.guild_only()
    async def warn_cmd(self, interaction: discord.Interaction, user: discord.Member, reason: app_commands.Range[str, 1, 400]):
        error = self.hierarchy_error(interaction, user)
        if error:
            await interaction.response.send_message(embed=error_embed("Cannot Warn", error), ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)

        case = await self.add_modlog(interaction.guild.id, user.id, "warn", str(reason), interaction.user.id)
        total = await self.db.count("modlogs", {"guild_id": interaction.guild.id, "user_id": user.id, "action": "warn"})
        sent = await self.dm_moderated(user, interaction.guild, "You have been warned", str(reason), COLOR_GOLD)

        embed = discord.Embed(title="Member Warned", description=f"{user.mention} was warned.", color=COLOR_GOLD)
        embed.add_field(name="Case", value=f"`#{case}`", inline=True)
        embed.add_field(name="Total Warnings", value=f"`{total}`", inline=True)
        embed.add_field(name="User DM", value=self.dm_status(sent), inline=True)
        embed.add_field(name="Reason", value=str(reason), inline=False)
        await interaction.followup.send(embed=embed, ephemeral=True)

    # ---- /warnings ----
    @app_commands.command(name="warnings", description="Show the warnings of a user.")
    @app_commands.describe(user="The user.")
    @app_commands.guild_only()
    async def warnings_cmd(self, interaction: discord.Interaction, user: discord.User):
        await self.show_page(interaction, "warns", interaction.guild.id, user.id, 0, edit=False, ephemeral=True)

    # ---- /modlog ----
    @app_commands.command(name="modlog", description="Show all moderation logs of a user.")
    @app_commands.describe(user="The user.")
    @app_commands.guild_only()
    async def modlog_cmd(self, interaction: discord.Interaction, user: discord.User):
        await self.show_page(interaction, "modlog", interaction.guild.id, user.id, 0, edit=False, ephemeral=True)

    # ---- /clear ----
    @app_commands.command(name="clear", description="Delete recent messages in this channel.")
    @app_commands.describe(amount="How many messages to delete (1-100).")
    @app_commands.guild_only()
    async def clear_cmd(self, interaction: discord.Interaction, amount: app_commands.Range[int, 1, 100]):
        channel = interaction.channel
        if not hasattr(channel, "purge"):
            await interaction.response.send_message(
                embed=error_embed("Invalid Channel", "I cannot delete messages in this channel."), ephemeral=True
            )
            return
        perms = channel.permissions_for(interaction.guild.me)
        if not (perms.manage_messages and perms.read_message_history):
            await interaction.response.send_message(
                embed=error_embed("Missing Permissions", "I need **Manage Messages** and **Read Message History** in this channel."),
                ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)
        try:
            deleted = await channel.purge(limit=int(amount), reason=self.audit_reason(interaction, "Clear command"))
        except Exception as e:
            print(f"[Clear Error] {e}")
            await interaction.followup.send(embed=error_embed("Clear Error", f"Something went wrong: `{str(e)}`"), ephemeral=True)
            return

        await interaction.followup.send(
            embed=discord.Embed(
                title="Messages Deleted",
                description=f"Deleted `{len(deleted)}` message(s). Messages older than 14 days cannot be deleted in bulk.",
                color=COLOR_GREEN
            ),
            ephemeral=True
        )

    # ---- /timeout ----
    @app_commands.command(name="timeout", description="Timeout a member.")
    @app_commands.describe(user="The member.", duration="Length like 10m, 1h, 1d or 1w (max 28d).", reason="Why they are timed out.")
    @app_commands.guild_only()
    async def timeout_cmd(self, interaction: discord.Interaction, user: discord.Member, duration: str, reason: app_commands.Range[str, 1, 400]):
        seconds = parse_duration_seconds(duration)
        if seconds is None or seconds < 1 or seconds > 28 * 86400:
            await interaction.response.send_message(
                embed=error_embed("Invalid Duration", "Use a time like `10m`, `1h`, `1d` or `1w`. The maximum is `28d`."), ephemeral=True
            )
            return
        error = self.hierarchy_error(interaction, user)
        if error:
            await interaction.response.send_message(embed=error_embed("Cannot Timeout", error), ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)

        try:
            await user.timeout(timedelta(seconds=seconds), reason=self.audit_reason(interaction, str(reason)))
        except discord.Forbidden:
            await interaction.followup.send(
                embed=error_embed("Cannot Timeout", "I could not timeout this member. I need the **Moderate Members** permission, and administrators cannot be timed out."),
                ephemeral=True
            )
            return

        duration_text = duration.strip()
        case = await self.add_modlog(interaction.guild.id, user.id, "timeout", str(reason), interaction.user.id, duration_text)
        sent = await self.dm_moderated(user, interaction.guild, "You have been timed out", str(reason), COLOR_RED, duration_text)

        until = int(datetime.now(timezone.utc).timestamp()) + seconds
        embed = discord.Embed(title="Member Timed Out", description=f"{user.mention} was timed out.", color=COLOR_RED)
        embed.add_field(name="Case", value=f"`#{case}`", inline=True)
        embed.add_field(name="Duration", value=f"`{duration_text}`", inline=True)
        embed.add_field(name="Ends", value=f"<t:{until}:R>", inline=True)
        embed.add_field(name="User DM", value=self.dm_status(sent), inline=True)
        embed.add_field(name="Reason", value=str(reason), inline=False)
        await interaction.followup.send(embed=embed, ephemeral=True)

    # ---- /untimeout ----
    @app_commands.command(name="untimeout", description="Remove a timeout from a member.")
    @app_commands.describe(user="The member.")
    @app_commands.guild_only()
    async def untimeout_cmd(self, interaction: discord.Interaction, user: discord.Member):
        if not user.is_timed_out():
            await interaction.response.send_message(
                embed=error_embed("Not Timed Out", f"{user.mention} is not timed out."), ephemeral=True
            )
            return
        await interaction.response.defer(ephemeral=True)

        try:
            await user.timeout(None, reason=self.audit_reason(interaction, "Timeout removed"))
        except discord.Forbidden:
            await interaction.followup.send(
                embed=error_embed("Cannot Remove Timeout", "I need the **Moderate Members** permission and a role above this member."),
                ephemeral=True
            )
            return

        case = await self.add_modlog(interaction.guild.id, user.id, "untimeout", "Timeout removed", interaction.user.id)
        sent = await self.dm_moderated(user, interaction.guild, "Your timeout was removed", None, COLOR_GREEN)

        embed = discord.Embed(title="Timeout Removed", description=f"{user.mention} can talk again.", color=COLOR_GREEN)
        embed.add_field(name="Case", value=f"`#{case}`", inline=True)
        embed.add_field(name="User DM", value=self.dm_status(sent), inline=True)
        await interaction.followup.send(embed=embed, ephemeral=True)

    # ---- /kick ----
    @app_commands.command(name="kick", description="Kick a member from the server.")
    @app_commands.describe(user="The member.", reason="Why they are kicked.")
    @app_commands.guild_only()
    async def kick_cmd(self, interaction: discord.Interaction, user: discord.Member, reason: app_commands.Range[str, 1, 400]):
        error = self.hierarchy_error(interaction, user)
        if error:
            await interaction.response.send_message(embed=error_embed("Cannot Kick", error), ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)

        # DM first, because the user cannot be reached after the kick
        sent = await self.dm_moderated(user, interaction.guild, "You have been kicked", str(reason), COLOR_RED)
        try:
            await user.kick(reason=self.audit_reason(interaction, str(reason)))
        except discord.Forbidden:
            await interaction.followup.send(
                embed=error_embed("Cannot Kick", "I need the **Kick Members** permission and a role above this member."), ephemeral=True
            )
            return

        case = await self.add_modlog(interaction.guild.id, user.id, "kick", str(reason), interaction.user.id)
        embed = discord.Embed(title="Member Kicked", description=f"`{user}` was kicked.", color=COLOR_RED)
        embed.add_field(name="Case", value=f"`#{case}`", inline=True)
        embed.add_field(name="User DM", value=self.dm_status(sent), inline=True)
        embed.add_field(name="Reason", value=str(reason), inline=False)
        await interaction.followup.send(embed=embed, ephemeral=True)

    # ---- /ban ----
    @app_commands.command(name="ban", description="Ban a user from the server.")
    @app_commands.describe(user="The user (also works for people who left).", reason="Why they are banned.")
    @app_commands.guild_only()
    async def ban_cmd(self, interaction: discord.Interaction, user: discord.User, reason: app_commands.Range[str, 1, 400]):
        guild = interaction.guild
        member = guild.get_member(user.id)
        if member is None:
            try:
                member = await guild.fetch_member(user.id)
            except discord.HTTPException:
                member = None

        if member is not None:
            error = self.hierarchy_error(interaction, member)
        elif user.id == interaction.user.id:
            error = "You cannot do this to yourself."
        elif user.id == guild.me.id:
            error = "I cannot do this to myself."
        else:
            error = None
        if error:
            await interaction.response.send_message(embed=error_embed("Cannot Ban", error), ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)

        try:
            await guild.fetch_ban(user)
            await interaction.followup.send(
                embed=error_embed("Already Banned", f"`{user}` is already banned."), ephemeral=True
            )
            return
        except discord.NotFound:
            pass
        except discord.HTTPException:
            pass

        # DM first, because the user cannot be reached after the ban
        sent = await self.dm_moderated(user, guild, "You have been banned", str(reason), COLOR_RED)
        try:
            await guild.ban(user, reason=self.audit_reason(interaction, str(reason)), delete_message_seconds=0)
        except discord.Forbidden:
            await interaction.followup.send(
                embed=error_embed("Cannot Ban", "I need the **Ban Members** permission and a role above this member."), ephemeral=True
            )
            return

        case = await self.add_modlog(guild.id, user.id, "ban", str(reason), interaction.user.id)
        embed = discord.Embed(title="User Banned", description=f"`{user}` (`{user.id}`) was banned.", color=COLOR_RED)
        embed.add_field(name="Case", value=f"`#{case}`", inline=True)
        embed.add_field(name="User DM", value=self.dm_status(sent), inline=True)
        embed.add_field(name="Reason", value=str(reason), inline=False)
        await interaction.followup.send(embed=embed, ephemeral=True)

    # ---- /unban ----
    @app_commands.command(name="unban", description="Unban a user.")
    @app_commands.describe(user="Start typing to pick a banned user, or paste a user ID.")
    @app_commands.guild_only()
    async def unban_cmd(self, interaction: discord.Interaction, user: str):
        digits = re.sub(r"\D", "", user)
        if not digits:
            await interaction.response.send_message(
                embed=error_embed("Invalid User", "Please pick a banned user from the list or paste a user ID."), ephemeral=True
            )
            return
        user_id = int(digits)
        await interaction.response.defer(ephemeral=True)

        try:
            await interaction.guild.unban(discord.Object(id=user_id), reason=self.audit_reason(interaction, "Unban command"))
        except discord.NotFound:
            await interaction.followup.send(
                embed=error_embed("Not Banned", f"User `{user_id}` is not banned in this server."), ephemeral=True
            )
            return
        except discord.Forbidden:
            await interaction.followup.send(
                embed=error_embed("Cannot Unban", "I need the **Ban Members** permission."), ephemeral=True
            )
            return

        case = await self.add_modlog(interaction.guild.id, user_id, "unban", "Unbanned", interaction.user.id)

        sent = False
        try:
            target = await self.bot.fetch_user(user_id)
            sent = await self.dm_moderated(target, interaction.guild, "You have been unbanned", None, COLOR_GREEN)
        except Exception:
            pass

        embed = discord.Embed(title="User Unbanned", description=f"User `{user_id}` was unbanned.", color=COLOR_GREEN)
        embed.add_field(name="Case", value=f"`#{case}`", inline=True)
        embed.add_field(name="User DM", value=self.dm_status(sent), inline=True)
        await interaction.followup.send(embed=embed, ephemeral=True)

    @unban_cmd.autocomplete("user")
    async def unban_user_autocomplete(self, interaction: discord.Interaction, current: str):
        if interaction.guild is None:
            return []
        current = current.lower()
        choices = []
        try:
            async for entry in interaction.guild.bans(limit=1000):
                label = f"{entry.user} ({entry.user.id})"
                if current in label.lower():
                    choices.append(app_commands.Choice(name=label[:100], value=str(entry.user.id)))
                if len(choices) >= 25:
                    break
        except Exception:
            return []
        return choices

    # ---- /lock and /unlock ----
    async def set_channel_lock(self, interaction: discord.Interaction, channel: Optional[discord.TextChannel], lock: bool):
        target = channel or interaction.channel
        if not isinstance(target, discord.TextChannel):
            await interaction.response.send_message(
                embed=error_embed("Invalid Channel", "Please choose a text channel."), ephemeral=True
            )
            return
        await interaction.response.defer(ephemeral=True)

        word = "locked" if lock else "unlocked"
        try:
            await target.set_permissions(
                interaction.guild.default_role,
                send_messages=False if lock else None,
                reason=self.audit_reason(interaction, f"Channel {word}")
            )
        except discord.Forbidden:
            await interaction.followup.send(
                embed=error_embed("Missing Permissions", f"I need the **Manage Channels** permission in {target.mention}."), ephemeral=True
            )
            return

        notice = discord.Embed(
            title=f"🔒 Channel Locked" if lock else "🔓 Channel Unlocked",
            description="Members can no longer send messages here." if lock else "Members can send messages here again.",
            color=COLOR_RED if lock else COLOR_GREEN
        )
        try:
            await target.send(embed=notice)
        except Exception:
            pass

        await interaction.followup.send(
            embed=discord.Embed(title=f"Channel {word.title()}", description=f"{target.mention} is now {word}.", color=COLOR_GREEN),
            ephemeral=True
        )

    @app_commands.command(name="lock", description="Stop @everyone from sending messages in a channel.")
    @app_commands.describe(channel="Channel (default: this channel).")
    @app_commands.guild_only()
    async def lock_cmd(self, interaction: discord.Interaction, channel: Optional[discord.TextChannel] = None):
        await self.set_channel_lock(interaction, channel, True)

    @app_commands.command(name="unlock", description="Let @everyone send messages in a channel again.")
    @app_commands.describe(channel="Channel (default: this channel).")
    @app_commands.guild_only()
    async def unlock_cmd(self, interaction: discord.Interaction, channel: Optional[discord.TextChannel] = None):
        await self.set_channel_lock(interaction, channel, False)


async def setup(bot: commands.Bot):
    await bot.add_cog(UtilityCog(bot))
