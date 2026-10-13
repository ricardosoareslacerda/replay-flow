"""Janela principal (PyQt6). Só orquestra: toda a regra está em core/ e services/."""

from __future__ import annotations

import logging
import subprocess
import sys
import threading
from pathlib import Path

from PyQt6.QtCore import QObject, QSettings, Qt, QThread, QUrl, pyqtSignal
from PyQt6.QtGui import QDesktopServices, QGuiApplication
from PyQt6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFileDialog, QFormLayout, QGroupBox,
    QHBoxLayout, QInputDialog, QLabel, QLineEdit, QMainWindow, QMessageBox, QPlainTextEdit, QProgressBar,
    QPushButton, QSpinBox, QVBoxLayout, QWidget,
)

from replayflow.core import network_guard, session_store
from replayflow.core.errors import CancelledError, ReplayFlowError
from replayflow.core.pipeline import INPUT_EVENTS_FILE, Pipeline
from replayflow.models import AppConfig, FrameMode, PipelineConfig, Session, Stage, StageStatus
from replayflow.models.config import portable_home
from replayflow.services import audio_devices, exporter, ffmpeg
from replayflow.services.input_capture import InputCapture

LANGUAGES = {"pt-BR": "pt", "en": "en", "es": "es"}
MODELS = ["tiny", "base", "small", "medium", "large-v3"]
FRAME_MODES = {"Ambos": FrameMode.BOTH, "Por tempo": FrameMode.INTERVAL, "Mudança de cena": FrameMode.SCENE}
STATUS_ICON = {None: "⏳", StageStatus.DONE: "✅", StageStatus.SKIPPED: "➖", StageStatus.FAILED: "❌",
               StageStatus.PENDING: "○"}
STAGE_NAMES = {Stage.AUDIO: "áudio", Stage.TRANSCRIPT: "transcrição", Stage.FRAMES: "imagens",
               Stage.OCR: "texto das telas", Stage.INPUT: "cliques e teclas", Stage.EXPORT: "pacote"}
MIC_SILENT_DB = -80.0  # média abaixo disso = microfone mudo (um microfone vivo em sala quieta fica em torno de -70)
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
        self.reviewed = self.sent = False  # passos 4 e 5 do guia, por sessão aberta
        self._build()
        self._wire_logging()
        self._apply_private_mode()
        self._refresh()

    # ---------- layout ----------

    def _build(self) -> None:
        defaults = PipelineConfig()
        self.base_path = str(self.settings.value("base_dir", str(AppConfig().base_dir)))

        # Configurações moram em janelas do menu; o corpo da janela fica só com o passo a passo.
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
        self.chk_redact = QCheckBox("Mascarar e-mails, CPF/CNPJ e números de cartão no pacote para o Claude",
                                    checked=self.settings.value("redact", True, type=bool))
        self.chk_redact.toggled.connect(lambda on: self.settings.setValue("redact", on))
        self.chk_auto = QCheckBox("Ao parar a gravação, processar e preparar o pacote para o Claude automaticamente",
                                  checked=self.settings.value("auto_flow", True, type=bool))
        self.chk_auto.toggled.connect(lambda on: self.settings.setValue("auto_flow", on))
        self.mic = self._device_combo(audio_devices.microphones(), "mic_device")
        self.out = self._device_combo(audio_devices.outputs(), "out_device")
        self.audio_dialog = self._audio_dialog()
        self.options_dialog = self._options_dialog()
        self._build_menu()

        root = QVBoxLayout()
        warning = QLabel(WARNING)
        warning.setWordWrap(True)
        warning.setStyleSheet("background:#fff3cd;color:#664d03;padding:6px;border-radius:4px;")
        root.addWidget(warning)

        # Um bloco só: o nome da atividade faz parte do passo a passo (sem ele não há o que gravar).
        guide = QGroupBox("Registrar uma atividade do seu dia a dia")
        gl = QVBoxLayout(guide)
        self.steps_label = QLabel()
        self.steps_label.setTextFormat(Qt.TextFormat.RichText)
        self.hint = QLabel()
        self.hint.setWordWrap(True)
        name_row = QFormLayout()
        self.name = QLineEdit(placeholderText="ex.: baixar e arquivar as faturas do mês")
        self.name.returnPressed.connect(self._next)
        name_row.addRow("Que atividade você vai mostrar?", self.name)
        self.session_label = QLabel()
        self.session_label.setStyleSheet("color:gray;")
        buttons = QHBoxLayout()
        self.btn_next = QPushButton()
        self.btn_next.setMinimumHeight(44)
        self.btn_next.setStyleSheet("font-size:14px;font-weight:bold;")
        self.btn_next.clicked.connect(self._next)
        self.btn_pause = QPushButton("Pausar")
        self.btn_pause.setMinimumHeight(44)
        self.btn_pause.clicked.connect(self._toggle_pause)
        buttons.addWidget(self.btn_next, stretch=1)
        buttons.addWidget(self.btn_pause)
        self.progress = QProgressBar(maximum=len(Stage))
        self.stage_label = QLabel()
        for w in (self.steps_label, self.hint):
            gl.addWidget(w)
        gl.addLayout(name_row)
        gl.addWidget(self.session_label)
        gl.addLayout(buttons)
        gl.addWidget(self.progress)
        gl.addWidget(self.stage_label)
        root.addWidget(guide)

        log_box = QGroupBox("Andamento")
        ll = QVBoxLayout(log_box)
        self.log = QPlainTextEdit(readOnly=True)
        self.log.setMaximumBlockCount(5000)
        self.log.setMinimumHeight(140)
        ll.addWidget(self.log)
        root.addWidget(log_box, stretch=1)

        container = QWidget()
        container.setLayout(root)
        self.setCentralWidget(container)
        screen = QGuiApplication.primaryScreen()
        avail = screen.availableGeometry() if screen else None
        self.resize(900, min(720, avail.height() - 60) if avail else 720)

    def _build_menu(self) -> None:
        bar = self.menuBar()
        m = bar.addMenu("&Atividade")
        self.act_new = m.addAction("Nova atividade", self._start_over)
        self.act_open = m.addAction("Abrir atividade gravada…", self._open_session)
        self.act_import = m.addAction("Importar vídeo já gravado…", self._import_dialog)
        m.addSeparator()
        self.act_play = m.addAction("Reproduzir vídeo", self._play_video)
        self.act_folder = m.addAction("Abrir pasta da atividade", self._open_folder)
        m.addSeparator()
        m.addAction("Sair", self.close)
        t = bar.addMenu("&Ferramentas")
        self.act_process = t.addAction("Processar / retomar processamento", lambda: self._process())
        self.act_package = t.addAction("Gerar pacote para Claude", self._package)
        self.act_prompt = t.addAction("Copiar prompt para Claude", self._copy_prompt)
        t.addSeparator()
        self.act_cancel = t.addAction("Cancelar processamento", self._cancel)
        c = bar.addMenu("&Configurações")
        self.act_audio = c.addAction("Áudio: microfone e som…", self.audio_dialog.exec)
        self.act_options = c.addAction("Opções…", self.options_dialog.exec)
        self.act_base = c.addAction("Pasta onde as atividades são salvas…", self._pick_base_dir)

    def _audio_dialog(self) -> QDialog:
        d = QDialog(self)
        d.setWindowTitle("Áudio da gravação")
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
        af.addRow("Frames", self.frame_mode)
        af.addRow("Intervalo entre frames", self.interval)
        af.addRow("Largura máx. da imagem", self.max_width)
        af.addRow("Qualidade JPG", self.quality)
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
            input_offset_s=self.session.config.input_offset_s if self.session else 0.0,  # medido na gravação
            redact_sensitive=self.chk_redact.isChecked(),
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
        idle = not busy and not recording  # gravando ou processando: nada que interfira fica disponível
        has_session = self.session is not None
        has_video = has_session and self.session.video_file is not None  # type: ignore[union-attr]
        self.name.setEnabled(idle and not has_session)
        for a in (self.act_new, self.act_open, self.act_import, self.act_audio, self.act_options, self.act_base):
            a.setEnabled(idle)
        self.act_play.setEnabled(idle and has_video)
        self.act_folder.setEnabled(idle and has_session)
        self.act_process.setEnabled(idle and has_video)
        self.act_package.setEnabled(idle and has_video)
        self.act_prompt.setEnabled(idle and has_session)
        self.act_cancel.setEnabled(busy)
        self.btn_pause.setVisible(recording)
        self.progress.setVisible(has_video)
        self.stage_label.setVisible(has_video)
        self.btn_pause.setText("Retomar" if self.paused else "Pausar")
        self._refresh_guide()
        if self.session:
            s = self.session
            self.session_label.setText(f"Atividade: <b>{s.name}</b> · pasta {s.id} · vídeo: {s.video_file or 'ainda não gravado'}")
            self.progress.setValue(sum(st.status != StageStatus.PENDING for st in s.stages.values()))
            self.stage_label.setText("  ".join(f"{STATUS_ICON[st.status]} {STAGE_NAMES[k]}" for k, st in s.stages.items()))
        else:
            self.session_label.setText("")

    # ---------- guia passo a passo ----------

    def _guide(self) -> tuple[int, str, str, bool]:
        """(passo atual 1-5, texto do botão Próximo, dica, botão habilitado). Passo 6 = concluído."""
        s = self.session
        recording = self.capture is not None or self.obs is not None
        if s is None:
            return 1, "Próximo: começar", "Escreva com suas palavras qual atividade você vai mostrar " \
                "(ex.: baixar e arquivar as faturas do mês) e clique em Próximo.", True
        if recording:
            return 2, "Parar gravação", "Gravando: faça o trabalho falando em voz alta o que faz e por quê. " \
                "F9 antes e depois de digitar senhas; Pausar se precisar interromper.", True
        if not s.video_file:
            return 2, "Próximo: gravar a tela", "Ao clicar, a gravação começa: faça a atividade normalmente, " \
                "explicando em voz alta o que faz e por quê. Já tem um vídeo pronto? Menu Atividade › " \
                "Importar vídeo já gravado.", True
        if self.thread is not None:
            return 3, "Processando… aguarde", "Transcrevendo, tirando as fotos e lendo o texto das telas. " \
                "Os comandos ficam bloqueados até terminar (para cancelar: Ferramentas › Cancelar processamento).", False
        if not all(st.status in (StageStatus.DONE, StageStatus.SKIPPED) for st in s.stages.values()):
            return 3, "Próximo: processar a gravação", "Transforma o vídeo em transcrição, fotos dos cliques, " \
                "texto das telas e o pacote para o Claude.", True
        if not self.reviewed:
            return 4, "Próximo: revisar o pacote", "Abre o pacote gerado. Confira se não há senhas, CPFs ou dados " \
                "de clientes antes de enviar. \"Reproduzir vídeo\" mostra a gravação.", True
        if not self.sent:
            return 5, "Próximo: enviar ao Claude", "Copia o prompt, mostra o arquivo do pacote e abre o Claude no " \
                "navegador. Nada é enviado sozinho: você cola e anexa.", True
        return 6, "Registrar outra atividade", "Concluído! Para mostrar outra atividade, clique abaixo.", True

    def _refresh_guide(self) -> None:
        step, text, hint, enabled = self._guide()
        names = ["Nomear a atividade", "Gravar", "Processar", "Revisar", "Enviar ao Claude"]
        parts = []
        for i, name in enumerate(names, start=1):
            label = f"{'✔' if i < step else i} {name}"
            parts.append(f"<b><u>{label}</u></b>" if i == step else
                         f"<span style='color:gray'>{label}</span>" if i > step else label)
        self.steps_label.setText("  →  ".join(parts))
        self.hint.setText(hint)
        self.btn_next.setText(text)
        self.btn_next.setEnabled(enabled)

    def _next(self) -> None:
        step = self._guide()[0]
        if step == 1:
            if not self.name.text().strip():
                self.name.setFocus()
                return self._error("Escreva qual atividade você vai mostrar (ex.: baixar as faturas do mês).")
            self._new_session()
        elif step == 2:
            self._toggle_record()
        elif step == 3:
            self._process()
        elif step == 4:
            self._review()
        elif step == 5:
            self._send_to_claude()
        else:
            self._start_over()

    def _package_file(self) -> Path:
        assert self.session
        return self.session.root / "export" / "claude_package.md"

    def _review(self) -> None:
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._package_file())))
        self.reviewed = True
        self._refresh()

    def _send_to_claude(self) -> None:
        """Só prepara: copia o prompt, seleciona o arquivo no Explorer e abre o Claude. O envio é do usuário."""
        package = self._package_file()
        self._copy_prompt()
        if sys.platform == "win32":
            subprocess.Popen(["explorer", "/select,", str(package)])
        else:
            self._open_folder()
        QDesktopServices.openUrl(QUrl("https://claude.ai/new"))
        QMessageBox.information(
            self, "Enviar ao Claude",
            "Abri o Claude no navegador e a pasta com o pacote.\n\n"
            "1. Na conversa do Claude, cole o prompt (Ctrl+V). Ele já está copiado.\n"
            "2. Arraste o arquivo claude_package.md (já selecionado na pasta) para a conversa.\n"
            "3. Opcional: arraste também algumas fotos de clique da pasta frames.\n"
            "4. Envie e aguarde a análise do fluxo.")
        self.sent = True
        self._refresh()

    def _start_over(self) -> None:
        self.session = None
        self.name.clear()
        self.name.setFocus()
        self.reviewed = self.sent = False
        self.progress.setValue(0)
        self.stage_label.clear()
        self._refresh()

    def _apply_private_mode(self) -> None:
        network_guard.apply(self.chk_private.isChecked())
        logging.getLogger("replayflow").info(
            "modo privado " + ("ATIVO: rede externa bloqueada" if self.chk_private.isChecked() else "DESATIVADO"))

    def _error(self, msg: str) -> None:
        QMessageBox.warning(self, "ReplayFlow", msg)

    # ---------- ações ----------

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
        base = Path(self.base_path)
        config = self._config().model_copy(update={"input_offset_s": 0.0})  # não herda a sincronia da sessão anterior
        self.session = session_store.create_session(base, name, config)
        self.reviewed = self.sent = False
        logging.getLogger("replayflow").info(f"atividade criada: {self.session.root}")
        self._refresh()

    def _open_session(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "Abrir atividade gravada", self.base_path)
        if not d:
            return
        try:
            self.session = session_store.load(Path(d))
        except ReplayFlowError as e:
            return self._error(str(e))
        self.name.setText(self.session.name)
        self.reviewed = self.sent = False
        self._load_config_into_form(self.session.config)
        self._refresh()

    def _import_dialog(self) -> None:
        """Vídeo gravado fora do app: nomeia a atividade, copia o vídeo e já processa."""
        home = portable_home()  # portátil: abre nas gravações da própria pasta; depois, na última usada
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
        self.name.setText(name.text().strip())
        self._new_session()
        assert self.session
        self.session = session_store.import_video(self.session, Path(path.text()))
        logging.getLogger("replayflow").info(f"vídeo importado: {path.text()}")
        self.auto_flow = True
        self._process()

    def _toggle_record(self) -> None:
        if self.capture or self.obs:
            return self._stop_record()
        assert self.session
        log = logging.getLogger("replayflow")
        try:
            self.obs = self._connect_obs()
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
                self.capture.start()  # antes do OBS: nenhum clique se perde enquanto o vídeo arranca
            offset = 0.0
            if self.obs:
                self.obs.ensure_display()
                self.obs.set_devices(self.mic.currentData(), self.out.currentData())
                log.info(f"microfone: {self.mic.currentText()} · som do computador: {self.out.currentText()}")
                if self.obs.prepare_tracks():
                    log.info("áudio em trilhas separadas: 1 = microfone (narração), 2 = som do computador")
                video_start = self.obs.start()
                if self.capture:  # sincronia: leva o relógio dos cliques/teclas para o relógio do vídeo
                    offset = self.capture.started_at - video_start
                    log.info(f"sincronia: vídeo começou {-offset:.2f}s depois da captura de input")
            self._set_input_offset(offset)
            log.info("gravação iniciada" + (" (OBS)" if self.obs else "") + (" + captura de input" if self.capture else ""))
        except ReplayFlowError as e:
            if self.capture:
                self.capture.stop()
            self.obs = self.capture = None
            self._error(str(e))
        self._refresh()

    def _set_input_offset(self, offset: float) -> None:
        assert self.session
        config = self.session.config.model_copy(update={"input_offset_s": round(offset, 3)})
        self.session = self.session.model_copy(update={"config": config})
        session_store.save(self.session)

    def _toggle_pause(self) -> None:
        log = logging.getLogger("replayflow")
        if self.paused:
            if self.obs:
                self.obs.resume()
            if self.capture:
                self.capture.resume()
            log.info("gravação retomada")
        else:
            if self.capture:
                self.capture.pause()
            if self.obs:
                self.obs.pause()
            log.info("gravação pausada: nada é gravado nem registrado até retomar")
        self.paused = not self.paused
        self._refresh()

    def _connect_obs(self):  # type: ignore[no-untyped-def]
        """Abre o OBS portátil se preciso e conecta; só pede senha se a conexão sem senha falhar."""
        from replayflow.services.obs import ObsRecorder, launch_portable_obs, portable_obs_exe

        if portable_obs_exe() and not self.obs_proc:
            logging.getLogger("replayflow").info("abrindo o OBS (minimizado na bandeja)…")
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

    def _stop_record(self) -> None:
        assert self.session
        if self.paused:
            self._toggle_pause()
        if self.capture:
            self.capture.stop()
        video = self.obs.stop() if self.obs else None
        self.capture = self.obs = None
        if video:
            self.session = session_store.import_video(self.session, video)
            self.reviewed = self.sent = False
            logging.getLogger("replayflow").info(f"gravação finalizada e importada: {video}")
            self._check_mic(self.session.root / "raw" / self.session.video_file)  # type: ignore[operator]
            if self.chk_auto.isChecked():
                logging.getLogger("replayflow").info("fluxo automático: processando e preparando o pacote para o Claude…")
                self.auto_flow = True
                self._process()
                return
        self._refresh()

    def _check_mic(self, video: Path) -> None:
        """Avisa na hora se a trilha da voz saiu muda (microfone errado, no mudo ou sem volume)."""
        try:
            level = ffmpeg.mean_volume(video, stream=0)
        except ReplayFlowError:
            return
        if level is not None and level < MIC_SILENT_DB:
            logging.getLogger("replayflow").warning(f"a trilha do microfone ficou praticamente muda ({level:.0f} dB)")
            self._error(f"O microfone \"{self.mic.currentText()}\" gravou silêncio ({level:.0f} dB).\n\n"
                        "Escolha outro em Configurações › Áudio e grave de novo, ou confira se ele não está no mudo.")

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
        self.stage_label.setText(f"{STATUS_ICON[status]} {STAGE_NAMES[stage]}")
        if self.session:
            self.session = session_store.load(self.session.root)
            self.progress.setValue(sum(st.status != StageStatus.PENDING for st in self.session.stages.values()))

    def _on_finished(self, session: Session) -> None:
        self._end_worker()
        logging.getLogger("replayflow").info("processamento concluído")
        if self.auto_flow:
            self.auto_flow = False
            logging.getLogger("replayflow").info("pacote pronto: siga o Passo a passo (revisar e enviar ao Claude)")

    def _on_failed(self, msg: str) -> None:
        self.auto_flow = False
        self._end_worker()
        self._error(msg)

    def _play_video(self) -> None:
        """Abre a gravação no player padrão do Windows."""
        if self.session and self.session.video_file:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.session.root / "raw" / self.session.video_file)))

    def _open_folder(self) -> None:
        if self.session:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.session.root)))

    def _copy_prompt(self) -> None:
        if self.session:
            QGuiApplication.clipboard().setText(exporter.build_prompt(self.session))
            logging.getLogger("replayflow").info("prompt copiado; anexe export/claude_package.md no Claude")

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
        if self.obs_proc:  # fecha o OBS só se foi o ReplayFlow que o abriu
            from replayflow.services.obs import stop_portable_obs

            stop_portable_obs(self.obs_proc)
        super().closeEvent(event)
