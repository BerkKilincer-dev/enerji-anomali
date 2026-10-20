from datetime import datetime
from types import SimpleNamespace

from src.scoring import advice

EVENT = {
    "id": 7,
    "building": "Acme HQ",
    "segment": "office",
    "primary_use": "Office",
    "ts_start": datetime(2026, 10, 19, 1),
    "ts_end": datetime(2026, 10, 19, 5),
    "source": "rule",
    "rule_type": "night_base_load",
    "excess_kwh": 1270.0,
    "est_cost_tl": 4445.0,
    "explanation": "Gece tüketimi son haftalardaki aynı saatin 1,6 katına çıktı.",
}


class FakeMessages:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self.response


def fake_client(stop_reason="end_turn", text="Klima gece açık kalmış olabilir."):
    content = [SimpleNamespace(type="thinking"), SimpleNamespace(type="text", text=text)]
    messages = FakeMessages(SimpleNamespace(stop_reason=stop_reason, content=content))
    return SimpleNamespace(beta=SimpleNamespace(messages=messages)), messages


def test_prompt_carries_the_event_facts_in_turkish():
    text = advice.prompt(EVENT)
    assert "Acme HQ" in text and "night_base_load" in text
    assert "1.270 kWh" in text and "4.445 TL" in text
    assert "2026-10-19 01:00 - 2026-10-19 05:00" in text


def test_advisor_returns_text_and_uses_fallbacks_where_supported():
    client, messages = fake_client()
    assert advice.ClaudeAdvisor("claude-opus-5-5", client)(EVENT) == (
        "Klima gece açık kalmış olabilir."
    )
    call = messages.calls[0]
    assert call["model"] == "claude-opus-5-5" and call["fallbacks"] == "default"
    assert call["system"] == advice.SYSTEM_PROMPT
    assert call["output_config"] == {"effort": "low"}

    client, messages = fake_client()
    advice.ClaudeAdvisor("claude-haiku-5-5", client)(EVENT)
    assert "fallbacks" not in messages.calls[0] and "betas" not in messages.calls[0]


def test_advisor_returns_none_on_refusal_and_caps_length():
    client, _ = fake_client(stop_reason="refusal")
    assert advice.ClaudeAdvisor("claude-opus-5-5", client)(EVENT) is None
    client, _ = fake_client(text="x" * 5000)
    assert len(advice.ClaudeAdvisor("claude-opus-5-5", client)(EVENT)) == advice.MAX_ADVICE_CHARS


def test_advice_is_off_without_an_api_key(monkeypatch, fresh_settings):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert advice.advisor_from_settings() is None
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setenv("LLM_MODEL", "claude-haiku-5-5")
    fresh_settings.cache_clear()
    advisor = advice.advisor_from_settings()
    assert isinstance(advisor, advice.ClaudeAdvisor) and advisor.model == "claude-haiku-5-5"
