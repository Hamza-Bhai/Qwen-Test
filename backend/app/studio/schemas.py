"""Request/response models for the Growth Studio API."""

from pydantic import BaseModel


class SerpApiStatus(BaseModel):
    configured: bool
    month: str
    used: int
    cap: int
    remaining: int


class StudioStatus(BaseModel):
    serpapi: SerpApiStatus
    keywords: list[str]


class KeywordsPayload(BaseModel):
    keywords: list[str]
