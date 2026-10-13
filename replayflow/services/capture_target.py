"""O que gravar: um monitor inteiro ou uma janela específica (Windows).

A gravação mostra só essa área, mas os cliques chegam em coordenadas da área de trabalho inteira.
`region` guarda o retângulo gravado (em pixels físicos da tela) para o pipeline converter cada clique
para a posição certa no vídeo (foto do clique e rótulo lido sob o cursor).
"""

from __future__ import annotations

import sys
from dataclasses import dataclass

Region = tuple[int, int, int, int]  # (esquerda, topo, largura, altura) na área de trabalho


@dataclass(frozen=True)
class Target:
    kind: str                 # "monitor" | "window"
    label: str
    region: Region
    hwnd: int = 0
    window_spec: str = ""     # formato do OBS: "título:classe:executável.exe"
    primary: bool = False


def monitors() -> list[Target]:
    if sys.platform != "win32":
        return []
    import ctypes
    from ctypes import wintypes

    class MONITORINFO(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT), ("rcWork", wintypes.RECT),
                    ("dwFlags", wintypes.DWORD)]

    found: list[Target] = []
    proc = ctypes.WINFUNCTYPE(ctypes.c_int, wintypes.HMONITOR, wintypes.HDC, ctypes.POINTER(wintypes.RECT),
                              wintypes.LPARAM)

    def cb(hmon, _hdc, _rect, _data):  # type: ignore[no-untyped-def]
        info = MONITORINFO(cbSize=ctypes.sizeof(MONITORINFO))
        ctypes.windll.user32.GetMonitorInfoW(hmon, ctypes.byref(info))
        r = info.rcMonitor
        primary = bool(info.dwFlags & 1)
        n = len(found) + 1
        found.append(Target("monitor", f"Tela inteira · Monitor {n} · {r.right - r.left}×{r.bottom - r.top}"
                            + (" (principal)" if primary else ""), (r.left, r.top, r.right - r.left, r.bottom - r.top),
                            primary=primary))
        return 1

    ctypes.windll.user32.EnumDisplayMonitors(None, None, proc(cb), 0)
    return sorted(found, key=lambda t: (not t.primary, t.region[0], t.region[1]))


def _exe_name(hwnd: int) -> str:
    import ctypes
    from ctypes import wintypes

    pid = wintypes.DWORD()
    ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid.value)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(1024)
        size = wintypes.DWORD(1024)
        ctypes.windll.kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size))
        return buf.value.rsplit("\\", 1)[-1]
    finally:
        ctypes.windll.kernel32.CloseHandle(handle)


def obs_escape(text: str) -> str:
    """O OBS separa título:classe:exe por ':' e codifica ':' e '#' dentro de cada parte."""
    return text.replace("#", "#22").replace(":", "#3A")


def client_region(hwnd: int) -> Region | None:
    """Área interna da janela (sem bordas e barra de título) em coordenadas da área de trabalho."""
    if sys.platform != "win32":
        return None
    import ctypes
    from ctypes import wintypes

    rect, origin = wintypes.RECT(), wintypes.POINT(0, 0)
    if not ctypes.windll.user32.GetClientRect(hwnd, ctypes.byref(rect)):
        return None
    ctypes.windll.user32.ClientToScreen(hwnd, ctypes.byref(origin))
    return origin.x, origin.y, rect.right - rect.left, rect.bottom - rect.top


def windows(skip_titles: tuple[str, ...] = ("ReplayFlow", "OBS ")) -> list[Target]:
    if sys.platform != "win32":
        return []
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    found: list[Target] = []
    proc = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)

    def cb(hwnd, _data):  # type: ignore[no-untyped-def]
        if not user32.IsWindowVisible(hwnd) or user32.IsIconic(hwnd) or user32.GetWindow(hwnd, 4):  # 4 = dono
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if not length:
            return True
        title = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, title, length + 1)
        cls = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, cls, 256)
        region = client_region(hwnd)
        if (not region or region[2] < 200 or region[3] < 150 or title.value.startswith(skip_titles)
                or cls.value in ("Progman", "WorkerW", "Shell_TrayWnd")):
            return True
        exe = _exe_name(hwnd)
        spec = ":".join(obs_escape(p) for p in (title.value, cls.value, exe))
        found.append(Target("window", f"Janela · {title.value[:70]}", region, int(hwnd), spec))
        return True

    user32.EnumWindows(proc(cb), 0)
    return found


def to_video(x: float, y: float, region: Region | None, video_w: int, video_h: int) -> tuple[float, float] | None:
    """Converte um clique da área de trabalho para pixels do vídeo; None se caiu fora da área gravada.

    O OBS encaixa a área no quadro mantendo a proporção, alinhada no canto superior esquerdo.
    Sem região (gravações antigas ou vídeo importado), vale o comportamento anterior: tela = vídeo.
    """
    if region is None:
        return x, y
    left, top, w, h = region
    if not (left <= x < left + w and top <= y < top + h):
        return None
    s = min(video_w / w, video_h / h)
    return (x - left) * s, (y - top) * s
