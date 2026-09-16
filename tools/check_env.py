#!/usr/bin/env python3
"""klimagg-web: validate a local or production .env without printing secrets.

Ablage im Repo:
    tools/check_env.py

Standardmäßig wird <repo>/.env geprüft, also die .env eine Ebene oberhalb von
``tools/``. Es werden keine Secret-Werte ausgegeben und keine Dateien verändert.

Beispiele:
    python tools/check_env.py
    python tools/check_env.py --mode production
    python tools/check_env.py --mode local
    python tools/check_env.py --env /pfad/zur/.env
    python tools/check_env.py --strict
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse


SCRIPT_PATH = Path(__file__).resolve()
DEFAULT_PROJECT_ROOT = SCRIPT_PATH.parent.parent
DEFAULT_ENV_PATH = DEFAULT_PROJECT_ROOT / ".env"


@dataclass(frozen=True)
class Finding:
    level: str  # ERROR | WARN | INFO
    key: str
    message: str


TRUE_VALUES = {"1", "true", "yes", "on"}
FALSE_VALUES = {"0", "false", "no", "off"}
BOOL_VALUES = TRUE_VALUES | FALSE_VALUES

PLACEHOLDER_MARKERS = (
    "change_me",
    "change-me",
    "changeme",
    "example.invalid",
    "example.org",
    "example.com",
    "demo@example",
)

FORBIDDEN_LEGACY_KEYS = {
    "ADMIN_PANEL_BASIC_AUTH_ENABLED": "Legacy-Basic-Auth ist entfernt; Admin läuft über Magic-Link/JWT + DB-is_admin.",
    "ADMIN_PANEL_BASIC_USER": "Legacy-Basic-Auth ist entfernt.",
    "ADMIN_PANEL_BASIC_PASSWORD": "Legacy-Basic-Auth ist entfernt.",
    "AUTH_DEV_BYPASS_ENABLED": "Separater Dev-Auth-Bypass ist entfernt.",
    "AUTH_DEV_BYPASS_SECRET": "Separater Dev-Auth-Bypass ist entfernt.",
    "AUTH_DEV_BYPASS_LOCALHOST_ONLY": "Separater Dev-Auth-Bypass ist entfernt.",
    "ALEMBIC_AUTO_UPGRADE_ON_WEB_UPDATE": "Automatische Alembic-Migration beim Webupdate ist entfernt.",
    "KGG_TEST_EMAIL": "Separate Testadresse ist kein Runtime-Setting mehr; lokales Login nutzt den normalen Magic-Link-Flow mit exponierter Login-URL.",
}

ALWAYS_EXPLICIT = (
    "PROJECT_NAME",
    "PUBLIC_BASE_URL",
    "DATABASE_URL",
    "AUTH_MAGIC_LINK_EMAIL_ENABLED",
    "AUTH_EXPOSE_LOGIN_URL",
    "MAGIC_LINK_TOKEN_LIFETIME_MINUTES",
    "MAGIC_LINK_MAX_PER_EMAIL_PER_HOUR",
    "ACCESS_TOKEN_EXPIRE_MINUTES",
    "JWT_SECRET_KEY",
    "JWT_ALGORITHM",
    "ENABLE_SECURITY_HEADERS",
    "BACKEND_CORS_ORIGINS",
)

PRODUCTION_EXPLICIT = (
    "ANALYTICS_SALT",
    "LEGAL_PROVIDER_NAME",
    "LEGAL_PROVIDER_ADDRESS",
)

MAIL_ROLE_KEYS = (
    "MAIL_INFO_ADDRESS",
    "MAIL_LOGIN_ADDRESS",
    "MAIL_KONTAKT_ADDRESS",
    "MAIL_DATENSCHUTZ_ADDRESS",
    "MAIL_ADMIN_ADDRESS",
)

MAIL_ROUTING_KEYS = (
    "MAIL_CONTACT_FORM_TO",
    "MAIL_INTEREST_FORM_TO",
    "MAIL_DATENSCHUTZ_FORM_TO",
    "MAIL_ADMIN_ALERT_TO",
)


class EnvParseError(ValueError):
    pass


def parse_env(path: Path) -> tuple[dict[str, str], list[Finding]]:
    values: dict[str, str] = {}
    findings: list[Finding] = []
    if not path.is_file():
        raise EnvParseError(f".env nicht gefunden: {path}")

    key_re = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
    for lineno, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            findings.append(Finding("ERROR", f"Zeile {lineno}", "Kein KEY=VALUE-Format."))
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key_re.fullmatch(key):
            findings.append(Finding("ERROR", f"Zeile {lineno}", f"Ungültiger Variablenname: {key!r}."))
            continue

        # Einfache dotenv-Quotes entfernen. Secret-Werte werden nie ausgegeben.
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            quote = value[0]
            value = value[1:-1]
            if quote == '"':
                value = value.replace(r"\n", "\n").replace(r"\t", "\t")
                value = value.replace(r'\"', '"').replace(r"\\", "\\")

        if key in values:
            findings.append(Finding("ERROR", key, f"Variable ist mehrfach definiert (erneut in Zeile {lineno})."))
        values[key] = value

    return values, findings


def clean(value: str | None) -> str:
    return str(value or "").strip()


def is_placeholder(value: str | None) -> bool:
    low = clean(value).lower()
    return any(marker in low for marker in PLACEHOLDER_MARKERS)


def parse_bool(values: dict[str, str], key: str, findings: list[Finding]) -> bool | None:
    raw = clean(values.get(key)).lower()
    if raw not in BOOL_VALUES:
        findings.append(Finding("ERROR", key, "Muss ein Bool-Wert sein: true/false, 1/0, yes/no oder on/off."))
        return None
    return raw in TRUE_VALUES


def parse_int(
    values: dict[str, str],
    key: str,
    findings: list[Finding],
    *,
    minimum: int | None = None,
    maximum: int | None = None,
) -> int | None:
    try:
        value = int(clean(values.get(key)))
    except (TypeError, ValueError):
        findings.append(Finding("ERROR", key, "Muss eine ganze Zahl sein."))
        return None
    if minimum is not None and value < minimum:
        findings.append(Finding("ERROR", key, f"Muss mindestens {minimum} sein."))
    if maximum is not None and value > maximum:
        findings.append(Finding("ERROR", key, f"Darf höchstens {maximum} sein."))
    return value


def valid_email(value: str) -> bool:
    value = clean(value)
    if not value or len(value) > 254 or "@" not in value:
        return False
    local, domain = value.rsplit("@", 1)
    if not local or not domain or "." not in domain:
        return False
    if any(ch.isspace() for ch in value):
        return False
    return True


def origin_of(url: str) -> str:
    p = urlparse(url)
    if not p.scheme or not p.netloc:
        return ""
    return f"{p.scheme.lower()}://{p.netloc.lower()}"


def detect_mode(values: dict[str, str]) -> str:
    url = clean(values.get("PUBLIC_BASE_URL"))
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if host in {"localhost", "127.0.0.1", "::1"} or host.endswith(".localhost"):
        return "local"
    if parsed.scheme in {"http", "https"} and host:
        return "production"

    expose = clean(values.get("AUTH_EXPOSE_LOGIN_URL")).lower()
    if expose in TRUE_VALUES:
        return "local"
    return "production"


def validate(values: dict[str, str], mode: str) -> list[Finding]:
    findings: list[Finding] = []

    # Legacy-Konfiguration soll nicht nur wirkungslos sein, sondern verschwinden.
    for key, message in FORBIDDEN_LEGACY_KEYS.items():
        if key in values:
            findings.append(Finding("ERROR", key, message))

    for key in ALWAYS_EXPLICIT:
        if key not in values or not clean(values.get(key)):
            findings.append(Finding("ERROR", key, "Fehlt in .env bzw. ist leer."))

    if mode == "production":
        for key in PRODUCTION_EXPLICIT:
            if key not in values or not clean(values.get(key)):
                findings.append(Finding("ERROR", key, "Muss in Produktion explizit gesetzt sein."))

    project_name = clean(values.get("PROJECT_NAME"))
    if project_name and is_placeholder(project_name):
        findings.append(Finding("WARN", "PROJECT_NAME", "Sieht nach Demo-/Platzhalterwert aus."))

    # URL / Datenbank
    public_url = clean(values.get("PUBLIC_BASE_URL"))
    if public_url:
        parsed = urlparse(public_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            findings.append(Finding("ERROR", "PUBLIC_BASE_URL", "Muss eine vollständige http(s)-URL sein."))
        elif parsed.path not in {"", "/"} or parsed.params or parsed.query or parsed.fragment:
            findings.append(Finding("WARN", "PUBLIC_BASE_URL", "Soll nur aus Origin bestehen, ohne Pfad/Query/Fragment."))
        if mode == "production":
            if parsed.scheme != "https":
                findings.append(Finding("ERROR", "PUBLIC_BASE_URL", "Produktion muss HTTPS verwenden."))
            if (parsed.hostname or "").lower() in {"localhost", "127.0.0.1", "::1"}:
                findings.append(Finding("ERROR", "PUBLIC_BASE_URL", "Produktion darf nicht auf localhost zeigen."))
            if is_placeholder(public_url):
                findings.append(Finding("ERROR", "PUBLIC_BASE_URL", "Produktion darf keine Beispiel-/Platzhalterdomain verwenden."))

    db_url = clean(values.get("DATABASE_URL"))
    if db_url and not db_url.startswith("sqlite:///"):
        findings.append(Finding("ERROR", "DATABASE_URL", "Die öffentliche Distribution unterstützt ausschließlich sqlite:///... URLs."))
    if mode == "production" and "demo" in db_url.lower():
        findings.append(Finding("ERROR", "DATABASE_URL", "Produktionskonfiguration verweist auf eine Demo-Datenbank."))

    # Auth-Vertrag
    mail_enabled = parse_bool(values, "AUTH_MAGIC_LINK_EMAIL_ENABLED", findings) if "AUTH_MAGIC_LINK_EMAIL_ENABLED" in values else None
    expose_login = parse_bool(values, "AUTH_EXPOSE_LOGIN_URL", findings) if "AUTH_EXPOSE_LOGIN_URL" in values else None
    security_headers = parse_bool(values, "ENABLE_SECURITY_HEADERS", findings) if "ENABLE_SECURITY_HEADERS" in values else None

    if mail_enabled is False and expose_login is False:
        findings.append(Finding("ERROR", "AUTH_*", "Weder Mailversand noch lokaler Login-Link ist aktiv; Anmeldung wäre nicht möglich."))
    if mode == "production":
        if mail_enabled is not True:
            findings.append(Finding("ERROR", "AUTH_MAGIC_LINK_EMAIL_ENABLED", "Muss in Produktion true sein."))
        if expose_login is not False:
            findings.append(Finding("ERROR", "AUTH_EXPOSE_LOGIN_URL", "Muss in Produktion false sein; Login-Token darf nicht in der API-Antwort erscheinen."))
        if security_headers is not True:
            findings.append(Finding("ERROR", "ENABLE_SECURITY_HEADERS", "Muss in Produktion true sein."))
    elif mode == "local" and mail_enabled is False and expose_login is not True:
        findings.append(Finding("ERROR", "AUTH_EXPOSE_LOGIN_URL", "Bei lokal deaktiviertem Mailversand muss der Login-Link exponiert werden."))

    magic_lifetime = parse_int(values, "MAGIC_LINK_TOKEN_LIFETIME_MINUTES", findings, minimum=1, maximum=1440) if "MAGIC_LINK_TOKEN_LIFETIME_MINUTES" in values else None
    if magic_lifetime is not None and not (10 <= magic_lifetime <= 60):
        findings.append(Finding("WARN", "MAGIC_LINK_TOKEN_LIFETIME_MINUTES", "Üblicher Zielbereich ist 10–60 Minuten (aktuell empfohlen: 30)."))

    per_email = parse_int(values, "MAGIC_LINK_MAX_PER_EMAIL_PER_HOUR", findings, minimum=1, maximum=100) if "MAGIC_LINK_MAX_PER_EMAIL_PER_HOUR" in values else None
    if per_email is not None and per_email > 10:
        findings.append(Finding("WARN", "MAGIC_LINK_MAX_PER_EMAIL_PER_HOUR", "Mehr als 10 Anfragen/Stunde je Adresse ist ungewöhnlich großzügig."))

    if "MAGIC_LINK_MAX_PER_IP_PER_HOUR" in values:
        per_ip = parse_int(values, "MAGIC_LINK_MAX_PER_IP_PER_HOUR", findings, minimum=1, maximum=1000)
        if per_ip is not None and per_email is not None and per_ip < per_email:
            findings.append(Finding("WARN", "MAGIC_LINK_MAX_PER_IP_PER_HOUR", "IP-Limit liegt unter dem E-Mail-Limit; das kann legitime Mehrnutzer-IP-Adressen unnötig sperren."))
    else:
        findings.append(Finding("INFO", "MAGIC_LINK_MAX_PER_IP_PER_HOUR", "Nicht gesetzt; config.py-Default 10 wird verwendet."))

    access_minutes = parse_int(values, "ACCESS_TOKEN_EXPIRE_MINUTES", findings, minimum=1, maximum=10080) if "ACCESS_TOKEN_EXPIRE_MINUTES" in values else None
    if access_minutes is not None and access_minutes > 1440:
        findings.append(Finding("WARN", "ACCESS_TOKEN_EXPIRE_MINUTES", "Länger als 24 Stunden; für Browser-JWTs ist ein kürzeres Fenster sicherer."))

    if clean(values.get("JWT_ALGORITHM")) and clean(values.get("JWT_ALGORITHM")) != "HS256":
        findings.append(Finding("WARN", "JWT_ALGORITHM", "Die aktuelle Codebasis ist auf HS256 ausgelegt/getestet."))

    jwt_secret = values.get("JWT_SECRET_KEY", "")
    if jwt_secret:
        if is_placeholder(jwt_secret):
            level = "ERROR" if mode == "production" else "WARN"
            findings.append(Finding(level, "JWT_SECRET_KEY", "Enthält einen bekannten Platzhalter."))
        if mode == "production" and len(jwt_secret) < 32:
            findings.append(Finding("ERROR", "JWT_SECRET_KEY", "Für Produktion mindestens 32 Zeichen verwenden."))

    # Analytics-Salt ist sicherheits-/datenschutzrelevant, wenn Analytics aktiv ist.
    analytics_enabled = None
    if "ENABLE_ANALYTICS" in values:
        analytics_enabled = parse_bool(values, "ENABLE_ANALYTICS", findings)
    elif mode == "production":
        # config.py-Default ist true, deshalb Salt trotzdem prüfen.
        analytics_enabled = True
        findings.append(Finding("INFO", "ENABLE_ANALYTICS", "Nicht gesetzt; config.py-Default true wird verwendet."))

    if analytics_enabled:
        salt = values.get("ANALYTICS_SALT", "")
        if mode == "production":
            if not salt:
                findings.append(Finding("ERROR", "ANALYTICS_SALT", "Fehlt trotz aktivierter Analytics; Default darf in Produktion nicht verwendet werden."))
            elif is_placeholder(salt) or len(salt) < 16:
                findings.append(Finding("ERROR", "ANALYTICS_SALT", "Muss in Produktion ein eigener, ausreichend langer Zufallswert sein."))
        elif salt and is_placeholder(salt):
            findings.append(Finding("WARN", "ANALYTICS_SALT", "Lokaler Platzhalter ist okay; vor Produktion ersetzen."))

    # CORS
    cors_raw = clean(values.get("BACKEND_CORS_ORIGINS"))
    if cors_raw:
        try:
            cors = json.loads(cors_raw)
            if not isinstance(cors, list) or not all(isinstance(x, str) and x.strip() for x in cors):
                raise ValueError
        except Exception:
            findings.append(Finding("ERROR", "BACKEND_CORS_ORIGINS", "Muss eine JSON-Liste von Origins sein, z. B. [\"https://example.org\"]."))
        else:
            if "*" in cors:
                findings.append(Finding("ERROR", "BACKEND_CORS_ORIGINS", "Wildcard ist mit credentials nicht zulässig/sinnvoll."))
            if mode == "production":
                if any("localhost" in x or "127.0.0.1" in x for x in cors):
                    findings.append(Finding("WARN", "BACKEND_CORS_ORIGINS", "Enthält localhost in Produktionskonfiguration."))
                pub_origin = origin_of(public_url)
                normalized = {origin_of(x) for x in cors}
                if pub_origin and pub_origin not in normalized:
                    findings.append(Finding("INFO", "BACKEND_CORS_ORIGINS", "PUBLIC_BASE_URL ist nicht enthalten; bei reinem Same-Origin-Betrieb ist das okay."))

    # Mailtransport, sobald Mail aktiv ist.
    if mail_enabled:
        for key in ("SMTP_HOST", "SMTP_PORT", "SMTP_USE_TLS", "SMTP_USERNAME", "SMTP_PASSWORD"):
            if key not in values:
                findings.append(Finding("ERROR", key, "Fehlt bei aktiviertem Magic-Link-Mailversand."))

        host = clean(values.get("SMTP_HOST"))
        if not host:
            findings.append(Finding("ERROR", "SMTP_HOST", "Darf bei aktiviertem Mailversand nicht leer sein."))
        elif mode == "production" and is_placeholder(host):
            findings.append(Finding("ERROR", "SMTP_HOST", "Produktion darf keinen Beispiel-SMTP-Host verwenden."))

        if "SMTP_PORT" in values:
            parse_int(values, "SMTP_PORT", findings, minimum=1, maximum=65535)
        if "SMTP_USE_TLS" in values:
            tls = parse_bool(values, "SMTP_USE_TLS", findings)
            if mode == "production" and tls is not True:
                findings.append(Finding("WARN", "SMTP_USE_TLS", "STARTTLS sollte im Produktionsbetrieb aktiviert sein."))

        username = clean(values.get("SMTP_USERNAME"))
        password = values.get("SMTP_PASSWORD", "")
        if bool(username) != bool(password):
            findings.append(Finding("ERROR", "SMTP_USERNAME/SMTP_PASSWORD", "Müssen gemeinsam gesetzt oder gemeinsam leer sein."))
        if mode == "production":
            if is_placeholder(username):
                findings.append(Finding("ERROR", "SMTP_USERNAME", "Enthält einen Beispiel-/Platzhalterwert."))
            if is_placeholder(password):
                findings.append(Finding("ERROR", "SMTP_PASSWORD", "Enthält einen bekannten Platzhalter."))

        timeout = values.get("SMTP_TIMEOUT_SECONDS")
        if timeout is not None:
            parse_int(values, "SMTP_TIMEOUT_SECONDS", findings, minimum=1, maximum=120)
        else:
            findings.append(Finding("INFO", "SMTP_TIMEOUT_SECONDS", "Nicht gesetzt; Mail-Service verwendet Default 20 Sekunden."))

        # Die primären Rollen sollen in der produktiven Instanz explizit sein.
        if mode == "production":
            for key in MAIL_ROLE_KEYS:
                value = clean(values.get(key))
                if not value:
                    findings.append(Finding("ERROR", key, "Soll in Produktion explizit gesetzt sein."))
                elif not valid_email(value):
                    findings.append(Finding("ERROR", key, "Ist keine plausible E-Mail-Adresse."))
                elif is_placeholder(value):
                    findings.append(Finding("ERROR", key, "Enthält eine Beispiel-/Platzhalterdomain."))

            noreply = clean(values.get("MAIL_NOREPLY_ADDRESS"))
            if not noreply:
                findings.append(Finding("WARN", "MAIL_NOREPLY_ADDRESS", "Nicht zwingend, aber für Systemmails sinnvoll explizit zu setzen."))
            elif not valid_email(noreply):
                findings.append(Finding("ERROR", "MAIL_NOREPLY_ADDRESS", "Ist keine plausible E-Mail-Adresse."))

            for key in MAIL_ROUTING_KEYS:
                value = clean(values.get(key))
                if not value:
                    findings.append(Finding("INFO", key, "Nicht gesetzt; mail.py verwendet den jeweiligen Rollen-Fallback."))
                elif not valid_email(value):
                    findings.append(Finding("ERROR", key, "Ist keine plausible E-Mail-Adresse."))
        else:
            # Lokal nur gesetzte Adressen prüfen.
            for key in (*MAIL_ROLE_KEYS, "MAIL_NOREPLY_ADDRESS", *MAIL_ROUTING_KEYS):
                value = clean(values.get(key))
                if value and not valid_email(value):
                    findings.append(Finding("ERROR", key, "Ist keine plausible E-Mail-Adresse."))

    # Dev-only Einstellungen dürfen auf Produktion nicht versehentlich aktiv sein.
    if mode == "production":
        if "KGG_DEV_DISABLE_COMMENT_QUOTA" in values:
            dev_quota = parse_bool(values, "KGG_DEV_DISABLE_COMMENT_QUOTA", findings)
            if dev_quota is True:
                findings.append(Finding("ERROR", "KGG_DEV_DISABLE_COMMENT_QUOTA", "Darf in Produktion nicht aktiviert sein."))


    # Public legal/contact pages read operator data from environment-backed settings.
    if mode == "production":
        for key in ("LEGAL_PROVIDER_NAME", "LEGAL_PROVIDER_ADDRESS"):
            if not clean(values.get(key)):
                findings.append(Finding("ERROR", key, "Muss für eine öffentliche Installation explizit gesetzt sein."))

    return findings


def print_report(path: Path, mode: str, findings: list[Finding]) -> tuple[int, int, int]:
    errors = [f for f in findings if f.level == "ERROR"]
    warnings = [f for f in findings if f.level == "WARN"]
    infos = [f for f in findings if f.level == "INFO"]

    print("klimagg-web .env check")
    print(f"Datei: {path}")
    print(f"Modus: {mode}")
    print("Hinweis: Secret-Werte werden absichtlich nicht ausgegeben.\n")

    for level, items, mark in (
        ("FEHLER", errors, "✗"),
        ("WARNUNGEN", warnings, "!"),
        ("HINWEISE", infos, "i"),
    ):
        if not items:
            continue
        print(f"{level} ({len(items)}):")
        for item in items:
            print(f"  {mark} {item.key}: {item.message}")
        print()

    if not errors and not warnings:
        print("OK: Keine Fehler oder Warnungen gefunden.")
    elif not errors:
        print(f"OK mit Warnungen: 0 Fehler, {len(warnings)} Warnung(en).")
    else:
        print(f"NICHT OK: {len(errors)} Fehler, {len(warnings)} Warnung(en).")

    return len(errors), len(warnings), len(infos)


def main() -> int:
    parser = argparse.ArgumentParser(description="Validates the klimagg-web .env without printing secrets.")
    parser.add_argument("--env", type=Path, default=DEFAULT_ENV_PATH, help="Pfad zur .env (Default: <repo>/.env)")
    parser.add_argument(
        "--mode",
        choices=("auto", "local", "production"),
        default="auto",
        help="Prüfprofil; auto erkennt anhand PUBLIC_BASE_URL/Auth-Konfiguration.",
    )
    parser.add_argument("--strict", action="store_true", help="Warnungen ebenfalls als fehlerhaften Exit behandeln.")
    args = parser.parse_args()

    env_path = args.env.expanduser().resolve()
    try:
        values, parse_findings = parse_env(env_path)
    except (OSError, EnvParseError) as exc:
        print(f"FEHLER: {exc}", file=sys.stderr)
        return 1

    mode = detect_mode(values) if args.mode == "auto" else args.mode
    findings = parse_findings + validate(values, mode)
    errors, warnings, _ = print_report(env_path, mode, findings)

    if errors:
        return 1
    if args.strict and warnings:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
