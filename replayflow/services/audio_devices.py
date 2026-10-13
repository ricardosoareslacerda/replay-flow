"""Lista microfones e saídas de som ativos do Windows, no formato de device_id do OBS (WASAPI).

Lê o registro (MMDevices): funciona sem o OBS aberto e sem permissão de administrador.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass

DEFAULT = "default"
_ROOT = r"SOFTWARE\Microsoft\Windows\CurrentVersion\MMDevices\Audio"
_DESC = "{a45c254e-df1c-4efd-8020-67d146a850e0},2"   # ex.: "Microfone"
_IFACE = "{b3f8fa53-0004-438e-9003-51a46e139bfc},6"  # ex.: "USB PnP Sound Device"
_ACTIVE = 0x1
_FLOW = {"Capture": "{0.0.1.00000000}", "Render": "{0.0.0.00000000}"}


@dataclass(frozen=True)
class AudioDevice:
    id: str
    name: str


def _list(kind: str) -> list[AudioDevice]:
    if sys.platform != "win32":
        return []
    import winreg

    devices = []
    try:
        root = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, rf"{_ROOT}\{kind}")
    except OSError:
        return []
    with root:
        for i in range(winreg.QueryInfoKey(root)[0]):
            guid = winreg.EnumKey(root, i)
            try:
                with winreg.OpenKey(root, guid) as dev:
                    state = winreg.QueryValueEx(dev, "DeviceState")[0]
                with winreg.OpenKey(root, rf"{guid}\Properties") as props:
                    desc = winreg.QueryValueEx(props, _DESC)[0]
                    iface = winreg.QueryValueEx(props, _IFACE)[0]
            except OSError:
                continue
            if state == _ACTIVE:  # bits altos marcam endpoints ocultos (ex.: Mixagem estéreo), que o OBS não lista
                devices.append(AudioDevice(f"{_FLOW[kind]}.{guid}", f"{desc} ({iface})"))
    return sorted(devices, key=lambda d: d.name)


def microphones() -> list[AudioDevice]:
    return _list("Capture")


def outputs() -> list[AudioDevice]:
    return _list("Render")
