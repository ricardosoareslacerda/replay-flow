from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from replayflow.models import TimelineEvent
from replayflow.models.timeline import EventSource


def write_source(path: Path, source: EventSource, events: Iterable[TimelineEvent]) -> None:
    """Substitui os eventos de uma origem. Reexecutar uma etapa é idempotente."""
    kept = [e for e in read(path) if e.source != source]
    merged = sorted([*kept, *events], key=lambda e: (e.t, e.source, e.kind))
    tmp = path.with_suffix(".tmp")
    tmp.write_text("".join(e.model_dump_json() + "\n" for e in merged), encoding="utf-8")
    tmp.replace(path)


def read(path: Path) -> list[TimelineEvent]:
    if not path.exists():
        return []
    return [
        TimelineEvent.model_validate_json(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
