from datetime import datetime
from pathlib import Path

from replayflow.core import session_store
from replayflow.core.layout import SessionLayout
from replayflow.models import PipelineConfig, Stage, StageStatus

NOW = datetime(2026, 10, 13, 9, 0).astimezone()


def test_create_session_builds_folder_structure(tmp_path: Path) -> None:
    session = session_store.create_session(tmp_path, "Automação Boletos", PipelineConfig(), NOW)

    assert session.id == "2026-10-13_001_automacao-boletos"
    layout = SessionLayout(session.root)
    assert all(d.is_dir() for d in layout.dirs())
    assert layout.session_json.is_file()


def test_sequence_increments_per_day(tmp_path: Path) -> None:
    session_store.create_session(tmp_path, "a", PipelineConfig(), NOW)
    second = session_store.create_session(tmp_path, "b", PipelineConfig(), NOW)

    assert second.id == "2026-10-13_002_b"


def test_save_and_load_roundtrip(tmp_path: Path) -> None:
    session = session_store.create_session(tmp_path, "x", PipelineConfig(language="en"), NOW)

    loaded = session_store.load(session.root)

    assert loaded == session


def test_import_video_copies_and_hashes(tmp_path: Path) -> None:
    video = tmp_path / "OBS.MKV"
    video.write_bytes(b"fake video")
    session = session_store.create_session(tmp_path / "s", "x", PipelineConfig(), NOW)

    session = session_store.import_video(session, video)

    assert (session.root / "raw" / "gravacao.mkv").read_bytes() == b"fake video"
    assert session.video_sha256 == session_store.sha256_file(video)


def test_stage_done_is_invalidated_by_config_change(tmp_path: Path) -> None:
    session = session_store.create_session(tmp_path, "x", PipelineConfig(), NOW)
    session = session_store.mark_stage(session, Stage.FRAMES, StageStatus.DONE)
    assert session.is_done(Stage.FRAMES)

    changed = session.model_copy(update={"config": PipelineConfig(max_width=640)})

    assert not changed.is_done(Stage.FRAMES)


def test_portable_home_sets_default_base_dir(monkeypatch, tmp_path: Path) -> None:
    from replayflow.models.config import default_base_dir

    monkeypatch.setenv("REPLAYFLOW_HOME", str(tmp_path))

    assert default_base_dir() == tmp_path / "Sessions"
