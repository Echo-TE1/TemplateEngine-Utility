import os
import re
import time
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
    match = re.match(r"^\s*(\d+)\s*([smhdw])\s*$", (text or "").lower())
    if not match:
        return None
    value, unit = int(match.group(1)), match.group(2)
    multiplier = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}[unit]
    return value * multiplier


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
            for child in self.children:
                child.disabled = True

    @discord.ui.button(label="Enter Giveaway", emoji="🎉", style=discord.ButtonStyle.primary, custom_id="utility_giveaway_enter")
    async def enter_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.handle_entry(interaction)


# -------------------------------------------------------------
# COG
# -------------------------------------------------------------
class UtilityCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.started_at = datetime.now(timezone.utc)
        self.store = GiveawayStore()

    async def cog_load(self):
        self.bot.add_view(GiveawayView(self))
        self.daily_revenue_log.start()
        self.giveaway_checker.start()

    async def cog_unload(self):
        self.daily_revenue_log.cancel()
        self.giveaway_checker.cancel()

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
        guild_only=True,
        default_permissions=discord.Permissions(manage_guild=True)
    )

    @staticmethod
    def can_manage(interaction: discord.Interaction) -> bool:
        if interaction.user.id == BOT_OWNER_ID:
            return True
        perms = interaction.permissions
        return bool(perms and (perms.manage_guild or perms.administrator))

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
        await self.store.update(doc["_id"], {"ended": True, "winner_ids": winner_ids})

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
                embed=error_embed("Permission Denied", "You need the **Manage Server** permission to use giveaway commands."),
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
                embed=error_embed("Permission Denied", "You need the **Manage Server** permission to use giveaway commands."),
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
                embed=error_embed("Permission Denied", "You need the **Manage Server** permission to use giveaway commands."),
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


async def setup(bot: commands.Bot):
    await bot.add_cog(UtilityCog(bot))
