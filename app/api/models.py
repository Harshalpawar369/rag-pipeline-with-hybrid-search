"""Request schemas. Every inbound message is validated before use."""
from __future__ import annotations

from typing import Annotated, Literal, Union
from uuid import UUID

from pydantic import BaseModel, Field, TypeAdapter


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=2000)
    matter_ids: list[UUID] = Field(min_length=1, max_length=20)
    mode: Literal["hybrid", "vector", "keyword"] = "hybrid"
    top_n: int = Field(default=8, ge=1, le=20)
    include_original: bool = False


class SummarizeRequest(BaseModel):
    matter_ids: list[UUID] = Field(min_length=1, max_length=20)


class AskFrame(BaseModel):
    type: Literal["ask"]
    request_id: str = Field(min_length=1, max_length=64)
    session_id: str = Field(min_length=1, max_length=64)
    matter_ids: list[UUID] = Field(min_length=1, max_length=20)
    question: str = Field(min_length=1, max_length=2000)
    include_original: bool = False


class CancelFrame(BaseModel):
    type: Literal["cancel"]
    request_id: str = Field(min_length=1, max_length=64)


class PingFrame(BaseModel):
    type: Literal["ping"]


ClientFrame = Annotated[Union[AskFrame, CancelFrame, PingFrame], Field(discriminator="type")]
client_frame_adapter: TypeAdapter = TypeAdapter(ClientFrame)
