"""Posts a redeployment log embed to a Discord channel on startup.

Logs: bot version, timestamp, who ran it, and what changed. Info comes from
the environment Render provides (RENDER_GIT_COMMIT, RENDER_GIT_BRANCH) and,
when reachable, the public GitHub API for the commit author and message.

The channel and repo are hard-coded in config.py. The version is
"v<number of commits>" so it goes up with every change, plus the short SHA.

Env vars (optional):
  GITHUB_TOKEN  raises the GitHub API rate limit / allows private repos
"""

from __future__ import annotations

import logging
import os
import re
from datetime import datetime, timezone
from typing import Optional

import aiohttp
import discord

log = logging.getLogger("deploy_log")

from config import DEPLOY_LOG_CHANNEL_ID, GITHUB_REPO

FOOTER_PREFIX = "deploy "


async def _commit_info(sha: str) -> dict:
    repo = GITHUB_REPO
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "deploy-log"}
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=8)) as s:
            async with s.get(
                f"https://api.github.com/repos/{repo}/commits/{sha}", headers=headers
            ) as resp:
                if resp.status != 200:
                    return {}
                return await resp.json()
    except (aiohttp.ClientError, TimeoutError):
        return {}


async def _commit_count(sha: str) -> Optional[int]:
    """Total commits up to sha (via the Link header of a per_page=1 listing)."""
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "deploy-log"}
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    url = f"https://api.github.com/repos/{GITHUB_REPO}/commits?sha={sha}&per_page=1"
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=8)) as s:
            async with s.get(url, headers=headers) as resp:
                if resp.status != 200:
                    return None
                match = re.search(r'[?&]page=(\d+)>; rel="last"', resp.headers.get("Link", ""))
                if match:
                    return int(match.group(1))
                return len(await resp.json()) or None
    except (aiohttp.ClientError, TimeoutError, ValueError):
        return None


async def _already_logged(channel: discord.abc.Messageable, bot_id: int, marker: str) -> bool:
    """True if one of our recent embeds already carries this deploy marker."""
    try:
        async for msg in channel.history(limit=25):
            if msg.author.id != bot_id:
                continue
            for embed in msg.embeds:
                if embed.footer and embed.footer.text == marker:
                    return True
    except discord.HTTPException:
        pass
    return False


async def post_deploy_log(bot: discord.Client) -> None:
    channel_id = DEPLOY_LOG_CHANNEL_ID
    sha = os.environ.get("RENDER_GIT_COMMIT", "").strip()
    branch = os.environ.get("RENDER_GIT_BRANCH", "").strip() or "unknown"
    if not sha:
        log.info("RENDER_GIT_COMMIT not set (not a Render deploy); skipping deploy log")
        return

    # A new Render deploy gets a new instance ID, so a manual redeploy of the
    # same commit is still logged; a plain restart of one instance is not.
    instance = os.environ.get("RENDER_INSTANCE_ID", "").strip()
    marker = f"{FOOTER_PREFIX}{sha[:12]}"
    if instance:
        marker += f" {instance[-12:]}"

    try:
        channel = bot.get_channel(channel_id) or await bot.fetch_channel(channel_id)
    except discord.HTTPException as exc:
        log.warning("Deploy log channel %s unavailable: %s", channel_id, exc)
        return

    # Render restarts the same deploy now and then; log each commit only once.
    if bot.user and await _already_logged(channel, bot.user.id, marker):
        log.info("Deploy %s already logged", sha[:7])
        return

    info = await _commit_info(sha)
    count = await _commit_count(sha)
    version = f"v{count} ({sha[:7]})" if count else sha[:7]
    commit = info.get("commit", {})
    message = (commit.get("message") or "").strip()
    author = info.get("author") or {}
    ran_by = author.get("login") or commit.get("author", {}).get("name") or "unknown"
    ran_by_text = f"[{ran_by}]({author['html_url']})" if author.get("html_url") else ran_by
    commit_url = info.get("html_url") or f"https://github.com/{GITHUB_REPO}/commit/{sha}"

    updated = message or "Commit details unavailable (could not reach GitHub)."
    if len(updated) > 1000:
        updated = updated[:997] + "..."

    embed = discord.Embed(
        title="Redeployment",
        colour=discord.Colour.green(),
        timestamp=datetime.now(timezone.utc),
    )
    embed.add_field(name="Bot version", value=f"`{version}`", inline=True)
    embed.add_field(name="Branch", value=f"`{branch}`", inline=True)
    embed.add_field(name="Ran by", value=ran_by_text, inline=True)
    embed.add_field(name="Deployed at", value=discord.utils.format_dt(embed.timestamp, "F"), inline=False)
    embed.add_field(name="Updated content", value=updated, inline=False)
    embed.add_field(name="Commit", value=f"[{sha[:7]}]({commit_url})", inline=False)
    embed.set_footer(text=marker)

    try:
        await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
        log.info("Posted deploy log for %s", sha[:7])
    except discord.HTTPException as exc:
        log.warning("Could not post deploy log: %s", exc)
