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
GREY = discord.Colour(0x4F545C)

Record = dict[str, Any]


# ------------------------------------------------------------------ embeds


def build_embed(rec: Record) -> discord.Embed:
    ends = int(rec["ends"])
    lines: list[str] = []
    if rec.get("ended"):
        if rec.get("description"):
            lines += [rec["description"], ""]
        winners = ", ".join(f"<@{u}>" for u in rec.get("winner_ids", [])) or "No valid entries"
        lines += [
            f"**Ended:** <t:{ends}:R>",
            f"**Hosted by:** <@{rec['host']}>",
            f"**Entries:** {len(rec['entries'])}",
            f"**Winners:** {winners}",
        ]
        colour = GREY
    else:
        lines = ([rec["description"]] if rec.get("description") else []) + ["Click the button below to enter!", ""]
        lines += [
            f"**Ends:** <t:{ends}:R> (<t:{ends}:f>)",
            f"**Hosted by:** <@{rec['host']}>",
            f"**Entries:** {len(rec['entries'])}",
            f"**Winners:** {rec['winners']}",
        ]
        if rec.get("role"):
            lines.append(f"**Required role:** <@&{rec['role']}>")
        colour = GREEN
    embed = discord.Embed(title=f"🎉 {rec['prize']}", description="\n".join(lines), colour=colour)
    embed.set_footer(text=f"Giveaway ID: {rec['id']}" + (" • Ended" if rec.get("ended") else ""))
    return embed


class JoinView(discord.ui.View):
    """Persistent: one instance handles the Join button of every giveaway."""

    def __init__(self, cog: "GiveawayCog", ended: bool = False) -> None:
        super().__init__(timeout=None)
        self.cog = cog
        if ended:
            self.clear_items()
            self.add_item(discord.ui.Button(label="Giveaway ended", style=discord.ButtonStyle.secondary,
                                            disabled=True))

    @discord.ui.button(label="Join", emoji="🎉", style=discord.ButtonStyle.success, custom_id="giveaway:join")
    async def join(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self.cog.handle_join(interaction)


class LeaveView(discord.ui.View):
    def __init__(self, cog: "GiveawayCog", message_id: int) -> None:
        super().__init__(timeout=60)
        self.cog, self.message_id = cog, message_id

    @discord.ui.button(label="Leave giveaway", style=discord.ButtonStyle.danger)
    async def leave(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        rec = self.cog.records.get(self.message_id)
        if rec and not rec.get("ended") and interaction.user.id in rec["entries"]:
            rec["entries"].remove(interaction.user.id)
            self.cog.mark_dirty(self.message_id)
        await interaction.response.edit_message(content="You left the giveaway.", view=None)


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

    async def cog_load(self) -> None:
        self.bot.add_view(JoinView(self))

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
            await message.edit(embed=build_embed(rec))
        except discord.NotFound:
            await self._drop(rec)  # the giveaway message was deleted by hand
        except discord.HTTPException as exc:
            log.warning("Could not update giveaway %s: %s", rec["id"], exc)

    async def _eligible(self, guild: Optional[discord.Guild], rec: Record, user_id: int) -> bool:
        if guild is None:
            return False
        member = guild.get_member(user_id)
        if member is None:
            try:
                member = await guild.fetch_member(user_id)
            except discord.HTTPException:
                return False
        if member.bot:
            return False
        return not rec.get("role") or any(r.id == rec["role"] for r in member.roles)

    async def _end(self, rec: Record) -> None:
        guild = self.bot.get_guild(rec["guild"])
        pool = list(rec["entries"])
        random.shuffle(pool)
        winners: list[int] = []
        for user_id in pool:
            if len(winners) >= rec["winners"]:
                break
            if await self._eligible(guild, rec, user_id):
                winners.append(user_id)
        rec.update(ended=True, ended_at=int(time.time()), winner_ids=winners, ends=min(rec["ends"], int(time.time())))
        self._dirty.discard(rec["id"])

        message = await self._message(rec)
        if message is not None:
            try:
                await message.edit(embed=build_embed(rec), view=JoinView(self, ended=True))
                if winners:
                    mentions = ", ".join(f"<@{u}>" for u in winners)
                    await message.reply(
                        f"🎉 Congratulations {mentions}! You won **{rec['prize']}**!",
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

    async def handle_join(self, interaction: discord.Interaction) -> None:
        message_id = interaction.message.id if interaction.message else 0
        rec = self.records.get(message_id)
        if rec is None or rec.get("ended") or rec["ends"] <= time.time():
            await interaction.response.send_message("This giveaway has ended.", ephemeral=True)
            return
        user = interaction.user
        if rec.get("role") and not (
            isinstance(user, discord.Member) and any(r.id == rec["role"] for r in user.roles)
        ):
            await interaction.response.send_message(
                f"You need the <@&{rec['role']}> role to enter.", ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none())
            return
        if user.id in rec["entries"]:
            await interaction.response.send_message(
                "You're already in this giveaway.", ephemeral=True, view=LeaveView(self, message_id))
            return
        rec["entries"].append(user.id)
        self.mark_dirty(message_id)
        await interaction.response.send_message("You're in! Good luck 🎉", ephemeral=True)

    # ----------------------------------------------------------- commands

    @giveaway.command(name="start", description="Start a giveaway.")
    @app_commands.describe(
        prize="What is being given away",
        duration="How long it runs, e.g. 30m, 2h, 1d12h (10s to 60d)",
        winners="Number of winners (default 1)",
        channel="Where to post it (default: this channel)",
        description="Extra text shown in the embed",
        required_role="Only members with this role can enter",
    )
    async def start(
        self,
        interaction: discord.Interaction,
        prize: app_commands.Range[str, 1, 200],
        duration: str,
        winners: app_commands.Range[int, 1, 20] = 1,
        channel: Optional[discord.TextChannel] = None,
        description: Optional[app_commands.Range[str, 1, 500]] = None,
        required_role: Optional[discord.Role] = None,
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
        if not (perms.view_channel and perms.send_messages and perms.embed_links):
            await interaction.response.send_message(
                f"I need View Channel, Send Messages and Embed Links in {target.mention}.", ephemeral=True)
            return
        active = sum(1 for r in self.records.values() if r["guild"] == interaction.guild_id and not r.get("ended"))
        if active >= MAX_ACTIVE_PER_GUILD:
            await interaction.response.send_message(
                f"This server already has {MAX_ACTIVE_PER_GUILD} running giveaways.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        rec: Record = dict(
            id=0, guild=interaction.guild_id, channel=target.id, host=interaction.user.id, prize=prize,
            description=description or "", winners=winners, ends=int(time.time()) + seconds,
            entries=[], role=required_role.id if required_role else None, ended=False, winner_ids=[],
        )
        sent = await target.send(embed=build_embed({**rec, "id": "…"}), view=JoinView(self),
                                 allowed_mentions=discord.AllowedMentions.none())
        rec["id"] = sent.id
        self.records[sent.id] = rec
        await sent.edit(embed=build_embed(rec))
        await self.store.save(str(sent.id), rec)
        await interaction.followup.send(
            f"Giveaway started in {target.mention}. ID: `{sent.id}` ({sent.jump_url})", ephemeral=True)

    @giveaway.command(name="edit", description="Change a running giveaway.")
    @app_commands.describe(
        giveaway="The giveaway's message ID or message link",
        prize="New prize",
        duration="New time left from now, e.g. 2h",
        winners="New number of winners",
        description="New description text",
    )
    async def edit(
        self,
        interaction: discord.Interaction,
        giveaway: str,
        prize: Optional[app_commands.Range[str, 1, 200]] = None,
        duration: Optional[str] = None,
        winners: Optional[app_commands.Range[int, 1, 20]] = None,
        description: Optional[app_commands.Range[str, 1, 500]] = None,
    ) -> None:
        rec = self._find(interaction, giveaway)
        if rec is None:
            await interaction.response.send_message("I couldn't find a giveaway with that ID or link.", ephemeral=True)
            return
        if rec.get("ended"):
            await interaction.response.send_message("That giveaway has already ended.", ephemeral=True)
            return
        if prize is None and duration is None and winners is None and description is None:
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
        if description is not None:
            rec["description"] = description
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
