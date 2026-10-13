from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class SessionLayout:
    """Caminhos de uma sessão. Única fonte da estrutura de pastas."""

    root: Path

    @property
    def raw(self) -> Path:
        return self.root / "raw"

    @property
    def audio(self) -> Path:
        return self.root / "audio"

    @property
    def frames(self) -> Path:
        return self.root / "frames"

    @property
    def ocr(self) -> Path:
        return self.root / "ocr"

    @property
    def transcript(self) -> Path:
        return self.root / "transcript"

    @property
    def export(self) -> Path:
        return self.root / "export"

    @property
    def logs(self) -> Path:
        return self.root / "logs"

    @property
    def session_json(self) -> Path:
        return self.root / "session.json"

    @property
    def timeline(self) -> Path:
        return self.root / "timeline.jsonl"

    @property
    def audio_wav(self) -> Path:
        return self.audio / "audio.wav"

    @property
    def pipeline_log(self) -> Path:
        return self.logs / "pipeline.log"

    def dirs(self) -> tuple[Path, ...]:
        return (self.raw, self.audio, self.frames, self.ocr, self.transcript, self.export, self.logs)

    def create(self) -> None:
        for d in self.dirs():
            d.mkdir(parents=True, exist_ok=True)
