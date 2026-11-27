"""UsageCog: tracks images made, network traffic and uptime so the bot stays
inside Render's free limits (Render suspends a free service that goes over
its bandwidth). Owner command: !usage [calibrate <MB shown on Render>].

  - Bandwidth is estimated from the container's own network counters
    (outbound bytes) and saved in a private storage channel, per month.
  - At 70% / 90% / 95% of the limit a warning goes to the error-log channel.
  - From GUARD_PERCENT (default 95%) image generation pauses for everyone
    except the bot owner, so Render never suspends the bot.

Env vars (all optional):
  BANDWIDTH_LIMIT_MB   default 5120 (Render free plan: 5 GB)
  GUARD_PERCENT        default 95
  RENDER_API_KEY       adds pipeline minutes and service count to !usage
                       (RENDER_SERVICE_ID is set by Render itself)
"""

from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timezone
from typing import Any, Optional

import aiohttp
import discord
from discord.ext import commands, tasks

from blob_store import BlobStore
from config import ERROR_LOG_CHANNEL_ID, USAGE_LOG_CHANNEL_ID
from error_log import report_error
from logcard import GREEN, GOLD, PURPLE, RED, send_log
from usage_math import MB, bar, counter_delta, fmt_bytes, month_key, percent, read_net

log = logging.getLogger(__name__)

SAMPLE_SECONDS = 300
FREE_HOURS = 750
FREE_PIPELINE_MINUTES = 500
WARN_LEVELS = (70, 90, 95)
LIMIT_MB = float(os.environ.get("BANDWIDTH_LIMIT_MB", "5120") or 5120)
GUARD_PERCENT = float(os.environ.get("GUARD_PERCENT", "95") or 95)


class UsageCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.store = BlobStore(bot, "usage-data")
        self.rec: dict[str, Any] = self._blank(month_key())
        self._loaded = False
        self._last_net: Optional[tuple[int, int]] = None
        self._last_time: Optional[float] = None
        self.session: Optional[aiohttp.ClientSession] = None
        self._hour = ""  # the hour (UTC) the last report covered up to
        self._base: dict[str, float] = {}  # counters at the last report

    @staticmethod
    def _blank(month: str) -> dict[str, Any]:
        return dict(month=month, renders=0, gif=0, image_bytes=0, uptime=0.0, net_rx=0, net_tx=0,
                    offset=0, warned=[])

    # ---------------------------------------------------------- lifecycle

    async def cog_load(self) -> None:
        self.session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15))

    async def cog_unload(self) -> None:
        self.sample.cancel()
        self.hourly.cancel()
        try:  # keep the counters from the last few minutes across a redeploy
            await self.store.save(self.rec["month"], self.rec)
        except Exception:
            pass
        if self.session is not None:
            await self.session.close()

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        if not self._loaded:
            self._loaded = True
            saved = await self.store.load_all()
            month = month_key()
            if month in saved:
                self.rec = {**self._blank(month), **saved[month]}
        if not self.sample.is_running():
            self.sample.start()
        if not self.hourly.is_running():
            self._hour = self._hour_key()
            self._snapshot()
            self.hourly.start()

    # ----------------------------------------------------------- tracking

    def record_render(self, size: int, is_gif: bool) -> None:
        self._roll()
        self.rec["renders"] += 1
        self.rec["gif"] += 1 if is_gif else 0
        self.rec["image_bytes"] += int(size)

    def _roll(self) -> None:
        month = month_key()
        if self.rec["month"] != month:
            old = self.rec
            self.rec = self._blank(month)
            self.bot.loop.create_task(self.store.save(old["month"], old))

    @tasks.loop(seconds=SAMPLE_SECONDS)
    async def sample(self) -> None:
        try:
            self._roll()
            now = time.time()
            if self._last_time is not None:
                self.rec["uptime"] += now - self._last_time
            self._last_time = now
            net = read_net()
            if net is not None:
                if self._last_net is not None:
                    drx, dtx = counter_delta(self._last_net, net)
                    self.rec["net_rx"] += drx
                    self.rec["net_tx"] += dtx
                self._last_net = net
            await self.store.save(self.rec["month"], self.rec)
            await self._warn_if_needed()
        except Exception as exc:
            await report_error(self.bot, "usage sampling", exc)

    @sample.before_loop
    async def _before_sample(self) -> None:
        await self.bot.wait_until_ready()

    # ------------------------------------------------------- hourly report

    @staticmethod
    def _hour_key() -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d %H")

    def _snapshot(self) -> None:
        self._base = {"renders": self.rec["renders"], "gif": self.rec["gif"],
                      "tx": self.rec["net_tx"], "rx": self.rec["net_rx"], "bytes": self.rec["image_bytes"]}

    @tasks.loop(seconds=30)
    async def hourly(self) -> None:
        """Post the usage report at the start of every hour (UTC clock; the
        minute marks are the same in every timezone with whole-hour offsets)."""
        try:
            key = self._hour_key()
            if key == self._hour:
                return
            self._hour = key
            await self.post_report()
        except Exception as exc:
            await report_error(self.bot, "hourly usage report", exc)

    @hourly.before_loop
    async def _before_hourly(self) -> None:
        await self.bot.wait_until_ready()

    async def post_report(self) -> None:
        self._roll()
        rec, base = self.rec, self._base or {}
        pct = self.used_percent()
        guard = "Paused (limit reached)" if pct >= GUARD_PERCENT else "Active"
        colour = RED if pct >= 90 else GOLD if pct >= 70 else GREEN
        hour_images = rec["renders"] - int(base.get("renders", rec["renders"]))
        hour_gifs = rec["gif"] - int(base.get("gif", rec["gif"]))
        hour_tx = rec["net_tx"] - base.get("tx", rec["net_tx"])
        hour_rx = rec["net_rx"] - base.get("rx", rec["net_rx"])
        avg = rec["image_bytes"] / rec["renders"] if rec["renders"] else 0
        items = [
            ("Bandwidth This Month", f"`{bar(pct)}` **{pct:.1f}%**\n{fmt_bytes(self.used_bytes())} of "
                                      f"{fmt_bytes(LIMIT_MB * MB)}"),
            ("Last Hour", f"`{hour_images:,}` images (`{hour_gifs:,}` GIFs)\nOut {fmt_bytes(max(hour_tx, 0))} \u2022 "
                          f"In {fmt_bytes(max(hour_rx, 0))}"),
            ("This Month", f"`{rec['renders']:,}` images (`{rec['gif']:,}` GIFs) \u2022 avg {fmt_bytes(avg)}\n"
                           f"Out {fmt_bytes(rec['net_tx'])} \u2022 In {fmt_bytes(rec['net_rx'])}"),
            ("Uptime", f"`{rec['uptime'] / 3600:.1f}` h of `{FREE_HOURS}` h free hours (bot's own count)"),
            ("Image Generation", f"{guard} \u2022 pauses at {GUARD_PERCENT:.0f}%"),
        ]
        api = await self._render_api()
        if api and "error" not in api:
            if "pipeline_minutes" in api:
                items.append(("Pipeline Minutes", f"`{api['pipeline_minutes']:.0f}` of `{FREE_PIPELINE_MINUTES}` (about)"))
            items.append(("Services", f"`{api['services']}` of `25`"))
        self._snapshot()
        await send_log(self.bot, USAGE_LOG_CHANNEL_ID, f"Hourly Usage \u2022 {rec['month']}", colour, items)

    # -------------------------------------------------------------- guard

    def used_bytes(self) -> float:
        return self.rec["net_tx"] + self.rec["offset"]

    def used_percent(self) -> float:
        return percent(self.used_bytes(), LIMIT_MB * MB)

    def render_allowed(self) -> tuple[bool, str]:
        if self.used_percent() >= GUARD_PERCENT:
            return False, ("Image generation is paused for the rest of the month to stay inside the hosting "
                           "bandwidth limit. It comes back when the month resets.")
        return True, ""

    async def _warn_if_needed(self) -> None:
        pct = self.used_percent()
        for level in WARN_LEVELS:
            if pct >= level and level not in self.rec["warned"]:
                self.rec["warned"].append(level)
                try:
                    channel = self.bot.get_channel(ERROR_LOG_CHANNEL_ID) or await self.bot.fetch_channel(ERROR_LOG_CHANNEL_ID)
                    note = " Image generation is now paused." if pct >= GUARD_PERCENT else ""
                    await channel.send(embed=discord.Embed(
                        title=f"Bandwidth at {pct:.0f}%",
                        description=f"About {fmt_bytes(self.used_bytes())} of {fmt_bytes(LIMIT_MB * MB)} used "
                                    f"this month.{note}", colour=discord.Colour.orange()))
                except discord.HTTPException:
                    pass

    # ---------------------------------------------------------- Render API

    async def _render_api(self) -> Optional[dict[str, Any]]:
        key = os.environ.get("RENDER_API_KEY", "").strip()
        service = os.environ.get("RENDER_SERVICE_ID", "").strip()
        if not key or self.session is None:
            return None
        headers = {"Authorization": f"Bearer {key}", "Accept": "application/json"}
        out: dict[str, Any] = {}
        try:
            async with self.session.get("https://api.render.com/v1/services?limit=100", headers=headers) as resp:
                resp.raise_for_status()
                out["services"] = len(await resp.json())
            if service:
                async with self.session.get(f"https://api.render.com/v1/services/{service}/deploys?limit=100",
                                            headers=headers) as resp:
                    resp.raise_for_status()
                    start = datetime.now(timezone.utc).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
                    seconds = 0.0
                    for item in await resp.json():
                        deploy = item.get("deploy", item)
                        begin, end = deploy.get("createdAt"), deploy.get("finishedAt")
                        if not begin or not end:
                            continue
                        b = datetime.fromisoformat(begin.replace("Z", "+00:00"))
                        e = datetime.fromisoformat(end.replace("Z", "+00:00"))
                        if b >= start:
                            seconds += (e - b).total_seconds()
                    out["pipeline_minutes"] = seconds / 60
        except (aiohttp.ClientError, ValueError, TypeError) as exc:
            out["error"] = str(exc)[:200]
        return out

    # ------------------------------------------------------------ command

    @commands.command(name="usage")
    @commands.is_owner()
    async def usage_command(self, ctx: commands.Context, action: Optional[str] = None,
                            value: Optional[float] = None) -> None:
        """!usage: this month's bandwidth, images and uptime. !usage calibrate <MB>: match Render's number."""
        self._roll()
        if action == "calibrate":
            if value is None or value < 0:
                await ctx.send("Use `!usage calibrate 267` with the MB shown on Render's usage page.")
                return
            self.rec["offset"] = int(value * MB - self.rec["net_tx"])
            await self.store.save(self.rec["month"], self.rec)
            await ctx.send(f"Calibrated: now showing {fmt_bytes(self.used_bytes())} used.")
            return

        rec = self.rec
        pct = self.used_percent()
        hours = rec["uptime"] / 3600
        avg = rec["image_bytes"] / rec["renders"] if rec["renders"] else 0
        card = discord.Embed(title=f"Usage for {rec['month']}", colour=discord.Colour.blurple())
        card.add_field(name="Bandwidth (estimate)", inline=False,
                       value=f"`{bar(pct)}` {pct:.1f}%\n{fmt_bytes(self.used_bytes())} of {fmt_bytes(LIMIT_MB * MB)}"
                             f" • pauses at {GUARD_PERCENT:.0f}%")
        card.add_field(name="Images", value=f"{rec['renders']:,} made\n{rec['gif']:,} GIFs\navg {fmt_bytes(avg)}")
        card.add_field(name="Network", value=f"out {fmt_bytes(rec['net_tx'])}\nin {fmt_bytes(rec['net_rx'])}")
        card.add_field(name="Uptime (bot's own count)", value=f"{hours:.1f} h of {FREE_HOURS} h")
        api = await self._render_api()
        if api is None:
            card.set_footer(text="Set RENDER_API_KEY to also see pipeline minutes and service count.")
        elif "error" in api:
            card.add_field(name="Render API", value=f"Could not read it: {api['error']}", inline=False)
        else:
            if "pipeline_minutes" in api:
                card.add_field(name="Pipeline minutes (about)", value=f"{api['pipeline_minutes']:.0f} of {FREE_PIPELINE_MINUTES}")
            card.add_field(name="Services", value=f"{api['services']} of 25")
        await ctx.send(embed=card)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(UsageCog(bot))
