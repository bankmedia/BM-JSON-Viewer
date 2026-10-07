"""Volltextindex, Cache und Suchabfragen."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from exitviewer import index, scan
from exitviewer.index import SearchIndex, build_query


@pytest.fixture
def lokaler_cache(tmp_path: Path, monkeypatch):
    """Index in ein Wegwerf-Verzeichnis statt in das echte Nutzerprofil."""
    ziel = tmp_path / "appdata"
    ziel.mkdir()
    monkeypatch.setenv("LOCALAPPDATA", str(ziel))
    return ziel


@pytest.fixture
def gebaut(sample_export: Path, lokaler_cache: Path):
    result = scan.scan(sample_export)
    ix = SearchIndex(sample_export)
    ix.build(result)
    yield result, ix
    ix.close()


class TestAbfrage:
    @pytest.mark.parametrize(
        "eingabe,erwartet",
        [
            ("Köln", '"Köln"*'),
            ("zwei Wörter", '"zwei" AND "Wörter"*'),
            ("", ""),
            ("   ", ""),
        ],
    )
    def test_uebersetzung(self, eingabe, erwartet):
        assert build_query(eingabe) == erwartet

    @pytest.mark.parametrize("eingabe", ['"', "*", "AND OR NOT", "a(b)c", "^x", '"" OR 1=1'])
    def test_sonderzeichen_zerlegen_die_abfrage_nicht(self, eingabe, gebaut):
        """Freitext darf nicht in die FTS-Syntax durchschlagen."""
        _, ix = gebaut
        ix.search(eingabe)  # keine Ausnahme


class TestSuche:
    def test_findet_feldwert(self, gebaut):
        _, ix = gebaut
        assert any("Grüße" in h.title for h in ix.search("Grüße"))

    def test_findet_text_im_html_feld(self, gebaut):
        """Rich-Text muss im Klartext durchsuchbar sein, nicht als Markup."""
        _, ix = gebaut
        assert ix.search("besprochen")
        assert not ix.search("strong")

    def test_findet_deutsches_datum(self, gebaut):
        _, ix = gebaut
        assert ix.search("09.04.2020")

    def test_findet_iso_datum(self, gebaut):
        _, ix = gebaut
        assert ix.search("2020-04-09")

    def test_findet_dateinamen(self, gebaut):
        _, ix = gebaut
        assert ix.search("Bericht.docx")

    def test_umlaute_werden_gefaltet(self, gebaut):
        """``remove_diacritics`` lässt "Prufung" auf "Prüfung" greifen.

        Das ß bleibt davon unberührt — ``unicode61`` bildet es nicht auf "ss"
        ab, "Grosse" findet also kein "Größe".
        """
        _, ix = gebaut
        assert ix.search("Prufung")
        assert ix.search("Koln")

    def test_filter_auf_anwendung(self, gebaut):
        _, ix = gebaut
        assert ix.search("Grüße", app="Protokolle")
        assert not ix.search("Grüße", app="Andere Anwendung")

    def test_treffer_hat_ausschnitt(self, gebaut):
        _, ix = gebaut
        treffer = ix.search("besprochen")[0]
        assert index.HIT_START in treffer.snippet
        assert "besprochen" in treffer.snippet

    def test_ohne_treffer(self, gebaut):
        _, ix = gebaut
        assert ix.search("Zeichenkettedieesnichtgibt") == []


class TestCache:
    def test_neu_gebaut_ist_aktuell(self, gebaut):
        _, ix = gebaut
        assert ix.is_current()

    def test_bestand_wird_vollstaendig_wiederhergestellt(self, gebaut, sample_export):
        original, ix = gebaut
        geladen = ix.load()
        assert len(geladen.records) == len(original.records)
        assert {r.title for r in geladen.records} == {r.title for r in original.records}
        assert sum(len(r.attachments) for r in geladen.records) == sum(
            len(r.attachments) for r in original.records
        )

    def test_anhangspfade_zeigen_wieder_richtig(self, gebaut):
        _, ix = gebaut
        geladen = ix.load()
        voll = next(r for r in geladen.records if r.record_id == "15")
        bericht = next(a for a in voll.attachments if a.name == "Bericht.docx")
        assert bericht.path.exists()

    def test_datum_ueberlebt_den_cache(self, gebaut):
        _, ix = gebaut
        voll = next(r for r in ix.load().records if r.record_id == "15")
        assert voll.date is not None

    def test_neuer_datensatz_entwertet_den_cache(self, gebaut, sample_export: Path):
        _, ix = gebaut
        neu = sample_export / "Protokolle" / "20230101_090000_Neu"
        neu.mkdir()
        (neu / "neu.json").write_text('{"Protokoll": {"id": 99}}', encoding="utf-8")
        assert not ix.is_current()

    def test_fingerprint_ist_stabil(self, sample_export: Path):
        """Nach dem Einschwingen muss derselbe Bestand denselben Wert ergeben.

        Windows schreibt den mtime eines Verzeichnisses verzögert zurück — kurz
        nach dem Anlegen ändert er sich noch. Bei einem Archivexport sind die
        Zeitstempel Jahre alt; träte es doch auf, wäre die Folge lediglich ein
        überflüssiger Neuaufbau, nie ein fälschlich als gültig angesehener Cache.
        """
        for _ in range(20):
            erst = index.fingerprint(sample_export)
            dann = index.fingerprint(sample_export)
            if erst == dann:
                break
            time.sleep(0.05)
        assert erst == dann

    def test_cache_liegt_ausserhalb_des_exports(self, gebaut, sample_export, lokaler_cache):
        """Der Export kann auf einem schreibgeschützten Laufwerk liegen."""
        _, ix = gebaut
        assert lokaler_cache in ix.path.parents
        assert sample_export not in ix.path.parents
