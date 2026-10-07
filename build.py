"""Baut die auslieferbare EXE.

    python build.py

Erzeugt ``dist/ExitExportViewer.exe`` — eine einzelne Datei ohne
Installationsvoraussetzungen. Icon und Versionsressource entstehen mit, damit
die Datei signierbar ist und im Explorer nicht wie ein anonymes Binary aussieht;
unsignierte, merkmalslose EXE-Dateien landen in Banken regelmäßig in der
AV-Quarantäne.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from exitviewer import APP_NAME, APP_VERSION  # noqa: E402

HERE = Path(__file__).parent
BUILD = HERE / "build"
DIST = HERE / "dist"
NAME = "ExitExportViewer"
COMPANY = "Bank-Media"

#: Qt-Module, die der Viewer nicht benutzt. Ohne die Ausschlüsse landet die halbe
#: Qt-Distribution in der EXE — allen voran QtWebEngine, das ausgerechnet die
#: Browser-Engine mitliefern würde, die hier ausdrücklich nicht gewollt ist.
QT_AUSSCHLUESSE = [
    "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtWebEngineQuick",
    "PySide6.QtWebChannel", "PySide6.QtWebSockets",
    "PySide6.QtQml", "PySide6.QtQuick", "PySide6.QtQuick3D", "PySide6.QtQuickWidgets",
    "PySide6.Qt3DCore", "PySide6.Qt3DRender", "PySide6.Qt3DExtras",
    "PySide6.QtCharts", "PySide6.QtDataVisualization", "PySide6.QtGraphs",
    "PySide6.QtMultimedia", "PySide6.QtMultimediaWidgets",
    "PySide6.QtBluetooth", "PySide6.QtNfc", "PySide6.QtPositioning",
    "PySide6.QtSerialPort", "PySide6.QtSql", "PySide6.QtTest",
    "PySide6.QtPdf", "PySide6.QtPdfWidgets", "PySide6.QtDesigner", "PySide6.QtHelp",
    "PySide6.QtUiTools", "PySide6.QtScxml", "PySide6.QtSensors",
]

WEITERE_AUSSCHLUESSE = ["tkinter", "unittest", "pydoc", "doctest", "pytest", "PIL", "numpy"]


def erzeuge_icon(ziel: Path) -> Path | None:
    """Zeichnet ein schlichtes Aktensymbol, falls noch keines vorliegt."""
    if ziel.exists():
        return ziel
    try:
        from PySide6.QtCore import QRect, Qt
        from PySide6.QtGui import QColor, QFont, QGuiApplication, QImage, QPainter
    except ImportError:
        return None

    # QPainter auf QImage braucht eine QGuiApplication-Instanz.
    app = QGuiApplication.instance() or QGuiApplication([])

    image = QImage(256, 256, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)

    painter.setBrush(QColor("#10233b"))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawRoundedRect(QRect(24, 12, 208, 232), 22, 22)

    painter.setBrush(QColor("#f4f7fb"))
    painter.drawRoundedRect(QRect(48, 40, 160, 176), 12, 12)

    painter.setPen(QColor("#8fa4bf"))
    for index, breite in enumerate((104, 88, 112, 72, 96)):
        oben = 74 + index * 26
        painter.fillRect(QRect(72, oben, breite, 10), QColor("#c2d0e2"))

    painter.setPen(QColor("#1f5fa8"))
    font = QFont("Segoe UI", 58)
    font.setBold(True)
    painter.setFont(font)
    painter.drawText(image.rect().adjusted(0, 96, -22, 0), Qt.AlignmentFlag.AlignRight, "i")
    painter.end()

    ziel.parent.mkdir(parents=True, exist_ok=True)
    return ziel if image.save(str(ziel), "ICO") else None


def schreibe_versionsressource(ziel: Path) -> Path:
    """Windows-Versionsressource — ohne sie zeigt die EXE keine Herkunft."""
    haupt, neben, patch = (APP_VERSION.split(".") + ["0", "0", "0"])[:3]
    vier = f"{haupt}, {neben}, {patch}, 0"
    ziel.parent.mkdir(parents=True, exist_ok=True)
    ziel.write_text(
        f"""VSVersionInfo(
  ffi=FixedFileInfo(
    filevers=({vier}), prodvers=({vier}),
    mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo([
      StringTable('040704b0', [
        StringStruct('CompanyName', '{COMPANY}'),
        StringStruct('FileDescription', '{APP_NAME} — lesende Ansicht für Datenexporte'),
        StringStruct('FileVersion', '{APP_VERSION}'),
        StringStruct('InternalName', '{NAME}'),
        StringStruct('OriginalFilename', '{NAME}.exe'),
        StringStruct('ProductName', '{APP_NAME}'),
        StringStruct('ProductVersion', '{APP_VERSION}'),
        StringStruct('LegalCopyright', '{COMPANY}')])
    ]),
    VarFileInfo([VarStruct('Translation', [1031, 1200])])
  ]
)
""",
        encoding="utf-8",
    )
    return ziel


def main() -> int:
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        print("PyInstaller fehlt. Zuerst:  pip install -r requirements.txt")
        return 1

    for ordner in (BUILD, DIST):
        if ordner.exists():
            shutil.rmtree(ordner)

    icon = erzeuge_icon(HERE / "exitviewer" / "resources" / "app.ico")
    version_datei = schreibe_versionsressource(BUILD / "version.txt")
    css = HERE / "exitviewer" / "resources" / "akte.css"

    befehl = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean",
        "--onefile", "--windowed",
        "--name", NAME,
        "--distpath", str(DIST),
        "--workpath", str(BUILD / "work"),
        "--specpath", str(BUILD),
        # Das Stylesheet wird über importlib.resources geladen und muss deshalb
        # als Paketdatei im Bündel liegen.
        "--add-data", f"{css}{os.pathsep}exitviewer/resources",
        # Ohne die Zeitzonendaten kann zoneinfo unter Windows Europe/Berlin nicht
        # auflösen und sämtliche Zeitangaben blieben in UTC stehen.
        "--collect-all", "tzdata",
        "--version-file", str(version_datei),
    ]
    if icon:
        befehl += ["--icon", str(icon)]
    for modul in QT_AUSSCHLUESSE + WEITERE_AUSSCHLUESSE:
        befehl += ["--exclude-module", modul]
    # run.py statt exitviewer/__main__.py: PyInstaller führt sein Einstiegsmodul
    # als eigenständiges Skript aus, in dem die relativen Paketimporte scheitern.
    befehl.append(str(HERE / "run.py"))

    ergebnis = subprocess.run(befehl, cwd=HERE)
    if ergebnis.returncode != 0:
        return ergebnis.returncode

    exe = DIST / f"{NAME}.exe"
    if not exe.exists():
        print("Build lief durch, aber die EXE fehlt.")
        return 1

    print(f"\nFertig: {exe}  ({exe.stat().st_size / 1024 / 1024:.1f} MB)")
    print("\nVor der Verteilung signieren, sonst greift die AV-Quarantäne:")
    print(f'  signtool sign /fd SHA256 /tr <Zeitstempelserver> /td SHA256 "{exe}"')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
