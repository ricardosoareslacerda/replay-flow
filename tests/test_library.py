from datetime import datetime
from pathlib import Path

from replayflow.core import session_store
from replayflow.models import PipelineConfig
from replayflow.ui.library import scan


def test_scan_lists_newest_first_and_status(tmp_path: Path) -> None:
    old = session_store.create_session(tmp_path, "Faturas", PipelineConfig(), datetime(2026, 10, 1).astimezone())
    video = tmp_path / "v.mkv"
    video.write_bytes(b"x" * 10)
    session_store.import_video(old, video)
    session_store.create_session(tmp_path, "Boletos", PipelineConfig(), datetime(2026, 10, 20).astimezone())
    (tmp_path / "lixo").mkdir()  # pasta qualquer, sem session.json

    entries = scan(tmp_path)

    assert [(e.session.name, e.status) for e in entries] == [("Boletos", "Sem vídeo"), ("Faturas", "Falta processar")]
