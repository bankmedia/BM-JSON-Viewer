"""Mitgelieferte Dateien (Stylesheet). Als Paket, damit PyInstaller sie findet."""

from __future__ import annotations

from importlib.resources import files


def stylesheet() -> str:
    """Das Akten-Stylesheet als Text."""
    return files(__name__).joinpath("akte.css").read_text(encoding="utf-8")
