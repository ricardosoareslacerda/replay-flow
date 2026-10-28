from replayflow.models import TimelineEvent
from replayflow.services import verify


def _said(t: float, text: str) -> TimelineEvent:
    words = [[t + i * 0.3, t + i * 0.3 + 0.2, w] for i, w in enumerate(text.split())]
    return TimelineEvent(t=t, source="transcript", kind="segment", data={"end": t + 3, "text": text, "words": words})


def test_confirms_terms_seen_on_screen_and_suggests_fixes() -> None:
    events = [
        TimelineEvent(t=4.0, source="window", kind="focus", data={"title": "Boleto Au!Happy - DUCKY LACERDA - Gmail"}),
        TimelineEvent(t=5.0, source="frame", kind="click", data={"file": "c.jpg", "target": "Salvar", "near": "Salvar Cancelar"}),
        _said(4.5, "salvamos o boleto do Claserda"),
    ]
    report = verify.check(events)

    status = {m.said: (m.status, m.suggestion) for m in report.mentions}
    assert status["salvamos"] == ("confirmado", "")
    assert status["boleto"] == ("confirmado", "")
    assert status["Claserda"] == ("provável erro", "LACERDA")
    assert any("Claserda" in line and "LACERDA" in line for line in verify.summary_lines(report))


def test_flags_long_stretch_of_actions_without_narration() -> None:
    clicks = [TimelineEvent(t=t, source="mouse", kind="click", data={"x": 1, "y": 1}) for t in (30, 40, 55)]
    report = verify.check([_said(0, "abrindo o sistema"), *clicks])

    assert report.silent_gaps == [(30, 55, 3)]


def test_apply_fixes_names_and_keeps_the_original() -> None:
    events = [
        TimelineEvent(t=4.0, source="window", kind="focus", data={"title": "Boleto Au!Happy - DUCKY LACERDA - Gmail"}),
        _said(4.5, "boleto do Claserda e Claserda"),
    ]
    report = verify.check(events)
    fixed = verify.apply(events, report)

    seg = next(e for e in fixed if e.source == "transcript")
    assert seg.data["text"] == "boleto do LACERDA e LACERDA"
    assert seg.data["original"] == "boleto do Claserda e Claserda"
    assert [w[2] for w in seg.data["words"]][-1] == "LACERDA"
    m = next(m for m in report.mentions if m.applied)
    assert (m.where, m.occurrences) == ("título da janela", 2)
    assert "| 00:05 | Claserda | LACERDA | título da janela às 00:04 |" in verify.to_markdown(report, "x")
