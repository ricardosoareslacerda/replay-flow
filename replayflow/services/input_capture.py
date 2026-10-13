"""Captura local de cliques, teclas e janela ativa.

O texto digitado é agrupado em trechos (flush em Enter/Tab/Esc, clique ou atalho).
No modo sigiloso (hotkey, padrão F9) só o número de caracteres é registrado:
o valor nunca chega ao disco.
"""

from __future__ import annotations

import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path

from replayflow.core.errors import ToolMissingError
from replayflow.models import TimelineEvent

_FLUSH_KEYS = {"enter", "tab", "esc"}
_MODIFIERS = {"ctrl", "ctrl_l", "ctrl_r", "alt", "alt_l", "alt_r", "alt_gr", "cmd", "cmd_l", "cmd_r"}


def active_window_title() -> str | None:
    if sys.platform != "win32":
        return None
    import ctypes

    user32 = ctypes.windll.user32
    hwnd = user32.GetForegroundWindow()
    length = user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buf, length + 1)
    return buf.value or None


class InputRecorder:
    """Regras de agregação, sem dependência de pynput (testável)."""

    def __init__(self, emit: Callable[[TimelineEvent], None], secret_key: str = "f9",
                 window_title: Callable[[], str | None] = active_window_title) -> None:
        self._emit = emit
        self._secret_key = secret_key
        self._window_title = window_title
        self._last_window: str | None = None
        self._buffer: list[str] = []
        self._buffer_t = 0.0
        self._secret = False
        self._modifiers: set[str] = set()

    @property
    def secret_mode(self) -> bool:
        return self._secret

    def _event(self, t: float, source: str, kind: str, **data: object) -> None:
        title = self._window_title()
        if title and title != self._last_window:
            self._last_window = title
            self._emit(TimelineEvent(t=t, source="window", kind="focus", data={"title": title}))
        self._emit(TimelineEvent(t=t, source=source, kind=kind, data=data))  # type: ignore[arg-type]

    def flush(self, t: float) -> None:
        if not self._buffer:
            return
        if self._secret:
            self._event(self._buffer_t, "key", "secret", chars=len(self._buffer))
        else:
            self._event(self._buffer_t, "key", "text", text="".join(self._buffer))
        self._buffer.clear()

    def on_click(self, t: float, x: int, y: int, button: str) -> None:
        self.flush(t)
        self._event(t, "mouse", "click", x=x, y=y, button=button)

    def on_press(self, t: float, key: str, char: str | None) -> None:
        """key: nome da tecla especial (ex.: 'enter') ou '' para caractere; char: caractere digitado."""
        if key == self._secret_key:
            self.flush(t)
            self._secret = not self._secret
            self._event(t, "key", "secret_mode", on=self._secret)
            return
        if key in _MODIFIERS:
            if key != "alt_gr":
                self._modifiers.add(key.split("_")[0])
            return
        # Símbolos via AltGr (ex.: "/" no ABNT2) chegam com modificador mas são texto.
        if self._modifiers and (key or (char is not None and char.isalnum())):
            self.flush(t)
            name = (char or key).lower() if not self._secret else "?"
            self._event(t, "key", "hotkey", keys="+".join([*sorted(self._modifiers), name]))
            return
        if char:
            if not self._buffer:
                self._buffer_t = t
            self._buffer.append(char)
        elif key == "backspace" and self._buffer:
            self._buffer.pop()
        elif key == "space":
            self.on_press(t, "", " ")
        elif key in _FLUSH_KEYS:
            self.flush(t)
            self._event(t, "key", "special", key=key)

    def on_release(self, key: str) -> None:
        if key in _MODIFIERS:
            self._modifiers.discard(key.split("_")[0])


class InputCapture:
    """Liga o InputRecorder ao pynput e grava em JSONL (append, uma linha por evento)."""

    def __init__(self, out_file: Path, secret_key: str = "f9", mouse: bool = True, keys: bool = True) -> None:
        try:
            from pynput import keyboard, mouse as pmouse
        except ImportError:
            raise ToolMissingError("pynput não instalado: pip install -e .[input]") from None
        self._kb, self._ms = keyboard, pmouse
        self._file = out_file.open("a", encoding="utf-8")
        self._lock = threading.Lock()
        self._t0 = time.monotonic()
        self.recorder = InputRecorder(self._write, secret_key)
        self._listeners = []
        if keys:
            self._listeners.append(keyboard.Listener(on_press=self._press, on_release=self._release))
        if mouse:
            self._listeners.append(pmouse.Listener(on_click=self._click))

    def _now(self) -> float:
        return time.monotonic() - self._t0

    def _write(self, event: TimelineEvent) -> None:
        self._file.write(event.model_dump_json() + "\n")
        self._file.flush()

    @staticmethod
    def _key_name(key: object) -> tuple[str, str | None]:
        char = getattr(key, "char", None)
        if char is not None:
            if len(char) == 1 and ord(char) < 32:  # Ctrl+letra chega como caractere de controle
                return "", chr(ord(char) + 96)
            return "", char if char.isprintable() else None
        return getattr(key, "name", str(key)), None

    def _press(self, key: object) -> None:
        name, char = self._key_name(key)
        with self._lock:
            self.recorder.on_press(self._now(), name, char)

    def _release(self, key: object) -> None:
        with self._lock:
            self.recorder.on_release(self._key_name(key)[0])

    def _click(self, x: int, y: int, button: object, pressed: bool) -> None:
        if pressed:
            with self._lock:
                self.recorder.on_click(self._now(), x, y, getattr(button, "name", str(button)))

    def start(self) -> None:
        self._t0 = time.monotonic()
        for listener in self._listeners:
            listener.start()

    def stop(self) -> None:
        for listener in self._listeners:
            listener.stop()
        with self._lock:
            self.recorder.flush(self._now())
            self._file.close()
