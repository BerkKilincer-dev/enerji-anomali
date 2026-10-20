"""Turkish advice for finished live events, written by Claude.

Optional and paid per call: on only when ANTHROPIC_API_KEY is set. The rule-based
`explanation` stays the source of truth; `advice` adds a likely cause and a concrete action
for the facility manager. Each event is asked once, after it has ended (a still-running event
would change), and at most MAX_PER_CYCLE events per scoring cycle are sent, costliest first.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

import anthropic
import psycopg
from psycopg.rows import dict_row

from src.config import get_settings
from src.models.events import tr_number

log = logging.getLogger(__name__)

MAX_PER_CYCLE = 10
MAX_ADVICE_CHARS = 2000
# An event is final once scoring has moved this far past its last hour (see service.py).
SETTLE_HOURS = 6
# Older events are left alone, so a first run on a long backlog does not send hundreds.
RECENT_DAYS = 7
# Models that accept server-side refusal fallbacks on the Claude API.
FALLBACK_MODELS = ("claude-opus-5-5", "claude-opus-5", "claude-fable-5-1", "claude-sonnet-5-5")

SYSTEM_PROMPT = """Bir enerji yönetimi uygulamasında tesis yöneticilerine yazıyorsun.
Sana bir binanın saatlik elektrik tüketiminde bulunan bir anomali olayı veriliyor: zaman
aralığı, tespit türü, beklenenin üstündeki tüketim ve tahmini maliyet.

Türkçe, sade ve kısa yaz: en fazla 3 cümle, madde işareti ve başlık kullanma.
1. Bu olayın en olası nedenini söyle (ör. gece açık kalan klima, aydınlatma, kompresör,
   sayaç arızası), kesin bilmediğini belli ederek.
2. Tesis yöneticisinin bugün yapabileceği somut bir kontrol veya işlem öner.
Verilen sayıları değiştirme, yeni sayı uydurma. Sayaç arızası şüphesi varsa enerji israfı
değil veri sorunu olduğunu belirt."""

PENDING_SQL = """
SELECT a.id, COALESCE(b.name, b.external_id) AS building, b.segment, b.primary_use,
       a.ts_start, a.ts_end, a.source, a.rule_type, a.excess_kwh, a.est_cost_tl, a.explanation
FROM anomalies a
JOIN buildings b USING (building_id)
JOIN scoring_state s USING (building_id)
WHERE a.origin = 'live' AND a.advice IS NULL
  AND a.ts_end <= s.last_scored_ts - make_interval(hours => %(settle)s)
  AND a.ts_end >= s.last_scored_ts - make_interval(days => %(recent)s)
ORDER BY a.est_cost_tl DESC NULLS LAST, a.ts_start
LIMIT %(limit)s
"""

Advisor = Callable[[dict], str | None]


def prompt(event: dict) -> str:
    """The event as plain facts; only fields the dashboard already shows."""
    kind = event["rule_type"] or {"ml_sup": "model (etiketli)", "ml_unsup": "model (beklenen)"}.get(
        event["source"], event["source"]
    )
    lines = [
        f"Bina: {event['building']} (segment: {event['segment'] or 'bilinmiyor'}, "
        f"kullanım: {event['primary_use'] or 'bilinmiyor'})",
        f"Zaman: {event['ts_start']:%Y-%m-%d %H:%M} - {event['ts_end']:%Y-%m-%d %H:%M}"
        " (yerel saat)",
        f"Tespit türü: {kind}",
        f"Beklenenin üstünde tüketim: {tr_number(event['excess_kwh'] or 0)} kWh",
        f"Tahmini maliyet: {tr_number(event['est_cost_tl'] or 0)} TL",
        f"Sistem açıklaması: {event['explanation']}",
    ]
    return "\n".join(lines)


class ClaudeAdvisor:
    def __init__(self, model: str, client: anthropic.Anthropic | None = None):
        self.model = model
        self.client = client or anthropic.Anthropic()

    def __call__(self, event: dict) -> str | None:
        """Advice text, or None when the model declined; API errors propagate."""
        extra: dict = {}
        if self.model in FALLBACK_MODELS:
            extra = {"betas": ["server-side-fallback-2026-07-01"], "fallbacks": "default"}
        response = self.client.beta.messages.create(
            model=self.model,
            max_tokens=4096,
            output_config={"effort": "low"},
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt(event)}],
            **extra,
        )
        if response.stop_reason == "refusal":
            return None
        text = "".join(block.text for block in response.content if block.type == "text")
        return text.strip()[:MAX_ADVICE_CHARS] or None


def advisor_from_settings() -> Advisor | None:
    settings = get_settings()
    return ClaudeAdvisor(settings.llm_model) if settings.llm_enabled else None


def write_advice(conn: psycopg.Connection, advise: Advisor, limit: int = MAX_PER_CYCLE) -> int:
    """Ask for advice on settled events; stops at the first API error and retries next cycle."""
    params = {"settle": SETTLE_HOURS, "recent": RECENT_DAYS, "limit": limit}
    with conn.cursor(row_factory=dict_row) as cur:
        events = cur.execute(PENDING_SQL, params).fetchall()
    written = 0
    for event in events:
        try:
            text = advise(event)
        except (anthropic.APIConnectionError, anthropic.RateLimitError) as exc:
            log.warning("advice paused, will retry next cycle: %s", exc)
            break
        except anthropic.APIStatusError as exc:
            log.error("advice request failed (%s), will retry next cycle", exc.status_code)
            break
        # An empty string marks a declined event, so it is not asked again every cycle.
        conn.execute("UPDATE anomalies SET advice = %s WHERE id = %s", (text or "", event["id"]))
        written += 1
    return written
