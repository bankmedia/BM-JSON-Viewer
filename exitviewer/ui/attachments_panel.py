"""Anhänge des Datensatzes.

Maßgeblich ist das Dateisystem, nicht die JSON-Referenz: das Protokoll-PDF wird
im Datensatz gar nicht genannt, und ``"Anhänge": {}`` steht auch dann, wenn
Dateien im Ordner liegen. Was im Datensatz erwähnt ist, wird zusätzlich
markiert.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QLabel,
    QMessageBox,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..model import Record
from ..scan import group_attachments

_ROLE_PATH = Qt.ItemDataRole.UserRole


def human_size(count: int) -> str:
    size = float(count)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GB"


def open_with_default_app(path: Path) -> None:
    """Datei mit der Standardanwendung öffnen — rein lesend."""
    if sys.platform == "win32":
        os.startfile(str(path))  # noqa: S606 — gewollter Aufruf der Shell-Zuordnung
    elif sys.platform == "darwin":
        subprocess.run(["open", str(path)], check=False)
    else:
        subprocess.run(["xdg-open", str(path)], check=False)


class AttachmentsPanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)

        self.heading = QLabel("Anhänge")
        font = self.heading.font()
        font.setWeight(QFont.Weight.DemiBold)
        self.heading.setFont(font)
        self.heading.setStyleSheet("padding: 4px 2px;")

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Datei", "Größe"])
        self.tree.setRootIsDecorated(True)
        self.tree.setUniformRowHeights(True)
        self.tree.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.tree.setColumnWidth(0, 240)
        self.tree.itemActivated.connect(self._open_item)
        self.tree.itemDoubleClicked.connect(self._open_item)

        self.hint = QLabel()
        self.hint.setWordWrap(True)
        self.hint.setStyleSheet("color: #6b7789; padding: 2px;")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(4)
        layout.addWidget(self.heading)
        layout.addWidget(self.tree, 1)
        layout.addWidget(self.hint)

    def clear(self) -> None:
        self.tree.clear()
        self.hint.clear()
        self.heading.setText("Anhänge")

    def show_record(self, record: Record) -> None:
        self.tree.clear()
        groups = group_attachments(record.attachments)
        self.heading.setText(f"Anhänge ({len(groups)})")

        if not groups:
            self.hint.setText("Keine Dateien in diesem Datensatzordner.")
            return

        collapsed = 0
        for group in groups:
            newest = group.newest
            label = newest.label if not group.older else group.name
            item = QTreeWidgetItem(self.tree, [label, human_size(newest.size)])
            item.setData(0, _ROLE_PATH, str(newest.path))
            item.setToolTip(0, str(newest.path))
            if newest.referenced:
                item.setToolTip(0, f"{newest.path}\n\nIm Datensatz namentlich genannt.")
                font = item.font(0)
                font.setWeight(QFont.Weight.DemiBold)
                item.setFont(0, font)

            if group.older:
                collapsed += len(group.older)
                item.setText(0, f"{group.name}  ({len(group.versions)} Fassungen)")
                for old in group.versions:
                    stamp = old.version.strftime("%d.%m.%Y %H:%M") if old.version else old.name
                    child = QTreeWidgetItem(item, [stamp, human_size(old.size)])
                    child.setData(0, _ROLE_PATH, str(old.path))
                    child.setToolTip(0, str(old.path))

        total = sum(g.total_size for g in groups)
        text = f"Gesamt {human_size(total)}. Doppelklick öffnet mit der Standardanwendung."
        if collapsed:
            text = (
                f"{collapsed} ältere Fassungen zusammengefasst. " + text
            )
        self.hint.setText(text)

    def _open_item(self, item: QTreeWidgetItem, _column: int = 0) -> None:
        target = item.data(0, _ROLE_PATH)
        if not target:
            return
        path = Path(str(target))
        if not path.exists():
            QMessageBox.warning(
                self, "Datei nicht gefunden", f"Die Datei existiert nicht mehr:\n{path}"
            )
            return
        try:
            open_with_default_app(path)
        except OSError as exc:
            QMessageBox.warning(
                self, "Öffnen fehlgeschlagen", f"{path}\n\n{exc.strerror or exc}"
            )
