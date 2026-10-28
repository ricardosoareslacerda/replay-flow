"""Componentes visuais da janela: interruptor, botões segmentados, linha do tempo, prévia do vídeo e as
camadas transparentes por cima da tela (seleção de área, escolha de janela, moldura e destaque do mouse)."""

from __future__ import annotations

from collections.abc import Callable

from PyQt6.QtCore import QPoint, QPropertyAnimation, QRect, QRectF, QSize, Qt, QTimer, pyqtProperty, pyqtSignal
from PyQt6.QtGui import (
    QColor, QCursor, QFont, QFontMetrics, QGuiApplication, QKeyEvent, QMouseEvent, QPainter, QPainterPath, QPen,
    QPixmap,
)
from PyQt6.QtWidgets import QAbstractButton, QButtonGroup, QHBoxLayout, QPushButton, QWidget

from replayflow.services import capture_target
from replayflow.ui import theme

Region = capture_target.Region


# ---------- conversão entre pixels físicos (Win32/pynput/OBS) e lógicos (Qt) ----------

def _screen_origin(screen) -> QPoint:  # type: ignore[no-untyped-def]
    """Canto do monitor em pixels físicos: casa o QScreen com o monitor do Windows pelo tamanho e posição."""
    dpr = screen.devicePixelRatio()
    g = screen.geometry()
    size = (round(g.width() * dpr), round(g.height() * dpr))
    candidates = [m for m in capture_target.monitors() if m.region[2:] == size]
    if not candidates:
        return QPoint(round(g.x() * dpr), round(g.y() * dpr))
    best = min(candidates, key=lambda m: abs(m.region[0] - g.x() * dpr) + abs(m.region[1] - g.y() * dpr))
    return QPoint(best.region[0], best.region[1])


def to_logical(region: Region) -> tuple[QRect, object] | None:
    """Região física → retângulo lógico do Qt e o QScreen onde ela está."""
    cx, cy = region[0] + region[2] / 2, region[1] + region[3] / 2
    for screen in QGuiApplication.screens():
        o, dpr, g = _screen_origin(screen), screen.devicePixelRatio(), screen.geometry()
        if o.x() <= cx < o.x() + g.width() * dpr and o.y() <= cy < o.y() + g.height() * dpr:
            x = g.x() + (region[0] - o.x()) / dpr
            y = g.y() + (region[1] - o.y()) / dpr
            return QRect(round(x), round(y), round(region[2] / dpr), round(region[3] / dpr)), screen
    return None


def to_physical(rect: QRect, screen) -> Region:  # type: ignore[no-untyped-def]
    o, dpr, g = _screen_origin(screen), screen.devicePixelRatio(), screen.geometry()
    return (round(o.x() + (rect.x() - g.x()) * dpr), round(o.y() + (rect.y() - g.y()) * dpr),
            round(rect.width() * dpr), round(rect.height() * dpr))


# ---------- controles ----------

class Switch(QAbstractButton):
    """Interruptor liga/desliga (acessível: é um botão marcável, funciona com Tab e Espaço)."""

    def __init__(self, checked: bool = False, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setCheckable(True)
        self.setChecked(checked)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(46, 26)

    def sizeHint(self) -> QSize:
        return QSize(46, 26)

    def paintEvent(self, _event) -> None:  # type: ignore[no-untyped-def]
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        on = self.isChecked()
        track = QColor(theme.TEAL if on else theme.TRACK_OFF)
        if not self.isEnabled():
            track.setAlpha(110)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(track)
        p.drawRoundedRect(QRectF(0, 2, 46, 22), 11, 11)
        p.setBrush(QColor("#F4F6F8"))
        p.drawEllipse(QRectF(25 if on else 3, 4, 18, 18))
        if self.hasFocus():
            p.setPen(QPen(QColor(theme.AMBER), 1.5))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(QRectF(0.5, 1.5, 45, 23), 11, 11)


class Segmented(QWidget):
    """Grupo de botões exclusivos (Tela inteira | Área | Janela, HD | Full HD…)."""

    changed = pyqtSignal(int)

    def __init__(self, labels: list[str], current: int = 0, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Segmented")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(3, 3, 3, 3)
        layout.setSpacing(3)
        self.group = QButtonGroup(self)
        for i, label in enumerate(labels):
            b = QPushButton(label)
            b.setCheckable(True)
            b.setObjectName("SegButton")
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            bold = QFont(b.font())
            bold.setBold(True)
            b.setMinimumWidth(QFontMetrics(bold).horizontalAdvance(label) + 30)  # o selecionado fica em negrito
            self.group.addButton(b, i)
            layout.addWidget(b)
        self.group.button(max(0, min(current, len(labels) - 1))).setChecked(True)
        self.group.idClicked.connect(self.changed.emit)

    def index(self) -> int:
        return self.group.checkedId()

    def set_index(self, i: int) -> None:
        self.group.button(i).setChecked(True)


class Timeline(QWidget):
    """Passo a passo como a barra de um player: marcadores Nomear → Gravar → Processar → Revisar e enviar."""

    LABELS = ["Nomear", "Gravar", "Processar", "Revisar e enviar"]

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.step, self.live = 1, False
        self.setMinimumHeight(48)

    def set_step(self, step: int, live: bool = False) -> None:
        self.step, self.live = step, live
        labels = ", ".join(f"{i + 1} {n}" + (" (atual)" if i + 1 == step else "") for i, n in enumerate(self.LABELS))
        self.setAccessibleName("Passo a passo: " + labels)
        self.update()

    def paintEvent(self, _event) -> None:  # type: ignore[no-untyped-def]
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, n, y = self.width(), len(self.LABELS), 12
        xs = [12 + i * (w - 24) / (n - 1) for i in range(n)]
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(theme.LINE))
        p.drawRoundedRect(QRectF(12, y - 2, w - 24, 4), 2, 2)
        done_to = xs[min(self.step, n) - 1]
        p.setBrush(QColor(theme.AMBER if self.live else theme.TEAL_LIGHT))
        p.drawRoundedRect(QRectF(12, y - 2, max(0, done_to - 12), 4), 2, 2)
        font = QFont(self.font())
        font.setPointSizeF(9)
        for i, x in enumerate(xs):
            current = i + 1 == self.step
            if current:
                p.setBrush(QColor(242, 165, 65, 60))
                p.drawEllipse(QRectF(x - 14, y - 14, 28, 28))
                p.setBrush(QColor(theme.AMBER))
                p.drawEllipse(QRectF(x - 9, y - 9, 18, 18))
            elif i + 1 < self.step:
                p.setBrush(QColor(theme.TEAL_LIGHT))
                p.drawEllipse(QRectF(x - 9, y - 9, 18, 18))
            else:
                p.setBrush(QColor(theme.BG))
                p.setPen(QPen(QColor("#3A444F"), 2))
                p.drawEllipse(QRectF(x - 8, y - 8, 16, 16))
                p.setPen(Qt.PenStyle.NoPen)
            font.setBold(current)
            p.setFont(font)
            p.setPen(QColor(theme.AMBER if current else theme.MUTED if i + 1 > self.step else theme.TEXT_SOFT))
            align = Qt.AlignmentFlag.AlignLeft if i == 0 else Qt.AlignmentFlag.AlignRight if i == n - 1 \
                else Qt.AlignmentFlag.AlignHCenter
            box = QRectF(x - (0 if i == 0 else 140 if i == n - 1 else 70), y + 14, 140, 18)
            p.drawText(box, int(align | Qt.AlignmentFlag.AlignVCenter), self.LABELS[i])
            p.setPen(Qt.PenStyle.NoPen)


class Preview(QWidget):
    """Miniatura do que vai para o vídeo: imagem atual da área, moldura, anel do mouse, webcam e cronômetro."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMinimumSize(420, 236)
        self.pixmap: QPixmap | None = None
        self.mouse = self.webcam = self.recording = False
        self.webcam_corner = "bottom-right"
        self.webcam_frame: QPixmap | None = None  # imagem ao vivo da câmera (Qt antes de gravar, OBS durante)
        self.timer_text = ""
        self.setAccessibleName("Prévia do vídeo")

    def grab(self, region: Region | None) -> None:
        """Atualiza a imagem da área (só leitura da tela, local; nada é salvo)."""
        found = to_logical(region) if region else None
        if not found:
            self.pixmap = None
        else:
            rect, screen = found
            g = screen.geometry()
            self.pixmap = screen.grabWindow(0, rect.x() - g.x(), rect.y() - g.y(), rect.width(), rect.height())
        self.update()

    def paintEvent(self, _event) -> None:  # type: ignore[no-untyped-def]
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        outer = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        p.setPen(QPen(QColor(theme.AMBER if self.recording else theme.BORDER), 2 if self.recording else 1))
        p.setBrush(QColor("#0B0E11"))
        p.drawRoundedRect(outer, 8, 8)
        area = outer.adjusted(10, 10, -10, -10)
        if self.pixmap and not self.pixmap.isNull():
            pw, ph = self.pixmap.width(), self.pixmap.height()
            s = min(area.width() / pw, area.height() / ph)
            target = QRectF(area.x() + (area.width() - pw * s) / 2, area.y() + (area.height() - ph * s) / 2, pw * s, ph * s)
            p.drawPixmap(target, self.pixmap, QRectF(self.pixmap.rect()))
        else:
            target = area
            p.setPen(QColor(theme.MUTED))
            p.drawText(area, int(Qt.AlignmentFlag.AlignCenter), "Escolha a área de captura")
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(QColor(theme.AMBER), 2))
        p.drawRect(target)
        if self.mouse:
            p.setPen(QPen(QColor(224, 60, 50, 230), 3))
            p.drawEllipse(target.center(), 13, 13)
        if self.webcam:
            r = min(target.width(), target.height()) * 0.16
            right, bottom = self.webcam_corner.endswith("right"), self.webcam_corner.startswith("bottom")
            cx = target.right() - r - 10 if right else target.left() + r + 10
            cy = target.bottom() - r - 10 if bottom else target.top() + r + 10
            circle = QRectF(cx - r, cy - r, 2 * r, 2 * r)
            frame = self.webcam_frame
            if frame is not None and not frame.isNull():
                clip = QPainterPath()
                clip.addEllipse(circle)
                p.save()
                p.setClipPath(clip)
                s = max(circle.width() / frame.width(), circle.height() / frame.height())
                w, h = frame.width() * s, frame.height() * s
                p.drawPixmap(QRectF(cx - w / 2, cy - h / 2, w, h), frame, QRectF(frame.rect()))
                p.restore()
            else:
                p.setBrush(QColor("#2B3A44"))
                p.drawEllipse(circle)
                p.setPen(QColor(theme.TEXT_SOFT))
                p.drawText(circle, int(Qt.AlignmentFlag.AlignCenter), "câmera…")
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(QColor(theme.TEAL_LIGHT), 3))
            p.drawEllipse(circle)
        if self.timer_text:
            font = QFont(self.font())
            font.setPointSize(20)
            font.setBold(True)
            p.setFont(font)
            box = QRectF(target.left() + 12, target.top() + 10, 150, 42)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(16, 20, 24, 220))
            p.drawRoundedRect(box, 8, 8)
            p.setBrush(QColor("#E0533D"))
            p.drawEllipse(QRectF(box.left() + 12, box.center().y() - 5, 10, 10))
            p.setPen(QColor(theme.TEXT))
            p.drawText(box.adjusted(30, 0, 0, 0), int(Qt.AlignmentFlag.AlignVCenter), self.timer_text)


# ---------- camadas por cima da tela ----------

class _Overlay(QWidget):
    """Janela sem borda, sempre por cima, cobrindo o monitor onde está o cursor."""

    def __init__(self) -> None:
        super().__init__(None, Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
                         | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setMouseTracking(True)
        self.screen_ = QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
        self.setGeometry(self.screen_.geometry())
        self.setCursor(Qt.CursorShape.CrossCursor)

    def show_over(self) -> None:
        self.show()
        self.raise_()
        self.activateWindow()
        self.setFocus()


class AreaSelector(_Overlay):
    """Escurece o monitor e deixa o usuário arrastar o retângulo a gravar (cantos ajustáveis)."""

    RATIOS = [("Livre", None), ("16:9", 16 / 9), ("4:3", 4 / 3)]

    def __init__(self, on_done: Callable[[Region | None], None]) -> None:
        super().__init__()
        self.on_done = on_done
        self.rect_ = QRect()
        self.ratio: float | None = None
        self.mode = ""  # "new", "move" ou o canto sendo arrastado ("tl", "tr", "bl", "br")
        self.anchor = QPoint()
        self.bar = QWidget(self)
        self.bar.setObjectName("OverlayBar")
        bl = QHBoxLayout(self.bar)
        bl.setContentsMargins(12, 8, 8, 8)
        bl.setSpacing(6)
        title = QPushButton("Desenhe a área que vai para o vídeo")
        title.setObjectName("OverlayTitle")
        title.setEnabled(False)
        bl.addWidget(title)
        self.ratio_group = Segmented([r[0] for r in self.RATIOS])
        self.ratio_group.changed.connect(self._set_ratio)
        bl.addWidget(self.ratio_group)
        cancel = QPushButton("Cancelar (Esc)")
        cancel.clicked.connect(lambda: self._finish(False))
        ok = QPushButton("Usar esta área (Enter)")
        ok.setObjectName("Primary")
        ok.clicked.connect(lambda: self._finish(True))
        bl.addWidget(cancel)
        bl.addWidget(ok)
        self.bar.adjustSize()
        self.bar.move((self.width() - self.bar.width()) // 2, 24)

    def _set_ratio(self, i: int) -> None:
        self.ratio = self.RATIOS[i][1]
        if self.ratio and not self.rect_.isNull():
            self.rect_.setHeight(round(self.rect_.width() / self.ratio))
        self.update()

    def _corner_at(self, pos: QPoint) -> str:
        r = self.rect_
        for name, c in (("tl", r.topLeft()), ("tr", r.topRight()), ("bl", r.bottomLeft()), ("br", r.bottomRight())):
            if (c - pos).manhattanLength() < 18:
                return name
        return ""

    def mousePressEvent(self, e: QMouseEvent) -> None:
        pos = e.position().toPoint()
        corner = self._corner_at(pos) if not self.rect_.isNull() else ""
        if corner:
            self.mode = corner
        elif not self.rect_.isNull() and self.rect_.contains(pos):
            self.mode, self.anchor = "move", pos - self.rect_.topLeft()
        else:
            self.mode, self.anchor = "new", pos
            self.rect_ = QRect(pos, pos)
        self.update()

    def mouseMoveEvent(self, e: QMouseEvent) -> None:
        pos = e.position().toPoint()
        if not self.mode:
            corner = self._corner_at(pos) if not self.rect_.isNull() else ""
            self.setCursor(Qt.CursorShape.SizeFDiagCursor if corner in ("tl", "br") else
                           Qt.CursorShape.SizeBDiagCursor if corner else
                           Qt.CursorShape.SizeAllCursor if self.rect_.contains(pos) else Qt.CursorShape.CrossCursor)
            return
        r = QRect(self.rect_)
        if self.mode == "new":
            r = QRect(self.anchor, pos).normalized()
        elif self.mode == "move":
            r.moveTopLeft(pos - self.anchor)
        else:
            if "l" in self.mode:
                r.setLeft(pos.x())
            if "r" in self.mode:
                r.setRight(pos.x())
            if "t" in self.mode:
                r.setTop(pos.y())
            if "b" in self.mode:
                r.setBottom(pos.y())
            r = r.normalized()
        if self.ratio and self.mode != "move":
            r.setHeight(round(r.width() / self.ratio))
        self.rect_ = r.intersected(self.rect())
        self.update()

    def mouseReleaseEvent(self, _e: QMouseEvent) -> None:
        self.mode = ""

    def keyPressEvent(self, e: QKeyEvent) -> None:
        if e.key() == Qt.Key.Key_Escape:
            self._finish(False)
        elif e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self._finish(True)

    def _finish(self, ok: bool) -> None:
        region = None
        if ok and self.rect_.width() >= 64 and self.rect_.height() >= 64:
            region = to_physical(self.rect_.translated(self.geometry().topLeft()), self.screen_)
        self.close()
        self.on_done(region)

    def paintEvent(self, _event) -> None:  # type: ignore[no-untyped-def]
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        dim = QColor(12, 16, 20, 165)
        full = self.rect()
        if self.rect_.isNull():
            p.fillRect(full, dim)
            return
        r = self.rect_
        p.fillRect(QRect(0, 0, full.width(), r.top()), dim)
        p.fillRect(QRect(0, r.bottom() + 1, full.width(), full.height() - r.bottom()), dim)
        p.fillRect(QRect(0, r.top(), r.left(), r.height()), dim)
        p.fillRect(QRect(r.right() + 1, r.top(), full.width() - r.right(), r.height()), dim)
        p.setPen(QPen(QColor(theme.AMBER), 1, Qt.PenStyle.DashLine))
        p.drawRect(r)
        p.setPen(QPen(QColor(theme.AMBER), 4))
        L = 28
        for c, dx, dy in ((r.topLeft(), 1, 1), (r.topRight(), -1, 1), (r.bottomLeft(), 1, -1), (r.bottomRight(), -1, -1)):
            p.drawLine(c, c + QPoint(dx * L, 0))
            p.drawLine(c, c + QPoint(0, dy * L))
        phys = to_physical(r, self.screen_)
        label = f"{phys[2]} × {phys[3]}"
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(16, 20, 24, 225))
        box = QRectF(r.right() - 110, r.bottom() - 34, 100, 26)
        p.drawRoundedRect(box, 6, 6)
        p.setPen(QColor(theme.AMBER))
        p.drawText(box, int(Qt.AlignmentFlag.AlignCenter), label)


class WindowPicker(_Overlay):
    """Passe o mouse: a janela embaixo fica contornada; clique para escolher. Esc cancela."""

    def __init__(self, on_done: Callable[[capture_target.Target | None], None]) -> None:
        super().__init__()
        self.on_done = on_done
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.windows = capture_target.windows()  # ordem de cima para baixo (z-order)
        self.hover: capture_target.Target | None = None

    def _at(self, pos: QPoint) -> capture_target.Target | None:
        x, y = to_physical(QRect(pos + self.geometry().topLeft(), QSize(1, 1)), self.screen_)[:2]
        for t in self.windows:
            left, top, w, h = t.region
            if left <= x < left + w and top <= y < top + h:
                return t
        return None

    def mouseMoveEvent(self, e: QMouseEvent) -> None:
        hit = self._at(e.position().toPoint())
        if hit is not self.hover:
            self.hover = hit
            self.update()

    def mousePressEvent(self, e: QMouseEvent) -> None:
        chosen = self._at(e.position().toPoint())
        self.close()
        self.on_done(chosen)

    def keyPressEvent(self, e: QKeyEvent) -> None:
        if e.key() == Qt.Key.Key_Escape:
            self.close()
            self.on_done(None)

    def paintEvent(self, _event) -> None:  # type: ignore[no-untyped-def]
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(12, 16, 20, 70))
        p.setPen(QColor(theme.TEXT))
        p.setBrush(QColor(16, 20, 24, 225))
        tip = QRectF(self.width() / 2 - 230, 24, 460, 36)
        p.drawRoundedRect(tip, 8, 8)
        p.drawText(tip, int(Qt.AlignmentFlag.AlignCenter), "Clique na janela da atividade · Esc cancela")
        if self.hover:
            found = to_logical(self.hover.region)
            if found:
                r = found[0].translated(-self.geometry().topLeft())
                p.fillRect(r, QColor(242, 165, 65, 40))
                p.setPen(QPen(QColor(theme.AMBER), 4))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawRect(r)
                p.setPen(QColor(theme.TEXT))
                p.drawText(QRectF(r.left() + 10, r.top() + 10, r.width() - 20, 24), self.hover.label)


class _PassThrough(QWidget):
    """Camada que não recebe cliques (o usuário trabalha "através" dela)."""

    def __init__(self) -> None:
        super().__init__(None, Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
                         | Qt.WindowType.Tool | Qt.WindowType.WindowTransparentForInput
                         | Qt.WindowType.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)


class RegionOutline(_PassThrough):
    """Moldura âmbar desenhada POR FORA da área gravada: mostra o que entra no vídeo sem aparecer nele."""

    PAD = 5

    def show_for(self, region: Region) -> None:
        found = to_logical(region)
        if not found:
            return
        self.setGeometry(found[0].adjusted(-self.PAD, -self.PAD, self.PAD, self.PAD))
        self.show()

    def paintEvent(self, _event) -> None:  # type: ignore[no-untyped-def]
        p = QPainter(self)
        p.setPen(QPen(QColor(theme.AMBER), 3))
        p.drawRect(QRectF(self.rect()).adjusted(1.5, 1.5, -1.5, -1.5))


class MouseHighlight(_PassThrough):
    """Anel vermelho que segue o cursor e um pulso a cada clique (ligado só se o usuário pedir)."""

    SIZE = 90

    def __init__(self) -> None:
        super().__init__()
        self.resize(self.SIZE, self.SIZE)
        self._pulse = 0.0
        self.follow = QTimer(self)
        self.follow.timeout.connect(self._move)
        self.anim = QPropertyAnimation(self, b"pulse", self)
        self.anim.setDuration(380)
        self.anim.setStartValue(0.0)
        self.anim.setEndValue(1.0)

    def get_pulse(self) -> float:
        return self._pulse

    def set_pulse(self, v: float) -> None:
        self._pulse = v
        self.update()

    pulse = pyqtProperty(float, fget=get_pulse, fset=set_pulse)

    def start(self) -> None:
        self._move()
        self.show()
        self.follow.start(15)

    def stop(self) -> None:
        self.follow.stop()
        self.hide()

    def clicked(self) -> None:
        self.anim.stop()
        self.anim.start()

    def _move(self) -> None:
        pos = QCursor.pos()
        self.move(pos.x() - self.SIZE // 2, pos.y() - self.SIZE // 2)

    def paintEvent(self, _event) -> None:  # type: ignore[no-untyped-def]
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        c = QRectF(self.rect()).center()
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(QColor(224, 60, 50, 215), 3.5))
        p.drawEllipse(c, 17, 17)
        if 0 < self._pulse < 1:
            p.setPen(QPen(QColor(224, 60, 50, int(200 * (1 - self._pulse))), 3))
            r = 17 + 24 * self._pulse
            p.drawEllipse(c, r, r)


class AnnotationLayer(QWidget):
    """Caneta de apontamento por cima do monitor gravado (como em apresentações).

    Com a caneta ligada (F8), o mouse risca a tela do ponto A ao ponto B e os traços aparecem no vídeo;
    os cliques não chegam aos programas embaixo. Desligando (F8 de novo), os traços somem em ~1 s e o
    mouse volta a funcionar normalmente. Botão direito limpa os traços.
    """

    stroke_done = pyqtSignal(object)  # lista de pontos físicos [(x, y), ...] de um traço

    COLOR = QColor(224, 60, 50)

    def __init__(self) -> None:
        super().__init__(None, Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
                         | Qt.WindowType.Tool | Qt.WindowType.WindowTransparentForInput)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.strokes: list[list[QPoint]] = []
        self.current: list[QPoint] | None = None
        self.drawing = False
        self.alpha = 1.0
        self.screen_ = QGuiApplication.primaryScreen()
        self.fade = QTimer(self)
        self.fade.timeout.connect(self._fade_step)

    def attach(self, region: Region) -> None:
        """Cobre o monitor da área gravada."""
        found = to_logical(region)
        if found:
            self.screen_ = found[1]
        self.setGeometry(self.screen_.geometry())

    def set_drawing(self, on: bool) -> None:
        self.drawing = on
        self.setWindowFlag(Qt.WindowType.WindowTransparentForInput, not on)
        if on:
            self.fade.stop()
            self.alpha = 1.0
            self.setCursor(Qt.CursorShape.CrossCursor)
        else:
            self.current = None
            self.fade.start(40)
        self.show()
        if on:
            self.raise_()
        self.update()

    def clear(self) -> None:
        self.strokes.clear()
        self.current = None
        self.update()

    def stop(self) -> None:
        self.drawing = False
        self.fade.stop()
        self.clear()
        self.hide()

    def _fade_step(self) -> None:
        self.alpha -= 0.04
        if self.alpha <= 0:
            self.fade.stop()
            self.clear()
            self.alpha = 1.0
            if not self.drawing:
                self.hide()
        self.update()

    def mousePressEvent(self, e: QMouseEvent) -> None:
        if e.button() == Qt.MouseButton.RightButton:
            return self.clear()
        self.current = [e.position().toPoint()]
        self.strokes.append(self.current)

    def mouseMoveEvent(self, e: QMouseEvent) -> None:
        if self.current is not None:
            self.current.append(e.position().toPoint())
            self.update()

    def mouseReleaseEvent(self, _e: QMouseEvent) -> None:
        if self.current and len(self.current) > 1:
            origin = self.geometry().topLeft()
            phys = [to_physical(QRect(pt + origin, QSize(1, 1)), self.screen_)[:2] for pt in self.current]
            self.stroke_done.emit(phys)
        self.current = None

    def paintEvent(self, _event) -> None:  # type: ignore[no-untyped-def]
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self.drawing:
            p.fillRect(self.rect(), QColor(0, 0, 0, 1))  # quase invisível, mas recebe o mouse
        color = QColor(self.COLOR)
        color.setAlphaF(max(0.0, min(1.0, self.alpha)))
        p.setPen(QPen(color, 5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        for stroke in self.strokes:
            if len(stroke) > 1:
                path = QPainterPath(stroke[0].toPointF())
                for pt in stroke[1:]:
                    path.lineTo(pt.toPointF())
                p.drawPath(path)
        if self.drawing:
            tip = QRectF(self.width() / 2 - 250, 14, 500, 34)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(16, 20, 24, 225))
            p.drawRoundedRect(tip, 8, 8)
            p.setPen(QColor(theme.TEXT))
            p.drawText(tip, int(Qt.AlignmentFlag.AlignCenter), "Caneta ligada · F8 volta ao mouse · botão direito limpa")


class TimerBadge(QWidget):
    """Cronômetro flutuante no canto do monitor gravado: visível mesmo com o ReplayFlow minimizado.
    Fica fora da área gravada quando há espaço (no modo Tela inteira ele aparece no vídeo)."""

    def __init__(self) -> None:
        super().__init__(None, Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
                         | Qt.WindowType.Tool | Qt.WindowType.WindowTransparentForInput
                         | Qt.WindowType.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.text = "00:00"
        self.paused = False
        self.resize(150, 44)

    def show_at(self, region: Region) -> None:
        found = to_logical(region)
        screen = found[1] if found else QGuiApplication.primaryScreen()
        g = screen.availableGeometry()
        self.move(g.right() - self.width() - 16, g.top() + 16)
        self.show()

    def set_time(self, text: str, paused: bool) -> None:
        self.text, self.paused = text, paused
        self.update()

    def paintEvent(self, _event) -> None:  # type: ignore[no-untyped-def]
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(16, 20, 24, 230))
        p.drawRoundedRect(QRectF(self.rect()), 10, 10)
        p.setBrush(QColor(theme.AMBER if self.paused else "#E0533D"))
        p.drawEllipse(QRectF(14, 15, 14, 14))
        font = QFont(self.font())
        font.setPointSize(15)
        font.setBold(True)
        p.setFont(font)
        p.setPen(QColor(theme.TEXT))
        p.drawText(QRectF(36, 0, self.width() - 40, self.height()), int(Qt.AlignmentFlag.AlignVCenter), self.text)
