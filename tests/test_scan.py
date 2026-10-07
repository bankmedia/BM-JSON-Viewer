"""Verzeichnis-Scan gegen einen Miniatur-Export."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest

from exitviewer import scan
from exitviewer.scan import group_attachments, split_version, unwrap_record


class TestStruktur:
    def test_exportwurzel_mit_anwendungsordner(self, sample_export: Path):
        result = scan.scan(sample_export)
        assert {r.app for r in result.records} == {"Protokolle"}
        assert len(result.records) == 4

    def test_direkt_geoeffneter_anwendungsordner(self, sample_app_folder: Path):
        """Der Anwender wählt oft den Anwendungsordner statt der Wurzel."""
        result = scan.scan(sample_app_folder)
        assert len(result.records) == 4
        assert {r.app for r in result.records} == {"Protokolle"}

    def test_ordner_ohne_json_ist_kein_datensatz(self, sample_export: Path):
        result = scan.scan(sample_export)
        assert all("Nur Dateien" not in str(r.folder) for r in result.records)

    def test_leeres_verzeichnis_meldet_sich(self, tmp_path: Path):
        result = scan.scan(tmp_path)
        assert result.records == []
        assert result.problems

    def test_hauptdatensatz_wird_ausgepackt(self):
        data, typ = unwrap_record({"Protokoll": {"id": 1}})
        assert data == {"id": 1} and typ == "Protokoll"

    def test_objekt_ohne_huelle_bleibt(self):
        data, typ = unwrap_record({"id": 1, "x": 2})
        assert data == {"id": 1, "x": 2} and typ == ""


class TestRobustheit:
    def test_defektes_json_bricht_den_scan_nicht_ab(self, sample_export: Path):
        result = scan.scan(sample_export)
        kaputt = [r for r in result.records if "Kaputt" in r.folder.name]
        assert len(kaputt) == 1
        assert kaputt[0].error is not None
        assert not kaputt[0].ok
        # Die übrigen Datensätze sind trotzdem da.
        assert sum(1 for r in result.records if r.ok) == 3

    def test_cp1252_wird_erkannt(self, sample_export: Path):
        """Der Exporter schreibt mit der Standardkodierung der JVM."""
        result = scan.scan(sample_export)
        ansi = next(r for r in result.records if "ANSI" in r.folder.name)
        assert ansi.encoding == "cp1252"
        assert ansi.data["Ort"] == "Köln"
        assert ansi.title == "Prüfung Größe"

    def test_utf8_bleibt_utf8(self, sample_export: Path):
        result = scan.scan(sample_export)
        voll = next(r for r in result.records if r.record_id == "15")
        assert voll.encoding == "utf-8"
        assert voll.title == "Grüße aus Köln"

    def test_uebergrosse_datei_wird_uebersprungen(self, sample_export: Path, monkeypatch):
        monkeypatch.setattr(scan, "MAX_JSON_BYTES", 10)
        result = scan.scan(sample_export)
        assert all(r.truncated or r.error for r in result.records)
        # Der Datensatz bleibt sichtbar statt zu verschwinden.
        assert len(result.records) == 4

    def test_abbruch_wird_beachtet(self, sample_export: Path):
        result = scan.scan(sample_export, cancelled=lambda: True)
        assert result.records == []
        assert any("abgebrochen" in p.lower() for p in result.problems)


class TestDatensatz:
    def test_titel_datum_und_id(self, sample_export: Path):
        result = scan.scan(sample_export)
        voll = next(r for r in result.records if r.record_id == "15")
        assert voll.title == "Grüße aus Köln"
        assert voll.record_type == "Protokoll"
        assert voll.date.astimezone().strftime("%d.%m.%Y") == "09.04.2020"

    def test_chronologisch_sortiert(self, sample_export: Path):
        result = scan.scan(sample_export)
        datiert = [r.date for r in result.records if r.date]
        assert datiert == sorted(datiert)

    def test_doppelte_ids_werden_gefunden(self, sample_export: Path, tmp_path: Path):
        zwilling = sample_export / "Protokolle" / "20200409_150116_Grüße aus Koeln"
        zwilling.mkdir()
        quelle = next(
            (sample_export / "Protokolle").glob("20200409_150116_Grüße aus Köln/*.json")
        )
        (zwilling / "kopie.json").write_bytes(quelle.read_bytes())

        result = scan.scan(sample_export)
        assert len(result.duplicate_ids()) == 1


class TestAnhaenge:
    def test_platte_ist_massgeblich(self, sample_export: Path):
        """Das Protokoll-PDF steht in keinem Feld des Datensatzes."""
        result = scan.scan(sample_export)
        voll = next(r for r in result.records if r.record_id == "15")
        namen = {a.name for a in voll.attachments}
        assert any(n.startswith("Protokoll (PDF)_") for n in namen)
        assert "Bericht.docx" in namen

    def test_unterordner_werden_mitgenommen(self, sample_export: Path):
        result = scan.scan(sample_export)
        voll = next(r for r in result.records if r.record_id == "15")
        bericht = next(a for a in voll.attachments if a.name == "Bericht.docx")
        assert bericht.subfolder == "Protokoll_Anhänge"

    def test_referenzierte_dateien_werden_markiert(self, sample_export: Path):
        result = scan.scan(sample_export)
        voll = next(r for r in result.records if r.record_id == "15")
        bericht = next(a for a in voll.attachments if a.name == "Bericht.docx")
        pdf = next(a for a in voll.attachments if a.name.startswith("Protokoll (PDF)"))
        assert bericht.referenced
        assert not pdf.referenced

    def test_haupt_json_ist_kein_anhang(self, sample_export: Path):
        result = scan.scan(sample_export)
        voll = next(r for r in result.records if r.record_id == "15")
        assert all(not a.name.endswith(".json") for a in voll.attachments)


class TestVersionen:
    @pytest.mark.parametrize(
        "name,basis",
        [
            ("Protokoll (PDF)_20260223_120227.pdf", "Protokoll (PDF)"),
            ("Protokolle_20200505_150428.pdf", "Protokolle"),
            ("Bericht.docx", "Bericht.docx"),
            ("ohne_zeitstempel_x.pdf", "ohne_zeitstempel_x.pdf"),
        ],
    )
    def test_basisname(self, name, basis):
        assert split_version(name)[0] == basis

    def test_zeitstempel_wird_gelesen(self):
        _, stamp = split_version("Protokoll (PDF)_20260223_120227.pdf")
        assert stamp == datetime(2026, 2, 23, 12, 2, 27)

    def test_gruppierung_neueste_zuerst(self, sample_export: Path):
        """Der Export lief versehentlich täglich — für den Revisor ist das eine
        Datei mit Versionen, keine drei Anhänge."""
        result = scan.scan(sample_export)
        voll = next(r for r in result.records if r.record_id == "15")
        gruppen = {g.name: g for g in group_attachments(voll.attachments)}
        pdf = gruppen["Protokoll (PDF)"]
        assert len(pdf.versions) == 3
        assert pdf.newest.version == datetime(2026, 2, 25, 11, 24, 6)
        assert len(pdf.older) == 2

    def test_einzeldateien_bleiben_einzeln(self, sample_export: Path):
        result = scan.scan(sample_export)
        voll = next(r for r in result.records if r.record_id == "15")
        gruppen = {g.name: g for g in group_attachments(voll.attachments)}
        assert len(gruppen["Bericht.docx"].versions) == 1
