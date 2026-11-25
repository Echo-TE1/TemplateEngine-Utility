"""Posts a redeployment log embed to a Discord channel on startup.

Logs: bot version, timestamp, who ran it, and what changed. Info comes from
the environment Render provides (RENDER_GIT_COMMIT, RENDER_GIT_BRANCH) and,
when reachable, the public GitHub API for the commit author and message.

Env vars (all optional):
  DEPLOY_LOG_CHANNEL_ID  channel to post in (default below)
  BOT_VERSION            overrides the version label (default: short commit SHA)
  GITHUB_REPO            owner/name used for commit lookup
  GITHUB_TOKEN           raises the GitHub API rate limit / allows private repos
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone

import aiohttp
import discord

log = logging.getLogger("deploy_log")

DEFAULT_CHANNEL_ID = 1575116831281254425
DEFAULT_REPO = "Echo-TE1/TemplateEngine-Utility"
FOOTER_PREFIX = "deploy "


async def _commit_info(sha: str) -> dict:
    repo = os.environ.get("GITHUB_REPO", DEFAULT_REPO)
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
    channel_id = int(os.environ.get("DEPLOY_LOG_CHANNEL_ID", DEFAULT_CHANNEL_ID))
    sha = os.environ.get("RENDER_GIT_COMMIT", "").strip()
    branch = os.environ.get("RENDER_GIT_BRANCH", "").strip() or "unknown"
    if not sha:
        log.info("RENDER_GIT_COMMIT not set (not a Render deploy); skipping deploy log")
        return

    version = os.environ.get("BOT_VERSION", "").strip() or sha[:7]
    marker = f"{FOOTER_PREFIX}{sha[:12]}"

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
    commit = info.get("commit", {})
    message = (commit.get("message") or "").strip()
    author = info.get("author") or {}
    ran_by = author.get("login") or commit.get("author", {}).get("name") or "unknown"
    ran_by_text = f"[{ran_by}]({author['html_url']})" if author.get("html_url") else ran_by
    commit_url = info.get("html_url") or f"https://github.com/{os.environ.get('GITHUB_REPO', DEFAULT_REPO)}/commit/{sha}"

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
