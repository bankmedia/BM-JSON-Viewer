"""Nachweis, dass der Export unangetastet bleibt.

Die Anforderung ist keine Stilfrage: der Export liegt womöglich auf einem
schreibgeschützten Netzlaufwerk oder auf dem Datenträger, den der Kunde als
Archiv erhalten hat. Eine Änderung daran wäre ein Revisionsmangel.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from exitviewer import render, scan
from exitviewer.index import SearchIndex
from exitviewer.render import RenderOptions, render_record


def abbild(root: Path) -> dict[str, tuple[int, int]]:
    """Jede Datei mit Größe und Änderungszeit (in Nanosekunden)."""
    zustand: dict[str, tuple[int, int]] = {}
    for ordner, _unterordner, dateien in os.walk(root):
        for name in dateien:
            pfad = Path(ordner) / name
            stat = pfad.stat()
            zustand[str(pfad.relative_to(root))] = (stat.st_size, stat.st_mtime_ns)
    return zustand


@pytest.fixture
def lokaler_cache(tmp_path: Path, monkeypatch) -> Path:
    ziel = tmp_path / "appdata"
    ziel.mkdir()
    monkeypatch.setenv("LOCALAPPDATA", str(ziel))
    return ziel


def test_scan_index_und_darstellung_lassen_den_export_unveraendert(
    sample_export: Path, lokaler_cache: Path
):
    vorher = abbild(sample_export)

    result = scan.scan(sample_export)
    ix = SearchIndex(sample_export)
    ix.build(result)
    ix.load()
    ix.search("Grüße")
    for record in result.records:
        render_record(record, RenderOptions(show_empty=True, show_technical=True))
        render.inline_images(record)
    ix.close()

    nachher = abbild(sample_export)

    assert set(nachher) == set(vorher), "Dateien wurden angelegt oder entfernt"
    veraendert = {k for k in vorher if vorher[k] != nachher[k]}
    assert not veraendert, f"Dateien wurden verändert: {sorted(veraendert)}"


def test_der_index_landet_nicht_im_export(sample_export: Path, lokaler_cache: Path):
    result = scan.scan(sample_export)
    ix = SearchIndex(sample_export)
    ix.build(result)
    ix.close()

    assert ix.path.exists()
    assert lokaler_cache in ix.path.parents
    assert not list(sample_export.rglob("*.db"))


def test_scan_kommt_ohne_schreibrecht_aus(sample_export: Path, lokaler_cache: Path, monkeypatch):
    """Fängt jeden schreibenden ``open``-Aufruf unterhalb der Exportwurzel ab.

    Wirksamer als das Prüfen von Zeitstempeln: ein Schreibversuch schlägt hier
    sofort fehl, statt nur eine geänderte Datei zu hinterlassen.
    """
    echtes_open = open
    wurzel = str(sample_export.resolve()).lower()

    def wachendes_open(file, mode="r", *args, **kwargs):
        pfad = str(Path(file).resolve()).lower()
        if pfad.startswith(wurzel) and any(z in mode for z in ("w", "a", "x", "+")):
            raise AssertionError(f"Schreibzugriff auf den Export: {file} (Modus {mode!r})")
        return echtes_open(file, mode, *args, **kwargs)

    monkeypatch.setattr("builtins.open", wachendes_open)

    result = scan.scan(sample_export)
    ix = SearchIndex(sample_export)
    ix.build(result)
    ix.search("Grüße")
    ix.close()

    assert sum(1 for r in result.records if r.ok) == 3
