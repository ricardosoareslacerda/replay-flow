from datetime import datetime
from pathlib import Path

import pytest

from replayflow.core import pipeline as pipeline_mod
from replayflow.core import session_store, timeline_store
from replayflow.core.errors import ReplayFlowError
from replayflow.core.pipeline import Pipeline
from replayflow.models import PipelineConfig, Session, Stage, StageStatus, TimelineEvent
from replayflow.services.ffmpeg import ExtractedFrame
from replayflow.services.transcriber import Segment


class FakeServices:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.fail_ocr = False

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        mp = monkeypatch
        mp.setattr(pipeline_mod.ffmpeg, "probe_duration", lambda v: 12.0)
        mp.setattr(pipeline_mod.ffmpeg, "has_audio", lambda v: True)
        mp.setattr(pipeline_mod.ffmpeg, "extract_audio", self._audio)
        mp.setattr(pipeline_mod.ffmpeg, "extract_frames", self._frames)
        mp.setattr(pipeline_mod.transcriber, "transcribe", self._transcribe)
        mp.setattr(pipeline_mod.ocr, "run", self._ocr)
        mp.setattr(pipeline_mod, "tool_versions", lambda: {"ffmpeg": "test"})

    def _call(self, name: str) -> None:
        self.calls.append(name)

    def _audio(self, video: Path, wav: Path, cancel: object) -> None:
        self._call("audio")
        wav.write_bytes(b"RIFF")

    def _frames(self, video: Path, frames_dir: Path, cfg: PipelineConfig, cancel: object) -> list[ExtractedFrame]:
        self._call("frames")
        f = frames_dir / "frame_000001.jpg"
        f.write_bytes(b"x")
        return [ExtractedFrame(f, 2.0, True)]

    def _transcribe(self, wav: Path, model: str, lang: str, offline: bool, cancel: object) -> list[Segment]:
        self._call(f"transcribe:offline={offline}")
        return [Segment(start=0.5, end=3.0, text="abro o boleto", confidence=0.9)]

    def _ocr(self, frames: list[Path], ocr_dir: Path, lang: str, cancel: object) -> dict[Path, str]:
        self._call("ocr")
        if self.fail_ocr:
            raise ReplayFlowError("Tesseract não encontrado.")
        return {f: "Valor R$ 10,00" for f in frames}


@pytest.fixture
def session(tmp_path: Path) -> Session:
    s = session_store.create_session(tmp_path, "x", PipelineConfig(), datetime(2026, 10, 13).astimezone())
    video = tmp_path / "v.mkv"
    video.write_bytes(b"v")
    s = session_store.import_video(s, video)
    (s.root / "raw" / "input_events.jsonl").write_text(
        TimelineEvent(t=1.0, source="mouse", kind="click", data={"x": 1, "y": 2}).model_dump_json() + "\n")
    return s


def test_runs_all_stages_and_builds_timeline(session: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeServices()
    fake.install(monkeypatch)

    result = Pipeline(session).run()

    assert fake.calls == ["audio", "transcribe:offline=True", "frames", "ocr"]
    assert result.duration_s == 12.0
    assert all(result.stages[s].status == StageStatus.DONE for s in (Stage.AUDIO, Stage.OCR, Stage.INPUT))
    timeline = timeline_store.read(session.root / "timeline.jsonl")
    assert [(e.t, e.source) for e in timeline] == [(0.5, "transcript"), (1.0, "mouse"), (2.0, "frame"), (2.0, "ocr")]
    assert (session.root / "transcript" / "transcript.md").read_text(encoding="utf-8").count("00:00 - 00:03") == 1


def test_resume_skips_done_stages_after_failure(session: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeServices()
    fake.install(monkeypatch)
    fake.fail_ocr = True
    with pytest.raises(ReplayFlowError):
        Pipeline(session).run()
    failed = session_store.load(session.root)
    assert failed.stages[Stage.OCR].status == StageStatus.FAILED

    fake.calls.clear()
    fake.fail_ocr = False
    Pipeline(failed).run()

    assert fake.calls == ["ocr"]


def test_disabled_stages_are_skipped(session: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeServices()
    fake.install(monkeypatch)
    session = session.model_copy(update={"config": PipelineConfig(transcription_enabled=False, ocr_enabled=False)})

    result = Pipeline(session).run()

    assert fake.calls == ["frames"]
    assert result.stages[Stage.TRANSCRIPT].status == StageStatus.SKIPPED


def test_cancel_before_start_raises(session: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    FakeServices().install(monkeypatch)
    p = Pipeline(session)
    p.cancel.set()

    with pytest.raises(pipeline_mod.CancelledError):
        p.run()
