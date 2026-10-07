# Exit-Export Viewer

Bank-Media gibt Kunden bei Abkündigung einer Anwendung deren Daten als
JSON-Export auf einem Datenträger mit. Technisch lesbar ist das sofort, für
einen Revisor aber nicht — das hier ist der Versuch, aus dem Export wieder
etwas zu machen, das wie eine Akte aussieht statt wie ein Datenstruktur-Dump.
Kein Browser, kein Server, keine Installation — läuft als einzelne EXE direkt
vom Datenträger.

![Akten-Ansicht](docs/akte.png)

## Verwendung

```
ExitExportViewer.exe [Exportordner]
```

Ohne Argument geht's über **Datei → Exportordner öffnen …**. Funktioniert
sowohl mit der Exportwurzel als auch mit einem einzelnen Anwendungsordner.

| Tastenkürzel | Wirkung |
|---|---|
| `Strg+O` | Exportordner öffnen |
| `F5` | Neu einlesen (Zwischenspeicher verwerfen) |
| `Strg+F` | In die Volltextsuche springen |
| `Strg+L` | Leere Felder ein-/ausblenden |
| `Strg+T` | Technische Felder (GUIDs) ein-/ausblenden |

## Für die Freigabe

Die wichtigste Eigenschaft ist auch die unspektakulärste: Es wird nirgends
unterhalb des Exportordners geschrieben. Der Export darf also auf einem
schreibgeschützten Netzlaufwerk oder einem Archivdatenträger liegen, ohne dass
das zum Problem wird. Das ist nicht nur eine Behauptung im README —
`tests/test_readonly.py` fängt jeden schreibenden Dateizugriff unterhalb der
Wurzel ab und vergleicht zusätzlich Größen und Zeitstempel vor und nach einem
kompletten Durchlauf.

Netzwerk gibt's keines. Keine Sockets, kein lauschender Port, kein
localhost-Webserver — genau das, was bei einer Bank sofort Fragen aufwirft,
wenn es anders wäre. Die Akten-Ansicht lädt Bilder ausschließlich aus den
Anhängen des gerade geöffneten Datensatzes; jeder andere Verweis (`http`,
`file`, relativ) läuft ins Leere. Einzige Randnotiz: `Qt6Network.dll` liegt
trotzdem im Bündel, weil Qt6Gui sie unter Windows als Abhängigkeit mitzieht.
Benutzt wird sie nicht — lässt sich mit `Get-NetTCPConnection -OwningProcess
<PID>` gegenprüfen, falls das jemand genauer wissen will.

Rich-Text-Felder aus dem Export laufen durch eine Positivliste, bevor sie
angezeigt werden: `<script>`, `<iframe>`, `<style>` und `<object>` fallen
samt Inhalt raus, Attribute und Adressen (`href`, `src`) werden entfernt.
Der Suchindex liegt nicht im Export, sondern unter
`%LOCALAPPDATA%\ExitExportViewer\`, Fenstereinstellungen in der Registry unter
`HKCU\Software\Bank-Media\ExitExportViewer`. Wichtig für die Freigabe: Der
Index enthält den Textinhalt des Exports, liegt also nicht automatisch unter
denselben Zugriffsregeln wie der Export selbst.

Die EXE selbst ist eine Datei, ~46 MB, keine Laufzeitvoraussetzungen. Vor der
Verteilung muss sie signiert werden — unsigniert landet sie bei der
Banken-Virenschutzlösung erfahrungsgemäß zuverlässig in Quarantäne.

## Eigenheiten der Daten

Der Export stammt aus einem Groovy-Skript mit `JsonBuilder`, und das merkt
man an ein paar Stellen deutlich:

`JsonBuilder` schreibt Listen als Maps — `"Verteiler": {"1": "…", "2": "…"}`
statt eines Arrays. Zähler-Keys werden deshalb als Liste dargestellt,
Text-Keys als Tabelle mit Bezeichnungsspalte. Echte JSON-Arrays funktionieren
natürlich trotzdem.

Zeiten stehen im Export in UTC, auch wenn's nicht dransteht —
`"Datum": "…T14:00:00+0000"` ist ein Termin um 16:00 Uhr Ortszeit. Die
Anzeige rechnet alles nach `Europe/Berlin` um; Fristen ohne Uhrzeit bleiben
reine Datumsangaben.

Technische Felder (GUIDs, interne Schlüssel) werden am *Wert* erkannt, nicht
am Namen — bewusst so, weil der Export Felder wie
`"Datensatz Ersteller ID": "Christian Lever"` oder `"Status ID": "Offen"`
enthält, wo echter Inhalt in einem Feld mit technisch klingendem Namen steckt.
Eine Regel über den Feldnamen hätte genau diesen Inhalt versteckt.

Anhänge sind ein eigenes Kapitel: Das Protokoll-PDF taucht in keinem Feld des
Datensatzes auf, und `"Anhänge": {}` steht im JSON selbst dann, wenn im
Ordner Dateien liegen. Die Ansicht schaut deshalb zusätzlich auf die Platte
und hebt Dateien hervor, die im Datensatz namentlich erwähnt werden.
Versionierte Dateien nach dem Muster `<Name>_<yyyyMMdd_HHmmss>.<ext>` werden
dabei zu einem Eintrag mit aufklappbarer Versionshistorie zusammengefasst.
Bildverweise in Rich-Text-Feldern zeigen oft auf Portalpfade
(`userfiles/Image/…`), die es im Export gar nicht gibt — liegt die Datei als
Anhang daneben, wird sie trotzdem angezeigt, sonst erscheint ein benannter
Platzhalter statt eines kaputten Bildes, damit der Fehlbestand sichtbar
bleibt und nicht einfach verschwindet.

Und falls eine Datei mal kaputt ist: Unlesbare oder ungültige JSON-Dateien
reißen nichts ab, sondern erscheinen rot im Baum mit der Ursache dahinter.
Kodierung wird automatisch erkannt (UTF-8 mit und ohne BOM, sonst Rückfall
auf cp1252).

## Entwicklung

```bash
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt

.venv\Scripts\python -m pytest          # 168 Tests
.venv\Scripts\python run.py "C:\Temp\Export alle Protokolle"
.venv\Scripts\python build.py           # -> dist\ExitExportViewer.exe
```

### Aufbau

| Datei | Aufgabe |
|---|---|
| `exitviewer/scan.py` | Verzeichnis-Scan, Kodierungserkennung, Anhangserfassung |
| `exitviewer/fmt.py` | Wert-Kosmetik: Datum, Zahlen, Bool, GUID-Erkennung |
| `exitviewer/sanitize.py` | HTML-Positivliste für Feldwerte |
| `exitviewer/render.py` | Datensatz → Akten-HTML, Container-Erkennung |
| `exitviewer/index.py` | SQLite-FTS5-Index und Zwischenspeicher |
| `exitviewer/ui/` | Qt-Oberfläche |

### Messwerte

Einmal gegen einen echten Export gemessen: 242 Datensätze, 23.003 Dateien,
2,8 GB (davon gerade mal 1,38 MB JSON — der Rest sind Anhänge).

| Vorgang | Dauer |
|---|---|
| Vollständiger Scan und Indexaufbau | ~3 s |
| Öffnen aus dem Zwischenspeicher | ~0,4 s |

## Grenzen

Ein paar Dinge, die bewusst so sind oder einfach noch nicht gelöst wurden:

- Die Suche faltet Umlaute (`Prufung` findet `Prüfung`), aber nicht das ß —
  `Grosse` findet kein `Größe`.
- `Kategorie ID`, `Workflow ID` und `Projekt GUID` verweisen auf Daten
  außerhalb des Exports. Die lassen sich von hier aus nicht auflösen.
- Kein Druck, kein PDF-Export — war nie Anforderung, also gibt's das nicht.
