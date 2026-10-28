"""dHash de 64 bits com Pillow: suficiente para detectar telas repetidas, sem dependência extra."""

from __future__ import annotations

from pathlib import Path

from PIL import Image


def dhash(path: Path, size: int = 8) -> int:
    with Image.open(path) as img:
        px = img.convert("L").resize((size + 1, size), Image.Resampling.LANCZOS).tobytes()
    bits = 0
    for row in range(size):
        for col in range(size):
            i = row * (size + 1) + col
            bits = (bits << 1) | (px[i] > px[i + 1])
    return bits


def distance(a: int, b: int) -> int:
    return (a ^ b).bit_count()
