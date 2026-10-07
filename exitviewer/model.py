"""Datenmodell für einen gescannten Export."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path


@dataclass(slots=True)
class Attachment:
    """Eine Datei im Datensatzordner.

    Maßgeblich ist die Platte, nicht die JSON-Referenz: das Protokoll-PDF taucht
    im JSON überhaupt nicht auf, und ``"Anhänge": {}`` steht auch dann, wenn
    Dateien vorhanden sind.
    """

    path: Path
    name: str
    size: int
    mtime: float
    #: Unterordner relativ zum Datensatzordner ("" = direkt im Ordner)
    subfolder: str = ""
    #: Im JSON namentlich referenziert?
    referenced: bool = False
    #: Basisname ohne ``_<yyyyMMdd_HHmmss>``-Suffix; Schlüssel der Versionsgruppe
    group: str = ""
    #: Zeitstempel aus dem Dateinamen, falls vorhanden
    version: datetime | None = None

    @property
    def label(self) -> str:
        return f"{self.subfolder}/{self.name}" if self.subfolder else self.name


@dataclass(slots=True)
class AttachmentGroup:
    """Mehrere Fassungen derselben Datei, neueste zuerst."""

    name: str
    versions: list[Attachment]

    @property
    def newest(self) -> Attachment:
        return self.versions[0]

    @property
    def older(self) -> list[Attachment]:
        return self.versions[1:]

    @property
    def total_size(self) -> int:
        return sum(a.size for a in self.versions)


@dataclass(slots=True)
class Record:
    """Ein Datensatz = ein Ordner mit genau einem Haupt-JSON."""

    app: str
    folder: Path
    json_path: Path | None = None
    #: Der ausgepackte Hauptdatensatz (bei ``{"Protokoll": {...}}`` das Innere)
    data: dict | None = None
    #: Der äußere Key, falls ausgepackt wurde ("Protokoll")
    record_type: str = ""
    title: str = ""
    record_id: str | None = None
    #: Fachliches Datum des Datensatzes — ordnet ihn ein und unterscheidet die
    #: vielen gleichnamigen Einträge voneinander.
    date: datetime | None = None
    attachments: list[Attachment] = field(default_factory=list)
    #: Fehlertext, wenn das JSON nicht lesbar war — der Datensatz bleibt im Baum
    error: str | None = None
    #: JSON überschritt das Größenlimit und wurde nicht vollständig geladen
    truncated: bool = False
    encoding: str = "utf-8"

    @property
    def ok(self) -> bool:
        return self.error is None and self.data is not None

    @property
    def key(self) -> str:
        return str(self.folder)


@dataclass(slots=True)
class ScanResult:
    root: Path
    records: list[Record] = field(default_factory=list)
    #: Warnungen (unlesbare Ordner, Parse-Fehler) für die Statusanzeige
    problems: list[str] = field(default_factory=list)

    @property
    def apps(self) -> list[str]:
        return sorted({r.app for r in self.records})

    def duplicate_ids(self) -> dict[tuple[str, str], list[Record]]:
        """Datensätze, die sich eine ID teilen — im realen Export kommt das vor
        (derselbe Datensatz in zwei Ordnern mit unterschiedlicher Namens-
        Bereinigung)."""
        buckets: dict[tuple[str, str], list[Record]] = {}
        for record in self.records:
            if record.record_id:
                buckets.setdefault((record.app, record.record_id), []).append(record)
        return {k: v for k, v in buckets.items() if len(v) > 1}
