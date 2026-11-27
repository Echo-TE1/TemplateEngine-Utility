"""GiveawayCog: /giveaway create | edit | end | reroll | delete | list | status

Modelled on the Template Engine / Edith bot's giveaways: one purple card with a
gift button and an Entries (n) button inside it, join/leave embeds, a winner
card, luck roles, ping_everyone, a message from the host, end-now and reroll.

Entries, end time and winners are kept in a private storage channel
(blob_store.py), so running giveaways survive redeploys and are ended on time
after a restart.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from typing import Any, Optional

import discord
from discord import app_commands
from discord.ext import commands, tasks

from blob_store import BlobStore
from config import GIVEAWAY_LOG_CHANNEL_ID
from error_log import report_error
from logcard import send_log
from timeutil import parse_duration, parse_message_ref

log = logging.getLogger(__name__)

STORE_CHANNEL = "giveaway-data"
MAX_ACTIVE_PER_GUILD = 25
MIN_SECONDS = 1
MAX_SECONDS = 365 * 86400  # 12 months
KEEP_ENDED_SECONDS = 30 * 86400  # ended giveaways stay rerollable for 30 days
FLUSH_SECONDS = 5
PAGE_SIZE = 20

PURPLE = 0x8B5CF6
GREEN = 0x2ECC71
RED = 0xF43F5E
GOLD = 0xF1C40F
GRAY = 0x95A5A6
JOIN_ID = "giveaway_enter"
ENTRIES_ID = "giveaway_entries"
# ids used by the first version of the cards: those old messages must keep working
JOIN_IDS = {JOIN_ID, "giveaway:join"}
ENTRIES_IDS = {ENTRIES_ID, "giveaway:entries"}
RETRY_LOAD_SECONDS = 60
NO_PINGS = discord.AllowedMentions.none()

Record = dict[str, Any]


# ------------------------------------------------------------------ helpers


def embed(title: str, text: Optional[str] = None, colour: int = PURPLE) -> discord.Embed:
    return discord.Embed(title=title, description=text, colour=colour)


def ok(title: str, text: Optional[str] = None) -> discord.Embed:
    return embed(title, text, GREEN)


def err(title: str, text: Optional[str] = None) -> discord.Embed:
    return embed(title, text, RED)


def luck_text(rec: Record) -> str:
    luck = int(rec.get("luck") or 1)
    roles = rec.get("luck_roles") or []
    if luck <= 1 or not roles:
        return ""
    return f"**Luck:** {luck}x for " + ", ".join(f"<@&{r}>" for r in roles)


async def _noop(interaction: discord.Interaction) -> None:
    """Clicks are handled in GiveawayCog.on_interaction (works after restarts)."""


def build_layout(rec: Record) -> discord.ui.LayoutView:
    """The whole giveaway as one card: prize, end time, host, entries, winners
    and the buttons."""
    ended = bool(rec.get("ended"))
    ends = int(rec["ends"])
    host = f"<@{rec['host']}>"
    entries = len(rec["entries"])

    container = discord.ui.Container(accent_colour=discord.Colour(GRAY if ended else PURPLE))
    if rec.get("ping"):  # first line inside the card
        container.add_item(discord.ui.TextDisplay(rec["ping"]))
    container.add_item(discord.ui.TextDisplay(f"### {rec['prize']}"))
    if ended:
        winners = rec.get("winner_ids", [])
        label = "Winner" if len(winners) == 1 else "Winners"
        shown = ", ".join(f"<@{w}>" for w in winners) if winners else "No valid entries"
        container.add_item(discord.ui.Separator())
        container.add_item(discord.ui.TextDisplay(f"**Hosted by:** {host}\n**{label}:** {shown}"))
    else:
        container.add_item(discord.ui.TextDisplay(f"Ends <t:{ends}:R> • <t:{ends}:F>"))
        container.add_item(discord.ui.Separator())
        details = f"**Hosted by:** {host}\n**Entries:** {entries}\n**Winners:** {int(rec['winners'])}"
        if rec.get("role"):
            details += f"\n**Required role:** <@&{rec['role']}>"
        if luck_text(rec):
            details += f"\n{luck_text(rec)}"
        container.add_item(discord.ui.TextDisplay(details))
    if rec.get("host_message"):
        container.add_item(discord.ui.Separator())
        container.add_item(discord.ui.TextDisplay(f"**Message from Host**\n{rec['host_message']}"))

    gift = discord.ui.Button(style=discord.ButtonStyle.primary, emoji="\U0001F381",
                             custom_id=JOIN_ID if not ended else "giveaway_enter_ended", disabled=ended)
    count = discord.ui.Button(style=discord.ButtonStyle.secondary, label=f"Entries ({entries})",
                              custom_id=ENTRIES_ID if not ended else "giveaway_entries_ended", disabled=ended)
    for button in (gift, count):
        button.callback = _noop  # type: ignore[assignment]
    row = discord.ui.ActionRow()
    row.add_item(gift)
    row.add_item(count)
    container.add_item(row)

    view = discord.ui.LayoutView(timeout=None)
    view.add_item(container)
    return view


class EntriesView(discord.ui.View):
    """Paged entrant list, shown only to the person who pressed Entries."""

    def __init__(self, rec: Record) -> None:
        super().__init__(timeout=120)
        self.rec = rec
        self.page = 0
        self._sync()

    @property
    def pages(self) -> int:
        return max(1, -(-len(self.rec["entries"]) // PAGE_SIZE))

    def _sync(self) -> None:
        self.previous.disabled = self.page <= 0
        self.next.disabled = self.page >= self.pages - 1

    def embed(self) -> discord.Embed:
        entries = self.rec["entries"]
        chunk = entries[self.page * PAGE_SIZE:(self.page + 1) * PAGE_SIZE]
        names = "\n".join(f"<@{u}>" for u in chunk) or "No entries yet."
        return embed(
            "Giveaway Entries",
            f"**{self.rec['prize']}**\nTotal entries: `{len(entries)}`\n\n{names}\n\n**Page {self.page + 1}/{self.pages}**",
        )

    @discord.ui.button(emoji="◀️", style=discord.ButtonStyle.secondary)
    async def previous(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.page = max(0, self.page - 1)
        self._sync()
        await interaction.response.edit_message(embed=self.embed(), view=self)

    @discord.ui.button(emoji="▶️", style=discord.ButtonStyle.secondary)
    async def next(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.page = min(self.pages - 1, self.page + 1)
        self._sync()
        await interaction.response.edit_message(embed=self.embed(), view=self)


# --------------------------------------------------------------------- cog


class GiveawayCog(commands.Cog):
    giveaway = app_commands.Group(
        name="giveaway",
        description="Create and manage giveaways.",
        guild_only=True,
        default_permissions=discord.Permissions(administrator=True),
    )

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.store = BlobStore(bot, STORE_CHANNEL)
        self.records: dict[int, Record] = {}
        self._dirty: set[int] = set()
        self._loaded = False  # True once the stored giveaways were read successfully
        self._load_lock = asyncio.Lock()
        self._next_load_try = 0.0
        self.debug: dict[str, Any] = {"last_tick": 0.0, "stage": "not started", "last_error": None}

    # ---------------------------------------------------------- lifecycle

    async def cog_unload(self) -> None:
        """Runs when the bot shuts down for a redeploy: save entries that joined in the last few seconds."""
        self.tick.cancel()
        for message_id in list(self._dirty):
            rec = self.records.get(message_id)
            if rec is not None:
                try:
                    await asyncio.wait_for(self._save(rec), timeout=8)
                except Exception:
                    log.warning("Could not flush giveaway %s on shutdown", message_id)

    async def ensure_loaded(self) -> None:
        """Read the stored giveaways (retried until it works). Button clicks wait for this, so a click right
        after a redeploy is never answered with 'not found'."""
        if self._loaded:
            return
        async with self._load_lock:
            if self._loaded or time.time() < self._next_load_try:
                return
            stored = await self.store.load_all()
            for key, rec in stored.items():
                try:
                    self.records.setdefault(int(key), rec)  # never overwrite a newer in-memory change
                except (ValueError, TypeError):
                    continue
            if self.store.persistent:
                self._loaded = True
                log.info("Loaded %d stored giveaway(s)", len(self.records))
            else:
                self._next_load_try = time.time() + RETRY_LOAD_SECONDS

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        await self.ensure_loaded()
        if not self.tick.is_running():
            self.tick.start()

    def mark_dirty(self, message_id: int) -> None:
        self._dirty.add(message_id)

    async def _save(self, rec: Record) -> None:
        await self.store.save(str(rec["id"]), rec)

    # --------------------------------------------------------------- loop

    @tasks.loop(seconds=FLUSH_SECONDS)
    async def tick(self) -> None:
        try:
            await self.ensure_loaded()  # retries if the storage channel was not readable at startup
            now = time.time()
            self.debug["last_tick"] = now
            for message_id, rec in list(self.records.items()):
                if rec.get("ended"):
                    if now - rec.get("ended_at", now) > KEEP_ENDED_SECONDS:
                        await self._drop(rec)
                    continue
                if rec["ends"] <= now:
                    self.debug["stage"] = f"{message_id}: ending"
                    await self.finish(rec)
            self.debug["stage"] = "refreshing entry counts"
            for message_id in list(self._dirty):
                self._dirty.discard(message_id)
                rec = self.records.get(message_id)
                if rec and not rec.get("ended"):
                    await self._refresh_message(rec)
                    await self._save(rec)
            self.debug["stage"] = "idle"
        except Exception as exc:  # never let the loop die
            self.debug["last_error"] = repr(exc)
            await report_error(self.bot, "giveaway loop", exc)

    @tick.before_loop
    async def _before_tick(self) -> None:
        await self.bot.wait_until_ready()

    # ------------------------------------------------------------ helpers

    async def _channel(self, rec: Record):
        channel = self.bot.get_channel(rec["channel"])
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(rec["channel"])
            except discord.HTTPException:
                return None
        return channel

    async def _message(self, rec: Record) -> Optional[discord.Message]:
        channel = await self._channel(rec)
        if channel is None:
            return None
        try:
            return await channel.fetch_message(rec["id"])
        except discord.HTTPException:
            return None

    async def _drop(self, rec: Record) -> None:
        self.records.pop(rec["id"], None)
        self._dirty.discard(rec["id"])
        await self.store.delete(str(rec["id"]))

    async def _refresh_message(self, rec: Record, message: Optional[discord.Message] = None) -> bool:
        message = message or await self._message(rec)
        if message is None:
            return False
        try:
            await message.edit(view=build_layout(rec), allowed_mentions=NO_PINGS)
            return True
        except discord.HTTPException as exc:
            log.warning("Could not update giveaway %s: %s", rec["id"], exc)
            return False

    def _find(self, interaction: discord.Interaction, ref: str) -> Optional[Record]:
        message_id = parse_message_ref(ref)
        rec = self.records.get(message_id) if message_id else None
        if rec is None or rec["guild"] != (interaction.guild_id or 0):
            return None
        return rec

    async def _member(self, guild: Optional[discord.Guild], user_id: int) -> Optional[discord.Member]:
        if guild is None:
            return None
        member = guild.get_member(user_id)
        if member is not None:
            return member
        try:
            return await guild.fetch_member(user_id)
        except discord.HTTPException:
            return None

    async def _valid_entries(self, rec: Record, exclude: tuple[int, ...] = ()) -> list[discord.Member]:
        """Entrants still in the server who are not bots (and hold the required role)."""
        guild = self.bot.get_guild(rec["guild"])
        out = []
        for user_id in rec["entries"]:
            if user_id in exclude:
                continue
            member = await self._member(guild, user_id)
            if member is None or member.bot:
                continue
            if rec.get("role") and not any(r.id == rec["role"] for r in member.roles):
                continue
            out.append(member)
        return out

    @staticmethod
    def _pick(rec: Record, valid: list[discord.Member], count: int) -> list[discord.Member]:
        """Random winners; members with a luck role are `luck` times as likely."""
        count = min(count, len(valid))
        if count <= 0:
            return []
        luck = int(rec.get("luck") or 1)
        roles = {int(r) for r in rec.get("luck_roles") or []}
        if luck <= 1 or not roles:
            return random.sample(valid, count)
        keyed = []
        for member in valid:
            weight = luck if any(r.id in roles for r in member.roles) else 1
            keyed.append((random.random() ** (1.0 / weight), member))
        keyed.sort(key=lambda pair: pair[0], reverse=True)
        return [m for _, m in keyed[:count]]

    async def _announce(self, rec: Record, winners: list[discord.Member], message: Optional[discord.Message],
                        rerolled: bool = False) -> bool:
        channel = message.channel if message else await self._channel(rec)
        if channel is None:
            return False
        jump = f"https://discord.com/channels/{rec['guild']}/{rec['channel']}/{rec['id']}"
        if winners:
            mentions = ", ".join(m.mention for m in winners)
            new = "new " if rerolled else ""
            verb = f"is the {new}winner" if len(winners) == 1 else f"are the {new}winners"
            content = f"\U0001F381 {mentions} {verb}!"
            blocks = []
            for n, member in enumerate(winners, start=1):
                head = "Winner" if len(winners) == 1 else f"Winner {n}"
                blocks.append(f"**{head}**\n**Mention:** {member.mention}\n**Username:** {member.name}\n"
                              f"**User ID:** `{member.id}`")
            card = discord.Embed(title=rec["prize"], colour=GRAY,
                                 description="\n".join(blocks) + f"\n\n[Jump to giveaway]({jump})")
            if len(winners) == 1:
                card.set_thumbnail(url=winners[0].display_avatar.url)
        else:
            content = None
            card = err("No Winners", f"There were no valid entries for **{rec['prize']}**.\n\n[Jump to giveaway]({jump})")
        allowed = discord.AllowedMentions(users=True, roles=False, everyone=False, replied_user=False)
        if message is not None:
            try:
                await message.reply(content=content, embed=card, allowed_mentions=allowed)
                return True
            except discord.HTTPException as exc:
                log.warning("Reply failed for giveaway %s (%s); sending a normal message", rec["id"], exc)
        try:
            await channel.send(content=content, embed=card, allowed_mentions=allowed)
            return True
        except discord.HTTPException as exc:
            self.debug["last_error"] = f"Could not post in channel {rec['channel']}: {exc!r}"
            return False

    async def _tell_host(self, rec: Record, winners: list[discord.Member]) -> None:
        try:
            host = await self.bot.fetch_user(rec["host"])
            names = ", ".join(m.mention for m in winners) or "nobody"
            await host.send(embed=err(
                "Could Not Post Giveaway Winners",
                f"I could not post the winners for **{rec['prize']}** in <#{rec['channel']}>.\nWinners: {names}\n"
                "Check that I can View Channel, Send Messages and Embed Links there."))
        except discord.HTTPException:
            pass

    @staticmethod
    def _winner_lines(winners: list[discord.Member]) -> str:
        return "\n".join(f"{m.mention} \u2022 {m.name} \u2022 `{m.id}`" for m in winners) or "No valid entries"

    async def _log(self, title: str, colour: int, rec: Record, *, by: Optional[int] = None,
                   winners: Optional[list[discord.Member]] = None, extra: tuple = ()) -> None:
        """One card in the giveaway log channel."""
        link = f"https://discord.com/channels/{rec['guild']}/{rec['channel']}/{rec['id']}"
        items = [("Prize", rec["prize"]), ("Host", f"<@{rec['host']}>")]
        if by:
            items.append(("Action By", f"<@{by}>"))
        if winners is not None:
            items.append(("Winner" if len(winners) == 1 else "Winners", self._winner_lines(winners)))
        items += list(extra)
        items += [
            ("Entries", f"`{len(rec['entries'])}`"),
            ("Winners Allowed", f"`{rec['winners']}`"),
            ("Channel", f"<#{rec['channel']}>"),
            ("Ends" if not rec.get("ended") else "Ended", f"<t:{int(rec['ends'])}:F> (<t:{int(rec['ends'])}:R>)"),
            ("Giveaway", f"[Jump to giveaway]({link})\nMessage ID `{rec['id']}`"),
        ]
        await send_log(self.bot, GIVEAWAY_LOG_CHANNEL_ID, title, colour, items)

    async def finish(self, rec: Record, early: bool = False, by: Optional[int] = None) -> list[discord.Member]:
        """End a giveaway: pick winners, update the card, announce."""
        if rec.get("ended"):
            return []
        rec["ended"] = True  # set first so nothing else ends it twice
        self._dirty.discard(rec["id"])
        try:
            valid = await asyncio.wait_for(self._valid_entries(rec), timeout=30)
        except asyncio.TimeoutError:
            valid = []
        winners = self._pick(rec, valid, int(rec["winners"]))
        rec.update(winner_ids=[m.id for m in winners], ended_at=int(time.time()),
                   ends=int(time.time()) if early else rec["ends"])
        await self._save(rec)

        message = await self._message(rec)
        if message is not None:
            await self._refresh_message(rec, message)
        announced = await self._announce(rec, winners, message)
        if not announced:
            await self._tell_host(rec, winners)
        await self._log("Giveaway Ended Early" if early else "Giveaway Ended", PURPLE, rec, by=by, winners=winners)
        return winners

    # ------------------------------------------------------------ buttons

    @commands.Cog.listener()
    async def on_interaction(self, interaction: discord.Interaction) -> None:
        if interaction.type is not discord.InteractionType.component:
            return
        custom_id = (interaction.data or {}).get("custom_id")
        if custom_id not in JOIN_IDS | ENTRIES_IDS:
            return
        try:
            await self.ensure_loaded()
            if custom_id in JOIN_IDS:
                await self.handle_join(interaction)
            else:
                await self.handle_entries(interaction)
        except Exception as exc:
            await report_error(self.bot, "giveaway button", exc, user=interaction.user, guild=interaction.guild)
            try:
                if not interaction.response.is_done():
                    await interaction.response.send_message(embed=err("Something Went Wrong"), ephemeral=True)
            except discord.HTTPException:
                pass

    def _running(self, interaction: discord.Interaction) -> tuple[Optional[Record], Optional[discord.Embed]]:
        rec = self.records.get(interaction.message.id if interaction.message else 0)
        if rec is None:
            return None, err("Giveaway Not Found", "This giveaway is no longer tracked.")
        if rec.get("ended") or rec["ends"] <= time.time():
            return None, err("Giveaway Ended", "This giveaway has already ended.")
        return rec, None

    async def handle_join(self, interaction: discord.Interaction) -> None:
        """The gift button: enter, or leave if already in."""
        rec, problem = self._running(interaction)
        if rec is None:
            await interaction.response.send_message(embed=problem, ephemeral=True)
            return
        user = interaction.user
        if user.id in rec["entries"]:
            rec["entries"].remove(user.id)
            card = embed("Entry Removed",
                         f"You left the giveaway for **{rec['prize']}**.\nClick the gift button again to rejoin.", GOLD)
        else:
            if rec.get("role") and not (
                isinstance(user, discord.Member) and any(r.id == rec["role"] for r in user.roles)
            ):
                await interaction.response.send_message(
                    embed=err("Can't Enter", f"You need the <@&{rec['role']}> role to enter **{rec['prize']}**."),
                    ephemeral=True)
                return
            rec["entries"].append(user.id)
            card = ok("You Are In!",
                      f"You entered the giveaway for **{rec['prize']}**.\nTotal entries: `{len(rec['entries'])}`\n"
                      "Click the gift button again to leave.")
        self.mark_dirty(rec["id"])  # the card's counts are refreshed within a few seconds
        await interaction.response.send_message(embed=card, ephemeral=True)

    async def handle_entries(self, interaction: discord.Interaction) -> None:
        rec, problem = self._running(interaction)
        if rec is None:
            await interaction.response.send_message(embed=problem, ephemeral=True)
            return
        view = EntriesView(rec)
        await interaction.response.send_message(
            embed=view.embed(), view=view if view.pages > 1 else discord.utils.MISSING, ephemeral=True,
            allowed_mentions=NO_PINGS)

    # ----------------------------------------------------------- commands

    async def _not_found(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_message(
            embed=err("Giveaway Not Found", "No giveaway in this server matches that message ID or link."),
            ephemeral=True)

    async def _choices(self, interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
        out = []
        for rec in self.records.values():
            if rec["guild"] != interaction.guild_id:
                continue
            state = "ended" if rec.get("ended") else "active"
            label = f"{rec['prize'][:55]} ({state}) {rec['id']}"
            if current.lower() in label.lower():
                out.append(app_commands.Choice(name=label[:100], value=str(rec["id"])))
        return out[:25]

    @giveaway.command(name="create", description="Start a giveaway.")
    @app_commands.describe(
        prize="What is being given away.",
        duration="How long it runs, from 1s to 12 months, e.g. 45s, 30m, 2h, 1d12h, 2w, 6mo.",
        winners="Number of winners (default 1).",
        channel="Where to post it (defaults to this channel).",
        luck="Optional. How much more likely luck-role members are to win.",
        luck_role="Role that gets the luck boost (needs luck).",
        luck_role_2="Another role that gets the luck boost.",
        luck_role_3="Another role that gets the luck boost.",
        ping_everyone="True pings @everyone when the giveaway starts (default False).",
        host_message="A message from the host, shown on the giveaway.",
        required_role="Only members with this role can enter.",
    )
    @app_commands.choices(luck=[
        app_commands.Choice(name="2x luck", value=2), app_commands.Choice(name="3x luck", value=3),
        app_commands.Choice(name="5x luck", value=5), app_commands.Choice(name="10x luck", value=10),
    ])
    async def create(
        self,
        interaction: discord.Interaction,
        prize: app_commands.Range[str, 1, 200],
        duration: str,
        winners: app_commands.Range[int, 1, 25] = 1,
        channel: Optional[discord.TextChannel] = None,
        luck: Optional[app_commands.Choice[int]] = None,
        luck_role: Optional[discord.Role] = None,
        luck_role_2: Optional[discord.Role] = None,
        luck_role_3: Optional[discord.Role] = None,
        ping_everyone: bool = False,
        host_message: Optional[app_commands.Range[str, 1, 500]] = None,
        required_role: Optional[discord.Role] = None,
    ) -> None:
        luck_roles = list(dict.fromkeys(r.id for r in (luck_role, luck_role_2, luck_role_3) if r is not None))
        if bool(luck) != bool(luck_roles):
            await interaction.response.send_message(
                embed=err("Luck Needs Both Options",
                          "Choose the `luck` amount and at least one `luck_role`, or leave both empty."),
                ephemeral=True)
            return
        seconds = parse_duration(duration)
        if seconds is None or not MIN_SECONDS <= seconds <= MAX_SECONDS:
            await interaction.response.send_message(
                embed=err("Invalid Duration", "Use a duration from `1s` to `12mo`, for example `45s`, `30m`, `2h`, `1d12h`, `2w` or `6mo` (a month is 30 days)."),
                ephemeral=True)
            return
        target = channel or interaction.channel
        if not isinstance(target, discord.TextChannel):
            await interaction.response.send_message(
                embed=err("Wrong Channel", "Giveaways can only be posted in text channels."), ephemeral=True)
            return
        perms = target.permissions_for(interaction.guild.me)
        if not (perms.view_channel and perms.send_messages):
            await interaction.response.send_message(
                embed=err("Missing Permission", f"I need `View Channel` and `Send Messages` in {target.mention}."),
                ephemeral=True)
            return
        if ping_everyone and not perms.mention_everyone:
            await interaction.response.send_message(
                embed=err("Missing Permission",
                          f"I need `Mention Everyone` in {target.mention} to ping @everyone. Give me the "
                          "permission or set `ping_everyone` to False."),
                ephemeral=True)
            return
        active = sum(1 for r in self.records.values() if r["guild"] == interaction.guild_id and not r.get("ended"))
        if active >= MAX_ACTIVE_PER_GUILD:
            await interaction.response.send_message(
                embed=err("Too Many Giveaways", f"This server already has {MAX_ACTIVE_PER_GUILD} running giveaways."),
                ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        rec: Record = dict(
            id=0, guild=interaction.guild_id, channel=target.id, host=interaction.user.id, prize=prize,
            winners=int(winners), ends=int(time.time()) + seconds, entries=[], ended=False, winner_ids=[],
            past_winners=[], host_message=host_message or "", ping="@everyone" if ping_everyone else "", role=required_role.id if required_role else None,
            luck=int(luck.value) if luck else 1, luck_roles=luck_roles,
        )
        try:
            # the @everyone line is part of this one message, so it pings when the giveaway is posted
            allowed = discord.AllowedMentions(everyone=True) if ping_everyone else NO_PINGS
            sent = await target.send(view=build_layout(rec), allowed_mentions=allowed)
        except discord.HTTPException as exc:
            await interaction.followup.send(embed=err("Could Not Post Giveaway", f"`{exc}`"), ephemeral=True)
            return
        rec["id"] = sent.id
        self.records[sent.id] = rec
        await self._save(rec)

        details = []
        if luck_text(rec):
            details.append(("Luck Boost", luck_text(rec).replace("**Luck:** ", "")))
        if required_role:
            details.append(("Required Role", required_role.mention))
        if host_message:
            details.append(("Message From Host", host_message))
        details.append(("Ping", "`@everyone`" if ping_everyone else "`No ping`"))
        await self._log("Giveaway Started", GREEN, rec, by=interaction.user.id, extra=tuple(details))

        card = ok("Giveaway Created", f"Posted in {target.mention}.\n[Jump to giveaway]({sent.jump_url})")
        if luck_text(rec):
            card.add_field(name="Luck Boost", value=luck_text(rec).replace("**Luck:** ", ""), inline=False)
        if host_message:
            card.add_field(name="Message From Host", value=host_message, inline=False)
        card.add_field(name="Ping", value="`@everyone`" if ping_everyone else "`No ping`", inline=True)
        card.add_field(name="Message ID", value=f"`{sent.id}`", inline=True)
        card.add_field(name="Ends", value=f"<t:{rec['ends']}:R>", inline=True)
        await interaction.followup.send(embed=card, ephemeral=True)

    @giveaway.command(name="edit", description="Change the prize, winners or time left of a running giveaway.")
    @app_commands.describe(
        message_id="The giveaway to edit (message ID or link).",
        prize="New prize.",
        winners="New number of winners.",
        duration="New time left from now (1s to 12 months), e.g. 1h, 2d, 3mo.",
        host_message="New message from the host.",
    )
    async def edit(
        self,
        interaction: discord.Interaction,
        message_id: str,
        prize: Optional[app_commands.Range[str, 1, 200]] = None,
        winners: Optional[app_commands.Range[int, 1, 25]] = None,
        duration: Optional[str] = None,
        host_message: Optional[app_commands.Range[str, 1, 500]] = None,
    ) -> None:
        rec = self._find(interaction, message_id)
        if rec is None:
            await self._not_found(interaction)
            return
        if rec.get("ended"):
            await interaction.response.send_message(
                embed=err("Giveaway Ended", "Ended giveaways cannot be edited. Use `/giveaway reroll` instead."),
                ephemeral=True)
            return
        if prize is None and winners is None and duration is None and host_message is None:
            await interaction.response.send_message(
                embed=err("Nothing To Change", "Give at least one option to change."), ephemeral=True)
            return
        changes = []
        if duration is not None:
            seconds = parse_duration(duration)
            if seconds is None or not MIN_SECONDS <= seconds <= MAX_SECONDS:
                await interaction.response.send_message(
                    embed=err("Invalid Duration", "Use a duration from `1s` to `12mo`, for example `45s`, `2h`, `1d12h` or `6mo`."), ephemeral=True)
                return
            changes.append(f"Ends: <t:{rec['ends']}:R> \u2192 <t:{int(time.time()) + seconds}:R>")
            rec["ends"] = int(time.time()) + seconds
        if prize is not None:
            changes.append(f"Prize: {rec['prize']} \u2192 {prize}")
            rec["prize"] = prize
        if winners is not None:
            changes.append(f"Winners: {rec['winners']} \u2192 {winners}")
            rec["winners"] = int(winners)
        if host_message is not None:
            changes.append("Message from host changed")
            rec["host_message"] = host_message
        await interaction.response.defer(ephemeral=True)
        await self._save(rec)
        await self._log("Giveaway Edited", GOLD, rec, by=interaction.user.id,
                        extra=(("Changes", "\n".join(changes)),))
        if not await self._refresh_message(rec):
            await interaction.followup.send(embed=err("Saved, But Could Not Update Message"), ephemeral=True)
            return
        card = ok("Giveaway Updated", f"**{rec['prize']}**")
        card.add_field(name="Winners", value=f"`{rec['winners']}`", inline=True)
        card.add_field(name="Ends", value=f"<t:{rec['ends']}:R>", inline=True)
        await interaction.followup.send(embed=card, ephemeral=True)

    @giveaway.command(name="end", description="End a giveaway now and pick the winners.")
    @app_commands.describe(message_id="The giveaway to end (message ID or link).")
    async def end(self, interaction: discord.Interaction, message_id: str) -> None:
        rec = self._find(interaction, message_id)
        if rec is None:
            await self._not_found(interaction)
            return
        if rec.get("ended"):
            await interaction.response.send_message(
                embed=err("Already Ended", "That giveaway has already ended."), ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        winners = await self.finish(rec, early=True, by=interaction.user.id)
        text = ", ".join(m.mention for m in winners) or "No valid entries"
        await interaction.followup.send(
            embed=ok("Giveaway Ended", f"**{rec['prize']}**\nWinners: {text}"), ephemeral=True, allowed_mentions=NO_PINGS)

    @giveaway.command(name="reroll", description="Pick new winner(s) for an ended giveaway.")
    @app_commands.describe(message_id="The ended giveaway (message ID or link).",
                           winners="How many new winners (defaults to the original number).")
    async def reroll(
        self, interaction: discord.Interaction, message_id: str,
        winners: Optional[app_commands.Range[int, 1, 25]] = None,
    ) -> None:
        rec = self._find(interaction, message_id)
        if rec is None:
            await self._not_found(interaction)
            return
        if not rec.get("ended"):
            await interaction.response.send_message(
                embed=err("Still Running", "That giveaway has not ended yet. Use `/giveaway end` first."),
                ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        # Anyone who already won can't be picked again.
        past = list(dict.fromkeys(list(rec.get("past_winners", [])) + list(rec.get("winner_ids", []))))
        valid = await self._valid_entries(rec, exclude=tuple(past))
        picked = self._pick(rec, valid, int(winners or rec["winners"]))
        if not picked:
            await interaction.followup.send(
                embed=err("Nobody Left To Pick", "Everyone who entered has already won, or left the server."),
                ephemeral=True)
            return
        rec["past_winners"] = past
        rec["winner_ids"] = [m.id for m in picked]
        await self._save(rec)
        message = await self._message(rec)
        if message is not None:
            await self._refresh_message(rec, message)
        await self._announce(rec, picked, message, rerolled=True)
        await self._log("Giveaway Rerolled", PURPLE, rec, by=interaction.user.id, winners=picked)
        await interaction.followup.send(
            embed=ok("Giveaway Rerolled", f"**{rec['prize']}**\nNew winner(s): " + ", ".join(m.mention for m in picked)),
            ephemeral=True, allowed_mentions=NO_PINGS)

    @giveaway.command(name="delete", description="Delete a giveaway and its message.")
    @app_commands.describe(message_id="The giveaway to delete (message ID or link).")
    async def delete(self, interaction: discord.Interaction, message_id: str) -> None:
        rec = self._find(interaction, message_id)
        if rec is None:
            await self._not_found(interaction)
            return
        await interaction.response.defer(ephemeral=True)
        message = await self._message(rec)
        if message is not None:
            try:
                await message.delete()
            except discord.HTTPException:
                pass
        await self._drop(rec)
        await self._log("Giveaway Deleted", RED, rec, by=interaction.user.id,
                        extra=(("State", "Ended" if rec.get("ended") else "Was still running"),))
        await interaction.followup.send(
            embed=ok("Giveaway Deleted", f"**{rec['prize']}** was removed. No winners were picked."), ephemeral=True)

    @giveaway.command(name="list", description="Show this server's running giveaways.")
    async def list(self, interaction: discord.Interaction) -> None:
        mine = sorted(
            (r for r in self.records.values() if r["guild"] == interaction.guild_id and not r.get("ended")),
            key=lambda r: r["ends"],
        )
        if not mine:
            await interaction.response.send_message(embed=embed("Running Giveaways", "There are no running giveaways."),
                                                    ephemeral=True)
            return
        card = embed("Running Giveaways")
        for rec in mine[:25]:
            link = f"https://discord.com/channels/{rec['guild']}/{rec['channel']}/{rec['id']}"
            card.add_field(
                name=rec["prize"][:100],
                value=f"[Jump to giveaway]({link})\nEnds <t:{int(rec['ends'])}:R> • {len(rec['entries'])} entries "
                      f"• {rec['winners']} winner(s)\nID: `{rec['id']}`",
                inline=False)
        await interaction.response.send_message(embed=card, ephemeral=True)

    @giveaway.command(name="status", description="Check why a giveaway is not ending (tracking, timer, errors).")
    @app_commands.describe(message_id="The giveaway message ID or link.")
    async def status(self, interaction: discord.Interaction, message_id: str) -> None:
        rec = self._find(interaction, message_id)
        card = embed("Giveaway Status")
        card.add_field(name="Giveaways Tracked", value=f"`{len(self.records)}`", inline=True)
        age = time.time() - self.debug["last_tick"] if self.debug["last_tick"] else None
        card.add_field(name="Timer Last Ran", value=f"`{int(age)}s ago`" if age is not None else "`never`", inline=True)
        card.add_field(name="Storage", value="`saved in Discord`" if self.store.persistent else "`memory only`", inline=True)
        if rec is None:
            card.add_field(
                name="Problem",
                value="This giveaway is not tracked. The bot forgot it (no storage channel?). Use `/giveaway delete` "
                      "and create a new one.", inline=False)
        else:
            card.add_field(name="Prize", value=rec["prize"], inline=False)
            card.add_field(name="State", value="`ended`" if rec.get("ended") else "`running`", inline=True)
            card.add_field(name="Ends", value=f"<t:{int(rec['ends'])}:R>", inline=True)
            card.add_field(name="Entries", value=f"`{len(rec['entries'])}`", inline=True)
        card.add_field(name="Timer Step", value=f"`{self.debug['stage']}`", inline=False)
        card.add_field(name="Last Error", value=f"```{self.debug['last_error'] or 'None'}```"[:1000], inline=False)
        await interaction.response.send_message(embed=card, ephemeral=True)

    @edit.autocomplete("message_id")
    @end.autocomplete("message_id")
    @reroll.autocomplete("message_id")
    @delete.autocomplete("message_id")
    @status.autocomplete("message_id")
    async def message_id_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        return await self._choices(interaction, current)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(GiveawayCog(bot))
