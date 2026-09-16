"""KlimaGG-Web — database foundation.
Version: v2.0.0

Provides the SQLAlchemy engine, session factory, declarative base, SQLite connection tuning, and script-oriented session helpers. The public distribution intentionally supports SQLite only.
"""

from __future__ import annotations

from contextlib import contextmanager
import threading

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, declarative_base, sessionmaker

from config import settings

__version__ = "2.0.0"

DATABASE_URL = settings.DATABASE_URL

SQLITE_WRITE_LOCK = threading.RLock()


def _settings_int(name: str, default: int, *, minimum: int | None = None) -> int:
    try:
        value = int(getattr(settings, name, default) or default)
    except Exception:
        value = int(default)
    if minimum is not None:
        value = max(int(minimum), int(value))
    return int(value)


def _settings_bool(name: str, default: bool) -> bool:
    try:
        return bool(getattr(settings, name, default))
    except Exception:
        return bool(default)


def _settings_choice(name: str, default: str, allowed: set[str]) -> str:
    value = str(getattr(settings, name, default) or default).strip().upper()
    if value not in allowed:
        return str(default).strip().upper()
    return value


if not DATABASE_URL.startswith("sqlite"):
    raise RuntimeError(
        "KlimaGG-Web public distribution supports SQLite only. "
        "Set DATABASE_URL to a sqlite:///... URL."
    )

_busy_timeout_ms = _settings_int("SQLITE_BUSY_TIMEOUT_MS", 10000, minimum=0)
connect_args = {
    "check_same_thread": False,
    "timeout": max(1.0, float(_busy_timeout_ms) / 1000.0),
}

engine = create_engine(DATABASE_URL, connect_args=connect_args, future=True, pool_pre_ping=True)


@event.listens_for(engine, "connect")
def _set_sqlite_pragmas(dbapi_connection, _connection_record) -> None:
    """Apply configured SQLite PRAGMAs to each new connection.
    
    This is intentionally best-effort: unavailable PRAGMAs must not prevent startup on unusual SQLite builds, in-memory databases, or read-only files. Operational verification belongs in health/startup diagnostics.
    """
    def _pragma(sql: str) -> None:
        cursor = None
        try:
            cursor = dbapi_connection.cursor()
            cursor.execute(sql)
        except Exception:
            pass
        finally:
            try:
                if cursor is not None:
                    cursor.close()
            except Exception:
                pass

    journal = _settings_choice(
        "SQLITE_JOURNAL_MODE",
        "WAL",
        {"DELETE", "TRUNCATE", "PERSIST", "MEMORY", "WAL", "OFF"},
    )
    synchronous = _settings_choice(
        "SQLITE_SYNCHRONOUS",
        "NORMAL",
        {"OFF", "NORMAL", "FULL", "EXTRA"},
    )
    temp_store = _settings_choice("SQLITE_TEMP_STORE", "MEMORY", {"DEFAULT", "FILE", "MEMORY"})
    busy_timeout_ms = _settings_int("SQLITE_BUSY_TIMEOUT_MS", 10000, minimum=0)
    cache_size_kb = _settings_int("SQLITE_CACHE_SIZE_KB", 64000, minimum=0)

    _pragma(f"PRAGMA busy_timeout={busy_timeout_ms}")
    _pragma(f"PRAGMA journal_mode={journal}")
    _pragma(f"PRAGMA synchronous={synchronous}")
    _pragma(f"PRAGMA temp_store={temp_store}")
    if cache_size_kb > 0:
        # A negative SQLite cache size is interpreted as KiB instead of pages.
        _pragma(f"PRAGMA cache_size=-{cache_size_kb}")
    if _settings_bool("SQLITE_ENABLE_FOREIGN_KEYS", True):
        _pragma("PRAGMA foreign_keys=ON")
SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine,
    future=True,
)

Base = declarative_base()


def get_session() -> Session:
    """Return a raw SQLAlchemy session for scripts and tools.
    
    FastAPI endpoints should use the application `get_db()` dependency so request-scoped sessions are always closed.
    """
    return SessionLocal()


@contextmanager
def session_scope() -> Session:
    """Transactional session context manager for scripts and tools.
    
    Commits on success, rolls back on exceptions, and always closes the session.
    """
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
        