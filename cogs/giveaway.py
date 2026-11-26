"""GiveawayCog: /giveaway start | edit | delete | list

A giveaway is one embed with a green Join button inside it. Entries, end time
and winners are kept in a private storage channel (blob_store.py), so running
giveaways survive redeploys and are ended on time after a restart.
"""

from __future__ import annotations

import logging
import random
import time
from typing import Any, Optional

import discord
from discord import app_commands
from discord.ext import commands, tasks

from blob_store import BlobStore
from error_log import report_error
from timeutil import parse_duration, parse_message_ref

log = logging.getLogger(__name__)

STORE_CHANNEL = "giveaway-data"
MAX_ACTIVE_PER_GUILD = 25
MIN_SECONDS = 10
MAX_SECONDS = 60 * 86400
KEEP_ENDED_SECONDS = 7 * 86400
FLUSH_SECONDS = 5
GREEN = discord.Colour(0x57F287)
RED = discord.Colour(0xED4245)

Record = dict[str, Any]


# ------------------------------------------------------------------ layout

PURPLE = discord.Colour(0x6C5CE7)
GREY = discord.Colour(0x95A5A6)
JOIN_ID = "giveaway:join"
ENTRIES_ID = "giveaway:entries"
PAGE_SIZE = 20


async def _noop(interaction: discord.Interaction) -> None:
    """Clicks are handled by GiveawayCog.on_interaction (works after restarts)."""


def host_message_of(rec: Record) -> str:
    return rec.get("host_message") or rec.get("description") or ""


def build_layout(rec: Record) -> discord.ui.LayoutView:
    """The giveaway message: one container (title, details, host message and
    the gift / Entries buttons), plus an optional ping line under it."""
    ended = bool(rec.get("ended"))
    ends = int(rec["ends"])
    entries = len(rec["entries"])

    container = discord.ui.Container(accent_colour=GREY if ended else PURPLE)
    container.add_item(discord.ui.TextDisplay(f"### {rec['prize']}"))
    if ended:
        winners = rec.get("winner_ids", [])
        label = "Winner" if rec["winners"] == 1 else "Winners"
        shown = ", ".join(f"<@{u}>" for u in winners) or "No valid entries"
        container.add_item(discord.ui.Separator())
        container.add_item(discord.ui.TextDisplay(f"**Hosted by:** <@{rec['host']}>\n**{label}:** {shown}"))
    else:
        container.add_item(discord.ui.TextDisplay(f"Ends <t:{ends}:R> \u2022 <t:{ends}:F>"))
        container.add_item(discord.ui.Separator())
        details = f"**Hosted by:** <@{rec['host']}>\n**Entries:** {entries}\n**Winners:** {rec['winners']}"
        if rec.get("role"):
            details += f"\n**Required role:** <@&{rec['role']}>"
        container.add_item(discord.ui.TextDisplay(details))
    host_message = host_message_of(rec)
    if host_message:
        container.add_item(discord.ui.Separator())
        container.add_item(discord.ui.TextDisplay(f"### Message from Host\n{host_message}"))

    gift = discord.ui.Button(style=discord.ButtonStyle.primary, emoji="\U0001F381",
                             custom_id="giveaway:ended-join" if ended else JOIN_ID, disabled=ended)
    count = discord.ui.Button(style=discord.ButtonStyle.secondary, label=f"Entries ({entries})",
                              custom_id="giveaway:ended-entries" if ended else ENTRIES_ID, disabled=ended)
    for button in (gift, count):
        button.callback = _noop  # type: ignore[assignment]
    row = discord.ui.ActionRow()
    row.add_item(gift)
    row.add_item(count)
    container.add_item(row)

    view = discord.ui.LayoutView(timeout=None)
    view.add_item(container)
    if rec.get("ping"):
        view.add_item(discord.ui.TextDisplay(rec["ping"]))
    return view


def status_embed(title: str, text: str, colour: discord.Colour) -> discord.Embed:
    return discord.Embed(title=title, description=text, colour=colour)


def winner_embed(rec: Record, members: list[discord.Member], jump_url: str) -> discord.Embed:
    blocks = []
    for index, member in enumerate(members):
        head = "**Winner**" if len(members) == 1 else f"**Winner {index + 1}**"
        blocks.append(f"{head}\n**Mention:** {member.mention}\n**Username:** {member.name}\n"
                      f"**User ID:** `{member.id}`")
    embed = discord.Embed(title=rec["prize"], colour=GREY,
                          description="\n\n".join(blocks) + f"\n\n[Jump to giveaway]({jump_url})")
    embed.set_thumbnail(url=members[0].display_avatar.url)
    return embed


class EntriesView(discord.ui.View):
    """Paged list of entrants, shown only to the person who pressed Entries."""

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
        return status_embed(
            "Giveaway Entries",
            f"**{self.rec['prize']}**\nTotal entries: `{len(entries)}`\n\n{names}\n\n**Page {self.page + 1}/{self.pages}**",
            PURPLE,
        )

    @discord.ui.button(emoji="\u25C0\uFE0F", style=discord.ButtonStyle.secondary)
    async def previous(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.page = max(0, self.page - 1)
        self._sync()
        await interaction.response.edit_message(embed=self.embed(), view=self)

    @discord.ui.button(emoji="\u25B6\uFE0F", style=discord.ButtonStyle.secondary)
    async def next(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.page = min(self.pages - 1, self.page + 1)
        self._sync()
        await interaction.response.edit_message(embed=self.embed(), view=self)


# --------------------------------------------------------------------- cog


class GiveawayCog(commands.Cog):
    giveaway = app_commands.Group(
        name="giveaway",
        description="Run giveaways",
        guild_only=True,
        default_permissions=discord.Permissions(manage_guild=True),
    )

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.store = BlobStore(bot, STORE_CHANNEL)
        self.records: dict[int, Record] = {}
        self._dirty: set[int] = set()
        self._loaded = False

    # ---------------------------------------------------------- lifecycle

    async def cog_unload(self) -> None:
        self.tick.cancel()

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        if not self._loaded:
            self._loaded = True
            for key, rec in (await self.store.load_all()).items():
                try:
                    self.records[int(key)] = rec
                except (ValueError, TypeError):
                    continue
        if not self.tick.is_running():
            self.tick.start()

    def mark_dirty(self, message_id: int) -> None:
        self._dirty.add(message_id)

    # --------------------------------------------------------------- loop

    @tasks.loop(seconds=FLUSH_SECONDS)
    async def tick(self) -> None:
        try:
            now = time.time()
            for message_id, rec in list(self.records.items()):
                if rec.get("ended"):
                    if now - rec.get("ended_at", now) > KEEP_ENDED_SECONDS:
                        self.records.pop(message_id, None)
                        await self.store.delete(str(message_id))
                    continue
                if rec["ends"] <= now:
                    await self._end(rec)
            for message_id in list(self._dirty):
                self._dirty.discard(message_id)
                rec = self.records.get(message_id)
                if rec and not rec.get("ended"):
                    await self._refresh_message(rec)
                    await self.store.save(str(message_id), rec)
        except Exception as exc:  # never let the loop die
            await report_error(self.bot, "giveaway loop", exc)

    @tick.before_loop
    async def _before_tick(self) -> None:
        await self.bot.wait_until_ready()

    # ------------------------------------------------------------ helpers

    async def _message(self, rec: Record) -> Optional[discord.PartialMessage]:
        channel = self.bot.get_channel(rec["channel"])
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(rec["channel"])
            except discord.HTTPException:
                return None
        return channel.get_partial_message(rec["id"]) if hasattr(channel, "get_partial_message") else None

    async def _drop(self, rec: Record) -> None:
        self.records.pop(rec["id"], None)
        self._dirty.discard(rec["id"])
        await self.store.delete(str(rec["id"]))

    async def _refresh_message(self, rec: Record) -> None:
        message = await self._message(rec)
        if message is None:
            return
        try:
            await message.edit(view=build_layout(rec), allowed_mentions=discord.AllowedMentions.none())
        except discord.NotFound:
            await self._drop(rec)  # the giveaway message was deleted by hand
        except discord.HTTPException as exc:
            log.warning("Could not update giveaway %s: %s", rec["id"], exc)

    async def _eligible(self, guild: Optional[discord.Guild], rec: Record, user_id: int) -> Optional[discord.Member]:
        """The member if they can still win (in the server, not a bot, has the role)."""
        if guild is None:
            return None
        member = guild.get_member(user_id)
        if member is None:
            try:
                member = await guild.fetch_member(user_id)
            except discord.HTTPException:
                return None
        if member.bot:
            return None
        if rec.get("role") and not any(r.id == rec["role"] for r in member.roles):
            return None
        return member

    async def _end(self, rec: Record) -> None:
        guild = self.bot.get_guild(rec["guild"])
        pool = list(rec["entries"])
        random.shuffle(pool)
        members: list[discord.Member] = []
        for user_id in pool:
            if len(members) >= rec["winners"]:
                break
            member = await self._eligible(guild, rec, user_id)
            if member is not None:
                members.append(member)
        winners = [m.id for m in members]
        rec.update(ended=True, ended_at=int(time.time()), winner_ids=winners, ends=min(rec["ends"], int(time.time())))
        self._dirty.discard(rec["id"])

        message = await self._message(rec)
        if message is not None:
            try:
                await message.edit(view=build_layout(rec), allowed_mentions=discord.AllowedMentions.none())
                if members:
                    mentions = ", ".join(m.mention for m in members)
                    verb = "is the winner!" if len(members) == 1 else "are the winners!"
                    jump = f"https://discord.com/channels/{rec['guild']}/{rec['channel']}/{rec['id']}"
                    await message.reply(
                        f"\U0001F381 {mentions} {verb}",
                        embed=winner_embed(rec, members, jump),
                        allowed_mentions=discord.AllowedMentions(users=True),
                    )
                else:
                    await message.reply(f"No valid entries for **{rec['prize']}**, so nobody won.")
            except discord.NotFound:
                await self._drop(rec)
                return
            except discord.HTTPException as exc:
                log.warning("Could not announce giveaway %s: %s", rec["id"], exc)
        await self.store.save(str(rec["id"]), rec)

    def _find(self, interaction: discord.Interaction, ref: str) -> Optional[Record]:
        message_id = parse_message_ref(ref)
        rec = self.records.get(message_id) if message_id else None
        if rec is None or rec["guild"] != (interaction.guild_id or 0):
            return None
        return rec

    # --------------------------------------------------------------- join

    @commands.Cog.listener()
    async def on_interaction(self, interaction: discord.Interaction) -> None:
        if interaction.type is not discord.InteractionType.component:
            return
        custom_id = (interaction.data or {}).get("custom_id")
        try:
            if custom_id == JOIN_ID:
                await self.handle_join(interaction)
            elif custom_id == ENTRIES_ID:
                await self.handle_entries(interaction)
        except Exception as exc:
            await report_error(self.bot, "giveaway button", exc, user=interaction.user, guild=interaction.guild)
            try:
                if not interaction.response.is_done():
                    await interaction.response.send_message("Something went wrong.", ephemeral=True)
            except discord.HTTPException:
                pass

    def _running(self, interaction: discord.Interaction) -> Optional[Record]:
        rec = self.records.get(interaction.message.id if interaction.message else 0)
        return rec if rec and not rec.get("ended") and rec["ends"] > time.time() else None

    async def handle_join(self, interaction: discord.Interaction) -> None:
        """The gift button: enter, or leave if already in."""
        rec = self._running(interaction)
        if rec is None:
            await interaction.response.send_message(
                embed=status_embed("Giveaway Ended", "This giveaway has already ended.", RED), ephemeral=True)
            return
        user = interaction.user
        if user.id not in rec["entries"] and rec.get("role") and not (
            isinstance(user, discord.Member) and any(r.id == rec["role"] for r in user.roles)
        ):
            await interaction.response.send_message(
                embed=status_embed("Can't Enter", f"You need the <@&{rec['role']}> role to enter **{rec['prize']}**.", RED),
                ephemeral=True)
            return
        if user.id in rec["entries"]:
            rec["entries"].remove(user.id)
            embed = status_embed(
                "You Left!",
                f"You left the giveaway for **{rec['prize']}**.\nTotal entries: `{len(rec['entries'])}`\n"
                "Click the gift button again to rejoin.", RED)
        else:
            rec["entries"].append(user.id)
            embed = status_embed(
                "You Are In!",
                f"You entered the giveaway for **{rec['prize']}**.\nTotal entries: `{len(rec['entries'])}`\n"
                "Click the gift button again to leave.", GREEN)
        self.mark_dirty(rec["id"])
        await interaction.response.send_message(embed=embed, ephemeral=True)

    async def handle_entries(self, interaction: discord.Interaction) -> None:
        rec = self._running(interaction)
        if rec is None:
            await interaction.response.send_message(
                embed=status_embed("Giveaway Ended", "This giveaway has already ended.", RED), ephemeral=True)
            return
        view = EntriesView(rec)
        await interaction.response.send_message(
            embed=view.embed(), view=view if view.pages > 1 else discord.utils.MISSING, ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none())

    # ----------------------------------------------------------- commands

    @giveaway.command(name="start", description="Start a giveaway.")
    @app_commands.describe(
        prize="What is being given away",
        duration="How long it runs, e.g. 30m, 2h, 1d12h (10s to 60d)",
        winners="Number of winners (default 1)",
        channel="Where to post it (default: this channel)",
        host_message="Shown under 'Message from Host'",
        required_role="Only members with this role can enter",
        ping="Ping this role (or @everyone) when the giveaway starts",
    )
    async def start(
        self,
        interaction: discord.Interaction,
        prize: app_commands.Range[str, 1, 200],
        duration: str,
        winners: app_commands.Range[int, 1, 20] = 1,
        channel: Optional[discord.TextChannel] = None,
        host_message: Optional[app_commands.Range[str, 1, 500]] = None,
        required_role: Optional[discord.Role] = None,
        ping: Optional[discord.Role] = None,
    ) -> None:
        seconds = parse_duration(duration)
        if seconds is None or not MIN_SECONDS <= seconds <= MAX_SECONDS:
            await interaction.response.send_message(
                "Duration must look like `30m`, `2h` or `1d12h`, between 10 seconds and 60 days.", ephemeral=True)
            return
        target = channel or interaction.channel
        if not isinstance(target, discord.TextChannel):
            await interaction.response.send_message("Pick a normal text channel.", ephemeral=True)
            return
        perms = target.permissions_for(interaction.guild.me)
        if not (perms.view_channel and perms.send_messages):
            await interaction.response.send_message(
                f"I need View Channel and Send Messages in {target.mention}.", ephemeral=True)
            return
        active = sum(1 for r in self.records.values() if r["guild"] == interaction.guild_id and not r.get("ended"))
        if active >= MAX_ACTIVE_PER_GUILD:
            await interaction.response.send_message(
                f"This server already has {MAX_ACTIVE_PER_GUILD} running giveaways.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        rec: Record = dict(
            id=0, guild=interaction.guild_id, channel=target.id, host=interaction.user.id, prize=prize,
            host_message=host_message or "", winners=winners,
            ping=("@everyone" if ping.is_default() else ping.mention) if ping else "", ends=int(time.time()) + seconds,
            entries=[], role=required_role.id if required_role else None, ended=False, winner_ids=[],
        )
        mentions = discord.AllowedMentions(everyone=True, roles=[ping]) if ping else discord.AllowedMentions.none()
        sent = await target.send(view=build_layout(rec), allowed_mentions=mentions)
        rec["id"] = sent.id
        self.records[sent.id] = rec
        await self.store.save(str(sent.id), rec)
        await interaction.followup.send(
            f"Giveaway started in {target.mention}. ID: `{sent.id}` ({sent.jump_url})", ephemeral=True)

    @giveaway.command(name="edit", description="Change a running giveaway.")
    @app_commands.describe(
        giveaway="The giveaway's message ID or message link",
        prize="New prize",
        duration="New time left from now, e.g. 2h",
        winners="New number of winners",
        host_message="New host message",
    )
    async def edit(
        self,
        interaction: discord.Interaction,
        giveaway: str,
        prize: Optional[app_commands.Range[str, 1, 200]] = None,
        duration: Optional[str] = None,
        winners: Optional[app_commands.Range[int, 1, 20]] = None,
        host_message: Optional[app_commands.Range[str, 1, 500]] = None,
    ) -> None:
        rec = self._find(interaction, giveaway)
        if rec is None:
            await interaction.response.send_message("I couldn't find a giveaway with that ID or link.", ephemeral=True)
            return
        if rec.get("ended"):
            await interaction.response.send_message("That giveaway has already ended.", ephemeral=True)
            return
        if prize is None and duration is None and winners is None and host_message is None:
            await interaction.response.send_message("Fill in at least one thing to change.", ephemeral=True)
            return
        if duration is not None:
            seconds = parse_duration(duration)
            if seconds is None or not MIN_SECONDS <= seconds <= MAX_SECONDS:
                await interaction.response.send_message(
                    "Duration must look like `30m`, `2h` or `1d12h`, between 10 seconds and 60 days.", ephemeral=True)
                return
            rec["ends"] = int(time.time()) + seconds
        if prize is not None:
            rec["prize"] = prize
        if winners is not None:
            rec["winners"] = winners
        if host_message is not None:
            rec["host_message"] = host_message
        await interaction.response.defer(ephemeral=True)
        await self._refresh_message(rec)
        await self.store.save(str(rec["id"]), rec)
        await interaction.followup.send(f"Updated giveaway `{rec['id']}`.", ephemeral=True)

    @giveaway.command(name="delete", description="Cancel a giveaway and delete its message.")
    @app_commands.describe(giveaway="The giveaway's message ID or message link")
    async def delete(self, interaction: discord.Interaction, giveaway: str) -> None:
        rec = self._find(interaction, giveaway)
        if rec is None:
            await interaction.response.send_message("I couldn't find a giveaway with that ID or link.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        message = await self._message(rec)
        if message is not None:
            try:
                await message.delete()
            except discord.HTTPException:
                pass
        await self._drop(rec)
        await interaction.followup.send(f"Deleted giveaway **{rec['prize']}**. No winners were picked.", ephemeral=True)

    @giveaway.command(name="list", description="Show this server's running giveaways.")
    async def list(self, interaction: discord.Interaction) -> None:
        mine = sorted(
            (r for r in self.records.values() if r["guild"] == interaction.guild_id and not r.get("ended")),
            key=lambda r: r["ends"],
        )
        if not mine:
            await interaction.response.send_message("There are no running giveaways.", ephemeral=True)
            return
        embed = discord.Embed(title="Running giveaways", colour=GREEN)
        for rec in mine[:25]:
            link = f"https://discord.com/channels/{rec['guild']}/{rec['channel']}/{rec['id']}"
            embed.add_field(
                name=rec["prize"][:100],
                value=f"[Jump to giveaway]({link})\nEnds <t:{int(rec['ends'])}:R> • {len(rec['entries'])} entries • "
                      f"{rec['winners']} winner(s)\nID: `{rec['id']}`",
                inline=False,
            )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @edit.autocomplete("giveaway")
    @delete.autocomplete("giveaway")
    async def giveaway_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        typed = current.lower()
        out = []
        for rec in self.records.values():
            if rec["guild"] == interaction.guild_id and not rec.get("ended") and (
                not typed or typed in rec["prize"].lower() or typed in str(rec["id"])
            ):
                out.append(app_commands.Choice(name=f"{rec['prize'][:70]} ({rec['id']})", value=str(rec["id"])))
        return out[:25]


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(GiveawayCog(bot))
