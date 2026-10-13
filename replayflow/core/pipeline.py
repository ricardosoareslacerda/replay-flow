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
from replayflow.services import exporter, ffmpeg, ocr, transcriber

log = logging.getLogger(__name__)

INPUT_EVENTS_FILE = "input_events.jsonl"
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

    def _stage_audio(self) -> bool:
        if not self.session.config.transcription_enabled:
            return False
        if not ffmpeg.has_audio(self.video):
            log.warning("vídeo sem trilha de áudio: transcrição pulada")
            return False
        ffmpeg.extract_audio(self.video, self.layout.audio_wav, self.cancel)
        return True

    def _stage_transcript(self) -> bool:
        cfg = self.session.config
        if not cfg.transcription_enabled or not self.layout.audio_wav.exists():
            return False
        segments = transcriber.transcribe(self.layout.audio_wav, cfg.whisper_model, cfg.language,
                                          offline=self.private_mode, cancel=self.cancel)
        transcriber.write_outputs(segments, self.layout.transcript, self.session.name)
        timeline_store.write_source(self.layout.timeline, "transcript", [
            TimelineEvent(t=s.start, source="transcript", kind="segment",
                          data={"end": s.end, "text": s.text, "confidence": s.confidence})
            for s in segments
        ])
        return True

    def _stage_frames(self) -> bool:
        frames = ffmpeg.extract_frames(self.video, self.layout.frames, self.session.config, self.cancel)
        timeline_store.write_source(self.layout.timeline, "frame", [
            TimelineEvent(t=f.t, source="frame", kind="captured",
                          data={"file": f.file.name, "scene_change": f.scene_change})
            for f in frames
        ])
        return True

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

    def _stage_input(self) -> bool:
        raw = self.layout.raw / INPUT_EVENTS_FILE
        if not raw.exists():
            return False
        offset = self.session.config.input_offset_s
        events = [e.model_copy(update={"t": max(0.0, e.t + offset)}) for e in timeline_store.read(raw)]
        for source in ("mouse", "key", "window"):
            timeline_store.write_source(self.layout.timeline, source, [e for e in events if e.source == source])
        return True

    def _stage_export(self) -> bool:
        exporter.export(self.session, tool_versions())
        return True


def tool_versions() -> dict[str, str]:
    return {"ffmpeg": ffmpeg.version(), "faster_whisper": transcriber.version(), "tesseract": ocr.version()}
