"""Gera <destino>/ReplayFlow.exe: um executável único que abre o ReplayFlow portátil (Windows).

    python scripts/make_launcher.py F:\\Pipeline-Capture

É o lançador gráfico do distlib (o mesmo que o pip usa nos scripts), com o caminho do Python escrito
relativo à pasta do próprio .exe (<launcher_dir>), seguido de um zip com o __main__ que chama o app.
Assim a pasta continua portátil (outra letra de unidade, pendrive) e nada depende de .bat.
Em Linux e macOS use `python3 -m replayflow` (ou o comando `replayflow` instalado pelo pip).
"""

from __future__ import annotations

import argparse
import io
import zipfile
from pathlib import Path

MAIN = b"""import sys
from replayflow.app.main import main
sys.exit(main())
"""


def build(target: Path) -> Path:
    import pip._vendor.distlib as distlib

    launcher = (Path(distlib.__file__).parent / "w64.exe").read_bytes()  # w = sem console
    shebang = b"#!<launcher_dir>\\python\\pythonw.exe\r\n"
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w") as z:
        z.writestr("__main__.py", MAIN)
    exe = target / "ReplayFlow.exe"
    exe.write_bytes(launcher + shebang + payload.getvalue())
    return exe


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("target", type=Path, help="pasta portátil (ex.: F:\\Pipeline-Capture)")
    print("gerado:", build(ap.parse_args().target))
