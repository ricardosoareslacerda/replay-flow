"""Gera os artefatos de export a partir da timeline: frames_index.csv, manifest.json e claude_package.md."""

from __future__ import annotations

import csv
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

    @property
    def relevant(self) -> bool:
        return bool(self.frame.data.get("scene_change") or self.actions or self.speech)


def describe_action(e: TimelineEvent) -> str:
    match e.source, e.kind:
        case "mouse", "click":
            return f"clique {e.data.get('button', '')} em ({e.data.get('x')}, {e.data.get('y')})"
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
        case "window", "focus":
            return f"janela ativa: {e.data['title']}"
    return f"{e.source}:{e.kind}"


def build_steps(events: list[TimelineEvent], duration: float) -> list[Step]:
    frames = [e for e in events if e.source == "frame"]
    ocr_by_frame = {e.data["frame"]: e for e in events if e.source == "ocr"}
    steps = []
    for i, frame in enumerate(frames):
        start = 0.0 if i == 0 else frame.t  # o 1º passo cobre o que ocorre antes do 1º frame
        end = frames[i + 1].t if i + 1 < len(frames) else max(duration, frame.t)
        last = i + 1 == len(frames)

        def inside(t: float) -> bool:
            return start <= t < end or (last and t >= start)

        steps.append(Step(
            index=i + 1, start=frame.t, end=end, frame=frame, ocr=ocr_by_frame.get(frame.data["file"]),
            speech=[e for e in events if e.source == "transcript" and inside(e.t)],
            actions=[e for e in events if e.source in ("mouse", "key", "window") and inside(e.t)],
        ))
    return steps


def write_frames_index(steps: list[Step], path: Path) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["frame_file", "timestamp_seconds", "timestamp_human", "ocr_file", "ocr_chars", "scene_change", "notes"])
        for s in steps:
            w.writerow([
                s.frame.data["file"], f"{s.frame.t:.3f}", mmss(s.frame.t),
                s.ocr.data["file"] if s.ocr else "", s.ocr.data["chars"] if s.ocr else 0,
                str(bool(s.frame.data.get("scene_change"))).lower(),
                "; ".join(describe_action(a) for a in s.actions),
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
        "## Transcrição completa",
        "",
        *(_transcript_lines(events) or ["_Sem transcrição._"]),
        "",
        "## Passos relevantes",
        "",
    ]
    for s in (s for s in steps if s.relevant):
        out += [f"### Passo {s.index} [{mmss(s.start)} - {mmss(s.end)}] `{s.frame.data['file']}`"
                + (" (mudança de tela)" if s.frame.data.get("scene_change") else ""), ""]
        if s.speech:
            out += ["**Narração:** " + " ".join(e.data["text"] for e in s.speech), ""]
        if s.actions:
            out += ["**Ações:**", *[f"- [{mmss(a.t)}] {describe_action(a)}" for a in s.actions], ""]
        if s.ocr and s.ocr.data["text"]:
            text = s.ocr.data["text"]
            cut = text[:OCR_EXCERPT_CHARS] + (" [...]" if len(text) > OCR_EXCERPT_CHARS else "")
            out += ["**OCR da tela:**", "", "```text", cut, "```", ""]
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
