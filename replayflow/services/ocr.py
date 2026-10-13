"""OCR local com Tesseract (pytesseract)."""

from __future__ import annotations

import shutil
import sys
import threading
from pathlib import Path

from replayflow.core.errors import CancelledError, ReplayFlowError, ToolMissingError

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


def run(frames: list[Path], ocr_dir: Path, lang: str, cancel: threading.Event | None = None) -> dict[Path, str]:
    """Gera ocr/<frame>.txt para cada frame. Retorna {frame: texto}."""
    from PIL import Image

    tess = _pytesseract()
    if lang not in tess.get_languages(config=""):
        raise ToolMissingError(f"Idioma '{lang}' do Tesseract não instalado (pacote tesseract-ocr-{lang}).")
    texts: dict[Path, str] = {}
    for frame in frames:
        if cancel is not None and cancel.is_set():
            raise CancelledError()
        try:
            with Image.open(frame) as img:
                text = tess.image_to_string(img, lang=lang).strip()
        except tess.TesseractError as e:
            raise ReplayFlowError(f"Tesseract falhou em {frame.name}: {e}") from e
        (ocr_dir / f"{frame.stem}.txt").write_text(text, encoding="utf-8")
        texts[frame] = text
    return texts


def version() -> str:
    try:
        return str(_pytesseract().get_tesseract_version())
    except ToolMissingError:
        return "ausente"
