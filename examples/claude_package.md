# Sessão: Automação boletos

- **ID:** 2026-10-13_001_automacao-boletos
- **Data/hora:** 13/10/2026 09:30
- **Duração do vídeo:** 01:14
- **Passos (frames):** 6, dos quais 6 relevantes

## Pipeline executado

- Frames: modo `both`, intervalo 5.0s, cena > 0.3, largura máx. 1280px
- Transcrição: faster-whisper small (pt)
- OCR: Tesseract (por)
- Etapas: audio=done, transcript=done, frames=done, ocr=done, input=done
- Processado 100% localmente. Trechos digitados em modo sigiloso aparecem só como `[SEGREDO: n caracteres]`.

## Instruções para análise

Você está analisando a gravação de um fluxo de trabalho real. Use a narração, as ações registradas (cliques, texto digitado, janelas) e o OCR das telas. Produza:

1. Descrição passo a passo do fluxo observado.
2. Regras de negócio implícitas.
3. Ações realizadas pelo usuário.
4. Condições, exceções e pontos de decisão.
5. Dados de entrada e saída.
6. Candidatos a automação.
7. Riscos, validações e pontos de atenção.
8. Pseudo-código ou especificação de RPA.
9. Perguntas que precisam ser esclarecidas antes de automatizar.

Cite o passo e o timestamp (MM:SS) que embasam cada conclusão. Sinalize o que for inferido sem evidência direta.

## Transcrição completa

- **[00:01]** Primeiro eu abro o e-mail do banco com a fatura do mês.
- **[00:08]** Baixo o PDF anexo, ele sempre vem protegido por senha.
- **[00:21]** A senha são os cinco primeiros dígitos do CPF do titular.
- **[00:30]** Aqui eu confiro o vencimento e o valor total, e copio a linha digitável.
- **[00:50]** Por fim salvo com o nome no padrão banco, mês e valor, na pasta do mês.

## Passos relevantes

### Passo 1 [00:00 - 00:08] `frame_000001.jpg`

**Narração:** Primeiro eu abro o e-mail do banco com a fatura do mês.

**Ações:**
- [00:03] janela ativa: Gmail - Google Chrome
- [00:04] clique esquerdo em (812, 344)

**OCR da tela:**

```text
Gmail - Caixa de entrada
Banco XPTO - Fatura de outubro disponível
```

### Passo 2 [00:08 - 00:21] `frame_000002.jpg` (mudança de tela)

**Narração:** Baixo o PDF anexo, ele sempre vem protegido por senha.

**Ações:**
- [00:10] clique esquerdo em (1104, 612)

**OCR da tela:**

```text
fatura_102026.pdf (PDF) 182 KB
Baixar
```

### Passo 3 [00:21 - 00:30] `frame_000003.jpg` (mudança de tela)

**Narração:** A senha são os cinco primeiros dígitos do CPF do titular.

**Ações:**
- [00:22] janela ativa: Adobe Acrobat Reader
- [00:23] modo sigiloso ligado
- [00:23] digitou [SEGREDO: 5 caracteres]
- [00:25] tecla enter
- [00:25] modo sigiloso desligado

**OCR da tela:**

```text
Este documento está protegido por senha
Senha: [__________]  OK
```

### Passo 4 [00:30 - 00:35] `frame_000004.jpg` (mudança de tela)

**Narração:** Aqui eu confiro o vencimento e o valor total, e copio a linha digitável.

**OCR da tela:**

```text
FATURA OUTUBRO/2026
Vencimento: 20/10/2026
Total a pagar: R$ 1.284,37
Linha digitável: 34191.79001 01043.510047 91020.150008 1 97850000128437
```

### Passo 5 [00:35 - 00:50] `frame_000005.jpg`

**Ações:**
- [00:36] atalho ctrl+c

### Passo 6 [00:50 - 01:14] `frame_000006.jpg` (mudança de tela)

**Narração:** Por fim salvo com o nome no padrão banco, mês e valor, na pasta do mês.

**Ações:**
- [00:52] atalho ctrl+shift+s
- [00:54] digitou "xpto_2026-10_1284,37.pdf"
- [00:58] tecla enter

**OCR da tela:**

```text
Salvar como
Nome: fatura_102026.pdf
Pasta: F:\Financeiro\Boletos\2026-10
```

## Índice completo de frames

| Passo | Frame | Tempo | Cena | Ações |
|---|---|---|---|---|
| 1 | frame_000001.jpg | 00:00 |  | 2 |
| 2 | frame_000002.jpg | 00:08 | sim | 1 |
| 3 | frame_000003.jpg | 00:21 | sim | 5 |
| 4 | frame_000004.jpg | 00:30 | sim | 0 |
| 5 | frame_000005.jpg | 00:35 |  | 1 |
| 6 | frame_000006.jpg | 00:50 | sim | 3 |
