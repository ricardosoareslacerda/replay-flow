import sys

from PyQt6.QtWidgets import QApplication

from replayflow.ui.main_window import MainWindow


def run() -> None:
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())
