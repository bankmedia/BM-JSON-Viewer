"""Wert-Kosmetik: aus Rohwerten des Exports lesbare deutsche Darstellung machen.

Die Regeln hier folgen dem, was im realen Export tatsächlich vorkommt:
Zeitstempel in UTC (``+0000``), Feldnamen bereits als deutsche Klartext-Labels,
GUIDs als 32-40 Zeichen Hex, sehr viele ``null``-Felder.
"""

from __future__ import annotations

import html
import re
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

# Der Export schreibt UTC. Ohne Umrechnung zeigt der Viewer dem Revisor die
# falsche Uhrzeit (ein 16:00-Termin steht als "14:00:00+0000" in den Daten).
LOCAL_TZ = ZoneInfo("Europe/Berlin")

# Groovys JsonOutput schreibt Date/Timestamp im Basisformat mit +0000. Andere
# Exporter liefern ggf. den erweiterten Offset oder gar keinen — alles zulassen.
_ISO_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?$"
)
_GUID_RE = re.compile(r"^[0-9A-Fa-f]{32,40}$")
_HEX_COLOR_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"[ \t\r\f\v]+")

#: Kandidaten für den Titel eines Datensatzes, in absteigender Priorität.
#: ``Titel`` ist im realen Export durchgängig ``null``, ``Thema`` trägt den
#: echten Titel — die Kette darf also nicht beim ersten vorhandenen Key stoppen,
#: sondern erst beim ersten *belegten*.
TITLE_KEYS = (
    "titel", "title", "thema", "betreff", "subject",
    "bezeichnung", "name", "kurzbeschreibung",
)

ID_KEYS = ("id", "lid", "recid", "datensatz id")

#: Datumsfelder, die einen Datensatz zeitlich einordnen, in fallender Priorität.
#: ``Datum`` ist der Sitzungstermin und sagt mehr als der Zeitpunkt, zu dem der
#: Datensatz angelegt wurde.
DATE_KEYS = (
    "datum", "date", "sitzung am", "erstellungsdatum",
    "created", "änderungsdatum", "dtinsert",
)

#: Datensatzordner heißen ``20200409_150116_<Thema>`` — der Zeitstempel ist der
#: letzte Anker, wenn im JSON kein Datum steht.
_FOLDER_STAMP_RE = re.compile(r"^(\d{8})_(\d{6})")


def parse_datetime(value: object) -> datetime | None:
    """Erkennt einen ISO-Zeitstempel und gibt ihn zeitzonenbewusst zurück."""
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not _ISO_RE.match(text):
        return None
    normalised = text.replace(" ", "T")
    if normalised.endswith("Z"):
        normalised = normalised[:-1] + "+00:00"
    else:
        # "+0000" -> "+00:00"; fromisoformat akzeptiert das Basisformat erst
        # ab 3.11 und wir wollen nicht davon abhängen.
        m = re.search(r"([+-])(\d{2})(\d{2})$", normalised)
        if m:
            normalised = normalised[: m.start()] + f"{m.group(1)}{m.group(2)}:{m.group(3)}"
    try:
        parsed = datetime.fromisoformat(normalised)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def format_datetime(value: datetime) -> str:
    """``30.06.2026 18:42`` in lokaler Zeit; reine Datumsangaben ohne Uhrzeit.

    Fristen stehen als Mitternacht in den Daten (``2020-12-30T23:00:00+0000``
    ist der 31.12. um 00:00 Ortszeit). Ein angehängtes ``00:00`` suggeriert dort
    eine Genauigkeit, die der Wert nicht hat.
    """
    local = value.astimezone(LOCAL_TZ)
    if local.hour == 0 and local.minute == 0 and local.second == 0:
        return local.strftime("%d.%m.%Y")
    return local.strftime("%d.%m.%Y %H:%M")


def format_number(value: int | float) -> str:
    """Deutsche Notation. Tausenderpunkte erst ab 10.000, damit vierstellige
    IDs nicht als ``1.234`` erscheinen."""
    if isinstance(value, bool):
        return "Ja" if value else "Nein"
    if isinstance(value, int):
        return f"{value:,}".replace(",", ".") if abs(value) >= 10_000 else str(value)
    if value != value or value in (float("inf"), float("-inf")):  # NaN/Inf
        return str(value)
    text = f"{value:,.10g}".replace(",", "\x00").replace(".", ",").replace("\x00", ".")
    return text


def format_scalar(value: object) -> str:
    """Einzelwert für die Anzeige aufbereiten (ohne HTML-Behandlung)."""
    if value is None:
        return "–"
    if isinstance(value, bool):
        return "Ja" if value else "Nein"
    if isinstance(value, (int, float)):
        return format_number(value)
    if isinstance(value, str):
        parsed = parse_datetime(value)
        if parsed is not None:
            return format_datetime(parsed)
        return value.strip()
    return str(value)


def is_empty(value: object) -> bool:
    """Leer im Sinne von "standardmäßig nicht anzeigen"."""
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, (dict, list, tuple, set)):
        return len(value) == 0
    return False


def is_guid(value: object) -> bool:
    return isinstance(value, str) and bool(_GUID_RE.match(value.strip()))


def is_hex_color(value: object) -> bool:
    return isinstance(value, str) and bool(_HEX_COLOR_RE.match(value.strip()))


def is_technical(key: str, value: object) -> bool:
    """Technisches Feld? **Wertbasiert**, nicht namensbasiert.

    Der Export enthält ``"Datensatz Ersteller ID": "Christian Lever"`` und
    ``"Status ID": "Offen"`` — eine Regel "Key endet auf ID" würde echten
    Inhalt verstecken. Maßgeblich ist deshalb, ob der *Wert* wie ein
    technischer Schlüssel aussieht.
    """
    return is_guid(value)


def strip_guid_suffix(label: str) -> str:
    """``"Projekt-Nr. - 4717A741..."`` -> ``"Projekt-Nr."``

    Die Keys unter ``Benutzerdefinierte Kontrollen`` tragen die GUID der
    Kontrolle im Klartext, weil der Titel allein nicht eindeutig ist.
    """
    parts = label.rsplit(" - ", 1)
    if len(parts) == 2 and is_guid(parts[1]):
        return parts[0].strip()
    return label


_CAMEL_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")

#: Kürzel, die sonst als "Id" oder "Guid" erscheinen würden.
_ACRONYMS = {"id": "ID", "guid": "GUID", "uid": "UID", "url": "URL", "pdf": "PDF"}


def prettify_label(key: str) -> str:
    """Fallback für technische Feldnamen.

    Im realen Export sind die Keys bereits deutsche Labels; das hier greift nur,
    wenn ein anderer Exporter ``customerSince`` oder ``customer_since`` liefert.
    """
    label = strip_guid_suffix(key.strip())
    if not label:
        return key
    if label.lower() in _ACRONYMS:
        return _ACRONYMS[label.lower()]
    if " " in label:
        return label
    label = label.replace("_", " ").replace("-", " ")
    label = _CAMEL_RE.sub(" ", label)
    label = _WS_RE.sub(" ", label).strip()
    return label[:1].upper() + label[1:] if label else key


def looks_like_html(value: object) -> bool:
    return isinstance(value, str) and bool(re.search(r"<(p|br|div|span|table|h[1-6]|strong|em|ul|ol|li)\b", value, re.I))


def html_to_text(value: str) -> str:
    """HTML auf Klartext reduzieren — für den Suchindex und Trefferausschnitte."""
    text = re.sub(r"(?is)<(script|style)\b.*?</\1>", " ", value)
    text = re.sub(r"(?i)<(br|/p|/div|/tr|/h[1-6]|/li)\s*/?>", "\n", text)
    text = re.sub(r"(?i)</t[dh]>", "\t", text)
    text = _TAG_RE.sub(" ", text)
    text = html.unescape(text)
    text = _WS_RE.sub(" ", text)
    text = re.sub(r"\n\s*\n+", "\n", text)
    return text.strip()


def is_numeric_key_map(value: object) -> bool:
    """``{"1": ..., "2": ...}`` — Groovys JsonBuilder schreibt Listen so.

    Deshalb greift ``isinstance(v, list)`` im realen Export nie und die
    Container-Erkennung muss über die Key-Form laufen.
    """
    return (
        isinstance(value, dict)
        and len(value) > 0
        and all(isinstance(k, str) and k.strip().lstrip("-").isdigit() for k in value)
    )


def pick_title(record: dict, fallback: str) -> str:
    """Erster *belegter* Titel-Kandidat, sonst der Ordnername."""
    lowered = {k.lower(): v for k, v in record.items() if isinstance(k, str)}
    for candidate in TITLE_KEYS:
        value = lowered.get(candidate)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return fallback


def pick_date(record: dict, folder_name: str = "") -> datetime | None:
    """Das Datum, unter dem ein Datensatz einzuordnen ist."""
    lowered = {k.lower(): v for k, v in record.items() if isinstance(k, str)}
    for candidate in DATE_KEYS:
        parsed = parse_datetime(lowered.get(candidate))
        if parsed is not None:
            return parsed
    match = _FOLDER_STAMP_RE.match(folder_name)
    if match:
        try:
            return datetime.strptime(
                match.group(1) + match.group(2), "%Y%m%d%H%M%S"
            ).replace(tzinfo=LOCAL_TZ)
        except ValueError:
            return None
    return None


def format_date(value: datetime | None) -> str:
    return value.astimezone(LOCAL_TZ).strftime("%d.%m.%Y") if value else ""


def pick_record_id(record: dict) -> str | None:
    lowered = {k.lower(): v for k, v in record.items() if isinstance(k, str)}
    for candidate in ID_KEYS:
        value = lowered.get(candidate)
        if value is not None and not isinstance(value, (dict, list)):
            return str(value)
    return None
