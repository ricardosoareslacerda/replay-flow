from __future__ import annotations

import hashlib
import os
import sys
from enum import StrEnum
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

WhisperModel = Literal["tiny", "base", "small", "medium", "large-v3"]


class FrameMode(StrEnum):
    INTERVAL = "interval"
    SCENE = "scene"
    BOTH = "both"


def portable_home() -> Path | None:
    """Raiz da instalação portátil (definida pelo ReplayFlow.bat), ou None."""
    home = os.environ.get("REPLAYFLOW_HOME")
    return Path(home) if home else None


def default_base_dir() -> Path:
    if home := portable_home():
        return home / "Sessions"
    if sys.platform == "win32":
        return Path.home() / "Pipeline-Capture" / "Sessions"
    return Path.home() / "ReplayFlow" / "Sessions"


class PipelineConfig(BaseModel):
    """Parâmetros que afetam o resultado. Entram no hash de reprodutibilidade."""

    model_config = ConfigDict(frozen=True)

    language: str = "pt"
    whisper_model: WhisperModel = "small"
    transcription_enabled: bool = True
    ocr_enabled: bool = True
    ocr_lang: str = "por+eng"  # telas de trabalho misturam português e inglês; só "por" lê inglês errado
    frame_mode: FrameMode = FrameMode.BOTH
    frame_interval_s: float = Field(default=5.0, gt=0)
    scene_threshold: float = Field(default=0.3, gt=0, lt=1)
    dedupe_min_gap_s: float = Field(default=1.0, ge=0)
    dedupe_hash_distance: int = Field(default=5, ge=0)
    max_width: int = Field(default=1280, ge=320)
    jpg_quality: int = Field(default=85, ge=1, le=100)
    input_offset_s: float = 0.0  # alinha o relógio da captura de input ao vídeo
    redact_sensitive: bool = True  # mascara e-mails, CPF/CNPJ e cartões no pacote (só afeta o export)
    # Área da área de trabalho que foi gravada (monitor ou janela): esquerda, topo, largura, altura.
    capture_region: tuple[int, int, int, int] | None = None

    def fingerprint(self) -> str:
        # Opções que só mudam o export não invalidam as etapas já processadas.
        return hashlib.sha256(self.model_dump_json(exclude={"redact_sensitive"}, exclude_none=True).encode()).hexdigest()


class CaptureConfig(BaseModel):
    """Captura de input ao vivo. Nunca persiste o conteúdo digitado em modo sigiloso."""

    model_config = ConfigDict(frozen=True)

    mouse_enabled: bool = False
    keys_enabled: bool = False
    active_window_enabled: bool = False
    secret_hotkey: str = "f9"


class AppConfig(BaseModel):
    base_dir: Path = Field(default_factory=default_base_dir)
    private_mode: bool = True
    pipeline: PipelineConfig = PipelineConfig()
    capture: CaptureConfig = CaptureConfig()
