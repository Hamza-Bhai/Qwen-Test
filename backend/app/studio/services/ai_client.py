"""OpenAI-compatible chat-completions client for the Growth Studio advisor (``AI_*`` settings).

JSON mode only, a hard daily cap (``AI_DAILY_CAP``) tracked in ``QuotaUsage``
(service ``"ai"``). A call counts once the provider answered; a network failure
does not count.
"""

import json
import re
import shutil
import subprocess
import tempfile

import httpx
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.services.quota import QuotaTracker

SERVICE = "ai"
TIMEOUT = 120.0
TEMPERATURE = 0.4
CLI_TIMEOUT = 300


class AINotConfigured(RuntimeError):
    pass


class AIBudgetExceeded(RuntimeError):
    pass


class AIError(RuntimeError):
    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code


def _parse_json(content: str) -> dict:
    text = content.strip()
    fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", text, flags=re.DOTALL)
    if fenced:
        text = fenced.group(1)
    try:
        data = json.loads(text)
    except ValueError:
        raise AIError(502, "The model did not return JSON.") from None
    if not isinstance(data, dict):
        raise AIError(502, "The model did not return a JSON object.")
    return data


class AIClient:
    def __init__(self, db: Session, *, http: httpx.Client | None = None) -> None:
        self.db = db
        self.http = http
        self.quota = QuotaTracker(db, service=SERVICE)

    @property
    def cap(self) -> int:
        return get_settings().ai_daily_cap

    @property
    def uses_cli(self) -> bool:
        return get_settings().ai_provider == "claude_cli"

    @property
    def configured(self) -> bool:
        if self.uses_cli:
            return shutil.which(get_settings().claude_cli_path) is not None
        return bool(get_settings().ai_api_key)

    def used_today(self) -> int:
        return self.quota.used_today()

    def _check_budget(self) -> None:
        if self.used_today() >= self.cap:
            raise AIBudgetExceeded(f"Daily AI limit reached ({self.used_today()}/{self.cap} calls today). Try again tomorrow.")

    def complete_json(self, system: str, user: str) -> tuple[dict, str]:
        if self.uses_cli:
            return self._complete_with_cli(system, user)
        settings = get_settings()
        if not settings.ai_api_key:
            raise AINotConfigured("AI is not configured: add AI_API_KEY (and AI_BASE_URL / AI_MODEL) to backend/.env.")
        self._check_budget()
        http = self.http or httpx.Client(timeout=TIMEOUT)
        try:
            response = http.request(
                "POST", f"{settings.ai_base_url.rstrip('/')}/chat/completions",
                headers={"Authorization": f"Bearer {settings.ai_api_key}"},
                json={
                    "model": settings.ai_model,
                    "temperature": TEMPERATURE,
                    "response_format": {"type": "json_object"},
                    "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
                },
            )
        except httpx.HTTPError as exc:
            raise AIError(502, f"Could not reach the AI provider ({type(exc).__name__}).") from None
        self.quota.record("chat.completions", 1)
        if response.status_code != 200:
            try:
                message = response.json().get("error", {}).get("message")
            except ValueError:
                message = None
            raise AIError(502, f"AI provider error {response.status_code}: {message or 'no details'}")
        body = response.json()
        try:
            content = body["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError):
            raise AIError(502, "The AI provider returned an unexpected response.") from None
        return _parse_json(content), body.get("model") or settings.ai_model

    def _complete_with_cli(self, system: str, user: str) -> tuple[dict, str]:
        """``claude -p`` with every tool disabled, run in an empty temp folder (no project files or CLAUDE.md)."""
        settings = get_settings()
        executable = shutil.which(settings.claude_cli_path)
        if executable is None:
            raise AINotConfigured(
                "AI_PROVIDER=claude_cli but the Claude Code CLI was not found. Install it and run `claude` once to sign in."
            )
        self._check_budget()
        args = [executable, "-p", "--output-format", "json", "--tools", "", "--system-prompt", system,
                "--no-session-persistence", "--strict-mcp-config", "--safe-mode"]
        if settings.claude_cli_model:
            args += ["--model", settings.claude_cli_model]
        try:
            with tempfile.TemporaryDirectory(prefix="yt-studio-ai-") as workdir:
                done = subprocess.run(args, input=user, capture_output=True, text=True, encoding="utf-8",
                                      timeout=CLI_TIMEOUT, cwd=workdir)
        except subprocess.TimeoutExpired:
            raise AIError(502, f"Claude did not answer within {CLI_TIMEOUT} seconds.") from None
        except OSError as exc:
            raise AIError(502, f"Could not start the Claude Code CLI ({exc}).") from None
        self.quota.record("claude.cli", 1)
        try:
            body = json.loads(done.stdout)
        except ValueError:
            body = None
        if done.returncode != 0 or not isinstance(body, dict) or body.get("is_error"):
            detail = (body or {}).get("result") if isinstance(body, dict) else None
            detail = detail or (done.stderr or done.stdout or "no details").strip()[:500]
            raise AIError(502, f"Claude Code CLI error: {detail}")
        return _parse_json(body.get("result") or ""), f"claude-cli:{settings.claude_cli_model or 'default'}"
