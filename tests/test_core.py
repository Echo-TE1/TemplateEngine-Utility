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


def test_duration_and_refs():
    from timeutil import parse_duration, parse_message_ref
    assert parse_duration("1h30m") == 5400 and parse_duration("2d") == 172800 and parse_duration("45 min") == 2700
    assert parse_duration("1s") == 1 and parse_duration("12mo") == 12 * 30 * 86400 and parse_duration("1y") == 365 * 86400
    assert parse_duration("2 weeks 3 days") == 17 * 86400 and parse_duration("1d12h") == 129600 and parse_duration("90 seconds") == 90
    assert parse_duration("5mins") == 300 and parse_duration("0s") is None and parse_duration("1x") is None
    assert parse_duration("soon") is None and parse_duration("") is None and parse_duration("5x") is None
    assert parse_message_ref("123456") == 123456
    assert parse_message_ref("https://discord.com/channels/1/2/3456") == 3456
    assert parse_message_ref("nope") is None


def test_usage_math():
    from usage_math import bar, counter_delta, fmt_bytes, parse_net_dev, percent
    sample = ("Inter-|   Receive | Transmit\n face |bytes packets errs drop fifo frame compressed multicast|bytes packets\n"
              "    lo: 100 1 0 0 0 0 0 0 100 1 0 0 0 0 0 0\n"
              "  eth0: 5000 10 0 0 0 0 0 0 7000 9 0 0 0 0 0 0\n")
    assert parse_net_dev(sample) == (5000, 7000)
    assert parse_net_dev("") is None
    assert counter_delta((10, 20), (15, 30)) == (5, 10)
    assert counter_delta((1000, 2000), (5, 7)) == (5, 7)  # container restarted
    assert percent(50, 100) == 50 and percent(1, 0) == 0
    assert fmt_bytes(267 * 1024 * 1024) == "267 MB" and fmt_bytes(5 * 1024 ** 3) == "5.00 GB"
    assert bar(50, 10).count("\u2593") == 5
