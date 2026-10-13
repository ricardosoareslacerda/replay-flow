import shutil
import subprocess
from pathlib import Path

import pytest
from PIL import Image

from replayflow.models import FrameMode, PipelineConfig
from replayflow.services import ffmpeg
from replayflow.services.ffmpeg import ExtractedFrame

needs_ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg ausente")


def _img(path: Path, color: tuple[int, int, int], stripe: bool = False) -> Path:
    img = Image.new("RGB", (64, 64), color)
    if stripe:
        for x in range(0, 64, 8):
            for y in range(64):
                img.putpixel((x, y), (0, 0, 0))
    img.save(path)
    return path


def test_parse_showinfo() -> None:
    stderr = (
        "[Parsed_showinfo_2 @ 0x1] n:   0 pts:      0 pts_time:0       duration:1\n"
        "frame=    2 fps=0.0\n"
        "[Parsed_showinfo_2 @ 0x1] n:   1 pts:  15360 pts_time:5.5     duration:1\n"
    )
    assert ffmpeg.parse_showinfo(stderr) == [0.0, 5.5]


def test_jpeg_qscale_bounds() -> None:
    assert ffmpeg.jpeg_qscale(100) == 2
    assert ffmpeg.jpeg_qscale(1) == 31


def test_dedupe_drops_close_and_identical_keeps_scene(tmp_path: Path) -> None:
    white = _img(tmp_path / "a.jpg", (255, 255, 255))
    white2 = _img(tmp_path / "b.jpg", (255, 255, 255))
    striped = _img(tmp_path / "c.jpg", (255, 255, 255), stripe=True)
    frames = [
        ExtractedFrame(white, 0.0, False),
        ExtractedFrame(white2, 10.0, False),   # mesma tela: descartado
        ExtractedFrame(striped, 20.0, False),
        ExtractedFrame(striped, 20.4, True),   # próximo, mas cena vence o intervalo
    ]

    kept = ffmpeg.dedupe(frames, min_gap_s=1.0, max_distance=5)

    assert [(f.t, f.scene_change) for f in kept] == [(0.0, False), (20.4, True)]


@needs_ffmpeg
def test_extract_audio_and_frames(tmp_path: Path) -> None:
    video = tmp_path / "v.mkv"
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error",
         "-f", "lavfi", "-i", "color=c=red:s=640x480:d=3,format=yuv420p",
         "-f", "lavfi", "-i", "color=c=blue:s=640x480:d=3,format=yuv420p",
         "-f", "lavfi", "-i", "sine=f=440:d=6",
         "-filter_complex", "[0][1]concat=n=2:v=1:a=0[v]", "-map", "[v]", "-map", "2", str(video)],
        check=True,
    )
    wav = tmp_path / "a.wav"
    ffmpeg.extract_audio(video, wav)
    assert wav.stat().st_size > 6 * 16000 * 2 * 0.9

    frames_dir = tmp_path / "frames"
    frames_dir.mkdir()
    cfg = PipelineConfig(frame_mode=FrameMode.BOTH, frame_interval_s=1, max_width=320)
    frames = ffmpeg.extract_frames(video, frames_dir, cfg)

    assert [f.file.name for f in frames] == ["frame_000001.jpg", "frame_000002.jpg"]
    assert frames[0].t < 0.1 and not frames[0].scene_change and 2.9 <= frames[1].t <= 3.1 and frames[1].scene_change
    with Image.open(frames[0].file) as img:
        assert img.width == 320
    assert abs(ffmpeg.probe_duration(video) - 6) < 0.2


def test_parse_sounds_inverts_silences() -> None:
    from replayflow.services.ffmpeg import parse_sounds

    stderr = ("[silencedetect @ 0x1] silence_start: 0\n"
              "[silencedetect @ 0x1] silence_end: 3.2 | silence_duration: 3.2\n"
              "[silencedetect @ 0x1] silence_start: 4.0\n"
              "[silencedetect @ 0x1] silence_end: 9.5 | silence_duration: 5.5\n")
    assert parse_sounds(stderr, duration=10.0) == [(3.2, 4.0), (9.5, 10.0)]


def test_parse_sounds_ignores_blips_and_handles_no_silence() -> None:
    from replayflow.services.ffmpeg import parse_sounds

    assert parse_sounds("silence_start: 0\nsilence_end: 5.0\nsilence_start: 5.1\n", duration=8.0) == []
    assert parse_sounds("", duration=2.0) == [(0.0, 2.0)]


def test_mean_volume_parses_volumedetect(monkeypatch, tmp_path) -> None:
    from replayflow.services import ffmpeg

    monkeypatch.setattr(ffmpeg, "audio_streams", lambda v: 2)
    monkeypatch.setattr(ffmpeg, "_bin", lambda n: n)
    monkeypatch.setattr(ffmpeg, "_run", lambda args, cancel=None: "[Parsed_volumedetect_0] mean_volume: -91.0 dB\n")
    assert ffmpeg.mean_volume(tmp_path / "v.mkv", stream=0) == -91.0
    monkeypatch.setattr(ffmpeg, "audio_streams", lambda v: 0)
    assert ffmpeg.mean_volume(tmp_path / "v.mkv", stream=0) is None
