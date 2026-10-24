"""OpenAI-compatible chat-completions client for the Growth Studio advisor (``AI_*`` settings).

JSON mode only, a hard daily cap (``AI_DAILY_CAP``) tracked in ``QuotaUsage``
(service ``"ai"``). A call counts once the provider answered; a network failure
does not count.
"""

import json
import re

import httpx
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.services.quota import QuotaTracker

SERVICE = "ai"
TIMEOUT = 120.0
TEMPERATURE = 0.4


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
    def configured(self) -> bool:
        return bool(get_settings().ai_api_key)

    def used_today(self) -> int:
        return self.quota.used_today()

    def complete_json(self, system: str, user: str) -> tuple[dict, str]:
        settings = get_settings()
        if not settings.ai_api_key:
            raise AINotConfigured("AI is not configured: add AI_API_KEY (and AI_BASE_URL / AI_MODEL) to backend/.env.")
        if self.used_today() >= self.cap:
            raise AIBudgetExceeded(f"Daily AI limit reached ({self.used_today()}/{self.cap} calls today). Try again tomorrow.")
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
