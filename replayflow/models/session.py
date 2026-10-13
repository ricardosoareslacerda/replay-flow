from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, Field

from replayflow.models.config import PipelineConfig


class Stage(StrEnum):
    AUDIO = "audio"
    TRANSCRIPT = "transcript"
    FRAMES = "frames"
    OCR = "ocr"
    INPUT = "input"
    EXPORT = "export"


class StageStatus(StrEnum):
    PENDING = "pending"
    DONE = "done"
    FAILED = "failed"
    SKIPPED = "skipped"


class StageState(BaseModel):
    status: StageStatus = StageStatus.PENDING
    finished_at: datetime | None = None
    config_fingerprint: str | None = None
    error: str | None = None


class Session(BaseModel):
    id: str
    name: str
    created_at: datetime
    root: Path
    video_file: str | None = None
    video_sha256: str | None = None
    duration_s: float | None = None
    config: PipelineConfig = PipelineConfig()
    stages: dict[Stage, StageState] = Field(default_factory=lambda: {s: StageState() for s in Stage})

    def is_done(self, stage: Stage) -> bool:
        """Etapa concluída com a config atual: pode ser pulada ao retomar."""
        state = self.stages[stage]
        return state.status in (StageStatus.DONE, StageStatus.SKIPPED) and (
            state.config_fingerprint == self.config.fingerprint()
        )
