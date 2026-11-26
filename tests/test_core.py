import io

import pytest
from PIL import Image

from cardrender import (EmbedSpec, MessageSpec, ReplySpec, custom_emoji_ids, parse_reactions,
                        render_card)
from timeutil import conversation_timestamps, resolve_timestamp


def avatar() -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (64, 64), (88, 101, 242)).save(out, "PNG")
    return out.getvalue()


def test_reactions_flexible():
    assert parse_reactions("💀 4, 🔥 2") == [("💀", 4), ("🔥", 2)]
    assert parse_reactions("💀4 🔥2") == [("💀", 4), ("🔥", 2)]
    assert parse_reactions("💀 x4") == [("💀", 4)]
    assert parse_reactions("4 💀") == [("💀", 4)]
    assert parse_reactions("💀") == [("💀", 1)]
    assert parse_reactions("") == []


def test_custom_emoji_ids():
    assert custom_emoji_ids("hi <:ab:111111111111111111> <a:cd:222222222222222222>", "<:ef:333333333333333333>") == ["111111111111111111", "222222222222222222", "333333333333333333"]


def test_timestamps():
    assert resolve_timestamp("yesterday at 5") == "yesterday at 5"
    assert resolve_timestamp("12h").startswith("Today at ") and "M" in resolve_timestamp("12h")
    assert ":" in resolve_timestamp("military") and "M" not in resolve_timestamp("24h")
    assert len(conversation_timestamps(3, "24h")) == 3
    assert conversation_timestamps(2, "custom") == ["custom", "custom"]


def test_settings_clean():
    pytest.importorskip("discord")
    from settings_store import clean

    assert clean({"theme": "nope", "label": 1, "bogus": 1}) == {"label": True}
    assert clean({"theme": "onyx"}) == {"theme": "onyx"}


@pytest.mark.parametrize("theme", ["dark", "ash", "onyx", "light"])
def test_render_single(theme):
    spec = MessageSpec(avatar_bytes=avatar(), name="Alex", text="**hi** <:xx:111111111111111111> 😀 `code` こんにちは",
                       timestamp="Today at 8:44 PM", reactions=[("💀", 3)])
    buf, ext = render_card([spec], theme_name=theme)
    assert ext == "png"
    assert Image.open(buf).width > 100


def test_render_conversation_reply_embed_label():
    a = MessageSpec(avatar_bytes=avatar(), name="A", text="first", timestamp="t",
                    reply=ReplySpec(name="B", text="earlier"), edited=True)
    b = MessageSpec(avatar_bytes=avatar(), name="A", text="second", timestamp="t", continued=True,
                    embed=EmbedSpec(title="T", description="D"))
    buf, _ = render_card([a, b], label=True)
    assert Image.open(buf).height > 100


def test_render_gif():
    spec = MessageSpec(avatar_bytes=avatar(), name="A", text="hi", timestamp="t")
    buf, ext = render_card([spec], as_gif=True)
    assert ext == "gif"
    assert Image.open(buf).format == "GIF"


def test_timezone_per_user():
    from timeutil import valid_timezone
    assert valid_timezone("Asia/Tokyo") and not valid_timezone("Nope/Zone") and not valid_timezone("")
    assert resolve_timestamp("12h", "Asia/Tokyo").startswith("Today at ")
    assert resolve_timestamp("12h", "bogus").startswith("Today at ")
