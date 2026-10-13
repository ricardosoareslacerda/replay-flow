"""Orquestra as etapas. Cada etapa é pulada se já concluída com a mesma config (retomada)."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from pathlib import Path

from replayflow.core import logging_setup, session_store, timeline_store
from replayflow.core.errors import CancelledError, ReplayFlowError
from replayflow.core.layout import SessionLayout
from replayflow.models import Session, Stage, StageStatus, TimelineEvent
from replayflow.services import click_marker, exporter, ffmpeg, ocr, transcriber

log = logging.getLogger(__name__)

INPUT_EVENTS_FILE = "input_events.jsonl"
CLICK_LEAD_S = 0.15  # a foto do clique mostra a tela imediatamente antes do efeito do clique
Progress = Callable[[Stage, StageStatus | None], None]  # None = iniciando


class Pipeline:
    def __init__(self, session: Session, private_mode: bool = True,
                 cancel: threading.Event | None = None, progress: Progress | None = None) -> None:
        self.session = session
        self.layout = SessionLayout(session.root)
        self.private_mode = private_mode
        self.cancel = cancel or threading.Event()
        self._progress = progress or (lambda stage, status: None)

    @property
    def video(self) -> Path:
        if not self.session.video_file:
            raise ReplayFlowError("A sessão não tem vídeo. Importe uma gravação primeiro.")
        return self.layout.raw / self.session.video_file

    def run(self, stages: tuple[Stage, ...] = tuple(Stage)) -> Session:
        handler = logging_setup.attach_session_log(self.layout.pipeline_log)
        try:
            if self.session.duration_s is None:
                self._save(self.session.model_copy(update={"duration_s": ffmpeg.probe_duration(self.video)}))
            for stage in stages:
                self._run_stage(stage)
            return self.session
        finally:
            logging_setup.detach(handler)

    def _save(self, session: Session) -> None:
        session_store.save(session)
        self.session = session

    def _run_stage(self, stage: Stage) -> None:
        if stage != Stage.EXPORT and self.session.is_done(stage):  # export sempre reflete o estado atual
            log.info("[%s] já concluída, pulando", stage, extra={"stage": stage})
            self._progress(stage, self.session.stages[stage].status)
            return
        if self.cancel.is_set():
            raise CancelledError()
        self._progress(stage, None)
        log.info("[%s] iniciada", stage, extra={"stage": stage})
        try:
            ran = getattr(self, f"_stage_{stage.value}")()
        except CancelledError:
            log.warning("[%s] cancelada", stage, extra={"stage": stage})
            raise
        except ReplayFlowError as e:
            self.session = session_store.mark_stage(self.session, stage, StageStatus.FAILED, str(e))
            self._progress(stage, StageStatus.FAILED)
            log.error("[%s] falhou: %s", stage, e, extra={"stage": stage})
            raise
        status = StageStatus.DONE if ran else StageStatus.SKIPPED
        self.session = session_store.mark_stage(self.session, stage, status)
        self._progress(stage, status)
        log.info("[%s] %s", stage, status, extra={"stage": stage, "status": status})

    # Cada etapa retorna False quando desativada pela config (SKIPPED).

    def _input_events(self) -> list[TimelineEvent]:
        """Eventos de clique/tecla já no relógio do vídeo (input_offset_s vem da sincronia com o OBS)."""
        raw = self.layout.raw / INPUT_EVENTS_FILE
        offset = self.session.config.input_offset_s
        return merge_double_clicks([e.model_copy(update={"t": max(0.0, e.t + offset)}) for e in timeline_store.read(raw)])

    def _stage_audio(self) -> bool:
        tracks = ffmpeg.audio_streams(self.video)
        if tracks >= 2:  # gravação do ReplayFlow: trilha 1 = microfone, trilha 2 = som do computador
            sounds = ffmpeg.detect_sounds(self.video, 1, self.session.duration_s or 0, self.cancel)
            timeline_store.write_source(self.layout.timeline, "audio", [
                TimelineEvent(t=start, source="audio", kind="system_sound", data={"end": end}) for start, end in sounds
            ])
            log.info("narração na trilha 1; %d sons do computador detectados na trilha 2", len(sounds))
        if not self.session.config.transcription_enabled:
            return tracks >= 2
        if not tracks:
            log.warning("vídeo sem trilha de áudio: transcrição pulada")
            return False
        ffmpeg.extract_audio(self.video, self.layout.audio_wav, self.cancel, stream=0)
        return True

    def _stage_transcript(self) -> bool:
        cfg = self.session.config
        if not cfg.transcription_enabled or not self.layout.audio_wav.exists():
            return False
        segments = transcriber.transcribe(self.layout.audio_wav, cfg.whisper_model, cfg.language,
                                          offline=self.private_mode, cancel=self.cancel)
        if not segments:
            log.warning("nenhuma fala detectada na narração: confira se o microfone certo está ligado e com volume")
        transcriber.write_outputs(segments, self.layout.transcript, self.session.name)
        timeline_store.write_source(self.layout.timeline, "transcript", [
            TimelineEvent(t=s.start, source="transcript", kind="segment",
                          data={"end": s.end, "text": s.text, "confidence": s.confidence, "words": s.words})
            for s in segments
        ])
        return True

    def _stage_frames(self) -> bool:
        frames = ffmpeg.extract_frames(self.video, self.layout.frames, self.session.config, self.cancel)
        events = [
            TimelineEvent(t=f.t, source="frame", kind="captured",
                          data={"file": f.file.name, "scene_change": f.scene_change})
            for f in frames
        ]
        timeline_store.write_source(self.layout.timeline, "frame", [*events, *self._click_frames()])
        return True

    def _click_frames(self) -> list[TimelineEvent]:
        """Uma foto por clique, tirada um instante antes dele, com o ponto clicado marcado."""
        for old in self.layout.frames.glob("click_*.jpg"):
            old.unlink()
        clicks = [e for e in self._input_events() if e.source == "mouse" and e.kind == "click"]
        if not clicks:
            return []
        cfg = self.session.config
        scale = min(cfg.max_width, width := ffmpeg.video_width(self.video)) / width
        events = []
        for i, c in enumerate(clicks, start=1):
            if self.cancel.is_set():
                raise CancelledError()
            out = self.layout.frames / f"click_{i:06d}.jpg"
            ffmpeg.extract_frame_at(self.video, c.t - CLICK_LEAD_S, out, cfg, self.cancel)
            if not out.exists():  # clique depois do fim do vídeo
                continue
            target, near = self._click_target(out, c.data["x"] * scale, c.data["y"] * scale)  # antes do círculo
            click_marker.mark(out, c.data["x"], c.data["y"], scale, cfg.jpg_quality)
            events.append(TimelineEvent(t=c.t, source="frame", kind="click", data={
                "file": out.name, "scene_change": False, "click": {"x": c.data["x"], "y": c.data["y"]}, "target": target, "near": near}))
        log.info("fotos de clique geradas: %d", len(events))
        return events

    def _stage_ocr(self) -> bool:
        cfg = self.session.config
        if not cfg.ocr_enabled:
            return False
        frame_events = [e for e in timeline_store.read(self.layout.timeline) if e.source == "frame"]
        frames = [self.layout.frames / e.data["file"] for e in frame_events]
        texts = ocr.run(frames, self.layout.ocr, cfg.ocr_lang, self.cancel)
        timeline_store.write_source(self.layout.timeline, "ocr", [
            TimelineEvent(t=e.t, source="ocr", kind="text",
                          data={"frame": f.name, "file": f"{f.stem}.txt", "chars": len(texts[f]), "text": texts[f]})
            for e, f in zip(frame_events, frames)
        ])
        return True

    def _click_target(self, image: Path, x: float, y: float) -> tuple[str, str]:
        """(rótulo em que o usuário clicou, texto da região em volta), lidos por OCR antes de marcar o ponto."""
        cfg = self.session.config
        if not cfg.ocr_enabled:
            return "", ""
        try:
            return ocr.text_at(image, x, y, cfg.ocr_lang), ocr.text_near(image, x, y, cfg.ocr_lang)
        except ReplayFlowError as e:  # sem Tesseract: a etapa de OCR avisará; a foto do clique segue sem rótulo
            log.warning("rótulo do clique indisponível: %s", e)
            return "", ""

    def _stage_input(self) -> bool:
        if not (self.layout.raw / INPUT_EVENTS_FILE).exists():
            return False
        events = self._input_events()
        for source in ("mouse", "key", "window"):
            timeline_store.write_source(self.layout.timeline, source, [e for e in events if e.source == source])
        return True

    def _stage_export(self) -> bool:
        exporter.export(self.session, tool_versions())
        return True


def merge_double_clicks(events: list[TimelineEvent], window_s: float = 0.5, radius: int = 4) -> list[TimelineEvent]:
    """Dois cliques no mesmo ponto em menos de meio segundo viram um duplo clique (um passo, uma foto)."""
    out: list[TimelineEvent] = []
    last: TimelineEvent | None = None
    for e in events:
        if e.source == "mouse" and e.kind == "click":
            if (last is not None and e.t - last.t <= window_s and e.data.get("button") == last.data.get("button")
                    and abs(e.data["x"] - last.data["x"]) <= radius and abs(e.data["y"] - last.data["y"]) <= radius
                    and not last.data.get("double")):
                out[out.index(last)] = last.model_copy(update={"data": {**last.data, "double": True}})
                last = None
                continue
            last = e
        out.append(e)
    return out


def tool_versions() -> dict[str, str]:
    return {"ffmpeg": ffmpeg.version(), "faster_whisper": transcriber.version(), "tesseract": ocr.version()}
