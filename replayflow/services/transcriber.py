"""Transcrição local com faster-whisper."""

from __future__ import annotations

import json
import logging
import math
import os
import re
import sys
import threading
from pathlib import Path

from pydantic import BaseModel

from replayflow.core.errors import CancelledError, ReplayFlowError, ToolMissingError

log = logging.getLogger(__name__)

_GPU_ERROR = re.compile(r"cublas|cudnn|cuda|cudart", re.IGNORECASE)


class Segment(BaseModel):
    start: float
    end: float
    text: str
    confidence: float | None = None
    words: list[tuple[float, float, str]] = []  # (início, fim, palavra): distribui a fala pelos passos


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
               cancel: threading.Event | None = None, hotwords: str | None = None) -> list[Segment]:
    """offline=True (modo privado) só usa modelos já baixados; nunca acessa a rede."""
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        raise ToolMissingError("faster-whisper não instalado: pip install -e .[media]") from None

    device = gpu_device()
    try:
        return _transcribe(WhisperModel, device, wav, model_name, language, offline, cancel, hotwords)
    except RuntimeError as e:
        if device == "cpu" or not _GPU_ERROR.search(str(e)):
            raise
        log.warning("GPU indisponível para a transcrição (%s); usando a CPU", e)
        return _transcribe(WhisperModel, "cpu", wav, model_name, language, offline, cancel, hotwords)


def _register_cuda_dlls() -> None:
    """Põe no caminho de DLLs o cuBLAS/cuDNN instalados via pip (nvidia-*-cu12) na pasta portátil."""
    if sys.platform != "win32":
        return
    import site

    for sp in site.getsitepackages():
        for bin_dir in Path(sp).glob("nvidia/*/bin"):
            os.add_dll_directory(str(bin_dir))
            os.environ["PATH"] = f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}"


def gpu_device() -> str:
    """'cuda' quando há placa NVIDIA utilizável; senão 'cpu'. A GPU transcreve ~2x mais rápido."""
    try:
        _register_cuda_dlls()
        import ctranslate2

        return "cuda" if ctranslate2.get_cuda_device_count() > 0 else "cpu"
    except Exception:
        return "cpu"


def _transcribe(whisper_model, device: str, wav: Path, model_name: str, language: str, offline: bool,  # type: ignore[no-untyped-def]
                cancel: threading.Event | None, hotwords: str | None = None) -> list[Segment]:
    try:
        model = whisper_model(model_name, device=device, compute_type="int8", local_files_only=offline)
    except RuntimeError as e:
        if device != "cpu" and _GPU_ERROR.search(str(e)):
            raise
        raise ReplayFlowError(f"Falha ao carregar o modelo '{model_name}': {e}") from e
    except Exception as e:  # HF lança tipos variados para modelo ausente
        if offline:
            raise ReplayFlowError(
                f"Modelo '{model_name}' não está no cache local. Baixe uma vez com "
                f"`replayflow download-model {model_name}` (desativa o modo privado só para isso)."
            ) from e
        raise ReplayFlowError(f"Falha ao carregar o modelo '{model_name}': {e}") from e

    raw_segments, info = model.transcribe(str(wav), language=language, vad_filter=True, word_timestamps=True,
                                           hotwords=hotwords or None)  # nomes vistos na tela: "Au!Happy", não "ao rap"
    segments: list[Segment] = []
    for s in raw_segments:  # gerador: a transcrição avança a cada iteração
        if cancel is not None and cancel.is_set():
            raise CancelledError()
        segments.append(Segment(start=s.start, end=s.end, text=s.text.strip(),
                                confidence=round(math.exp(s.avg_logprob), 3),
                                words=[(w.start, w.end, w.word.strip()) for w in (s.words or [])]))
    log.info("%d trechos transcritos (%s)", len(segments), "GPU" if device == "cuda" else "CPU",
             extra={"segments": len(segments), "duration": info.duration, "device": device})
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
