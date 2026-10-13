"""Controle opcional do OBS via obs-websocket (somente localhost; senha não é armazenada).

Na instalação portátil o OBS fica em <REPLAYFLOW_HOME>/tools/obs; o ReplayFlow o abre sozinho,
minimizado na bandeja, quando o usuário clica em Gravar, e o fecha ao sair.
"""

from __future__ import annotations

import logging
import shutil
import socket
import subprocess
import time
from pathlib import Path

from replayflow.core.errors import ReplayFlowError, ToolMissingError
from replayflow.models.config import portable_home

log = logging.getLogger(__name__)

WINDOW_SOURCE = "Janela"
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


def clear_crash_flag(exe: Path) -> None:
    """Apaga as marcas de execução do OBS portátil.

    O ReplayFlow encerra o OBS que ele mesmo abriu; sem isso o OBS acharia que travou e perguntaria
    a cada abertura se deve iniciar no Modo Seguro (que desligaria o WebSocket usado para gravar).
    """
    sentinel = exe.parents[2] / "config" / "obs-studio" / ".sentinel"
    for run in sentinel.glob("run_*"):
        shutil.rmtree(run, ignore_errors=True) if run.is_dir() else run.unlink(missing_ok=True)


def stop_portable_obs(proc: subprocess.Popen[bytes]) -> None:
    if proc.poll() is None:
        proc.terminate()
        proc.wait(10)
    if exe := portable_obs_exe():
        clear_crash_flag(exe)


def launch_portable_obs(port: int = 4455, wait_s: float = 30) -> subprocess.Popen[bytes] | None:
    """Abre o OBS portátil se ele não estiver rodando. Devolve o processo se fomos nós que o abrimos."""
    if port_open(port=port):
        return None
    exe = portable_obs_exe()
    if not exe:
        return None
    clear_crash_flag(exe)
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

    def prepare_tracks(self) -> bool:
        """Microfone na trilha 1 e som do computador na trilha 2 (narração limpa para a transcrição)."""
        try:
            special = self._client.get_special_inputs()
            mic, desktop = special.mic1, special.desktop1
            if not mic:
                return False
            only = lambda n: {str(i): i == n for i in range(1, 7)}  # noqa: E731
            self._client.set_input_audio_tracks(mic, only(1))
            if desktop:
                self._client.set_input_audio_tracks(desktop, only(2))
            mode = self._client.get_profile_parameter("Output", "Mode").parameter_value or "Simple"
            section = "SimpleOutput" if mode == "Simple" else "AdvOut"
            self._client.set_profile_parameter(section, "RecTracks", "3")
            return True
        except Exception as e:  # trilhas separadas são melhoria, não requisito
            log.warning("não foi possível separar as trilhas de áudio no OBS: %s", e)
            return False

    def ensure_display(self) -> None:
        """Captura de tela sem monitor escolhido grava preto: escolhe o monitor principal."""
        try:
            inputs = self._client.get_input_list("monitor_capture").inputs
            for item in inputs:
                name = item["inputName"]
                if self._client.get_input_settings(name).input_settings.get("monitor_id"):
                    continue
                options = self._client.get_input_properties_list_property_items(name, "monitor_id").property_items
                real = [o for o in options if o["itemValue"] and "Selec" not in o["itemName"]]
                primary = next((o for o in real if "principal" in o["itemName"].lower() or "primary" in o["itemName"].lower()),
                               real[0] if real else None)
                if primary:
                    self._client.set_input_settings(name, {"monitor_id": primary["itemValue"]}, True)
                    log.info("captura de tela: %s", primary["itemName"])
        except Exception as e:  # sem isso a gravação ainda funciona se o monitor já estiver escolhido
            log.warning("não foi possível conferir o monitor da captura no OBS: %s", e)

    def set_target(self, target) -> None:  # type: ignore[no-untyped-def]
        """Grava só o monitor ou a janela escolhida, encaixada no quadro e alinhada no canto superior
        esquerdo (é essa a conta que capture_target.to_video desfaz para posicionar os cliques)."""
        c = self._client
        scene = c.get_current_program_scene().current_program_scene_name
        video = c.get_video_settings()
        items = c.get_scene_item_list(scene).scene_items
        screen = next((i for i in items if i.get("inputKind") == "monitor_capture"), None)
        window = next((i for i in items if i.get("sourceName") == WINDOW_SOURCE), None)
        if target.kind == "monitor":
            if screen is None:
                raise ReplayFlowError("A cena do OBS não tem captura de tela. Rode scripts/setup_obs.py de novo.")
            options = c.get_input_properties_list_property_items(screen["sourceName"], "monitor_id").property_items
            origin = f"@ {target.region[0]},{target.region[1]}"
            chosen = next((o for o in options if origin in o["itemName"]), None)
            if chosen:
                c.set_input_settings(screen["sourceName"], {"monitor_id": chosen["itemValue"]}, True)
            active, inactive = screen, window
        else:
            settings = {"window": target.window_spec, "method": 2, "client_area": True, "cursor": True}
            if window is None:
                item_id = c.create_input(scene, WINDOW_SOURCE, "window_capture", settings, True).scene_item_id
                window = {"sourceName": WINDOW_SOURCE, "sceneItemId": item_id}
            else:
                c.set_input_settings(WINDOW_SOURCE, settings, True)
            active, inactive = window, screen
        if inactive is not None:
            c.set_scene_item_enabled(scene, inactive["sceneItemId"], False)
        c.set_scene_item_enabled(scene, active["sceneItemId"], True)
        c.set_scene_item_transform(scene, active["sceneItemId"], {
            "positionX": 0.0, "positionY": 0.0, "alignment": 5, "boundsType": "OBS_BOUNDS_SCALE_INNER",
            "boundsWidth": float(video.base_width), "boundsHeight": float(video.base_height), "boundsAlignment": 5})
        log.info("gravando: %s", target.label)

    def set_devices(self, mic_id: str, desktop_id: str) -> None:
        """Escolhe qual microfone e qual saída de som o OBS grava ("default" = padrão do Windows)."""
        special = self._client.get_special_inputs()
        for name, device in ((special.mic1, mic_id), (special.desktop1, desktop_id)):
            if name:
                self._client.set_input_settings(name, {"device_id": device}, True)
                self._client.set_input_mute(name, False)

    def start(self) -> float:
        """Inicia a gravação e devolve o instante (time.monotonic) em que o vídeo de fato começou."""
        self._client.start_record()
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            status = self._client.get_record_status()
            now = time.monotonic()
            if status.output_active and status.output_duration > 0:
                return now - status.output_duration / 1000
            time.sleep(0.02)
        raise ReplayFlowError("O OBS não confirmou o início da gravação em 10s.")

    def pause(self) -> None:
        self._client.pause_record()

    def resume(self) -> None:
        self._client.resume_record()

    def stop(self) -> Path:
        """Para a gravação e devolve o caminho do arquivo gerado pelo OBS, já fechado e completo."""
        path = Path(self._client.stop_record().output_path)
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:  # StopRecord responde antes de o OBS terminar de gravar o arquivo
            if not self._client.get_record_status().output_active:
                try:
                    with path.open("ab"):
                        return path
                except PermissionError:
                    pass
            time.sleep(0.2)
        raise ReplayFlowError(f"O OBS não terminou de gravar {path.name} em 30s.")
