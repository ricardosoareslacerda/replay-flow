from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

EventSource = Literal["transcript", "frame", "ocr", "mouse", "key", "window", "marker", "audio"]


class TimelineEvent(BaseModel):
    """Um fato observado na sessão, em segundos desde o início do vídeo."""

    t: float = Field(ge=0)
    source: EventSource
    kind: str
    data: dict[str, Any] = Field(default_factory=dict)
