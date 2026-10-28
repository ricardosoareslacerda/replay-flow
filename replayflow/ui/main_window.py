"""Janela principal (PyQt6). Só orquestra: toda a regra está em core/ e services/.

Layout: linha do tempo do passo a passo no topo; à esquerda "O que entra no vídeo" (área, microfone, som,
destaque do mouse, webcam); à direita a prévia do vídeo, qualidade/formato e o botão principal, que muda
conforme o passo (Iniciar gravação → Parar e processar → Revisar e enviar para a IA).
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

from PyQt6.QtCore import QObject, QSettings, Qt, QThread, QTimer, QUrl, pyqtSignal
from PyQt6.QtGui import QDesktopServices, QGuiApplication, QPixmap
from PyQt6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFileDialog, QFormLayout, QFrame, QGroupBox,
    QHBoxLayout, QInputDialog, QLabel, QLineEdit, QMainWindow, QMessageBox, QPlainTextEdit, QPushButton, QSpinBox,
    QStackedWidget, QVBoxLayout, QWidget,
)

from replayflow.core import network_guard, session_store, timeline_store
from replayflow.core.errors import CancelledError, ReplayFlowError
from replayflow.core.layout import SessionLayout
from replayflow.core.pipeline import INPUT_EVENTS_FILE, Pipeline
from replayflow.models import AppConfig, FrameMode, PipelineConfig, Session, Stage, StageStatus
from replayflow.models.config import portable_home
from replayflow.services import audio_devices, capture_target, exporter, ffmpeg, verify
from replayflow.services.input_capture import InputCapture
from replayflow.ui import theme
from replayflow.ui.widgets import (
    AnnotationLayer, AreaSelector, MouseHighlight, Preview, RegionOutline, Segmented, Switch, Timeline, TimerBadge,
    WindowPicker,
)

LANGUAGES = {"pt-BR": "pt", "en": "en", "es": "es"}
MODELS = ["tiny", "base", "small", "medium", "large-v3"]
FRAME_MODES = {"Ambos": FrameMode.BOTH, "Por tempo": FrameMode.INTERVAL, "Mudança de cena": FrameMode.SCENE}
STAGE_NAMES = {Stage.AUDIO: "áudio", Stage.TRANSCRIPT: "transcrição", Stage.FRAMES: "imagens",
               Stage.OCR: "texto das telas", Stage.INPUT: "cliques e teclas", Stage.EXPORT: "verificação e pacote"}
AREA_MODES = ["Tela inteira", "Área", "Janela"]
QUALITIES = [("HD 720p", 720), ("Full HD 1080p", 1080)]
FORMATS = [("MKV", "mkv"), ("MP4", "mp4"), ("MP4 H.265", "hevc"), ("WMV", "wmv")]
MIC_SILENT_DB = -80.0  # média abaixo disso = microfone mudo (um microfone vivo em sala quieta fica em torno de -70)


class LogBridge(QObject, logging.Handler):
    line = pyqtSignal(str)

    def __init__(self) -> None:
        QObject.__init__(self)
        logging.Handler.__init__(self)
        self.setFormatter(logging.Formatter("%(asctime)s %(message)s", "%H:%M:%S"))

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


class ClickRelay(QObject):
    """Leva eventos das threads do pynput (clique, tecla F8) até a thread da interface."""

    clicked = pyqtSignal()
    pen_key = pyqtSignal()


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("ReplayFlow")
        home = portable_home()  # portátil: preferências num .ini da própria pasta, não no registro
        self.settings = (QSettings(str(home / "settings.ini"), QSettings.Format.IniFormat) if home
                         else QSettings("ReplayFlow", "ReplayFlow"))
        self.session: Session | None = None
        self.thread: QThread | None = None
        self.worker: Worker | None = None
        self.capture: InputCapture | None = None
        self.obs = None
        self.obs_proc: subprocess.Popen[bytes] | None = None
        self.paused = False
        self.auto_flow = False
        self.sent = False
        self.rec_t0 = self.rec_paused_at = self.rec_paused_total = 0.0
        self.recording_region: capture_target.Region | None = None
        self.area_region = self._load_region("area_region")
        self.window_target: capture_target.Target | None = None
        self.outline = RegionOutline()
        self.highlight = MouseHighlight()
        self.click_relay = ClickRelay()
        self.click_relay.clicked.connect(self.highlight.clicked)
        self.badge = TimerBadge()
        self.pen = AnnotationLayer()
        self.pen.stroke_done.connect(self._pen_stroke)
        self.click_relay.pen_key.connect(self._toggle_pen)
        self.pen_listener = None
        self.camera = None  # câmera do Qt para a prévia antes de gravar (o OBS assume durante a gravação)
        self._build()
        self._wire_logging()
        self._apply_private_mode()
        self.clock = QTimer(self)
        self.clock.timeout.connect(self._tick)
        self.preview_timer = QTimer(self)
        self.preview_timer.timeout.connect(self._update_preview)
        self.preview_timer.start(1500)
        self.cam_timer = QTimer(self)
        self.cam_timer.timeout.connect(self._poll_obs_webcam)
        self._refresh_area()

    # ---------- layout ----------

    def _build(self) -> None:
        defaults = PipelineConfig()
        self.base_path = str(self.settings.value("base_dir", str(AppConfig().base_dir)))
        self._build_settings_widgets(defaults)
        self._build_menu()

        root = QVBoxLayout()
        root.setContentsMargins(24, 16, 24, 12)
        root.setSpacing(14)

        top = QHBoxLayout()
        top.setSpacing(24)
        name_box = QVBoxLayout()
        name_box.setSpacing(6)
        name_label = QLabel("Atividade que você vai mostrar")
        name_label.setObjectName("Sub")
        self.name = QLineEdit(placeholderText="ex.: baixar e arquivar as faturas do mês")
        self.name.setMinimumWidth(360)
        self.name.textChanged.connect(lambda _: self._refresh())
        self.name.returnPressed.connect(self._primary)
        name_label.setBuddy(self.name)
        name_box.addWidget(name_label)
        name_box.addWidget(self.name)
        self.timeline = Timeline()
        self.timer_label = QLabel()
        self.timer_label.setStyleSheet(f"font-size:26px;font-weight:700;color:#E0533D;padding-left:8px;")
        self.timer_label.setAccessibleName("Tempo de gravação")
        self.timer_label.setVisible(False)
        top.addLayout(name_box)
        top.addWidget(self.timeline, stretch=1)
        top.addWidget(self.timer_label)
        root.addLayout(top)

        body = QHBoxLayout()
        body.setSpacing(18)
        body.addWidget(self._sources_panel())
        body.addWidget(self._video_panel(), stretch=1)
        root.addLayout(body, stretch=1)

        self.log = QPlainTextEdit(readOnly=True)
        self.log.setMaximumBlockCount(3000)
        self.log.setFixedHeight(78)
        self.log.setAccessibleName("Andamento")
        root.addWidget(self.log)

        footer = QHBoxLayout()
        self.privacy_label = QLabel()
        self.privacy_label.setObjectName("Muted")
        footer.addWidget(self.privacy_label)
        footer.addStretch(1)
        self.btn_library = QPushButton("Minhas atividades →")
        self.btn_library.setFlat(True)
        self.btn_library.setStyleSheet(f"border:0;min-height:22px;color:{theme.TEAL_LIGHT};font-weight:600;")
        self.btn_library.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_library.clicked.connect(self._library)
        footer.addWidget(self.btn_library)
        hint = QLabel("   ·   F9 = digitar senha sem registrar")
        hint.setObjectName("Muted")
        footer.addWidget(hint)
        root.addLayout(footer)

        container = QWidget()
        container.setLayout(root)
        self.setCentralWidget(container)
        screen = QGuiApplication.primaryScreen()
        avail = screen.availableGeometry() if screen else None
        self.resize(1180, min(720, avail.height() - 40) if avail else 720)

    def _panel(self, title: str) -> tuple[QFrame, QVBoxLayout]:
        frame = QFrame()
        frame.setObjectName("Panel")
        lay = QVBoxLayout(frame)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        label = QLabel(title.upper())
        label.setObjectName("Section")
        label.setContentsMargins(16, 14, 16, 8)
        lay.addWidget(label)
        return frame, lay

    def _row(self, title: str, sub: str, control: QWidget) -> tuple[QFrame, QLabel]:
        row = QFrame()
        row.setObjectName("Row")
        lay = QHBoxLayout(row)
        lay.setContentsMargins(16, 10, 16, 10)
        texts = QVBoxLayout()
        texts.setSpacing(1)
        t = QLabel(title)
        t.setObjectName("Title")
        s = QLabel(sub)
        s.setObjectName("Sub")
        texts.addWidget(t)
        texts.addWidget(s)
        lay.addLayout(texts, stretch=1)
        lay.addWidget(control)
        control.setAccessibleName(title)
        return row, s

    def _sources_panel(self) -> QFrame:
        frame, lay = self._panel("O que entra no vídeo")
        frame.setFixedWidth(440)

        area = QFrame()
        area.setObjectName("Row")
        al = QVBoxLayout(area)
        al.setContentsMargins(16, 6, 16, 12)
        al.setSpacing(8)
        title = QLabel("Área de captura")
        title.setObjectName("Title")
        self.area_mode = Segmented(AREA_MODES, int(self.settings.value("area_mode", 0)))
        self.area_mode.setAccessibleName("Área de captura")
        self.area_mode.changed.connect(self._area_mode_changed)
        self.area_hint = QLabel()
        self.area_hint.setObjectName("Sub")
        self.area_hint.setWordWrap(True)
        pick_row = QHBoxLayout()
        self.monitor_combo = QComboBox()
        self.monitor_combo.setAccessibleName("Monitor")
        for m in capture_target.monitors():
            self.monitor_combo.addItem(m.label.replace("Tela inteira · ", ""), m)
        self.monitor_combo.setCurrentIndex(min(int(self.settings.value("monitor", 0)),
                                               max(0, self.monitor_combo.count() - 1)))
        self.monitor_combo.currentIndexChanged.connect(
            lambda i: (self.settings.setValue("monitor", i), self._refresh_area()))
        self.btn_pick = QPushButton()
        self.btn_pick.clicked.connect(self._pick_area)
        pick_row.addWidget(self.monitor_combo, stretch=1)
        pick_row.addWidget(self.btn_pick)
        al.addWidget(title)
        al.addWidget(self.area_mode)
        al.addLayout(pick_row)
        al.addWidget(self.area_hint)
        lay.addWidget(area)

        self.sw_mic = Switch(self.settings.value("mic_on", True, type=bool))
        self.sw_speaker = Switch(self.settings.value("speaker_on", True, type=bool))
        self.sw_mouse = Switch(self.settings.value("mouse_on", False, type=bool))
        self.sw_cam = Switch(self.settings.value("cam_on", False, type=bool))
        rows = [
            (self.sw_mic, "mic_on", "Microfone · sua narração",
             lambda on: self.mic.currentText() if on else "Sem narração: só cliques e telas"),
            (self.sw_speaker, "speaker_on", "Som do computador",
             lambda on: "Alertas e avisos viram eventos" if on else "Desligado"),
            (self.sw_mouse, "mouse_on", "Destaque do mouse",
             lambda on: "Anel vermelho e pulso a cada clique" if on else "Cursor normal"),
            (self.sw_cam, "cam_on", "Webcam no canto",
             lambda on: "Seu rosto no canto inferior direito" if on else "Desligada"),
        ]
        self.sw_pen = Switch(self.settings.value("pen_on", False, type=bool))
        rows.append((self.sw_pen, "pen_on", "Caneta de apontamento (F8)",
                     lambda on: "Na gravação, F8 liga/desliga a caneta" if on else "Desligada"))
        self.source_rows = []
        for sw, key, title, sub in rows:
            row, label = self._row(title, sub(sw.isChecked()), sw)
            sw.toggled.connect(lambda on, k=key, lbl=label, f=sub: (self.settings.setValue(k, on), lbl.setText(f(on)),
                                                                    self._update_preview()))
            lay.addWidget(row)
            self.source_rows.append(sw)
        self.chk_pen_log = QCheckBox("Registrar os desenhos da caneta no pacote para a IA",
                                     checked=self.settings.value("pen_log", True, type=bool))
        self.chk_pen_log.toggled.connect(lambda on: self.settings.setValue("pen_log", on))
        self.chk_pen_log.setContentsMargins(16, 0, 16, 0)
        pen_log_box = QHBoxLayout()
        pen_log_box.setContentsMargins(16, 6, 16, 0)
        pen_log_box.addWidget(self.chk_pen_log)
        lay.addLayout(pen_log_box)
        self.sw_pen.toggled.connect(self.chk_pen_log.setEnabled)
        self.chk_pen_log.setEnabled(self.sw_pen.isChecked())
        self.sw_cam.toggled.connect(lambda _: self._sync_camera())
        lay.addStretch(1)
        self.live_counts = QLabel()
        self.live_counts.setObjectName("Sub")
        self.live_counts.setContentsMargins(16, 8, 16, 14)
        self.live_counts.setWordWrap(True)
        lay.addWidget(self.live_counts)
        return frame

    def _video_panel(self) -> QFrame:
        frame, lay = self._panel("Prévia do vídeo")
        inner = QVBoxLayout()
        inner.setContentsMargins(16, 0, 16, 16)
        inner.setSpacing(12)
        self.stack = QStackedWidget()
        self.preview = Preview()
        self.summary = QLabel()
        self.summary.setWordWrap(True)
        self.summary.setTextFormat(Qt.TextFormat.RichText)
        self.summary.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.summary.setStyleSheet(f"background:#0B0E11;border:1px solid {theme.BORDER};border-radius:8px;padding:14px;")
        self.stack.addWidget(self.preview)
        self.stack.addWidget(self.summary)
        inner.addWidget(self.stack, stretch=1)

        self.output_row = QWidget()
        orow = QHBoxLayout(self.output_row)
        orow.setContentsMargins(0, 0, 0, 0)
        self.quality = Segmented([q[0] for q in QUALITIES], int(self.settings.value("quality", 1)))
        self.quality.changed.connect(lambda i: self.settings.setValue("quality", i))
        self.out_format = Segmented([f[0] for f in FORMATS], int(self.settings.value("format", 1)))
        self.out_format.changed.connect(lambda i: self.settings.setValue("format", i))
        self.quality.setAccessibleName("Qualidade do vídeo")
        self.out_format.setAccessibleName("Formato do vídeo")
        orow.addWidget(self.quality)
        orow.addWidget(self.out_format)
        orow.addStretch(1)
        inner.addWidget(self.output_row)

        self.send_row = QWidget()
        srow = QHBoxLayout(self.send_row)
        srow.setContentsMargins(0, 0, 0, 0)
        lbl = QLabel("Enviar para")
        lbl.setObjectName("Sub")
        self.ai_target = QComboBox()
        self.ai_target.setAccessibleName("Enviar para")
        for key, (label, _url) in exporter.AI_TARGETS.items():
            self.ai_target.addItem(label, key)
        self.ai_target.setCurrentIndex(max(0, self.ai_target.findData(self.settings.value("ai_target", "claude"))))
        self.ai_target.currentIndexChanged.connect(
            lambda _: self.settings.setValue("ai_target", self.ai_target.currentData()))
        srow.addWidget(lbl)
        srow.addWidget(self.ai_target, stretch=1)
        inner.addWidget(self.send_row)

        self.btn_primary = QPushButton()
        self.btn_primary.setObjectName("Primary")
        self.btn_primary.clicked.connect(self._primary)
        inner.addWidget(self.btn_primary)

        secondary = QHBoxLayout()
        self.btn_pause = QPushButton("Pausar")
        self.btn_pause.clicked.connect(self._toggle_pause)
        self.btn_discard = QPushButton("Descartar")
        self.btn_discard.setObjectName("Danger")
        self.btn_discard.setToolTip("Para e apaga esta gravação, sem processar nada")
        self.btn_discard.clicked.connect(self._discard_record)
        self.btn_watch = QPushButton("Assistir à gravação")
        self.btn_watch.clicked.connect(self._play_video)
        self.btn_folder = QPushButton("Abrir pasta")
        self.btn_folder.clicked.connect(self._open_folder)
        self.btn_delete = QPushButton("Excluir…")
        self.btn_delete.setObjectName("Danger")
        self.btn_delete.setToolTip("Apagar o vídeo ou a atividade inteira")
        self.btn_delete.clicked.connect(self._delete_dialog)
        for b in (self.btn_pause, self.btn_discard, self.btn_watch, self.btn_folder, self.btn_delete):
            secondary.addWidget(b)
        inner.addLayout(secondary)
        lay.addLayout(inner, stretch=1)
        return frame

    def _build_settings_widgets(self, defaults: PipelineConfig) -> None:
        self.language = QComboBox()
        self.language.addItems(LANGUAGES)
        self.model = QComboBox()
        self.model.addItems(MODELS)
        self.model.setCurrentText(defaults.whisper_model)
        self.frame_mode = QComboBox()
        self.frame_mode.addItems(FRAME_MODES)
        self.interval = QDoubleSpinBox(minimum=0.5, maximum=600, value=defaults.frame_interval_s, suffix=" s")
        self.max_width = QSpinBox(minimum=320, maximum=3840, value=defaults.max_width, suffix=" px")
        self.jpg_quality = QSpinBox(minimum=1, maximum=100, value=defaults.jpg_quality)
        self.chk_transcribe = QCheckBox("Transcrição", checked=True)
        self.chk_ocr = QCheckBox("OCR (texto das telas)", checked=True)
        self.chk_mouse = QCheckBox("Capturar cliques e janela", checked=True)
        self.chk_keys = QCheckBox("Capturar teclas (F9 = modo sigiloso)", checked=True)
        self.chk_private = QCheckBox("Modo privado (bloqueia rede externa)", checked=True)
        self.chk_private.toggled.connect(self._apply_private_mode)
        self.chk_redact = QCheckBox("Mascarar e-mails, CPF/CNPJ e números de cartão no pacote",
                                    checked=self.settings.value("redact", True, type=bool))
        self.chk_redact.toggled.connect(lambda on: (self.settings.setValue("redact", on), self._refresh_footer()))
        self.chk_auto = QCheckBox("Ao parar a gravação, processar e preparar o pacote automaticamente",
                                  checked=self.settings.value("auto_flow", True, type=bool))
        self.chk_auto.toggled.connect(lambda on: self.settings.setValue("auto_flow", on))
        self.mic = self._device_combo(audio_devices.microphones(), "mic_device")
        self.out = self._device_combo(audio_devices.outputs(), "out_device")
        self.audio_dialog = self._audio_dialog()
        self.options_dialog = self._options_dialog()

    def _build_menu(self) -> None:
        bar = self.menuBar()
        m = bar.addMenu("&Atividade")
        self.act_new = m.addAction("Nova atividade", self._start_over)
        self.act_library = m.addAction("Minhas atividades…", self._library)
        self.act_library.setShortcut("Ctrl+L")
        self.act_open = m.addAction("Abrir atividade de outra pasta…", self._open_session)
        self.act_import = m.addAction("Importar vídeo já gravado…", self._import_dialog)
        m.addSeparator()
        self.act_play = m.addAction("Assistir à gravação", self._play_video)
        self.act_folder = m.addAction("Abrir pasta da atividade", self._open_folder)
        self.act_delete = m.addAction("Excluir gravação ou atividade…", self._delete_dialog)
        m.addSeparator()
        m.addAction("Sair", self.close)
        t = bar.addMenu("&Ferramentas")
        self.act_process = t.addAction("Processar / retomar processamento", lambda: self._process())
        self.act_package = t.addAction("Gerar pacote de novo", self._package)
        self.act_prompt = t.addAction("Copiar prompt para a IA", self._copy_prompt)
        t.addSeparator()
        self.act_cancel = t.addAction("Cancelar processamento", self._cancel)
        c = bar.addMenu("&Configurações")
        self.act_audio = c.addAction("Dispositivos de áudio…", self.audio_dialog.exec)
        self.act_options = c.addAction("Opções…", self.options_dialog.exec)
        self.act_base = c.addAction("Pasta onde as atividades são salvas…", self._pick_base_dir)

    def _audio_dialog(self) -> QDialog:
        d = QDialog(self)
        d.setWindowTitle("Dispositivos de áudio")
        f = QFormLayout(d)
        info = QLabel("Escolha o microfone que grava a sua voz e a saída de som do computador. "
                      "Se a transcrição sair vazia, troque o microfone aqui.")
        info.setWordWrap(True)
        f.addRow(info)
        f.addRow("Microfone (sua voz)", self.mic)
        f.addRow("Som do computador", self.out)
        close = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close.rejected.connect(d.accept)
        f.addRow(close)
        d.setMinimumWidth(560)
        return d

    def _options_dialog(self) -> QDialog:
        d = QDialog(self)
        d.setWindowTitle("Opções")
        f = QFormLayout(d)
        for w in (self.chk_auto, self.chk_private, self.chk_redact):
            f.addRow(w)
        adv = QGroupBox("Avançado (deixe o padrão se estiver em dúvida)")
        af = QFormLayout(adv)
        af.addRow("Idioma", self.language)
        af.addRow("Modelo Whisper", self.model)
        af.addRow("Imagens da tela", self.frame_mode)
        af.addRow("Intervalo entre imagens", self.interval)
        af.addRow("Largura máx. da imagem", self.max_width)
        af.addRow("Qualidade JPG", self.jpg_quality)
        for w in (self.chk_transcribe, self.chk_ocr, self.chk_mouse, self.chk_keys):
            af.addRow(w)
        f.addRow(adv)
        close = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close.rejected.connect(d.accept)
        f.addRow(close)
        return d

    def _device_combo(self, devices: list[audio_devices.AudioDevice], key: str) -> QComboBox:
        combo = QComboBox()
        combo.addItem("Padrão do Windows", audio_devices.DEFAULT)
        for d in devices:
            combo.addItem(d.name, d.id)
        saved = combo.findData(self.settings.value(key, audio_devices.DEFAULT))
        combo.setCurrentIndex(max(saved, 0))
        combo.currentIndexChanged.connect(lambda _: self.settings.setValue(key, combo.currentData()))
        return combo

    def _wire_logging(self) -> None:
        self.log_bridge = LogBridge()
        self.log_bridge.line.connect(self.log.appendPlainText)
        logger = logging.getLogger("replayflow")
        logger.setLevel(logging.INFO)
        logger.addHandler(self.log_bridge)

    # ---------- área de captura ----------

    def _load_region(self, key: str) -> capture_target.Region | None:
        raw = self.settings.value(key, "")
        try:
            parts = tuple(int(v) for v in str(raw).split(","))
            return parts if len(parts) == 4 else None  # type: ignore[return-value]
        except ValueError:
            return None

    def _monitor(self) -> capture_target.Target | None:
        return self.monitor_combo.currentData()

    def _current_region(self) -> capture_target.Region | None:
        """Retângulo físico que vai para o vídeo no modo escolhido (None = falta escolher)."""
        mode = self.area_mode.index()
        if mode == 0:
            m = self._monitor()
            return m.region if m else None
        if mode == 1:
            return self.area_region
        if self.window_target is None:
            return None
        region = capture_target.client_region(self.window_target.hwnd)
        if region is None:
            return None
        screen = capture_target.monitor_of(region, capture_target.monitors())
        return capture_target.clamp(region, screen.region) if screen else region

    def _area_mode_changed(self, i: int) -> None:
        self.settings.setValue("area_mode", i)
        if (i == 1 and self.area_region is None) or (i == 2 and self.window_target is None):
            self._pick_area()
        self._refresh_area()

    def _pick_area(self) -> None:
        mode = self.area_mode.index()
        if mode == 0:
            return
        self.showMinimized()  # sai da frente para a pessoa ver a tela inteira
        overlay = AreaSelector if mode == 1 else WindowPicker
        callback = self._area_chosen if mode == 1 else self._window_chosen
        QTimer.singleShot(300, lambda: self._open_overlay(overlay(callback)))

    def _open_overlay(self, overlay) -> None:  # type: ignore[no-untyped-def]
        self._overlay = overlay  # mantém referência até fechar
        overlay.show_over()

    def _area_chosen(self, region: capture_target.Region | None) -> None:
        self.showNormal()
        self.activateWindow()
        if region:
            self.area_region = region
            self.settings.setValue("area_region", ",".join(map(str, region)))
            logging.getLogger("replayflow").info(f"área escolhida: {region[2]}×{region[3]}")
        self._refresh_area()

    def _window_chosen(self, target: capture_target.Target | None) -> None:
        self.showNormal()
        self.activateWindow()
        if target:
            self.window_target = target
            logging.getLogger("replayflow").info(f"janela escolhida: {target.label.replace('Janela · ', '')}")
        self._refresh_area()

    def _refresh_area(self) -> None:
        mode = self.area_mode.index()
        self.monitor_combo.setVisible(mode == 0 and self.monitor_combo.count() > 1)
        self.btn_pick.setVisible(mode != 0)
        self.btn_pick.setText("Desenhar de novo…" if mode == 1 and self.area_region else
                              "Desenhar área…" if mode == 1 else
                              "Trocar janela…" if self.window_target else "Escolher janela…")
        region = self._current_region()
        if mode == 0:
            m = self._monitor()
            hint = (m.label if m else "Monitor principal") + " · o vídeo mostra a tela toda"
        elif mode == 1:
            hint = (f"Área de {region[2]} × {region[3]} pixels" if region else "Você desenha o retângulo na tela") \
                + " · uma moldura âmbar marca a área durante a gravação"
        else:
            hint = (self.window_target.label.replace("Janela · ", "Janela: ") if self.window_target and region
                    else "Clique em Escolher janela e depois na janela da atividade") \
                + " · deixe-a no tamanho final antes de gravar"
        self.area_hint.setText(hint)
        self._refresh()
        self._update_preview()

    def _sync_camera(self) -> None:
        """Liga a câmera do Qt só para a prévia, quando a webcam está ligada e não há gravação."""
        recording = self.capture is not None or self.obs is not None
        want = self.sw_cam.isChecked() and not recording and self.stack.currentWidget() is self.preview
        if want and self.camera is None:
            try:
                from PyQt6.QtMultimedia import QCamera, QMediaCaptureSession, QMediaDevices, QVideoSink

                if not QMediaDevices.videoInputs():
                    return
                self.camera = QCamera(QMediaDevices.defaultVideoInput())
                self.cam_session = QMediaCaptureSession()
                self.cam_sink = QVideoSink()
                self.cam_session.setCamera(self.camera)
                self.cam_session.setVideoSink(self.cam_sink)
                self.cam_sink.videoFrameChanged.connect(self._camera_frame)
                self.camera.start()
            except Exception as e:  # sem câmera ou sem suporte: a prévia mostra só o círculo
                logging.getLogger("replayflow").warning(f"prévia da webcam indisponível: {e}")
                self.camera = None
        elif not want and self.camera is not None:
            self.camera.stop()
            self.camera = None
            self.preview.webcam_frame = None
        self.preview.update()

    def _camera_frame(self, frame) -> None:  # type: ignore[no-untyped-def]
        image = frame.toImage()
        if not image.isNull():
            self.preview.webcam_frame = QPixmap.fromImage(image.scaledToWidth(320))
            self.preview.update()

    def _poll_obs_webcam(self) -> None:
        """Durante a gravação a câmera é do OBS: a prévia mostra o que ele está gravando."""
        if self.obs is None or not self.sw_cam.isChecked():
            return
        data = self.obs.webcam_frame()
        if data:
            pix = QPixmap()
            if pix.loadFromData(data):
                self.preview.webcam_frame = pix
                self.preview.update()

    def _update_preview(self) -> None:
        recording = self.capture is not None or self.obs is not None
        p = self.preview
        p.mouse, p.webcam, p.recording = self.sw_mouse.isChecked(), self.sw_cam.isChecked(), recording
        if not self.isMinimized() and self.stack.currentWidget() is self.preview:
            p.grab(self.recording_region if recording else self._current_region())
        else:
            p.update()

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
            jpg_quality=self.jpg_quality.value(),
            input_offset_s=self.session.config.input_offset_s if self.session else 0.0,  # medido na gravação
            redact_sensitive=self.chk_redact.isChecked(),
            capture_region=self.session.config.capture_region if self.session else None,
            output_format=FORMATS[self.out_format.index()][1],
        )

    def _load_config_into_form(self, cfg: PipelineConfig) -> None:
        self.language.setCurrentText(next((k for k, v in LANGUAGES.items() if v == cfg.language), "pt-BR"))
        self.model.setCurrentText(cfg.whisper_model)
        self.frame_mode.setCurrentText(next(k for k, v in FRAME_MODES.items() if v == cfg.frame_mode))
        self.interval.setValue(cfg.frame_interval_s)
        self.max_width.setValue(cfg.max_width)
        self.jpg_quality.setValue(cfg.jpg_quality)
        self.chk_transcribe.setChecked(cfg.transcription_enabled)
        self.chk_ocr.setChecked(cfg.ocr_enabled)
        self.out_format.set_index(next((i for i, f in enumerate(FORMATS) if f[1] == cfg.output_format), 1))

    def _done(self) -> bool:
        s = self.session
        return bool(s and s.video_file and all(st.status in (StageStatus.DONE, StageStatus.SKIPPED)
                                               for st in s.stages.values()))

    def _guide(self) -> tuple[int, str, str, bool]:
        """(passo 1-5, texto do botão principal, estilo, habilitado). 5 = concluído e enviado."""
        s = self.session
        if self.capture is not None or self.obs is not None:
            return 2, "Parar e processar", "Primary", True
        if self.thread is not None:
            return 3, "Processando… aguarde", "Primary", False
        if s is None or not s.video_file:
            return (2 if (s is not None or self.name.text().strip()) else 1), "Iniciar gravação", "Primary", True
        if not self._done():
            return 3, "Processar a gravação", "Primary", True
        if not self.sent:
            return 4, "Revisar e enviar para a IA", "Send", True
        return 5, "Registrar outra atividade", "Primary", True

    def _refresh(self) -> None:
        busy = self.thread is not None
        recording = self.capture is not None or self.obs is not None
        idle = not busy and not recording  # gravando ou processando: nada que interfira fica disponível
        has_session = self.session is not None
        has_video = has_session and self.session.video_file is not None  # type: ignore[union-attr]
        step, text, style, enabled = self._guide()
        self.timeline.set_step(min(step, 4), live=recording)
        if self.btn_primary.objectName() != style:
            self.btn_primary.setObjectName(style)
            self.btn_primary.style().unpolish(self.btn_primary)
            self.btn_primary.style().polish(self.btn_primary)
        self.btn_primary.setText(text)
        self.btn_primary.setEnabled(enabled)
        self.name.setEnabled(idle and not has_session)
        for w in (self.area_mode, self.monitor_combo, self.btn_pick, *self.source_rows, self.quality, self.out_format):
            w.setEnabled(idle and not has_video)
        self.chk_pen_log.setEnabled(idle and not has_video and self.sw_pen.isChecked())
        for a in (self.act_new, self.act_library, self.act_open, self.act_import, self.act_audio, self.act_options,
                  self.act_base):
            a.setEnabled(idle)
        self.btn_library.setEnabled(idle)
        self.act_play.setEnabled(idle and has_video)
        self.act_folder.setEnabled(idle and has_session)
        self.act_delete.setEnabled(idle and has_video)
        self.act_process.setEnabled(idle and has_video)
        self.act_package.setEnabled(idle and has_video)
        self.act_prompt.setEnabled(idle and has_session)
        self.act_cancel.setEnabled(busy)
        self.btn_pause.setVisible(recording)
        self.btn_pause.setText("Retomar" if self.paused else "Pausar")
        self.btn_discard.setVisible(recording)
        done = self._done() and idle
        self.btn_watch.setVisible(done)
        self.btn_folder.setVisible(done)
        self.btn_delete.setVisible(done)
        self.timer_label.setVisible(recording)
        self.send_row.setVisible(done and not self.sent)
        self.output_row.setVisible(not done and not busy)
        self.stack.setCurrentWidget(self.summary if done or busy else self.preview)
        self._sync_camera()
        if busy and self.session:
            names = "  ".join(f"{'✓' if st.status in (StageStatus.DONE, StageStatus.SKIPPED) else '…'} {STAGE_NAMES[k]}"
                              for k, st in self.session.stages.items())
            self.summary.setText(f"<p style='font-size:15px;font-weight:600'>Processando a gravação</p>"
                                 f"<p style='color:{theme.TEXT_SOFT}'>{names}</p>"
                                 f"<p style='color:{theme.MUTED}'>Transcrição, fotos dos cliques, leitura das telas e "
                                 "verificação. Os comandos ficam bloqueados até terminar.</p>")
        self._refresh_footer()

    def _refresh_footer(self) -> None:
        private = self.chk_private.isChecked()
        parts = ["● Modo privado: nada sai do computador" if private else "Modo privado desligado"]
        if self.chk_redact.isChecked():
            parts.append("máscara de e-mails/CPF ligada")
        self.privacy_label.setText("   ·   ".join(parts))
        self.privacy_label.setStyleSheet(f"color:{theme.TEAL_LIGHT if private else theme.DANGER};")

    def _show_summary(self) -> None:
        """Depois do processamento: o que o pacote tem, a correção aplicada e alertas, para revisar."""
        assert self.session
        layout = SessionLayout(self.session.root)
        events = timeline_store.read(layout.timeline)
        report = verify.check(events)
        clicks = len(list(layout.frames.glob("click_*.jpg")))
        speech = sum(e.source == "transcript" for e in events)
        steps = sum(e.source == "frame" for e in events)
        score = f"{report.score:.0%}" if report.score is not None else "—"
        fixes = [m for m in report.mentions if m.applied]
        fix_html = "".join(f"<li>{m.said} → <b>{m.suggestion}</b> <span style='color:{theme.MUTED}'>"
                           f"(visto em {m.where}, {m.similarity:.0%})</span></li>" for m in fixes) or "<li>nenhuma</li>"
        mmss = lambda t: f"{int(t) // 60:02d}:{int(t) % 60:02d}"  # noqa: E731
        gaps = "".join(f"<li>sem narração de {mmss(g[0])} a {mmss(g[1])} ({g[2]} ações)</li>" for g in report.silent_gaps)
        self.summary.setText(
            "<p style='font-size:15px;font-weight:600'>Pronto para revisar</p>"
            "<table cellspacing='0' cellpadding='4'>"
            f"<tr><td>Roteiro</td><td><b>{steps} passos · {mmss(self.session.duration_s or 0)}</b></td></tr>"
            f"<tr><td>Fotos dos cliques</td><td><b>{clicks}</b></td></tr>"
            f"<tr><td>Narração transcrita</td><td><b>{speech} trechos</b></td></tr>"
            f"<tr><td>Fala confirmada na tela</td><td><b style='color:{theme.TEAL_LIGHT}'>{score}</b></td></tr></table>"
            f"<p style='color:{theme.MUTED};margin-bottom:2px'>Correções aplicadas na transcrição (confira):</p>"
            f"<ul style='margin-top:0'>{fix_html}</ul>"
            + (f"<p style='color:{theme.AMBER};margin-bottom:2px'>Atenção:</p><ul style='margin-top:0'>{gaps}</ul>"
               if gaps else "")
            + f"<p style='color:{theme.MUTED}'>Nada é enviado sozinho: você revisa o pacote, cola o prompt e anexa.</p>")

    # ---------- botão principal ----------

    def _primary(self) -> None:
        step = self._guide()[0]
        if self.capture is not None or self.obs is not None:
            self._stop_record()
        elif step in (1, 2):
            if self.session is None:
                if not self.name.text().strip():
                    self.name.setFocus()
                    return self._error("Escreva qual atividade você vai mostrar (ex.: baixar as faturas do mês).")
                self._new_session()
            self._start_record()
        elif step == 3:
            self._process()
        elif step == 4:
            self._send_to_ai()
        else:
            self._start_over()

    def _package_file(self) -> Path:
        assert self.session
        return self.session.root / "export" / exporter.PACKAGE

    def _send_to_ai(self) -> None:
        """Revisão e envio num passo: abre o pacote, copia o prompt, mostra o arquivo e abre a IA escolhida.
        O envio é sempre da pessoa."""
        target = self.ai_target.currentData()
        name, url = exporter.AI_TARGETS[target]
        package = self._package_file()
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(package)))
        self._copy_prompt()
        if target != "agent":
            if sys.platform == "win32":
                subprocess.Popen(["explorer", "/select,", str(package)])
            else:
                self._open_folder()
        if url:
            QDesktopServices.openUrl(QUrl(url))
        if target == "agent":
            how = ("O prompt copiado já traz o caminho desta gravação. Cole-o no agente (ex.: Claude Code, Cursor, "
                   "Copilot no VS Code): ele lê os arquivos da pasta sozinho, sem anexos.")
        else:
            how = (f"{'Abri o ' + name + ' no navegador. ' if url else ''}Na conversa, cole o prompt (Ctrl+V) e "
                   f"arraste o arquivo {exporter.PACKAGE}, já selecionado na pasta. Opcional: algumas fotos de "
                   "clique da pasta frames.")
        QMessageBox.information(self, "Revisar e enviar", "Abri o pacote para você revisar antes de enviar: apague o "
                                "que não deve ir.\n\n" + how)
        self.sent = True
        self._refresh()

    def _start_over(self) -> None:
        self.session = None
        self.name.clear()
        self.name.setFocus()
        self.sent = False
        self.recording_region = None
        self.live_counts.clear()
        self._refresh()
        self._update_preview()

    def _apply_private_mode(self) -> None:
        network_guard.apply(self.chk_private.isChecked())
        logging.getLogger("replayflow").info(
            "modo privado " + ("ATIVO: rede externa bloqueada" if self.chk_private.isChecked() else "DESATIVADO"))
        if hasattr(self, "privacy_label"):
            self._refresh_footer()

    def _error(self, msg: str) -> None:
        QMessageBox.warning(self, "ReplayFlow", msg)

    # ---------- atividades e importação ----------

    def _pick_base_dir(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "Pasta onde as atividades são salvas", self.base_path)
        if d:
            self.base_path = d
            self.settings.setValue("base_dir", d)
            logging.getLogger("replayflow").info(f"atividades serão salvas em {d}")

    def _new_session(self) -> None:
        name = self.name.text().strip()
        if not name:
            return self._error("Escreva qual atividade você vai mostrar.")
        config = self._config().model_copy(update={"input_offset_s": 0.0, "capture_region": None})
        self.session = session_store.create_session(Path(self.base_path), name, config)
        self.sent = False
        logging.getLogger("replayflow").info(f"atividade criada: {self.session.root}")
        self._refresh()

    def _library(self) -> None:
        from replayflow.ui.library import LibraryDialog

        if self.thread is not None or self.capture is not None or self.obs is not None:
            return
        LibraryDialog(Path(self.base_path), self._load_session, self).exec()
        if self.session and not self.session.root.exists():  # a aberta foi excluída na lista
            self._start_over()

    def _open_session(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "Abrir atividade gravada", self.base_path)
        if d:
            self._load_session(Path(d))

    def _load_session(self, root: Path) -> None:
        try:
            self.session = session_store.load(root)
        except ReplayFlowError as e:
            return self._error(str(e))
        self.name.setText(self.session.name)
        self.sent = False
        self._load_config_into_form(self.session.config)
        if self._done():
            self._show_summary()
        self._refresh()

    def _import_dialog(self) -> None:
        """Vídeo gravado fora do app: nomeia a atividade, copia o vídeo e já processa."""
        home = portable_home()
        d = QDialog(self)
        d.setWindowTitle("Importar vídeo já gravado")
        f = QFormLayout(d)
        name = QLineEdit(self.name.text() if self.session is None else "",
                         placeholderText="ex.: conferência do extrato bancário")
        path = QLineEdit(readOnly=True)

        def choose() -> None:
            start = self.settings.value("video_dir", str(home / "Gravacoes") if home else "")
            file, _ = QFileDialog.getOpenFileName(d, "Vídeo gravado", start, "Vídeos (*.mkv *.mp4 *.mov *.flv *.webm)")
            if file:
                path.setText(file)

        pick = QPushButton("Escolher arquivo…")
        pick.clicked.connect(choose)
        row = QHBoxLayout()
        row.addWidget(path, stretch=1)
        row.addWidget(pick)
        info = QLabel("O vídeo é copiado para uma nova atividade e processado em seguida. Vídeos de fora não têm "
                      "cliques e teclas registrados: o pacote usa a fala e as imagens da tela.")
        info.setWordWrap(True)
        f.addRow("Que atividade aparece no vídeo?", name)
        f.addRow("Arquivo", row)
        f.addRow(info)
        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        go = box.addButton("Importar e processar", QDialogButtonBox.ButtonRole.AcceptRole)
        go.setEnabled(False)
        for w in (name, path):
            w.textChanged.connect(lambda _: go.setEnabled(bool(name.text().strip() and path.text())))
        box.accepted.connect(d.accept)
        box.rejected.connect(d.reject)
        f.addRow(box)
        d.setMinimumWidth(620)
        if d.exec() != QDialog.DialogCode.Accepted:
            return
        self.settings.setValue("video_dir", str(Path(path.text()).parent))
        self.session = None
        self.name.setText(name.text().strip())
        self._new_session()
        assert self.session
        self.session = session_store.import_video(self.session, Path(path.text()))
        logging.getLogger("replayflow").info(f"vídeo importado: {path.text()}")
        self.auto_flow = True
        self._process()

    # ---------- gravação ----------

    def _start_record(self) -> None:
        assert self.session
        log = logging.getLogger("replayflow")
        region = self._current_region()
        if region is None:
            self._error("Escolha a área de captura antes de gravar.")
            return self._pick_area()
        monitor = capture_target.monitor_of(region, capture_target.monitors()) or self._monitor()
        if self.camera is not None:  # a webcam é de um programa por vez: o OBS precisa dela agora
            self.camera.stop()
            self.camera = None
        try:
            self.obs = self._connect_obs()
        except ReplayFlowError as e:
            answer = QMessageBox.question(
                self, "ReplayFlow", f"{e}\n\nCapturar apenas cliques e teclas? Grave o vídeo por outro programa "
                "no mesmo instante e importe-o depois.")
            if answer != QMessageBox.StandardButton.Yes:
                return
        try:
            if self.chk_mouse.isChecked() or self.chk_keys.isChecked():
                self.capture = InputCapture(self.session.root / "raw" / INPUT_EVENTS_FILE,
                                            mouse=self.chk_mouse.isChecked(), keys=self.chk_keys.isChecked(),
                                            on_click=self.click_relay.clicked.emit)
                self.capture.start()  # antes do OBS: nenhum clique se perde enquanto o vídeo arranca
            offset = 0.0
            if self.obs:
                self.obs.set_devices(self.mic.currentData(), self.out.currentData())
                self.obs.set_audio(self.sw_mic.isChecked(), self.sw_speaker.isChecked())
                if monitor is not None:
                    self.obs.configure(monitor, region, QUALITIES[self.quality.index()][1],
                                       webcam=self.sw_cam.isChecked())
                if self.obs.prepare_tracks():
                    log.info("áudio em trilhas separadas: 1 = microfone (narração), 2 = som do computador")
                video_start = self.obs.start()
                if self.capture:  # sincronia: leva o relógio dos cliques/teclas para o relógio do vídeo
                    offset = self.capture.started_at - video_start
                    log.info(f"sincronia: vídeo começou {-offset:.2f}s depois da captura de input")
            self._set_recording_meta(offset, region)
            self.recording_region = region
            if self.area_mode.index() != 0:
                self.outline.show_for(region)
            if self.sw_mouse.isChecked():
                self.highlight.start()
            if self.sw_pen.isChecked():
                self.pen.attach(region)
                self._start_pen_hotkey()
            if self.area_mode.index() != 0:
                self.badge.show_at(region)
            if self.sw_cam.isChecked() and self.obs:
                self.cam_timer.start(200)
            self.rec_t0, self.rec_paused_total, self.paused = time.monotonic(), 0.0, False
            self.clock.start(500)
            self._tick()
            log.info("gravação iniciada" + (" (OBS)" if self.obs else "") + (" + cliques e teclas" if self.capture else ""))
        except ReplayFlowError as e:
            self._teardown_record()
            self._error(str(e))
        self._refresh()
        self._update_preview()

    def _set_recording_meta(self, offset: float, region: capture_target.Region) -> None:
        assert self.session
        config = self.session.config.model_copy(update={
            "input_offset_s": round(offset, 3), "capture_region": tuple(region),
            "output_format": FORMATS[self.out_format.index()][1]})
        self.session = self.session.model_copy(update={"config": config})
        session_store.save(self.session)

    def _tick(self) -> None:
        paused = self.rec_paused_total + (time.monotonic() - self.rec_paused_at if self.paused else 0.0)
        elapsed = int(time.monotonic() - self.rec_t0 - paused)
        clock = f"{elapsed // 60:02d}:{elapsed % 60:02d}"
        self.preview.timer_text = f"{'pausado ' if self.paused else ''}{clock}"
        self.timer_label.setText(("⏸ " if self.paused else "⏺ ") + clock)
        self.badge.set_time(clock, self.paused)
        if self.capture:
            c = self.capture.counts
            self.live_counts.setText(f"Registrado até agora: {c.get('click', 0)} cliques · {c.get('text', 0)} "
                                     f"trechos digitados · {c.get('window', 0)} trocas de janela")
        self._update_preview()

    def _toggle_pause(self) -> None:
        log = logging.getLogger("replayflow")
        if self.paused:
            if self.obs:
                self.obs.resume()
            if self.capture:
                self.capture.resume()
            self.rec_paused_total += time.monotonic() - self.rec_paused_at
            log.info("gravação retomada")
        else:
            if self.capture:
                self.capture.pause()
            if self.obs:
                self.obs.pause()
            self.rec_paused_at = time.monotonic()
            log.info("gravação pausada: nada é gravado nem registrado até retomar")
        self.paused = not self.paused
        self._refresh()
        self._tick()

    def _connect_obs(self):  # type: ignore[no-untyped-def]
        """Abre o OBS portátil se preciso e conecta; só pede senha se a conexão sem senha falhar."""
        from replayflow.services.obs import ObsRecorder, launch_portable_obs, portable_obs_exe

        if portable_obs_exe() and not self.obs_proc:
            logging.getLogger("replayflow").info("abrindo o OBS (escondido na bandeja)…")
            QGuiApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
            try:
                self.obs_proc = launch_portable_obs()
            finally:
                QGuiApplication.restoreOverrideCursor()
        try:
            return ObsRecorder()
        except ReplayFlowError:
            password, ok = QInputDialog.getText(self, "OBS", "Senha do obs-websocket:", QLineEdit.EchoMode.Password)
            if not ok:
                raise
            return ObsRecorder(password=password or None)  # senha só em memória

    def _teardown_record(self) -> Path | None:
        """Para OBS, captura, moldura, destaque e cronômetro. Devolve o vídeo gravado (se houver)."""
        if self.paused:
            self._toggle_pause()
        if self.capture:
            self.capture.stop()
        video = None
        if self.obs:
            try:
                video = self.obs.stop()
            except ReplayFlowError as e:
                logging.getLogger("replayflow").error(str(e))
        self.capture = self.obs = None
        self.outline.hide()
        self.highlight.stop()
        self.pen.stop()
        self._stop_pen_hotkey()
        self.badge.hide()
        self.cam_timer.stop()
        self.preview.webcam_frame = None
        self.timer_label.clear()
        self.clock.stop()
        self.preview.timer_text = ""
        return video

    # ---------- caneta de apontamento ----------

    def _start_pen_hotkey(self) -> None:
        """F8 liga/desliga a caneta de qualquer programa (o ReplayFlow fica em segundo plano na gravação)."""
        try:
            from pynput import keyboard
        except ImportError:
            return
        relay = self.click_relay

        def press(key) -> None:  # type: ignore[no-untyped-def]
            if key == keyboard.Key.f8:
                relay.pen_key.emit()

        self.pen_listener = keyboard.Listener(on_press=press)
        self.pen_listener.start()
        logging.getLogger("replayflow").info("caneta pronta: F8 liga/desliga, botão direito limpa")

    def _stop_pen_hotkey(self) -> None:
        if self.pen_listener is not None:
            self.pen_listener.stop()
            self.pen_listener = None

    def _toggle_pen(self) -> None:
        if self.obs is None and self.capture is None:
            return
        on = not self.pen.drawing
        self.pen.set_drawing(on)
        if self.capture:
            self.capture.annotating = on  # traços não são cliques em programas

    def _pen_stroke(self, points: list[tuple[int, int]]) -> None:
        if self.capture and self.chk_pen_log.isChecked():
            self.capture.record_annotation(points)

    # ---------- excluir ----------

    def _delete_dialog(self) -> None:
        """Depois de processar: apagar só o vídeo (mantém pacote e fotos) ou a atividade inteira."""
        if self.session is None or self.thread is not None:
            return
        box = QMessageBox(QMessageBox.Icon.Warning, "Excluir", "O que você quer apagar? Não dá para desfazer.",
                          parent=self)
        only_video = box.addButton("Só o vídeo (mantém o pacote)", QMessageBox.ButtonRole.DestructiveRole)
        everything = box.addButton("A atividade inteira", QMessageBox.ButtonRole.DestructiveRole)
        box.addButton(QMessageBox.StandardButton.Cancel)
        box.exec()
        root = self.session.root
        if box.clickedButton() is only_video:
            for f in [*(root / "raw").glob("*.mkv"), *(root / "raw").glob("*.mp4"), *(root / "export").glob("video.*")]:
                f.unlink(missing_ok=True)
            (root / "audio" / "audio.wav").unlink(missing_ok=True)
            logging.getLogger("replayflow").info("vídeo apagado; pacote, fotos e transcrição mantidos")
            self.btn_watch.setEnabled(False)
        elif box.clickedButton() is everything:
            name = self.session.name
            self._start_over()
            shutil.rmtree(root, ignore_errors=True)
            logging.getLogger("replayflow").info(f"atividade apagada: {name}")

    def _discard_record(self) -> None:
        """Interrompe a gravação e joga o vídeo fora: nada é importado nem processado."""
        if QMessageBox.question(self, "Descartar gravação", "Parar e descartar esta gravação? "
                                "O vídeo e os cliques registrados serão apagados e nada será processado.") \
                != QMessageBox.StandardButton.Yes:
            return
        assert self.session
        video = self._teardown_record()
        if video:
            video.unlink(missing_ok=True)
        (self.session.root / "raw" / INPUT_EVENTS_FILE).unlink(missing_ok=True)
        self.live_counts.clear()
        logging.getLogger("replayflow").info("gravação descartada; grave de novo quando quiser")
        self._refresh()
        self._update_preview()

    def _stop_record(self) -> None:
        assert self.session
        video = self._teardown_record()
        if video:
            self.session = session_store.import_video(self.session, video)
            self.sent = False
            logging.getLogger("replayflow").info(f"gravação finalizada e importada: {video.name}")
            home = portable_home()
            if home and video.parent == home / "Gravacoes":
                video.unlink(missing_ok=True)  # o original do OBS já foi copiado para a atividade
            self._check_mic(self.session.root / "raw" / self.session.video_file)  # type: ignore[operator]
            if self.chk_auto.isChecked():
                self.auto_flow = True
                self._process()
                return
        self._refresh()

    def _check_mic(self, video: Path) -> None:
        """Avisa na hora se a trilha da voz saiu muda (microfone errado, no mudo ou sem volume)."""
        if not self.sw_mic.isChecked():
            return
        try:
            level = ffmpeg.mean_volume(video, stream=0)
        except ReplayFlowError:
            return
        if level is not None and level < MIC_SILENT_DB:
            logging.getLogger("replayflow").warning(f"a trilha do microfone ficou praticamente muda ({level:.0f} dB)")
            self._error(f"O microfone \"{self.mic.currentText()}\" gravou silêncio ({level:.0f} dB).\n\n"
                        "Escolha outro em Configurações › Dispositivos de áudio, ou confira se ele não está no mudo.")

    # ---------- processamento ----------

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

    def _on_progress(self, stage: Stage, status: StageStatus | None) -> None:
        if self.session:
            self.session = session_store.load(self.session.root)
        self._refresh()

    def _on_finished(self, session: Session) -> None:
        self._end_worker()
        logging.getLogger("replayflow").info("processamento concluído: revise e envie para a IA")
        self.auto_flow = False
        self._show_summary()
        self._refresh()

    def _on_failed(self, msg: str) -> None:
        self.auto_flow = False
        self._end_worker()
        self._refresh()
        self._error(msg)

    # ---------- pasta, vídeo e prompt ----------

    def _play_video(self) -> None:
        if self.session and self.session.video_file:
            final = next(iter((self.session.root / "export").glob("video.*")), None)
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(final or self.session.root / "raw" / self.session.video_file)))

    def _open_folder(self) -> None:
        if self.session:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.session.root)))

    def _copy_prompt(self) -> None:
        if self.session:
            QGuiApplication.clipboard().setText(exporter.build_prompt(self.session, self.ai_target.currentData()))
            logging.getLogger("replayflow").info("prompt copiado para a área de transferência")

    def closeEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        if self.thread is not None and QMessageBox.question(
                self, "ReplayFlow", "O processamento ainda está em andamento. Sair mesmo assim? "
                "Ele poderá ser retomado depois em Ferramentas › Processar / retomar.") != QMessageBox.StandardButton.Yes:
            event.ignore()
            return
        if self.capture or self.obs:
            self._stop_record()
        self._cancel()
        if self.thread:
            self.thread.quit()
            self.thread.wait(5000)
        self.outline.close()
        self.highlight.close()
        self.pen.close()
        self.badge.close()
        if self.camera is not None:
            self.camera.stop()
        if self.obs_proc:  # fecha o OBS só se foi o ReplayFlow que o abriu
            from replayflow.services.obs import stop_portable_obs

            stop_portable_obs(self.obs_proc)
        super().closeEvent(event)
