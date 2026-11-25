"""StatsCog: two stage channels in one guild that show live bot stats.

  "<n> indiv. users"   unique non-bot users across every server the bot is in
  "<n> servers"        total servers the bot is in

The channels are found again by name after a redeploy (or by ID via env vars),
so they are reused rather than duplicated. Discord limits a channel to 2 renames
per 10 minutes, so changes are batched and applied at most once per interval.

The guild ID is hard-coded in config.py. A channel is only reused if its name
matches AND it carries a permission overwrite for the bot's own member, which
the bot adds when it creates the channel. That marker lives in Discord, so it
survives redeploys, and channels made by anyone else are never touched.

Needs the Manage Channels permission in that guild. Counting *unique* users
needs the Server Members intent; without it the user count falls back to the
sum of every server's member count (which counts people in several servers
more than once).
"""

from __future__ import annotations

import logging
import re
from typing import Optional

import discord
from discord.ext import commands, tasks

from config import GUILD_ID

log = logging.getLogger(__name__)

UPDATE_MINUTES = 6  # 2 renames / 10 min limit => stay safely under it

USERS_RE = re.compile(r"^[\d,]+ indiv\. users$")
SERVERS_RE = re.compile(r"^[\d,]+ servers?$")


def users_name(count: int) -> str:
    return f"{count:,} indiv. users"


def servers_name(count: int) -> str:
    return f"{count:,} servers"


class StatsCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.guild_id = GUILD_ID
        self._dirty = True
        self._users_channel: Optional[discord.StageChannel] = None
        self._servers_channel: Optional[discord.StageChannel] = None

    # ------------------------------------------------------------ counting

    def unique_users(self) -> int:
        if self.bot.intents.members:
            return sum(1 for u in self.bot.users if not u.bot)
        return sum(g.member_count or 0 for g in self.bot.guilds)

    def server_count(self) -> int:
        return len(self.bot.guilds)

    # ----------------------------------------------------------- lifecycle

    async def cog_load(self) -> None:
        self.refresh.change_interval(minutes=UPDATE_MINUTES)

    async def cog_unload(self) -> None:
        self.refresh.cancel()

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        if not self.refresh.is_running():
            self.refresh.start()

    # Any of these can change a count; the loop applies the change in a batch.
    @commands.Cog.listener()
    async def on_guild_join(self, guild: discord.Guild) -> None:
        self._dirty = True

    @commands.Cog.listener()
    async def on_guild_remove(self, guild: discord.Guild) -> None:
        self._dirty = True

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        self._dirty = True

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member) -> None:
        self._dirty = True

    # ------------------------------------------------------------- channels

    @staticmethod
    def _made_by_bot(guild: discord.Guild, channel: discord.StageChannel) -> bool:
        """The bot adds an overwrite for itself when it creates the channel."""
        return guild.me in channel.overwrites

    def _find_channel(
        self, guild: discord.Guild, pattern: re.Pattern[str]
    ) -> Optional[discord.StageChannel]:
        for channel in guild.stage_channels:
            if pattern.match(channel.name) and self._made_by_bot(guild, channel):
                return channel
        return None

    async def _create_channel(self, guild: discord.Guild, name: str) -> discord.StageChannel:
        everyone = guild.default_role
        overwrites = {
            # Visible to everyone, but nobody can join the stage.
            everyone: discord.PermissionOverwrite(view_channel=True, connect=False),
            guild.me: discord.PermissionOverwrite(
                view_channel=True, connect=True, manage_channels=True
            ),
        }
        channel = await guild.create_stage_channel(
            name=name,
            overwrites=overwrites,
            reason="Live bot stats channel",
        )
        log.info("Created stage channel %r (%s)", name, channel.id)
        return channel

    async def _ensure(self, guild: discord.Guild) -> bool:
        """Find (or create) both stage channels. Returns False if not possible."""
        if self._users_channel is None or guild.get_channel(self._users_channel.id) is None:
            self._users_channel = self._find_channel(guild, USERS_RE)
        if self._servers_channel is None or guild.get_channel(self._servers_channel.id) is None:
            self._servers_channel = self._find_channel(guild, SERVERS_RE)

        try:
            if self._users_channel is None:
                self._users_channel = await self._create_channel(
                    guild, users_name(self.unique_users())
                )
            if self._servers_channel is None:
                self._servers_channel = await self._create_channel(
                    guild, servers_name(self.server_count())
                )
        except discord.Forbidden:
            log.warning("Missing Manage Channels permission in guild %s", guild.id)
            return False
        except discord.HTTPException as exc:
            # Most often: the server is not a Community server (no stage channels).
            log.warning("Could not create stage channel in guild %s: %s", guild.id, exc)
            return False
        return True

    async def _rename(self, channel: discord.StageChannel, name: str) -> None:
        if channel.name == name:
            return
        try:
            await channel.edit(name=name, reason="Live bot stats update")
            log.info("Renamed stats channel to %r", name)
        except discord.HTTPException as exc:
            log.warning("Could not rename %s to %r: %s", channel.id, name, exc)
            self._dirty = True  # try again next round

    # ----------------------------------------------------------------- loop

    @tasks.loop(minutes=UPDATE_MINUTES)
    async def refresh(self) -> None:
        guild = self.bot.get_guild(self.guild_id)
        if guild is None:
            log.warning("Stats guild %s not found (is the bot in it?)", self.guild_id)
            return

        # Always re-check on each tick so a deleted channel is recreated,
        # but only rename when something changed.
        if not await self._ensure(guild):
            return
        if not self._dirty:
            return
        self._dirty = False

        assert self._users_channel is not None and self._servers_channel is not None
        await self._rename(self._users_channel, users_name(self.unique_users()))
        await self._rename(self._servers_channel, servers_name(self.server_count()))

    @refresh.before_loop
    async def _before_refresh(self) -> None:
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(StatsCog(bot))
