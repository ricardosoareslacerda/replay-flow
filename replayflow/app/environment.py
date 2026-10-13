"""Prepara o ambiente do processo em Python puro (substitui o que o ReplayFlow.bat fazia).

Instalação portátil (Windows): <home>/python/pythonw.exe, <home>/app/replayflow, <home>/tools/{ffmpeg,tesseract,obs},
<home>/models. Detectamos <home> a partir do próprio interpretador ou do pacote, então o app abre igual
por ReplayFlow.exe, por `python -m replayflow` ou por um atalho, sem depender de .bat.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def find_home() -> Path | None:
    if os.environ.get("REPLAYFLOW_HOME"):
        return Path(os.environ["REPLAYFLOW_HOME"])
    package = Path(__file__).resolve().parents[2]          # <home>/app/replayflow/app/environment.py → <home>/app
    for candidate in (Path(sys.executable).resolve().parent.parent, package.parent):
        if (candidate / "app" / "replayflow").is_dir() and (candidate / "tools").is_dir():
            return candidate
    return None


def setup() -> Path | None:
    """Define REPLAYFLOW_HOME, PATH (ffmpeg, tesseract), TESSDATA_PREFIX e HF_HOME. Idempotente."""
    home = find_home()
    if home:
        os.environ["REPLAYFLOW_HOME"] = str(home)
        tools = [home / "tools" / "ffmpeg", home / "tools" / "tesseract"]
        path = os.environ.get("PATH", "")
        missing = [str(t) for t in tools if t.is_dir() and str(t) not in path.split(os.pathsep)]
        os.environ["PATH"] = os.pathsep.join([*missing, path])
        tessdata = home / "tools" / "tesseract" / "tessdata"
        if tessdata.is_dir():
            os.environ.setdefault("TESSDATA_PREFIX", str(tessdata))
        os.environ.setdefault("HF_HOME", str(home / "models"))
    hide_child_consoles()
    return home


def hide_child_consoles() -> None:
    """No Windows, programas de linha de comando chamados pela interface (FFmpeg, Tesseract…) abririam uma
    janela preta cada um. Força CREATE_NO_WINDOW em todo subprocesso; programas gráficos (OBS, Explorer)
    não são afetados por essa flag."""
    if sys.platform != "win32" or getattr(subprocess.Popen, "_replayflow_hidden", False):
        return
    original = subprocess.Popen.__init__

    def init(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        kwargs["creationflags"] = kwargs.get("creationflags", 0) | subprocess.CREATE_NO_WINDOW
        original(self, *args, **kwargs)

    subprocess.Popen.__init__ = init  # type: ignore[method-assign]
    subprocess.Popen._replayflow_hidden = True  # type: ignore[attr-defined]
