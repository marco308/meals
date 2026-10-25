from pydantic import BaseModel, Field


class AssistantIn(BaseModel):
    url: str = Field(min_length=1, max_length=500)
    token: str = Field(min_length=1, max_length=500)
    auto_prepare: bool = False


class AssistantUpdate(BaseModel):
    auto_prepare: bool


class AssistantOut(BaseModel):
    configured: bool
    # Only the host, to say where messages go: a webhook's path can carry a
    # secret of its own, and the token is never returned at all.
    host: str | None
    auto_prepare: bool
