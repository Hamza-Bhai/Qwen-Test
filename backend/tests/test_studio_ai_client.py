"""OpenAI-compatible AI client: JSON mode, daily cap, errors (MockTransport, no network)."""

import json

import httpx
import pytest

from app.core.config import get_settings
from app.studio.services.ai_client import AIBudgetExceeded, AIClient, AIError, AINotConfigured
from tests.studio_fakes import open_db


@pytest.fixture()
def db(monkeypatch):
    monkeypatch.setenv("AI_API_KEY", "sk-test")
    monkeypatch.setenv("AI_BASE_URL", "https://ai.example/v1")
    monkeypatch.setenv("AI_MODEL", "test-model")
    monkeypatch.delenv("AI_DAILY_CAP", raising=False)
    get_settings.cache_clear()
    with open_db() as session:
        yield session


def _reply(content: str, status: int = 200):
    def handler(request: httpx.Request) -> httpx.Response:
        handler.requests.append(request)
        if status != 200:
            return httpx.Response(status, json={"error": {"message": "Invalid API key"}})
        return httpx.Response(200, json={"model": "test-model-2026", "choices": [{"message": {"content": content}}]})

    handler.requests = []
    return handler


def _client(db, handler):
    return AIClient(db, http=httpx.Client(transport=httpx.MockTransport(handler)))


def test_not_configured(db, monkeypatch):
    monkeypatch.setenv("AI_API_KEY", "")
    get_settings.cache_clear()
    with pytest.raises(AINotConfigured, match="AI_API_KEY"):
        _client(db, _reply("{}")).complete_json("s", "u")


def test_sends_json_mode_and_model(db):
    handler = _reply('{"ok": true}')
    data, model = _client(db, handler).complete_json("system text", "user text")
    assert data == {"ok": True} and model == "test-model-2026"
    req = handler.requests[0]
    assert str(req.url) == "https://ai.example/v1/chat/completions"
    assert req.headers["Authorization"] == "Bearer sk-test"
    body = json.loads(req.content)
    assert body["model"] == "test-model" and body["response_format"] == {"type": "json_object"}
    assert body["messages"] == [{"role": "system", "content": "system text"}, {"role": "user", "content": "user text"}]


def test_json_inside_code_fence_is_accepted(db):
    data, _ = _client(db, _reply('```json\n{"a": 1}\n```')).complete_json("s", "u")
    assert data == {"a": 1}


def test_cap_blocks_fourth_call(db):
    client = _client(db, _reply('{"a": 1}'))
    for _ in range(3):
        client.complete_json("s", "u")
    assert client.used_today() == 3 and client.cap == 3
    with pytest.raises(AIBudgetExceeded):
        client.complete_json("s", "u")


def test_network_error_not_counted(db):
    def offline(request):
        raise httpx.ConnectError("no route")

    client = _client(db, offline)
    with pytest.raises(AIError) as info:
        client.complete_json("s", "u")
    assert info.value.status_code == 502 and client.used_today() == 0


def test_provider_401_is_error_and_counted(db):
    client = _client(db, _reply("", status=401))
    with pytest.raises(AIError, match="Invalid API key") as info:
        client.complete_json("s", "u")
    assert info.value.status_code == 502 and client.used_today() == 1


def test_non_json_content_rejected(db):
    client = _client(db, _reply("Sure! Here are some ideas..."))
    with pytest.raises(AIError, match="did not return JSON"):
        client.complete_json("s", "u")
    assert client.used_today() == 1
