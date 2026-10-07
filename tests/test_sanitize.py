"""HTML-Härtung der Feldwerte."""

from __future__ import annotations

import pytest

from exitviewer.sanitize import IMAGE_SCHEME, sanitize_html


class TestEntfernt:
    @pytest.mark.parametrize(
        "quelle,verboten",
        [
            ("<script>alert(1)</script>Rest", "alert"),
            ("<style>body{x}</style>Rest", "body{x}"),
            ("<iframe src='http://x'></iframe>Rest", "iframe"),
            ("<object data='x'></object>Rest", "object"),
        ],
    )
    def test_gefaehrliche_elemente_samt_inhalt(self, quelle, verboten):
        result = sanitize_html(quelle)
        assert verboten not in result
        assert "Rest" in result

    def test_alle_adressen_verschwinden(self):
        """Kein Feldwert darf eine ladbare Adresse in das Dokument bringen."""
        result = sanitize_html('<a href="http://example.invalid">Klick</a>')
        assert "href" not in result and "example.invalid" not in result
        assert "Klick" in result

    def test_attribute_werden_verworfen(self):
        result = sanitize_html('<p onclick="boom()" style="color:red" class="x">Text</p>')
        assert result == "<p>Text</p>"

    def test_struktur_attribute_bleiben(self):
        assert sanitize_html('<td colspan="2">z</td>') == '<td colspan="2">z</td>'

    def test_nicht_numerische_struktur_attribute_fallen_weg(self):
        assert sanitize_html('<td colspan="javascript:x">z</td>') == "<td>z</td>"


class TestErhalten:
    def test_inhaltstragende_tags(self):
        quelle = "<p>Vor <strong>Ort</strong> und <em>zeitnah</em></p>"
        assert sanitize_html(quelle) == quelle

    def test_tabellen_bleiben(self):
        """Die Rich-Text-Felder enthalten echte Tabellen — sie sind der Grund
        für die HTML-Darstellung überhaupt."""
        quelle = "<table><tbody><tr><td>TOP</td><td>Wer</td></tr></tbody></table>"
        assert sanitize_html(quelle) == quelle

    def test_unbekanntes_tag_behaelt_seinen_text(self):
        assert sanitize_html("<marquee>Text</marquee>") == "Text"

    def test_text_wird_maskiert(self):
        assert sanitize_html("3 < 5 & 6 > 2") == "3 &lt; 5 &amp; 6 &gt; 2"

    def test_falsche_verschachtelung_wird_geschlossen(self):
        result = sanitize_html("<b>eins<i>zwei</b>")
        assert result.count("<b>") == result.count("</b>") == 1
        assert result.count("<i>") == result.count("</i>") == 1

    def test_nicht_geschlossene_tags(self):
        result = sanitize_html("<p>offen")
        assert result == "<p>offen</p>"


class TestBilder:
    def test_unbekanntes_bild_wird_als_fehlbestand_ausgewiesen(self):
        result = sanitize_html('<img src="userfiles/Image/fehlt.png">')
        assert "Bild nicht im Export enthalten: fehlt.png" in result
        assert "<img" not in result

    def test_bekanntes_bild_wird_eingebunden(self):
        """Die ``src`` zeigt auf einen Portalpfad, die Datei liegt aber als
        Anhang im Datensatzordner — dann wird sie von dort gezeigt."""
        result = sanitize_html(
            '<img src="userfiles/Image/plan.png">', known_images={"plan.png"}
        )
        assert result == f'<img src="{IMAGE_SCHEME}:plan.png">'

    def test_aufloesung_ist_unabhaengig_von_gross_kleinschreibung(self):
        result = sanitize_html('<img src="X/PLAN.PNG">', known_images={"plan.png"})
        assert result.startswith(f'<img src="{IMAGE_SCHEME}:')

    def test_remote_bild_ohne_entsprechung_wird_nicht_geladen(self):
        result = sanitize_html('<img src="http://example.invalid/track.png">')
        assert "http" not in result
        assert "Bild nicht im Export" in result

    def test_bild_ohne_quelle(self):
        assert "ohne Namen" in sanitize_html("<img>")

    def test_selbstschliessendes_bild(self):
        result = sanitize_html('<img src="a/plan.png" />', known_images={"plan.png"})
        assert f"{IMAGE_SCHEME}:plan.png" in result


def test_kaputte_eingabe_wird_zu_klartext():
    """Eine Ausnahme mitten in der Akte wäre schlimmer als roher Text."""
    assert sanitize_html("<<<>>><p") is not None
