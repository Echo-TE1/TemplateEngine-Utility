"""Tiny web server so hosts like Render (Web Service) see an open port
and external pingers can keep the service awake."""

from __future__ import annotations

import logging
import os

from aiohttp import web

log = logging.getLogger("keep_alive")


async def start_keep_alive() -> web.AppRunner:
    app = web.Application()

    async def ok(_: web.Request) -> web.Response:
        return web.Response(text="ok")

    app.router.add_get("/", ok)
    app.router.add_get("/health", ok)

    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", "8080"))
    await web.TCPSite(runner, "0.0.0.0", port).start()
    log.info("Keep-alive server listening on port %d", port)
    return runner
