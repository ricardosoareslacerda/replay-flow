from __future__ import annotations

import hashlib
import re
import shutil
import unicodedata
from datetime import datetime
from pathlib import Path

from replayflow.core.errors import ReplayFlowError
from replayflow.core.layout import SessionLayout
from replayflow.models import PipelineConfig, Session, Stage, StageStatus

_SEQ_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})_(\d{3})_")


def slugify(text: str) -> str:
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_text.lower()).strip("-")
    return slug or "sessao"


def _next_seq(base_dir: Path, day: str) -> int:
    seqs = [
        int(m.group(2))
        for p in base_dir.iterdir()
        if p.is_dir() and (m := _SEQ_RE.match(p.name)) and m.group(1) == day
    ] if base_dir.exists() else []
    return max(seqs, default=0) + 1


def create_session(
    base_dir: Path, name: str, config: PipelineConfig, now: datetime | None = None
) -> Session:
    now = now or datetime.now().astimezone()
    day = now.strftime("%Y-%m-%d")
    session_id = f"{day}_{_next_seq(base_dir, day):03d}_{slugify(name)}"
    layout = SessionLayout(base_dir / session_id)
    layout.create()
    session = Session(id=session_id, name=name, created_at=now, root=layout.root, config=config)
    save(session)
    return session


def save(session: Session) -> None:
    path = SessionLayout(session.root).session_json
    tmp = path.with_suffix(".tmp")
    tmp.write_text(session.model_dump_json(indent=2), encoding="utf-8")
    tmp.replace(path)  # escrita atômica: retomada nunca lê JSON pela metade


def load(root: Path) -> Session:
    path = SessionLayout(root).session_json
    if not path.is_file():
        raise ReplayFlowError(f"{root} não é uma sessão do ReplayFlow (session.json ausente).")
    session = Session.model_validate_json(path.read_text(encoding="utf-8"))
    return session.model_copy(update={"root": root})  # pasta pode ter sido movida


def sha256_file(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while block := f.read(chunk):
            h.update(block)
    return h.hexdigest()


def import_video(session: Session, video: Path) -> Session:
    """Copia o vídeo para raw/ preservando a extensão e registra o hash."""
    target = SessionLayout(session.root).raw / f"gravacao{video.suffix.lower()}"
    shutil.copy2(video, target)
    updated = session.model_copy(update={"video_file": target.name, "video_sha256": sha256_file(target)})
    save(updated)
    return updated


def mark_stage(session: Session, stage: Stage, status: StageStatus, error: str | None = None) -> Session:
    stages = dict(session.stages)
    stages[stage] = stages[stage].model_copy(
        update={
            "status": status,
            "finished_at": datetime.now().astimezone(),
            "config_fingerprint": session.config.fingerprint(),
            "error": error,
        }
    )
    updated = session.model_copy(update={"stages": stages})
    save(updated)
    return updated
