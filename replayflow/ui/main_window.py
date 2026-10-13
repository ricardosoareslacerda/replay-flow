"""Janela principal (PyQt6). Só orquestra: toda a regra está em core/ e services/."""

from __future__ import annotations

import logging
import threading
from pathlib import Path

from PyQt6.QtCore import QObject, QSettings, QThread, QUrl, pyqtSignal
from PyQt6.QtGui import QDesktopServices, QGuiApplication
from PyQt6.QtWidgets import (
    QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QFormLayout, QGroupBox, QHBoxLayout, QInputDialog,
    QLabel, QLineEdit, QMainWindow, QMessageBox, QPlainTextEdit, QProgressBar, QPushButton, QSpinBox,
    QVBoxLayout, QWidget,
)

from replayflow.core import network_guard, session_store
from replayflow.core.errors import CancelledError, ReplayFlowError
from replayflow.core.pipeline import INPUT_EVENTS_FILE, Pipeline
from replayflow.models import AppConfig, FrameMode, PipelineConfig, Session, Stage, StageStatus
from replayflow.services import exporter
from replayflow.services.input_capture import InputCapture

LANGUAGES = {"pt-BR": "pt", "en": "en", "es": "es"}
MODELS = ["tiny", "base", "small", "medium", "large-v3"]
FRAME_MODES = {"Ambos": FrameMode.BOTH, "Por tempo": FrameMode.INTERVAL, "Mudança de cena": FrameMode.SCENE}
STATUS_ICON = {None: "⏳", StageStatus.DONE: "✅", StageStatus.SKIPPED: "➖", StageStatus.FAILED: "❌",
               StageStatus.PENDING: "○"}
WARNING = ("⚠ Gravações podem conter dados sensíveis (valores, CPFs, documentos). Tudo fica nesta máquina; "
           "use F9 para o modo sigiloso ao digitar senhas e revise o pacote antes de enviá-lo a qualquer IA.")


class LogBridge(QObject, logging.Handler):
    line = pyqtSignal(str)

    def __init__(self) -> None:
        QObject.__init__(self)
        logging.Handler.__init__(self)
        self.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%H:%M:%S"))

    def emit(self, record: logging.LogRecord) -> None:
        self.line.emit(self.format(record))


class Worker(QObject):
    progress = pyqtSignal(object, object)
    finished = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, session: Session, private_mode: bool, stages: tuple[Stage, ...]) -> None:
        super().__init__()
        self.cancel = threading.Event()
        self._pipeline = Pipeline(session, private_mode, self.cancel, lambda s, st: self.progress.emit(s, st))
        self._stages = stages

    def run(self) -> None:
        try:
            self.finished.emit(self._pipeline.run(self._stages))
        except (ReplayFlowError, CancelledError) as e:
            self.failed.emit(str(e))
        except Exception as e:  # erro inesperado não pode matar a UI
            logging.getLogger("replayflow").exception("erro inesperado")
            self.failed.emit(f"Erro inesperado: {e}")


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("ReplayFlow")
        self.settings = QSettings("ReplayFlow", "ReplayFlow")
        self.session: Session | None = None
        self.thread: QThread | None = None
        self.worker: Worker | None = None
        self.capture: InputCapture | None = None
        self.obs = None
        self._build()
        self._wire_logging()
        self._apply_private_mode()
        self._refresh()

    # ---------- layout ----------

    def _build(self) -> None:
        defaults = PipelineConfig()
        root = QVBoxLayout()
        warning = QLabel(WARNING)
        warning.setWordWrap(True)
        warning.setStyleSheet("background:#fff3cd;color:#664d03;padding:6px;border-radius:4px;")
        root.addWidget(warning)

        sess = QGroupBox("Sessão")
        form = QFormLayout(sess)
        self.name = QLineEdit(placeholderText="ex.: automação boletos")
        base = QHBoxLayout()
        self.base_dir = QLineEdit(self.settings.value("base_dir", str(AppConfig().base_dir)))
        pick = QPushButton("…")
        pick.clicked.connect(self._pick_base_dir)
        base.addWidget(self.base_dir)
        base.addWidget(pick)
        form.addRow("Nome", self.name)
        form.addRow("Pasta base", base)
        row = QHBoxLayout()
        self.btn_new = self._button("Nova sessão", self._new_session, row)
        self.btn_open = self._button("Abrir sessão…", self._open_session, row)
        self.btn_import = self._button("Importar vídeo…", self._import_video, row)
        self.btn_record = self._button("Gravar (OBS)", self._toggle_record, row)
        form.addRow(row)
        self.session_label = QLabel("Nenhuma sessão aberta")
        form.addRow(self.session_label)
        root.addWidget(sess)

        opts = QGroupBox("Opções")
        of = QFormLayout(opts)
        self.language = QComboBox()
        self.language.addItems(LANGUAGES)
        self.model = QComboBox()
        self.model.addItems(MODELS)
        self.model.setCurrentText(defaults.whisper_model)
        self.frame_mode = QComboBox()
        self.frame_mode.addItems(FRAME_MODES)
        self.interval = QDoubleSpinBox(minimum=0.5, maximum=600, value=defaults.frame_interval_s, suffix=" s")
        self.max_width = QSpinBox(minimum=320, maximum=3840, value=defaults.max_width, suffix=" px")
        self.quality = QSpinBox(minimum=1, maximum=100, value=defaults.jpg_quality)
        self.chk_transcribe = QCheckBox("Transcrição", checked=True)
        self.chk_ocr = QCheckBox("OCR", checked=True)
        self.chk_mouse = QCheckBox("Capturar cliques e janela", checked=True)
        self.chk_keys = QCheckBox("Capturar teclas (F9 = modo sigiloso)", checked=True)
        self.chk_private = QCheckBox("Modo privado (bloqueia rede externa)", checked=True)
        self.chk_private.toggled.connect(self._apply_private_mode)
        of.addRow("Idioma", self.language)
        of.addRow("Modelo Whisper", self.model)
        of.addRow("Frames", self.frame_mode)
        of.addRow("Intervalo", self.interval)
        of.addRow("Largura máx.", self.max_width)
        of.addRow("Qualidade JPG", self.quality)
        toggles = QHBoxLayout()
        for w in (self.chk_transcribe, self.chk_ocr, self.chk_mouse, self.chk_keys):
            toggles.addWidget(w)
        of.addRow(toggles)
        of.addRow(self.chk_private)
        root.addWidget(opts)

        self.progress = QProgressBar(maximum=len(Stage))
        self.stage_label = QLabel()
        root.addWidget(self.progress)
        root.addWidget(self.stage_label)

        actions = QHBoxLayout()
        self.btn_process = self._button("Processar sessão", lambda: self._process(), actions)
        self.btn_cancel = self._button("Cancelar", self._cancel, actions)
        self.btn_folder = self._button("Abrir pasta da sessão", self._open_folder, actions)
        self.btn_package = self._button("Gerar pacote para Claude", self._package, actions)
        self.btn_prompt = self._button("Copiar prompt para Claude", self._copy_prompt, actions)
        root.addLayout(actions)

        self.log = QPlainTextEdit(readOnly=True)
        self.log.setMaximumBlockCount(5000)
        root.addWidget(self.log, stretch=1)

        container = QWidget()
        container.setLayout(root)
        self.setCentralWidget(container)
        self.resize(900, 760)

    @staticmethod
    def _button(text: str, slot, layout: QHBoxLayout) -> QPushButton:  # type: ignore[no-untyped-def]
        b = QPushButton(text)
        b.clicked.connect(slot)
        layout.addWidget(b)
        return b

    def _wire_logging(self) -> None:
        self.log_bridge = LogBridge()
        self.log_bridge.line.connect(self.log.appendPlainText)
        logger = logging.getLogger("replayflow")
        logger.setLevel(logging.INFO)
        logger.addHandler(self.log_bridge)

    # ---------- estado ----------

    def _config(self) -> PipelineConfig:
        return PipelineConfig(
            language=LANGUAGES[self.language.currentText()],
            whisper_model=self.model.currentText(),  # type: ignore[arg-type]
            transcription_enabled=self.chk_transcribe.isChecked(),
            ocr_enabled=self.chk_ocr.isChecked(),
            frame_mode=FRAME_MODES[self.frame_mode.currentText()],
            frame_interval_s=self.interval.value(),
            max_width=self.max_width.value(),
            jpg_quality=self.quality.value(),
        )

    def _load_config_into_form(self, cfg: PipelineConfig) -> None:
        self.language.setCurrentText(next((k for k, v in LANGUAGES.items() if v == cfg.language), "pt-BR"))
        self.model.setCurrentText(cfg.whisper_model)
        self.frame_mode.setCurrentText(next(k for k, v in FRAME_MODES.items() if v == cfg.frame_mode))
        self.interval.setValue(cfg.frame_interval_s)
        self.max_width.setValue(cfg.max_width)
        self.quality.setValue(cfg.jpg_quality)
        self.chk_transcribe.setChecked(cfg.transcription_enabled)
        self.chk_ocr.setChecked(cfg.ocr_enabled)

    def _refresh(self) -> None:
        busy = self.thread is not None
        recording = self.capture is not None or self.obs is not None
        has_session = self.session is not None
        has_video = has_session and self.session.video_file is not None  # type: ignore[union-attr]
        for b in (self.btn_new, self.btn_open):
            b.setEnabled(not busy and not recording)
        self.btn_import.setEnabled(has_session and not busy and not recording)
        self.btn_record.setEnabled(has_session and not busy)
        self.btn_record.setText("Parar gravação" if recording else "Gravar (OBS)")
        self.btn_process.setEnabled(has_video and not busy and not recording)
        self.btn_cancel.setEnabled(busy)
        self.btn_folder.setEnabled(has_session)
        self.btn_package.setEnabled(has_video and not busy)
        self.btn_prompt.setEnabled(has_session)
        if self.session:
            s = self.session
            self.session_label.setText(f"<b>{s.id}</b> · vídeo: {s.video_file or '—'}")
            self.progress.setValue(sum(st.status != StageStatus.PENDING for st in s.stages.values()))
            self.stage_label.setText("  ".join(f"{STATUS_ICON[st.status]} {k}" for k, st in s.stages.items()))

    def _apply_private_mode(self) -> None:
        network_guard.apply(self.chk_private.isChecked())
        logging.getLogger("replayflow").info(
            "modo privado " + ("ATIVO: rede externa bloqueada" if self.chk_private.isChecked() else "DESATIVADO"))

    def _error(self, msg: str) -> None:
        QMessageBox.warning(self, "ReplayFlow", msg)

    # ---------- ações ----------

    def _pick_base_dir(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "Pasta base", self.base_dir.text())
        if d:
            self.base_dir.setText(d)

    def _new_session(self) -> None:
        name = self.name.text().strip()
        if not name:
            return self._error("Informe o nome da sessão.")
        base = Path(self.base_dir.text())
        self.settings.setValue("base_dir", str(base))
        self.session = session_store.create_session(base, name, self._config())
        logging.getLogger("replayflow").info(f"sessão criada: {self.session.root}")
        self._refresh()

    def _open_session(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "Abrir sessão", self.base_dir.text())
        if not d:
            return
        try:
            self.session = session_store.load(Path(d))
        except ReplayFlowError as e:
            return self._error(str(e))
        self.name.setText(self.session.name)
        self._load_config_into_form(self.session.config)
        self._refresh()

    def _import_video(self) -> None:
        f, _ = QFileDialog.getOpenFileName(self, "Vídeo do OBS", "", "Vídeos (*.mkv *.mp4 *.mov *.flv *.webm)")
        if f and self.session:
            self.session = session_store.import_video(self.session, Path(f))
            logging.getLogger("replayflow").info(f"vídeo importado: {f}")
            self._refresh()

    def _toggle_record(self) -> None:
        if self.capture or self.obs:
            return self._stop_record()
        assert self.session
        log = logging.getLogger("replayflow")
        password, ok = QInputDialog.getText(self, "OBS", "Senha do obs-websocket (vazio se não houver):",
                                            QLineEdit.EchoMode.Password)
        if not ok:
            return
        try:
            from replayflow.services.obs import ObsRecorder

            self.obs = ObsRecorder(password=password or None)  # senha só em memória
        except ReplayFlowError as e:
            answer = QMessageBox.question(
                self, "ReplayFlow", f"{e}\n\nCapturar apenas o input? Inicie a gravação no OBS manualmente "
                "no mesmo instante e importe o vídeo depois.")
            if answer != QMessageBox.StandardButton.Yes:
                return
        try:
            if self.chk_mouse.isChecked() or self.chk_keys.isChecked():
                self.capture = InputCapture(self.session.root / "raw" / INPUT_EVENTS_FILE,
                                            mouse=self.chk_mouse.isChecked(), keys=self.chk_keys.isChecked())
            if self.obs:
                self.obs.start()
            if self.capture:
                self.capture.start()
            log.info("gravação iniciada" + (" (OBS)" if self.obs else "") + (" + captura de input" if self.capture else ""))
        except ReplayFlowError as e:
            self.obs = self.capture = None
            self._error(str(e))
        self._refresh()

    def _stop_record(self) -> None:
        assert self.session
        if self.capture:
            self.capture.stop()
        video = self.obs.stop() if self.obs else None
        self.capture = self.obs = None
        if video:
            self.session = session_store.import_video(self.session, video)
            logging.getLogger("replayflow").info(f"gravação finalizada e importada: {video}")
        self._refresh()

    def _process(self, stages: tuple[Stage, ...] = tuple(Stage)) -> None:
        assert self.session
        self.session = self.session.model_copy(update={"config": self._config()})
        session_store.save(self.session)
        self.thread = QThread(self)
        self.worker = Worker(self.session, self.chk_private.isChecked(), stages)
        self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.run)
        self.worker.progress.connect(self._on_progress)
        self.worker.finished.connect(self._on_finished)
        self.worker.failed.connect(self._on_failed)
        self.thread.start()
        self._refresh()

    def _package(self) -> None:
        self._process((Stage.EXPORT,))

    def _cancel(self) -> None:
        if self.worker:
            self.worker.cancel.set()

    def _end_worker(self) -> None:
        if self.thread:
            self.thread.quit()
            self.thread.wait()
        self.thread = self.worker = None
        if self.session:
            self.session = session_store.load(self.session.root)
        self._refresh()

    def _on_progress(self, stage: Stage, status: StageStatus | None) -> None:
        self.stage_label.setText(f"{STATUS_ICON[status]} {stage}")
        if self.session:
            self.session = session_store.load(self.session.root)
            self.progress.setValue(sum(st.status != StageStatus.PENDING for st in self.session.stages.values()))

    def _on_finished(self, session: Session) -> None:
        self._end_worker()
        logging.getLogger("replayflow").info("processamento concluído")

    def _on_failed(self, msg: str) -> None:
        self._end_worker()
        self._error(msg)

    def _open_folder(self) -> None:
        if self.session:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.session.root)))

    def _copy_prompt(self) -> None:
        if self.session:
            QGuiApplication.clipboard().setText(exporter.build_prompt(self.session))
            logging.getLogger("replayflow").info("prompt copiado; anexe export/claude_package.md no Claude")

    def closeEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        if self.capture or self.obs:
            self._stop_record()
        self._cancel()
        if self.thread:
            self.thread.quit()
            self.thread.wait(5000)
        super().closeEvent(event)
