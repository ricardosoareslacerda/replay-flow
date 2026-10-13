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
WEBCAM_SOURCE = "Webcam"
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

    def configure(self, monitor, region, out_height: int, webcam: bool = False,  # type: ignore[no-untyped-def]
                  webcam_corner: str = "bottom-right") -> tuple[int, int]:
        """Prepara a cena para gravar exatamente `region` (tela inteira, área desenhada ou janela).

        Tudo vira um recorte da captura do monitor: o quadro do vídeo passa a ter o tamanho da área
        (sem tarjas) e a saída é reduzida para a altura escolhida (HD 720 / Full HD 1080) mantendo a
        proporção. Assim o destaque do mouse aparece no vídeo em qualquer modo e capture_target.to_video
        converte os cliques com uma conta só. Devolve o tamanho do vídeo (largura, altura).
        """
        c = self._client
        scene = c.get_current_program_scene().current_program_scene_name
        items = c.get_scene_item_list(scene).scene_items
        screen = next((i for i in items if i.get("inputKind") == "monitor_capture"), None)
        if screen is None:
            raise ReplayFlowError("A cena do OBS não tem captura de tela. Rode scripts/setup_obs.py de novo.")
        options = c.get_input_properties_list_property_items(screen["sourceName"], "monitor_id").property_items
        origin = f"@ {monitor.region[0]},{monitor.region[1]}"
        chosen = next((o for o in options if origin in o["itemName"]), None)
        if chosen:
            c.set_input_settings(screen["sourceName"], {"monitor_id": chosen["itemValue"]}, True)

        even = lambda v: max(2, int(round(v)) // 2 * 2)  # noqa: E731 - encoders exigem dimensões pares
        left, top, w, h = region
        w, h = even(w), even(h)
        scale = min(1.0, out_height / h)
        out_w, out_h = even(w * scale), even(h * scale)
        c.set_video_settings(30, 1, w, h, out_w, out_h)

        mx, my, mw, mh = monitor.region
        crop = {"cropLeft": left - mx, "cropTop": top - my,
                "cropRight": max(0, mx + mw - (left + w)), "cropBottom": max(0, my + mh - (top + h))}
        c.set_scene_item_enabled(scene, screen["sceneItemId"], True)
        c.set_scene_item_transform(scene, screen["sceneItemId"], {
            "positionX": 0.0, "positionY": 0.0, "alignment": 5, "boundsType": "OBS_BOUNDS_NONE",
            "scaleX": 1.0, "scaleY": 1.0, **{k: float(v) for k, v in crop.items()}})
        for i in items:  # captura de janela antiga (versões anteriores) fica desligada
            if i.get("sourceName") == WINDOW_SOURCE:
                c.set_scene_item_enabled(scene, i["sceneItemId"], False)
        self._webcam(scene, items, webcam, w, h, webcam_corner)
        log.info("área gravada: %dx%d a partir de (%d, %d); vídeo %dx%d", w, h, left, top, out_w, out_h)
        return out_w, out_h

    def _webcam(self, scene: str, items: list[dict], on: bool, w: int, h: int, corner: str) -> None:
        """Rosto no canto (~20% da largura), por cima da tela gravada."""
        c = self._client
        cam = next((i for i in items if i.get("sourceName") == WEBCAM_SOURCE), None)
        if not on:
            if cam:
                c.set_scene_item_enabled(scene, cam["sceneItemId"], False)
            return
        if cam is None:
            item_id = c.create_input(scene, WEBCAM_SOURCE, "dshow_input", {}, True).scene_item_id
            devices = c.get_input_properties_list_property_items(WEBCAM_SOURCE, "video_device_id").property_items
            real = [d for d in devices if d["itemValue"]]
            if not real:
                c.remove_input(WEBCAM_SOURCE)
                raise ReplayFlowError("Nenhuma webcam encontrada. Desligue a opção Webcam ou conecte uma câmera.")
            c.set_input_settings(WEBCAM_SOURCE, {"video_device_id": real[0]["itemValue"]}, True)
        else:
            item_id = cam["sceneItemId"]
        size = round(w * 0.2)
        margin = round(w * 0.02)
        right, bottom = corner.endswith("right"), corner.startswith("bottom")
        c.set_scene_item_enabled(scene, item_id, True)
        c.set_scene_item_index(scene, item_id, len(items))  # sempre por cima
        c.set_scene_item_transform(scene, item_id, {
            "boundsType": "OBS_BOUNDS_SCALE_INNER", "boundsWidth": float(size), "boundsHeight": float(size * 3 // 4),
            "boundsAlignment": 0, "alignment": (8 if bottom else 4) | (2 if right else 1),
            "positionX": float(w - margin if right else margin), "positionY": float(h - margin if bottom else margin)})

    def set_audio(self, mic_on: bool, speaker_on: bool) -> None:
        """Interruptores Microfone e Som do computador: mudo no OBS (a trilha continua existindo)."""
        special = self._client.get_special_inputs()
        for name, on in ((special.mic1, mic_on), (special.desktop1, speaker_on)):
            if name:
                self._client.set_input_mute(name, not on)
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
