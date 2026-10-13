from replayflow.services.transcriber import Segment, mmss, to_markdown


def test_mmss() -> None:
    assert mmss(0) == "00:00"
    assert mmss(75.9) == "01:15"
    assert mmss(3600) == "60:00"


def test_to_markdown() -> None:
    md = to_markdown([Segment(start=5, end=12.4, text="Abro o portal.", confidence=0.8)], "Boletos")

    assert md == "# Transcrição: Boletos\n\n**[00:05 - 00:12]** Abro o portal.\n"


def test_gpu_error_falls_back_to_cpu(monkeypatch, tmp_path) -> None:
    import sys
    import types

    from replayflow.services import transcriber

    devices = []

    class FakeModel:
        def __init__(self, name, device, compute_type, local_files_only):
            self.device = device
            devices.append(device)

        def transcribe(self, wav, language, vad_filter):
            def gen():
                if self.device == "cuda":
                    raise RuntimeError("Library cublas64_12.dll is not found or cannot be loaded")
                yield types.SimpleNamespace(start=0.0, end=1.0, text=" testando ", avg_logprob=0.0)
            return gen(), types.SimpleNamespace(duration=1.0)

    monkeypatch.setitem(sys.modules, "faster_whisper", types.SimpleNamespace(WhisperModel=FakeModel))
    monkeypatch.setattr(transcriber, "gpu_device", lambda: "cuda")
    segments = transcriber.transcribe(tmp_path / "a.wav", "small", "pt", offline=True)

    assert devices == ["cuda", "cpu"]
    assert [s.text for s in segments] == ["testando"]
