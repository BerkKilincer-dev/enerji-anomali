import io
import json

from src.scoring import alerts


def test_telegram_is_off_without_token_and_chat(monkeypatch, fresh_settings):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:abc")
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    assert alerts.telegram_from_settings() is None
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "42")
    fresh_settings.cache_clear()
    assert alerts.telegram_from_settings() == alerts.Telegram("123:abc", "42")


def test_telegram_posts_the_message(monkeypatch):
    calls = []

    def fake_urlopen(request, timeout):
        calls.append((request.full_url, json.loads(request.data), timeout))
        return io.BytesIO()

    monkeypatch.setattr(alerts.urllib.request, "urlopen", fake_urlopen)
    alerts.Telegram("123:abc", "42").send("hello")
    assert calls == [
        ("https://api.telegram.org/bot123:abc/sendMessage", {"chat_id": "42", "text": "hello"}, 10)
    ]
