import os
from typing import Optional

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands

BOT_OWNER_ID = 1219266886143967245

# Roblox time frame names used by the economy APIs
REVENUE_PERIODS = {
    "day": ("Day", "Past Day"),
    "week": ("Week", "Past 7 Days"),
    "month": ("Month", "Past 30 Days"),
    "year": ("Year", "Past Year"),
}


def get_roblox_cookie_header() -> dict:
    """Builds the .ROBLOSECURITY cookie header from the ROBLOX_COOKIE environment variable."""
    raw_cookie = (os.getenv("ROBLOX_COOKIE") or "").strip().strip('"\'')
    if not raw_cookie:
        return {}
    if not raw_cookie.startswith(".ROBLOSECURITY="):
        raw_cookie = f".ROBLOSECURITY={raw_cookie}"
    return {"Cookie": raw_cookie}


def error_embed(title: str, description: str) -> discord.Embed:
    return discord.Embed(title=title, description=description, color=0xF43F5E)


class UtilityCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # -------------------------------------------------------------
    # SLASH COMMAND: ROBLOX REVENUE
    # -------------------------------------------------------------
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
        # Revenue is private account data, so only the owner can use this command.
        if interaction.user.id != BOT_OWNER_ID:
            await interaction.response.send_message(
                embed=error_embed("Permission Denied", "Only the bot owner can use this command."),
                ephemeral=True
            )
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
        group_id = os.getenv("ROBLOX_GROUP_ID", "").strip()

        try:
            async with aiohttp.ClientSession(headers=headers) as session:
                if group_id:
                    # Group revenue
                    url = f"https://economy.roblox.com/v1/groups/{group_id}/revenue/summary/{time_frame}"
                    source_label = f"Group `{group_id}`"
                    async with session.get(url) as resp:
                        if resp.status in (401, 403):
                            await interaction.followup.send(
                                embed=error_embed(
                                    "Access Denied",
                                    "Roblox rejected the cookie. Make sure `ROBLOX_COOKIE` is valid and has permission to view group revenue."
                                ),
                                ephemeral=True
                            )
                            return
                        if resp.status != 200:
                            await interaction.followup.send(
                                embed=error_embed("Roblox API Error", f"Roblox returned status `{resp.status}`."),
                                ephemeral=True
                            )
                            return
                        data = await resp.json()

                    sales = data.get("itemSaleRobux", 0)
                    pending = data.get("pendingRobux", 0)
                    extra_name, extra_value = "Group Payouts", data.get("groupPayoutRobux", 0)
                else:
                    # Revenue of the account that owns the cookie
                    async with session.get("https://users.roblox.com/v1/users/authenticated") as resp:
                        if resp.status != 200:
                            await interaction.followup.send(
                                embed=error_embed(
                                    "Cookie Invalid",
                                    "Roblox did not accept `ROBLOX_COOKIE`. The cookie may be expired. Please log in again and update it."
                                ),
                                ephemeral=True
                            )
                            return
                        me = await resp.json()

                    user_id = me.get("id")
                    source_label = f"{me.get('name', 'Unknown')} (`{user_id}`)"

                    url = (
                        f"https://economy.roblox.com/v2/users/{user_id}/transaction-totals"
                        f"?timeFrame={time_frame}&transactionType=summary"
                    )
                    async with session.get(url) as resp:
                        if resp.status != 200:
                            await interaction.followup.send(
                                embed=error_embed("Roblox API Error", f"Roblox returned status `{resp.status}`."),
                                ephemeral=True
                            )
                            return
                        data = await resp.json()

                    sales = data.get("salesTotal", 0)
                    pending = data.get("pendingRobuxTotal", 0)
                    extra_name, extra_value = "Affiliate Sales", data.get("affiliateSalesTotal", 0)

            embed = discord.Embed(
                title=f"Robux Revenue - {period_label}",
                description=f"Source: {source_label}",
                color=0x2ECC71
            )
            embed.add_field(name="Sales", value=f"`{int(sales):,}` R$", inline=True)
            embed.add_field(name=extra_name, value=f"`{int(extra_value):,}` R$", inline=True)
            embed.add_field(name="Pending", value=f"`{int(pending):,}` R$", inline=True)
            embed.set_footer(text="Data from the Roblox Economy API")
            await interaction.followup.send(embed=embed, ephemeral=True)

        except Exception as e:
            print(f"[Revenue Error] {e}")
            await interaction.followup.send(
                embed=error_embed("Revenue Error", f"Something went wrong: `{str(e)}`"),
                ephemeral=True
            )


async def setup(bot: commands.Bot):
    await bot.add_cog(UtilityCog(bot))
