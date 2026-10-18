import json

from mesabot.ai import Gemini, _model_rank
from mesabot.bot import handle_message


class FakeResponse:
    def __init__(self, status, data):
        self.status_code, self._data = status, data
        self.text = json.dumps(data)

    def json(self):
        return self._data


class FakeGeminiHTTP:
    """Lists models, asks for one tool call, then answers using the tool result."""

    def __init__(self, fail_first_model=False):
        self.calls = []
        self.fail_first_model = fail_first_model

    def request(self, method, url, json=None, timeout=None, headers=None):
        self.calls.append((method, url, json))
        assert headers["x-goog-api-key"] == "k"
        if url.endswith("/models?pageSize=200"):
            return FakeResponse(200, {"models": [
                {"name": "models/gemini-9-flash", "supportedGenerationMethods": ["generateContent"]},
                {"name": "models/gemini-9-flash-lite", "supportedGenerationMethods": ["generateContent"]},
                {"name": "models/gemini-9-pro", "supportedGenerationMethods": ["generateContent"]},
                {"name": "models/gemini-9-flash-image", "supportedGenerationMethods": ["generateContent"]},
            ]})
        if self.fail_first_model and "gemini-9-flash:" in url:
            return FakeResponse(429, {"error": {"message": "quota"}})
        contents = json["contents"]
        last = contents[-1]["parts"][0]
        if "functionResponse" in last:
            result = last["functionResponse"]["response"]["result"]
            return FakeResponse(200, {"candidates": [{"content": {"role": "model", "parts": [
                {"text": f"You have *{result['count']}* sessions."}]}}]})
        return FakeResponse(200, {"candidates": [{"content": {"role": "model", "parts": [
            {"functionCall": {"name": "get_schedule",
                              "args": {"from_date": "2026-10-19", "to_date": "2026-10-25"}},
             "thoughtSignature": "sig"}]}}]})


def test_model_ranking_prefers_stable_full_newest():
    names = ["gemini-3-flash-preview", "gemini-3.5-flash", "gemini-3.5-flash-lite", "gemini-2.5-flash"]
    assert sorted(names, key=_model_rank, reverse=True)[0] == "gemini-3.5-flash"


def test_gemini_answers_with_tools_and_keeps_signature(make_svc):
    http = FakeGeminiHTTP()
    svc = make_svc("2026-10-19T19:00:00+05:30", ai=Gemini("k", http=http))
    reply = handle_message(svc, "what does my week look like")
    assert reply == "You have *6* sessions."
    gen = [c for c in http.calls if c[0] == "POST"]
    assert "gemini-9-flash:generateContent" in gen[0][1]          # best model picked, not lite/pro/image
    assert gen[1][2]["contents"][1]["parts"][0]["thoughtSignature"] == "sig"  # model turn echoed as-is
    # Follow-ups see the previous exchange.
    handle_message(svc, "and next week?")
    follow_up = [c for c in http.calls if c[0] == "POST"][-2][2]["contents"]
    assert follow_up[0]["parts"][0]["text"] == "what does my week look like"
    assert follow_up[1]["parts"][0]["text"] == "You have *6* sessions."
    assert follow_up[2]["parts"][0]["text"] == "and next week?"


def test_gemini_falls_back_to_next_model_on_quota(make_svc):
    http = FakeGeminiHTTP(fail_first_model=True)
    svc = make_svc("2026-10-19T19:00:00+05:30", ai=Gemini("k", http=http))
    assert "sessions" in handle_message(svc, "what does my week look like")
    assert any("gemini-9-flash-lite:generateContent" in c[1] for c in http.calls)


def test_quick_rules_skip_gemini(make_svc):
    http = FakeGeminiHTTP()
    svc = make_svc("2026-10-19T19:00:00+05:30", ai=Gemini("k", http=http))
    assert "Better not" in handle_message(svc, "can i skip data 102")
    assert "2 SQL classes" in handle_message(svc, "how many sql classes this month")
    assert not [c for c in http.calls if c[0] == "POST"]


def test_gemini_failure_falls_back_to_rules(make_svc):
    class Broken:
        def request(self, *a, **k):
            return FakeResponse(500, {"error": {"message": "down"}})
    svc = make_svc("2026-10-19T19:00:00+05:30", ai=Gemini("k", model="gemini-x", http=Broken()))
    assert "Yes, these need attention" in handle_message(svc, "am I at risk of low attendance")
