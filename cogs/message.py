"""MessageCog: renders a Discord-style chat-card image for a selected user.

Command: /generate discord message

Image rendering only: no webhooks, no impersonation. The bot replies with its
own message and attaches the generated image.
"""

from __future__ import annotations

import asyncio
import io
import logging
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Literal

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands
from PIL import Image, ImageChops, ImageDraw, ImageFont

log = logging.getLogger(__name__)

# ---------------------------------------------------------------- constants

CANVAS_WIDTH = 800
PADDING = 32
AVATAR_SIZE = 96
AVATAR_TEXT_GAP = 24
HEADER_BODY_GAP = 24
BLOCK_GAP = 16
LINE_SPACING = 8
MAX_TEXT_LENGTH = 1000
MAX_AVATAR_BYTES = 8 * 1024 * 1024
MAX_IMAGE_BYTES = 8 * 1024 * 1024
MAX_EMOJI_BYTES = 512 * 1024
MAX_ATTACHMENT_HEIGHT = 500
MAX_REACTIONS = 8
SUPERSAMPLE = 4  # for smooth circular avatar edges

NAME_FONT_SIZE = 32
TIME_FONT_SIZE = 20
BODY_FONT_SIZE = 28
TAG_FONT_SIZE = 20
COUNT_FONT_SIZE = 22

REACTION_HEIGHT = 44
REACTION_PAD = 12
REACTION_GAP = 10
EMOJI_SIZE = 26

FONT_DIR = Path(__file__).resolve().parent.parent / "fonts"

# display name -> (regular file, bold file), bundled in ./fonts
FONTS: dict[str, tuple[str, str]] = {
    "Inter": ("Inter-Regular.otf", "Inter-Bold.otf"),
    "DejaVu Sans": ("DejaVuSans.ttf", "DejaVuSans-Bold.ttf"),
    "DejaVu Serif": ("DejaVuSerif.ttf", "DejaVuSerif-Bold.ttf"),
    "DejaVu Mono": ("DejaVuSansMono.ttf", "DejaVuSansMono-Bold.ttf"),
}
FontName = Literal["Inter", "DejaVu Sans", "DejaVu Serif", "DejaVu Mono"]

SYSTEM_FONT_FALLBACKS = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "DejaVuSans.ttf",
    "arial.ttf",
)

MENTION_RE = re.compile(r"(@(?:everyone|here|[\w.]{1,32}))")
REACTION_TOKEN_RE = re.compile(r"<a?:\w+:\d+>|\d+|[^\s,]+")
CUSTOM_EMOJI_RE = re.compile(r"<a?:\w+:(\d+)>")

TWEMOJI_URL = "https://cdn.jsdelivr.net/gh/jdecked/twemoji@latest/assets/72x72/{name}.png"
CUSTOM_EMOJI_URL = "https://cdn.discordapp.com/emojis/{id}.png?size=64"


@dataclass(frozen=True)
class Theme:
    background: tuple[int, int, int]
    name: tuple[int, int, int]
    timestamp: tuple[int, int, int]
    body: tuple[int, int, int]
    mention_fg: tuple[int, int, int]
    mention_bg: tuple[int, int, int]
    pill_bg: tuple[int, int, int]
    pill_fg: tuple[int, int, int]
    tag_bg: tuple[int, int, int]
    tag_fg: tuple[int, int, int]
    placeholder: tuple[int, int, int]


THEMES: dict[str, Theme] = {
    "dark": Theme(
        background=(49, 51, 56),
        name=(255, 255, 255),
        timestamp=(148, 155, 164),
        body=(219, 222, 225),
        mention_fg=(201, 205, 251),
        mention_bg=(61, 66, 112),
        pill_bg=(43, 45, 49),
        pill_fg=(219, 222, 225),
        tag_bg=(64, 66, 73),
        tag_fg=(219, 222, 225),
        placeholder=(114, 118, 125),
    ),
    "light": Theme(
        background=(255, 255, 255),
        name=(6, 6, 7),
        timestamp=(94, 103, 114),
        body=(46, 51, 56),
        mention_fg=(80, 92, 220),
        mention_bg=(230, 232, 253),
        pill_bg=(242, 243, 245),
        pill_fg=(46, 51, 56),
        tag_bg=(235, 237, 239),
        tag_fg=(46, 51, 56),
        placeholder=(163, 166, 170),
    ),
}


# ------------------------------------------------------------------ helpers


@lru_cache(maxsize=64)
def _load_font(font_name: str, bold: bool, size: int) -> ImageFont.ImageFont:
    regular, bold_file = FONTS.get(font_name, FONTS["Inter"])
    candidates = [str(FONT_DIR / (bold_file if bold else regular))]
    candidates += [str(FONT_DIR / ("DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"))]
    candidates += list(SYSTEM_FONT_FALLBACKS)
    for path in candidates:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    try:
        return ImageFont.load_default(size=size)  # Pillow >= 10.1
    except TypeError:
        return ImageFont.load_default()


def _text_width(draw: ImageDraw.ImageDraw, text: str, font) -> float:
    return draw.textlength(text, font=font)


def _wrap_text(draw: ImageDraw.ImageDraw, text: str, font, max_width: int) -> list[str]:
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


def _rounded_mask(size: tuple[int, int], radius: int) -> Image.Image:
    big = (size[0] * SUPERSAMPLE, size[1] * SUPERSAMPLE)
    mask = Image.new("L", big, 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        (0, 0, big[0] - 1, big[1] - 1), radius=radius * SUPERSAMPLE, fill=255
    )
    return mask.resize(size, Image.LANCZOS)


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


def _draw_body_line(
    draw: ImageDraw.ImageDraw,
    x: int,
    y: int,
    line: str,
    font,
    line_height: int,
    theme: Theme,
    highlight: bool,
) -> None:
    parts = MENTION_RE.split(line) if highlight else [line]
    for i, part in enumerate(parts):
        if not part:
            continue
        width = _text_width(draw, part, font)
        if highlight and i % 2 == 1:
            draw.rounded_rectangle(
                (x - 3, y, x + width + 3, y + line_height),
                radius=4,
                fill=theme.mention_bg,
            )
            draw.text((x, y), part, font=font, fill=theme.mention_fg)
        else:
            draw.text((x, y), part, font=font, fill=theme.body)
        x += width


def _prepare_attachment(data: bytes, max_w: int, max_h: int) -> Image.Image:
    """Decode an attached image (first frame), shrink to fit, round corners."""
    with Image.open(io.BytesIO(data)) as raw:
        im = raw.convert("RGBA")  # first frame for animated images
    scale = min(max_w / im.width, max_h / im.height, 1.0)
    size = (max(1, int(im.width * scale)), max(1, int(im.height * scale)))
    if size != im.size:
        im = im.resize(size, Image.LANCZOS)
    mask = _rounded_mask(im.size, 12)
    im.putalpha(ImageChops.multiply(im.getchannel("A"), mask))
    return im


def parse_reactions(raw: str | None) -> list[tuple[str, int]]:
    """'👍 3, 🔥 2 😂' -> [('👍', 3), ('🔥', 2), ('😂', 1)]. Custom emoji
    like <:name:123> are supported; plain words are ignored."""
    out: list[tuple[str, int]] = []
    if not raw:
        return out
    for token in REACTION_TOKEN_RE.findall(raw):
        if token.isdigit():
            if out:
                out[-1] = (out[-1][0], max(1, min(int(token), 999999)))
            continue
        if CUSTOM_EMOJI_RE.fullmatch(token) or any(ord(c) > 127 for c in token):
            out.append((token, 1))
        if len(out) >= MAX_REACTIONS:
            break
    return out[:MAX_REACTIONS]


def emoji_url(emoji: str) -> str:
    custom = CUSTOM_EMOJI_RE.fullmatch(emoji)
    if custom:
        return CUSTOM_EMOJI_URL.format(id=custom.group(1))
    if "‍" not in emoji:
        emoji = emoji.replace("️", "")
    return TWEMOJI_URL.format(name="-".join(f"{ord(c):x}" for c in emoji))


def render_quote_card(
    *,
    avatar_bytes: bytes,
    name: str,
    clan_tag: str | None,
    timestamp: str | None,
    text: str,
    theme_name: str = "dark",
    font_name: str = "Inter",
    attachment_bytes: bytes | None = None,
    reactions: list[tuple[bytes, int]] | None = None,
    mention_highlight: bool = True,
    as_gif: bool = False,
) -> tuple[io.BytesIO, str]:
    """Render the chat card. Returns (file buffer, extension). Blocking."""
    theme = THEMES.get(theme_name, THEMES["dark"])

    name_font = _load_font(font_name, True, NAME_FONT_SIZE)
    time_font = _load_font(font_name, False, TIME_FONT_SIZE)
    body_font = _load_font(font_name, False, BODY_FONT_SIZE)
    tag_font = _load_font(font_name, True, TAG_FONT_SIZE)
    count_font = _load_font(font_name, True, COUNT_FONT_SIZE)

    # Scratch canvas for measuring text before we know the final height.
    measure = ImageDraw.Draw(Image.new("RGB", (1, 1)))

    text_left = PADDING + AVATAR_SIZE + AVATAR_TEXT_GAP
    header_width = CANVAS_WIDTH - text_left - PADDING
    body_width = CANVAS_WIDTH - 2 * PADDING

    # Clan tag pill, shown next to the name.
    tag = (clan_tag or "").strip()[:4] or None
    tag_w = int(_text_width(measure, tag, tag_font)) + 16 if tag else 0
    tag_h = 28
    name_max = header_width - (tag_w + 10 if tag else 0)

    # Truncate the display name if it would overflow the header area.
    shown_name = name
    if _text_width(measure, shown_name, name_font) > name_max:
        while len(shown_name) > 1 and _text_width(measure, shown_name + "…", name_font) > name_max:
            shown_name = shown_name[:-1]
        shown_name += "…"

    body_lines = _wrap_text(measure, text, body_font, body_width)
    body_ascent, body_descent = body_font.getmetrics()
    body_line_h = body_ascent + body_descent
    line_step = body_line_h + LINE_SPACING
    body_height = line_step * len(body_lines) - LINE_SPACING

    # Optional attached image.
    attachment = None
    if attachment_bytes:
        attachment = _prepare_attachment(attachment_bytes, body_width, MAX_ATTACHMENT_HEIGHT)

    # Optional reactions, laid out in wrapped rows.
    reaction_items: list[tuple[Image.Image, str, int]] = []
    for emoji_bytes, count in reactions or []:
        try:
            with Image.open(io.BytesIO(emoji_bytes)) as raw:
                emoji_img = raw.convert("RGBA").resize((EMOJI_SIZE, EMOJI_SIZE), Image.LANCZOS)
        except (OSError, ValueError):
            continue
        reaction_items.append((emoji_img, str(count), count))

    placed: list[tuple[int, int, int, Image.Image, str]] = []  # x, row, width, img, label
    rows = 0
    if reaction_items:
        x = 0
        for emoji_img, label, _ in reaction_items:
            w = REACTION_PAD + EMOJI_SIZE + 8 + int(_text_width(measure, label, count_font)) + REACTION_PAD
            if x and x + w > body_width:
                x = 0
                rows += 1
            placed.append((x, rows, w, emoji_img, label))
            x += w + REACTION_GAP
        rows += 1
    reactions_height = rows * REACTION_HEIGHT + max(0, rows - 1) * REACTION_GAP

    height = PADDING + AVATAR_SIZE + HEADER_BODY_GAP + body_height
    if attachment is not None:
        height += BLOCK_GAP + attachment.height
    if placed:
        height += BLOCK_GAP + reactions_height
    height += PADDING

    image = Image.new("RGB", (CANVAS_WIDTH, height), theme.background)
    draw = ImageDraw.Draw(image)

    # Avatar (fall back to a plain circle if the image can't be decoded)
    try:
        with Image.open(io.BytesIO(avatar_bytes)) as raw:
            raw.load()
            avatar = _circular_avatar(raw, AVATAR_SIZE)
    except (OSError, ValueError, Image.DecompressionBombError):
        placeholder = Image.new("RGB", (AVATAR_SIZE, AVATAR_SIZE), theme.placeholder)
        avatar = _circular_avatar(placeholder, AVATAR_SIZE)
    image.paste(avatar, (PADDING, PADDING), avatar)

    # Name (+ clan tag) and optional timestamp, centred beside the avatar
    name_ascent, name_descent = name_font.getmetrics()
    time_ascent, time_descent = time_font.getmetrics()
    name_h = name_ascent + name_descent
    time_h = time_ascent + time_descent
    block_h = name_h + (4 + time_h if timestamp else 0)
    block_top = PADDING + (AVATAR_SIZE - block_h) // 2

    draw.text((text_left, block_top), shown_name, font=name_font, fill=theme.name)
    if tag:
        tag_x = text_left + int(_text_width(draw, shown_name, name_font)) + 10
        tag_y = block_top + (name_h - tag_h) // 2
        draw.rounded_rectangle(
            (tag_x, tag_y, tag_x + tag_w, tag_y + tag_h), radius=6, fill=theme.tag_bg
        )
        draw.text(
            (tag_x + 8, tag_y + (tag_h - TAG_FONT_SIZE) // 2 - 2),
            tag,
            font=tag_font,
            fill=theme.tag_fg,
        )
    if timestamp:
        draw.text(
            (text_left, block_top + name_h + 4),
            timestamp,
            font=time_font,
            fill=theme.timestamp,
        )

    # Body text
    y = PADDING + AVATAR_SIZE + HEADER_BODY_GAP
    for line in body_lines:
        if line:
            _draw_body_line(
                draw, PADDING, y, line, body_font, body_line_h, theme, mention_highlight
            )
        y += line_step
    y -= LINE_SPACING

    # Attached image
    if attachment is not None:
        y += BLOCK_GAP
        image.paste(attachment, (PADDING, y), attachment)
        y += attachment.height

    # Reactions
    if placed:
        y += BLOCK_GAP
        for x, row, w, emoji_img, label in placed:
            top = y + row * (REACTION_HEIGHT + REACTION_GAP)
            left = PADDING + x
            draw.rounded_rectangle(
                (left, top, left + w, top + REACTION_HEIGHT), radius=8, fill=theme.pill_bg
            )
            image.paste(
                emoji_img,
                (left + REACTION_PAD, top + (REACTION_HEIGHT - EMOJI_SIZE) // 2),
                emoji_img,
            )
            draw.text(
                (left + REACTION_PAD + EMOJI_SIZE + 8, top + REACTION_HEIGHT // 2),
                label,
                font=count_font,
                fill=theme.pill_fg,
                anchor="lm",
            )

    buffer = io.BytesIO()
    if as_gif:
        image.convert("P", palette=Image.ADAPTIVE, colors=256).save(buffer, format="GIF")
        ext = "gif"
    else:
        image.save(buffer, format="PNG")
        ext = "png"
    buffer.seek(0)
    return buffer, ext


# ---------------------------------------------------------------------- cog


class MessageCog(commands.Cog):
    """Slash command group: /generate discord message"""

    generate = app_commands.Group(name="generate", description="Generate images")
    discord_group = app_commands.Group(
        name="discord", description="Discord-style images", parent=generate
    )

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.session: aiohttp.ClientSession | None = None

    async def cog_load(self) -> None:
        self.session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10))

    async def cog_unload(self) -> None:
        if self.session is not None:
            await self.session.close()
            self.session = None

    async def _fetch_bytes(self, url: str, limit: int) -> bytes:
        """Download a URL fully, enforcing a size cap."""
        assert self.session is not None
        async with self.session.get(url) as resp:
            resp.raise_for_status()
            # StreamReader.read(n) may return only the first chunk (or
            # nothing), so read the whole body in a loop with a size cap.
            data = bytearray()
            async for chunk in resp.content.iter_chunked(64 * 1024):
                data.extend(chunk)
                if len(data) > limit:
                    raise ValueError("Download is too large.")
        if not data:
            raise ValueError("Download was empty.")
        return bytes(data)

    async def _fetch_avatar(self, user: discord.abc.User) -> bytes:
        url = user.display_avatar.replace(size=256, format="png").url
        return await self._fetch_bytes(url, MAX_AVATAR_BYTES)

    async def _fetch_emoji(self, emoji: str, count: int) -> tuple[bytes, int] | None:
        try:
            return await self._fetch_bytes(emoji_url(emoji), MAX_EMOJI_BYTES), count
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError):
            log.warning("Could not fetch emoji %r", emoji)
            return None

    @discord_group.command(
        name="message",
        description="Generate a Discord-style message image for a user.",
    )
    @app_commands.describe(
        user="The user the message is shown as",
        text="The message text to render",
        theme="Card theme (default: dark)",
        display_font="Font used on the card (default: Inter)",
        image="An image to show under the message",
        timestamp="Show the timestamp (default: yes)",
        mention_highlight="Highlight @mentions in the text (default: yes)",
        reactions="Reactions, e.g. '👍 3 🔥 2' (custom emoji supported)",
        togif="Send the result as a GIF instead of a PNG",
        use_username="Show the username instead of the display name",
        hide_clantags="Hide the user's clan tag",
    )
    async def message(
        self,
        interaction: discord.Interaction,
        user: discord.User,
        text: app_commands.Range[str, 1, MAX_TEXT_LENGTH],
        theme: Literal["dark", "light"] = "dark",
        display_font: FontName = "Inter",
        image: discord.Attachment | None = None,
        timestamp: bool = True,
        mention_highlight: bool = True,
        reactions: str | None = None,
        togif: bool = False,
        use_username: bool = False,
        hide_clantags: bool = False,
    ) -> None:
        await interaction.response.defer(thinking=True)

        if image is not None:
            if not (image.content_type or "").startswith("image/"):
                await interaction.followup.send("The attachment must be an image.", ephemeral=True)
                return
            if image.size > MAX_IMAGE_BYTES:
                await interaction.followup.send(
                    "That image is too large (max 8 MB).", ephemeral=True
                )
                return

        try:
            avatar_bytes = await self._fetch_avatar(user)
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError):
            await interaction.followup.send(
                "Couldn't download that user's avatar. Please try again.",
                ephemeral=True,
            )
            return

        attachment_bytes = None
        if image is not None:
            try:
                attachment_bytes = await self._fetch_bytes(image.url, MAX_IMAGE_BYTES)
            except (aiohttp.ClientError, asyncio.TimeoutError, ValueError):
                await interaction.followup.send(
                    "Couldn't download the attached image. Please try again.",
                    ephemeral=True,
                )
                return

        parsed = parse_reactions((reactions or "")[:200])
        fetched = await asyncio.gather(*(self._fetch_emoji(e, c) for e, c in parsed))
        reaction_images = [r for r in fetched if r is not None]

        name = user.name if use_username else user.display_name
        clan_tag = None
        if not hide_clantags:
            clan_tag = getattr(getattr(user, "primary_guild", None), "tag", None)
        stamp = discord.utils.utcnow().strftime("%Y-%m-%d %H:%M UTC") if timestamp else None

        try:
            buffer, ext = await asyncio.to_thread(
                render_quote_card,
                avatar_bytes=avatar_bytes,
                name=name,
                clan_tag=clan_tag,
                timestamp=stamp,
                text=text,
                theme_name=theme,
                font_name=display_font,
                attachment_bytes=attachment_bytes,
                reactions=reaction_images,
                mention_highlight=mention_highlight,
                as_gif=togif,
            )
        except Exception:
            await interaction.followup.send(
                "Something went wrong while rendering the image.",
                ephemeral=True,
            )
            raise

        await interaction.followup.send(file=discord.File(buffer, filename=f"message.{ext}"))


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(MessageCog(bot))
