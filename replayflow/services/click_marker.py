"""Desenha onde o usuário clicou na foto do clique (círculo vermelho)."""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw


def mark(image: Path, x: int, y: int, scale: float, quality: int = 85) -> bool:
    """x, y em pixels da tela gravada; scale = largura da imagem / largura do vídeo.

    Devolve False se o clique caiu fora da imagem (ex.: outro monitor), sem alterar o arquivo.
    """
    with Image.open(image) as img:
        cx, cy = x * scale, y * scale
        if not (0 <= cx < img.width and 0 <= cy < img.height):
            return False
        r = max(10, img.width // 60)
        out = img.convert("RGB")
        draw = ImageDraw.Draw(out)
        draw.ellipse((cx - r, cy - r, cx + r, cy + r), outline=(230, 30, 30), width=max(3, r // 4))
        draw.ellipse((cx - 2, cy - 2, cx + 2, cy + 2), fill=(230, 30, 30))
    out.save(image, quality=quality)
    return True
