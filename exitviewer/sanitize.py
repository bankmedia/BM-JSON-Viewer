"""HTML aus Feldwerten auf eine sichere Teilmenge reduzieren.

Der Export liefert Rich-Text-Felder als HTML (im Beispielexport 76 echte
``<table>``, Überschriften, ``<strong>``). Das soll dargestellt werden — aber
nichts davon darf die Anwendung zu einem Netzwerkzugriff bewegen oder das
Layout der Akte übernehmen. Deshalb Allowlist statt Blockliste: unbekannte
Tags und **alle** Attribute bis auf wenige harmlose fallen weg.
"""

from __future__ import annotations

import html
import re
from collections.abc import Container
from html.parser import HTMLParser
from urllib.parse import quote

#: Schema für Bilder, die aus den Anhängen des Datensatzes aufgelöst wurden.
#: Die Ansicht liefert genau diese und sonst nichts aus.
IMAGE_SCHEME = "akte"

#: Tags, die inhaltlich etwas beitragen und von QTextBrowser dargestellt werden.
ALLOWED_TAGS = frozenset(
    {
        "p", "br", "div", "span", "blockquote", "pre", "code",
        "b", "strong", "i", "em", "u", "s", "sub", "sup",
        "ul", "ol", "li", "dl", "dt", "dd",
        "table", "thead", "tbody", "tfoot", "tr", "td", "th", "caption",
        "h1", "h2", "h3", "h4", "h5", "h6", "hr",
    }
)

#: Tags, deren *Inhalt* ebenfalls verschwinden muss.
DROP_WITH_CONTENT = frozenset({"script", "style", "iframe", "object", "embed", "applet", "head"})

#: Einzige Attribute, die überleben — sie tragen Struktur, keine Adressen.
ALLOWED_ATTRS = {
    "td": {"colspan", "rowspan"},
    "th": {"colspan", "rowspan"},
}

VOID_TAGS = frozenset({"br", "hr", "img"})


def image_basename(attrs: list[tuple[str, str | None]]) -> str:
    """Dateiname aus dem ``src`` eines ``<img>`` — ohne Pfad, ohne Query."""
    for key, value in attrs:
        if key.lower() in ("src", "data-src") and value:
            source = value.strip().split("?", 1)[0].split("#", 1)[0]
            return re.split(r"[\\/]", source)[-1]
    return ""


class _Sanitizer(HTMLParser):
    def __init__(self, known_images: Container[str] = frozenset()) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._suppress = 0
        self._open: list[str] = []
        self._known = known_images

    # -- Hilfen ----------------------------------------------------------

    def _emit(self, text: str) -> None:
        if not self._suppress:
            self.parts.append(text)

    def _image(self, attrs: list[tuple[str, str | None]]) -> str:
        """Bildverweis auflösen oder als Fehlbestand ausweisen.

        Die ``src``-Angaben zeigen auf Portalpfade (``userfiles/Image/…``), die
        es im Export nicht gibt. Die Datei selbst liegt aber oft sehr wohl im
        Datensatzordner — dann wird sie von dort gezeigt. Nur wenn sich nichts
        findet, tritt der Platzhalter an ihre Stelle: für ein
        Revisionswerkzeug ist der benannte Fehlbestand ehrlicher als ein
        stilles Weglassen.
        """
        name = image_basename(attrs)
        if name and name.lower() in self._known:
            return f'<img src="{IMAGE_SCHEME}:{quote(name)}">'
        label = html.escape(name) if name else "ohne Namen"
        return f'<span class="fehlt">[Bild nicht im Export enthalten: {label}]</span>'

    # -- HTMLParser ------------------------------------------------------

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag in DROP_WITH_CONTENT:
            self._suppress += 1
            return
        if tag == "img":
            self._emit(self._image(attrs))
            return
        if tag not in ALLOWED_TAGS:
            # Unbekanntes Tag: Inhalt behalten, Hülle verwerfen. Das gilt auch
            # für <a> — der Text bleibt lesbar, das Ziel verschwindet.
            return
        allowed = ALLOWED_ATTRS.get(tag, frozenset())
        kept = "".join(
            f' {k.lower()}="{html.escape(v, quote=True)}"'
            for k, v in attrs
            if v is not None and k.lower() in allowed and v.strip().isdigit()
        )
        self._emit(f"<{tag}{kept}>")
        if tag not in VOID_TAGS:
            self._open.append(tag)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag == "img":
            self._emit(self._image(attrs))
            return
        if tag in ALLOWED_TAGS:
            self._emit(f"<{tag}>" if tag in VOID_TAGS else f"<{tag}></{tag}>")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in DROP_WITH_CONTENT:
            self._suppress = max(0, self._suppress - 1)
            return
        if tag not in ALLOWED_TAGS or tag in VOID_TAGS:
            return
        if tag in self._open:
            # Bis zum passenden Tag zurückrollen, damit falsch verschachtelter
            # Quelltext keine offenen Elemente hinterlässt.
            while self._open:
                current = self._open.pop()
                self._emit(f"</{current}>")
                if current == tag:
                    break

    def handle_data(self, data: str) -> None:
        self._emit(html.escape(data))

    def close(self) -> str:  # type: ignore[override]
        super().close()
        while self._open:
            self.parts.append(f"</{self._open.pop()}>")
        return "".join(self.parts)


def sanitize_html(value: str, known_images: Container[str] = frozenset()) -> str:
    """Feldwert als darstellbares, netzwerkfreies HTML.

    ``known_images`` enthält die kleingeschriebenen Dateinamen der Anhänge des
    Datensatzes; nur diese werden als Bild eingebunden.
    """
    parser = _Sanitizer(known_images)
    try:
        parser.feed(value)
        return parser.close()
    except Exception:
        # Lieber sichtbarer Klartext als eine Ausnahme mitten in der Akte.
        return html.escape(value)
