# ReplayFlow

Gravador local de fluxo de trabalho: vídeo (OBS), narração, OCR e input viram uma timeline única (`timeline.jsonl`), da qual saem os pacotes para análise por IA. Tudo roda 100% local; o modo privado bloqueia rede externa.

> Em construção. Etapas 1 a 3 concluídas (fundação, pipeline, exportação + UI). Rode `replayflow` sem argumentos para abrir a interface. Instalação completa (FFmpeg, Tesseract, faster-whisper) e UI entram nas próximas etapas.

```powershell
py -3.11 -m venv .venv; .venv\Scripts\Activate.ps1
pip install -e .[all,dev]
pytest
replayflow new "Automação boletos" --video C:\caminho\gravacao.mkv
replayflow download-model small          # única ação com rede, feita uma vez
replayflow process <pasta-da-sessão>      # processa ou retoma
```
