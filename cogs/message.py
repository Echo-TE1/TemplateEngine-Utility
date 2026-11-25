"""MessageCog: renders a Discord-client-style chat card for a selected user.

Command: /generate discord message

Rendering is done locally with Pillow at 2x resolution and downsampled with
LANCZOS. The bot replies with its own message and attaches the generated
image (PNG or GIF). No webhooks, no impersonation of accounts.
"""

from __future__ import annotations

import asyncio
import io
import logging
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Literal, Optional, Union

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands
from PIL import Image, ImageChops, ImageDraw, ImageFont, ImageSequence

log = logging.getLogger(__name__)

RGB = tuple[int, int, int]

# ---------------------------------------------------------------- constants

S = 2  # supersampling factor: draw at 2x, downsample with LANCZOS


def px(value: float) -> int:
    """CSS pixels -> supersampled pixels."""
    return int(round(value * S))


# Layout (CSS px)
PAD = 16  # outer padding on all sides
AVATAR = 40
AVATAR_GAP = 16  # gap between avatar and text column
AVATAR_TOP_OFFSET = 2
LINE_H = 22  # header and body line height
MAX_COL = 640  # max width of the text column
BODY_SIZE = 15
NAME_SIZE = 16
TIME_SIZE = 12
ATTACH_MAX_W = 400
ATTACH_MAX_H = 300
ATTACH_RADIUS = 8
ATTACH_GAP = 4
REACTION_H = 24
REACTION_PAD = 6
REACTION_EMOJI = 16
REACTION_GAP = 4
REACTION_RADIUS = 8

MAX_TEXT_LENGTH = 1000
MAX_AVATAR_BYTES = 8 * 1024 * 1024
MAX_IMAGE_BYTES = 8 * 1024 * 1024
MAX_REACTIONS = 12
MAX_GIF_FRAMES = 30
MASK_SUPERSAMPLE = 4

FONT_DIR = Path(__file__).resolve().parent.parent / "fonts"
UI_FONT = "OpenSans-Variable.ttf"  # Noto Sans-style stand-in for gg sans
EMOJI_FONT = "NotoColorEmoji.ttf"

# display_font choice -> (bundled file, variable-font style or None)
DISPLAY_FONTS: dict[str, tuple[str, Optional[str]]] = {
    "default": (UI_FONT, "Bold"),
    "sakura": ("NothingYouCouldDo-Regular.ttf", None),
    "jellybean": ("ComicRelief-Bold.ttf", None),
    "modern": ("Outfit-Bold.ttf", None),
    "medieval": ("KaTeX_Fraktur-Bold.ttf", None),
    "8bit": ("Silkscreen-Regular.ttf", None),
    "vampyre": ("Gloock-Regular.ttf", None),
}
ThemeName = Literal["dark", "ash", "onyx", "light"]
FontChoice = Literal["default", "sakura", "jellybean", "modern", "medieval", "8bit", "vampyre"]

MENTION_RE = re.compile(r"(@(?:everyone|here|[\w.]{1,32}))")
REACTION_TOKEN_RE = re.compile(r"[^\s,]+")

AMBER: RGB = (0xF0, 0xB2, 0x32)
BLURPLE: RGB = (0x58, 0x65, 0xF2)


def _hex(value: str) -> RGB:
    value = value.lstrip("#")
    return int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)


def _blend(fg: RGB, bg: RGB, alpha: float) -> RGB:
    return tuple(int(round(f * alpha + b * (1 - alpha))) for f, b in zip(fg, bg))  # type: ignore[return-value]


@dataclass(frozen=True)
class Theme:
    bg: RGB
    name_default: RGB
    body: RGB
    timestamp: RGB
    mention_bg: RGB
    mention_fg: RGB
    pill_bg: RGB
    pill_border: RGB
    pill_count: RGB
    chip_bg: RGB  # clan tag chip
    highlight_bg: RGB  # row tint for mention_highlight


def _dark(bg: str, highlight: Optional[str] = None) -> Theme:
    bg_rgb = _hex(bg)
    return Theme(
        bg=bg_rgb,
        name_default=_hex("#F2F3F5"),
        body=_hex("#DBDEE1"),
        timestamp=_hex("#949BA4"),
        mention_bg=_hex("#3C4270"),
        mention_fg=_hex("#C9CDFB"),
        pill_bg=_hex("#2B2D31"),
        pill_border=_hex("#3F4147"),
        pill_count=_hex("#B5BAC1"),
        chip_bg=_blend((255, 255, 255), bg_rgb, 0.08),
        highlight_bg=_hex(highlight) if highlight else _blend(AMBER, bg_rgb, 0.10),
    )


THEMES: dict[str, Theme] = {
    "dark": _dark("#313338", highlight="#433B2C"),
    "ash": _dark("#323339"),
    "onyx": _dark("#070709"),
    "light": Theme(
        bg=_hex("#FFFFFF"),
        name_default=_hex("#060607"),
        body=_hex("#313338"),
        timestamp=_hex("#5C5E66"),
        mention_bg=_hex("#E6E8FD"),
        mention_fg=_hex("#505CDC"),
        pill_bg=_hex("#F2F3F5"),
        pill_border=_hex("#E3E5E8"),
        pill_count=_hex("#4E5058"),
        chip_bg=_blend((0, 0, 0), (255, 255, 255), 0.06),
        highlight_bg=_blend(AMBER, (255, 255, 255), 0.10),
    ),
}


# ------------------------------------------------------------------ helpers


@lru_cache(maxsize=128)
def _font(file: str, variation: Optional[str], size: int) -> ImageFont.FreeTypeFont:
    """Load a bundled font. Never falls back to ImageFont.load_default()."""
    for name in (file, UI_FONT, "DejaVuSans.ttf"):
        try:
            font = ImageFont.truetype(str(FONT_DIR / name), size)
        except OSError:
            continue
        if variation and name == UI_FONT:
            try:
                font.set_variation_by_name(variation)
            except Exception:  # variable-font support missing: keep default weight
                pass
        return font
    raise RuntimeError(f"No usable fonts found in {FONT_DIR}")


def _ui(size_css: float, style: str = "Regular") -> ImageFont.FreeTypeFont:
    return _font(UI_FONT, style, px(size_css))


def _baseline(font: ImageFont.FreeTypeFont, top: int, line_h: int) -> int:
    """Baseline y that vertically centres the font inside a line box."""
    ascent, descent = font.getmetrics()
    return top + (line_h - (ascent + descent)) // 2 + ascent


def _wrap_text(measure: ImageDraw.ImageDraw, text: str, font, max_width: int) -> list[str]:
    """Word-wrap preserving newlines; break words that are wider than the column."""
    lines: list[str] = []
    for paragraph in text.split("\n"):
        if not paragraph.strip():
            lines.append("")
            continue
        current = ""
        for word in paragraph.split(" "):
            candidate = word if not current else f"{current} {word}"
            if measure.textlength(candidate, font=font) <= max_width:
                current = candidate
                continue
            if current:
                lines.append(current)
                current = ""
            while measure.textlength(word, font=font) > max_width:
                cut = len(word)
                while cut > 1 and measure.textlength(word[:cut], font=font) > max_width:
                    cut -= 1
                lines.append(word[:cut])
                word = word[cut:]
            current = word
        lines.append(current)
    return lines


def _rounded_mask(size: tuple[int, int], radius: int) -> Image.Image:
    big = (size[0] * MASK_SUPERSAMPLE, size[1] * MASK_SUPERSAMPLE)
    mask = Image.new("L", big, 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        (0, 0, big[0] - 1, big[1] - 1), radius=radius * MASK_SUPERSAMPLE, fill=255
    )
    return mask.resize(size, Image.LANCZOS)


def _circular_avatar(avatar: Image.Image, size: int) -> Image.Image:
    avatar = avatar.convert("RGBA").resize((size, size), Image.LANCZOS)
    big = size * MASK_SUPERSAMPLE
    mask = Image.new("L", (big, big), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, big - 1, big - 1), fill=255)
    mask = mask.resize((size, size), Image.LANCZOS)
    out = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    out.paste(avatar, (0, 0), mask)
    return out


@lru_cache(maxsize=256)
def _emoji_image(emoji: str, size: int) -> Optional[Image.Image]:
    """Render a Unicode emoji with the bundled Noto Color Emoji font."""
    try:
        font = ImageFont.truetype(str(FONT_DIR / EMOJI_FONT), 109)
    except OSError:
        return None
    canvas = Image.new("RGBA", (136, 128), (0, 0, 0, 0))
    try:
        ImageDraw.Draw(canvas).text((0, 0), emoji, font=font, embedded_color=True)
    except Exception:
        return None
    if canvas.getbbox() is None:
        return None
    height = max(1, int(round(size * 128 / 136)))
    scaled = canvas.resize((size, height), Image.LANCZOS)
    square = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    square.paste(scaled, (0, (size - height) // 2))
    return square


def parse_reactions(raw: Optional[str]) -> list[tuple[str, int]]:
    """'💀 4, 🔥 2' -> [('💀', 4), ('🔥', 2)]. A missing count means 1.
    Only Unicode emoji are supported; plain words are ignored."""
    out: list[tuple[str, int]] = []
    if not raw:
        return out
    for token in REACTION_TOKEN_RE.findall(raw):
        if token.isdigit():
            if out:
                out[-1] = (out[-1][0], max(1, min(int(token), 999999)))
        elif any(ord(c) > 127 for c in token):
            out.append((token, 1))
        if len(out) >= MAX_REACTIONS:
            break
    return out


def _decode_attachment(data: bytes, animated: bool) -> tuple[list[Image.Image], list[int]]:
    """Return RGBA frames and per-frame durations (ms)."""
    frames: list[Image.Image] = []
    durations: list[int] = []
    with Image.open(io.BytesIO(data)) as im:
        if animated and getattr(im, "is_animated", False):
            for i, frame in enumerate(ImageSequence.Iterator(im)):
                if i >= MAX_GIF_FRAMES:
                    break
                frames.append(frame.convert("RGBA"))
                durations.append(max(20, int(frame.info.get("duration", 100) or 100)))
        else:
            frames.append(im.convert("RGBA"))
            durations.append(0)
    return frames, durations


def default_timestamp(tz_name: Optional[str] = None) -> str:
    """'Today at 8:44 PM' in CARD_TIMEZONE (default UTC)."""
    tz_name = tz_name or os.environ.get("CARD_TIMEZONE", "UTC")
    try:
        from zoneinfo import ZoneInfo

        now = datetime.now(ZoneInfo(tz_name))
    except Exception:
        now = datetime.now(timezone.utc)
    return f"Today at {now.strftime('%I:%M %p').lstrip('0')}"


# ----------------------------------------------------------------- renderer


def render_message_card(
    *,
    avatar_bytes: bytes,
    name: str,
    name_color: Optional[RGB],
    clan_tag: Optional[str],
    is_app: bool,
    timestamp: str,
    text: str,
    theme_name: str = "dark",
    font_key: str = "default",
    attachment_bytes: Optional[bytes] = None,
    reactions: Optional[list[tuple[str, int]]] = None,
    row_highlight: bool = False,
    as_gif: bool = False,
) -> tuple[io.BytesIO, str]:
    """Render the card at 2x, downsample with LANCZOS. Returns (buffer, ext)."""
    theme = THEMES.get(theme_name, THEMES["dark"])

    name_file, name_style = DISPLAY_FONTS.get(font_key, DISPLAY_FONTS["default"])
    name_font = _font(name_file, name_style, px(NAME_SIZE))
    body_font = _ui(BODY_SIZE)
    time_font = _ui(TIME_SIZE)
    chip_font = _ui(12, "Bold")
    app_font = _ui(10, "Bold")
    count_font = _ui(14, "SemiBold")

    measure = ImageDraw.Draw(Image.new("RGB", (1, 1)))

    def width(value: str, font) -> int:
        return int(round(measure.textlength(value, font=font)))

    text_x = px(PAD + AVATAR + AVATAR_GAP)
    max_col = px(MAX_COL)
    line_h = px(LINE_H)
    resolved_name_color = name_color or theme.name_default

    # ---- header pieces: name, [clan tag], [APP badge], timestamp
    chip_text = (clan_tag or "").strip()[:4] or None
    chip_w = width(chip_text, chip_font) + px(10) if chip_text else 0
    chip_h = px(16)
    app_w = width("APP", app_font) + px(8)
    app_h = px(15)
    time_w = width(timestamp, time_font)

    extras = (px(4) + chip_w if chip_text else 0) + (px(4) + app_w if is_app else 0)
    time_block = px(8) + time_w
    name_max = max(px(40), max_col - extras - time_block)

    shown_name = name
    if width(shown_name, name_font) > name_max:
        while len(shown_name) > 1 and width(shown_name + "…", name_font) > name_max:
            shown_name = shown_name[:-1]
        shown_name += "…"
    name_w = width(shown_name, name_font)
    header_w = name_w + extras + time_block

    # ---- body
    body_lines = _wrap_text(measure, text, body_font, max_col)
    body_w = max((width(line, body_font) for line in body_lines), default=0)

    # ---- attachment
    frames: list[Image.Image] = []
    durations: list[int] = []
    att_w = att_h = 0
    if attachment_bytes:
        frames, durations = _decode_attachment(attachment_bytes, animated=as_gif)
        src_w, src_h = frames[0].size
        w1 = min(src_w, ATTACH_MAX_W)
        h1 = src_h * w1 / src_w
        if h1 > ATTACH_MAX_H:
            w1 = w1 * ATTACH_MAX_H / h1
            h1 = ATTACH_MAX_H
        att_w, att_h = max(1, px(w1)), max(1, px(h1))
        att_mask = _rounded_mask((att_w, att_h), px(ATTACH_RADIUS))
        prepared = []
        for frame in frames:
            resized = frame.resize((att_w, att_h), Image.LANCZOS)
            resized.putalpha(ImageChops.multiply(resized.getchannel("A"), att_mask))
            prepared.append(resized)
        frames = prepared

    # ---- reactions (wrapped rows of pills)
    pill_items: list[tuple[int, int, int, Image.Image, str]] = []  # x, row, w, emoji, label
    react_w = 0
    rows = 0
    if reactions:
        x = 0
        for emoji, count in reactions:
            emoji_img = _emoji_image(emoji, px(REACTION_EMOJI))
            if emoji_img is None:
                continue
            label = str(count)
            w = px(REACTION_PAD) * 2 + px(REACTION_EMOJI) + px(6) + width(label, count_font)
            if x and x + w > max_col:
                x = 0
                rows += 1
            pill_items.append((x, rows, w, emoji_img, label))
            react_w = max(react_w, x + w)
            x += w + px(REACTION_GAP)
        if pill_items:
            rows += 1
    react_h = rows * px(REACTION_H) + max(0, rows - 1) * px(REACTION_GAP)

    # ---- card size
    content_w = min(max_col, max(header_w, body_w, att_w, react_w))
    card_w = text_x + content_w + px(PAD)

    y = px(PAD)
    header_top = y
    y += line_h
    body_top = y
    y += line_h * len(body_lines)
    att_y = react_y = 0
    if frames:
        y += px(ATTACH_GAP)
        att_y = y
        y += att_h
    if pill_items:
        y += px(REACTION_GAP)
        react_y = y
        y += react_h
    card_h = max(y, px(PAD + AVATAR_TOP_OFFSET + AVATAR)) + px(PAD)

    # make both dimensions divisible by S so the downsample is exact
    card_w += card_w % S
    card_h += card_h % S

    # ---- draw (2x)
    base = Image.new("RGB", (card_w, card_h), theme.highlight_bg if row_highlight else theme.bg)
    draw = ImageDraw.Draw(base)

    if row_highlight:
        draw.rectangle((0, 0, px(3) - 1, card_h), fill=AMBER)

    # avatar
    try:
        with Image.open(io.BytesIO(avatar_bytes)) as raw:
            raw.load()
            avatar = _circular_avatar(raw, px(AVATAR))
    except (OSError, ValueError, Image.DecompressionBombError):
        avatar = _circular_avatar(Image.new("RGB", (8, 8), (114, 118, 125)), px(AVATAR))
    base.paste(avatar, (px(PAD), px(PAD + AVATAR_TOP_OFFSET)), avatar)

    # header row, baseline-aligned
    baseline = _baseline(name_font, header_top, line_h)
    draw.text((text_x, baseline), shown_name, font=name_font, fill=resolved_name_color, anchor="ls")
    x = text_x + name_w
    center_y = header_top + line_h // 2

    if chip_text:
        x += px(4)
        top = center_y - chip_h // 2
        draw.rounded_rectangle((x, top, x + chip_w, top + chip_h), radius=px(4), fill=theme.chip_bg)
        draw.text(
            (x + chip_w // 2, _baseline(chip_font, top, chip_h)),
            chip_text,
            font=chip_font,
            fill=resolved_name_color,
            anchor="ms",
        )
        x += chip_w
    if is_app:
        x += px(4)
        top = center_y - app_h // 2 + px(0.5)
        draw.rounded_rectangle((x, top, x + app_w, top + app_h), radius=px(3), fill=BLURPLE)
        draw.text(
            (x + app_w // 2, _baseline(app_font, top, app_h)),
            "APP",
            font=app_font,
            fill=(255, 255, 255),
            anchor="ms",
        )
        x += app_w
    x += px(8)
    draw.text((x, baseline), timestamp, font=time_font, fill=theme.timestamp, anchor="ls")

    # body lines, with inline @mention pills
    for i, line in enumerate(body_lines):
        if not line:
            continue
        top = body_top + i * line_h
        line_baseline = _baseline(body_font, top, line_h)
        cursor = text_x
        for j, part in enumerate(MENTION_RE.split(line)):
            if not part:
                continue
            part_w = measure.textlength(part, font=body_font)
            if j % 2 == 1:
                draw.rounded_rectangle(
                    (cursor, top + px(1), cursor + part_w + px(4), top + line_h - px(1)),
                    radius=px(3),
                    fill=theme.mention_bg,
                )
                draw.text((cursor + px(2), line_baseline), part, font=body_font, fill=theme.mention_fg, anchor="ls")
                cursor += part_w + px(4)
            else:
                draw.text((cursor, line_baseline), part, font=body_font, fill=theme.body, anchor="ls")
                cursor += part_w

    # reaction pills
    for px_x, row, w, emoji_img, label in pill_items:
        left = text_x + px_x
        top = react_y + row * (px(REACTION_H) + px(REACTION_GAP))
        draw.rounded_rectangle(
            (left, top, left + w, top + px(REACTION_H)),
            radius=px(REACTION_RADIUS),
            fill=theme.pill_bg,
            outline=theme.pill_border,
            width=px(1),
        )
        base.paste(
            emoji_img,
            (left + px(REACTION_PAD), top + (px(REACTION_H) - px(REACTION_EMOJI)) // 2),
            emoji_img,
        )
        draw.text(
            (left + px(REACTION_PAD) + px(REACTION_EMOJI) + px(6), top + px(REACTION_H) // 2),
            label,
            font=count_font,
            fill=theme.pill_count,
            anchor="lm",
        )

    # ---- downsample (+ attachment frames) and encode
    out_size = (card_w // S, card_h // S)

    def finish(frame_img: Optional[Image.Image]) -> Image.Image:
        canvas = base.copy() if frame_img is not None else base
        if frame_img is not None:
            canvas.paste(frame_img, (text_x, att_y), frame_img)
        return canvas.resize(out_size, Image.LANCZOS)

    buffer = io.BytesIO()
    if as_gif:
        outputs = [finish(f) for f in frames] if frames else [finish(None)]
        paletted = [im.convert("P", palette=Image.ADAPTIVE, colors=256) for im in outputs]
        if len(paletted) > 1:
            paletted[0].save(
                buffer,
                format="GIF",
                save_all=True,
                append_images=paletted[1:],
                duration=durations,
                loop=0,
                disposal=2,
            )
        else:
            paletted[0].save(buffer, format="GIF")
        ext = "gif"
    else:
        finish(frames[0] if frames else None).save(buffer, format="PNG", optimize=True)
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
        self.session: Optional[aiohttp.ClientSession] = None

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

    @discord_group.command(
        name="message",
        description="Generate a Discord-style message image.",
    )
    @app_commands.describe(
        text="The message content",
        user="Who the message is shown as (defaults to you)",
        theme="Card theme (default: dark)",
        display_font="Display name font (default: default)",
        image="An image to show under the message",
        timestamp="Custom timestamp text (default: Today at <current time>)",
        mention_highlight="Highlight the row like a mention (amber tint and bar)",
        reactions="Reactions, e.g. '💀 4, 🔥 2'",
        togif="Output a GIF instead of a PNG",
        use_username="Show the username instead of the display name",
        hide_clantags="Hide the clan tag next to the name",
    )
    async def message(
        self,
        interaction: discord.Interaction,
        text: app_commands.Range[str, 1, MAX_TEXT_LENGTH],
        user: Optional[Union[discord.Member, discord.User]] = None,
        theme: ThemeName = "dark",
        display_font: FontChoice = "default",
        image: Optional[discord.Attachment] = None,
        timestamp: Optional[str] = None,
        mention_highlight: bool = False,
        reactions: Optional[str] = None,
        togif: bool = False,
        use_username: bool = False,
        hide_clantags: bool = False,
    ) -> None:
        await interaction.response.defer(thinking=True)
        target = user or interaction.user

        if image is not None:
            if not (image.content_type or "").startswith("image/"):
                await interaction.followup.send("The attachment must be an image.", ephemeral=True)
                return
            if image.size > MAX_IMAGE_BYTES:
                await interaction.followup.send("That image is too large (max 8 MB).", ephemeral=True)
                return

        try:
            avatar_url = target.display_avatar.replace(size=128, format="png").url
            avatar_bytes = await self._fetch_bytes(avatar_url, MAX_AVATAR_BYTES)
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError):
            await interaction.followup.send(
                "Couldn't download that user's avatar. Please try again.", ephemeral=True
            )
            return

        attachment_bytes = None
        if image is not None:
            try:
                attachment_bytes = await self._fetch_bytes(image.url, MAX_IMAGE_BYTES)
            except (aiohttp.ClientError, asyncio.TimeoutError, ValueError):
                await interaction.followup.send(
                    "Couldn't download the attached image. Please try again.", ephemeral=True
                )
                return

        colour = getattr(target, "color", None)
        name_color: Optional[RGB] = None
        if colour is not None and getattr(colour, "value", 0) != 0:
            name_color = (colour.r, colour.g, colour.b)

        clan_tag = None
        if not hide_clantags:
            clan_tag = getattr(getattr(target, "primary_guild", None), "tag", None)

        try:
            buffer, ext = await asyncio.to_thread(
                render_message_card,
                avatar_bytes=avatar_bytes,
                name=target.name if use_username else target.display_name,
                name_color=name_color,
                clan_tag=clan_tag,
                is_app=bool(getattr(target, "bot", False)),
                timestamp=(timestamp or "").strip()[:60] or default_timestamp(),
                text=text,
                theme_name=theme,
                font_key=display_font,
                attachment_bytes=attachment_bytes,
                reactions=parse_reactions((reactions or "")[:300]),
                row_highlight=mention_highlight,
                as_gif=togif,
            )
        except Exception:
            await interaction.followup.send(
                "Something went wrong while rendering the image.", ephemeral=True
            )
            raise

        await interaction.followup.send(file=discord.File(buffer, filename=f"message.{ext}"))


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(MessageCog(bot))
