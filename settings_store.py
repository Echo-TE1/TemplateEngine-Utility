"""Per-user default options, stored as messages in a private channel of the
home server. Render's disk is wiped on every deploy, so the data lives in
Discord instead; if the channel is unavailable it falls back to memory."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Optional

import discord

from timeutil import valid_timezone
from config import GUILD_ID, SETTINGS_CHANNEL_NAME

log = logging.getLogger("settings_store")

ALLOWED_KEYS = {"theme", "timezone", "timestamp", "hide_clantags", "use_username", "label"}
THEMES = {"dark", "ash", "onyx", "light"}


def clean(values: dict[str, Any]) -> dict[str, Any]:
    """Keep only valid keys and values."""
    out: dict[str, Any] = {}
    for key, value in values.items():
        if key not in ALLOWED_KEYS or value is None:
            continue
        if key == "theme" and value not in THEMES:
            continue
        if key == "timezone":
            if not valid_timezone(str(value)):
                continue
            value = str(value).strip()
        if key == "timestamp":
            value = str(value)[:60]
        if key in {"hide_clantags", "use_username", "label"}:
            value = bool(value)
        out[key] = value
    return out


class SettingsStore:
    def __init__(self, bot: discord.Client) -> None:
        self.bot = bot
        self._data: dict[int, dict[str, Any]] = {}
        self._message_ids: dict[int, int] = {}
        self._channel: Optional[discord.TextChannel] = None
        self._lock = asyncio.Lock()
        self.persistent = False
        self._loaded = False

    async def _ensure_channel(self) -> Optional[discord.TextChannel]:
        if self._channel is not None:
            return self._channel
        guild = self.bot.get_guild(GUILD_ID)
        if guild is None:
            return None
        for channel in guild.text_channels:
            if channel.name == SETTINGS_CHANNEL_NAME and guild.me in channel.overwrites:
                self._channel = channel
                return channel
        try:
            overwrites = {
                guild.default_role: discord.PermissionOverwrite(view_channel=False),
                guild.me: discord.PermissionOverwrite(
                    view_channel=True, send_messages=True, read_message_history=True, manage_messages=True
                ),
            }
            self._channel = await guild.create_text_channel(
                SETTINGS_CHANNEL_NAME, overwrites=overwrites, reason="Per-user defaults storage"
            )
            log.info("Created settings channel %s", self._channel.id)
        except discord.HTTPException as exc:
            log.warning("Could not create the settings channel: %s", exc)
            return None
        return self._channel

    async def load(self) -> None:
        """Read every stored record once (safe to call repeatedly)."""
        async with self._lock:
            if self._loaded:
                return
            channel = await self._ensure_channel()
            if channel is None:
                log.warning("Per-user defaults will not survive a redeploy (no storage channel)")
                self._loaded = True
                return
            try:
                async for message in channel.history(limit=None, oldest_first=True):
                    if message.author.id != self.bot.user.id:
                        continue
                    try:
                        record = json.loads(message.content)
                        user_id = int(record["u"])
                    except (ValueError, KeyError, TypeError):
                        continue
                    self._data[user_id] = clean(record.get("d", {}))
                    self._message_ids[user_id] = message.id
                self.persistent = True
            except discord.HTTPException as exc:
                log.warning("Could not read stored defaults: %s", exc)
            self._loaded = True

    def get(self, user_id: int) -> dict[str, Any]:
        return dict(self._data.get(user_id, {}))

    async def update(self, user_id: int, values: dict[str, Any]) -> dict[str, Any]:
        await self.load()
        merged = {**self._data.get(user_id, {}), **clean(values)}
        await self._save(user_id, merged)
        return dict(merged)

    async def reset(self, user_id: int) -> None:
        await self.load()
        await self._save(user_id, {})

    async def _save(self, user_id: int, data: dict[str, Any]) -> None:
        async with self._lock:
            self._data[user_id] = data
            channel = await self._ensure_channel()
            if channel is None:
                return
            payload = json.dumps({"u": user_id, "d": data}, separators=(",", ":"))
            try:
                message_id = self._message_ids.get(user_id)
                if message_id is not None:
                    try:
                        await channel.get_partial_message(message_id).edit(content=payload)
                        return
                    except discord.NotFound:
                        pass
                sent = await channel.send(payload)
                self._message_ids[user_id] = sent.id
                self.persistent = True
            except discord.HTTPException as exc:
                log.warning("Could not save defaults for %s: %s", user_id, exc)
