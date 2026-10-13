from pathlib import Path

from replayflow.services import obs


def test_no_portable_obs_without_home(monkeypatch) -> None:
    monkeypatch.delenv("REPLAYFLOW_HOME", raising=False)
    assert obs.portable_obs_exe() is None


def test_portable_obs_found_in_home(monkeypatch, tmp_path: Path) -> None:
    exe = tmp_path / "tools" / "obs" / "bin" / "64bit" / "obs64.exe"
    exe.parent.mkdir(parents=True)
    exe.touch()
    monkeypatch.setenv("REPLAYFLOW_HOME", str(tmp_path))
    assert obs.portable_obs_exe() == exe


def test_launch_skipped_when_obs_already_running(monkeypatch) -> None:
    monkeypatch.setattr(obs, "port_open", lambda **_: True)
    monkeypatch.setattr(obs.subprocess, "Popen", lambda *a, **k: (_ for _ in ()).throw(AssertionError("não devia abrir")))
    assert obs.launch_portable_obs() is None


def test_launch_skipped_without_portable_obs(monkeypatch) -> None:
    monkeypatch.setattr(obs, "port_open", lambda **_: False)
    monkeypatch.setattr(obs, "portable_obs_exe", lambda: None)
    assert obs.launch_portable_obs() is None
