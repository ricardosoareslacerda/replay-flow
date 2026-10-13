import os
from pathlib import Path

from replayflow.app import environment


def test_setup_points_tools_and_models_to_portable_home(tmp_path: Path, monkeypatch) -> None:
    for d in ("app/replayflow", "tools/ffmpeg", "tools/tesseract/tessdata", "models"):
        (tmp_path / d).mkdir(parents=True)
    monkeypatch.setenv("REPLAYFLOW_HOME", str(tmp_path))
    monkeypatch.delenv("TESSDATA_PREFIX", raising=False)
    monkeypatch.delenv("HF_HOME", raising=False)
    monkeypatch.setenv("PATH", "C:\\Windows")

    assert environment.setup() == tmp_path
    assert os.environ["PATH"].split(os.pathsep)[:2] == [str(tmp_path / "tools" / "ffmpeg"), str(tmp_path / "tools" / "tesseract")]
    assert os.environ["TESSDATA_PREFIX"] == str(tmp_path / "tools" / "tesseract" / "tessdata")
    assert os.environ["HF_HOME"] == str(tmp_path / "models")
