"""FFmpeg via subprocess: duração, áudio WAV 16 kHz mono e extração de frames com timestamp."""

from __future__ import annotations

import logging
import re
import shutil
import subprocess
import sys
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path

from replayflow.core.errors import CancelledError, ReplayFlowError, ToolMissingError
from replayflow.models import FrameMode, PipelineConfig
from replayflow.services import imagehash

log = logging.getLogger(__name__)

# Sem isso cada chamada do FFmpeg abre e fecha uma janela de console quando a UI roda via pythonw.
_NO_WINDOW: dict = {"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {}

_PTS_RE = re.compile(r"pts_time:\s*([0-9.]+)")


@dataclass(frozen=True)
class ExtractedFrame:
    file: Path
    t: float
    scene_change: bool


def _bin(name: str) -> str:
    path = shutil.which(name)
    if not path:
        raise ToolMissingError(f"{name} não encontrado no PATH. Instale o FFmpeg (veja o README).")
    return path


def _run(args: list[str], cancel: threading.Event | None = None) -> str:
    """Executa e devolve o stderr (onde o FFmpeg escreve showinfo). Cancela matando o processo."""
    log.info("executando %s", Path(args[0]).stem, extra={"cmd": args})
    proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                            encoding="utf-8", errors="replace", **_NO_WINDOW)
    result: dict[str, str] = {}
    reader = threading.Thread(target=lambda: result.update(zip(("out", "err"), proc.communicate())))
    reader.start()
    while reader.is_alive():
        reader.join(0.2)
        if cancel is not None and cancel.is_set():
            proc.kill()
            reader.join()
            raise CancelledError()
    if proc.returncode != 0:
        tail = "\n".join(result.get("err", "").strip().splitlines()[-5:])
        raise ReplayFlowError(f"{Path(args[0]).stem} falhou (código {proc.returncode}):\n{tail}")
    return result.get("err", "")


def probe_duration(video: Path) -> float:
    out = subprocess.run(
        [_bin("ffprobe"), "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(video)],
        capture_output=True, text=True, **_NO_WINDOW,
    )
    try:
        return float(out.stdout.strip())
    except ValueError:
        raise ReplayFlowError(f"Não foi possível ler a duração de {video.name}: {out.stderr.strip()}") from None


def audio_streams(video: Path) -> int:
    out = subprocess.run(
        [_bin("ffprobe"), "-v", "error", "-select_streams", "a", "-show_entries", "stream=index", "-of", "csv=p=0", str(video)],
        capture_output=True, text=True, **_NO_WINDOW,
    )
    return len(out.stdout.split())


def has_audio(video: Path) -> bool:
    return audio_streams(video) > 0


def video_width(video: Path) -> int:
    out = subprocess.run(
        [_bin("ffprobe"), "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width", "-of", "csv=p=0", str(video)],
        capture_output=True, text=True, **_NO_WINDOW,
    )
    try:
        return int(out.stdout.split()[0])
    except (IndexError, ValueError):
        raise ReplayFlowError(f"Não foi possível ler a largura de {video.name}: {out.stderr.strip()}") from None


def extract_audio(video: Path, wav: Path, cancel: threading.Event | None = None, stream: int = 0) -> None:
    """WAV 16 kHz mono de uma trilha. Gravações do OBS do ReplayFlow: trilha 0 = microfone, 1 = computador."""
    _run([_bin("ffmpeg"), "-y", "-hide_banner", "-i", str(video), "-map", f"0:a:{stream}", "-vn", "-ac", "1",
          "-ar", "16000", "-c:a", "pcm_s16le", str(wav)], cancel)


_MEAN_RE = re.compile(r"mean_volume:\s*(-?[0-9.]+|-inf) dB")


def mean_volume(video: Path, stream: int = 0) -> float | None:
    """Volume médio (dB) de uma trilha de áudio; None se não houver a trilha."""
    if audio_streams(video) <= stream:
        return None
    stderr = _run([_bin("ffmpeg"), "-hide_banner", "-i", str(video), "-map", f"0:a:{stream}",
                   "-af", "volumedetect", "-f", "null", "-"])
    m = _MEAN_RE.search(stderr)
    return None if not m else float("-inf") if m.group(1) == "-inf" else float(m.group(1))


_SILENCE_RE = re.compile(r"silence_(start|end):\s*(-?[0-9.]+)")


def parse_sounds(stderr: str, duration: float, min_len: float = 0.3) -> list[tuple[float, float]]:
    """Converte a saída do silencedetect em intervalos com som [(início, fim)]."""
    sounds: list[tuple[float, float]] = []
    cursor: float | None = 0.0  # início do trecho com som em andamento
    for kind, value in _SILENCE_RE.findall(stderr):
        t = max(0.0, float(value))
        if kind == "start":
            if cursor is not None and t - cursor >= min_len:
                sounds.append((cursor, t))
            cursor = None
        else:
            cursor = t
    if cursor is not None and duration - cursor >= min_len:
        sounds.append((cursor, duration))
    return sounds


def detect_sounds(video: Path, stream: int, duration: float, cancel: threading.Event | None = None,
                  noise_db: int = -45) -> list[tuple[float, float]]:
    """Intervalos em que a trilha tem som (ex.: alertas e avisos do computador)."""
    stderr = _run([_bin("ffmpeg"), "-hide_banner", "-i", str(video), "-map", f"0:a:{stream}",
                   "-af", f"silencedetect=n={noise_db}dB:d=0.5", "-f", "null", "-"], cancel)
    return parse_sounds(stderr, duration)


def extract_frame_at(video: Path, t: float, out: Path, cfg: PipelineConfig, cancel: threading.Event | None = None) -> None:
    _run([_bin("ffmpeg"), "-y", "-hide_banner", "-ss", f"{max(0.0, t):.3f}", "-i", str(video), "-frames:v", "1",
          "-vf", f"scale='min({cfg.max_width},iw)':-2", "-q:v", str(jpeg_qscale(cfg.jpg_quality)), str(out)], cancel)


def jpeg_qscale(quality: int) -> int:
    """Converte qualidade 1-100 para a escala -q:v do MJPEG (2 = melhor, 31 = pior)."""
    return round(31 - (quality - 1) * 29 / 99)


def parse_showinfo(stderr: str) -> list[float]:
    return [float(m.group(1)) for line in stderr.splitlines() if "showinfo" in line and (m := _PTS_RE.search(line))]


def _select(video: Path, out_dir: Path, select: str, cfg: PipelineConfig, cancel: threading.Event | None) -> list[tuple[Path, float]]:
    out_dir.mkdir(parents=True, exist_ok=True)
    vf = f"select='{select}',scale='min({cfg.max_width},iw)':-2,showinfo"
    stderr = _run([_bin("ffmpeg"), "-y", "-hide_banner", "-i", str(video), "-vf", vf, "-fps_mode", "vfr",
                   "-q:v", str(jpeg_qscale(cfg.jpg_quality)), str(out_dir / "%06d.jpg")], cancel)
    files = sorted(out_dir.glob("*.jpg"))
    times = parse_showinfo(stderr)
    if len(files) != len(times):
        raise ReplayFlowError(f"FFmpeg gerou {len(files)} frames mas reportou {len(times)} timestamps.")
    return list(zip(files, times))


def dedupe(frames: list[ExtractedFrame], min_gap_s: float, max_distance: int) -> list[ExtractedFrame]:
    """Remove frames muito próximos no tempo ou visualmente iguais ao último mantido.

    Mudança de cena detectada pelo FFmpeg nunca é descartada por hash (o dHash não vê
    troca entre telas de cor lisa). Na disputa por proximidade, a cena vence o intervalo.
    """
    kept: list[ExtractedFrame] = []
    last_hash = 0
    for f in sorted(frames, key=lambda f: (f.t, not f.scene_change)):
        h = imagehash.dhash(f.file)
        if kept:
            too_close = f.t - kept[-1].t < min_gap_s
            same_screen = not f.scene_change and imagehash.distance(h, last_hash) <= max_distance
            if too_close or same_screen:
                if too_close and f.scene_change and not kept[-1].scene_change:
                    kept[-1], last_hash = f, h
                continue
        kept.append(f)
        last_hash = h
    return kept


def extract_frames(video: Path, frames_dir: Path, cfg: PipelineConfig,
                   cancel: threading.Event | None = None) -> list[ExtractedFrame]:
    """Extrai, deduplica e numera frames em frames_dir/frame_000001.jpg."""
    for old in frames_dir.glob("frame_*.jpg"):
        old.unlink()  # idempotente ao reprocessar
    with tempfile.TemporaryDirectory(dir=frames_dir) as tmp:
        tmp_dir = Path(tmp)
        candidates: list[ExtractedFrame] = []
        if cfg.frame_mode in (FrameMode.INTERVAL, FrameMode.BOTH):
            sel = f"isnan(prev_selected_t)+gte(t-prev_selected_t\\,{cfg.frame_interval_s})"
            candidates += [ExtractedFrame(p, t, False) for p, t in _select(video, tmp_dir / "interval", sel, cfg, cancel)]
        if cfg.frame_mode in (FrameMode.SCENE, FrameMode.BOTH):
            sel = f"eq(n\\,0)+gt(scene\\,{cfg.scene_threshold})"
            scene = _select(video, tmp_dir / "scene", sel, cfg, cancel)
            candidates += [ExtractedFrame(p, t, i > 0) for i, (p, t) in enumerate(scene)]  # 0 = frame inicial

        kept = dedupe(candidates, cfg.dedupe_min_gap_s, cfg.dedupe_hash_distance)
        result = []
        for i, f in enumerate(kept, start=1):
            target = frames_dir / f"frame_{i:06d}.jpg"
            shutil.move(f.file, target)
            result.append(ExtractedFrame(target, f.t, f.scene_change))
    log.info("%d frames mantidos de %d candidatos", len(result), len(candidates),
             extra={"candidates": len(candidates), "kept": len(result)})
    return result


def version() -> str:
    try:
        out = subprocess.run([_bin("ffmpeg"), "-version"], capture_output=True, text=True, **_NO_WINDOW).stdout
        return out.splitlines()[0] if out else "desconhecida"
    except ToolMissingError:
        return "ausente"
