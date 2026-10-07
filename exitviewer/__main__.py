"""Einstiegspunkt: ``python -m exitviewer [Exportordner]``."""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtWidgets import QApplication

from . import APP_NAME, APP_VERSION


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv if argv is None else argv)

    app = QApplication(argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(APP_VERSION)
    app.setOrganizationName("Bank-Media")

    from .ui.main_window import MainWindow

    window = MainWindow()
    window.show()

    # Ein mitgegebener Pfad wird direkt geöffnet — praktisch für eine
    # Verknüpfung auf den Datenträger.
    start = next((a for a in argv[1:] if not a.startswith("-")), None)
    if start and Path(start).is_dir():
        window.open_folder(Path(start))

    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
