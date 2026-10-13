# ReplayFlow

Gravador local de fluxo de trabalho: vídeo (OBS), narração, OCR e input viram uma timeline única (`timeline.jsonl`), da qual saem os pacotes para análise por IA. Tudo roda 100% local; o modo privado bloqueia rede externa.

> Em construção. Etapa 1 (fundação) concluída. Instalação completa (FFmpeg, Tesseract, faster-whisper) e UI entram nas próximas etapas.

```powershell
py -3.11 -m venv .venv; .venv\Scripts\Activate.ps1
pip install -e .[dev]
pytest
replayflow new "Automação boletos" --video C:\caminho\gravacao.mkv
```
