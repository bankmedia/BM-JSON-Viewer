"""Hauptfenster: Navigation | Akte | Anhänge."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSettings, Qt, QThread, Signal
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QFileDialog,
    QMainWindow,
    QMessageBox,
    QProgressDialog,
    QSplitter,
    QWidget,
)

from .. import APP_NAME, APP_VERSION
from ..index import SearchIndex
from ..model import Record, ScanResult
from ..render import RenderOptions, inline_images, render_record
from ..scan import scan
from .attachments_panel import AttachmentsPanel
from .document_view import DocumentView
from .navigator import Navigator


class ScanWorker(QThread):
    """Scan und Indexaufbau außerhalb des GUI-Threads.

    Die SQLite-Verbindung bleibt in diesem Thread und wird am Ende geschlossen;
    das Hauptfenster öffnet sich anschließend eine eigene. SQLite-Verbindungen
    sind an ihren Erzeuger-Thread gebunden.
    """

    progress = Signal(int, int, str)
    done = Signal(object)
    failed = Signal(str)

    def __init__(self, root: Path, force: bool, parent=None):
        super().__init__(parent)
        self.root = root
        self.force = force
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    def run(self) -> None:  # pragma: no cover — Thread-Einstieg
        index = SearchIndex(self.root)
        try:
            if not self.force and index.is_current():
                self.progress.emit(0, 0, "Zwischenspeicher wird geladen …")
                result = index.load()
            else:
                result = scan(
                    self.root,
                    progress=lambda done, total, label: self.progress.emit(done, total, label),
                    cancelled=lambda: self._cancelled,
                )
                if self._cancelled:
                    self.failed.emit("Abgebrochen.")
                    return
                self.progress.emit(0, 0, "Suchindex wird aufgebaut …")
                index.build(result)
            self.done.emit(result)
        except Exception as exc:  # pragma: no cover — Sicherheitsnetz
            self.failed.emit(f"{type(exc).__name__}: {exc}")
        finally:
            index.close()


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.resize(1440, 900)

        self._settings = QSettings("Bank-Media", "ExitExportViewer")
        self._records: dict[str, Record] = {}
        self._current: Record | None = None
        self._index: SearchIndex | None = None
        self._worker: ScanWorker | None = None
        self._progress: QProgressDialog | None = None
        self._options = RenderOptions()

        self.navigator = Navigator()
        self.navigator.record_selected.connect(self._show_record)

        self.document = DocumentView()
        self.document.expand_requested.connect(self._expand)

        self.attachments = AttachmentsPanel()

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self.navigator)
        splitter.addWidget(self.document)
        splitter.addWidget(self.attachments)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setStretchFactor(2, 0)
        splitter.setSizes([330, 780, 330])
        self.setCentralWidget(splitter)

        self._build_actions()
        self.statusBar().showMessage("Bereit. Öffnen Sie einen Exportordner.")
        self.document.show_message(
            "Kein Export geöffnet. Über <b>Datei → Exportordner öffnen …</b> "
            "wählen Sie das Wurzelverzeichnis des Datenexports."
        )

        last = self._settings.value("last_root", "")
        if last and Path(str(last)).is_dir():
            self.statusBar().showMessage(f"Zuletzt geöffnet: {last}")

    # -- Menü ------------------------------------------------------------

    def _build_actions(self) -> None:
        open_action = QAction("Exportordner &öffnen …", self)
        open_action.setShortcut(QKeySequence.StandardKey.Open)
        open_action.triggered.connect(self._choose_folder)

        reload_action = QAction("Neu &einlesen", self)
        reload_action.setShortcut(QKeySequence.StandardKey.Refresh)
        reload_action.triggered.connect(lambda: self._load(self._current_root(), force=True))

        quit_action = QAction("&Beenden", self)
        quit_action.setShortcut(QKeySequence.StandardKey.Quit)
        quit_action.triggered.connect(self.close)

        self.empty_action = QAction("&Leere Felder anzeigen", self, checkable=True)
        self.empty_action.setShortcut("Ctrl+L")
        self.empty_action.toggled.connect(self._toggle_empty)

        self.technical_action = QAction("&Technische Felder anzeigen", self, checkable=True)
        self.technical_action.setShortcut("Ctrl+T")
        self.technical_action.toggled.connect(self._toggle_technical)

        search_action = QAction("&Suche", self)
        search_action.setShortcut(QKeySequence.StandardKey.Find)
        search_action.triggered.connect(self.navigator.focus_search)

        about_action = QAction("Ü&ber", self)
        about_action.triggered.connect(self._about)

        file_menu = self.menuBar().addMenu("&Datei")
        file_menu.addAction(open_action)
        file_menu.addAction(reload_action)
        file_menu.addSeparator()
        file_menu.addAction(quit_action)

        view_menu = self.menuBar().addMenu("&Ansicht")
        view_menu.addAction(search_action)
        view_menu.addSeparator()
        view_menu.addAction(self.empty_action)
        view_menu.addAction(self.technical_action)

        help_menu = self.menuBar().addMenu("&Hilfe")
        help_menu.addAction(about_action)

        toolbar = self.addToolBar("Werkzeuge")
        toolbar.setMovable(False)
        toolbar.addAction(open_action)
        toolbar.addAction(reload_action)
        toolbar.addSeparator()
        toolbar.addAction(self.empty_action)
        toolbar.addAction(self.technical_action)

    def _about(self) -> None:
        QMessageBox.about(
            self,
            f"Über {APP_NAME}",
            f"<b>{APP_NAME}</b> {APP_VERSION}<br><br>"
            "Lesende Ansicht für JSON-Datenexporte abgekündigter Anwendungen."
            "<br><br>Der Export wird ausschließlich gelesen. Der Suchindex "
            "liegt unter <tt>%LOCALAPPDATA%</tt>. Die Anwendung stellt keine "
            "Netzwerkverbindungen her.",
        )

    # -- Laden -----------------------------------------------------------

    def _current_root(self) -> Path | None:
        value = self._settings.value("last_root", "")
        return Path(str(value)) if value else None

    def open_folder(self, root: Path, force: bool = False) -> None:
        """Export öffnen — auch von außen aufrufbar (Startparameter)."""
        self._load(root, force=force)

    def _choose_folder(self) -> None:
        start = self._settings.value("last_root", "")
        chosen = QFileDialog.getExistingDirectory(
            self, "Exportordner auswählen", str(start or "")
        )
        if chosen:
            self._load(Path(chosen), force=False)

    def _load(self, root: Path | None, force: bool) -> None:
        if root is None:
            self._choose_folder()
            return
        if not root.is_dir():
            QMessageBox.warning(self, "Ordner fehlt", f"Nicht gefunden:\n{root}")
            return
        if self._worker is not None and self._worker.isRunning():
            return

        self._settings.setValue("last_root", str(root))
        self.navigator.clear()
        self.attachments.clear()
        self.document.show_message("Export wird eingelesen …")

        self._progress = QProgressDialog("Export wird eingelesen …", "Abbrechen", 0, 0, self)
        self._progress.setWindowTitle(APP_NAME)
        self._progress.setWindowModality(Qt.WindowModality.WindowModal)
        self._progress.setMinimumDuration(300)
        self._progress.setAutoClose(False)
        self._progress.setAutoReset(False)

        self._worker = ScanWorker(root, force, self)
        self._progress.canceled.connect(self._worker.cancel)
        self._worker.progress.connect(self._on_progress)
        self._worker.done.connect(self._on_done)
        self._worker.failed.connect(self._on_failed)
        self._worker.start()

    def _on_progress(self, done: int, total: int, label: str) -> None:
        if self._progress is None:
            return
        self._progress.setMaximum(total)
        self._progress.setValue(done)
        self._progress.setLabelText(label if total == 0 else f"{done} / {total} — {label}")

    def _finish_progress(self) -> None:
        if self._progress is not None:
            self._progress.close()
            self._progress = None

    def _on_failed(self, message: str) -> None:
        self._finish_progress()
        self.document.show_message(f"Der Export konnte nicht gelesen werden: {message}")
        self.statusBar().showMessage(message)

    def _on_done(self, result: ScanResult) -> None:
        self._finish_progress()
        self._records = {record.key: record for record in result.records}
        if self._index is not None:
            self._index.close()
        self._index = SearchIndex(result.root)
        self.navigator.set_result(result, self._index)

        self.setWindowTitle(f"{APP_NAME} — {result.root}")
        attachments = sum(len(r.attachments) for r in result.records)
        message = (
            f"{len(result.records)} Datensätze · {len(result.apps)} Anwendung(en) · "
            f"{attachments} Dateien"
        )
        if result.problems:
            message += f" · {len(result.problems)} Hinweise"
        self.statusBar().showMessage(message)

        if result.records:
            self.document.show_message("Wählen Sie links einen Datensatz.")
        else:
            self.document.show_message(
                "In diesem Ordner wurden keine Datensätze gefunden. Erwartet wird "
                "ein Verzeichnis mit Unterordnern, die je eine JSON-Datei enthalten."
            )

    # -- Anzeige ---------------------------------------------------------

    def _show_record(self, key: str) -> None:
        record = self._records.get(key)
        if record is None:
            return
        self._current = record
        self._options.expanded.clear()
        self._rerender(keep_position=False)
        self.attachments.show_record(record)

    def _rerender(self, keep_position: bool = True) -> None:
        if self._current is None:
            return
        self.document.show_html(
            render_record(self._current, self._options),
            images=inline_images(self._current),
            keep_position=keep_position,
        )

    def _expand(self, path: str) -> None:
        self._options.expanded.add(path)
        self._rerender()

    def _toggle_empty(self, checked: bool) -> None:
        self._options.show_empty = checked
        self._rerender()

    def _toggle_technical(self, checked: bool) -> None:
        self._options.show_technical = checked
        self._rerender()

    # -- Ende ------------------------------------------------------------

    def closeEvent(self, event):  # noqa: N802 (Qt-Signatur)
        if self._worker is not None and self._worker.isRunning():
            self._worker.cancel()
            self._worker.wait(3000)
        if self._index is not None:
            self._index.close()
        super().closeEvent(event)
