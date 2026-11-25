"""Tiny web server so hosts like Render (Web Service) see an open port and
cron / uptime pingers (cron-job.org, UptimeRobot, ...) can keep it awake.

Designed to never upset a pinger: every path and method answers
200 with a 2-byte body ("ok"), so there are no 404/405 "HTTP error"
results and no "output too large" failures.
"""

from __future__ import annotations

import logging
import os

from aiohttp import web

log = logging.getLogger("keep_alive")

_HEADERS = {"Cache-Control": "no-store"}


async def _ok(_: web.Request) -> web.Response:
    return web.Response(text="ok", content_type="text/plain", headers=_HEADERS)


@web.middleware
async def _never_fail(request: web.Request, handler):
    try:
        return await handler(request)
    except Exception:  # includes HTTP errors such as 404/405
        return await _ok(request)


async def start_keep_alive() -> web.AppRunner:
    app = web.Application(middlewares=[_never_fail])
    app.router.add_route("*", "/{tail:.*}", _ok)  # GET, HEAD, POST, any path

    # access_log=None: frequent pings would otherwise flood the logs.
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    port = int(os.environ.get("PORT", "8080"))
    await web.TCPSite(runner, "0.0.0.0", port).start()
    log.info("Keep-alive server listening on port %d", port)
    return runner
