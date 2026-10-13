from replayflow.services.transcriber import Segment, mmss, to_markdown


def test_mmss() -> None:
    assert mmss(0) == "00:00"
    assert mmss(75.9) == "01:15"
    assert mmss(3600) == "60:00"


def test_to_markdown() -> None:
    md = to_markdown([Segment(start=5, end=12.4, text="Abro o portal.", confidence=0.8)], "Boletos")

    assert md == "# Transcrição: Boletos\n\n**[00:05 - 00:12]** Abro o portal.\n"
