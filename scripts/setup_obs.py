"""Instala e configura o OBS Studio portátil em <destino>/tools/obs para o ReplayFlow (só Windows).

    python scripts/setup_obs.py F:\\Pipeline-Capture [--version 32.2.2]

- modo portátil (configuração dentro da pasta, nada no Windows);
- servidor WebSocket ligado na porta 4455, sem senha (o firewall do Windows bloqueia acesso externo);
- cena com captura do monitor principal + microfone e som do computador;
- gravação em <destino>/Gravacoes, .mkv, microfone na trilha 1 e som do computador na trilha 2;
- sem login de transmissão, sem atualização automática e sem assistente de primeira execução.

Rodar de novo só reescreve a configuração; o download é pulado se o OBS já existir.
"""

from __future__ import annotations

import argparse
import json
import shutil
import tempfile
import urllib.request
import uuid
import zipfile
from pathlib import Path

DEFAULT_VERSION = "32.2.2"
URL = "https://github.com/obsproject/obs-studio/releases/download/{v}/OBS-Studio-{v}-Windows-x64.zip"
PROFILE, COLLECTION = "ReplayFlow", "ReplayFlow"


def screen_size() -> tuple[int, int]:
    try:
        import ctypes

        user32 = ctypes.windll.user32
        user32.SetProcessDPIAware()
        return user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)
    except Exception:
        return 1920, 1080


def install(obs_dir: Path, version: str) -> None:
    if (obs_dir / "bin" / "64bit" / "obs64.exe").exists():
        print("OBS já instalado em", obs_dir)
        return
    print(f"Baixando OBS {version} (~180 MB)…")
    with tempfile.TemporaryDirectory() as tmp:
        zip_path = Path(tmp) / "obs.zip"
        urllib.request.urlretrieve(URL.format(v=version), zip_path)  # noqa: S310 (URL fixa do GitHub oficial)
        with zipfile.ZipFile(zip_path) as z:
            z.extractall(obs_dir)


def write_ini(path: Path, sections: dict[str, dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    for name, values in sections.items():
        lines.append(f"[{name}]")
        lines += [f"{k}={v}" for k, v in values.items()]
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def configure(target: Path, obs_dir: Path) -> None:
    (obs_dir / "portable_mode.txt").touch()
    cfg = obs_dir / "config" / "obs-studio"
    recordings = target / "Gravacoes"
    recordings.mkdir(parents=True, exist_ok=True)
    w, h = screen_size()

    write_ini(cfg / "user.ini", {
        "General": {"EnableAutoUpdates": "false", "FirstRun": "true", "ConfirmOnExit": "false",
                    "Pre19Defaults": "false", "Pre21Defaults": "false", "Pre23Defaults": "false",
                    "Pre24.1Defaults": "false"},
        "BasicWindow": {"SysTrayEnabled": "true", "SysTrayWhenStarted": "false"},
        "Basic": {"Profile": PROFILE, "ProfileDir": PROFILE, "SceneCollection": COLLECTION,
                  "SceneCollectionFile": COLLECTION, "ConfigOnNewProfile": "false"},
    })
    write_ini(cfg / "basic" / "profiles" / PROFILE / "basic.ini", {
        "General": {"Name": PROFILE},
        "Video": {"BaseCX": w, "BaseCY": h, "OutputCX": w, "OutputCY": h, "FPSCommon": 30},
        "Output": {"Mode": "Simple"},
        "SimpleOutput": {"FilePath": str(recordings).replace("\\", "\\\\"), "RecFormat2": "mkv",
                         "RecQuality": "Small", "RecEncoder": "x264", "StreamEncoder": "x264", "RecTracks": 3},
    })
    (cfg / "basic" / "profiles" / PROFILE / "service.json").write_text(
        json.dumps({"type": "rtmp_custom", "settings": {"server": "", "key": "", "use_auth": False}}), encoding="utf-8")

    screen_id, scene_id = str(uuid.uuid4()), str(uuid.uuid4())
    collection = {
        "name": COLLECTION,
        "current_scene": "Cena", "current_program_scene": "Cena", "scene_order": [{"name": "Cena"}],
        # Trilhas: microfone só na 1 (mixers=1), som do computador só na 2 (mixers=2).
        "AuxAudioDevice1": {"name": "Mic/Aux", "uuid": str(uuid.uuid4()), "id": "wasapi_input_capture",
                            "versioned_id": "wasapi_input_capture", "settings": {"device_id": "default"}, "mixers": 1},
        "DesktopAudioDevice1": {"name": "Áudio do desktop", "uuid": str(uuid.uuid4()), "id": "wasapi_output_capture",
                                "versioned_id": "wasapi_output_capture", "settings": {"device_id": "default"},
                                "mixers": 2},
        "sources": [
            # Sem monitor_id: o ReplayFlow escolhe o monitor principal ao gravar (ObsRecorder.ensure_display).
            {"name": "Tela", "uuid": screen_id, "id": "monitor_capture", "versioned_id": "monitor_capture",
             "settings": {"capture_cursor": True}},
            {"name": "Cena", "uuid": scene_id, "id": "scene", "versioned_id": "scene",
             "settings": {"id_counter": 1, "custom_size": False, "items": [
                 {"name": "Tela", "source_uuid": screen_id, "visible": True, "locked": False, "id": 1,
                  "pos": {"x": 0.0, "y": 0.0}, "scale": {"x": 1.0, "y": 1.0}, "align": 5, "bounds_type": 0,
                  "rot": 0.0}]}},
        ],
    }
    scenes = cfg / "basic" / "scenes"
    scenes.mkdir(parents=True, exist_ok=True)
    (scenes / f"{COLLECTION}.json").write_text(json.dumps(collection, ensure_ascii=False, indent=2), encoding="utf-8")

    ws = cfg / "plugin_config" / "obs-websocket" / "config.json"
    ws.parent.mkdir(parents=True, exist_ok=True)
    ws.write_text(json.dumps({"server_enabled": True, "server_port": 4455, "auth_required": False,
                              "alerts_enabled": False, "first_load": False}, indent=2), encoding="utf-8")
    shutil.rmtree(cfg / ".sentinel", ignore_errors=True)  # sem pergunta de "Modo Seguro" na 1ª abertura
    print(f"OBS configurado: tela {w}x{h}, gravações em {recordings}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("target", type=Path, help="pasta portátil do ReplayFlow (ex.: F:\\Pipeline-Capture)")
    ap.add_argument("--version", default=DEFAULT_VERSION)
    args = ap.parse_args()
    obs_dir = args.target / "tools" / "obs"
    install(obs_dir, args.version)
    configure(args.target, obs_dir)


if __name__ == "__main__":
    main()
