from __future__ import annotations

from pathlib import Path

import pytest

qt_widgets = pytest.importorskip(
    "PySide6.QtWidgets",
    reason="PySide6 sistem OpenGL/EGL kitaplıkları bu ortamda yok",
    exc_type=ImportError,
)
QApplication = qt_widgets.QApplication


def test_window_opens_offscreen(tmp_path: Path) -> None:
    from pinforge.runtime import PinForgeRuntime
    from pinforge.security import MemorySecretStore
    from pinforge.ui.window import PinForgeWindow

    app = QApplication.instance() or QApplication([])
    runtime = PinForgeRuntime(tmp_path, MemorySecretStore())
    window = PinForgeWindow(runtime)
    assert window.windowTitle() == "PinForge"
    assert window.tabs.count() == 3
    window.close()
    app.processEvents()
