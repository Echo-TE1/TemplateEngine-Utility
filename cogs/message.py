"""MessageCog: renders a quote / chat-card PNG for a selected user.

Image rendering only: no webhooks, no impersonation. The bot replies with
its own message and attaches the generated PNG.
"""

from __future__ import annotations

import asyncio
import io
from dataclasses import dataclass
from typing import Literal

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands
from PIL import Image, ImageDraw, ImageFont

# ---------------------------------------------------------------- constants

CANVAS_WIDTH = 800
PADDING = 32
AVATAR_SIZE = 96
AVATAR_TEXT_GAP = 24
HEADER_BODY_GAP = 24
LINE_SPACING = 8
MAX_TEXT_LENGTH = 1000
MAX_AVATAR_BYTES = 8 * 1024 * 1024
SUPERSAMPLE = 4  # for smooth circular avatar edges

NAME_FONT_SIZE = 32
TIME_FONT_SIZE = 20
BODY_FONT_SIZE = 28

FONT_CANDIDATES_REGULAR = (
    "DejaVuSans.ttf",
    "arial.ttf",
    "Arial.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "C:/Windows/Fonts/arial.ttf",
)
FONT_CANDIDATES_BOLD = (
    "DejaVuSans-Bold.ttf",
    "arialbd.ttf",
    "Arial Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
)


@dataclass(frozen=True)
class Theme:
    background: tuple[int, int, int]
    name: tuple[int, int, int]
    timestamp: tuple[int, int, int]
    body: tuple[int, int, int]


THEMES: dict[str, Theme] = {
    "dark": Theme(
        background=(49, 51, 56),
        name=(255, 255, 255),
        timestamp=(148, 155, 164),
        body=(219, 222, 225),
    ),
    "light": Theme(
        background=(255, 255, 255),
        name=(6, 6, 7),
        timestamp=(94, 103, 114),
        body=(46, 51, 56),
    ),
}


# ------------------------------------------------------------------ helpers


def _load_font(candidates: tuple[str, ...], size: int) -> ImageFont.ImageFont:
    for path in candidates:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    try:
        # Pillow >= 10.1 can return a scalable default font.
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def _text_width(draw: ImageDraw.ImageDraw, text: str, font) -> float:
    return draw.textlength(text, font=font)


def _wrap_text(
    draw: ImageDraw.ImageDraw, text: str, font, max_width: int
) -> list[str]:
    """Word-wrap text to max_width, preserving newlines and splitting
    words that are too long to fit on a single line."""
    lines: list[str] = []
    for paragraph in text.split("\n"):
        if not paragraph.strip():
            lines.append("")
            continue

        current = ""
        for word in paragraph.split(" "):
            candidate = word if not current else f"{current} {word}"
            if _text_width(draw, candidate, font) <= max_width:
                current = candidate
                continue

            if current:
                lines.append(current)
                current = ""

            # Word alone may still be too wide: break it by characters.
            while _text_width(draw, word, font) > max_width:
                cut = len(word)
                while cut > 1 and _text_width(draw, word[:cut], font) > max_width:
                    cut -= 1
                lines.append(word[:cut])
                word = word[cut:]
            current = word

        lines.append(current)
    return lines


def _circular_avatar(avatar: Image.Image, size: int) -> Image.Image:
    """Return an RGBA avatar cropped to a circle with anti-aliased edges."""
    avatar = avatar.convert("RGBA").resize((size, size), Image.LANCZOS)

    big = size * SUPERSAMPLE
    mask = Image.new("L", (big, big), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, big - 1, big - 1), fill=255)
    mask = mask.resize((size, size), Image.LANCZOS)

    out = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    out.paste(avatar, (0, 0), mask)
    return out


def render_quote_card(
    avatar_bytes: bytes,
    display_name: str,
    timestamp: str,
    text: str,
    theme_name: str = "dark",
) -> io.BytesIO:
    """Render the chat card and return it as a PNG in a BytesIO (blocking)."""
    theme = THEMES.get(theme_name, THEMES["dark"])

    name_font = _load_font(FONT_CANDIDATES_BOLD, NAME_FONT_SIZE)
    time_font = _load_font(FONT_CANDIDATES_REGULAR, TIME_FONT_SIZE)
    body_font = _load_font(FONT_CANDIDATES_REGULAR, BODY_FONT_SIZE)

    # Scratch canvas for measuring text before we know the final height.
    measure = ImageDraw.Draw(Image.new("RGB", (1, 1)))

    text_left = PADDING + AVATAR_SIZE + AVATAR_TEXT_GAP
    header_width = CANVAS_WIDTH - text_left - PADDING
    body_width = CANVAS_WIDTH - 2 * PADDING

    # Truncate the display name if it would overflow the header area.
    name = display_name
    if _text_width(measure, name, name_font) > header_width:
        while len(name) > 1 and _text_width(measure, name + "…", name_font) > header_width:
            name = name[:-1]
        name += "…"

    body_lines = _wrap_text(measure, text, body_font, body_width)

    body_ascent, body_descent = body_font.getmetrics()
    line_height = body_ascent + body_descent + LINE_SPACING
    body_height = line_height * len(body_lines) - LINE_SPACING

    header_height = AVATAR_SIZE
    height = PADDING + header_height + HEADER_BODY_GAP + body_height + PADDING

    image = Image.new("RGB", (CANVAS_WIDTH, height), theme.background)
    draw = ImageDraw.Draw(image)

    # Avatar
    with Image.open(io.BytesIO(avatar_bytes)) as raw:
        raw.load()
        avatar = _circular_avatar(raw, AVATAR_SIZE)
    image.paste(avatar, (PADDING, PADDING), avatar)

    # Name + timestamp, vertically centred beside the avatar
    name_ascent, name_descent = name_font.getmetrics()
    time_ascent, time_descent = time_font.getmetrics()
    name_h = name_ascent + name_descent
    time_h = time_ascent + time_descent
    block_h = name_h + 4 + time_h
    block_top = PADDING + (AVATAR_SIZE - block_h) // 2

    draw.text((text_left, block_top), name, font=name_font, fill=theme.name)
    draw.text(
        (text_left, block_top + name_h + 4),
        timestamp,
        font=time_font,
        fill=theme.timestamp,
    )

    # Body text
    y = PADDING + header_height + HEADER_BODY_GAP
    for line in body_lines:
        if line:
            draw.text((PADDING, y), line, font=body_font, fill=theme.body)
        y += line_height

    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    buffer.seek(0)
    return buffer


# ---------------------------------------------------------------------- cog


class MessageCog(commands.Cog):
    """Slash command that renders a quote card image."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.session: aiohttp.ClientSession | None = None

    async def cog_load(self) -> None:
        self.session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=10)
        )

    async def cog_unload(self) -> None:
        if self.session is not None:
            await self.session.close()
            self.session = None

    async def _fetch_avatar(self, user: discord.abc.User) -> bytes:
        assert self.session is not None
        url = user.display_avatar.replace(size=256, format="png").url
        async with self.session.get(url) as resp:
            resp.raise_for_status()
            data = await resp.content.read(MAX_AVATAR_BYTES + 1)
        if len(data) > MAX_AVATAR_BYTES:
            raise ValueError("Avatar is too large.")
        return data

    @app_commands.command(
        name="quote",
        description="Render a quote card image for a user and message.",
    )
    @app_commands.describe(
        user="The user the quote is attributed to",
        text="The message text to render",
        theme="Card theme (default: dark)",
    )
    async def quote(
        self,
        interaction: discord.Interaction,
        user: discord.User,
        text: app_commands.Range[str, 1, MAX_TEXT_LENGTH],
        theme: Literal["dark", "light"] = "dark",
    ) -> None:
        await interaction.response.defer(thinking=True)

        try:
            avatar_bytes = await self._fetch_avatar(user)
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError):
            await interaction.followup.send(
                "Couldn't download that user's avatar. Please try again.",
                ephemeral=True,
            )
            return

        timestamp = discord.utils.utcnow().strftime("%Y-%m-%d %H:%M UTC")

        try:
            buffer = await asyncio.to_thread(
                render_quote_card,
                avatar_bytes,
                user.display_name,
                timestamp,
                text,
                theme,
            )
        except Exception:
            await interaction.followup.send(
                "Something went wrong while rendering the image.",
                ephemeral=True,
            )
            raise

        await interaction.followup.send(
            file=discord.File(buffer, filename="quote.png")
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(MessageCog(bot))
