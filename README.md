# ReplayFlow

Gravador local de fluxo de trabalho. Uma sessão gravada no OBS (vídeo + narração), somada às suas ações (cliques, texto digitado, janela ativa), vira uma **linha do tempo única** (`timeline.jsonl`): um gêmeo digital do que foi feito na tela. Dela saem os artefatos para auditoria, tutorial, QA ou análise por IA, como o `claude_package.md`.

Tudo roda **100% local**. No modo privado (padrão), o processo bloqueia qualquer conexão de rede fora do `localhost`.

- [Instalação](#instalação)
- [Uso](#uso)
- [Estrutura de uma sessão](#estrutura-de-uma-sessão)
- [Privacidade](#privacidade)
- [Arquitetura](#arquitetura)
- [Desenvolvimento](#desenvolvimento)
- [Melhorias futuras](#melhorias-futuras)

## Instalação

Requisitos: Python 3.11+, FFmpeg e Tesseract com o idioma português (`por`).

### Windows (recomendado)

```powershell
git clone https://github.com/ricardosoareslacerda/replay-flow.git
cd replay-flow
powershell -ExecutionPolicy Bypass -File scripts\setup.ps1 -InstallTools -Model small
```

O script cria a `.venv`, instala as dependências, instala FFmpeg (`Gyan.FFmpeg`) e Tesseract (`UB-Mannheim.TesseractOCR`) via `winget`, baixa o `por.traineddata` e o modelo Whisper escolhido. Reabra o terminal depois de instalar o FFmpeg para que ele entre no `PATH`.

Instalação manual equivalente:

```powershell
winget install Python.Python.3.12
winget install Gyan.FFmpeg
winget install UB-Mannheim.TesseractOCR   # no instalador, marque "Portuguese" em Additional language data
py -3.12 -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e ".[all,dev]"
replayflow download-model small
```

O Tesseract é encontrado automaticamente em `C:\Program Files\Tesseract-OCR\` mesmo fora do `PATH`.

### Windows portátil (pasta única, nada instalado no sistema)

```powershell
git clone https://github.com/ricardosoareslacerda/replay-flow.git
powershell -ExecutionPolicy Bypass -File replay-flow\scripts\build-portable.ps1 -Target F:\Pipeline-Capture -Model small
```

Monta `F:\Pipeline-Capture` com Python 3.12 embeddable, dependências, FFmpeg, Tesseract (+ português e inglês), OBS Studio portátil já configurado (`scripts/setup_obs.py`), modelo Whisper e o lançador `ReplayFlow.bat`. Com placa NVIDIA, inclui também cuBLAS/cuDNN (~1,3 GB) e a transcrição roda na GPU, voltando sozinha para a CPU se a placa falhar. Sessões e preferências ficam dentro da própria pasta (`Sessions\`, `settings.ini`), então ela pode ser copiada para outro PC ou pendrive. Abra com duplo clique em `ReplayFlow.bat`; pela linha de comando, `ReplayFlow.bat process <pasta-da-sessão>`. Para atualizar, rode o script de novo.

### macOS

```bash
./scripts/setup.sh small    # usa Homebrew: python@3.12 ffmpeg tesseract tesseract-lang
```

Na primeira gravação, conceda ao Terminal (ou ao Python) as permissões de **Acessibilidade** e **Monitoramento de Entrada** em Ajustes do Sistema > Privacidade, exigidas pela captura de cliques e teclas.

### Linux

```bash
./scripts/setup.sh small    # apt: ffmpeg tesseract-ocr tesseract-ocr-por | dnf: tesseract-langpack-por
```

A captura de input usa X11. Em Wayland, cliques e teclas de outras janelas não são visíveis ao app; inicie a sessão em "Xorg" para usá-la.

### faster-whisper

Já vem com `pip install -e ".[all]"`. Os modelos ficam no cache do Hugging Face (`~/.cache/huggingface`) e são baixados **uma única vez** com `replayflow download-model <modelo>`, a única ação do app que acessa a rede, e só quando você a roda. Tamanho aproximado: tiny 75 MB, base 145 MB, small 480 MB, medium 1,5 GB, large-v3 3 GB. Em CPU comum, `small` é o melhor equilíbrio para pt-BR.

## Uso

### Interface

```powershell
.venv\Scripts\replayflow.exe        # ou: replayflow, com a venv ativa
```

A janela tem um bloco só, **Registrar uma atividade do seu dia a dia**, com um botão **Próximo: …** que sempre executa a próxima ação:

1. **Nomear a atividade:** escreva com suas palavras o que vai mostrar (ex.: "baixar e arquivar as faturas do mês").
2. **Gravar:** abre o OBS escondido, liga a captura de cliques/teclas e grava. **Pausar/Retomar** aparece durante a gravação; a pausa é descontada de vídeo e eventos.
3. **Processar:** começa sozinho ao parar a gravação. Enquanto processa, todos os comandos ficam bloqueados (só **Ferramentas › Cancelar processamento** continua disponível).
4. **Revisar:** abre o `claude_package.md`.
5. **Enviar ao Claude:** copia o prompt, seleciona o pacote no Explorer e abre o Claude no navegador. O envio é sempre manual.

Menus:

- **Atividade:** nova, abrir uma gravada, **importar vídeo já gravado** (janela própria: nome + arquivo → importa e processa), reproduzir vídeo, abrir pasta.
- **Ferramentas:** processar/retomar, gerar pacote, copiar prompt, cancelar processamento.
- **Configurações:** **Áudio** (microfone da narração e saída de som; o app avisa se o microfone gravar silêncio), **Opções** (fluxo automático, modo privado, máscara de dados e ajustes avançados) e a pasta onde as atividades são salvas.
O que a gravação pelo OBS acrescenta: microfone e som do computador em trilhas separadas (a transcrição usa só a sua voz; sons do sistema viram eventos), sincronia medida entre o início real do vídeo e os cliques, e uma foto por clique (tirada 0,15 s antes, com o ponto marcado) com o rótulo lido sob o cursor e o texto em volta (caixas de diálogo). No Ctrl+V, o texto colado é registrado.

Durante a gravação, aperte **F9** antes de digitar uma senha e de novo depois. Nesse intervalo, só a quantidade de caracteres é registrada.

### Linha de comando

```bash
replayflow new "Automação boletos" --video D:\OBS\2026-10-13.mkv   # cria a sessão e imprime a pasta
replayflow process "C:\Users\ricar\Pipeline-Capture\Sessions\2026-10-13_001_automacao-boletos"
replayflow download-model small
```

## Estrutura de uma sessão

```
2026-10-13_001_automacao-boletos/
├── raw/            gravacao.mkv (trilha 1 = microfone, 2 = som do computador), input_events.jsonl
├── audio/          audio.wav (16 kHz mono)
├── frames/         frame_000001.jpg …, click_000001.jpg … (foto de cada clique)
├── ocr/            frame_000001.txt …
├── transcript/     transcript.json (start, end, text, confidence), transcript.md (MM:SS)
├── export/         claude_package.md, manifest.json, frames_index.csv
├── logs/           pipeline.log (JSON por linha)
├── timeline.jsonl  linha do tempo única: fonte de todos os exports
└── session.json    metadados, config e estado de cada etapa
```

Exemplos reais gerados pelo código estão em [`examples/`](examples): [`session.json`](examples/session.json), [`claude_package.md`](examples/claude_package.md), [`manifest.json`](examples/manifest.json) e [`frames_index.csv`](examples/frames_index.csv).

### Modos de frame

| Modo | Como funciona |
|---|---|
| Por tempo | 1 frame a cada N segundos (`select` do FFmpeg com `prev_selected_t`) |
| Mudança de cena | filtro `scene` do FFmpeg acima do limiar (padrão 0,3) |
| Ambos (padrão) | une os dois e remove frames a menos de 1 s um do outro ou visualmente iguais (dHash). Mudança de cena tem prioridade |

## Privacidade

- **Nada sai da máquina.** Não há APIs externas, telemetria nem upload. O envio ao Claude é manual.
- **O modo privado** intercepta `socket.connect` e a resolução DNS do processo. Qualquer destino fora do loopback gera erro, e o `localhost` continua liberado para o OBS. Os modelos Whisper são carregados com `local_files_only`.
- **Senhas e tokens não são armazenados.** A senha do OBS fica só em memória, e o texto digitado em modo sigiloso (F9) nunca é gravado.
- **Máscara no pacote (padrão ligado):** e-mails, CPF/CNPJ, números de cartão e números de 11 dígitos aparecem como `[e-mail]`, `[CPF]`, `[cartão]`… no `claude_package.md`. O material bruto da sessão não é alterado.
- **Texto colado (Ctrl+V)** é registrado, exceto no modo sigiloso (F9).
- **Atenção:** o OCR lê o que aparece na tela. Nomes, valores e assuntos de e-mail visíveis entram no pacote. Revise o `claude_package.md` (passo 4 do guia) antes de compartilhar.

## Arquitetura

```
replayflow/
├── models/    Pydantic: PipelineConfig (com fingerprint), Session (estado por etapa), TimelineEvent
├── core/      layout de pastas, session_store, timeline_store, pipeline (orquestração), network_guard, logging JSON
├── services/  adaptadores de ferramentas: ffmpeg, transcriber, ocr, input_capture, obs, exporter
├── ui/        PyQt6: janela fina; o trabalho roda em QThread com sinais de progresso e log
└── app/       ponto de entrada (UI sem argumentos; CLI com subcomandos)
```

Decisões centrais:

- **Timeline como fonte única.** Cada etapa grava apenas os eventos da própria origem (`transcript`, `frame`, `ocr`, `mouse`, `key`, `window`). Reexecutar uma etapa substitui seus eventos sem duplicar. Os exports são derivados da timeline.
- **Retomada por fingerprint.** Cada etapa concluída guarda o SHA-256 da config. Ao reprocessar, as etapas concluídas com a mesma config são puladas. Mudou a config, elas rodam de novo.
- **Reprodutível.** Mesma entrada + mesma config gera o mesmo `manifest.json` (coberto por teste).
- **Passos.** O pacote agrupa a sessão em passos: cada frame abre um intervalo com a narração, as ações e o OCR daquele trecho.

## Desenvolvimento

```bash
pip install -e ".[all,dev]"
pytest
```

Os testes cobrem: estrutura de pastas, manifest (incluindo determinismo), `transcript.md`, `frames_index.csv`, `claude_package.md`, regras da captura de input (modo sigiloso, AltGr, atalhos), retomada do pipeline e extração real com FFmpeg (pulada se o FFmpeg estiver ausente).

## Melhorias futuras

- **Indexação vetorial local** (ex.: `sqlite-vec` + embeddings locais) para buscar "onde eu baixei o boleto?" em todas as sessões.
- **Recorte por clique**: salvar um zoom da região ao redor de cada clique, ligando a ação ao elemento da tela.
- **Comparação entre sessões**: alinhar passos de duas execuções do mesmo processo para achar variações e exceções.
- **Geração de especificação RPA**: converter os passos em um esqueleto de robô (ex.: Robot Framework ou PyAutoGUI), com seletores sugeridos a partir do OCR.
- **Replay guiado**: reproduzir a sessão passo a passo como tutorial interativo ou roteiro de QA.
- **Mascaramento automático** de padrões sensíveis no OCR (CPF, cartão, linha digitável) antes do export.
