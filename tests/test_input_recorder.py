from replayflow.models import TimelineEvent
from replayflow.services.input_capture import InputRecorder


def _recorder(title: str | None = "Explorer") -> tuple[InputRecorder, list[TimelineEvent]]:
    events: list[TimelineEvent] = []
    return InputRecorder(events.append, window_title=lambda: title), events


def _type(rec: InputRecorder, t: float, text: str) -> None:
    for i, ch in enumerate(text):
        rec.on_press(t + i * 0.1, "", ch)


def test_groups_text_with_backspace_and_flushes_on_enter() -> None:
    rec, events = _recorder()
    _type(rec, 1.0, "boletx")
    rec.on_press(2.0, "backspace", None)
    _type(rec, 2.1, "o.pdf")
    rec.on_press(3.0, "enter", None)

    keys = [(e.kind, e.data) for e in events if e.source == "key"]
    assert keys == [("text", {"text": "boleto.pdf"}), ("special", {"key": "enter"})]
    assert events[0].source == "window" and events[0].data == {"title": "Explorer"}


def test_secret_mode_never_emits_typed_value() -> None:
    rec, events = _recorder()
    rec.on_press(1.0, "f9", None)
    _type(rec, 1.1, "12345")
    rec.on_press(2.0, "enter", None)
    rec.on_press(2.1, "f9", None)

    assert "12345" not in "".join(e.model_dump_json() for e in events)
    assert ("secret", {"chars": 5}) in [(e.kind, e.data) for e in events]
    assert not rec.secret_mode


def test_hotkey_and_altgr_symbol() -> None:
    rec, events = _recorder(None)
    rec.on_press(1.0, "ctrl_l", None)
    rec.on_press(1.1, "", "c")
    rec.on_release("ctrl_l")
    rec.on_press(2.0, "alt_gr", None)
    rec.on_press(2.1, "", "/")
    rec.on_release("alt_gr")
    rec.flush(3.0)

    assert [(e.kind, e.data) for e in events] == [("hotkey", {"keys": "ctrl+c"}), ("text", {"text": "/"})]


def test_click_flushes_pending_text() -> None:
    rec, events = _recorder(None)
    _type(rec, 1.0, "abc")
    rec.on_click(2.0, 10, 20, "left")

    assert [e.kind for e in events] == ["text", "click"]
    assert events[0].t == 1.0


def test_capture_pause_drops_events_and_discounts_paused_time(tmp_path, monkeypatch) -> None:
    from replayflow.services import input_capture as ic

    clock = [100.0]
    monkeypatch.setattr(ic.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(ic, "active_window_title", lambda: None)
    cap = ic.InputCapture(tmp_path / "events.jsonl", mouse=False, keys=False)
    cap.recorder._window_title = lambda: None
    clock[0] = 102.0
    cap._click(1, 1, "left", True)       # t = 2
    cap.pause()
    clock[0] = 110.0
    cap._click(2, 2, "left", True)       # em pausa: ignorado
    cap.resume()
    clock[0] = 111.0
    cap._click(3, 3, "left", True)       # t = 11 - 8 de pausa = 3
    cap.stop()

    lines = (tmp_path / "events.jsonl").read_text(encoding="utf-8").splitlines()
    import json
    assert [(json.loads(l)["t"], json.loads(l)["data"]["x"]) for l in lines] == [(2.0, 1), (3.0, 3)]
