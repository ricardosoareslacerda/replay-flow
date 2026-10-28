"""Minhas atividades: todas as gravações da pasta, com busca e ações (abrir, assistir, pasta, excluir)."""

from __future__ import annotations

import shutil
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from PyQt6.QtCore import Qt, QUrl
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import (
    QAbstractItemView, QDialog, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMessageBox, QPushButton, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget,
)

from replayflow.core import session_store
from replayflow.core.errors import ReplayFlowError
from replayflow.models import Session, StageStatus


@dataclass
class Entry:
    session: Session
    size_mb: float

    @property
    def status(self) -> str:
        s = self.session
        if not s.video_file:
            return "Sem vídeo"
        if any(st.status == StageStatus.FAILED for st in s.stages.values()):
            return "Falhou: retomar"
        if all(st.status in (StageStatus.DONE, StageStatus.SKIPPED) for st in s.stages.values()):
            return "Pronta"
        return "Falta processar"

    @property
    def video(self) -> Path | None:
        root = self.session.root
        final = next(iter((root / "export").glob("video.*")), None)
        raw = root / "raw" / self.session.video_file if self.session.video_file else None
        return final or (raw if raw and raw.exists() else None)


def scan(base: Path) -> list[Entry]:
    """Atividades da pasta, mais recentes primeiro. Pastas sem session.json válido são ignoradas."""
    entries = []
    if not base.is_dir():
        return entries
    for d in base.iterdir():
        if not (d / "session.json").is_file():
            continue
        try:
            s = session_store.load(d)
        except (ReplayFlowError, ValueError, OSError):
            continue
        size = sum(f.stat().st_size for f in d.rglob("*") if f.is_file())
        entries.append(Entry(s, size / 1_048_576))
    return sorted(entries, key=lambda e: e.session.created_at, reverse=True)


def _mmss(seconds: float | None) -> str:
    t = int(seconds or 0)
    return f"{t // 60:02d}:{t % 60:02d}" if seconds else "—"


class LibraryDialog(QDialog):
    COLUMNS = ["Atividade", "Data", "Duração", "Situação", "Tamanho"]

    def __init__(self, base: Path, on_open: Callable[[Path], None], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Minhas atividades")
        self.resize(920, 560)
        self.base, self.on_open = base, on_open
        self.entries: list[Entry] = []

        lay = QVBoxLayout(self)
        top = QHBoxLayout()
        self.search = QLineEdit(placeholderText="Buscar pelo nome da atividade…")
        self.search.setAccessibleName("Buscar atividade")
        self.search.textChanged.connect(self._fill)
        self.count = QLabel()
        self.count.setObjectName("Muted")
        top.addWidget(self.search, stretch=1)
        top.addWidget(self.count)
        lay.addLayout(top)

        self.table = QTableWidget(0, len(self.COLUMNS))
        self.table.setHorizontalHeaderLabels(self.COLUMNS)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for i in range(1, len(self.COLUMNS)):
            self.table.horizontalHeader().setSectionResizeMode(i, QHeaderView.ResizeMode.ResizeToContents)
        self.table.doubleClicked.connect(lambda _: self._open())
        self.table.itemSelectionChanged.connect(self._sync_buttons)
        self.table.setStyleSheet("QTableWidget{background:#0C1013;border:1px solid #252C34;border-radius:8px;"
                                 "gridline-color:#20262D;} QHeaderView::section{background:#171C22;color:#8A96A3;"
                                 "border:0;padding:6px;} QTableWidget::item:selected{background:#2A3640;}")
        lay.addWidget(self.table, stretch=1)

        actions = QHBoxLayout()
        self.btn_open = QPushButton("Abrir")
        self.btn_open.setObjectName("Send")
        self.btn_open.clicked.connect(self._open)
        self.btn_watch = QPushButton("Assistir")
        self.btn_watch.clicked.connect(self._watch)
        self.btn_folder = QPushButton("Abrir pasta")
        self.btn_folder.clicked.connect(self._folder)
        self.btn_delete = QPushButton("Excluir…")
        self.btn_delete.setObjectName("Danger")
        self.btn_delete.clicked.connect(self._delete)
        close = QPushButton("Fechar")
        close.clicked.connect(self.reject)
        for b in (self.btn_open, self.btn_watch, self.btn_folder, self.btn_delete):
            actions.addWidget(b)
        actions.addStretch(1)
        actions.addWidget(close)
        lay.addLayout(actions)
        self.reload()

    def reload(self) -> None:
        self.entries = scan(self.base)
        self._fill()

    def _visible(self) -> list[Entry]:
        q = self.search.text().strip().lower()
        return [e for e in self.entries if q in e.session.name.lower()]

    def _fill(self) -> None:
        rows = self._visible()
        self.table.setRowCount(len(rows))
        for r, e in enumerate(rows):
            s = e.session
            values = [s.name, f"{s.created_at:%d/%m/%Y %H:%M}", _mmss(s.duration_s), e.status, f"{e.size_mb:,.0f} MB"]
            for c, v in enumerate(values):
                item = QTableWidgetItem(v)
                if c == 0:
                    item.setData(Qt.ItemDataRole.UserRole, str(s.root))
                if c in (2, 4):
                    item.setTextAlignment(int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter))
                self.table.setItem(r, c, item)
        total = sum(e.size_mb for e in self.entries)
        self.count.setText(f"{len(rows)} de {len(self.entries)} atividades · {total / 1024:,.1f} GB")
        if rows:
            self.table.selectRow(0)
        self._sync_buttons()

    def _current(self) -> Entry | None:
        row = self.table.currentRow()
        rows = self._visible()
        return rows[row] if 0 <= row < len(rows) else None

    def _sync_buttons(self) -> None:
        e = self._current()
        for b in (self.btn_open, self.btn_folder, self.btn_delete):
            b.setEnabled(e is not None)
        self.btn_watch.setEnabled(bool(e and e.video))

    def _open(self) -> None:
        e = self._current()
        if e:
            self.accept()
            self.on_open(e.session.root)

    def _watch(self) -> None:
        e = self._current()
        if e and e.video:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(e.video)))

    def _folder(self) -> None:
        e = self._current()
        if e:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(e.session.root)))

    def _delete(self) -> None:
        e = self._current()
        if not e:
            return
        answer = QMessageBox.question(self, "Excluir atividade",
                                      f"Apagar \"{e.session.name}\" ({e.size_mb:,.0f} MB) com vídeo, fotos e pacote? "
                                      "Não dá para desfazer.")
        if answer == QMessageBox.StandardButton.Yes:
            shutil.rmtree(e.session.root, ignore_errors=True)
            self.reload()
