"""Navigation links: Anwendungsfilter, Volltextsuche, Datensatzbaum.

Baum und Trefferliste sind eine Bedieneinheit — dieselbe Auswahl, dieselbe
Filterung — und liegen deshalb in einem Widget mit umschaltbarer Ansicht statt
in zwei getrennten Panels.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QComboBox,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QStackedWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .. import fmt
from ..index import HIT_END, HIT_START, SearchIndex
from ..model import Record, ScanResult

ALL_APPS = "Alle Anwendungen"
_ROLE_KEY = Qt.ItemDataRole.UserRole


class Navigator(QWidget):
    record_selected = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._result: ScanResult | None = None
        self._index: SearchIndex | None = None
        self._duplicates: set[str] = set()

        self.app_filter = QComboBox()
        self.app_filter.addItem(ALL_APPS)
        self.app_filter.currentIndexChanged.connect(self._rebuild)

        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText("Volltextsuche …")
        self.search_box.setClearButtonEnabled(True)

        # Tastatureingaben nicht bei jedem Zeichen an die Suche durchreichen.
        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(180)
        self._debounce.timeout.connect(self._rebuild)
        self.search_box.textChanged.connect(lambda _: self._debounce.start())

        # Zwei Spalten: viele Datensätze tragen denselben Titel ("Test
        # 13.02.2013" kommt fünfmal vor) — erst das Datum macht sie
        # unterscheidbar.
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Datensatz", "Datum"])
        self.tree.setUniformRowHeights(True)
        self.tree.setColumnWidth(0, 210)
        self.tree.header().setStretchLastSection(False)
        self.tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.tree.header().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.tree.currentItemChanged.connect(self._on_tree_selection)

        self.hits = QListWidget()
        self.hits.setWordWrap(True)
        self.hits.currentItemChanged.connect(self._on_hit_selection)

        self.stack = QStackedWidget()
        self.stack.addWidget(self.tree)
        self.stack.addWidget(self.hits)

        self.status = QLabel()
        self.status.setStyleSheet("color: #6b7789; padding: 2px 4px;")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(4)
        layout.addWidget(self.app_filter)
        layout.addWidget(self.search_box)
        layout.addWidget(self.stack, 1)
        layout.addWidget(self.status)

    # -- Bestand ---------------------------------------------------------

    def set_result(self, result: ScanResult, index: SearchIndex) -> None:
        self._result = result
        self._index = index
        self._duplicates = {
            record.key for group in result.duplicate_ids().values() for record in group
        }

        self.app_filter.blockSignals(True)
        self.app_filter.clear()
        self.app_filter.addItem(ALL_APPS)
        for app in result.apps:
            self.app_filter.addItem(app)
        self.app_filter.setVisible(len(result.apps) > 1)
        self.app_filter.blockSignals(False)

        self._rebuild()

    def clear(self) -> None:
        self._result = None
        self._index = None
        self.tree.clear()
        self.hits.clear()
        self.status.clear()

    def current_app(self) -> str | None:
        app = self.app_filter.currentText()
        return None if app == ALL_APPS else app

    def focus_search(self) -> None:
        self.search_box.setFocus()
        self.search_box.selectAll()

    # -- Aufbau ----------------------------------------------------------

    def _rebuild(self) -> None:
        if self._result is None:
            return
        if self.search_box.text().strip():
            self._show_hits()
        else:
            self._show_tree()

    def _label(self, record: Record) -> str:
        label = record.title or record.folder.name
        if record.key in self._duplicates:
            label += "  ⧉"
        return label

    def _show_tree(self) -> None:
        self.stack.setCurrentWidget(self.tree)
        app = self.current_app()
        records = [r for r in self._result.records if app is None or r.app == app]

        self.tree.blockSignals(True)
        self.tree.clear()
        groups: dict[str, QTreeWidgetItem] = {}
        single_app = len({r.app for r in records}) <= 1

        for record in records:
            if single_app:
                parent = None
            else:
                parent = groups.get(record.app)
                if parent is None:
                    parent = QTreeWidgetItem(self.tree, [record.app])
                    font = parent.font(0)
                    font.setWeight(QFont.Weight.DemiBold)
                    parent.setFont(0, font)
                    groups[record.app] = parent

            item = QTreeWidgetItem(
                parent or self.tree, [self._label(record), fmt.format_date(record.date)]
            )
            item.setData(0, _ROLE_KEY, record.key)
            item.setToolTip(0, str(record.folder))
            item.setForeground(1, Qt.GlobalColor.gray)
            if record.error:
                item.setForeground(0, Qt.GlobalColor.darkRed)
                item.setToolTip(0, f"{record.folder}\n\n{record.error}")

        self.tree.expandAll()
        self.tree.blockSignals(False)

        total = len(self._result.records)
        shown = len(records)
        broken = sum(1 for r in records if r.error)
        text = f"{shown} von {total} Datensätzen"
        if broken:
            text += f" · {broken} mit Fehler"
        if self._duplicates:
            text += f" · ⧉ {len(self._duplicates)} doppelt vergebene IDs"
        self.status.setText(text)

    def _show_hits(self) -> None:
        self.stack.setCurrentWidget(self.hits)
        query = self.search_box.text().strip()
        hits = self._index.search(query, self.current_app()) if self._index else []

        self.hits.blockSignals(True)
        self.hits.clear()
        for hit in hits:
            snippet = hit.snippet.replace(HIT_START, "»").replace(HIT_END, "«")
            item = QListWidgetItem(f"{hit.title}\n{snippet}")
            item.setData(_ROLE_KEY, hit.key)
            item.setToolTip(hit.key)
            self.hits.addItem(item)
        self.hits.blockSignals(False)

        if hits:
            self.status.setText(f"{len(hits)} Treffer für „{query}“")
        else:
            self.status.setText(f"Keine Treffer für „{query}“")

    # -- Auswahl ---------------------------------------------------------

    def _emit(self, item: QListWidgetItem | QTreeWidgetItem | None, column: int | None) -> None:
        if item is None:
            return
        key = item.data(column, _ROLE_KEY) if column is not None else item.data(_ROLE_KEY)
        if key:
            self.record_selected.emit(str(key))

    def _on_tree_selection(self, current, _previous) -> None:
        self._emit(current, 0)

    def _on_hit_selection(self, current, _previous) -> None:
        self._emit(current, None)
