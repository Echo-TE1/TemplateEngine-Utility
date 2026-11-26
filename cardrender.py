"""Discord-style message card renderer (Pillow only, no Discord imports).

The card is drawn at 2x and downsampled with LANCZOS. Supported content:
markdown (bold, italic, underline, strikethrough, inline code, code blocks,
block quotes, spoilers), links, @mention / #channel pills, Unicode and custom
emoji (inline and as reactions), Japanese text, an attached image, a clan tag
chip and an APP badge.
"""

from __future__ import annotations

import io
import re
import threading
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Optional

from PIL import Image, ImageChops, ImageDraw, ImageFont, ImageSequence, features

RGB = tuple[int, int, int]
LANCZOS = Image.Resampling.LANCZOS

# ---------------------------------------------------------------- constants

S = 2  # supersampling factor


def px(value: float) -> int:
    """CSS pixels -> supersampled pixels."""
    return int(round(value * S))


PAD = 16
AVATAR = 40
AVATAR_GAP = 16
AVATAR_TOP_OFFSET = 2
LINE_H = 22
MAX_COL = 640
BODY_SIZE = 16
NAME_SIZE = 16
TIME_SIZE = 12
CODE_SIZE = 13.5
JUMBO_SIZE = 48
JUMBO_LINE_H = 54
JUMBO_MAX = 27
EMOJI_SIZE = 22
ATTACH_MAX_W = 400
ATTACH_MAX_H = 300
ATTACH_RADIUS = 8
ATTACH_GAP = 4
CODE_MARGIN = 4  # space above/below a code block
REACTION_H = 24
REACTION_PAD = 6
REACTION_EMOJI = 16
REACTION_GAP = 4
REACTION_RADIUS = 8
QUOTE_INDENT = 16
MAX_GIF_FRAMES = 30
MASK_SUPERSAMPLE = 4
MAX_REACTIONS = 12

FONT_DIR = Path(__file__).resolve().parent / "fonts"
UI_FONT = "OpenSans-Variable.ttf"
UI_ITALIC = "OpenSans-Italic-Variable.ttf"
CJK_REGULAR = "NotoSansJP-Regular.otf"
CJK_BOLD = "NotoSansJP-Bold.otf"
MONO_FONT = "DejaVuSansMono.ttf"
EMOJI_FONT = "NotoColorEmoji.ttf"
FALLBACK_FONT = "DejaVuSans.ttf"

AMBER: RGB = (0xF0, 0xB2, 0x32)
BLURPLE: RGB = (0x58, 0x65, 0xF2)

_RENDER_LOCK = threading.Lock()  # FreeType faces are shared and not thread-safe


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
    link: RGB
    mention_bg: RGB
    mention_fg: RGB
    pill_bg: RGB
    pill_border: RGB
    pill_count: RGB
    clan_bg: RGB
    clan_fg: RGB
    highlight_bg: RGB
    code_bg: RGB
    code_border: RGB
    quote_bar: RGB
    spoiler_bg: RGB
    embed_bg: RGB
    embed_border: RGB


def _dark(bg: str, highlight: Optional[str] = None) -> Theme:
    bg_rgb = _hex(bg)
    return Theme(
        bg=bg_rgb,
        name_default=_hex("#F2F3F5"),
        body=_hex("#DBDEE1"),
        timestamp=_hex("#949BA4"),
        link=_hex("#00A8FC"),
        mention_bg=_hex("#3C4270"),
        mention_fg=_hex("#C9CDFB"),
        pill_bg=_hex("#2B2D31"),
        pill_border=_hex("#3F4147"),
        pill_count=_hex("#B5BAC1"),
        clan_bg=_blend((78, 80, 88), bg_rgb, 0.65),
        clan_fg=_hex("#DBDEE1"),
        highlight_bg=_hex(highlight) if highlight else _blend(AMBER, bg_rgb, 0.10),
        code_bg=_hex("#2B2D31"),
        code_border=_hex("#1E1F22"),
        quote_bar=_hex("#4E5058"),
        spoiler_bg=_hex("#1E1F22"),
        embed_bg=_hex("#2B2D31"),
        embed_border=_hex("#1E1F22"),
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
        link=_hex("#006CE7"),
        mention_bg=_hex("#E6E8FD"),
        mention_fg=_hex("#505CDC"),
        pill_bg=_hex("#F2F3F5"),
        pill_border=_hex("#E3E5E8"),
        pill_count=_hex("#4E5058"),
        clan_bg=_hex("#E3E5E8"),
        clan_fg=_hex("#4E5058"),
        highlight_bg=_blend(AMBER, (255, 255, 255), 0.10),
        code_bg=_hex("#F2F3F5"),
        code_border=_hex("#E3E5E8"),
        quote_bar=_hex("#C4C9CE"),
        spoiler_bg=_hex("#C4C9CE"),
        embed_bg=_hex("#F2F3F5"),
        embed_border=_hex("#E3E5E8"),
    ),
}

# ------------------------------------------------------------------ fonts


@lru_cache(maxsize=256)
def _load(file: str, size: int, variation: Optional[str] = None) -> ImageFont.FreeTypeFont:
    """Load a bundled font (never ImageFont.load_default())."""
    for name in (file, UI_FONT, FALLBACK_FONT):
        try:
            font = ImageFont.truetype(str(FONT_DIR / name), size)
        except OSError:
            continue
        if variation and name == file and file in (UI_FONT, UI_ITALIC):
            try:
                font.set_variation_by_name(variation)
            except Exception:  # variable fonts unsupported: keep the default weight
                pass
        return font
    raise RuntimeError(f"No usable fonts found in {FONT_DIR}")


def font_for(
    script: str,
    size_css: float,
    weight: str = "Regular",
    italic: bool = False,
    code: bool = False,
) -> ImageFont.FreeTypeFont:
    size = px(size_css)
    if script == "cjk":
        return _load(CJK_BOLD if weight in ("Bold", "SemiBold") else CJK_REGULAR, size)
    if code:
        return _load(MONO_FONT, size)
    if italic:
        return _load(UI_ITALIC, size, "Italic" if weight == "Regular" else f"{weight} Italic")
    return _load(UI_FONT, size, weight)


def _baseline(font: ImageFont.FreeTypeFont, top: int, line_h: int) -> int:
    ascent, descent = font.getmetrics()
    return top + (line_h - (ascent + descent)) // 2 + ascent


_MEASURE = ImageDraw.Draw(Image.new("RGB", (1, 1)))


def _tlen(text: str, font) -> int:
    return int(round(_MEASURE.textlength(text, font=font)))


# --------------------------------------------------------------- text model

_BMP_SYMBOLS = (
    "©®‼⁉™ℹ↔-↙↩↪⌚⌛⌨⏏"
    "⏩-⏳⏸-⏺Ⓜ▪▫▶◀◻-◾☀-➿"
    "⤴⤵⬅-⬇⬛⬜⭐⭕〰〽㊗㊙"
)
_DEFAULT_EMOJI_BMP = (
    "⌚⌛⏩-⏬⏰⏳◽◾☔☕♈-♓♿⚓"
    "⚡⚪⚫⚽⚾⛄⛅⛎⛔⛪⛲⛳⛵⛺⛽"
    "✅✊✋✨❌❎❓-❕❗➕-➗➰➿"
    "⬛⬜⭐⭕"
)
_SKIN = "\U0001f3fb-\U0001f3ff"
_PICTO = "\U0001f300-\U0001faff"
EMOJI_RE = re.compile(
    "(?:"
    "[\U0001f1e6-\U0001f1ff]{2}"
    "|[0-9#*]️?⃣"
    f"|(?:[{_PICTO}]|[{_BMP_SYMBOLS}]️|[{_DEFAULT_EMOJI_BMP}])"
    f"️?[{_SKIN}]?"
    f"(?:‍(?:[{_PICTO}]|[♀♂⚕⚖✈❤☠⚧])️?[{_SKIN}]?)*"
    ")"
)
CUSTOM_EMOJI_RE = re.compile(r"<a?:(\w{2,32}):(\d{15,25})>")
URL_RE = re.compile(r"https?://[^\s<>]+")
MENTION_RE = re.compile(r"(?<![^ \t\r\n(])[@#][^ \t\r\n]+?(?=[.,!?;:)]*(?:[ \t\r\n]|$))")
CJK_CHARS = "⺀-⿟　-ヿㇰ-ㇿ㐀-䶿一-鿿豈-﫿＀-￯"
CJK_RE = re.compile(f"[{CJK_CHARS}]")
FENCE_RE = re.compile(r"```(?:[\w+-]*\n)?(.*?)```", re.DOTALL)

_HAS_RAQM = features.check("raqm")


@dataclass
class Atom:
    kind: str  # "word" | "space" | "emoji"
    text: str = ""
    script: str = "latin"
    size: float = BODY_SIZE
    weight: str = "Regular"
    italic: bool = False
    underline: bool = False
    strike: bool = False
    code: bool = False
    link: bool = False
    spoiler: bool = False
    mention: bool = False
    muted: bool = False
    emoji_key: str = ""  # unicode sequence, or "c:<id>" for a custom emoji
    emoji_px: int = 0
    pad_left: int = 0
    pad_right: int = 0
    width: int = 0
    font: Optional[ImageFont.FreeTypeFont] = field(default=None, repr=False)

    def measure(self) -> "Atom":
        if self.kind == "emoji":
            self.width = self.emoji_px
        else:
            self.font = font_for(self.script, self.size, self.weight, self.italic, self.code)
            self.width = _tlen(self.text, self.font)
            if self.mention:
                self.pad_left = self.pad_right = px(2)
        self.width += self.pad_left + self.pad_right
        return self


def _script(ch: str) -> str:
    return "cjk" if CJK_RE.match(ch) else "latin"


def _emoji_key(seq: str) -> str:
    """Without raqm, ZWJ sequences cannot be shaped, so use their first emoji."""
    if not _HAS_RAQM and "‍" in seq:
        seq = seq.split("‍")[0]
    return seq


def atomize(text: str, *, base: Atom) -> list[Atom]:
    """Split text into word / space / emoji atoms sharing base's style.
    CJK characters become one atom each (they can wrap anywhere)."""
    atoms: list[Atom] = []
    i, n = 0, len(text)
    word: list[str] = []

    def style(**kw) -> Atom:
        data = {k: getattr(base, k) for k in (
            "size", "weight", "italic", "underline", "strike", "code", "link", "spoiler", "mention", "muted"
        )}
        data.update(kw)
        return Atom(**data)

    def flush() -> None:
        if word:
            atoms.append(style(kind="word", text="".join(word)).measure())
            word.clear()

    while i < n:
        ch = text[i]
        custom = CUSTOM_EMOJI_RE.match(text, i)
        if custom:
            flush()
            atoms.append(style(kind="emoji", emoji_key=f"c:{custom.group(2)}", text=custom.group(0),
                               emoji_px=px(EMOJI_SIZE)).measure())
            i = custom.end()
            continue
        if not base.code:
            emoji = EMOJI_RE.match(text, i)
            if emoji:
                flush()
                atoms.append(style(kind="emoji", emoji_key=_emoji_key(emoji.group(0)),
                                   text=emoji.group(0), emoji_px=px(EMOJI_SIZE)).measure())
                i = emoji.end()
                continue
        if ch.isspace() and ch != " ":
            flush()
            j = i
            while j < n and text[j].isspace() and text[j] != " ":
                j += 1
            atoms.append(style(kind="space", text=" ").measure())
            i = j
            continue
        if _script(ch) == "cjk":
            flush()
            atoms.append(style(kind="word", text=ch, script="cjk").measure())
            i += 1
            continue
        word.append(ch)
        i += 1
    flush()
    return atoms


def _code_run(atoms: list[Atom]) -> None:
    if atoms:
        atoms[0].pad_left = px(4)
        atoms[0].width += px(4)
        atoms[-1].pad_right = px(4)
        atoms[-1].width += px(4)


def parse_inline(text: str, base: Atom) -> list[Atom]:
    """Markdown-ish inline parser -> atoms."""
    atoms: list[Atom] = []
    bold = italic = underline = strike = spoiler = False
    buf: list[str] = []
    i, n = 0, len(text)

    def current() -> Atom:
        return Atom(
            kind="word", size=base.size,
            weight="Bold" if bold else base.weight, italic=italic or base.italic,
            underline=underline, strike=strike, spoiler=spoiler,
        )

    def flush() -> None:
        if not buf:
            return
        chunk = "".join(buf)
        buf.clear()
        atoms.extend(_split_special(chunk, current()))

    while i < n:
        ch = text[i]
        if ch == "\\" and i + 1 < n and text[i + 1] in "*_~`|\\<>@#:":
            buf.append(text[i + 1])
            i += 2
            continue
        if ch == "`":
            ticks = len(text[i:]) - len(text[i:].lstrip("`"))
            marker = "`" * ticks
            end = text.find(marker, i + ticks)
            if end > i + ticks:
                flush()
                run = atomize(text[i + ticks:end].strip("\n"),
                              base=Atom(kind="word", size=CODE_SIZE, code=True, spoiler=spoiler))
                _code_run(run)
                atoms.extend(run)
                i = end + ticks
                continue
        for marker, name in (("**", "bold"), ("__", "underline"), ("~~", "strike"), ("||", "spoiler")):
            if text.startswith(marker, i):
                active = {"bold": bold, "underline": underline, "strike": strike, "spoiler": spoiler}[name]
                if active or text.find(marker, i + 2) > i + 2:
                    flush()
                    if name == "bold":
                        bold = not bold
                    elif name == "underline":
                        underline = not underline
                    elif name == "strike":
                        strike = not strike
                    else:
                        spoiler = not spoiler
                    i += 2
                    break
        else:
            if ch in "*_":
                prev = text[i - 1] if i else " "
                nxt = text[i + 1] if i + 1 < n else " "
                if italic:
                    ok = prev not in " \t" and (ch == "*" or not nxt.isalnum())
                else:
                    ok = (
                        nxt not in " \t"
                        and (ch == "*" or not prev.isalnum())
                        and text.find(ch, i + 1) > i + 1
                    )
                if ok:
                    flush()
                    italic = not italic
                    i += 1
                    continue
            buf.append(ch)
            i += 1
            continue
        # a two-character marker was consumed above (the for/else skipped the else)
    flush()
    return atoms


def _split_special(text: str, base: Atom) -> list[Atom]:
    """Within one styled run: pull out URLs and mentions, atomize the rest."""
    out: list[Atom] = []
    pos = 0
    pattern = re.compile(f"{URL_RE.pattern}|{MENTION_RE.pattern}")
    for match in pattern.finditer(text):
        out.extend(atomize(text[pos:match.start()], base=base))
        token = match.group(0)
        if token[0] in "@#":
            atom = Atom(kind="word", text=token, size=base.size, weight=base.weight,
                        italic=base.italic, mention=True, spoiler=base.spoiler)
            out.append(atom.measure())
        else:
            trimmed = token.rstrip(".,!?;:)")
            link_base = Atom(kind="word", size=base.size, weight=base.weight, italic=base.italic,
                             underline=base.underline, strike=base.strike, link=True,
                             spoiler=base.spoiler)
            out.extend(atomize(trimmed, base=link_base))
            out.extend(atomize(token[len(trimmed):], base=base))
        pos = match.end()
    out.extend(atomize(text[pos:], base=base))
    return out


# ------------------------------------------------------------- block layout


@dataclass
class Block:
    kind: str  # "para" | "quote" | "code"
    lines: list[list[Atom]] = field(default_factory=list)
    code_lines: list[str] = field(default_factory=list)
    height: int = 0
    width: int = 0
    jumbo: bool = False


def _wrap(atoms: list[Atom], max_width: int) -> list[list[Atom]]:
    lines: list[list[Atom]] = []
    cur: list[Atom] = []
    width = 0

    def newline() -> None:
        nonlocal cur, width
        while cur and cur[-1].kind == "space":
            cur.pop()
        lines.append(cur)
        cur, width = [], 0

    for atom in atoms:
        if atom.kind == "space":
            if cur:
                cur.append(atom)
                width += atom.width
            continue
        if width + atom.width > max_width and cur:
            newline()
        if atom.width > max_width and atom.kind == "word":
            piece = ""
            for ch in atom.text:
                trial = Atom(**{**atom.__dict__, "text": piece + ch, "pad_left": 0, "pad_right": 0}).measure()
                if trial.width > max_width and piece:
                    cur.append(Atom(**{**atom.__dict__, "text": piece, "pad_left": 0, "pad_right": 0}).measure())
                    newline()
                    piece = ch
                else:
                    piece += ch
            if piece:
                tail = Atom(**{**atom.__dict__, "text": piece, "pad_left": 0, "pad_right": 0}).measure()
                cur.append(tail)
                width += tail.width
            continue
        cur.append(atom)
        width += atom.width
    newline()
    return lines


def build_blocks(
    text: str, max_width: int, *, size: float = BODY_SIZE, allow_jumbo: bool = True, edited: bool = False
) -> list[Block]:
    base = Atom(kind="word", size=size)
    blocks: list[Block] = []

    def add_text(chunk: str) -> None:
        for raw in chunk.split("\n"):
            quote = raw.startswith("> ") or raw == ">"
            line = raw[2:] if quote else raw
            atoms = parse_inline(line, base)
            if quote:
                lines = _wrap(atoms, max_width - px(QUOTE_INDENT)) or [[]]
                blocks.append(Block("quote", lines=lines))
            else:
                lines = _wrap(atoms, max_width) or [[]]
                blocks.append(Block("para", lines=lines))

    pos = 0
    for fence in FENCE_RE.finditer(text):
        before = text[pos:fence.start()].rstrip("\n")
        if before.strip() or (before and not blocks):
            add_text(before)
        code = fence.group(1).strip("\n")
        blocks.append(Block("code", code_lines=code.split("\n") if code else [""]))
        pos = fence.end()
    rest = text[pos:].lstrip("\n") if pos else text
    if rest.strip() or not blocks:
        add_text(rest)

    # emoji-only messages are drawn large, like the client does
    if edited:
        _append_edited(blocks, max_width, size)

    paras = [b for b in blocks if b.kind == "para"]
    if allow_jumbo and not edited and len(blocks) == len(paras) == 1:
        flat = [a for line in paras[0].lines for a in line if a.kind != "space"]
        if flat and len(flat) <= JUMBO_MAX and all(a.kind == "emoji" for a in flat):
            for line in paras[0].lines:
                for a in line:
                    if a.kind == "emoji":
                        a.emoji_px = px(JUMBO_SIZE)
                        a.measure()
            paras[0].jumbo = True
    return blocks


def _append_edited(blocks: list[Block], max_width: int, size: float) -> None:
    """Add a small muted '(edited)' after the last line of text."""
    marker = Atom(kind="word", text="(edited)", size=max(9.0, size * 0.65), muted=True).measure()
    gap = Atom(kind="space", text=" ", size=size).measure()
    for block in reversed(blocks):
        if block.kind == "code":
            continue
        last = block.lines[-1]
        indent = px(QUOTE_INDENT) if block.kind == "quote" else 0
        if sum(a.width for a in last) + gap.width + marker.width + indent <= max_width:
            last.extend([gap, marker])
        else:
            block.lines.append([marker])
        return
    blocks.append(Block("para", lines=[[marker]]))


def _code_block_lines(block: Block, max_width: int, font) -> list[str]:
    inner = max_width - px(18)
    lines: list[str] = []
    for raw in block.code_lines:
        raw = raw.replace("\t", "    ")
        if not raw:
            lines.append("")
        while raw:
            cut = len(raw)
            while cut > 1 and _tlen(raw[:cut], font) > inner:
                cut -= 1
            lines.append(raw[:cut])
            raw = raw[cut:]
    return lines


# -------------------------------------------------------------- image utils


def _rounded_mask(size: tuple[int, int], radius: int) -> Image.Image:
    big = (size[0] * MASK_SUPERSAMPLE, size[1] * MASK_SUPERSAMPLE)
    mask = Image.new("L", big, 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        (0, 0, big[0] - 1, big[1] - 1), radius=radius * MASK_SUPERSAMPLE, fill=255
    )
    return mask.resize(size, LANCZOS)


def _circular_avatar(avatar: Image.Image, size: int) -> Image.Image:
    avatar = avatar.convert("RGBA").resize((size, size), LANCZOS)
    big = size * MASK_SUPERSAMPLE
    mask = Image.new("L", (big, big), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, big - 1, big - 1), fill=255)
    mask = mask.resize((size, size), LANCZOS)
    out = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    out.paste(avatar, (0, 0), mask)
    return out


@lru_cache(maxsize=512)
def _unicode_emoji(seq: str, size: int) -> Optional[Image.Image]:
    try:
        font = ImageFont.truetype(str(FONT_DIR / EMOJI_FONT), 109)
    except OSError:
        return None
    canvas = Image.new("RGBA", (400, 140), (0, 0, 0, 0))
    try:
        ImageDraw.Draw(canvas).text((0, 0), seq, font=font, embedded_color=True)
    except Exception:
        return None
    box = canvas.getbbox()
    if box is None:
        return None
    # a failed ZWJ join draws several glyphs side by side: keep only the first
    if box[2] - box[0] > 150 and "‍" in seq:
        return _unicode_emoji(seq.split("‍")[0], size)
    canvas = canvas.crop((0, 0, 136, 128))
    height = max(1, int(round(size * 128 / 136)))
    scaled = canvas.resize((size, height), LANCZOS)
    square = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    square.paste(scaled, (0, (size - height) // 2))
    return square


def _emoji_image(key: str, size: int, custom: dict[str, bytes]) -> Optional[Image.Image]:
    if key.startswith("c:"):
        data = custom.get(key[2:])
        if not data:
            return None
        try:
            with Image.open(io.BytesIO(data)) as raw:
                return raw.convert("RGBA").resize((size, size), LANCZOS)
        except (OSError, ValueError):
            return None
    return _unicode_emoji(key, size)


def parse_reactions(raw: Optional[str]) -> list[tuple[str, int]]:
    """Parse the reactions option into [(emoji, count), ...]. Unicode and
    custom emoji work, and the count may be written many ways:

        "💀 4, 🔥 2"   "💀4 🔥2"   "💀 x4"   "💀×4"   "4 💀, 2 🔥"   "💀"  (= 1)
    """
    out: list[list] = []  # [emoji, count, explicit]
    pattern = re.compile(
        f"(?P<custom>{CUSTOM_EMOJI_RE.pattern})|(?P<emoji>{EMOJI_RE.pattern})|(?P<num>\\d+)"
    )
    for segment in re.split(r"[,;\n]", raw or ""):
        pending: Optional[int] = None
        last: Optional[list] = None
        for match in pattern.finditer(segment):
            if match.group("num"):
                value = max(1, min(int(match.group("num")), 999999))
                if last is not None and not last[2]:
                    last[1], last[2] = value, True
                else:
                    pending = value
                continue
            entry = [match.group("custom") or _emoji_key(match.group("emoji")), 1, False]
            if pending is not None:
                entry[1], entry[2], pending = pending, True, None
            out.append(entry)
            last = entry
            if len(out) >= MAX_REACTIONS:
                return [(e, c) for e, c, _ in out]
    return [(e, c) for e, c, _ in out]


def custom_emoji_ids(*texts: str) -> list[str]:
    seen: dict[str, None] = {}
    for text in texts:
        for match in CUSTOM_EMOJI_RE.finditer(text or ""):
            seen[match.group(2)] = None
    return list(seen)


def _decode_attachment(data: bytes, animated: bool) -> tuple[list[Image.Image], list[int]]:
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


# ------------------------------------------------------------------ specs


@dataclass
class ReplySpec:
    """The 'replying to' line shown above a message."""
    name: str
    text: str = ""
    name_color: Optional[RGB] = None
    avatar_bytes: Optional[bytes] = None


@dataclass
class EmbedSpec:
    title: str = ""
    description: str = ""
    color: RGB = BLURPLE


@dataclass
class MessageSpec:
    avatar_bytes: bytes
    name: str
    text: str = ""
    name_color: Optional[RGB] = None
    clan_tag: Optional[str] = None
    clan_badge: Optional[bytes] = None
    role_icon: Optional[bytes] = None  # image bytes of the role icon
    role_emoji: Optional[str] = None  # or a Unicode emoji used as the role icon
    is_app: bool = False
    timestamp: str = ""
    edited: bool = False
    mention_colors: dict[str, RGB] = field(default_factory=dict)
    attachment_bytes: Optional[bytes] = None
    sticker_bytes: Optional[bytes] = None
    embed: Optional[EmbedSpec] = None
    reactions: list[tuple[str, int]] = field(default_factory=list)
    reply: Optional[ReplySpec] = None
    row_highlight: bool = False
    continued: bool = False  # same author as the previous message: no header/avatar


LABEL_TEXT = "Generated image - not a real Discord message"
STICKER_SIZE = 160
EMBED_MAX_W = 432
EMBED_LINE_H = 18
EMBED_TEXT = 14


@dataclass
class _Layout:
    spec: MessageSpec
    content_w: int = 0
    height: int = 0
    top_pad: int = 0
    reply_top: int = 0
    header_top: int = 0
    body_top: int = 0
    embed_top: int = 0
    sticker_top: int = 0
    att_top: int = 0
    react_top: int = 0
    # header pieces
    name_atoms: list[Atom] = field(default_factory=list)
    name_w: int = 0
    role_img: Optional[Image.Image] = None
    chip_text: Optional[str] = None
    chip_w: int = 0
    badge_img: Optional[Image.Image] = None
    app_w: int = 0
    # content
    blocks: list[Block] = field(default_factory=list)
    reply_atoms: list[Atom] = field(default_factory=list)
    reply_avatar: Optional[Image.Image] = None
    embed_w: int = 0
    embed_h: int = 0
    embed_title: list[list[Atom]] = field(default_factory=list)
    embed_blocks: list[Block] = field(default_factory=list)
    sticker: Optional[Image.Image] = None
    frames: list[Image.Image] = field(default_factory=list)
    durations: list[int] = field(default_factory=list)
    att_w: int = 0
    att_h: int = 0
    pills: list[tuple[int, int, int, Image.Image, str]] = field(default_factory=list)
    react_h: int = 0
    avatar: Optional[Image.Image] = None


def _img(data: Optional[bytes], size: int) -> Optional[Image.Image]:
    if not data:
        return None
    try:
        with Image.open(io.BytesIO(data)) as raw:
            raw.load()
            return raw.convert("RGBA").resize((size, size), LANCZOS)
    except (OSError, ValueError, Image.DecompressionBombError):
        return None


def _measure_blocks(blocks: list[Block], lh: int, max_width: int, mono_font) -> tuple[int, int]:
    """Set each block's height/width; returns (total height, widest line)."""
    total = widest = 0
    for block in blocks:
        if block.kind == "code":
            block.code_lines = _code_block_lines(block, max_width, mono_font)
            block.width = max(max((_tlen(l, mono_font) for l in block.code_lines), default=0) + px(18), px(60))
            block.height = px(7) * 2 + px(18) * len(block.code_lines) + px(CODE_MARGIN) * 2
            widest = max(widest, block.width)
        else:
            indent = px(QUOTE_INDENT) if block.kind == "quote" else 0
            line_h = px(JUMBO_LINE_H) if block.jumbo else lh
            block.height = line_h * len(block.lines)
            for line in block.lines:
                widest = max(widest, indent + sum(a.width for a in line))
        total += block.height
    return total, widest


def _layout_message(spec: MessageSpec, *, theme: Theme, custom: dict[str, bytes], first: bool,
                    animate: bool) -> _Layout:
    lay = _Layout(spec=spec)
    line_h = px(LINE_H)
    max_col = px(MAX_COL)
    time_font = font_for("latin", TIME_SIZE)
    chip_font = font_for("latin", 12, "SemiBold")
    app_font = font_for("latin", 10, "Bold")
    count_font = font_for("latin", 14, "SemiBold")
    mono_font = font_for("latin", CODE_SIZE, code=True)
    mono_small = font_for("latin", 13, code=True)

    lay.avatar = None if spec.continued else (
        _circular_avatar(Image.open(io.BytesIO(spec.avatar_bytes)).convert("RGBA"), px(AVATAR))
        if _img(spec.avatar_bytes, 8) is not None
        else _circular_avatar(Image.new("RGB", (8, 8), (114, 118, 125)), px(AVATAR))
    )
    y = px(PAD) if not spec.continued else px(2)
    lay.top_pad = y
    widths = [0]

    # ---- reply line
    if spec.reply is not None:
        lay.reply_top = y
        reply = spec.reply
        lay.reply_avatar = _circular_avatar(
            Image.open(io.BytesIO(reply.avatar_bytes)).convert("RGBA"), px(16)
        ) if _img(reply.avatar_bytes, 8) is not None else None
        label = atomize(reply.name, base=Atom(kind="word", size=14, weight="SemiBold"))
        body = atomize(" ".join((reply.text or "").split())[:200] or "Click to see attachment",
                       base=Atom(kind="word", size=14, muted=True))
        used = px(16 + 4) + sum(a.width for a in label) + px(6)
        room = max_col - used
        while body and sum(a.width for a in body) > room:
            body.pop()
            dots = atomize("…", base=Atom(kind="word", size=14, muted=True))
            if sum(a.width for a in body) + dots[0].width <= room:
                body.extend(dots)
                break
        lay.reply_atoms = label + [Atom(kind="space", text=" ", size=14).measure()] + body
        widths.append(used + sum(a.width for a in body))
        y += line_h

    # ---- header
    if not spec.continued:
        lay.header_top = y
        lay.chip_text = (spec.clan_tag or "").strip()[:4] or None
        if lay.chip_text and spec.clan_badge:
            lay.badge_img = _img(spec.clan_badge, px(12))
        badge_space = px(15) if lay.badge_img is not None else 0
        lay.chip_w = px(8) + badge_space + _tlen(lay.chip_text or "", chip_font) if lay.chip_text else 0
        lay.app_w = _tlen("APP", app_font) + px(8)
        lay.role_img = _img(spec.role_icon, px(20)) if spec.role_icon else (
            _unicode_emoji(_emoji_key(spec.role_emoji), px(20)) if spec.role_emoji else None
        )
        extras = (px(24) if lay.role_img is not None else 0) \
            + (px(4) + lay.chip_w if lay.chip_text else 0) + (px(4) + lay.app_w if spec.is_app else 0)
        time_w = _tlen(spec.timestamp, time_font)
        name_max = max(px(40), max_col - extras - px(8) - time_w)
        lay.name_atoms = atomize(spec.name, base=Atom(kind="word", size=NAME_SIZE, weight="SemiBold"))
        while lay.name_atoms and sum(a.width for a in lay.name_atoms) > name_max:
            lay.name_atoms.pop()
            dots = atomize("…", base=Atom(kind="word", size=NAME_SIZE, weight="SemiBold"))
            if sum(a.width for a in lay.name_atoms) + dots[0].width <= name_max:
                lay.name_atoms.extend(dots)
                break
        lay.name_w = sum(a.width for a in lay.name_atoms)
        widths.append(lay.name_w + extras + px(8) + time_w)
        y += line_h

    # ---- body
    lay.body_top = y
    if spec.text.strip() or spec.edited:
        lay.blocks = build_blocks(spec.text, max_col, edited=spec.edited)
        total, widest = _measure_blocks(lay.blocks, line_h, max_col, mono_font)
        y += total
        widths.append(widest)

    # ---- embed
    if spec.embed is not None and (spec.embed.title or spec.embed.description):
        embed = spec.embed
        y += px(4)
        lay.embed_top = y
        inner_max = px(EMBED_MAX_W) - px(4 + 12 + 16)
        title_atoms = parse_inline(embed.title, Atom(kind="word", size=16, weight="SemiBold")) if embed.title else []
        for atom in title_atoms:
            atom.link = True
        lay.embed_title = _wrap(title_atoms, inner_max) if title_atoms else []
        lay.embed_blocks = build_blocks(embed.description, inner_max, size=EMBED_TEXT, allow_jumbo=False) \
            if embed.description else []
        desc_h, desc_w = _measure_blocks(lay.embed_blocks, px(EMBED_LINE_H), inner_max, mono_small)
        title_w = max((sum(a.width for a in line) for line in lay.embed_title), default=0)
        lay.embed_w = px(4 + 12 + 16) + max(title_w, desc_w, px(100))
        lay.embed_h = px(8) + len(lay.embed_title) * px(22) + (px(4) if lay.embed_title and desc_h else 0) \
            + desc_h + px(16)
        y += lay.embed_h
        widths.append(lay.embed_w)

    # ---- sticker
    sticker_bytes = spec.sticker_bytes
    if sticker_bytes:
        try:
            with Image.open(io.BytesIO(sticker_bytes)) as raw:
                im = raw.convert("RGBA")
            scale = min(px(STICKER_SIZE) / im.width, px(STICKER_SIZE) / im.height)
            lay.sticker = im.resize((max(1, int(im.width * scale)), max(1, int(im.height * scale))), LANCZOS)
        except (OSError, ValueError, Image.DecompressionBombError):
            lay.sticker = None
    if lay.sticker is not None:
        y += px(ATTACH_GAP)
        lay.sticker_top = y
        y += lay.sticker.height
        widths.append(lay.sticker.width)

    # ---- attached image
    if spec.attachment_bytes:
        try:
            frames, durations = _decode_attachment(spec.attachment_bytes, animated=animate)
        except (OSError, ValueError, Image.DecompressionBombError):
            frames, durations = [], []
        if frames:
            src_w, src_h = frames[0].size
            w1 = min(src_w, ATTACH_MAX_W)
            h1 = src_h * w1 / src_w
            if h1 > ATTACH_MAX_H:
                w1 = w1 * ATTACH_MAX_H / h1
                h1 = ATTACH_MAX_H
            lay.att_w, lay.att_h = max(1, px(w1)), max(1, px(h1))
            mask = _rounded_mask((lay.att_w, lay.att_h), px(ATTACH_RADIUS))
            for frame in frames:
                resized = frame.resize((lay.att_w, lay.att_h), LANCZOS)
                resized.putalpha(ImageChops.multiply(resized.getchannel("A"), mask))
                lay.frames.append(resized)
            lay.durations = durations
            y += px(ATTACH_GAP)
            lay.att_top = y
            y += lay.att_h
            widths.append(lay.att_w)

    # ---- reactions
    rows = react_w = 0
    x = 0
    for key, count in spec.reactions:
        match = CUSTOM_EMOJI_RE.fullmatch(key)
        emoji_img = _emoji_image(f"c:{match.group(2)}" if match else key, px(REACTION_EMOJI), custom)
        if emoji_img is None:
            continue
        label = str(count)
        w = px(REACTION_PAD) * 2 + px(REACTION_EMOJI) + px(6) + _tlen(label, count_font)
        if x and x + w > max_col:
            x = 0
            rows += 1
        lay.pills.append((x, rows, w, emoji_img, label))
        react_w = max(react_w, x + w)
        x += w + px(REACTION_GAP)
    if lay.pills:
        rows += 1
        lay.react_h = rows * px(REACTION_H) + (rows - 1) * px(REACTION_GAP)
        y += px(REACTION_GAP)
        lay.react_top = y
        y += lay.react_h
        widths.append(react_w)

    avatar_bottom = (lay.header_top + px(AVATAR_TOP_OFFSET + AVATAR)) if not spec.continued else 0
    lay.height = max(y, avatar_bottom)
    lay.content_w = min(max_col, max(widths))
    return lay


# ----------------------------------------------------------------- renderer


def render_card(
    messages: list[MessageSpec],
    *,
    theme_name: str = "dark",
    custom_emoji: Optional[dict[str, bytes]] = None,
    as_gif: bool = False,
    label: bool = False,
) -> tuple[io.BytesIO, str]:
    """Render one or more messages as a single card at 2x, downsample with
    LANCZOS, and return (buffer, "png" | "gif")."""
    if not messages:
        raise ValueError("render_card needs at least one message")
    with _RENDER_LOCK:
        return _render(messages, theme_name, custom_emoji or {}, as_gif, label)


def render_message_card(
    *, text: str, avatar_bytes: bytes, name: str, theme_name: str = "dark",
    custom_emoji: Optional[dict[str, bytes]] = None, as_gif: bool = False, label: bool = False,
    **fields,
) -> tuple[io.BytesIO, str]:
    """Convenience wrapper: a single message built from keyword fields."""
    spec = MessageSpec(avatar_bytes=avatar_bytes, name=name, text=text, **fields)
    return render_card([spec], theme_name=theme_name, custom_emoji=custom_emoji, as_gif=as_gif, label=label)


def _render(messages, theme_name, custom, as_gif, label) -> tuple[io.BytesIO, str]:
    theme = THEMES.get(theme_name, THEMES["dark"])
    animate = as_gif and len(messages) == 1
    layouts = [
        _layout_message(m, theme=theme, custom=custom, first=i == 0, animate=animate)
        for i, m in enumerate(messages)
    ]

    text_x = px(PAD + AVATAR + AVATAR_GAP)
    content_w = max(lay.content_w for lay in layouts)
    card_w = text_x + content_w + px(PAD)
    label_font = font_for("latin", 11)
    label_h = 0
    if label:
        card_w = max(card_w, text_x + _tlen(LABEL_TEXT, label_font) + px(PAD))
        label_h = px(6 + 14)
    card_h = sum(lay.height for lay in layouts) + label_h + px(PAD)
    card_w += card_w % S
    card_h += card_h % S

    base = Image.new("RGB", (card_w, card_h), theme.bg)
    draw = ImageDraw.Draw(base)
    animated_att: Optional[tuple[_Layout, int]] = None

    y0 = 0
    for lay in layouts:
        att_abs = _paint_message(base, draw, lay, y0, card_w, text_x, theme, custom, animate)
        if animate and len(lay.frames) > 1:
            animated_att = (lay, att_abs)
        y0 += lay.height

    if label:
        draw.text((text_x, y0 + px(6) + _baseline(label_font, 0, px(14))), LABEL_TEXT,
                  font=label_font, fill=theme.timestamp, anchor="ls")

    out_size = (card_w // S, card_h // S)
    buffer = io.BytesIO()
    if as_gif:
        if animated_att is not None:
            lay, att_y = animated_att
            outputs = []
            for frame in lay.frames:
                canvas = base.copy()
                canvas.paste(frame, (text_x, att_y), frame)
                outputs.append(canvas.resize(out_size, LANCZOS))
            durations = lay.durations
        else:
            outputs, durations = [base.resize(out_size, LANCZOS)], [0]
        paletted = [im.convert("P", palette=Image.Palette.ADAPTIVE, colors=256) for im in outputs]
        if len(paletted) > 1:
            paletted[0].save(buffer, format="GIF", save_all=True, append_images=paletted[1:],
                             duration=durations, loop=0, disposal=2)
        else:
            paletted[0].save(buffer, format="GIF")
        ext = "gif"
    else:
        base.resize(out_size, LANCZOS).save(buffer, format="PNG", optimize=True)
        ext = "png"
    buffer.seek(0)
    return buffer, ext


def _paint_blocks(base, draw, blocks, x, y, lh, font, max_width, mono_font, theme, bg, mention_colors, custom):
    for block in blocks:
        if block.kind == "code":
            box_top = y + px(CODE_MARGIN)
            draw.rounded_rectangle((x, box_top, x + block.width, y + block.height - px(CODE_MARGIN) - 1),
                                   radius=px(4), fill=theme.code_bg, outline=theme.code_border, width=px(1))
            ly = box_top + px(7)
            for code_line in block.code_lines:
                if code_line:
                    draw.text((x + px(9), _baseline(mono_font, ly, px(18))), code_line,
                              font=mono_font, fill=theme.body, anchor="ls")
                ly += px(18)
        else:
            line_h = px(JUMBO_LINE_H) if block.jumbo else lh
            indent = 0
            if block.kind == "quote":
                draw.rounded_rectangle((x, y, x + px(4) - 1, y + block.height - 1),
                                       radius=px(2), fill=theme.quote_bar)
                indent = px(QUOTE_INDENT)
            ly = y
            for line in block.lines:
                cx = x + indent
                line_base = _baseline(font, ly, line_h)
                for atom in line:
                    _draw_atom(base, draw, atom, cx, line_base, ly, line_h, theme.body, theme, bg,
                               mention_colors, custom)
                    cx += atom.width
                ly += line_h
        y += block.height
    return y


def _paint_message(base, draw, lay: _Layout, y0: int, card_w: int, text_x: int, theme: Theme,
                   custom: dict[str, bytes], animate: bool) -> int:
    """Paint one message with its top edge at y0. Returns the attachment's
    absolute y (used to place animated frames)."""
    spec = lay.spec
    bg = theme.highlight_bg if spec.row_highlight else theme.bg
    line_h = px(LINE_H)
    if spec.row_highlight:
        draw.rectangle((0, y0, card_w, y0 + lay.height), fill=bg)
        draw.rectangle((0, y0, px(3) - 1, y0 + lay.height), fill=AMBER)

    body_font = font_for("latin", BODY_SIZE)
    mono_font = font_for("latin", CODE_SIZE, code=True)
    name_color = spec.name_color or theme.name_default

    # reply line
    if spec.reply is not None:
        top = y0 + lay.reply_top
        mid = top + line_h // 2
        cx = px(PAD + AVATAR // 2)
        start = text_x - px(4)
        r = px(8)
        av_top = y0 + lay.header_top + px(AVATAR_TOP_OFFSET)
        draw.line((cx, av_top - px(1), cx, mid + r), fill=theme.quote_bar, width=px(2))
        draw.arc((cx, mid, cx + 2 * r, mid + 2 * r), 180, 270, fill=theme.quote_bar, width=px(2))
        draw.line((cx + r, mid, start, mid), fill=theme.quote_bar, width=px(2))
        x = text_x
        if lay.reply_avatar is not None:
            base.paste(lay.reply_avatar, (x, mid - px(8)), lay.reply_avatar)
        x += px(20)
        base_font = font_for("latin", 14)
        baseline = _baseline(base_font, top, line_h)
        for atom in lay.reply_atoms:
            color = spec.reply.name_color or theme.name_default if atom.weight == "SemiBold" else theme.timestamp
            _draw_atom(base, draw, atom, x, baseline, top, line_h, color, theme, bg, {}, custom)
            x += atom.width

    # avatar + header
    if not spec.continued and lay.avatar is not None:
        base.paste(lay.avatar, (px(PAD), y0 + lay.header_top + px(AVATAR_TOP_OFFSET)), lay.avatar)
        top = y0 + lay.header_top
        name_font = font_for("latin", NAME_SIZE, "SemiBold")
        baseline = _baseline(name_font, top, line_h)
        x = text_x
        for atom in lay.name_atoms:
            _draw_atom(base, draw, atom, x, baseline, top, line_h, name_color, theme, bg, {}, custom)
            x += atom.width
        center_y = top + line_h // 2
        if lay.role_img is not None:
            x += px(4)
            base.paste(lay.role_img, (x, center_y - px(10)), lay.role_img)
            x += px(20)
        if lay.chip_text:
            chip_font = font_for("latin", 12, "SemiBold")
            chip_h = px(16)
            x += px(4)
            ctop = center_y - chip_h // 2
            draw.rounded_rectangle((x, ctop, x + lay.chip_w, ctop + chip_h), radius=px(4), fill=theme.clan_bg)
            left = x + px(4)
            if lay.badge_img is not None:
                base.paste(lay.badge_img, (left, ctop + (chip_h - px(12)) // 2), lay.badge_img)
                left += px(15)
            draw.text((left, _baseline(chip_font, ctop, chip_h)), lay.chip_text, font=chip_font,
                      fill=theme.clan_fg, anchor="ls")
            x += lay.chip_w
        if spec.is_app:
            app_font = font_for("latin", 10, "Bold")
            app_h = px(15)
            x += px(4)
            atop = center_y - app_h // 2 + px(0.5)
            draw.rounded_rectangle((x, atop, x + lay.app_w, atop + app_h), radius=px(3), fill=BLURPLE)
            draw.text((x + lay.app_w // 2, _baseline(app_font, atop, app_h)), "APP", font=app_font,
                      fill=(255, 255, 255), anchor="ms")
            x += lay.app_w
        x += px(8)
        draw.text((x, baseline), spec.timestamp, font=font_for("latin", TIME_SIZE), fill=theme.timestamp,
                  anchor="ls")

    # body
    if lay.blocks:
        _paint_blocks(base, draw, lay.blocks, text_x, y0 + lay.body_top, line_h, body_font,
                      px(MAX_COL), mono_font, theme, bg, spec.mention_colors, custom)

    # embed
    if lay.embed_h:
        ex, ey = text_x, y0 + lay.embed_top
        draw.rounded_rectangle((ex, ey, ex + lay.embed_w, ey + lay.embed_h), radius=px(4),
                               fill=theme.embed_bg, outline=theme.embed_border, width=px(1))
        draw.rounded_rectangle((ex, ey, ex + px(4), ey + lay.embed_h), radius=px(2), fill=spec.embed.color)
        iy = ey + px(8)
        ix = ex + px(4 + 12)
        title_font = font_for("latin", 16, "SemiBold")
        for line in lay.embed_title:
            lb = _baseline(title_font, iy, px(22))
            cx = ix
            for atom in line:
                _draw_atom(base, draw, atom, cx, lb, iy, px(22), theme.link, theme, theme.embed_bg, {}, custom)
                cx += atom.width
            iy += px(22)
        if lay.embed_title and lay.embed_blocks:
            iy += px(4)
        _paint_blocks(base, draw, lay.embed_blocks, ix, iy, px(EMBED_LINE_H), font_for("latin", EMBED_TEXT),
                      px(EMBED_MAX_W), font_for("latin", 13, code=True), theme, theme.embed_bg, {}, custom)

    # sticker
    if lay.sticker is not None:
        base.paste(lay.sticker, (text_x, y0 + lay.sticker_top), lay.sticker)

    # attached image
    att_abs = y0 + lay.att_top
    if lay.frames and not (animate and len(lay.frames) > 1):
        base.paste(lay.frames[0], (text_x, att_abs), lay.frames[0])

    # reactions
    count_font = font_for("latin", 14, "SemiBold")
    for px_x, row, w, emoji_img, text in lay.pills:
        left = text_x + px_x
        top = y0 + lay.react_top + row * (px(REACTION_H) + px(REACTION_GAP))
        draw.rounded_rectangle((left, top, left + w, top + px(REACTION_H)), radius=px(REACTION_RADIUS),
                               fill=theme.pill_bg, outline=theme.pill_border, width=px(1))
        base.paste(emoji_img, (left + px(REACTION_PAD), top + (px(REACTION_H) - px(REACTION_EMOJI)) // 2), emoji_img)
        draw.text((left + px(REACTION_PAD) + px(REACTION_EMOJI) + px(6), top + px(REACTION_H) // 2),
                  text, font=count_font, fill=theme.pill_count, anchor="lm")
    return att_abs


def _draw_atom(
    base: Image.Image, draw: ImageDraw.ImageDraw, atom: Atom, x: int, baseline: int,
    line_top: int, line_h: int, color: RGB, theme: Theme, bg: RGB,
    mention_colors: dict[str, RGB], custom: dict[str, bytes],
) -> None:
    right = x + atom.width

    if atom.spoiler:
        draw.rounded_rectangle((x, line_top + px(2), right, line_top + line_h - px(2)),
                               radius=px(3), fill=theme.spoiler_bg)
        return

    if atom.kind == "emoji":
        img = _emoji_image(atom.emoji_key, atom.emoji_px, custom)
        if img is not None:
            base.paste(img, (x, line_top + (line_h - atom.emoji_px) // 2), img)
        elif atom.text:  # custom emoji we could not download: show its :name:
            label = ":" + atom.text.split(":")[1] + ":"
            draw.text((x, baseline), label, font=font_for("latin", BODY_SIZE), fill=color, anchor="ls")
        return
    if atom.kind == "space":
        if atom.code:
            draw.rectangle((x, line_top + px(2), right, line_top + line_h - px(2)), fill=theme.code_bg)
        return

    fill = color
    text_x = x + atom.pad_left
    if atom.mention:
        tint = mention_colors.get(atom.text)
        pill_bg = _blend(tint, bg, 0.2) if tint else theme.mention_bg
        fill = tint if tint else theme.mention_fg
        draw.rounded_rectangle((x, line_top + px(1), right, line_top + line_h - px(1)),
                               radius=px(3), fill=pill_bg)
    elif atom.code:
        draw.rectangle((x, line_top + px(2), right, line_top + line_h - px(2)), fill=theme.code_bg)
    elif atom.link:
        fill = theme.link
    elif atom.muted:
        fill = theme.timestamp
    draw.text((text_x, baseline), atom.text, font=atom.font, fill=fill, anchor="ls")
    if atom.underline:
        draw.rectangle((text_x, baseline + px(2), text_x + atom.width - atom.pad_left - atom.pad_right,
                        baseline + px(2) + max(1, px(1)) - 1), fill=fill)
    if atom.strike:
        mid = baseline - px(BODY_SIZE * 0.3)
        draw.rectangle((x, mid, right, mid + max(1, px(1)) - 1), fill=fill)
