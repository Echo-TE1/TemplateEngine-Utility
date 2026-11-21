'use strict';

const http = require('node:http');

/** Tiny web server so Render (and a cron-job pinger) can see that the bot is alive. */
function keepAlive(port) {
  const server = http.createServer((req, res) => {
    if (req.url === '/health') {
      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ status: 'ok' }));
      return;
    }
    res.writeHead(200, { 'Content-Type': 'text/plain' });
    res.end('Bot is alive!');
  });
  // Render gives the port in the PORT environment variable.
  server.listen(port, '0.0.0.0', () => console.log(`[KeepAlive] Listening on port ${port}`));
  return server;
}

module.exports = { keepAlive };
