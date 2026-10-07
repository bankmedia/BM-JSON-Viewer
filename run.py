"""Startskript.

Getrennt von ``exitviewer/__main__.py``, weil PyInstaller sein Einstiegsmodul
als eigenständiges Skript ausführt — dort greifen die relativen Importe des
Pakets nicht. Dieses Skript importiert absolut und funktioniert in beiden
Welten.

    python run.py [Exportordner]
"""

from __future__ import annotations

from exitviewer.__main__ import main

if __name__ == "__main__":
    raise SystemExit(main())
