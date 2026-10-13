"""Transcrição local com faster-whisper."""

from __future__ import annotations

import json
import logging
import math
import threading
from pathlib import Path

from pydantic import BaseModel

from replayflow.core.errors import CancelledError, ReplayFlowError, ToolMissingError

log = logging.getLogger(__name__)


class Segment(BaseModel):
    start: float
    end: float
    text: str
    confidence: float | None = None


def mmss(seconds: float) -> str:
    total = int(seconds)
    return f"{total // 60:02d}:{total % 60:02d}"


def to_markdown(segments: list[Segment], title: str) -> str:
    lines = [f"# Transcrição: {title}", ""]
    lines += [f"**[{mmss(s.start)} - {mmss(s.end)}]** {s.text}" for s in segments]
    return "\n".join(lines) + "\n"


def write_outputs(segments: list[Segment], transcript_dir: Path, title: str) -> None:
    (transcript_dir / "transcript.json").write_text(
        json.dumps([s.model_dump() for s in segments], ensure_ascii=False, indent=2), encoding="utf-8")
    (transcript_dir / "transcript.md").write_text(to_markdown(segments, title), encoding="utf-8")


def transcribe(wav: Path, model_name: str, language: str, offline: bool,
               cancel: threading.Event | None = None) -> list[Segment]:
    """offline=True (modo privado) só usa modelos já baixados; nunca acessa a rede."""
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        raise ToolMissingError("faster-whisper não instalado: pip install -e .[media]") from None

    try:
        model = WhisperModel(model_name, device="auto", compute_type="int8", local_files_only=offline)
    except Exception as e:  # HF lança tipos variados para modelo ausente
        if offline:
            raise ReplayFlowError(
                f"Modelo '{model_name}' não está no cache local. Baixe uma vez com "
                f"`replayflow download-model {model_name}` (desativa o modo privado só para isso)."
            ) from e
        raise ReplayFlowError(f"Falha ao carregar o modelo '{model_name}': {e}") from e

    raw_segments, info = model.transcribe(str(wav), language=language, vad_filter=True)
    segments: list[Segment] = []
    for s in raw_segments:  # gerador: a transcrição avança a cada iteração
        if cancel is not None and cancel.is_set():
            raise CancelledError()
        segments.append(Segment(start=s.start, end=s.end, text=s.text.strip(),
                                confidence=round(math.exp(s.avg_logprob), 3)))
    log.info("%d trechos transcritos", len(segments), extra={"segments": len(segments), "duration": info.duration})
    return segments


def download_model(model_name: str) -> None:
    from faster_whisper import WhisperModel

    WhisperModel(model_name, device="cpu", compute_type="int8")


def version() -> str:
    try:
        from importlib.metadata import version as v

        return v("faster-whisper")
    except Exception:
        return "ausente"
