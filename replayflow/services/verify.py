"""Verificação final: confronta o que foi DITO (transcrição) com o que APARECEU na tela.

Evidência de tela = OCR dos frames, rótulo e texto em volta de cada clique, títulos de janela e texto
digitado/colado. Para cada termo relevante da fala, procura o mesmo termo na tela perto do momento em que
foi dito. O que não aparece em lugar nenhum, mas lembra um termo visto na tela, vira provável erro de
transcrição com sugestão de correção (ex.: "Claserda" → "LACERDA"). Tudo local e determinístico.
"""

from __future__ import annotations

import difflib
import re
import unicodedata
from dataclasses import dataclass, field

from replayflow.models import TimelineEvent
from replayflow.services.transcriber import mmss

WINDOW_S = 15.0          # tolerância entre a fala e a tela (a pessoa fala antes/depois de agir)
SILENT_GAP_S = 20.0      # ações sem narração por mais que isso viram alerta
FUZZY_CUTOFF = 0.75       # a partir daqui, sugere correção
APPLY_CUTOFF = 0.8        # a partir daqui, corrige sozinho (abaixo, só sugere para você decidir)
_WORD_RE = re.compile(r"[A-Za-zÀ-ÿ0-9!]+")
_STOPWORDS = set("""
aqui agora ainda algo algum alguma assim antes apos aquele aquela aquilo bem cada coisa como com contra
depois desse dessa deste desta disso dele dela eles elas entao essa esse esta este estou estamos estava
faco fazer feito fica ficar foram isso isto mais mesmo muito nada nao nessa nesse nesta neste nossa nosso
onde para pela pelo pode porque pois qual quando quem sera seja sobre sempre somente tambem temos tenho
tipo toda todo todos tudo uma umas uns vamos vai vou voce gente certo beleza okay teste testando entao
""".split())


def _norm(word: str) -> str:
    word = unicodedata.normalize("NFKD", word.lower())
    return "".join(c for c in word if not unicodedata.combining(c) and c.isalnum())


def _content_words(text: str) -> list[str]:
    return [w for w in _WORD_RE.findall(text) if len(_norm(w)) >= 4 and _norm(w) not in _STOPWORDS
            and not _norm(w).isdigit()]


def _same(a: str, b: str) -> bool:
    """Mesmo termo, tolerando flexão ("salvamos" ~ "salvar") e acento."""
    return a == b or (len(a) >= 5 and len(b) >= 5 and a[:5] == b[:5])


@dataclass
class Mention:
    t: float
    said: str
    status: str                 # "confirmado", "visto em outro momento", "provável erro", "sem evidência"
    seen_at: float | None = None
    suggestion: str = ""
    where: str = ""             # onde a forma correta apareceu: janela, clique, tela (OCR), digitado/colado
    similarity: float = 0.0
    occurrences: int = 0        # quantas vezes a palavra foi trocada na transcrição

    @property
    def applied(self) -> bool:
        return self.status == "provável erro" and self.similarity >= APPLY_CUTOFF


@dataclass
class Report:
    mentions: list[Mention] = field(default_factory=list)
    silent_gaps: list[tuple[float, float, int]] = field(default_factory=list)  # (início, fim, nº de ações)

    @property
    def checked(self) -> int:
        return len(self.mentions)

    @property
    def confirmed(self) -> int:
        return sum(m.status in ("confirmado", "visto em outro momento") for m in self.mentions)

    @property
    def score(self) -> float | None:
        return self.confirmed / self.checked if self.checked else None

    def by_status(self, status: str) -> list[Mention]:
        return [m for m in self.mentions if m.status == status]


def _evidence(events: list[TimelineEvent]) -> list[tuple[float, str, str, str]]:
    """(tempo, termo normalizado, termo como aparece na tela, onde apareceu)."""
    out: list[tuple[float, str, str, str]] = []

    def add(t: float, text: str, where: str) -> None:
        out.extend((t, _norm(w), w, where) for w in _content_words(text))

    for e in events:  # fontes mais confiáveis primeiro: o rótulo "visto" vem da melhor fonte
        if e.source == "window":
            add(e.t, e.data.get("title", ""), "título da janela")
        elif e.source == "key":
            add(e.t, e.data.get("text", "") + " " + e.data.get("pasted", ""), "texto digitado/colado")
        elif e.source == "frame":
            add(e.t, e.data.get("target", "") + " " + e.data.get("near", ""), "rótulo/área do clique")
    for e in events:
        if e.source == "ocr":
            add(e.t, e.data.get("text", ""), "texto lido da tela")
    return out


def _spoken(events: list[TimelineEvent]) -> list[tuple[float, str]]:
    spoken: list[tuple[float, str]] = []
    for e in events:
        if e.source != "transcript":
            continue
        words = e.data.get("words") or []
        if words:
            spoken += [(w[0], w[2]) for w in words]
        else:
            spoken += [(e.t, w) for w in _WORD_RE.findall(e.data["text"])]
    cleaned = [(t, w.strip(".,;:!?\"'-")) for t, w in spoken]
    return [(t, w) for t, w in cleaned if len(_norm(w)) >= 4 and _norm(w) not in _STOPWORDS and not _norm(w).isdigit()]


def check(events: list[TimelineEvent]) -> Report:
    evidence = _evidence(events)
    vocab: dict[str, str] = {}
    for _, n, raw, _w in evidence:
        vocab.setdefault(n, raw)
    report = Report()
    seen_terms: set[str] = set()
    for t, word in _spoken(events):
        n = _norm(word)
        if not n or n in seen_terms:  # cada termo é avaliado na 1ª vez em que é dito
            continue
        seen_terms.add(n)
        near = [et for et, en, _, _w in evidence if _same(n, en) and abs(et - t) <= WINDOW_S]
        if near:
            report.mentions.append(Mention(t, word, "confirmado", min(near, key=lambda et: abs(et - t))))
            continue
        anywhere = [et for et, en, _, _w in evidence if _same(n, en)]
        if anywhere:
            report.mentions.append(Mention(t, word, "visto em outro momento", min(anywhere, key=lambda et: abs(et - t))))
            continue
        close = difflib.get_close_matches(n, vocab, n=1, cutoff=FUZZY_CUTOFF)
        # Só sugere correção para nomes (maiúscula na fala ou na tela): palavras comuns parecidas
        # ("final" ~ "fina") geram falso alarme.
        if close and len(n) >= 5 and len(close[0]) >= 5 and (word[:1].isupper() or vocab[close[0]][:1].isupper()):
            when, where = next((et, w) for et, en, _, w in evidence if en == close[0])
            ratio = difflib.SequenceMatcher(None, n, close[0]).ratio()
            report.mentions.append(Mention(t, word, "provável erro", when, vocab[close[0]], where, round(ratio, 2)))
        else:
            report.mentions.append(Mention(t, word, "sem evidência"))
    report.silent_gaps = _silent_gaps(events)
    return report


def _silent_gaps(events: list[TimelineEvent]) -> list[tuple[float, float, int]]:
    speech = sorted((e.t, e.data.get("end", e.t)) for e in events if e.source == "transcript")
    actions = sorted(e.t for e in events if e.source in ("mouse", "key"))
    gaps, start, count = [], None, 0

    def spoken_at(t: float) -> bool:
        return any(s - 2 <= t <= end + 2 for s, end in speech)

    last = None
    for t in actions:
        if spoken_at(t):
            if start is not None and last is not None and last - start >= SILENT_GAP_S:
                gaps.append((start, last, count))
            start, count = None, 0
        else:
            start = t if start is None else start
            count += 1
        last = t
    if start is not None and last is not None and last - start >= SILENT_GAP_S:
        gaps.append((start, last, count))
    return gaps


def apply(events: list[TimelineEvent], report: Report) -> list[TimelineEvent]:
    """Aplica as correções de alta confiança na transcrição (cópia; o transcript.json original fica intacto)."""
    fixes = {m.said: m for m in report.mentions if m.applied}
    if not fixes:
        return events
    pattern = re.compile(r"(?<![\wÀ-ÿ])(" + "|".join(re.escape(w) for w in sorted(fixes, key=len, reverse=True))
                         + r")(?![\wÀ-ÿ])")

    def fix(text: str) -> str:
        def sub(m: re.Match[str]) -> str:
            fixes[m.group(1)].occurrences += 1
            return fixes[m.group(1)].suggestion
        return pattern.sub(sub, text)

    out = []
    for e in events:
        if e.source == "transcript":
            words = [[w[0], w[1], pattern.sub(lambda m: fixes[m.group(1)].suggestion, w[2])]
                     for w in e.data.get("words") or []]
            e = e.model_copy(update={"data": {**e.data, "text": fix(e.data["text"]), "words": words,
                                              "original": e.data["text"]}})
        out.append(e)
    return out


def _fix_line(m: Mention) -> str:
    return (f"[{mmss(m.t)}] \"{m.said}\" → \"{m.suggestion}\" — visto em {m.where} às {mmss(m.seen_at or 0)}, "
            f"{m.similarity:.0%} parecido")


def summary_lines(report: Report) -> list[str]:
    """Resumo para o topo do pacote_ia.md."""
    if not report.checked and not report.silent_gaps:
        return ["_Sem narração para verificar: a análise depende só das ações e das telas._"]
    lines = []
    if report.score is not None:
        lines.append(f"- **Concordância fala × tela:** {report.score:.0%} dos {report.checked} termos ditos aparecem na tela.")
    applied = [m for m in report.mentions if m.applied]
    pending = [m for m in report.by_status("provável erro") if not m.applied]
    if applied:
        lines.append("- **Correções já aplicadas na transcrição deste pacote** (a forma da tela substituiu a ouvida):")
        lines += [f"  - {_fix_line(m)}" for m in applied]
    if pending:
        lines.append("- **Possíveis erros não corrigidos** (semelhança baixa; confirme antes de usar):")
        lines += [f"  - {_fix_line(m)}" for m in pending]
    for start, end, n in report.silent_gaps:
        lines.append(f"- **Sem narração** de {mmss(start)} a {mmss(end)} ({n} ações): o porquê dessas ações não foi explicado.")
    return lines


def to_markdown(report: Report, title: str) -> str:
    applied = [m for m in report.mentions if m.applied]
    pending = [m for m in report.by_status("provável erro") if not m.applied]
    out = [f"# Verificação vídeo × transcrição: {title}", "",
           "1. **Apuração:** cada termo dito foi procurado na tela (títulos de janela, rótulos de clique, texto "
           f"digitado/colado e texto lido da tela) até {WINDOW_S:.0f}s antes ou depois.",
           f"2. **Correção:** nomes ouvidos errado e que aparecem na tela com {APPLY_CUTOFF:.0%}+ de semelhança foram "
           "corrigidos na transcrição do pacote. O original continua em `transcript/transcript.md`; a versão "
           "corrigida está em `transcript/transcript_corrigido.md`.",
           "3. **Resumo:** abaixo, cada correção com a evidência, para você conferir se faz sentido.", "",
           f"## Correções aplicadas ({len(applied)})", ""]
    if applied:
        out += ["| Quando | Ouvido | Corrigido para | Evidência na tela | Semelhança | Trocas |", "|---|---|---|---|---|---|"]
        out += [f"| {mmss(m.t)} | {m.said} | {m.suggestion} | {m.where} às {mmss(m.seen_at or 0)} | "
                f"{m.similarity:.0%} | {m.occurrences} |" for m in applied]
    else:
        out.append("_Nenhuma: os nomes ditos já batiam com a tela._")
    out += ["", f"## Possíveis erros NÃO corrigidos ({len(pending)})", ""]
    out += [f"- {_fix_line(m)}" for m in pending] or ["_Nenhum._"]
    out += ["", "## Pontos de atenção", ""]
    out += [f"- Sem narração de {mmss(s)} a {mmss(e)} ({n} ações)." for s, e, n in report.silent_gaps] or ["_Nenhum._"]
    if report.score is not None:
        out.append(f"- Concordância fala × tela: {report.score:.0%} dos {report.checked} termos ditos.")
    sections = (("confirmado", "Confirmados na tela no mesmo momento"),
                ("visto em outro momento", "Vistos na tela, mas em outro momento"),
                ("sem evidência", "Sem evidência na tela (intenção, regras ou coisas fora da tela)"))
    out += ["", "## Apuração completa", ""]
    for status, heading in sections:
        items = report.by_status(status)
        out += [f"### {heading} ({len(items)})", ""]
        out += [f"- [{mmss(m.t)}] {m.said}" + (f" · tela em {mmss(m.seen_at)}" if m.seen_at is not None else "")
                for m in items]
        out.append("")
    return "\n".join(out)