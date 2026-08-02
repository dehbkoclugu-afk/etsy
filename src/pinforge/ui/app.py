from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtWidgets import QApplication

from pinforge.observability import configure_logging
from pinforge.runtime import PinForgeRuntime, default_data_directory
from pinforge.security import SecretStore
from pinforge.ui.window import PinForgeWindow


STYLE = """
QWidget {
    background: #F4F0E6;
    color: #18201D;
    font-family: "DejaVu Sans";
    font-size: 14px;
}
QMainWindow, QTabWidget::pane { background: #F4F0E6; }
QFrame#sidebar, QFrame#editorPanel {
    background: #ECE7DB;
    border: 1px solid #D7D0C2;
    border-radius: 14px;
}
QLabel#appTitle { font-size: 26px; font-weight: 700; color: #173C35; }
QLabel#sectionTitle { font-size: 17px; font-weight: 700; color: #173C35; }
QLabel#muted { color: #66706B; }
QPushButton {
    min-height: 38px;
    padding: 0 16px;
    border: 1px solid #B8B1A4;
    border-radius: 9px;
    background: #FAF8F1;
    color: #173C35;
    font-weight: 600;
}
QPushButton:hover { background: #FFFFFF; border-color: #173C35; }
QPushButton:pressed { background: #E2DDD2; }
QPushButton:focus { border: 2px solid #C9855B; }
QPushButton#primaryButton {
    background: #173C35;
    color: #FAF8F1;
    border-color: #173C35;
}
QPushButton#primaryButton:hover { background: #24554A; }
QPushButton:disabled { background: #DCD7CC; color: #8A918D; border-color: #D0CABF; }
QLineEdit, QTextEdit {
    background: #FFFEFA;
    border: 1px solid #C8C1B5;
    border-radius: 8px;
    padding: 9px;
    selection-background-color: #C9855B;
}
QLineEdit:focus, QTextEdit:focus { border: 2px solid #173C35; }
QListWidget, QTableWidget {
    background: #FFFEFA;
    border: 1px solid #D7D0C2;
    border-radius: 9px;
    outline: 0;
}
QListWidget::item { padding: 11px; border-bottom: 1px solid #EEE9DF; }
QListWidget::item:selected { background: #DCE7E2; color: #173C35; }
QCheckBox { spacing: 8px; min-height: 30px; }
QCheckBox::indicator { width: 18px; height: 18px; }
QTabBar::tab { padding: 10px 22px; color: #54605A; }
QTabBar::tab:selected { color: #173C35; font-weight: 700; border-bottom: 3px solid #C9855B; }
QHeaderView::section { background: #E5DFD3; padding: 9px; border: 0; font-weight: 700; }
QStatusBar { background: #173C35; color: #FAF8F1; }
"""


def main(*, data_directory: str | Path | None = None) -> int:
    configure_logging()
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("PinForge")
    app.setOrganizationName("ECOVIA")
    app.setStyle("Fusion")
    app.setStyleSheet(STYLE)
    data_dir = data_directory or default_data_directory()
    runtime = PinForgeRuntime(data_dir, SecretStore())
    window = PinForgeWindow(runtime)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
