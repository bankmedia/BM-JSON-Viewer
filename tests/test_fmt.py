"""Wert-Kosmetik."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from exitviewer import fmt


class TestDatum:
    def test_utc_wird_nach_ortszeit_umgerechnet(self):
        """Der Kern der Datumsbehandlung: die Daten stehen in UTC.

        ``13:01:16+0000`` ist in Deutschland 15:01 (Sommerzeit). Ohne diese
        Umrechnung liest der Revisor durchgehend die falsche Uhrzeit.
        """
        parsed = fmt.parse_datetime("2020-04-09T13:01:16+0000")
        assert fmt.format_datetime(parsed) == "09.04.2020 15:01"

    def test_winterzeit(self):
        parsed = fmt.parse_datetime("2021-01-15T13:00:00+0000")
        assert fmt.format_datetime(parsed) == "15.01.2021 14:00"

    def test_mitternacht_wird_ohne_uhrzeit_gezeigt(self):
        """``2020-12-30T23:00:00+0000`` ist der 31.12. um 00:00 Ortszeit —
        eine Frist ohne Uhrzeitangabe."""
        parsed = fmt.parse_datetime("2020-12-30T23:00:00+0000")
        assert fmt.format_datetime(parsed) == "31.12.2020"

    @pytest.mark.parametrize(
        "text",
        [
            "2020-04-09T13:01:16+0000",
            "2020-04-09T13:01:16+00:00",
            "2020-04-09T13:01:16Z",
            "2020-04-09 13:01:16",
            "2020-04-09T13:01:16.250+0000",
        ],
    )
    def test_schreibweisen(self, text):
        assert fmt.parse_datetime(text) is not None

    @pytest.mark.parametrize(
        "text", ["", "kein Datum", "2020-04-09", "13:01:16", "4711", None, 17]
    )
    def test_nichtdaten(self, text):
        assert fmt.parse_datetime(text) is None

    def test_naiver_zeitstempel_gilt_als_utc(self):
        parsed = fmt.parse_datetime("2020-04-09 13:01:16")
        assert parsed.tzinfo is not None
        assert parsed.utcoffset() == timezone.utc.utcoffset(None)


class TestSkalare:
    @pytest.mark.parametrize(
        "value,expected",
        [
            (None, "–"),
            (True, "Ja"),
            (False, "Nein"),
            (5, "5"),
            (1234, "1234"),
            (12345, "12.345"),
            (12345.5, "12.345,5"),
            (0.25, "0,25"),
            ("  Text  ", "Text"),
        ],
    )
    def test_format_scalar(self, value, expected):
        assert fmt.format_scalar(value) == expected

    def test_vierstellige_zahlen_ohne_tausenderpunkt(self):
        """Sonst würde die Datensatz-ID 1234 als ``1.234`` erscheinen."""
        assert fmt.format_number(1234) == "1234"
        assert fmt.format_number(10000) == "10.000"


class TestTechnisch:
    @pytest.mark.parametrize(
        "value",
        [
            "5F1CFE8A50ABDDAA7B0301791812984BBA0C1FF3",
            "6ba5ca56c8d50a5e766fee1976f9144f2093cf82",
            "0123456789ABCDEF0123456789ABCDEF",
        ],
    )
    def test_guid_erkannt(self, value):
        assert fmt.is_guid(value)

    @pytest.mark.parametrize("value", ["Offen", "Christian Lever", "", "1234", None, 15])
    def test_kein_guid(self, value):
        assert not fmt.is_guid(value)

    def test_ausblenden_ist_wertbasiert_nicht_namensbasiert(self):
        """Der Export nennt Klartextfelder ``… ID``.

        ``"Datensatz Ersteller ID": "Christian Lever"`` und
        ``"Status ID": "Offen"`` sind echter Inhalt. Eine Regel über den
        Feldnamen würde sie verstecken.
        """
        assert not fmt.is_technical("Datensatz Ersteller ID", "Christian Lever")
        assert not fmt.is_technical("Status ID", "Offen")
        assert fmt.is_technical("Projekt GUID", "6BA5CA56C8D50A5E766FEE1976F9144F2093CF82")

    def test_guid_suffix_wird_vom_label_getrennt(self):
        label = "Projekt-Nr. - 4717A74113CD5FB9A8814FBAA67D6AD36F6343C7"
        assert fmt.strip_guid_suffix(label) == "Projekt-Nr."

    def test_label_ohne_guid_bleibt(self):
        assert fmt.strip_guid_suffix("Ersteller - Voller Name") == "Ersteller - Voller Name"


class TestLabels:
    @pytest.mark.parametrize(
        "key,expected",
        [
            ("Ersteller - Voller Name", "Ersteller - Voller Name"),
            ("customerSince", "Customer Since"),
            ("customer_since", "Customer since"),
            ("id", "ID"),
            ("guid", "GUID"),
        ],
    )
    def test_prettify(self, key, expected):
        assert fmt.prettify_label(key) == expected


class TestContainer:
    def test_jsonbuilder_map_gilt_als_liste(self):
        """Groovy schreibt Listen als ``{"1": …}`` — daran hängt die gesamte
        Container-Erkennung."""
        assert fmt.is_numeric_key_map({"1": "a", "2": "b"})
        assert not fmt.is_numeric_key_map({"1": "a", "x": "b"})
        assert not fmt.is_numeric_key_map({})

    @pytest.mark.parametrize(
        "value,leer",
        [(None, True), ("", True), ("  ", True), ({}, True), ([], True),
         (0, False), (False, False), ("x", False), ({"a": 1}, False)],
    )
    def test_is_empty(self, value, leer):
        assert fmt.is_empty(value) is leer


class TestAuswahl:
    def test_titel_nimmt_ersten_belegten_kandidaten(self):
        """``Titel`` ist im echten Export durchgängig null, ``Thema`` trägt den
        Titel — die Kette darf nicht beim ersten *vorhandenen* Key stehen
        bleiben."""
        record = {"Titel": None, "Thema": "Grüße aus Köln"}
        assert fmt.pick_title(record, "ordner") == "Grüße aus Köln"

    def test_titel_faellt_auf_ordnernamen_zurueck(self):
        assert fmt.pick_title({"Titel": None}, "20200409_Ordner") == "20200409_Ordner"

    def test_datum_bevorzugt_den_termin(self):
        record = {
            "Erstellungsdatum": "2020-04-09T13:01:16+0000",
            "Datum": "2020-05-01T10:00:00+0000",
        }
        assert fmt.pick_date(record, "").month == 5

    def test_datum_faellt_auf_ordnerstempel_zurueck(self):
        assert fmt.pick_date({}, "20200409_150116_Thema").date().isoformat() == "2020-04-09"

    def test_record_id(self):
        assert fmt.pick_record_id({"id": 15}) == "15"
        assert fmt.pick_record_id({"kein": "treffer"}) is None


class TestHtml:
    def test_html_wird_erkannt(self):
        assert fmt.looks_like_html("<p>Text</p>")
        assert not fmt.looks_like_html("2 < 3 und 4 > 1")

    def test_html_zu_text(self):
        text = fmt.html_to_text("<p>Eins</p><p>Zwei &amp; Drei</p>")
        assert "Eins" in text and "Zwei & Drei" in text
        assert "<" not in text

    def test_skript_inhalt_faellt_weg(self):
        assert "alert" not in fmt.html_to_text("<script>alert(1)</script>Text")
