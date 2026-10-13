"""OCR local com Tesseract (pytesseract)."""

from __future__ import annotations

import shutil
import sys
import threading
from pathlib import Path

from replayflow.core.errors import CancelledError, ReplayFlowError, ToolMissingError

MAX_DX, MAX_DY = 60, 24  # folga (em pixels da imagem ampliada 2x) entre o cursor e o texto aceito
MIN_LABEL_CONF = 70  # abaixo disso o "rótulo" costuma ser ícone lido como letras (ex.: "EEB -")
_WINDOWS_DEFAULT = Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe")


def _pytesseract():  # type: ignore[no-untyped-def]
    try:
        import pytesseract
    except ImportError:
        raise ToolMissingError("pytesseract não instalado: pip install -e .[media]") from None
    if not shutil.which("tesseract") and sys.platform == "win32" and _WINDOWS_DEFAULT.exists():
        pytesseract.pytesseract.tesseract_cmd = str(_WINDOWS_DEFAULT)
    try:
        pytesseract.get_tesseract_version()
    except pytesseract.TesseractNotFoundError:
        raise ToolMissingError("Tesseract não encontrado. Instale-o com o idioma 'por' (veja o README).") from None
    return pytesseract


def prepare(img):  # type: ignore[no-untyped-def]
    """Cinza e 2x maior: texto de interface é pequeno e o frame já foi reduzido; sem isso o Tesseract
    cola palavras ("Splitnarration") e troca letras."""
    from PIL import Image, ImageOps

    gray = ImageOps.grayscale(img)
    return gray.resize((gray.width * 2, gray.height * 2), Image.Resampling.LANCZOS)


def run(frames: list[Path], ocr_dir: Path, lang: str, cancel: threading.Event | None = None) -> dict[Path, str]:
    """Gera ocr/<frame>.txt para cada frame. Retorna {frame: texto}."""
    from PIL import Image

    tess = _pytesseract()
    installed = tess.get_languages(config="")
    for code in lang.split("+"):
        if code not in installed:
            raise ToolMissingError(f"Idioma '{code}' do Tesseract não instalado (pacote tesseract-ocr-{code}).")
    texts: dict[Path, str] = {}
    for frame in frames:
        if cancel is not None and cancel.is_set():
            raise CancelledError()
        try:
            with Image.open(frame) as img:
                text = tess.image_to_string(prepare(img), lang=lang).strip()
        except tess.TesseractError as e:
            raise ReplayFlowError(f"Tesseract falhou em {frame.name}: {e}") from e
        (ocr_dir / f"{frame.stem}.txt").write_text(text, encoding="utf-8")
        texts[frame] = text
    return texts


def text_at(image: Path, x: float, y: float, lang: str, half_w: int = 260, half_h: int = 50) -> str:
    """Rótulo em que o usuário clicou: a linha de texto que contém o ponto (x, y) da imagem.

    Só aceita texto sob o cursor (com pequena folga). Rótulo vizinho é pior que nenhum: um clique em
    "Salvar" não pode virar "Cancelar" só porque "Cancelar" foi o texto mais legível por perto.
    """
    from PIL import Image

    tess = _pytesseract()
    with Image.open(image) as img:
        left, top = max(0, int(x) - half_w), max(0, int(y) - half_h)
        area = prepare(img.crop((left, top, min(img.width, int(x) + half_w), min(img.height, int(y) + half_h))))
        tl, tt = max(0, int(x) - 70), max(0, int(y) - 14)
        tight = prepare(img.crop((tl, tt, min(img.width, int(x) + 70), min(img.height, int(y) + 14))))
    label = _line_at(tess, area, lang, "--psm 11", (x - left) * 2, (y - top) * 2)
    if not label:  # botões com borda costumam sumir na leitura esparsa; tenta só a faixa sob o cursor
        label = _line_at(tess, tight, lang, "--psm 7", (x - tl) * 2, (y - tt) * 2)
    return label[:80]


def text_near(image: Path, x: float, y: float, lang: str, half_w: int = 260, half_h: int = 90) -> str:
    """Texto da região em volta do clique: pega caixas de diálogo e campos que o OCR da tela inteira perde."""
    from PIL import Image

    tess = _pytesseract()
    with Image.open(image) as img:
        box = (max(0, int(x) - half_w), max(0, int(y) - half_h), min(img.width, int(x) + half_w), min(img.height, int(y) + half_h))
        crop = prepare(img.crop(box))
    try:
        text = tess.image_to_string(crop, lang=lang, config="--psm 6")
    except tess.TesseractError:
        return ""
    lines = [" ".join(line.split()) for line in text.splitlines()]
    return "\n".join(line for line in lines if _looks_like_label(line))


def _line_at(tess, img, lang: str, config: str, cx: float, cy: float) -> str:  # type: ignore[no-untyped-def]
    try:
        data = tess.image_to_data(img, lang=lang, config=config, output_type=tess.Output.DICT)
    except tess.TesseractError:
        return ""
    lines: dict[tuple[int, int, int], list[int]] = {}
    for i, word in enumerate(data["text"]):
        if word.strip() and float(data["conf"][i]) > 30:
            lines.setdefault((data["block_num"][i], data["par_num"][i], data["line_num"][i]), []).append(i)
    best, best_d = "", float("inf")
    for idx in lines.values():
        x0 = min(data["left"][i] for i in idx)
        x1 = max(data["left"][i] + data["width"][i] for i in idx)
        y0 = min(data["top"][i] for i in idx)
        y1 = max(data["top"][i] + data["height"][i] for i in idx)
        dx = max(x0 - cx, 0, cx - x1)
        dy = max(y0 - cy, 0, cy - y1)
        if dx > MAX_DX or dy > MAX_DY:
            continue
        text = " ".join(data["text"][i] for i in idx)
        conf = sum(float(data["conf"][i]) for i in idx) / len(idx)
        if dx + 3 * dy < best_d and conf >= MIN_LABEL_CONF and _looks_like_label(text):
            best, best_d = text, dx + 3 * dy
    return best.strip()

def _looks_like_label(text: str) -> bool:
    """Descarta ruído de ícones ('(O)', 'a]'): exige uma palavra de verdade."""
    chars = [c for c in text if not c.isspace()]
    return any(sum(c.isalpha() for c in w) >= 3 for w in text.split()) and (
        sum(c.isalnum() for c in chars) >= 0.6 * len(chars))


def version() -> str:
    try:
        return str(_pytesseract().get_tesseract_version())
    except ToolMissingError:
        return "ausente"
