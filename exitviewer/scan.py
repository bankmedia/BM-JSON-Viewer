"""Verzeichnis-Scan. Strikt lesend — es wird nie unterhalb der Wurzel geschrieben.

Struktur laut Exportvorgabe::

    Wurzel/<Anwendung>/<Datensatz-Ordner>/<name>.json + Anhänge

Der Nutzer öffnet aber genauso oft den Anwendungsordner selbst
(``C:\\Temp\\Export alle Protokolle``). Beide Fälle werden erkannt, indem ein
Ordner genau dann als Datensatz gilt, wenn er direkt eine ``*.json`` enthält.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Callable, Iterable
from datetime import datetime, timezone
from pathlib import Path

from . import fmt
from .model import Attachment, AttachmentGroup, Record, ScanResult

#: JSON darüber wird nicht vollständig geladen; die Ansicht weist darauf hin.
MAX_JSON_BYTES = 50 * 1024 * 1024
#: So viel Text wird von übergroßen Dateien noch für die Suche eingelesen.
MAX_INDEX_BYTES = 5 * 1024 * 1024
#: Schutz gegen pathologisch tiefe Anhangsbäume.
MAX_ATTACHMENT_DEPTH = 3

#: ``Protokoll (PDF)_20260223_120227.pdf`` -> Gruppe "Protokoll (PDF)"
_VERSION_RE = re.compile(r"^(?P<base>.+)_(?P<ts>\d{8}_\d{6})$")

ProgressFn = Callable[[int, int, str], None]
CancelFn = Callable[[], bool]


def _noop_progress(done: int, total: int, label: str) -> None:  # pragma: no cover
    pass


def _never_cancelled() -> bool:
    return False


def read_text(path: Path, limit: int | None = None) -> tuple[str, str]:
    """Datei als Text lesen und die verwendete Kodierung zurückgeben.

    Der Exporter schreibt mit der Plattform-Standardkodierung der JVM und
    ersetzt vorher ``\\uXXXX``-Escapes durch echte Zeichen — die Kodierung kann
    also je nach Server UTF-8 oder cp1252 sein. Der Beispielexport ist UTF-8
    ohne BOM.
    """
    with open(path, "rb") as handle:
        raw = handle.read() if limit is None else handle.read(limit)
    if raw.startswith(b"\xef\xbb\xbf"):
        return raw[3:].decode("utf-8", errors="replace"), "utf-8 (BOM)"
    for encoding in ("utf-8", "cp1252"):
        try:
            return raw.decode(encoding), encoding
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace"), "utf-8 (mit Ersetzungen)"


def unwrap_record(document: object) -> tuple[dict | None, str]:
    """``{"Protokoll": {...}}`` auf den Hauptdatensatz reduzieren.

    Ein JSON enthält genau einen Hauptdatensatz; der äußere Key benennt dessen
    Typ und gehört als Überschrift in die Akte, nicht als Abschnitt.
    """
    if not isinstance(document, dict):
        return None, ""
    if len(document) == 1:
        (key, value), = document.items()
        if isinstance(value, dict):
            return value, str(key)
    return document, ""


def is_record_dir(entry: os.DirEntry[str]) -> bool:
    try:
        with os.scandir(entry.path) as it:
            return any(e.is_file() and e.name.lower().endswith(".json") for e in it)
    except OSError:
        return False


def _pick_primary_json(folder: Path, names: list[str]) -> str:
    """Der Hauptdatensatz heißt meist wie der Ordner — aber nicht immer."""
    expected = folder.name.lower() + ".json"
    for name in names:
        if name.lower() == expected:
            return name
    return sorted(names)[0]


def _collect_attachments(
    folder: Path, skip: str, problems: list[str]
) -> list[Attachment]:
    """Alle Dateien des Datensatzordners außer dem Haupt-JSON.

    Unterordner ohne eigenes JSON (``Protokoll_Anhänge``, ``Aufgaben_Anhänge``,
    …) gehören zum Datensatz und werden mit aufgenommen.
    """
    found: list[Attachment] = []

    def walk(directory: Path, relative: str, depth: int) -> None:
        if depth > MAX_ATTACHMENT_DEPTH:
            return
        try:
            entries = list(os.scandir(directory))
        except OSError as exc:
            problems.append(f"{directory}: {exc.strerror or exc}")
            return
        for entry in entries:
            if entry.is_dir():
                # Ein Unterordner mit eigenem JSON ist ein eigener Datensatz
                # und wird an anderer Stelle erfasst.
                if not is_record_dir(entry):
                    child = f"{relative}/{entry.name}" if relative else entry.name
                    walk(Path(entry.path), child, depth + 1)
                continue
            if depth == 0 and entry.name == skip:
                continue
            try:
                stat = entry.stat()
            except OSError:
                continue
            base, version = split_version(entry.name)
            found.append(
                Attachment(
                    path=Path(entry.path),
                    name=entry.name,
                    size=stat.st_size,
                    mtime=stat.st_mtime,
                    subfolder=relative,
                    group=base,
                    version=version,
                )
            )

    walk(folder, "", 0)
    found.sort(key=lambda a: (a.subfolder, a.name))
    return found


def split_version(filename: str) -> tuple[str, datetime | None]:
    """``Protokoll (PDF)_20260223_120227.pdf`` -> ``("Protokoll (PDF)", dt)``.

    Der Export lief versehentlich täglich und hat pro Datensatz ~120 nahezu
    identische PDFs angesammelt. Für den Revisor ist das eine Datei mit
    Versionshistorie, keine 120 Anhänge.
    """
    stem, dot, extension = filename.rpartition(".")
    if not dot:
        stem, extension = filename, ""
    match = _VERSION_RE.match(stem)
    if not match:
        return filename, None
    try:
        timestamp = datetime.strptime(match.group("ts"), "%Y%m%d_%H%M%S")
    except ValueError:
        return filename, None
    return match.group("base"), timestamp


def group_attachments(attachments: Iterable[Attachment]) -> list[AttachmentGroup]:
    """Versionierte Dateien zu je einem Eintrag zusammenfassen, neueste zuerst."""
    buckets: dict[tuple[str, str], list[Attachment]] = {}
    order: list[tuple[str, str]] = []
    for attachment in attachments:
        key = (attachment.subfolder, attachment.group)
        if key not in buckets:
            buckets[key] = []
            order.append(key)
        buckets[key].append(attachment)

    groups: list[AttachmentGroup] = []
    for key in order:
        versions = buckets[key]
        if len(versions) > 1:
            versions.sort(
                key=lambda a: (a.version or datetime.min, a.mtime), reverse=True
            )
        groups.append(AttachmentGroup(name=key[1], versions=versions))
    return groups


def referenced_names(data: object) -> set[str]:
    """Alle Strings im Datensatz, die wie ein Dateiname aussehen.

    Dient nur zum Markieren ("im Datensatz genannt"); die Liste der Anhänge
    kommt aus dem Dateisystem.
    """
    names: set[str] = set()

    def walk(node: object) -> None:
        if isinstance(node, dict):
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)
        elif isinstance(node, str):
            candidate = node.strip()
            if 3 < len(candidate) < 260 and re.search(r"\.[A-Za-z0-9]{2,5}$", candidate):
                names.add(candidate)
                names.add(candidate.rsplit("/", 1)[-1].rsplit("\\", 1)[-1])

    walk(data)
    return names


def _mark_referenced(record: Record) -> None:
    if record.data is None:
        return
    names = referenced_names(record.data)
    if not names:
        return
    lowered = {n.lower() for n in names}
    for attachment in record.attachments:
        if attachment.name.lower() in lowered:
            attachment.referenced = True
            continue
        # Präfixbehaftete Ablagen (``<taskId>_``, ``<commentGuid>_``,
        # ``<kontrollTitel>_``) — dort steht im JSON der unpräfixierte Name.
        if attachment.subfolder and any(
            attachment.name.lower().endswith("_" + n) or attachment.name.lower() == n
            for n in lowered
        ):
            attachment.referenced = True


def _load_record(folder: Path, app: str, problems: list[str]) -> Record:
    record = Record(app=app, folder=folder, title=folder.name)
    try:
        json_names = [
            e.name
            for e in os.scandir(folder)
            if e.is_file() and e.name.lower().endswith(".json")
        ]
    except OSError as exc:
        record.error = f"Ordner nicht lesbar: {exc.strerror or exc}"
        problems.append(f"{folder}: {record.error}")
        return record

    if not json_names:
        record.error = "Kein JSON im Ordner gefunden"
        return record

    primary = _pick_primary_json(folder, json_names)
    record.json_path = folder / primary
    record.attachments = _collect_attachments(folder, primary, problems)

    try:
        size = record.json_path.stat().st_size
    except OSError as exc:
        record.error = f"Datei nicht lesbar: {exc.strerror or exc}"
        problems.append(f"{record.json_path}: {record.error}")
        return record

    if size > MAX_JSON_BYTES:
        record.truncated = True
        record.error = (
            f"Datei ist {size / 1024 / 1024:.0f} MB groß und wurde nicht geladen. "
            "Der Inhalt ist über die Suche auffindbar."
        )
        problems.append(f"{record.json_path}: übersprungen ({size} Bytes)")
        return record

    try:
        text, encoding = read_text(record.json_path)
        record.encoding = encoding
        document = json.loads(text)
    except json.JSONDecodeError as exc:
        record.error = f"JSON ungültig (Zeile {exc.lineno}, Spalte {exc.colno}): {exc.msg}"
        problems.append(f"{record.json_path}: {record.error}")
        return record
    except OSError as exc:
        record.error = f"Datei nicht lesbar: {exc.strerror or exc}"
        problems.append(f"{record.json_path}: {record.error}")
        return record

    data, record_type = unwrap_record(document)
    if data is None:
        record.error = "JSON enthält kein Objekt als Hauptdatensatz"
        return record

    record.data = data
    record.record_type = record_type
    record.title = fmt.pick_title(data, folder.name)
    record.record_id = fmt.pick_record_id(data)
    record.date = fmt.pick_date(data, folder.name)
    _mark_referenced(record)
    return record


def find_record_folders(root: Path) -> list[tuple[Path, str]]:
    """Datensatzordner samt Anwendungsname finden.

    Ein Ordner ist ein Datensatz, sobald er direkt eine ``*.json`` enthält.
    Damit funktioniert sowohl die Exportwurzel als auch ein direkt geöffneter
    Anwendungsordner.
    """
    found: list[tuple[Path, str]] = []
    try:
        level_one = sorted(
            (e for e in os.scandir(root) if e.is_dir()), key=lambda e: e.name
        )
    except OSError:
        return found

    for entry in level_one:
        if is_record_dir(entry):
            # Die Wurzel ist selbst der Anwendungsordner.
            found.append((Path(entry.path), root.name))
            continue
        try:
            children = sorted(
                (e for e in os.scandir(entry.path) if e.is_dir()), key=lambda e: e.name
            )
        except OSError:
            continue
        for child in children:
            if is_record_dir(child):
                found.append((Path(child.path), entry.name))
    return found


def scan(
    root: Path | str,
    progress: ProgressFn = _noop_progress,
    cancelled: CancelFn = _never_cancelled,
) -> ScanResult:
    """Export vollständig einlesen. Ausschließlich lesende Zugriffe."""
    root = Path(root)
    result = ScanResult(root=root)

    progress(0, 0, "Ordner werden gesucht …")
    folders = find_record_folders(root)
    total = len(folders)
    if total == 0:
        result.problems.append(
            "Keine Datensätze gefunden. Erwartet wird ein Ordner mit "
            "Unterordnern, die je eine JSON-Datei enthalten."
        )
        return result

    for position, (folder, app) in enumerate(folders, start=1):
        if cancelled():
            result.problems.append("Scan wurde abgebrochen.")
            break
        progress(position, total, folder.name)
        result.records.append(_load_record(folder, app, result.problems))

    # Chronologisch je Anwendung. Datensätze ohne Datum ans Ende, damit sie
    # nicht zwischen die datierten rutschen.
    undated = datetime.max.replace(tzinfo=timezone.utc)
    result.records.sort(
        key=lambda r: (r.app.lower(), r.date or undated, r.folder.name.lower())
    )
    return result
