"""Die Akten-Ansicht.

Ein ``QTextBrowser``, dem jede Möglichkeit genommen wurde, Ressourcen zu laden.
Der dargestellte Inhalt stammt aus einer Fremddatei; auf einem Bankarbeitsplatz
darf daraus unter keinen Umständen ein Netzwerkzugriff entstehen.
"""

from __future__ import annotations

from pathlib import Path
from urllib.parse import unquote

from PySide6.QtCore import QByteArray, Qt, QUrl, Signal
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QTextBrowser

from .. import resources
from ..sanitize import IMAGE_SCHEME

#: Größere Bilder werden nicht eingebunden — ein Anhang kann beliebig groß sein.
MAX_IMAGE_BYTES = 25 * 1024 * 1024


class DocumentView(QTextBrowser):
    """Zeigt eine Akte an und meldet Klicks auf "alle anzeigen"."""

    expand_requested = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._images: dict[str, Path] = {}
        self.setOpenLinks(False)
        self.setOpenExternalLinks(False)
        self.setReadOnly(True)
        self.document().setDefaultStyleSheet(resources.stylesheet())
        self.setStyleSheet("QTextBrowser { background: #ffffff; padding: 18px; }")
        self.anchorClicked.connect(self._on_anchor)

    # -- Abriegelung -----------------------------------------------------

    def loadResource(self, type_: int, name: QUrl):  # noqa: N802 (Qt-Signatur)
        """Liefert ausschließlich Bilder aus den Anhängen des offenen Datensatzes.

        Jeder andere Verweis — http, file, relativ — läuft ins Leere. Der
        Exportinhalt kann die Anwendung damit zu keinem Netzwerkzugriff und zu
        keinem Lesen außerhalb des Datensatzordners bewegen.
        """
        if name.scheme() != IMAGE_SCHEME:
            return None
        filename = unquote(name.toString()[len(IMAGE_SCHEME) + 1 :])
        path = self._images.get(filename.lower())
        if path is None:
            return None
        try:
            if path.stat().st_size > MAX_IMAGE_BYTES:
                return None
            image = QImage()
            if not image.loadFromData(QByteArray(path.read_bytes())):
                return None
        except OSError:
            return None

        # Auf die Spaltenbreite herunterrechnen: die Originale sind mehrere
        # tausend Pixel breit und würden die Akte sonst auseinanderziehen.
        limit = max(240, self.viewport().width() - 72)
        if image.width() > limit:
            image = image.scaledToWidth(limit, Qt.TransformationMode.SmoothTransformation)
        return image

    # -- Interaktion -----------------------------------------------------

    def _on_anchor(self, url: QUrl) -> None:
        target = url.toString()
        if target.startswith("mehr:"):
            self.expand_requested.emit(unquote(target[len("mehr:"):]))
        # Alles andere wird bewusst ignoriert: der Exportinhalt soll keine
        # Navigation auslösen können.

    def show_html(
        self,
        body: str,
        images: dict[str, Path] | None = None,
        keep_position: bool = False,
    ) -> None:
        # Erst die erlaubten Bilder setzen, dann den Inhalt — setHtml löst die
        # Ressourcenauflösung sofort aus.
        self._images = images or {}
        position = self.verticalScrollBar().value() if keep_position else 0
        self.setHtml(f"<body>{body}</body>")
        self.verticalScrollBar().setValue(position)

    def show_message(self, text: str) -> None:
        self.setHtml(f'<body><p class="leer">{text}</p></body>')
