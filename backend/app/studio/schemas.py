"""Request/response models for the Growth Studio API."""

from pydantic import BaseModel


class SerpApiStatus(BaseModel):
    configured: bool
    month: str
    used: int
    cap: int
    remaining: int


class OwnChannelBrief(BaseModel):
    id: int
    title: str


class OwnChannelDetail(OwnChannelBrief):
    subscriber_count: int | None


class OwnChannelResponse(BaseModel):
    channel: OwnChannelDetail | None
    candidates: list[OwnChannelBrief]


class OwnChannelPayload(BaseModel):
    channel_id: int


class StudioStatus(BaseModel):
    serpapi: SerpApiStatus
    keywords: list[str]
    own_channel: OwnChannelBrief | None = None
    auto_discovery: bool = False


class AutoDiscoveryPayload(BaseModel):
    enabled: bool


class KeywordsPayload(BaseModel):
    keywords: list[str]
