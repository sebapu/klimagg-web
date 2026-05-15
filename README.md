# KlimaGG-Web

**KlimaGG-Web** ist die öffentliche Codebasis der Webseite zum Klima-Generationen-Gesetz (KlimaGG: www.klimagg.de / www.klima-generationen-gesetz.de).

Die Webseite dient als offene Beteiligungsplattform für die Entwicklung, Diskussion und Verbesserung eines deutschen Klimaschutz-Gesetzentwurfs. Nutzerinnen und Nutzer können den Gesetzentwurf lesen, Artikel bewerten, Änderungsvorschläge kommentieren und Beiträge im Review-Prozess einordnen.

Ziel des Projekts ist ein transparenter, nachvollziehbarer und niedrigschwelliger Beteiligungsprozess für ein Gesetz, das Klimaschutz, Freiheit, Infrastrukturmodernisierung und Generationengerechtigkeit verbindet.

## Projektstand

Dieses Repository enthält die öffentliche technische Basis der Webseite. Nicht enthalten sind produktive Datenbanken, lokale Umgebungsdateien, private Secrets, Server-Konfigurationen und personenbezogene Daten.

Die produktive Webseite läuft getrennt vom öffentlichen Repository. Dieses Repository dient als nachvollziehbare Entwicklungs- und Veröffentlichungsbasis.

## Technischer Überblick

KlimaGG-Web ist eine minimalistische Python/FastAPI-Webanwendung mit serverseitigem HTML-Rendering und einer schlanken JavaScript/CSS-Oberfläche.

Zentrale Bestandteile:

- **FastAPI-Backend** für API-Endpunkte, Authentifizierung, Artikel, Votes, Kommentare, Reviews, News und Admin-Funktionen.
- **Jinja2-Templates** für Landing Page, Entwurfsseite, Einzelartikel und statische Seiten.
- **SQLAlchemy-Datenmodell** für Nutzer, Artikelversionen, Stimmen, Kommentare, Reviews, Reports, News und Metriken.
- **MiniMD-/Inline-Diff-Pipeline** für artikelgenaue Änderungsvorschläge, Kommentar-Diffkarten und Merge-Vorschauen.
- **Review- und Trust-Logik** für die strukturierte Bewertung von Kommentaren und Änderungsvorschlägen.
- **Snapshot-/Export-Werkzeuge** für Release- und Transparenz-Exporte.
- **LLM-Kontext-Dateien und Anweisungen** für externe Unterstützung beim Erstellen strukturierter Kommentarentwürfe.

Die App ist bewusst einfach gehalten: keine unnötigen Cookies, keine große Framework-Komplexität, kein GraphQL, kein schweres Frontend-Build-System.


## Was ist KlimaGG-Web?

KlimaGG-Web ist die Webplattform hinter dem Klima-Generationen-Gesetz. Sie verbindet einen öffentlich lesbaren Gesetzentwurf mit Beteiligungsfunktionen: Abstimmung, Kommentar, Änderungsvorschlag, Review und später nachvollziehbare Releases.

Der technische Kern ist darauf ausgelegt, Gesetzestext nicht nur als statischen Text anzuzeigen, sondern als versionierbaren, kommentierbaren und prüfbaren Arbeitsstand. Änderungen werden artikel- und blockgenau verarbeitet. Kommentare können konkrete Textänderungen enthalten, die als Inline-Diff und Kommentar-Diffkarte sichtbar werden.

Die Plattform soll zeigen, wie partizipative Gesetzesentwicklung digital, transparent und überprüfbar organisiert werden kann.


## Nicht im Repository enthalten

Dieses öffentliche Repository enthält keine produktiven oder personenbezogenen Daten.

Nicht enthalten sein dürfen insbesondere:

- `.env`
- lokale oder produktive Datenbanken wie `*.db`, `*.sqlite`, `*.sqlite3`
- private Schlüssel, Tokens oder Zugangsdaten
- produktive Serverkonfigurationen
- lokale virtuelle Python-Umgebungen wie `.venv/`
- temporäre Testausgaben und lokale Laufzeitdaten
- produktive Backups

Produktive Daten und Deployment-spezifische Konfigurationen bleiben außerhalb des öffentlichen Repositories.


## Architektur

Die Anwendung basiert auf FastAPI und Jinja2. Das Backend liefert sowohl HTML-Seiten als auch REST-Endpunkte aus. Die Templates liegen bewusst im Projekt-Root; statische Assets wie `style.css`, `script.js` und `auth_complete.js` werden kontrolliert ausgeliefert.

Die Datenhaltung erfolgt über SQLAlchemy-Modelle. Das Datenmodell umfasst unter anderem Nutzerkonten, Magic-Link-Login, Artikel und Artikelversionen, Votes, Kommentare, Reviews, Flags, Reports, News und Metriken.

Die zentrale Text- und Diff-Logik liegt in `indiff.py`. MiniMD ist dabei das persistente Arbeitsformat, HTML ist die operative Darstellungs- und Anker-Ebene. Die Diff-Pipeline erzeugt Inline-Diffs, Kommentar-Diffkarten, Merge-Vorschauen und Validierungen.

Konfiguration erfolgt über Umgebungsvariablen bzw. `.env` mittels `pydantic-settings`. Für lokale Entwicklung ist SQLite vorgesehen; produktiv kann eine andere Datenbank über `DATABASE_URL` konfiguriert werden.


> Hinweis: Dieses Repository ist die öffentliche Codebasis. Produktive Datenbanken, Secrets und serverseitige Betriebsdateien werden nicht versioniert und gehören nicht in Pull Requests oder Commits.
