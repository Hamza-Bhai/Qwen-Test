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


# --- Claude Code CLI provider (uses the owner's own Claude subscription login) ---------------

import subprocess  # noqa: E402


class FakeRun:
    def __init__(self, stdout="", returncode=0, stderr="", error=None):
        self.stdout, self.returncode, self.stderr, self.error = stdout, returncode, stderr, error
        self.calls = []

    def __call__(self, args, **kw):
        self.calls.append((args, kw))
        if self.error:
            raise self.error
        return subprocess.CompletedProcess(args, self.returncode, stdout=self.stdout, stderr=self.stderr)


@pytest.fixture()
def cli_db(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "claude_cli")
    monkeypatch.setenv("AI_API_KEY", "")
    monkeypatch.setenv("CLAUDE_CLI_MODEL", "sonnet")
    monkeypatch.delenv("AI_DAILY_CAP", raising=False)
    get_settings.cache_clear()
    monkeypatch.setattr("app.studio.services.ai_client.shutil.which", lambda name: "C:/bin/claude.exe")
    with open_db() as session:
        yield session


def _cli_ok(text, **extra):
    return json.dumps({"type": "result", "subtype": "success", "is_error": False, "result": text, **extra})


def test_cli_configured_when_claude_is_installed(cli_db, monkeypatch):
    assert AIClient(cli_db).configured is True
    monkeypatch.setattr("app.studio.services.ai_client.shutil.which", lambda name: None)
    assert AIClient(cli_db).configured is False
    with pytest.raises(AINotConfigured, match="Claude Code CLI"):
        AIClient(cli_db).complete_json("s", "u")


def test_cli_runs_print_mode_without_tools_and_parses_result(cli_db, monkeypatch):
    run = FakeRun(_cli_ok('{"answers": []}'))
    monkeypatch.setattr("app.studio.services.ai_client.subprocess.run", run)
    data, model = AIClient(cli_db).complete_json("system text", "user text")
    assert data == {"answers": []} and model == "claude-cli:sonnet"
    args, kw = run.calls[0]
    assert args[0] == "C:/bin/claude.exe" and "-p" in args
    assert args[args.index("--output-format") + 1] == "json"
    assert args[args.index("--tools") + 1] == ""
    assert args[args.index("--system-prompt") + 1] == "system text"
    assert args[args.index("--model") + 1] == "sonnet"
    assert "--no-session-persistence" in args
    assert "--safe-mode" in args  # no CLAUDE.md, hooks, plugins or MCP servers from the owner's setup
    assert kw["input"] == "user text" and kw["timeout"] >= 120
    assert kw["cwd"] and "YT-growth-intelligence" not in kw["cwd"]  # never inside the project
    assert AIClient(cli_db).used_today() == 1


def test_cli_error_result_is_ai_error(cli_db, monkeypatch):
    monkeypatch.setattr("app.studio.services.ai_client.subprocess.run",
                        FakeRun(json.dumps({"type": "result", "is_error": True, "result": "Usage limit reached"})))
    with pytest.raises(AIError, match="Usage limit reached"):
        AIClient(cli_db).complete_json("s", "u")


def test_cli_nonzero_exit_is_ai_error(cli_db, monkeypatch):
    monkeypatch.setattr("app.studio.services.ai_client.subprocess.run",
                        FakeRun("", returncode=1, stderr="Invalid API key · Please run /login"))
    with pytest.raises(AIError, match="/login"):
        AIClient(cli_db).complete_json("s", "u")


def test_cli_timeout_not_counted(cli_db, monkeypatch):
    monkeypatch.setattr("app.studio.services.ai_client.subprocess.run",
                        FakeRun(error=subprocess.TimeoutExpired(cmd="claude", timeout=300)))
    client = AIClient(cli_db)
    with pytest.raises(AIError, match="did not answer"):
        client.complete_json("s", "u")
    assert client.used_today() == 0


def test_cli_cap_applies(cli_db, monkeypatch):
    monkeypatch.setattr("app.studio.services.ai_client.subprocess.run", FakeRun(_cli_ok('{"a": 1}')))
    client = AIClient(cli_db)
    for _ in range(3):
        client.complete_json("s", "u")
    with pytest.raises(AIBudgetExceeded):
        client.complete_json("s", "u")
