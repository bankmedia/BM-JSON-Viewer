"""Akten-Renderer: Container-Erkennung und Sichtbarkeitsregeln."""

from __future__ import annotations

import pytest

from exitviewer import render
from exitviewer.model import Record
from exitviewer.render import RenderOptions, render_record


def akte(data: dict, **options) -> str:
    record = Record(app="A", folder=__import__("pathlib").Path("x"), data=data, title="T")
    return render_record(record, RenderOptions(**options))


class TestKlassifikation:
    @pytest.mark.parametrize(
        "value,erwartet",
        [
            ({}, render.EMPTY),
            ([], render.EMPTY),
            ("Text", render.SCALAR),
            # Groovy schreibt Listen als Map mit Zähler-Keys.
            ({"1": "Anna", "2": "Bert"}, render.LIST_SCALAR),
            ({"1": {"a": 1}, "2": {"a": 2}}, render.TABLE_ROWS),
            ({"29: Titel": {"a": 1}}, render.TABLE_KEYED),
            ({"a": 1, "b": 2}, render.FORM),
            ({"a": 1, "b": {"c": 2}}, render.MIXED),
            # Echte Arrays kommen im Export nicht vor, sollen aber tragen.
            (["Anna", "Bert"], render.LIST_SCALAR),
            ([{"a": 1}], render.TABLE_ROWS),
        ],
    )
    def test_formen(self, value, erwartet):
        assert render.classify(value) == erwartet

    def test_kontrollen_werden_an_der_struktur_erkannt(self):
        controls = {
            "Projekt-Nr. - 4717A74113CD5FB9A8814FBAA67D6AD36F6343C7": {
                "String Value": "2",
                "Display Value": "0815",
            }
        }
        assert render.classify(controls) == render.CONTROLS

    def test_gewoehnliche_objektmap_ist_keine_kontrollmap(self):
        assert render.classify({"29: X": {"Titel": "X"}}) == render.TABLE_KEYED


class TestSichtbarkeit:
    def test_leere_felder_standardmaessig_verborgen(self):
        html = akte({"Ort": None, "Thema": "Da"})
        assert "Thema" in html and "Ort" not in html

    def test_leere_felder_auf_wunsch(self):
        assert "Ort" in akte({"Ort": None, "Thema": "Da"}, show_empty=True)

    def test_guid_wert_verborgen_klartext_bleibt(self):
        """Die wertbasierte Regel entscheidet, nicht der Feldname."""
        html = akte(
            {
                "Projekt GUID": "6BA5CA56C8D50A5E766FEE1976F9144F2093CF82",
                "Status ID": "Offen",
            }
        )
        assert "Offen" in html
        assert "6BA5CA56" not in html

    def test_technische_felder_auf_wunsch(self):
        html = akte(
            {"Projekt GUID": "6BA5CA56C8D50A5E766FEE1976F9144F2093CF82"},
            show_technical=True,
        )
        assert "6BA5CA56" in html

    def test_leerer_abschnitt_erscheint_nicht(self):
        assert "Freigaben" not in akte({"Thema": "X", "Freigaben": {}})


class TestTabellen:
    def test_zaehler_map_wird_zur_aufzaehlung(self):
        html = akte({"Verteiler": {"1": "Anna", "2": "Bert"}})
        assert "<ul>" in html and "Anna" in html
        # Der Zähler-Key ist keine Information und darf nicht auftauchen.
        assert "<td" not in html.split("<ul>")[1]

    def test_zaehler_map_mit_objekten_ohne_schluesselspalte(self):
        html = akte({"Freigaben": {"1": {"Freigeber": "Claudia", "Position": 1}}})
        assert "Freigeber" in html and "Bezeichnung" not in html

    def test_redundanter_schluessel_entfaellt(self):
        """``"29: Kunden anrufen"`` wiederholt nur ID und Titel der Zeile."""
        html = akte(
            {"Aufgaben": {"29: Kunden anrufen": {"Datensatz ID": 29, "Titel": "Kunden anrufen"}}}
        )
        assert "Bezeichnung" not in html

    def test_eigenstaendiger_schluessel_bleibt(self):
        html = akte({"Posten": {"Sonderfall Nord": {"Menge": 3}}})
        assert "Bezeichnung" in html and "Sonderfall Nord" in html

    def test_durchgehend_leere_spalte_entfaellt(self):
        html = akte({"Zeilen": {"1": {"A": "x", "B": None}, "2": {"A": "y", "B": None}}})
        assert ">A<" in html and ">B<" not in html

    def test_lange_liste_wird_gekuerzt(self):
        html = akte({"Viele": {str(i): f"Wert {i}" for i in range(1, 40)}}, list_limit=10)
        assert "alle 39 Einträge anzeigen" in html
        assert "Wert 39" not in html

    def test_aufgeklappte_liste_zeigt_alles(self):
        # Der Pfad im Aufklapp-Link beginnt beim Datensatztyp; fehlt der,
        # heißt die Wurzel "root".
        html = akte(
            {"Viele": {str(i): f"Wert {i}" for i in range(1, 40)}},
            list_limit=10,
            expanded={"root.Viele"},
        )
        assert "Wert 39" in html
        assert "anzeigen</a>" not in html


class TestKontrollen:
    def test_display_value_schlaegt_string_value(self):
        """``String Value`` trägt den internen Schlüssel, ``Display Value`` den
        Text, den der Anwender gesehen hat."""
        html = akte(
            {
                "Benutzerdefinierte Kontrollen": {
                    "Projekt-Nr. - 4717A74113CD5FB9A8814FBAA67D6AD36F6343C7": {
                        "String Value": "2",
                        "Display Value": "0815",
                    }
                }
            }
        )
        assert "0815" in html
        assert ">2<" not in html

    def test_guid_wird_aus_dem_label_entfernt(self):
        html = akte(
            {
                "Benutzerdefinierte Kontrollen": {
                    "Kopie an GF? - 23877BBF302ECAD3D3C2E62732A02940416C0C42": {
                        "Boolean Value": True
                    }
                }
            }
        )
        assert "Kopie an GF?" in html
        assert "23877BBF" not in html
        assert "Ja" in html


class TestWerte:
    def test_datum_wird_lokalisiert(self):
        html = akte({"Erstellungsdatum": "2020-04-09T13:01:16+0000"})
        assert "09.04.2020 15:01" in html

    def test_bool_wird_deutsch(self):
        html = akte({"Vertraulich": True, "Offen": False})
        assert "Ja" in html and "Nein" in html

    def test_farbwert_bekommt_ein_muster(self):
        html = akte({"Projektstatus": "#80ff80"})
        assert "swatch" in html and "#80ff80" in html

    def test_html_feld_wird_gesaeubert(self):
        html = akte({"Inhalt": '<p onclick="x">Text</p><script>boom()</script>'})
        assert "boom" not in html and "onclick" not in html
        assert "Text" in html

    def test_titel_und_kopfzeile(self):
        record = Record(
            app="Protokolle",
            folder=__import__("pathlib").Path("x"),
            data={"id": 15},
            title="Grüße",
            record_type="Protokoll",
            record_id="15",
        )
        html = render_record(record)
        assert "<h1>Grüße</h1>" in html
        assert "Protokoll" in html and "ID 15" in html

    def test_fehlerhafter_datensatz_zeigt_die_ursache(self):
        record = Record(
            app="A",
            folder=__import__("pathlib").Path("x"),
            title="Kaputt",
            error="JSON ungültig (Zeile 1)",
        )
        html = render_record(record)
        assert "JSON ungültig" in html
