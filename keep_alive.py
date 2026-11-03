import os
import logging
from threading import Thread

from flask import Flask

app = Flask(__name__)

# Hide the noisy request logs from the cron job pings
logging.getLogger("werkzeug").setLevel(logging.ERROR)


@app.route("/")
def home():
    return "Bot is alive!", 200


@app.route("/health")
def health():
    return {"status": "ok"}, 200


def run():
    # Render gives the port in the PORT environment variable
    port = int(os.getenv("PORT", "8080"))
    app.run(host="0.0.0.0", port=port)


def keep_alive():
    """Start the web server in a background thread."""
    t = Thread(target=run, daemon=True)
    t.start()
