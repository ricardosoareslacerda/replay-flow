#!/usr/bin/env bash
# Setup do ReplayFlow no macOS/Linux: ferramentas do sistema, venv e dependências Python.
# Uso: ./scripts/setup.sh [modelo-whisper]   ex.: ./scripts/setup.sh small
set -euo pipefail
cd "$(dirname "$0")/.."
MODEL="${1:-}"

if command -v brew >/dev/null; then
  brew install python@3.12 ffmpeg tesseract tesseract-lang
elif command -v apt-get >/dev/null; then
  sudo apt-get update
  sudo apt-get install -y python3 python3-venv ffmpeg tesseract-ocr tesseract-ocr-por libegl1 libxkbcommon-x11-0
elif command -v dnf >/dev/null; then
  sudo dnf install -y python3 ffmpeg tesseract tesseract-langpack-por
else
  echo "Gerenciador de pacotes não reconhecido: instale python3.11+, ffmpeg e tesseract (com 'por') manualmente." >&2
fi

PY="$(command -v python3.12 || command -v python3)"
"$PY" -c 'import sys; assert sys.version_info >= (3, 11), f"Python {sys.version} < 3.11"'
[ -d .venv ] || "$PY" -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -e ".[all,dev]"

[ -n "$MODEL" ] && .venv/bin/replayflow download-model "$MODEL"

echo
echo "FFmpeg:    $(command -v ffmpeg >/dev/null && echo OK || echo AUSENTE)"
echo "Tesseract: $(tesseract --list-langs 2>/dev/null | grep -qx por && echo 'OK (por)' || echo 'AUSENTE ou sem idioma por')"
echo
echo "Pronto. Rode: .venv/bin/replayflow"
