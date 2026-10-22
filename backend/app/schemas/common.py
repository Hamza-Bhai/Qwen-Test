"""Shared Pydantic response envelopes."""

from pydantic import BaseModel


class HealthResponse(BaseModel):
    status: str
    app: str
    version: str
    scheduler_running: bool = False


class ErrorResponse(BaseModel):
    error: str
    detail: str | None = None
