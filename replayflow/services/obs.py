"""Controle opcional do OBS via obs-websocket (somente localhost; senha não é armazenada)."""

from __future__ import annotations

from pathlib import Path

from replayflow.core.errors import ReplayFlowError, ToolMissingError


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
