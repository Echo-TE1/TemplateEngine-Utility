# Quote card cog

A discord.py cog (`MessageCog` in `message.py`) with a `/quote` slash command.
It renders a PNG chat card (circular avatar, display name, timestamp, wrapped
text, dark or light theme) with Pillow and sends it as a `discord.File`.
Image rendering only: no webhooks, no impersonation.

Load it with `await bot.load_extension("message")`.

Install: `pip install -r requirements.txt`
