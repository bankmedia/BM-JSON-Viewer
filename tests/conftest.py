"""Miniatur-Export, der jede im echten Export gefundene Strukturform abbildet.

Die Daten werden erzeugt statt eingecheckt: nur so lassen sich Kodierungen
(cp1252 neben UTF-8) und defekte Dateien verlässlich festhalten, ohne dass ein
Editor oder die Versionsverwaltung sie unterwegs repariert.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

#: Ein Datensatz mit allen Formen: leere Map, numerisch+skalar,
#: numerisch+Objekt, Text+Objekt, Kontrollen, HTML mit Tabelle und Bild,
#: GUID-Werten und durchgängig leeren Feldern.
VOLLSTAENDIG = {
    "Protokoll": {
        "id": 15,
        "Ersteller - Voller Name": "Markus Ende",
        "Erstellungsdatum": "2020-04-09T13:01:16+0000",
        "Datum": "2020-04-09T14:00:00+0000",
        "Frist": "2020-12-30T23:00:00+0000",
        "Titel": None,
        "Thema": "Grüße aus Köln",
        "Status ID": "Offen",
        "Kategorie ID": "5F1CFE8A50ABDDAA7B0301791812984BBA0C1FF3",
        "Vertraulich": True,
        "Veröffentlicht": False,
        "Betrag": 12345.5,
        "Version": 1,
        "Inhalt": (
            '<p>Vor <strong>Ort</strong> besprochen.</p>'
            '<table><tr><td>TOP</td><td>Wer</td></tr></table>'
            '<img src="userfiles/Image/plan.png" alt="">'
            '<img src="userfiles/Image/fehlt.png" alt="">'
        ),
        "Anhänge": {"1": "Bericht.docx"},
        "Benutzerdefinierte Kontrollen": {
            "Projekt-Nr. - 4717A74113CD5FB9A8814FBAA67D6AD36F6343C7": {
                "String Value": "2",
                "Display Value": "0815",
            },
            "Kopie an GF? - 23877BBF302ECAD3D3C2E62732A02940416C0C42": {
                "Boolean Value": True
            },
        },
        "Aufgaben": {
            "29: Kunden anrufen": {
                "Datensatz ID": 29,
                "Titel": "Kunden anrufen",
                "Datensatz Ersteller ID": "Christian Lever",
                "Status ID": "Offen",
                "Projekt GUID": "6BA5CA56C8D50A5E766FEE1976F9144F2093CF82",
                "Verantwortlicher": None,
                "Anhänge": {},
            },
            "30: Angebot prüfen": {
                "Datensatz ID": 30,
                "Titel": "Angebot prüfen",
                "Datensatz Ersteller ID": "Sonja Weber",
                "Status ID": "Erledigt",
                "Verantwortlicher": None,
                "Anhänge": {},
            },
        },
        "Verteiler": {"1": "Christian Lever", "2": "Lisa Müller"},
        "Freigaben": {
            "1": {
                "Datensatz Guid": "ED4FA1BB882866856C1BFD03A2CD2439F9C5D741",
                "Anmerkung": "okay, passt!",
                "Freigeber": "Claudia Gerdes",
                "Freigegeben": True,
                "Position": 1,
            }
        },
        "Prüfungen": {},
        "Teilnehmer": {},
    }
}

SCHLICHT = {
    "Protokoll": {
        "id": 16,
        "Thema": "Sitzung im Süden",
        "Datum": "2021-06-30T16:42:00+0000",
        "Inhalt": "<p>Kurzer Text</p>",
        "Anhänge": {},
        "Aufgaben": {},
    }
}


def _write_json(path: Path, payload: dict, encoding: str = "utf-8") -> None:
    path.write_bytes(json.dumps(payload, ensure_ascii=False, indent=2).encode(encoding))


@pytest.fixture
def sample_export(tmp_path: Path) -> Path:
    """Exportwurzel mit einer Anwendung — die übliche Ablage."""
    root = tmp_path / "Export"
    app = root / "Protokolle"

    voll = app / "20200409_150116_Grüße aus Köln"
    voll.mkdir(parents=True)
    _write_json(voll / "20200409_150116_Grüße aus Köln.json", VOLLSTAENDIG)
    # Anhänge: das Protokoll-PDF taucht im JSON nicht auf, kommt aber in vielen
    # Tagesfassungen vor; der genannte Anhang liegt im Unterordner.
    for stamp in ("20260223_120227", "20260224_112439", "20260225_112406"):
        (voll / f"Protokoll (PDF)_{stamp}.pdf").write_bytes(b"%PDF-1.4 test")
    (voll / "Protokoll_Anhänge").mkdir()
    (voll / "Protokoll_Anhänge" / "Bericht.docx").write_bytes(b"docx")
    (voll / "Protokoll_Anhänge" / "plan.png").write_bytes(
        # 1x1-PNG, damit die Bildauflösung etwas Echtes zu laden hat
        bytes.fromhex(
            "89504e470d0a1a0a0000000d494844520000000100000001080600000"
            "01f15c4890000000a49444154789c6360000002000100ffff03000006"
            "0005570cf50000000049454e44ae426082"
        )
    )

    schlicht = app / "20210630_164200_Sitzung im Sueden"
    schlicht.mkdir(parents=True)
    _write_json(schlicht / "20210630_164200_Sitzung im Sueden.json", SCHLICHT)

    # Kodierung: derselbe Datensatz, aber cp1252 statt UTF-8.
    ansi = app / "20220101_090000_Umlaute ANSI"
    ansi.mkdir(parents=True)
    _write_json(
        ansi / "20220101_090000_Umlaute ANSI.json",
        {"Protokoll": {"id": 17, "Thema": "Prüfung Größe", "Ort": "Köln"}},
        encoding="cp1252",
    )

    # Defektes JSON: darf den Scan nicht abbrechen.
    kaputt = app / "20220202_100000_Kaputt"
    kaputt.mkdir(parents=True)
    (kaputt / "20220202_100000_Kaputt.json").write_text(
        '{"Protokoll": {"id": 18,,}', encoding="utf-8"
    )

    # Ordner ganz ohne JSON ist kein Datensatz.
    (app / "Nur Dateien").mkdir(parents=True)
    (app / "Nur Dateien" / "liesmich.txt").write_text("kein Datensatz", encoding="utf-8")

    return root


@pytest.fixture
def sample_app_folder(sample_export: Path) -> Path:
    """Der Anwendungsordner selbst — so öffnet der Anwender ihn oft direkt."""
    return sample_export / "Protokolle"
