"""Send a test message to the alert webhook of .env (ON_ALERT, ON_ALERT_WEBHOOK_URL), to check it.

    .venv/bin/python -m backend.cli.test_alert                      # a standard test message
    .venv/bin/python -m backend.cli.test_alert "any text you like"  # your own text

It sends right away (no waiting, no cooldown) and prints what happened. The webhook URL is never printed."""

import socket
import sys
from datetime import datetime
from urllib.parse import urlsplit

from ..core import config as C
from ..health import alerts


def main():
    ok, why = alerts.configured()
    if not ok:
        print(f"Not ready: {why}.\nSet ON_ALERT=1 and ON_ALERT_WEBHOOK_URL=<the webhook URL of the Google Chat space> in .env "
              f"(see docs/logs-and-alerts.md).")
        sys.exit(1)
    text = " ".join(sys.argv[1:]).strip() or (
        f"*{C.ALERT_PREFIX} {socket.gethostname()}*  test message\n"
        f"Sent {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}. If you read this, the alerts work.")
    print(f"Webhook host: {urlsplit(C.ALERT_WEBHOOK_URL).netloc}")
    try:
        alerts.send_message(text)
    except Exception as e:
        print(f"FAILED: {alerts._clean(e)}")
        sys.exit(1)
    print("Sent. Look in the chat space.")


if __name__ == "__main__":
    main()
