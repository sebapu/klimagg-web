# klimagg-web

## Deutsch

`klimagg-web` ist eine kleine Webplattform, mit der strukturierte Gesetzes- und Regeltexte veröffentlicht, kommentiert, bewertet, reviewed und versioniert werden können.

**Dies ist ein früher öffentlicher Arbeitsstand.** Das Repository macht den technischen Kern der laufenden Plattform einsehbar und lokal ausführbar. Der aktuelle Zweck ist vor allem Transparenz, Diskussion und Weiterentwicklung – noch nicht die Bereitstellung einer vollständig dokumentierten oder gehärteten Standardlösung für den Produktivbetrieb.

Die Software entstand ursprünglich für die öffentliche Entwicklung eines konkreten Gesetzentwurfs und wurde für dieses Repository auf einen generischen Kern reduziert.

### Was die Plattform kann

* strukturierte Artikel und Artikelversionen;
* Votes und Reaktionen;
* präzise Textänderungsvorschläge mit MiniMD / Patch v2;
* normale Kommentare ohne Textänderung;
* Reviews;
* Trust-basierte Beteiligung;
* Next-Draft-Auswahl und Konfliktbehandlung;
* Release-Vorbereitung und öffentliche HTML-Snapshots;
* Magic-Link-Login;
* Adminfunktionen über `User.is_admin`;
* datensparsame öffentliche Exporte;
* optionale externe LLM-Unterstützung über Markdown-Kontexte.

Der Stack ist bewusst einfach:

* FastAPI
* SQLAlchemy
* Jinja2
* Vanilla JavaScript
* SQLite
* kein Frontend-Framework
* keine automatische Alembic-Migration

### Drei Demo-Profile

Das Repository enthält drei lokale Beispieldatensätze:

* `neutral` – vollständig synthetischer Muster-Gesetzentwurf;
* `wohnen` – größerer Wohnungs-Gesetzentwurf mit rekonstruierter Versionsgeschichte;
* `bahn` – größerer Schienenmobilitäts-Entwurf mit technischen Kommentaren und Reviews.

Alle Demo-Nutzer und Beteiligungsdaten sind synthetisch.

### Kontakt

Fragen, Hinweise und Austausch:

**Sebastian Putzke**
`sebastian.putzke@klimagg.de`

Technische Hinweise können auch direkt über GitHub Issues eingebracht werden.

---

## English

`klimagg-web` is a small web platform for publishing, discussing, reviewing and versioning structured legal or policy documents.

**This is an early public working version.** The repository makes the technical core of the platform inspectable and locally runnable. Its current purpose is transparency, discussion and further development. It is not yet a fully documented or hardened reference deployment.

The project was originally built for a concrete public-law drafting project and has been reduced into a reusable core.

Main features include:

* structured articles and versions;
* votes and reactions;
* precise MiniMD / Patch v2 change proposals;
* comments;
* reviews;
* trust-aware participation;
* Next Draft selection and conflict handling;
* release preparation and public HTML snapshots;
* Magic-Link authentication;
* admin access through `User.is_admin`;
* privacy-conscious public exports;
* optional external LLM assistance.

---

## Quick start

Create a virtual environment and install dependencies:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

Create a local configuration:

```bash
cp .env.example .env
```

For local development, the intended authentication settings are:

```text
AUTH_MAGIC_LINK_EMAIL_ENABLED=false
AUTH_EXPOSE_LOGIN_URL=true
```

Validate the local configuration and start the application:

```bash
python tools/check_env.py --mode local
./dev_server.sh
```

Open:

```text
http://127.0.0.1:8000
```

## Demo data

Create a fresh local demo database with one of the included profiles:

```bash
python3 seed.py neutral --reset
python3 seed.py wohnen --reset
python3 seed.py bahn --reset
```

The profiles cover different parts of the workflow:

* `neutral`: active proposals, votes, reviews, Next Draft selection and patch conflicts;
* `wohnen`: archived historical proposals and a reconstructed version transition;
* `bahn`: technical comments and structured reviews.

Demo email addresses use the reserved `.invalid` domain.

## Repository structure

The core is intentionally kept small:

```text
app.py
config.py
db.py
models.py
schemas.py
review.py
indiff.py
mail.py
export_snapshot.py

base.html
index.html
entwurf.html
artikel.html
admin.html
versionen.html
kontakt.html
rechtliches.html
methodik.html
mitmachen.html

script.js
admin.js
auth_complete.js
style.css

seed.py
demo/
  neutral/
  wohnen/
  bahn/

llm-api/
  LLM-Instructions-Change.md
  LLM-Instructions-New-Article.md

tools/
  check_env.py
```

Runtime data such as `.env`, SQLite databases, generated exports, release output and local demo state must not be committed.

## Database

The public distribution supports SQLite only.

The schema is defined by the SQLAlchemy models in `models.py`. There is deliberately no automatic Alembic migration step.

For a new empty non-demo database:

```bash
.venv/bin/python -c 'import models; from db import Base, engine; Base.metadata.create_all(bind=engine)'
```

Do not use this as an automatic migration procedure for an existing production database.

## Authentication

There is one authentication path:

```text
Magic Link -> JWT -> User.is_admin
```

There is no separate admin password, HTTP Basic Auth or development authentication bypass.

Local development can expose the generated Magic-Link URL instead of sending email. A public installation must not do this.

## Configuration and private data

Configuration is supplied through environment variables or a local `.env`.

Use `.env.example` as documentation. Never commit the real `.env`.

The repository is not a backup location. Do not commit:

* production databases;
* user accounts or email addresses;
* login tokens;
* SMTP credentials;
* JWT secrets;
* API tokens;
* private backups;
* runtime exports containing personal data.

Public snapshots are generated separately by `export_snapshot.py` and are intended to contain public document state and anonymized aggregates rather than account-level personal data.

## MiniMD and Patch v2

`indiff.py` contains the canonical MiniMD / diff / merge logic.

Change proposals are anchored to exact source text and baseline hashes so that an edit is not silently applied to an unrelated document state.

Persisted MiniMD block names are part of the data contract and should not be renamed casually.

## External LLM assistance

The application can export Markdown context for use with an external language model.

The external model has no live access to the platform and cannot save, publish or approve changes. The user manually copies a resulting MiniMD proposal back into the application.

Generic instructions are included in:

```text
llm-api/LLM-Instructions-Change.md
llm-api/LLM-Instructions-New-Article.md
```

Generated context files are runtime artifacts and should not be committed.

## Production use

This first public release intentionally does not prescribe a production deployment stack.

A real public installation additionally needs HTTPS, a reverse proxy, process supervision, filesystem permissions, backups and host hardening. See [`SECURITY.md`](SECURITY.md) before exposing an installation publicly.

## Security

Security-sensitive areas include authentication, admin authorization, MiniMD rendering, exports, file serving and process-management functions.

Please report vulnerabilities privately as described in [`SECURITY.md`](SECURITY.md).

## License

`klimagg-web` is released under the existing MIT License. See [`LICENSE`](LICENSE).

