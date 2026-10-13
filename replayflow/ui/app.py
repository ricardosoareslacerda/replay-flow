import sys

from PyQt6.QtWidgets import QApplication

from replayflow.ui import theme
from replayflow.ui.main_window import MainWindow


def run() -> None:
    app = QApplication(sys.argv)
    app.setApplicationName("ReplayFlow")
    app.setStyle("Fusion")  # base neutra: o tema escuro vem da folha de estilo
    app.setStyleSheet(theme.STYLE)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())
