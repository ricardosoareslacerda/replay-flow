import csv
import json
from datetime import datetime
from pathlib import Path

import pytest

from replayflow.core import session_store, timeline_store
from replayflow.models import PipelineConfig, Session, TimelineEvent
from replayflow.services import exporter

EVENTS = [
    TimelineEvent(t=0.0, source="frame", kind="captured", data={"file": "frame_000001.jpg", "scene_change": False}),
    TimelineEvent(t=0.0, source="ocr", kind="text", data={"frame": "frame_000001.jpg", "file": "frame_000001.txt",
                                                          "chars": 9, "text": "Área de trabalho"}),
    TimelineEvent(t=10.0, source="frame", kind="captured", data={"file": "frame_000002.jpg", "scene_change": True}),
    TimelineEvent(t=10.0, source="ocr", kind="text", data={"frame": "frame_000002.jpg", "file": "frame_000002.txt",
                                                           "chars": 14, "text": "Senha do PDF:"}),
    TimelineEvent(t=1.0, source="transcript", kind="segment", data={"end": 4.0, "text": "Abro o boleto.", "confidence": 0.9}),
    TimelineEvent(t=11.0, source="window", kind="focus", data={"title": "Adobe Reader"}),
    TimelineEvent(t=11.5, source="key", kind="secret", data={"chars": 5}),
    TimelineEvent(t=12.0, source="key", kind="special", data={"key": "enter"}),
]


def _sorted_events() -> list[TimelineEvent]:
    return sorted(EVENTS, key=lambda e: (e.t, e.source))


@pytest.fixture
def session(tmp_path: Path) -> Session:
    s = session_store.create_session(tmp_path, "Automação boletos", PipelineConfig(),
                                     datetime(2026, 10, 13, 9, 30).astimezone())
    s = s.model_copy(update={"duration_s": 20.0, "video_file": "gravacao.mkv", "video_sha256": "abc"})
    session_store.save(s)
    timeline = s.root / "timeline.jsonl"
    for source in {e.source for e in EVENTS}:
        timeline_store.write_source(timeline, source, [e for e in EVENTS if e.source == source])
    return s


def test_build_steps_assigns_events_to_frame_windows() -> None:
    steps = exporter.build_steps(_sorted_events(), 20.0)

    assert [(s.start, s.end) for s in steps] == [(0.0, 10.0), (10.0, 20.0)]
    assert [e.data["text"] for e in steps[0].speech] == ["Abro o boleto."]
    assert [a.kind for a in steps[1].actions] == ["focus", "secret", "special"]


def test_frames_index_csv(session: Session) -> None:
    exporter.export(session, {"ffmpeg": "test"})

    with (session.root / "export" / "frames_index.csv").open(encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert list(rows[0]) == ["frame_file", "timestamp_seconds", "timestamp_human", "ocr_file", "ocr_chars",
                             "scene_change", "notes"]
    assert rows[1]["timestamp_human"] == "00:10"
    assert rows[1]["scene_change"] == "true"
    assert rows[1]["notes"] == "janela ativa: Adobe Reader; digitou [SEGREDO: 5 caracteres]; tecla enter"


def test_manifest(session: Session) -> None:
    exporter.export(session, {"ffmpeg": "test"})

    manifest = json.loads((session.root / "export" / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["video"] == {"file": "gravacao.mkv", "sha256": "abc", "duration_s": 20.0}
    assert manifest["counts"] == {"frames": 2, "relevant_steps": 2}
    assert manifest["pipeline"]["whisper_model"] == "small"
    assert manifest["tools"]["ffmpeg"] == "test"
    assert "export/claude_package.md" in manifest["files"]


def test_manifest_is_deterministic(session: Session) -> None:
    path = session.root / "export" / "manifest.json"
    exporter.export(session, {"ffmpeg": "test"})
    first = path.read_text(encoding="utf-8")
    exporter.export(session, {"ffmpeg": "test"})

    assert path.read_text(encoding="utf-8") == first


def test_claude_package(session: Session) -> None:
    exporter.export(session, {"ffmpeg": "test"})

    md = (session.root / "export" / "claude_package.md").read_text(encoding="utf-8")
    assert md.startswith("# Sessão: Automação boletos\n")
    assert "**Data/hora:** 13/10/2026 09:30" in md
    assert "**Duração do vídeo:** 00:20" in md
    assert "- **[00:01]** Abro o boleto." in md
    assert "### Passo 2 [00:10 - 00:20] Tela · `frame_000002.jpg` (mudança de tela)" in md
    assert "## Roteiro passo a passo" in md and "2. **[00:10]** [janela: Adobe Reader] digitou [SEGREDO: 5 caracteres]; tecla enter" in md
    assert "Senha do PDF:" in md
    assert "[SEGREDO: 5 caracteres]" in md
    for request in exporter.ANALYSIS_REQUESTS:
        assert request in md


def test_prompt_mentions_package(session: Session) -> None:
    prompt = exporter.build_prompt(session)

    assert "claude_package.md" in prompt and "Automação boletos" in prompt


def test_words_split_narration_and_click_label_names_the_target() -> None:
    events = [
        TimelineEvent(t=0.0, source="frame", kind="captured", data={"file": "frame_000001.jpg", "scene_change": False}),
        TimelineEvent(t=0.5, source="transcript", kind="segment", data={
            "end": 6.0, "text": "abro o portal clico em emitir",
            "words": [[0.5, 0.8, "abro"], [0.9, 1.0, "o"], [1.1, 1.6, "portal"], [4.2, 4.5, "clico"],
                      [4.6, 4.7, "em"], [4.8, 5.3, "emitir"]]}),
        TimelineEvent(t=4.0, source="frame", kind="click", data={
            "file": "click_000001.jpg", "scene_change": False, "click": {"x": 10, "y": 20}, "target": "Emitir boleto"}),
        TimelineEvent(t=4.0, source="mouse", kind="click", data={"x": 10, "y": 20, "button": "left"}),
        TimelineEvent(t=4.0, source="window", kind="focus", data={"title": "Portal do banco"}),
    ]
    steps = exporter.build_steps(sorted(events, key=lambda e: (e.t, e.source)), 8.0)

    assert [s.narration for s in steps] == ["abro o portal", "clico em emitir"]
    assert [a.source for a in steps[1].actions] == ["window", "mouse"]
    assert exporter._script_lines(steps)[1] == (
        '2. **[00:04]** [janela: Portal do banco] clique esquerdo em "Emitir boleto" (posição 10, 20)'
        ' — fala: "clico em emitir"')

def test_new_lines_shows_only_what_appeared_on_screen() -> None:
    before = "Salvar como\nNome: fatura_lopes_ricardo_202609.pdf\nSalvar Cancelar"
    after = "Salvar como\nNome: fatura_lopes_ricardo_202609.pdf\nSalvar Cancelar\nfatura_lopes_ricardo_202610.pdf já existe. Deseja substituí-lo?"

    assert exporter._new_lines(after, before) == ["fatura_lopes_ricardo_202610.pdf já existe. Deseja substituí-lo?"]
