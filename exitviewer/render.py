"""Datensatz → Akten-HTML.

Kein technisches JSON, sondern ein lesbares Dokument: Titel, Label/Wert-Zeilen,
Abschnitte für verschachtelte Objekte, Tabellen für Listen.

Die Container-Erkennung läuft über die Key-Form, nicht über den Python-Typ:
Groovys ``JsonBuilder`` schreibt Listen als ``{"1": …, "2": …}``, echte
JSON-Arrays kommen im Export gar nicht vor. Echte Listen werden trotzdem
unterstützt, damit ein anderer Exporter nicht sofort scheitert.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass, field, replace
from pathlib import Path
from urllib.parse import quote

from . import fmt
from .model import Record
from .sanitize import sanitize_html

# Formen, in denen ein Wert dargestellt werden kann.
EMPTY = "empty"
SCALAR = "scalar"
LIST_SCALAR = "list-scalar"
TABLE_ROWS = "table-rows"
TABLE_KEYED = "table-keyed"
FORM = "form"
CONTROLS = "controls"
MIXED = "mixed"

#: Werte-Keys der benutzerdefinierten Kontrollen, in Anzeigepriorität.
_CONTROL_VALUE_KEYS = (
    "Display Value", "Text Value", "String Value",
    "Long Value", "Float Value", "DateTime Value", "Boolean Value",
)
_CONTROL_EXTRA_KEYS = frozenset({"Dateien"})

#: Ab dieser Zeilenzahl wird eine Liste gekürzt und zum Aufklappen angeboten.
DEFAULT_LIST_LIMIT = 25


#: Endungen, die als Bild eingebunden werden dürfen.
IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp"})


@dataclass(slots=True)
class RenderOptions:
    show_empty: bool = False
    show_technical: bool = False
    list_limit: int = DEFAULT_LIST_LIMIT
    #: Pfade, die der Nutzer per "alle anzeigen" aufgeklappt hat.
    expanded: set[str] = field(default_factory=set)
    #: Dateinamen (klein) der Anhänge, die als Bild eingebunden werden dürfen.
    known_images: frozenset[str] = frozenset()


def inline_images(record: Record) -> dict[str, Path]:
    """Anhänge, die als eingebettetes Bild in Frage kommen.

    Die Rich-Text-Felder verweisen auf Portalpfade (``userfiles/Image/x.png``),
    die es im Export nicht gibt — die Datei selbst liegt aber häufig als Anhang
    im selben Datensatzordner. Aufgelöst wird ausschließlich über den
    Dateinamen und ausschließlich gegen diese Liste.
    """
    found: dict[str, Path] = {}
    for attachment in record.attachments:
        if Path(attachment.name).suffix.lower() in IMAGE_SUFFIXES:
            found.setdefault(attachment.name.lower(), attachment.path)
    return found


# ---------------------------------------------------------------- Analyse


def is_control_map(value: dict) -> bool:
    """Erkennt ``Benutzerdefinierte Kontrollen`` an der Struktur, nicht am Namen.

    Jeder Eintrag ist ein Objekt, dessen Keys auf ``… Value`` enden — pro
    Kontrolle ist genau einer davon belegt. Als Tabelle wäre das eine Matrix
    aus fast lauter Leerzellen; als Label/Wert-Zeile ist es genau das, was der
    Anwender im Ursprungssystem eingegeben hat.
    """
    inner = [v for v in value.values() if isinstance(v, dict)]
    if len(inner) != len(value) or not any(inner):
        return False
    for entry in inner:
        if not entry:
            continue
        for key in entry:
            if not (key.endswith(" Value") or key in _CONTROL_EXTRA_KEYS):
                return False
    return True


def classify(value: object) -> str:
    if fmt.is_empty(value):
        return EMPTY
    if isinstance(value, (str, int, float, bool)) or value is None:
        return SCALAR

    if isinstance(value, list):
        if all(not isinstance(v, (dict, list)) for v in value):
            return LIST_SCALAR
        if all(isinstance(v, dict) for v in value):
            return TABLE_ROWS
        return MIXED

    if isinstance(value, dict):
        entries = list(value.values())
        numeric_keys = fmt.is_numeric_key_map(value)
        all_objects = all(isinstance(v, dict) for v in entries)
        all_scalars = all(not isinstance(v, (dict, list)) for v in entries)
        if numeric_keys:
            # Die Map ist eine Liste: der Key ist nur ein Zähler und gehört
            # nicht in die Darstellung.
            return LIST_SCALAR if all_scalars else (TABLE_ROWS if all_objects else MIXED)
        if all_objects:
            return CONTROLS if is_control_map(value) else TABLE_KEYED
        if all_scalars:
            return FORM
        return MIXED
    return SCALAR


def _visible(key: str, value: object, options: RenderOptions) -> bool:
    if not options.show_empty and fmt.is_empty(value):
        return False
    if not options.show_technical and fmt.is_technical(key, value):
        return False
    return True


# ------------------------------------------------------------ Bausteine


def _esc(text: object) -> str:
    return html.escape(str(text), quote=False)


def _scalar_html(value: object, options: RenderOptions) -> str:
    """Einzelwert als HTML-Fragment."""
    if fmt.looks_like_html(value):
        inner = sanitize_html(str(value), options.known_images)
        return f'<div class="richtext">{inner}</div>'
    if fmt.is_hex_color(value):
        colour = str(value).strip()
        return (
            f'<span class="swatch" style="background-color:{colour};">&#160;&#160;&#160;</span>'
            f' <span class="mono">{_esc(colour)}</span>'
        )
    text = fmt.format_scalar(value)
    if fmt.is_guid(value):
        return f'<span class="mono technisch">{_esc(text)}</span>'
    return _esc(text).replace("\n", "<br>")


def _more_link(path: str, total: int) -> str:
    target = quote(path, safe="")
    return (
        f'<p class="mehr"><a href="mehr:{target}">'
        f"… alle {total} Einträge anzeigen</a></p>"
    )


def _limit(rows: list, path: str, options: RenderOptions) -> tuple[list, str]:
    if path in options.expanded or len(rows) <= options.list_limit:
        return rows, ""
    return rows[: options.list_limit], _more_link(path, len(rows))


def _columns(rows: list[dict], options: RenderOptions) -> list[str]:
    """Spalten in Reihenfolge des ersten Auftretens, leere und technische raus."""
    order: list[str] = []
    for row in rows:
        for key in row:
            if key not in order:
                order.append(key)

    keep: list[str] = []
    for key in order:
        values = [row.get(key) for row in rows]
        if not options.show_empty and all(fmt.is_empty(v) for v in values):
            continue
        if not options.show_technical and all(
            fmt.is_empty(v) or fmt.is_technical(key, v) for v in values
        ):
            continue
        keep.append(key)
    return keep


def _cell_html(value: object, path: str, options: RenderOptions) -> str:
    """Zellinhalt — verschachtelte Container werden kompakt eingebettet."""
    kind = classify(value)
    if kind == EMPTY:
        return "–"
    if kind == SCALAR:
        return _scalar_html(value, options)
    if kind == LIST_SCALAR:
        items = list(value.values()) if isinstance(value, dict) else list(value)
        return "<br>".join(_scalar_html(v, options) for v in items)
    if isinstance(value, dict):
        return "<br>".join(
            f"<i>{_esc(fmt.prettify_label(k))}:</i> {_cell_html(v, path, options)}"
            for k, v in value.items()
            if _visible(k, v, options)
        ) or "–"
    if isinstance(value, list):
        return "<br>".join(_cell_html(v, path, options) for v in value)
    return _scalar_html(value, options)


# ------------------------------------------------------------ Abschnitte


def _render_form(obj: dict, path: str, options: RenderOptions) -> str:
    rows = [(k, v) for k, v in obj.items() if _visible(k, v, options)]
    if not rows:
        return ""
    cells = "".join(
        f'<tr><td class="label">{_esc(fmt.prettify_label(k))}</td>'
        f'<td class="wert">{_cell_html(v, f"{path}.{k}", options)}</td></tr>'
        for k, v in rows
    )
    return f'<table class="form" width="100%">{cells}</table>'


def _render_list(value: object, path: str, options: RenderOptions) -> str:
    items = list(value.values()) if isinstance(value, dict) else list(value)
    items = [v for v in items if options.show_empty or not fmt.is_empty(v)]
    if not items:
        return ""
    shown, more = _limit(items, path, options)
    entries = "".join(f"<li>{_scalar_html(v, options)}</li>" for v in shown)
    return f"<ul>{entries}</ul>{more}"


def _key_is_redundant(rows: list[tuple[object, dict]]) -> bool:
    """Steckt der Map-Key schon vollständig in den Feldern der Zeile?

    ``"29: Neue Aufgabe aus Protokolle"`` wiederholt nur ``Datensatz ID`` und
    ``Titel``. Eine eigene Spalte dafür kostet Platz und sagt nichts Neues.
    """
    for key, row in rows:
        blob = " ".join(
            str(v) for v in row.values() if v is not None and not isinstance(v, (dict, list))
        )
        tokens = [t for t in re.split(r"[\s:,;_-]+", str(key)) if t]
        if not tokens or not all(t in blob for t in tokens):
            return False
    return True


def _render_table(
    value: object, path: str, options: RenderOptions, key_header: str | None
) -> str:
    if isinstance(value, dict):
        pairs = list(value.items())
    else:
        pairs = [(None, v) for v in value]

    rows = [(k, v) for k, v in pairs if isinstance(v, dict)]
    if not rows:
        return ""
    if key_header and _key_is_redundant(rows):
        key_header = None
    shown, more = _limit(rows, path, options)
    columns = _columns([v for _, v in shown], options)
    if not columns:
        return ""

    head = "".join(f"<th>{_esc(fmt.prettify_label(c))}</th>" for c in columns)
    if key_header:
        head = f"<th>{_esc(key_header)}</th>{head}"

    body = []
    for key, row in shown:
        cells = "".join(
            f'<td>{_cell_html(row.get(c), f"{path}.{c}", options)}</td>' for c in columns
        )
        if key_header:
            label = _esc(fmt.strip_guid_suffix(str(key)))
            cells = f'<td class="rowkey">{label}</td>{cells}'
        body.append(f"<tr>{cells}</tr>")

    return (
        f'<table class="grid" width="100%" border="1" cellspacing="0" cellpadding="4">'
        f"<thead><tr>{head}</tr></thead><tbody>{''.join(body)}</tbody></table>{more}"
    )


def _control_value(entry: dict, path: str, options: RenderOptions) -> str:
    """Aus einer Kontrolle den einen belegten Wert holen.

    ``Display Value`` gewinnt gegen ``String Value``: dort steht der Text, den
    der Anwender gesehen hat ("0815"), während ``String Value`` den internen
    Schlüssel trägt ("2").
    """
    for key in _CONTROL_VALUE_KEYS:
        if key in entry and not fmt.is_empty(entry[key]):
            rendered = _scalar_html(entry[key], options)
            break
    else:
        rendered = "–"

    files = entry.get("Dateien")
    if not fmt.is_empty(files):
        listing = _render_list(files, f"{path}.Dateien", options)
        rendered += f'<div class="dateien">{listing}</div>'
    return rendered


def _render_controls(value: dict, path: str, options: RenderOptions) -> str:
    rows = [(k, v) for k, v in value.items() if options.show_empty or v]
    if not rows:
        return ""
    shown, more = _limit(rows, path, options)
    cells = "".join(
        f'<tr><td class="label">{_esc(fmt.strip_guid_suffix(str(k)))}</td>'
        f'<td class="wert">{_control_value(v, path, options)}</td></tr>'
        for k, v in shown
    )
    return f'<table class="form" width="100%">{cells}</table>{more}'


def render_value(value: object, path: str, options: RenderOptions, level: int) -> str:
    """Einen Container in seiner passenden Form ausgeben."""
    kind = classify(value)
    if kind == EMPTY:
        return '<p class="leer">– keine Einträge –</p>' if options.show_empty else ""
    if kind == SCALAR:
        return f'<p>{_scalar_html(value, options)}</p>'
    if kind == LIST_SCALAR:
        return _render_list(value, path, options)
    if kind == TABLE_ROWS:
        return _render_table(value, path, options, key_header=None)
    if kind == TABLE_KEYED:
        return _render_table(value, path, options, key_header="Bezeichnung")
    if kind == CONTROLS:
        return _render_controls(value, path, options)
    if kind == FORM:
        return _render_form(value, path, options)
    return render_object(value, path, options, level)


def render_object(value: object, path: str, options: RenderOptions, level: int) -> str:
    """Gemischtes Objekt: erst die Einzelfelder, dann die Abschnitte.

    Das ist auch der Weg für den Hauptdatensatz selbst — er ist nichts anderes
    als ein gemischtes Objekt auf oberster Ebene.
    """
    if isinstance(value, list):
        return "".join(
            render_value(item, f"{path}[{i}]", options, level)
            for i, item in enumerate(value)
        )
    if not isinstance(value, dict):
        return f"<p>{_scalar_html(value, options)}</p>"

    plain: dict[str, object] = {}
    sections: list[tuple[str, object]] = []
    for key, item in value.items():
        if isinstance(item, (dict, list)) and not fmt.is_empty(item):
            sections.append((key, item))
        elif isinstance(item, (dict, list)):
            if options.show_empty:
                sections.append((key, item))
        else:
            plain[key] = item

    out = [_render_form(plain, path, options)]
    heading = min(level + 1, 6)
    for key, item in sections:
        body = render_value(item, f"{path}.{key}", options, level + 1)
        if not body:
            continue
        label = _esc(fmt.prettify_label(key))
        count = len(item) if isinstance(item, (dict, list)) else 0
        badge = f' <span class="anzahl">({count})</span>' if count > 1 else ""
        out.append(f"<h{heading}>{label}{badge}</h{heading}>{body}")
    return "".join(out)


# --------------------------------------------------------------- Dokument


def render_record(record: Record, options: RenderOptions | None = None) -> str:
    """Vollständige Akte eines Datensatzes als HTML-Rumpf."""
    options = options or RenderOptions()
    # Bildverweise dürfen nur gegen die Anhänge genau dieses Datensatzes
    # aufgelöst werden.
    options = replace(options, known_images=frozenset(inline_images(record)))
    parts: list[str] = [f"<h1>{_esc(record.title)}</h1>"]

    meta: list[str] = []
    if record.record_type:
        meta.append(_esc(record.record_type))
    meta.append(_esc(record.app))
    if record.record_id:
        meta.append(f"ID {_esc(record.record_id)}")
    parts.append(f'<p class="kopfzeile">{" · ".join(meta)}</p>')

    if record.error:
        parts.append(f'<p class="fehler">{_esc(record.error)}</p>')
    if record.truncated:
        parts.append(
            '<p class="hinweis">Die Datei wurde wegen ihrer Größe nicht '
            "vollständig geladen.</p>"
        )

    if record.data is not None:
        parts.append(render_object(record.data, record.record_type or "root", options, 1))

    parts.append(f'<p class="pfad">{_esc(record.folder)}</p>')
    return "".join(parts)
