"""Volltextindex (SQLite FTS5) mit Cache außerhalb des Exports.

Der Export liegt womöglich auf einem schreibgeschützten Netzlaufwerk, deshalb
landet der Index unter ``%LOCALAPPDATA%``. Bei unverändertem Bestand wird der
Export beim Öffnen gar nicht mehr angefasst.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from . import APP_DIR_NAME, fmt
from .model import Attachment, Record, ScanResult
from .scan import is_record_dir

SCHEMA_VERSION = "4"

#: Marker um Fundstellen im Trefferausschnitt. Steuerzeichen, damit sie nicht
#: mit Inhalt verwechselt werden können.
HIT_START = "\x02"
HIT_END = "\x03"

#: FTS5-Operatoren, die aus freier Nutzereingabe entfernt werden.
_FTS_SPECIALS = re.compile(r'[^\w\säöüÄÖÜß-]', re.UNICODE)


@dataclass(slots=True)
class SearchHit:
    key: str
    app: str
    title: str
    snippet: str


def cache_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
    root = Path(base) if base else Path.home() / ".cache"
    return root / APP_DIR_NAME


def cache_path(root: Path) -> Path:
    digest = hashlib.sha1(str(root.resolve()).lower().encode("utf-8")).hexdigest()[:16]
    return cache_dir() / f"idx-{digest}.db"


def fingerprint(root: Path) -> str:
    """Billiger Änderungsnachweis: Namen und mtimes der Ordner, keine Dateien.

    ``os.DirEntry.stat()`` liefert unter Windows die Werte aus der bereits
    gelesenen Verzeichniseintragung — es kostet also keinen zusätzlichen
    Syscall. Damit bleibt der Cache-Check auch auf einem Netzlaufwerk billig,
    während der volle Scan über 20.000 Dateien statten müsste.

    Windows schreibt Verzeichnis-Zeitstempel verzögert zurück: unmittelbar nach
    einer Änderung kann derselbe Bestand kurzzeitig zwei Werte liefern. Bei
    einem Archivexport ist das folgenlos, und die einzige mögliche Auswirkung
    ist ein überflüssiger Neuaufbau — nie ein Cache, der fälschlich als gültig
    gilt.
    """
    hasher = hashlib.sha1()
    hasher.update(SCHEMA_VERSION.encode())

    def subdirs(directory: Path) -> list[os.DirEntry[str]]:
        try:
            return sorted(
                (e for e in os.scandir(directory) if e.is_dir()), key=lambda e: e.name
            )
        except OSError:
            return []

    def absorb(entry: os.DirEntry[str]) -> None:
        try:
            mtime = entry.stat().st_mtime_ns
        except OSError:
            mtime = 0
        hasher.update(f"{entry.path}|{mtime}\n".encode("utf-8", "replace"))

    for level_one in subdirs(root):
        absorb(level_one)
        # In einen Datensatzordner wird nicht abgestiegen: sein mtime ändert
        # sich bereits, wenn sich sein Inhalt ändert. Die zweite Ebene wird nur
        # gebraucht, wenn die Wurzel die Exportwurzel ist und hier
        # Anwendungsordner stehen. ``is_record_dir`` bricht beim ersten
        # gefundenen JSON ab und ist deshalb billig.
        if not is_record_dir(level_one):
            for level_two in subdirs(Path(level_one.path)):
                absorb(level_two)

    return hasher.hexdigest()


def build_body(record: Record) -> str:
    """Durchsuchbarer Klartext eines Datensatzes: Labels, Werte, Dateinamen.

    Titel und Ordnername stehen bewusst **nicht** am Anfang: sie sind bereits
    eigene Indexspalten, und weil ein Suchbegriff dort fast immer zuerst
    auftritt, würde ``snippet()`` sonst regelmäßig den Ordnernamen statt der
    Fundstelle im Inhalt zeigen.
    """
    chunks: list[str] = []

    def walk(node: object) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if isinstance(key, str):
                    chunks.append(fmt.strip_guid_suffix(key))
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)
        elif isinstance(node, str):
            if fmt.looks_like_html(node):
                chunks.append(fmt.html_to_text(node))
                return
            stamp = fmt.parse_datetime(node)
            if stamp is not None:
                # Deutsch für die Anzeige im Trefferausschnitt, ISO daneben,
                # damit beide Schreibweisen gefunden werden.
                chunks.append(f"{fmt.format_datetime(stamp)} ({node})")
            else:
                chunks.append(node)
        elif isinstance(node, bool):
            chunks.append("Ja" if node else "Nein")
        elif isinstance(node, (int, float)):
            chunks.append(str(node))

    if record.data is not None:
        walk(record.data)
    chunks.append(record.folder.name)
    chunks.extend(a.name for a in record.attachments)
    return "\n".join(c for c in chunks if c)


def build_query(text: str) -> str:
    """Freitext in einen sicheren FTS5-Ausdruck übersetzen.

    Nutzereingaben dürfen die FTS-Syntax nicht durchschlagen lassen, sonst
    zerlegt ein Anführungszeichen oder ein ``*`` die Abfrage.
    """
    terms = [t for t in _FTS_SPECIALS.sub(" ", text).split() if t]
    if not terms:
        return ""
    quoted = [f'"{t}"' for t in terms[:-1]]
    # Letzter Begriff als Präfix, damit die Suche schon beim Tippen greift.
    quoted.append(f'"{terms[-1]}"*')
    return " AND ".join(quoted)


class SearchIndex:
    """Persistenter Index eines Exports."""

    def __init__(self, root: Path):
        self.root = Path(root)
        self.path = cache_path(self.root)
        self._db: sqlite3.Connection | None = None

    # -- Verbindung ------------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        if self._db is None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._db = sqlite3.connect(self.path)
            self._db.row_factory = sqlite3.Row
        return self._db

    def close(self) -> None:
        if self._db is not None:
            self._db.close()
            self._db = None

    # -- Aufbau ----------------------------------------------------------

    def _create_schema(self, db: sqlite3.Connection) -> None:
        db.executescript(
            """
            DROP TABLE IF EXISTS docs;
            DROP TABLE IF EXISTS records;
            DROP TABLE IF EXISTS attachments;
            DROP TABLE IF EXISTS meta;

            CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);

            CREATE TABLE records (
                id        INTEGER PRIMARY KEY,
                app       TEXT NOT NULL,
                folder    TEXT NOT NULL,
                json_path TEXT,
                title     TEXT NOT NULL,
                record_id TEXT,
                recdate   TEXT,
                rectype   TEXT,
                error     TEXT,
                truncated INTEGER NOT NULL DEFAULT 0,
                encoding  TEXT,
                payload   TEXT
            );
            CREATE INDEX records_app ON records(app);

            CREATE TABLE attachments (
                record   INTEGER NOT NULL REFERENCES records(id),
                name     TEXT NOT NULL,
                subfolder TEXT NOT NULL DEFAULT '',
                size     INTEGER NOT NULL DEFAULT 0,
                mtime    REAL NOT NULL DEFAULT 0,
                grp      TEXT NOT NULL DEFAULT '',
                version  TEXT,
                referenced INTEGER NOT NULL DEFAULT 0
            );
            CREATE INDEX attachments_record ON attachments(record);

            CREATE VIRTUAL TABLE docs USING fts5(
                app, title, body,
                tokenize = 'unicode61 remove_diacritics 2'
            );
            """
        )

    def build(self, result: ScanResult) -> None:
        """Index aus einem frischen Scan schreiben."""
        db = self._connect()
        self._create_schema(db)
        with db:
            for position, record in enumerate(result.records, start=1):
                db.execute(
                    "INSERT INTO records (id, app, folder, json_path, title, record_id,"
                    " recdate, rectype, error, truncated, encoding, payload)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        position,
                        record.app,
                        str(record.folder),
                        str(record.json_path) if record.json_path else None,
                        record.title,
                        record.record_id,
                        record.date.isoformat() if record.date else None,
                        record.record_type,
                        record.error,
                        int(record.truncated),
                        record.encoding,
                        json.dumps(record.data, ensure_ascii=False)
                        if record.data is not None
                        else None,
                    ),
                )
                db.executemany(
                    "INSERT INTO attachments (record, name, subfolder, size, mtime,"
                    " grp, version, referenced) VALUES (?,?,?,?,?,?,?,?)",
                    [
                        (
                            position,
                            a.name,
                            a.subfolder,
                            a.size,
                            a.mtime,
                            a.group,
                            a.version.isoformat() if a.version else None,
                            int(a.referenced),
                        )
                        for a in record.attachments
                    ],
                )
                db.execute(
                    "INSERT INTO docs (rowid, app, title, body) VALUES (?,?,?,?)",
                    (position, record.app, record.title, build_body(record)),
                )
            db.execute(
                "INSERT OR REPLACE INTO meta (key, value) VALUES ('fingerprint', ?)",
                (fingerprint(self.root),),
            )
            db.execute(
                "INSERT OR REPLACE INTO meta (key, value) VALUES ('root', ?)",
                (str(self.root),),
            )
            db.execute(
                "INSERT OR REPLACE INTO meta (key, value) VALUES ('built', ?)",
                (datetime.now().isoformat(timespec="seconds"),),
            )

    # -- Cache -----------------------------------------------------------

    def is_current(self) -> bool:
        if not self.path.exists():
            return False
        try:
            db = self._connect()
            row = db.execute("SELECT value FROM meta WHERE key='fingerprint'").fetchone()
        except sqlite3.DatabaseError:
            return False
        return bool(row) and row["value"] == fingerprint(self.root)

    def load(self) -> ScanResult:
        """Kompletten Bestand aus dem Cache herstellen — ohne Zugriff auf den Export."""
        db = self._connect()
        result = ScanResult(root=self.root)
        attachments: dict[int, list[Attachment]] = {}
        for row in db.execute("SELECT * FROM attachments"):
            attachments.setdefault(row["record"], []).append(
                Attachment(
                    # wird unten auf den Datensatzordner bezogen gesetzt
                    path=Path(),
                    name=row["name"],
                    size=row["size"],
                    mtime=row["mtime"],
                    subfolder=row["subfolder"],
                    referenced=bool(row["referenced"]),
                    group=row["grp"],
                    version=datetime.fromisoformat(row["version"])
                    if row["version"]
                    else None,
                )
            )
        for row in db.execute("SELECT * FROM records ORDER BY id"):
            folder = Path(row["folder"])
            record = Record(
                app=row["app"],
                folder=folder,
                json_path=Path(row["json_path"]) if row["json_path"] else None,
                data=json.loads(row["payload"]) if row["payload"] else None,
                record_type=row["rectype"] or "",
                title=row["title"],
                record_id=row["record_id"],
                date=datetime.fromisoformat(row["recdate"]) if row["recdate"] else None,
                error=row["error"],
                truncated=bool(row["truncated"]),
                encoding=row["encoding"] or "utf-8",
            )
            for attachment in attachments.get(row["id"], []):
                attachment.path = folder / attachment.subfolder / attachment.name
                record.attachments.append(attachment)
            result.records.append(record)
        return result

    # -- Suche -----------------------------------------------------------

    def search(self, text: str, app: str | None = None, limit: int = 300) -> list[SearchHit]:
        query = build_query(text)
        if not query:
            return []
        # Steuerzeichen als Fundstellenmarker: der Rumpf ist Klartext und kann
        # spitze Klammern enthalten, HTML-artige Marker wären dort nicht
        # eindeutig vom Inhalt zu unterscheiden.
        sql = (
            "SELECT r.folder AS folder, d.app AS app, d.title AS title,"
            f" snippet(docs, 2, '{HIT_START}', '{HIT_END}', '…', 18) AS snip"
            " FROM docs d JOIN records r ON r.id = d.rowid"
            " WHERE docs MATCH ?"
        )
        params: list[object] = [query]
        if app:
            sql += " AND d.app = ?"
            params.append(app)
        sql += " ORDER BY bm25(docs, 1.0, 8.0, 1.0) LIMIT ?"
        params.append(limit)
        try:
            rows = self._connect().execute(sql, params).fetchall()
        except sqlite3.OperationalError:
            return []
        return [
            SearchHit(
                key=row["folder"],
                app=row["app"],
                title=row["title"],
                # Der Rumpf ist bereits Klartext; hier nur Umbrüche glätten.
                snippet=" ".join((row["snip"] or "").split()),
            )
            for row in rows
        ]
