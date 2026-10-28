"""Identidade visual do ReplayFlow: grafite, âmbar (gravar) e verde-água (concluído)."""

BG = "#101418"
PANEL = "#171C22"
BORDER = "#252C34"
LINE = "#252C34"
FIELD = "#101418"
TEXT = "#E8ECEF"
TEXT_SOFT = "#AEB8C2"
MUTED = "#8A96A3"
AMBER = "#F2A541"
AMBER_TEXT = "#1A1206"
TEAL = "#2BB3A3"
TEAL_LIGHT = "#5FD3C4"
DANGER_BG = "#1F1513"
DANGER = "#F09A8B"
TRACK_OFF = "#323B45"

FONT = '"Segoe UI Variable Text", "Segoe UI", "Noto Sans", sans-serif'

STYLE = f"""
QMainWindow, QDialog {{ background: {BG}; }}
QWidget {{ color: {TEXT}; font-family: {FONT}; font-size: 13px; }}
QMenuBar {{ background: {BG}; color: {TEXT_SOFT}; border-bottom: 1px solid #20262D; padding: 4px 12px; }}
QMenuBar::item {{ padding: 6px 12px; border-radius: 6px; }}
QMenuBar::item:selected {{ background: {PANEL}; color: {TEXT}; }}
QMenu {{ background: {PANEL}; border: 1px solid {BORDER}; padding: 6px; }}
QMenu::item {{ padding: 7px 22px; border-radius: 6px; }}
QMenu::item:selected {{ background: #232A32; }}
QMenu::item:disabled {{ color: #56616D; }}
QFrame#Panel {{ background: {PANEL}; border: 1px solid {BORDER}; border-radius: 10px; }}
QLabel#Section {{ color: {MUTED}; font-size: 11.5px; font-weight: 600; letter-spacing: 0.6px; }}
QLabel#Title {{ font-size: 14px; font-weight: 600; }}
QLabel#Sub, QLabel#Muted {{ color: {MUTED}; font-size: 12px; }}
QLabel#Brand {{ font-size: 17px; font-weight: 700; }}
QLabel#Stat {{ font-size: 22px; font-weight: 700; }}
QFrame#Row {{ border-bottom: 1px solid #222930; }}
QLineEdit, QComboBox {{ background: {FIELD}; border: 1px solid #2A323B; border-radius: 8px; padding: 0 10px;
    min-height: 34px; color: {TEXT}; selection-background-color: {TEAL}; }}
QLineEdit:focus, QComboBox:focus {{ border-color: {AMBER}; }}
QLineEdit:disabled {{ color: {TEXT_SOFT}; }}
QComboBox QAbstractItemView {{ background: {PANEL}; border: 1px solid {BORDER}; selection-background-color: #2A3640; }}
QPushButton {{ background: {BG}; border: 1px solid #3A444F; border-radius: 9px; padding: 0 14px; min-height: 40px;
    color: {TEXT}; font-weight: 600; }}
QPushButton:hover {{ border-color: {TEXT_SOFT}; }}
QPushButton:disabled {{ color: #56616D; border-color: #2A323B; }}
QPushButton#Primary {{ background: {AMBER}; color: {AMBER_TEXT}; border: 0; font-size: 16px; font-weight: 700;
    min-height: 54px; }}
QPushButton#Primary:hover {{ background: #F6B866; }}
QPushButton#Primary:disabled {{ background: #3A3326; color: #8C7A5C; }}
QPushButton#Send {{ background: {TEAL}; color: #06201D; border: 0; font-size: 16px; font-weight: 700; min-height: 54px; }}
QPushButton#Send:hover {{ background: #3CC5B5; }}
QPushButton#Danger {{ background: {DANGER_BG}; color: {DANGER}; border: 1px solid #5B2C25; }}
QWidget#Segmented {{ background: {FIELD}; border: 1px solid #2A323B; border-radius: 8px; }}
QPushButton#SegButton {{ background: transparent; border: 0; border-radius: 6px; min-height: 30px; padding: 0 12px;
    color: {MUTED}; font-weight: 500; }}
QPushButton#SegButton:checked {{ background: #2A3640; color: {TEXT}; font-weight: 600; }}
QPushButton#SegButton:disabled {{ color: #4A5562; }}
QWidget#OverlayBar {{ background: {BG}; border: 1px solid #2A323B; border-radius: 10px; }}
QPushButton#OverlayTitle {{ border: 0; background: transparent; color: {TEXT}; font-weight: 600; }}
QPlainTextEdit {{ background: #0C1013; border: 1px solid {BORDER}; border-radius: 8px; color: {MUTED};
    font-family: Consolas, "Cascadia Mono", monospace; font-size: 11.5px; }}
QCheckBox {{ spacing: 8px; }}
QGroupBox {{ border: 1px solid {BORDER}; border-radius: 8px; margin-top: 14px; padding: 10px; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 10px; color: {MUTED}; }}
QToolTip {{ background: {PANEL}; color: {TEXT}; border: 1px solid {BORDER}; }}
QScrollBar:vertical {{ background: transparent; width: 10px; }}
QScrollBar::handle:vertical {{ background: #2A323B; border-radius: 4px; min-height: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
"""
