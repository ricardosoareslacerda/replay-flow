"""Controle opcional do OBS via obs-websocket (somente localhost; senha não é armazenada).

Na instalação portátil o OBS fica em <REPLAYFLOW_HOME>/tools/obs; o ReplayFlow o abre sozinho,
minimizado na bandeja, quando o usuário clica em Gravar, e o fecha ao sair.
"""

from __future__ import annotations

import socket
import subprocess
import time
from pathlib import Path

from replayflow.core.errors import ReplayFlowError, ToolMissingError
from replayflow.models.config import portable_home

OBS_ARGS = ["--minimize-to-tray", "--disable-updater", "--disable-shutdown-check"]


def portable_obs_exe() -> Path | None:
    home = portable_home()
    exe = home / "tools" / "obs" / "bin" / "64bit" / "obs64.exe" if home else None
    return exe if exe and exe.exists() else None


def port_open(host: str = "localhost", port: int = 4455) -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.5):
            return True
    except OSError:
        return False


def launch_portable_obs(port: int = 4455, wait_s: float = 30) -> subprocess.Popen[bytes] | None:
    """Abre o OBS portátil se ele não estiver rodando. Devolve o processo se fomos nós que o abrimos."""
    if port_open(port=port):
        return None
    exe = portable_obs_exe()
    if not exe:
        return None
    proc = subprocess.Popen([str(exe), *OBS_ARGS], cwd=exe.parent)
    deadline = time.monotonic() + wait_s
    while time.monotonic() < deadline:
        if port_open(port=port):
            time.sleep(1)  # o servidor aceita conexão um pouco antes de carregar a cena
            return proc
        if proc.poll() is not None:
            raise ReplayFlowError("O OBS fechou logo após abrir. Abra o OBS.bat da pasta do ReplayFlow para ver o motivo.")
        time.sleep(0.5)
    raise ReplayFlowError(f"O OBS abriu, mas o servidor WebSocket não respondeu em {wait_s:.0f}s.")


class ObsRecorder:
    def __init__(self, host: str = "localhost", port: int = 4455, password: str | None = None) -> None:
        try:
            import obsws_python as obs
        except ImportError:
            raise ToolMissingError("obsws-python não instalado: pip install -e .[obs]") from None
        try:
            self._client = obs.ReqClient(host=host, port=port, password=password or "", timeout=3)
        except Exception as e:
            raise ReplayFlowError(
                f"Não conectou ao OBS em {host}:{port}. Ative no OBS: Ferramentas > Configurações do Servidor WebSocket. ({e})"
            ) from e

    def start(self) -> None:
        self._client.start_record()

    def stop(self) -> Path:
        """Para a gravação e devolve o caminho do arquivo gerado pelo OBS."""
        return Path(self._client.stop_record().output_path)
