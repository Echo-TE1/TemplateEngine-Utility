"""Small durable key -> JSON store kept as messages (with a data.json file) in
a private channel of the home server, so data survives Render redeploys.
Falls back to memory if the channel cannot be created."""

from __future__ import annotations

import asyncio
import io
import json
import logging
from typing import Any, Optional

import discord

from config import GUILD_ID

log = logging.getLogger("blob_store")


class BlobStore:
    def __init__(self, bot: discord.Client, channel_name: str) -> None:
        self.bot = bot
        self.channel_name = channel_name
        self._channel: Optional[discord.TextChannel] = None
        self._ids: dict[str, int] = {}
        self._lock = asyncio.Lock()
        self.persistent = False

    async def _ensure_channel(self) -> Optional[discord.TextChannel]:
        if self._channel is not None:
            return self._channel
        guild = self.bot.get_guild(GUILD_ID)
        if guild is None:
            return None
        for channel in guild.text_channels:
            if channel.name == self.channel_name and guild.me in channel.overwrites:
                self._channel = channel
                return channel
        try:
            overwrites = {
                guild.default_role: discord.PermissionOverwrite(view_channel=False),
                guild.me: discord.PermissionOverwrite(
                    view_channel=True, send_messages=True, read_message_history=True,
                    manage_messages=True, attach_files=True),
            }
            self._channel = await guild.create_text_channel(
                self.channel_name, overwrites=overwrites, reason="Bot data storage")
        except discord.HTTPException as exc:
            log.warning("Could not create %s: %s", self.channel_name, exc)
        return self._channel

    async def load_all(self) -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        async with self._lock:
            channel = await self._ensure_channel()
            if channel is None:
                return out
            try:
                async for message in channel.history(limit=None, oldest_first=True):
                    if message.author.id != self.bot.user.id or not message.attachments:
                        continue
                    try:
                        out[message.content] = json.loads(await message.attachments[0].read())
                        self._ids[message.content] = message.id
                    except (ValueError, discord.HTTPException):
                        continue
                self.persistent = True
            except discord.HTTPException as exc:
                log.warning("Could not read %s: %s", self.channel_name, exc)
        return out

    @staticmethod
    def _file(data: dict[str, Any]) -> discord.File:
        return discord.File(io.BytesIO(json.dumps(data, separators=(",", ":")).encode()), filename="data.json")

    async def save(self, key: str, data: dict[str, Any]) -> None:
        async with self._lock:
            channel = await self._ensure_channel()
            if channel is None:
                return
            try:
                message_id = self._ids.get(key)
                if message_id is not None:
                    try:
                        await channel.get_partial_message(message_id).edit(attachments=[self._file(data)])
                        return
                    except discord.NotFound:
                        pass
                sent = await channel.send(content=key, file=self._file(data))
                self._ids[key] = sent.id
                self.persistent = True
            except discord.HTTPException as exc:
                log.warning("Could not save %s/%s: %s", self.channel_name, key, exc)

    async def delete(self, key: str) -> None:
        async with self._lock:
            message_id = self._ids.pop(key, None)
            channel = self._channel
            if message_id is None or channel is None:
                return
            try:
                await channel.get_partial_message(message_id).delete()
            except discord.HTTPException:
                pass
