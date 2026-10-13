"""Gera os artefatos de export a partir da timeline: frames_index.csv, manifest.json e claude_package.md."""

from __future__ import annotations

import csv
import difflib
import json
import platform
from dataclasses import dataclass, field
from pathlib import Path

import replayflow
from replayflow.core import timeline_store
from replayflow.core.layout import SessionLayout
from replayflow.models import Session, Stage, StageStatus, TimelineEvent
from replayflow.services.transcriber import mmss

OCR_EXCERPT_CHARS = 1500
ACTION_SOURCES = ("mouse", "key", "window", "audio")
_BUTTONS = {"left": "esquerdo", "right": "direito", "middle": "do meio"}

ANALYSIS_REQUESTS = (
    "Descrição passo a passo do fluxo observado.",
    "Regras de negócio implícitas.",
    "Ações realizadas pelo usuário.",
    "Condições, exceções e pontos de decisão.",
    "Dados de entrada e saída.",
    "Candidatos a automação.",
    "Riscos, validações e pontos de atenção.",
    "Pseudo-código ou especificação de RPA.",
    "Perguntas que precisam ser esclarecidas antes de automatizar.",
)


@dataclass
class Step:
    """Intervalo da sessão que começa em um frame e vai até o próximo."""

    index: int
    start: float
    end: float
    frame: TimelineEvent
    ocr: TimelineEvent | None = None
    speech: list[TimelineEvent] = field(default_factory=list)
    actions: list[TimelineEvent] = field(default_factory=list)
    narration: str = ""  # só as palavras ditas dentro deste passo

    @property
    def relevant(self) -> bool:
        return bool(self.frame.data.get("scene_change") or self.actions or self.narration)

    @property
    def target(self) -> str:
        """Rótulo em que o usuário clicou, lido na foto do clique."""
        return self.frame.data.get("target", "") if self.frame.kind == "click" else ""


def describe_action(e: TimelineEvent, target: str = "") -> str:
    match e.source, e.kind:
        case "mouse", "click":
            button = _BUTTONS.get(e.data.get("button", ""), e.data.get("button", ""))
            where = f"em \"{target}\" " if target else ""
            kind = "duplo clique" if e.data.get("double") else "clique"
            return f"{kind} {button} {where}(posição {e.data.get('x')}, {e.data.get('y')})"
        case "key", "text":
            return f"digitou \"{e.data['text']}\""
        case "key", "secret":
            return f"digitou [SEGREDO: {e.data['chars']} caracteres]"
        case "key", "hotkey":
            return f"atalho {e.data['keys']}"
        case "key", "special":
            return f"tecla {e.data['key']}"
        case "key", "secret_mode":
            return "modo sigiloso " + ("ligado" if e.data.get("on") else "desligado")
        case "audio", "system_sound":
            return f"som do computador ({e.data['end'] - e.t:.1f}s)"
        case "window", "focus":
            return f"janela ativa: {e.data['title']}"
    return f"{e.source}:{e.kind}"


def _frame_note(frame: TimelineEvent) -> str:
    if frame.kind == "click":
        return " (foto do clique, ponto marcado em vermelho)"
    return " (mudança de tela)" if frame.data.get("scene_change") else ""


def _action_order(e: TimelineEvent) -> tuple[float, int]:
    return e.t, 0 if e.source == "window" else 1  # a janela ativa vem antes do clique que a revelou


def build_steps(events: list[TimelineEvent], duration: float) -> list[Step]:
    frames = [e for e in events if e.source == "frame"]
    ocr_by_frame = {e.data["frame"]: e for e in events if e.source == "ocr"}
    transcript = [e for e in events if e.source == "transcript"]
    words = [(w[0], w[2]) for e in transcript for w in e.data.get("words") or []]
    steps = []
    for i, frame in enumerate(frames):
        start = 0.0 if i == 0 else frame.t  # o 1º passo cobre o que ocorre antes do 1º frame
        end = frames[i + 1].t if i + 1 < len(frames) else max(duration, frame.t)
        last = i + 1 == len(frames)

        def inside(t: float) -> bool:
            return start <= t < end or (last and t >= start)

        speech = [e for e in transcript if inside(e.t)]
        # Com tempo por palavra, cada frase cai no passo em que foi dita; sem isso, o trecho inteiro vai
        # para o passo em que começou.
        narration = (" ".join(w for t, w in words if inside(t)) if words
                     else " ".join(e.data["text"] for e in speech))
        steps.append(Step(
            index=i + 1, start=frame.t, end=end, frame=frame, ocr=ocr_by_frame.get(frame.data["file"]),
            speech=speech, narration=narration.strip(),
            actions=sorted((e for e in events if e.source in ACTION_SOURCES and inside(e.t)), key=_action_order),
        ))
    return steps


def _describe(step: Step, a: TimelineEvent) -> str:
    return describe_action(a, step.target if a.source == "mouse" and abs(a.t - step.frame.t) < 0.01 else "")


def _same_screen(a: str, b: str) -> bool:
    return bool(a) and bool(b) and difflib.SequenceMatcher(None, a, b).ratio() > 0.8


def _script_lines(steps: list[Step]) -> list[str]:
    """Roteiro em uma linha por passo: o que o Claude lê primeiro para entender a sequência."""
    lines, window, shown = [], "", ""
    for s in (s for s in steps if s.relevant):
        parts = []
        for a in s.actions:
            if a.source == "window":
                window = a.data["title"]
            else:
                parts.append(_describe(s, a))
        what = "; ".join(parts) or ("mudança de tela" if s.frame.data.get("scene_change") else "observação")
        said = f" — fala: \"{s.narration}\"" if s.narration else ""
        win = f" [janela: {window}]" if window and window != shown else ""  # só quando muda
        shown = window
        lines.append(f"{s.index}. **[{mmss(s.start)}]**{win} {what}{said}")
    return lines


def write_frames_index(steps: list[Step], path: Path) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["frame_file", "timestamp_seconds", "timestamp_human", "ocr_file", "ocr_chars", "scene_change", "notes"])
        for s in steps:
            w.writerow([
                s.frame.data["file"], f"{s.frame.t:.3f}", mmss(s.frame.t),
                s.ocr.data["file"] if s.ocr else "", s.ocr.data["chars"] if s.ocr else 0,
                str(bool(s.frame.data.get("scene_change"))).lower(),
                "; ".join(_describe(s, a) for a in s.actions),
            ])


def build_manifest(session: Session, layout: SessionLayout, steps: list[Step], versions: dict[str, str]) -> dict:
    files = sorted(
        str(p.relative_to(layout.root)).replace("\\", "/")
        for p in layout.root.rglob("*")
        if p.is_file() and p.parent != layout.logs and p.name != "manifest.json"
    )
    return {
        "app": "ReplayFlow",
        "app_version": replayflow.__version__,
        "session": {"id": session.id, "name": session.name, "created_at": session.created_at.isoformat()},
        "video": {"file": session.video_file, "sha256": session.video_sha256, "duration_s": session.duration_s},
        "pipeline": {
            "config": session.config.model_dump(mode="json"),
            "config_fingerprint": session.config.fingerprint(),
            "whisper_model": session.config.whisper_model if session.config.transcription_enabled else None,
            "stages": {k.value: v.status.value for k, v in session.stages.items() if k != Stage.EXPORT},
        },
        "counts": {"frames": len(steps), "relevant_steps": sum(s.relevant for s in steps)},
        "tools": {"python": platform.python_version(), **versions},
        "files": files,
    }


def _transcript_lines(events: list[TimelineEvent]) -> list[str]:
    return [f"- **[{mmss(e.t)}]** {e.data['text']}" for e in events if e.source == "transcript"]


def build_package(session: Session, steps: list[Step], events: list[TimelineEvent]) -> str:
    cfg = session.config
    done = ", ".join(f"{k.value}={v.status.value}" for k, v in session.stages.items() if v.status != StageStatus.PENDING)
    out = [
        f"# Sessão: {session.name}",
        "",
        f"- **ID:** {session.id}",
        f"- **Data/hora:** {session.created_at:%d/%m/%Y %H:%M}",
        f"- **Duração do vídeo:** {mmss(session.duration_s or 0)}",
        f"- **Passos (frames):** {len(steps)}, dos quais {sum(s.relevant for s in steps)} relevantes",
        "",
        "## Pipeline executado",
        "",
        f"- Frames: modo `{cfg.frame_mode}`, intervalo {cfg.frame_interval_s}s, cena > {cfg.scene_threshold}, "
        f"largura máx. {cfg.max_width}px",
        f"- Transcrição: {'faster-whisper ' + cfg.whisper_model + ' (' + cfg.language + ')' if cfg.transcription_enabled else 'desativada'}",
        f"- OCR: {'Tesseract (' + cfg.ocr_lang + ')' if cfg.ocr_enabled else 'desativado'}",
        f"- Etapas: {done}",
        "- Processado 100% localmente. Trechos digitados em modo sigiloso aparecem só como `[SEGREDO: n caracteres]`.",
        "",
        "## Instruções para análise",
        "",
        "Você está analisando a gravação de um fluxo de trabalho real. Use a narração, as ações registradas "
        "(cliques, texto digitado, janelas) e o OCR das telas. Produza:",
        "",
        *[f"{i}. {req}" for i, req in enumerate(ANALYSIS_REQUESTS, start=1)],
        "",
        "Cite o passo e o timestamp (MM:SS) que embasam cada conclusão. Sinalize o que for inferido sem evidência direta.",
        "",
        "## Como ler este pacote",
        "",
        "Tudo está no mesmo relógio do vídeo (MM:SS desde o início da gravação), e é o tempo que liga as fontes:",
        "",
        "- **Passo** = intervalo entre duas imagens da tela. Cada clique gera uma foto própria (tirada um instante "
        "antes do clique, com o ponto marcado em vermelho) e abre um novo passo.",
        "- **Ações** = o que o usuário fez naquele intervalo: janela ativa, clique (com o rótulo lido sob o cursor), "
        "texto digitado, atalhos e sons do computador. Capturadas do teclado e do mouse, não inferidas.",
        "- **Narração** = só as palavras ditas dentro daquele intervalo (a transcrição tem o tempo de cada palavra). "
        "É a intenção do usuário: use-a para explicar o porquê de cada ação.",
        "- **OCR** = texto lido automaticamente da imagem da tela. Pode ter erros de leitura (letras trocadas, "
        "palavras coladas): corrija pelo contexto e prefira a narração e o rótulo do clique quando divergirem.",
        "- Para conferir algo, cruze pelo tempo: a fala em 00:19 explica o clique em 00:19, que aparece na foto "
        "do clique e no OCR daquele passo. Se as fotos forem anexadas, o nome do arquivo está no título do passo.",
        "",
        "## Roteiro passo a passo",
        "",
        "Uma linha por passo, em ordem: janela ativa, o que foi feito (clique com o rótulo lido na tela, texto "
        "digitado, atalhos) e o que foi dito naquele momento. Os detalhes de cada passo vêm mais abaixo.",
        "",
        *(_script_lines(steps) or ["_Nenhuma ação registrada._"]),
        "",
        "## Transcrição completa",
        "",
        *(_transcript_lines(events) or ["_Sem transcrição._"]),
        "",
        "## Passos relevantes",
        "",
        "Passos sem ação, fala ou mudança de tela ficam só no índice do final (por isso a numeração pula). "
        "O OCR é o texto lido da tela inteira; quando a tela é a mesma do passo anterior, ele não é repetido.",
        "",
    ]
    last_ocr, last_ocr_step = "", 0
    for s in (s for s in steps if s.relevant):
        title = f"Clique em \"{s.target}\"" if s.target else ("Clique" if s.frame.kind == "click" else "Tela")
        out += [f"### Passo {s.index} [{mmss(s.start)} - {mmss(s.end)}] {title} · `{s.frame.data['file']}`"
                + _frame_note(s.frame), ""]
        if s.narration:
            out += [f"**Narração:** {s.narration}", ""]
        if s.actions:
            out += ["**Ações:**", *[f"- [{mmss(a.t)}] {_describe(s, a)}" for a in s.actions], ""]
        if s.ocr and s.ocr.data["text"]:
            text = s.ocr.data["text"]
            if _same_screen(text, last_ocr):
                out += [f"_Tela igual à do passo {last_ocr_step} (OCR omitido)._", ""]
            else:
                cut = text[:OCR_EXCERPT_CHARS] + (" [...]" if len(text) > OCR_EXCERPT_CHARS else "")
                out += ["**OCR da tela:**", "", "```text", cut, "```", ""]
                last_ocr, last_ocr_step = text, s.index
    out += ["## Índice completo de frames", "", "| Passo | Frame | Tempo | Cena | Ações |", "|---|---|---|---|---|"]
    out += [f"| {s.index} | {s.frame.data['file']} | {mmss(s.start)} | {'sim' if s.frame.data.get('scene_change') else ''} "
            f"| {len(s.actions)} |" for s in steps]
    return "\n".join(out) + "\n"


def build_prompt(session: Session) -> str:
    """Texto curto para colar no Claude junto com o claude_package.md anexado."""
    items = "\n".join(f"{i}. {r}" for i, r in enumerate(ANALYSIS_REQUESTS, start=1))
    return (
        f"Anexei o arquivo claude_package.md da sessão \"{session.name}\" ({mmss(session.duration_s or 0)}), "
        "gerado localmente a partir da gravação de tela, narração, OCR e ações registradas.\n"
        f"Com base nele, produza:\n{items}\n"
        "Cite passo e timestamp para cada conclusão e liste o que precisa ser confirmado comigo."
    )


def export(session: Session, versions: dict[str, str]) -> list[Path]:
    layout = SessionLayout(session.root)
    events = timeline_store.read(layout.timeline)
    steps = build_steps(events, session.duration_s or 0)
    index = layout.export / "frames_index.csv"
    package = layout.export / "claude_package.md"
    manifest = layout.export / "manifest.json"
    write_frames_index(steps, index)
    package.write_text(build_package(session, steps, events), encoding="utf-8")
    manifest.write_text(json.dumps(build_manifest(session, layout, steps, versions), ensure_ascii=False, indent=2),
                        encoding="utf-8")
    return [package, manifest, index]
