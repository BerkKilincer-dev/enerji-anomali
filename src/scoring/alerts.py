"""Telegram messages for new live anomaly events.

Enabled when TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID are set. Each event goes out once
(`notifications`), only while it is recent so a replayed backlog does not flood the chat, and
only above the building's `alert_settings`. The costliest events are sent first.
"""

from __future__ import annotations

import json
import logging
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass

import psycopg

from src.config import get_settings

log = logging.getLogger(__name__)

CHANNEL = "telegram"
RECENT_HOURS = 24  # wall-clock window; the dashboard anchors on the latest reading instead
MAX_PER_CYCLE = 10

PENDING_SQL = """
SELECT a.id, COALESCE(b.name, b.external_id), a.explanation
FROM anomalies a
JOIN buildings b USING (building_id)
LEFT JOIN alert_settings s USING (building_id)
WHERE a.origin = 'live'
  AND COALESCE(s.notify, true)
  AND COALESCE(a.est_cost_tl, 0) >= COALESCE(s.min_cost_tl, 0)
  AND a.ts_end >= (now() AT TIME ZONE b.timezone) - make_interval(hours => %(recent)s)
  AND NOT EXISTS (
      SELECT 1 FROM notifications n WHERE n.anomaly_id = a.id AND n.channel = %(channel)s)
ORDER BY a.est_cost_tl DESC NULLS LAST, a.ts_start
LIMIT %(limit)s
"""


@dataclass(frozen=True)
class Telegram:
    token: str
    chat_id: str
    timeout: float = 10

    def send(self, text: str) -> None:
        body = json.dumps({"chat_id": self.chat_id, "text": text}).encode()
        request = urllib.request.Request(
            f"https://api.telegram.org/bot{self.token}/sendMessage",
            data=body,
            headers={"Content-Type": "application/json"},
        )
        urllib.request.urlopen(request, timeout=self.timeout).close()


def telegram_from_settings() -> Telegram | None:
    settings = get_settings()
    if settings.telegram_bot_token and settings.telegram_chat_id:
        return Telegram(settings.telegram_bot_token, settings.telegram_chat_id)
    return None


def send_alerts(
    conn: psycopg.Connection, send: Callable[[str], None], channel: str = CHANNEL
) -> int:
    """Send recent unsent live events; stops at the first failure and retries next cycle."""
    params = {"recent": RECENT_HOURS, "channel": channel, "limit": MAX_PER_CYCLE}
    sent = 0
    for anomaly_id, building, explanation in conn.execute(PENDING_SQL, params).fetchall():
        try:
            send(f"Energy alert: {building}\n{explanation}")
        except OSError as exc:
            log.warning("alert for anomaly %d not sent: %s", anomaly_id, exc)
            break
        conn.execute(
            "INSERT INTO notifications (anomaly_id, channel) VALUES (%s, %s) "
            "ON CONFLICT DO NOTHING",
            (anomaly_id, channel),
        )
        sent += 1
    return sent
