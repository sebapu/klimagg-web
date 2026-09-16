"""KlimaGG-Web — FastAPI application.
Version: v2.0.1

Provides the REST API and server-rendered Jinja2 pages for KlimaGG-Web, serves a deliberately small set of static assets, and coordinates authentication, participation, review, release, and public-read paths.

Implementation code and developer documentation are English. German strings remain where they are user-facing content, legal/domain terminology, persisted compatibility values, public routes, or MiniMD contracts.

Database migrations are an explicit deployment operation; importing this module must not mutate the database."""

from datetime import date, datetime, timedelta, timezone
from dataclasses import asdict
from pathlib import Path
from xml.sax.saxutils import escape as _xml_escape
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse, quote, parse_qs
from urllib.request import Request as UrlRequest, urlopen
from urllib.error import HTTPError
import html
import re
import difflib
import secrets
import json
import os
import subprocess
import threading
import time
import asyncio
import hashlib
import ipaddress

import export_snapshot

import jwt
from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException, Query, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, text, inspect
from sqlalchemy.orm import Session, object_session



from config import settings
from db import SQLITE_WRITE_LOCK, SessionLocal, engine
from models import (
    Article,
    ArticleVersion,
    ArticleVote,
    CommentVote,
    LoginToken,
    User,
    Comment,
    CommentReaction,
    ArticleReaction,
    UserEvent,
    SystemConfig,
    Review,
    MetricsDaily,
    ReleaseRun,
)
from schemas import (
    ArticleDiffOut,
    ArticleOut,
    ArticleVoteRequest,
    CommentVoteRequest,
    MyCommentReactionsOut,
    MyArticleReactionsOut,
    ReactionToggleOut,
    ReactionToggleRequest,
    ArticleVoteSummaryOut,
    CommentVoteSummaryOut,
    ArticleVersionsOut,
    ArticleVersionMetaOut,
    AuthCallbackResponse,
    AccountDeleteRequest,
    AccountDeleteStatusOut,
    CommentCreate,
    CommentOut,
    CommentOverviewItemOut,
    CommentReviewsOut,
    PersonalCommentVoteOut,
    CommentUpdate,
    CommentForkResponse,
    CompleteSignupRequest,
    HealthOut,
    MagicLinkRequest,
    PersonalArticleVoteOut,
    ReviewCreate,
    ReviewNextOut,
    ReviewOut,
    UpdatePseudonymRequest,
    UpdatePlzRequest,
    UserOut,
    NewArticleDraftCreate,
    UserReviewStatsOut,
    AdminStatsOut,
    AdminUsersOverviewOut,
    AdminArticlesOverviewOut,
    AdminCommentsOverviewOut,
    AdminReviewQueueOut,
    AdminVoteDistributionOut,
    PublicProjectStatsOut,
    AdminExportMetaOut,
    AdminExportRunOut,
    AdminWebStableRequest,
    AdminWebUpgradeOut,
    AdminWebUpgradeRequest,
    AdminWebVersionsOut,
    CommentDiscardResponse,
    ArticleMiniMdOut,
    ContactInterestRequest,
    ContactInterestOut,
    PublicTrafficStatsOut,
)
from mail import send_magic_link_mail, send_interest_form_mail, validate_magic_link_mail_config
import indiff as code
import review as review_code


__version__ = "2.0.1"

# Legacy recommendation aliases used by compatibility paths outside the Review-v2 core.
_APPROVE_RECS = {"annehmen", "accept", "ok", "approve"}
_REVISE_RECS = {"überarbeiten", "ueberarbeiten", "revise", "changes"}
_REJECT_RECS = {"ablehnen", "reject", "discard"}

BASE_DIR = Path(__file__).resolve().parent


def _git_describe_ref(cwd: Path, *, fallback: str = "unknown") -> str:
    """Return the running web version from the local Git checkout, preferring an exact tag and falling back to `git describe` or the configured version."""
    def _run(args: List[str]) -> str | None:
        try:
            p = subprocess.run(
                ["git", *args],
                cwd=str(cwd),
                capture_output=True,
                text=True,
                timeout=5,
            )
            out = (p.stdout or "").strip()
            if int(p.returncode or 0) == 0 and out:
                return out
        except Exception:
            return None
        return None

    out = _run(["describe", "--tags", "--exact-match"])
    if out:
        return out

    out = _run(["describe", "--tags", "--always", "--dirty"])
    if out:
        return out

    return str(fallback or "unknown").strip() or "unknown"
templates = Jinja2Templates(directory=str(BASE_DIR))
templates.env.globals["settings"] = settings
templates.env.globals["APP_VERSION"] = _git_describe_ref(BASE_DIR, fallback=str(getattr(settings, "APP_VERSION", "") or "unknown"))
templates.env.globals["PUBLIC_BASE_URL"] = (settings.PUBLIC_BASE_URL or "").strip()
templates.env.globals["DIFF_CARD_CONTEXT_MAX_CHARS"] = int(getattr(settings, "DIFF_CARD_CONTEXT_MAX_CHARS", 25) or 25)
templates.env.globals["DIFF_CARD_CONTEXT_ELLIPSIS"] = str(getattr(settings, "DIFF_CARD_CONTEXT_ELLIPSIS", "...") or "...")


def _settings_str_list(name: str, default: List[str]) -> List[str]:
    """Read a string-list setting defensively without any request dependency."""
    raw = getattr(settings, name, default)
    if raw is None:
        return list(default)
    if isinstance(raw, str):
        values = [part.strip() for part in raw.split(",") if part.strip()]
        return values or list(default)
    if isinstance(raw, (list, tuple, set)):
        values = [str(v).strip() for v in raw if str(v).strip()]
        return values or list(default)
    return list(default)


def _public_comment_statuses() -> List[str]:
    """Return the persisted comment statuses exposed in the public article/comment UI."""
    return _settings_str_list("COMMENT_PUBLIC_STATUSES", ["veröffentlicht"])


def _next_draft_comment_statuses() -> List[str]:
    """Return the public-status boundary used by the secondary Next-Draft layer."""
    return _settings_str_list("COMMENT_NEXT_DRAFT_STATUSES", _public_comment_statuses())


def _public_new_article_comment_statuses() -> List[str]:
    """Return public statuses that can expose a `new_article` proposal."""
    out = list(_public_comment_statuses() or [])
    if not out:
        out = ["veröffentlicht"]
    return out


def _public_new_article_comment_ids_for_article(
    db: Session,
    article_id: int,
    *,
    version_id: int | None = None,
) -> List[int]:
    """Return published `new_article` comment ids that make a draft article publicly visible.
    
    The article version itself remains a draft; visibility exists only while at least one qualifying public comment still points to that exact article/version.
    """
    try:
        q = (
            db.query(Comment.id)
            .filter(Comment.article_id == int(article_id))
            .filter(Comment.comment_mode == "new_article")
            .filter(Comment.status.in_(_public_new_article_comment_statuses()))
        )
        if version_id is not None:
            q = q.filter(Comment.version_id == int(version_id))
        rows = q.order_by(Comment.created_at.asc(), Comment.id.asc()).all()
        return [int(row[0]) for row in rows if row and row[0] is not None]
    except Exception:
        return []


def _article_public_visibility_payload(db: Session, art: Article) -> Dict[str, Any]:
    """Return the public visibility state of an article, including any comment ids that expose a draft shell."""
    v = getattr(art, "current_version", None)
    if not v:
        return {"visibility_status": "hidden", "visibility_reason": None, "public_comment_ids": []}
    if _is_published_article_version(v):
        return {"visibility_status": "published", "visibility_reason": None, "public_comment_ids": []}
    ids = _public_new_article_comment_ids_for_article(
        db,
        int(getattr(art, "id", 0) or 0),
        version_id=int(getattr(v, "id", 0) or 0),
    )
    if ids:
        return {
            "visibility_status": "proposed_by_comment",
            "visibility_reason": "published_new_article_comment",
            "public_comment_ids": list(ids),
        }
    return {"visibility_status": "hidden", "visibility_reason": None, "public_comment_ids": []}


def _article_public_visibility_status(db: Session, art: Article) -> str:
    """published | proposed_by_comment | hidden"""
    return str(_article_public_visibility_payload(db, art).get("visibility_status") or "hidden")


def _article_public_visibility_dict(db: Session, art: Article) -> Dict[str, Any]:
    """Return the compact JSON/SSR visibility payload consumed by frontend diff logic."""
    payload = _article_public_visibility_payload(db, art)
    return {
        "visibility_status": str(payload.get("visibility_status") or "hidden"),
        "visibility_reason": payload.get("visibility_reason"),
        "public_comment_ids": [
            int(x) for x in list(payload.get("public_comment_ids") or []) if str(x).strip()
        ],
    }


def _article_ordering():
    """Kanonische Artikelreihenfolge: zuerst sort_order, dann interne aid."""
    return (Article.sort_order.asc(), Article.id.asc())


def _article_display_label(article: Article) -> str:
    """Build the public article label without falling back to the internal article id."""
    public_code = str(getattr(article, "public_code", "") or "").strip()
    title = str(getattr(article, "title", "") or "").strip()
    if public_code and title and public_code == title:
        return title
    if public_code and title:
        return f"{public_code} – {title}"
    if public_code:
        return public_code
    return title


def _article_toc_group_label(article: Article) -> str | None:
    """Return the TOC group label from the article itself or its `toc_parent`."""
    parent = getattr(article, "toc_parent", None)
    if parent is not None:
        return parent.toc_title
    return article.toc_title


def get_db() -> Session:
    """FastAPI dependency that yields one request-scoped SQLAlchemy session and always closes it."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# --- Metrics / Analytics Helpers -------------------------------------------

_METRICS_LAST_CLEANUP_DAY: date | None = None
_METRICS_BUFFER_LOCK = threading.Lock()
_METRICS_BUFFER: Dict[str, Dict[str, Any]] = {}
_METRICS_BUFFER_EVENT_COUNT = 0


def _metrics_utc_day() -> date:
    return datetime.utcnow().date()


def _metrics_truncate_ip(ip: str) -> str:
    try:
        addr = ipaddress.ip_address(ip)
        if addr.version == 4:
            net = ipaddress.ip_network(f"{ip}/{settings.ANALYTICS_IP_TRUNC_V4}", strict=False)
        else:
            net = ipaddress.ip_network(f"{ip}/{settings.ANALYTICS_IP_TRUNC_V6}", strict=False)
        return f"{net.network_address}/{net.prefixlen}"
    except Exception:
        return "unknown"


def _metrics_client_ip(request: Request) -> str:
    # Behind a reverse proxy, `request.client.host` is often only the proxy address.
    if settings.ANALYTICS_TRUST_X_FORWARDED_FOR:
        xff = (request.headers.get("x-forwarded-for") or "").strip()
        if xff:
            # The left-most XFF entry is treated as the original client by this deployment model.
            return xff.split(",")[0].strip() or "unknown"
    return (request.client.host if request.client else "unknown") or "unknown"


def _metrics_visitor_key(day: date, client_ip: str, user_agent: str) -> str:
    ip_trunc = _metrics_truncate_ip(client_ip or "")
    ua = (user_agent or "")[:200]
    raw = f"{day.isoformat()}|{ip_trunc}|{ua}|{settings.ANALYTICS_SALT}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _metrics_is_logged_in_request(request: Request) -> bool:
    auth = request.headers.get("authorization") or ""
    scheme, _, token = auth.partition(" ")
    if scheme.lower() != "bearer" or not token:
        return False
    try:
        jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])
        return True
    except Exception:
        return False


def _metrics_upsert_daily_increment(db: Session, day: date, field: str, inc: int) -> None:
    # Security invariant: `field` is currently supplied only by fixed internal call sites.
    # Add a strict allowlist before ever coupling this helper to request-controlled input.
    # UPSERT compatible with the supported SQLite/PostgreSQL paths.
    db.execute(
        text(
            f"""
            INSERT INTO metrics_daily (
              day, pageviews, pageviews_logged_in, visitors_est, onsite_seconds, onsite_seconds_logged_in, updated_at
            ) VALUES (
              :day, 0, 0, 0, 0, 0, CURRENT_TIMESTAMP
            )
            ON CONFLICT(day) DO UPDATE SET
              {field} = {field} + :inc,
              updated_at = CURRENT_TIMESTAMP
            """
        ),
        {"day": day.isoformat(), "inc": int(inc)},
    )




def _metrics_buffer_enabled() -> bool:
    return bool(getattr(settings, "ANALYTICS_BUFFER_ENABLED", True))


def _metrics_buffer_flush_interval_seconds() -> int:
    try:
        return max(5, int(getattr(settings, "ANALYTICS_BUFFER_FLUSH_INTERVAL_SECONDS", 30) or 30))
    except Exception:
        return 30


def _metrics_buffer_max_events() -> int:
    try:
        return max(1, int(getattr(settings, "ANALYTICS_BUFFER_MAX_EVENTS", 250) or 250))
    except Exception:
        return 250


def _metrics_queue_event(
    *,
    day: date,
    visitor_key: str | None = None,
    pageviews: int = 0,
    pageviews_logged_in: int = 0,
) -> None:
    """Buffer analytics events in-process instead of writing to SQLite on the request path."""
    global _METRICS_BUFFER_EVENT_COUNT
    day_key = day.isoformat()
    with _METRICS_BUFFER_LOCK:
        bucket = _METRICS_BUFFER.setdefault(
            day_key,
            {
                "pageviews": 0,
                "pageviews_logged_in": 0,
                "visitor_keys": set(),
            },
        )
        bucket["pageviews"] = int(bucket.get("pageviews") or 0) + max(0, int(pageviews or 0))
        bucket["pageviews_logged_in"] = int(bucket.get("pageviews_logged_in") or 0) + max(0, int(pageviews_logged_in or 0))
        if visitor_key:
            try:
                bucket.setdefault("visitor_keys", set()).add(str(visitor_key))
            except Exception:
                pass
        _METRICS_BUFFER_EVENT_COUNT += 1


def _metrics_pop_buffer() -> tuple[Dict[str, Dict[str, Any]], int]:
    global _METRICS_BUFFER_EVENT_COUNT
    with _METRICS_BUFFER_LOCK:
        data = dict(_METRICS_BUFFER)
        event_count = int(_METRICS_BUFFER_EVENT_COUNT or 0)
        _METRICS_BUFFER.clear()
        _METRICS_BUFFER_EVENT_COUNT = 0
    return data, event_count


def _metrics_restore_buffer(data: Dict[str, Dict[str, Any]], event_count: int) -> None:
    global _METRICS_BUFFER_EVENT_COUNT
    if not data:
        return
    with _METRICS_BUFFER_LOCK:
        for day_key, incoming in dict(data or {}).items():
            bucket = _METRICS_BUFFER.setdefault(
                str(day_key),
                {
                    "pageviews": 0,
                    "pageviews_logged_in": 0,
                    "visitor_keys": set(),
                },
            )
            for field in ("pageviews", "pageviews_logged_in"):
                bucket[field] = int(bucket.get(field) or 0) + int((incoming or {}).get(field) or 0)
            try:
                bucket.setdefault("visitor_keys", set()).update(set((incoming or {}).get("visitor_keys") or set()))
            except Exception:
                pass
        _METRICS_BUFFER_EVENT_COUNT += max(0, int(event_count or 0))


def _metrics_flush_buffer_once() -> int:
    """Flush buffered analytics to SQLite in batches; this path must never be request-critical."""
    global _METRICS_LAST_CLEANUP_DAY
    data, event_count = _metrics_pop_buffer()
    if not data:
        return 0

    db = SessionLocal()
    try:
        with SQLITE_WRITE_LOCK:
            for day_key, bucket in sorted(data.items()):
                try:
                    day = date.fromisoformat(str(day_key))
                except Exception:
                    continue

                if (
                    settings.ANALYTICS_VISITOR_KEYS_RETENTION_DAYS
                    and (_METRICS_LAST_CLEANUP_DAY is None or _METRICS_LAST_CLEANUP_DAY != day)
                ):
                    cutoff = day - timedelta(days=int(settings.ANALYTICS_VISITOR_KEYS_RETENTION_DAYS))
                    db.execute(
                        text("DELETE FROM metrics_visitors_daily WHERE day < :cutoff"),
                        {"cutoff": cutoff.isoformat()},
                    )
                    _METRICS_LAST_CLEANUP_DAY = day

                for field in ("pageviews", "pageviews_logged_in"):
                    inc = int((bucket or {}).get(field) or 0)
                    if inc > 0:
                        _metrics_upsert_daily_increment(db, day, field, inc)

                visitor_keys = sorted(set((bucket or {}).get("visitor_keys") or set()))
                for vkey in visitor_keys:
                    res = db.execute(
                        text(
                            """
                            INSERT INTO metrics_visitors_daily (day, visitor_key, created_at)
                            VALUES (:day, :vkey, CURRENT_TIMESTAMP)
                            ON CONFLICT(day, visitor_key) DO NOTHING
                            """
                        ),
                        {"day": day.isoformat(), "vkey": str(vkey)},
                    )
                    if getattr(res, "rowcount", 0) == 1:
                        _metrics_upsert_daily_increment(db, day, "visitors_est", 1)
            _commit_db(db)
        try:
            with _PUBLIC_TRAFFIC_STATS_CACHE_LOCK:
                _PUBLIC_TRAFFIC_STATS_CACHE.clear()
        except Exception:
            pass
        return int(event_count or 0)
    except Exception:
        _rollback_db_quietly(db)
        _metrics_restore_buffer(data, event_count)
        return 0
    finally:
        db.close()


def _metrics_background_flush_loop() -> None:
    interval = _metrics_buffer_flush_interval_seconds()
    while True:
        try:
            time.sleep(interval)
            if settings.ENABLE_ANALYTICS and _metrics_buffer_enabled():
                _metrics_flush_buffer_once()
        except Exception:
            pass


def _start_metrics_background_flush_thread() -> None:
    if not settings.ENABLE_ANALYTICS or not _metrics_buffer_enabled():
        return
    if getattr(_start_metrics_background_flush_thread, "_started", False):
        return
    _start_metrics_background_flush_thread._started = True  # type: ignore[attr-defined]
    t = threading.Thread(target=_metrics_background_flush_loop, name="web_metrics_flush", daemon=True)
    t.start()


def _shutdown_metrics_flush() -> None:
    try:
        if settings.ENABLE_ANALYTICS and _metrics_buffer_enabled():
            _metrics_flush_buffer_once()
    except Exception:
        pass


def _commit_db(db: Session) -> None:
    """Serialize application-level SQLite commits.
    
    SQLite supports concurrent readers but only one writer; the process-wide lock makes write boundaries explicit for Python threads.
    """
    with SQLITE_WRITE_LOCK:
        db.commit()


def _rollback_db_quietly(db: Session) -> None:
    try:
        db.rollback()
    except Exception:
        pass


def _sqlite_wal_checkpoint_truncate() -> None:
    """Run a best-effort WAL checkpoint during orderly shutdown so normal stop/restart cycles leave a compact SQLite state."""
    try:
        if not str(getattr(settings, "DATABASE_URL", "") or "").startswith("sqlite"):
            return
        with SQLITE_WRITE_LOCK:
            with engine.connect() as conn:
                conn.exec_driver_sql("PRAGMA wal_checkpoint(TRUNCATE)")
    except Exception:
        pass


def _shutdown_runtime_cleanup() -> None:
    _shutdown_metrics_flush()
    _sqlite_wal_checkpoint_truncate()
    try:
        engine.dispose()
    except Exception:
        pass


def _ensure_db_schema_ready() -> None:
    """Verify only the runtime tables required by this application version.

    This check is read-only and deliberately independent of any migration tool or
    migration history. Schema changes, if ever required, are a separate manual
    deployment operation.
    """
    inspector = inspect(engine)
    required_tables = {
        "users",
        "login_tokens",
        "articles",
        "article_versions",
        "votes",
        "article_reactions",
        "comments",
        "comment_votes",
        "comment_reactions",
        "reviews",
        "user_events",
        "system_config",
        "metrics_daily",
        "metrics_visitors_daily",
        "release_runs",
    }
    missing_tables = sorted(table for table in required_tables if not inspector.has_table(table))
    if missing_tables:
        raise RuntimeError(
            "[klimagg-web][DB] Database schema is incomplete for this application version "
            f"(missing tables: {', '.join(missing_tables)})."
        )


# --- Authentication / JWT helpers -----------------------------------------


def create_access_token(*, user_id: int, expires_delta: timedelta | None = None) -> str:
    """Create a signed JWT containing the user id in `sub` and the configured expiration in `exp`."""
    if expires_delta is None:
        
        expires_delta = timedelta(
            minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES,
        )

    to_encode = {
        "sub": str(user_id),
        "exp": datetime.utcnow() + expires_delta,
    }
    encoded_jwt = jwt.encode(
        to_encode,
        settings.JWT_SECRET_KEY,
        algorithm=settings.JWT_ALGORITHM,
    )
    return encoded_jwt


def _auth_error(code: str, message: str, *, status_code: int = status.HTTP_400_BAD_REQUEST) -> HTTPException:
    """Build a structured authentication error so clients can distinguish machine-readable auth states while retaining a human-readable message."""
    return HTTPException(
        status_code=status_code,
        detail={"code": str(code or "auth_error"), "message": str(message or "Anmeldung fehlgeschlagen.")},
    )


def _anonymize_and_deactivate_user(db: Session, user: User, *, now: datetime, reason: str) -> None:
    """Anonymize an account, invalidate future login use, clear communication preferences, and retain authored comments under an anonymous public label."""
    if getattr(user, "is_deleted", False):
        return

    # Anonymize authored comments.
    try:
        db.query(Comment).filter(Comment.user_id == user.id).update(
            {Comment.public_author_label: "Anonym"},
            synchronize_session=False,
        )
    except Exception:
        pass

    # Remove any remaining login tokens.
    try:
        db.query(LoginToken).filter(LoginToken.user_id == user.id).delete(synchronize_session=False)
    except Exception:
        pass

    # Release the unique e-mail value using the RFC 2606 reserved `.invalid` domain.
    unique_tail = f"u{user.id}-{int(time.time())}"
    user.email = f"deleted+{unique_tail}@example.invalid"
    user.pseudonym = "Anonym"
    user.plz = None
    user.trust_level = 0
    user.is_admin = False

    user.deletion_requested_at = user.deletion_requested_at or now
    user.deletion_scheduled_for = None
    user.deleted_at = now
    user.is_deleted = True

    # Record an account-lifecycle event when possible.
    try:
        ev = UserEvent(
            user_id=user.id,
            event_type="account_deleted",
            payload={"reason": reason},
            created_at=now,
        )
        db.add(ev)
    except Exception:
        pass

    db.add(user)


def _schedule_account_delete(db: Session, user: User, *, now: datetime) -> datetime:
    """Schedule account deletion after the configured grace period."""
    grace_hours = int(getattr(settings, "ACCOUNT_DELETE_GRACE_HOURS", 24) or 24)
    scheduled_for = now + timedelta(hours=grace_hours)
    user.deletion_requested_at = now
    user.deletion_scheduled_for = scheduled_for
    db.add(user)
    return scheduled_for


def _cancel_account_delete(db: Session, user: User) -> None:
    """Cancel a previously scheduled account deletion."""
    user.deletion_requested_at = None
    user.deletion_scheduled_for = None
    db.add(user)


def _process_due_account_deletions(db: Session, *, now: datetime) -> int:
    """Process due account deletions and return the number of accounts anonymized."""
    processed = 0
    try:
        q = (
            db.query(User)
            .filter(User.is_deleted == False)  # noqa: E712
            .filter(User.deletion_scheduled_for != None)  # noqa: E711
            .filter(User.deletion_scheduled_for <= now)
        )
        for u in q.all():
            _anonymize_and_deactivate_user(db, u, now=now, reason="scheduled")
            processed += 1
        if processed:
            _commit_db(db)
    except Exception:
        # Never block authentication because housekeeping failed.
        _rollback_db_quietly(db)
    return processed


def _start_account_delete_housekeeping_thread() -> None:
    """Start the in-process account-deletion housekeeping loop; auth requests remain read-only."""
    if not getattr(settings, "ACCOUNT_DELETE_HOUSEKEEPING_ENABLED", True):
        return
    interval = int(getattr(settings, "ACCOUNT_DELETE_HOUSEKEEPING_INTERVAL_SECONDS", 300) or 300)
    interval = max(30, min(interval, 3600))

    # Start at most once per process.
    if getattr(_start_account_delete_housekeeping_thread, "_started", False):
        return
    _start_account_delete_housekeeping_thread._started = True  # type: ignore[attr-defined]

    def _loop():
        while True:
            try:
                db = SessionLocal()
                try:
                    _process_due_account_deletions(db, now=datetime.utcnow())
                finally:
                    db.close()
            except Exception:
                # Keep the housekeeping loop alive across transient DB/startup failures.
                pass
            try:
                time.sleep(interval)
            except Exception:
                # Keep the loop alive.
                pass

    t = threading.Thread(target=_loop, name="web_account_delete_housekeeping", daemon=True)
    t.start()


def _startup_account_delete_housekeeping():
    
    
    _start_account_delete_housekeeping_thread()


def get_current_user(
    db: Session = Depends(get_db),
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> User:
    """Resolve and validate the bearer JWT and return the corresponding active user."""
    if not authorization:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing Authorization header",
        )

    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid Authorization header",
        )

    try:
        payload = jwt.decode(
            token,
            settings.JWT_SECRET_KEY,
            algorithms=[settings.JWT_ALGORITHM],
        )
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token expired",
        )
    except jwt.InvalidTokenError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token",
        )

    # Database stability: authentication requests remain read-only.
    # Due account deletions are handled by the housekeeping thread.

    user_id_str = payload.get("sub")

    if user_id_str is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token payload",
        )

    try:
        user_id = int(user_id_str)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token payload",
        )

    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
        )

    if getattr(user, "is_deleted", False) or getattr(user, "deleted_at", None) is not None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Account deleted",
        )

    return user


def get_current_user_optional(
    db: Session = Depends(get_db),
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> User | None:
    """Optional variant of `get_current_user`; return `None` instead of raising 401 for missing or invalid credentials."""
    if not authorization:
        return None
    try:
        return get_current_user(db=db, authorization=authorization)
    except HTTPException:
        return None


def require_admin(current_user: User) -> None:
    """Raise HTTP 403 unless the current user has administrator privileges."""
    if not getattr(current_user, "is_admin", False):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin privileges required",
        )


# ---------------------------------------------------------------------------
# Public versions/releases (listing and file serving)
# ---------------------------------------------------------------------------

_RELEASE_HTML_FILE_RE = re.compile(
    r"^(?P<prefix>v(?P<yy>\d{2})-(?P<mm>\d{2})-(?P<dd>\d{2}))_(?P<tag>[a-z0-9][a-z0-9-]*)_entwurf\.html$",
    re.IGNORECASE,
)


def _release_list_item(path: Path, *, url: str) -> Dict[str, Any] | None:
    match = _RELEASE_HTML_FILE_RE.match(path.name)
    if not match or not path.is_file():
        return None
    try:
        year = 2000 + int(match.group("yy"))
        dt = datetime(year, int(match.group("mm")), int(match.group("dd")))
        date_human = dt.strftime("%Y-%m-%d")
    except Exception:
        return None
    size_kb = int((path.stat().st_size + 1023) // 1024)
    release_id = str(match.group("prefix"))
    return {
        "folder": release_id,
        "date": dt.isoformat(),
        "date_human": date_human,
        "files": {
            "html": {"name": path.name, "url": url, "size_kb": size_kb},
        },
    }


def _list_public_release_items() -> List[Dict[str, Any]]:
    """List canonical flat public release HTML snapshots."""
    base = (BASE_DIR / "versions" / "release").resolve()
    if not base.is_dir():
        return []

    items: List[Dict[str, Any]] = []
    for path in sorted(base.glob("v??-??-??_*_entwurf.html")):
        item = _release_list_item(path, url=f"/versions/release/{path.name}")
        if item is not None:
            items.append(item)

    items.sort(key=lambda x: x.get("date") or "", reverse=True)
    return items

def _ensure_versions_dir() -> Path:
    p = (BASE_DIR / "versions").resolve()
    p.mkdir(parents=True, exist_ok=True)
    return p

def _admin_web_state_path() -> Path:
    return _ensure_versions_dir() / "admin_web_state.json"


# ---------------------------------------------------------------------------
# Admin settings currently persisted under the `versions/` runtime path.
# Only release-workflow state is stored here; the long-term server/repository
# placement of runtime state is handled separately from this local cleanup.
# ---------------------------------------------------------------------------

_ADMIN_SETTINGS_LOCK = threading.Lock()
_RELEASE_ADMIN_ACTION_LOCK = threading.Lock()
_RELEASE_ADMIN_STATES = {"normal", "release_focus", "finalizing"}

_GITHUB_VERSIONS_LOCK = threading.Lock()
_GITHUB_VERSIONS_CACHE: Dict[str, Any] = {
    "fetched_at": 0.0,
    "etag": "",
    "data": None,
    "last_checked_at": None,
    "source": None,
    "fetch_ok": False,
}


def _admin_settings_path() -> Path:
    return _ensure_versions_dir() / "admin_settings.json"


def _admin_settings_parse_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return datetime.fromisoformat(value)
    except Exception:
        return None


def _read_admin_settings() -> Dict[str, Any]:
    path = _admin_settings_path()
    raw: Dict[str, Any] = {}
    if path.is_file():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8") or "{}")
            if isinstance(loaded, dict):
                raw = loaded
        except Exception:
            raw = {}

    release_state = str(raw.get("release_state") or "normal").strip().lower()
    if release_state not in _RELEASE_ADMIN_STATES:
        release_state = "normal"

    started_by = raw.get("release_focus_started_by_user_id")
    try:
        started_by_id = int(started_by) if started_by is not None else None
    except Exception:
        started_by_id = None

    return {
        "release_state": release_state,
        "release_focus_started_at": _admin_settings_parse_datetime(raw.get("release_focus_started_at")),
        "release_focus_started_by_user_id": started_by_id,
        "updated_at": _admin_settings_parse_datetime(raw.get("updated_at")),
    }


def _write_admin_settings(state: Dict[str, Any]) -> Dict[str, Any]:
    current = _read_admin_settings()
    release_state = str(state.get("release_state", current.get("release_state", "normal")) or "normal").strip().lower()
    if release_state not in _RELEASE_ADMIN_STATES:
        raise ValueError(f"invalid_release_state:{release_state}")

    focus_started_at = state.get("release_focus_started_at", current.get("release_focus_started_at"))
    if isinstance(focus_started_at, str):
        focus_started_at = _admin_settings_parse_datetime(focus_started_at)
    focus_started_by = state.get("release_focus_started_by_user_id", current.get("release_focus_started_by_user_id"))
    try:
        focus_started_by = int(focus_started_by) if focus_started_by is not None else None
    except Exception:
        focus_started_by = None

    if release_state == "normal":
        focus_started_at = None
        focus_started_by = None

    updated_at = datetime.utcnow()
    out = {
        "release_state": release_state,
        "release_focus_started_at": (
            focus_started_at.isoformat(timespec="seconds") if isinstance(focus_started_at, datetime) else None
        ),
        "release_focus_started_by_user_id": focus_started_by,
        "updated_at": updated_at.isoformat(timespec="seconds"),
    }
    path = _admin_settings_path()
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)
    return {
        "release_state": release_state,
        "release_focus_started_at": focus_started_at,
        "release_focus_started_by_user_id": focus_started_by,
        "updated_at": updated_at,
    }


def _release_admin_state() -> Dict[str, Any]:
    with _ADMIN_SETTINGS_LOCK:
        return dict(_read_admin_settings())


def _release_admin_action_try_acquire() -> tuple[bool, Any]:
    """Serialize release-admin actions across threads and, on Unix, across processes."""
    if not _RELEASE_ADMIN_ACTION_LOCK.acquire(blocking=False):
        return False, None

    lock_file = None
    try:
        try:
            import fcntl  # Unix/Linux process lock; used in production and local development.
        except ImportError:
            return True, None

        lock_path = _ensure_versions_dir() / ".release_workflow.lock"
        lock_file = lock_path.open("a+", encoding="utf-8")
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (BlockingIOError, OSError):
            lock_file.close()
            _RELEASE_ADMIN_ACTION_LOCK.release()
            return False, None
        return True, lock_file
    except Exception:
        if lock_file is not None:
            try:
                lock_file.close()
            except Exception:
                pass
        _RELEASE_ADMIN_ACTION_LOCK.release()
        raise


def _release_admin_action_release(lock_file: Any) -> None:
    if lock_file is not None:
        try:
            import fcntl
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
        except Exception:
            pass
        try:
            lock_file.close()
        except Exception:
            pass
    if _RELEASE_ADMIN_ACTION_LOCK.locked():
        _RELEASE_ADMIN_ACTION_LOCK.release()


def _release_submit_blocked() -> bool:
    if not bool(getattr(settings, "RELEASE_FREEZE_ENABLED", True)):
        return False
    if not bool(getattr(settings, "RELEASE_FREEZE_BLOCK_NEW_COMMENTS_FOR_CURRENT_CYCLE", True)):
        return False
    state = str(_release_admin_state().get("release_state") or "normal")
    return state in {"release_focus", "finalizing"}
 
def _deploy_ref_path() -> Path:
    # Persisted deploy target + history (survives git checkouts because versions/ is gitignored).
    return _ensure_versions_dir() / "deploy_ref.txt"

def _utc_now_iso() -> str:
    return datetime.utcnow().isoformat(timespec="seconds") + "Z"

def _read_deploy_ref_file() -> Dict[str, Any]:
    """Read desired deploy ref + archive from versions/deploy_ref.txt.

    Format (human-editable):
      - First non-empty, non-comment line is the desired ref ("v1.0.15" or "ref=v1.0.15").
      - Archive entries are comment lines:
          # <ref> | <stable:y/n> | <start_utc> | <end_utc>
    """
    path = _deploy_ref_path()
    if not path.is_file():
        return {"desired_ref": None, "entries": []}

    desired: Optional[str] = None
    entries: List[Dict[str, Any]] = []
    try:
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = (raw or "").strip()
            if not line:
                continue
            if not line.startswith("#") and desired is None:
                if line.lower().startswith("ref="):
                    desired = line.split("=", 1)[1].strip()
                else:
                    desired = line
                continue
            if not line.startswith("#"):
                continue
            body = line.lstrip("#").strip()
            if "|" not in body:
                continue
            parts = [p.strip() for p in body.split("|")]
            if len(parts) < 3:
                continue
            ref = parts[0]
            stable_s = (parts[1] if len(parts) > 1 else "").lower()
            stable = stable_s in {"y", "yes", "true", "1"}
            start = parts[2] if len(parts) > 2 else ""
            end = parts[3] if len(parts) > 3 else ""
            if not ref or not start:
                continue
            entries.append({"ref": ref, "stable": bool(stable), "start": start, "end": end})
    except Exception:
        return {"desired_ref": None, "entries": []}

    return {"desired_ref": desired, "entries": entries}

def _write_deploy_ref_file(desired_ref: str, entries: List[Dict[str, Any]]) -> None:
    path = _deploy_ref_path()
    tmp = path.with_suffix(".tmp")
    desired_ref = (desired_ref or "").strip()

    lines: List[str] = []
    lines.append(f"ref={desired_ref}\n")
    lines.append("# archive: ref | stable(y/n) | start_utc | end_utc\n")
    for e in entries:
        ref = str(e.get("ref") or "").strip()
        if not ref:
            continue
        stable = "y" if bool(e.get("stable")) else "n"
        start = str(e.get("start") or "").strip()
        end = str(e.get("end") or "").strip()
        if not start:
            continue
        lines.append(f"# {ref} | {stable} | {start} | {end}\n")

    tmp.write_text("".join(lines), encoding="utf-8")
    tmp.replace(path)

def _deploy_history_close_open_entry(entries: List[Dict[str, Any]], ref: str, end_ts: str) -> bool:
    for e in reversed(entries):
        if str(e.get("ref") or "") == ref and not str(e.get("end") or "").strip():
            e["end"] = end_ts
            return True
    return False

def _deploy_history_set_open_stable(entries: List[Dict[str, Any]], ref: str, stable: bool) -> bool:
    for e in reversed(entries):
        if str(e.get("ref") or "") == ref and not str(e.get("end") or "").strip():
            e["stable"] = bool(stable)
            return True
    return False

def _record_deploy_upgrade(prev_ref: Optional[str], new_ref: str, new_is_stable: bool) -> None:
    """Update versions/deploy_ref.txt:
      - set desired ref
      - close previous open entry (end)
      - open new entry (start)
    """
    now = _utc_now_iso()
    st = _read_deploy_ref_file()
    entries: List[Dict[str, Any]] = list(st.get("entries") or [])

    prev_ref = (prev_ref or "").strip() or None
    new_ref = (new_ref or "").strip()
    if not new_ref:
        return

    # Avoid noisy history when "upgrading" to the same ref (often used as restart test).
    if prev_ref and prev_ref == new_ref:
        _write_deploy_ref_file(new_ref, entries)
        return

    if prev_ref:
        _deploy_history_close_open_entry(entries, prev_ref, now)

    entries.append({"ref": new_ref, "stable": bool(new_is_stable), "start": now, "end": ""})
    _write_deploy_ref_file(new_ref, entries)

def _record_deploy_stable_toggle(current_ref: str, stable: bool) -> None:
    """Update stable flag for the currently running (open) entry in deploy_ref history."""
    current_ref = (current_ref or "").strip()
    if not current_ref:
        return
    st = _read_deploy_ref_file()
    desired = (st.get("desired_ref") or current_ref)
    entries: List[Dict[str, Any]] = list(st.get("entries") or [])
    _deploy_history_set_open_stable(entries, current_ref, bool(stable))
    _write_deploy_ref_file(str(desired), entries)

def _read_admin_web_state() -> Dict[str, Any]:
    path = _admin_web_state_path()
    if not path.is_file():
        return {"stable_refs": [], "updated_at": None}
    try:
        raw = path.read_text(encoding="utf-8")
        data = json.loads(raw)
        if not isinstance(data, dict):
            return {"stable_refs": [], "updated_at": None}
        stable = data.get("stable_refs")
        if not isinstance(stable, list):
            stable = []
        stable = [str(x) for x in stable if isinstance(x, (str, int))]
        return {"stable_refs": stable, "updated_at": data.get("updated_at")}
    except Exception:
        return {"stable_refs": [], "updated_at": None}

def _write_admin_web_state(state: Dict[str, Any]) -> None:
    path = _admin_web_state_path()
    tmp = path.with_suffix(".tmp")
    safe = {
        "stable_refs": list({str(x) for x in (state.get("stable_refs") or [])}),
        "updated_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
    }
    tmp.write_text(json.dumps(safe, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)

def _run_git(args: List[str], timeout: int = 20) -> Tuple[int, str, str]:
    try:
        p = subprocess.run(
            ["git"] + list(args),
            cwd=str(BASE_DIR),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        return int(p.returncode), (p.stdout or ""), (p.stderr or "")
    except Exception as e:
        return 1, "", f"git error: {e}"

def _guess_github_owner_repo() -> Optional[Tuple[str, str]]:
    owner = (settings.GITHUB_REPO_OWNER or "").strip()
    repo = (settings.GITHUB_REPO_NAME or "").strip()
    if owner and repo:
        return owner, repo
    code, out, _ = _run_git(["config", "--get", "remote.origin.url"], timeout=8)
    if code != 0 or not out.strip():
        return None
    url = out.strip()
    if url.startswith("git@github.com:"):
        tail = url.split("git@github.com:", 1)[1]
        if tail.endswith(".git"):
            tail = tail[:-4]
        if "/" in tail:
            o, r = tail.split("/", 1)
            if o and r:
                return o, r
    if "github.com/" in url:
        try:
            parsed = urlparse(url)
            parts = [p for p in (parsed.path or "").split("/") if p]
            if len(parts) >= 2:
                o, r = parts[0], parts[1]
                if r.endswith(".git"):
                    r = r[:-4]
                if o and r:
                    return o, r
        except Exception:
            pass
    return None

_SEMVER_RE = re.compile(r"(?:^|[^0-9])(?:web-)?v?(\d+)\.(\d+)\.(\d+)(?:$|[^0-9])", re.IGNORECASE)
def _semver_key(ref: str) -> Tuple[int, int, int]:
    s = (ref or "").strip()
    m = _SEMVER_RE.search(s)
    if not m:
        return (-1, -1, -1)
    try:
        return (int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except Exception:
        return (-1, -1, -1)

def _github_api_get_json(path: str, *, etag: str = "") -> Tuple[Optional[Any], str, int]:
    base = "https://api.github.com"
    url = base + path
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "klimagg-web"}
    token = (settings.GITHUB_API_TOKEN or "").strip()
    if token:
        headers["Authorization"] = "Bearer " + token
    if etag:
        headers["If-None-Match"] = etag
    req = UrlRequest(url, headers=headers, method="GET")
    try:
        with urlopen(req, timeout=12) as resp:
            body = resp.read()
            new_etag = resp.headers.get("ETag") or ""
            code = int(getattr(resp, "status", 200) or 200)
            data = json.loads(body.decode("utf-8")) if body else None
            return data, new_etag, code
    except HTTPError as e:
        if int(getattr(e, "code", 0) or 0) == 304:
            return None, (e.headers.get("ETag") or etag or ""), 304
        raise

def _fetch_github_versions(*, force: bool = False) -> Dict[str, Any]:
    ttl = max(15, int(getattr(settings, "ADMIN_GITHUB_CACHE_SECONDS", 90) or 90))
    now = time.time()
    with _GITHUB_VERSIONS_LOCK:
        age = now - float(_GITHUB_VERSIONS_CACHE.get("fetched_at") or 0.0)
        if (not force) and _GITHUB_VERSIONS_CACHE.get("data") is not None and age < ttl:
            return {
                "items": _GITHUB_VERSIONS_CACHE.get("data") or [],
                "last_checked_at": _GITHUB_VERSIONS_CACHE.get("last_checked_at"),
                "cache_age_sec": max(0.0, age),
                "source": _GITHUB_VERSIONS_CACHE.get("source"),
                "fetch_ok": bool(_GITHUB_VERSIONS_CACHE.get("fetch_ok", False)),
            }
        owner_repo = _guess_github_owner_repo()
        if owner_repo is None:
            _GITHUB_VERSIONS_CACHE.update({"fetched_at": now, "data": [], "last_checked_at": datetime.utcnow(), "source": "none", "fetch_ok": False})
            return {"items": [], "last_checked_at": _GITHUB_VERSIONS_CACHE["last_checked_at"], "cache_age_sec": 0.0, "source": "none", "fetch_ok": False}
        owner, repo = owner_repo
        etag = str(_GITHUB_VERSIONS_CACHE.get("etag") or "")
        try:
            data, new_etag, code = _github_api_get_json(f"/repos/{owner}/{repo}/tags?per_page=100", etag=etag)
            checked_at = datetime.utcnow()
            if code == 304:
                _GITHUB_VERSIONS_CACHE.update({"fetched_at": now, "last_checked_at": checked_at, "source": f"github:{owner}/{repo}", "fetch_ok": True})
            else:
                items: List[Dict[str, Any]] = []
                if isinstance(data, list):
                    for it in data:
                        if not isinstance(it, dict):
                            continue
                        name = str(it.get("name") or "").strip()
                        if not name:
                            continue
                        sha = None
                        c = it.get("commit")
                        if isinstance(c, dict) and c.get("sha"):
                            sha = str(c.get("sha"))
                        items.append({"ref": name, "sha": sha})
                items.sort(key=lambda x: _semver_key(str(x.get("ref") or "")), reverse=True)
                _GITHUB_VERSIONS_CACHE.update({"fetched_at": now, "etag": new_etag or etag or "", "data": items, "last_checked_at": checked_at, "source": f"github:{owner}/{repo}", "fetch_ok": True})
        except Exception:
            cached = _GITHUB_VERSIONS_CACHE.get("data") or []
            _GITHUB_VERSIONS_CACHE.update({"fetched_at": now, "data": cached, "last_checked_at": datetime.utcnow(), "source": _GITHUB_VERSIONS_CACHE.get("source") or "error", "fetch_ok": False})
        age = now - float(_GITHUB_VERSIONS_CACHE.get("fetched_at") or now)
        return {"items": _GITHUB_VERSIONS_CACHE.get("data") or [], "last_checked_at": _GITHUB_VERSIONS_CACHE.get("last_checked_at"), "cache_age_sec": max(0.0, age), "source": _GITHUB_VERSIONS_CACHE.get("source"), "fetch_ok": bool(_GITHUB_VERSIONS_CACHE.get("fetch_ok", False))}


app = FastAPI(title=settings.PROJECT_NAME)


# Validate schema before starting background workers. This is read-only and never runs migrations.
app.add_event_handler("startup", _ensure_db_schema_ready)
app.add_event_handler("startup", _startup_account_delete_housekeeping)
app.add_event_handler("startup", _start_metrics_background_flush_thread)
app.add_event_handler("shutdown", _shutdown_runtime_cleanup)


# CORS is primarily for explicitly configured development/frontend origins.
# With credentials enabled, `BACKEND_CORS_ORIGINS` must remain an explicit allowlist.
# Do not add production wildcards or preview origins without an explicit review.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.BACKEND_CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def security_headers_middleware(request: Request, call_next):
    """Apply the configured standard security headers to responses without overwriting headers already set explicitly."""
    response = await call_next(request)

    # Production deployments are expected to keep application security headers enabled.
    
    if settings.ENABLE_SECURITY_HEADERS:
        # Do not overwrite headers explicitly set by a route/response.
        csp_value = (settings.CSP_DEFAULT or "").strip()
        response.headers.setdefault("Content-Security-Policy", csp_value)
        response.headers.setdefault("X-Frame-Options", settings.X_FRAME_OPTIONS)
        response.headers.setdefault("Referrer-Policy", settings.REFERRER_POLICY)
        response.headers.setdefault("X-Content-Type-Options", settings.X_CONTENT_TYPE_OPTIONS)

    return response


@app.middleware("http")
async def metrics_middleware(request: Request, call_next):
    """Collect privacy-friendly daily page-view and visitor estimates without cookies. Metrics collection must never break page delivery."""
    # Reloads or client disconnects may cancel the request with `CancelledError`.
    # Treat that as a normal disconnect rather than an application failure.
    try:
        response = await call_next(request)
    except asyncio.CancelledError:
        # Return a bodyless 204; non-standard 499 is deliberately avoided.
        return Response(status_code=204)

    if not settings.ENABLE_ANALYTICS:
        return response

    try:
        if request.method != "GET":
            return response

        path = request.url.path or "/"
        if any(path.startswith(p) for p in (settings.ANALYTICS_EXCLUDE_PREFIXES or [])):
            return response

        low = path.lower()
        if any(low.endswith(ext) for ext in (settings.ANALYTICS_EXCLUDE_EXTS or [])):
            return response

        if response.status_code != 200:
            return response

        ctype = (response.headers.get("content-type") or "").lower()
        if "text/html" not in ctype:
            return response

        day = _metrics_utc_day()
        client_ip = _metrics_client_ip(request)
        ua = request.headers.get("user-agent", "")
        vkey = _metrics_visitor_key(day, client_ip, ua)
        is_logged_in = _metrics_is_logged_in_request(request)

        if _metrics_buffer_enabled():
            _metrics_queue_event(
                day=day,
                visitor_key=vkey,
                pageviews=1,
                pageviews_logged_in=1 if is_logged_in else 0,
            )
            if _METRICS_BUFFER_EVENT_COUNT >= _metrics_buffer_max_events():
                threading.Thread(target=_metrics_flush_buffer_once, name="web_metrics_flush_once", daemon=True).start()
        else:
            db = SessionLocal()
            try:
                with SQLITE_WRITE_LOCK:
                    _metrics_upsert_daily_increment(db, day, "pageviews", 1)
                    if is_logged_in:
                        _metrics_upsert_daily_increment(db, day, "pageviews_logged_in", 1)
                    res = db.execute(
                        text(
                            """
                            INSERT INTO metrics_visitors_daily (day, visitor_key, created_at)
                            VALUES (:day, :vkey, CURRENT_TIMESTAMP)
                            ON CONFLICT(day, visitor_key) DO NOTHING
                            """
                        ),
                        {"day": day.isoformat(), "vkey": vkey},
                    )
                    if getattr(res, "rowcount", 0) == 1:
                        _metrics_upsert_daily_increment(db, day, "visitors_est", 1)
                    _commit_db(db)
            finally:
                db.close()
    except Exception:
        pass

    return response


# ---------------------------------------------------------------------------
# Allowlist-based pseudonym generator; free-form names are not accepted.
# ---------------------------------------------------------------------------
#

# - neutral vocabulary without political, financial, institutional, or abusive terms
# - intentionally easy to extend later; the current pool is defined in code
#

def _lower_first(s: str) -> str:
    if not s:
        return s
    return s[:1].lower() + s[1:]


def _adj_strong_nom(stem: str, gender: str) -> str:
    """Inflect a German adjective using strong nominative endings without an article.
    
    The intentionally small morphology helper supports the generated German pseudonym pool and includes common `-el`/`-er` stem handling.
    """
    base = (stem or "").strip()
    if not base:
        return base

    # Common German spelling rule: `dunkel -> dunkl-`, `teuer -> teur-` before endings.
    if len(base) >= 3 and base[-2] == "e" and (base.endswith("el") or base.endswith("er")):
        base = base[:-2] + base[-1]  # Drop the stem `e` before common `l`/`r` adjective endings.

    g = (gender or "m").lower()
    if g == "f":
        # German feminine/plural form example.
        return base if base.endswith("e") else base + "e"
    if g == "n":
        # German feminine/plural form example.s
        if base.endswith("e"):
            return base + "s"
        return base + "es"
    if g == "pl":
        # German feminine/plural form example.
        return base if base.endswith("e") else base + "e"

    # Default grammatical gender: masculine.
    if base.endswith("e"):
        return base + "r"
    return base + "er"


def _compound_prefix(w2: str, w3: str) -> str:
    """Apply the small set of German compound-linking rules used by the generated pseudonym pool, especially the common `-n` linker after words ending in `-e`."""
    a = (w2 or "").strip()
    b = (w3 or "").strip()
    if not a or not b:
        return a

    # Common compound-linking rule for the configured noun pools: `-e -> -en/-n`.
    # Examples: Fuge->Fugen, Küste->Küsten, Wiese->Wiesen, Aue->Auen, Gasse->Gassen, Treppe->Treppen, Laterne->Laternen.
    if a.endswith("e"):
        return a + "n"

    return a


def _compose_compound(w2: str, w3: str) -> str:
    """Compose a German noun compound as one word without internal capitalization, e.g. `Leinwand + Fläche -> Leinwandfläche`."""
    prefix = _compound_prefix(w2, w3)
    return prefix + _lower_first(w3)


# Pools store stems rather than pre-inflected forms; grammatical gender comes from the head noun (`w3`).
PSEUDONYM_POOLS: dict[str, dict[str, object]] = {
    "neutral": {
        "label": "Neutral · Form & Material",
        "description": "Sachlich, unaufgeregt. Formen, Materialien, Geometrie.",
        "w1_stems": ["klar", "ruhig", "schlicht", "gerade", "fein", "still", "fest", "leicht"],
        "w2": ["Basalt", "Quarz", "Granit", "Kalkstein", "Kupfer", "Stahl", "Glas", "Lehm", "Schiefer"],
        "w3": ["Bogen", "Kante", "Punkt", "Rand", "Linie", "Feld", "Pfad", "Kreis"],
        "w3_gender": {
            "Bogen": "m",
            "Kante": "f",
            "Punkt": "m",
            "Rand": "m",
            "Linie": "f",
            "Feld": "n",
            "Pfad": "m",
            "Kreis": "m",
        },
    },
    "natur": {
        "label": "Natur · Atmosphäre",
        "description": "Landschaft, Wetter, Ruhe. Ohne Aktivismus-Slogans.",
        "w1_stems": ["leis", "hell", "kühl", "sanft", "nebelig", "weit", "warm", "frisch"],
        "w2": ["Wald", "Moor", "Fluss", "Küste", "Wiese", "Tal", "Hügel", "See", "Aue"],
        "w3": ["Saum", "Wind", "Licht", "Ufer", "Pfad", "Regen", "Blick", "Dunst"],
        "w3_gender": {
            "Saum": "m",
            "Wind": "m",
            "Licht": "n",
            "Ufer": "n",
            "Pfad": "m",
            "Regen": "m",
            "Blick": "m",
            "Dunst": "m",
        },
    },
    "technik": {
        "label": "Technik · Messung",
        "description": "Werkstatt, Messpunkte, Mechanik – nüchtern & freundlich.",
        "w1_stems": ["präzise", "stabil", "ruhig", "fein", "sicher", "klar", "solide"],
        "w2": ["Sensor", "Regler", "Ventil", "Modul", "Raster", "Schaltkreis", "Antrieb", "Impuls"],
        "w3": ["Skala", "Achse", "Takt", "Wert", "Punkt", "Signal", "Drehmoment", "Kurve"],
        "w3_gender": {
            "Skala": "f",
            "Achse": "f",
            "Takt": "m",
            "Wert": "m",
            "Punkt": "m",
            "Signal": "n",
            "Drehmoment": "n",
            "Kurve": "f",
        },
    },
    "handwerk": {
        "label": "Handwerk · Werkstatt",
        "description": "Bodenständig: Holz, Ton, Bau, Werkzeuge (ohne Rollen-/Titelwörter).",
        "w1_stems": ["sauber", "glatt", "fest", "ruhig", "fein", "stabil", "sicher"],
        "w2": ["Hobel", "Ziegel", "Fuge", "Zapfen", "Werkbank", "Ton", "Faser", "Kelle"],
        "w3": ["Griff", "Kante", "Bogen", "Naht", "Kern", "Rand", "Schicht", "Fläche"],
        "w3_gender": {
            "Griff": "m",
            "Kante": "f",
            "Bogen": "m",
            "Naht": "f",
            "Kern": "m",
            "Rand": "m",
            "Schicht": "f",
            "Fläche": "f",
        },
    },
    "kunst": {
        "label": "Kunst · Farbe & Klang",
        "description": "Kreativ, aber neutral: Papier, Pigment, Linie, Klang.",
        "w1_stems": ["zart", "kräftig", "sanft", "hell", "fein", "ruhig"],
        "w2": ["Aquarell", "Pigment", "Papier", "Leinwand", "Klang", "Rhythmus", "Farbton"],
        "w3": ["Linie", "Rand", "Ton", "Bogen", "Fläche", "Takt", "Spur", "Punkt"],
        "w3_gender": {
            "Linie": "f",
            "Rand": "m",
            "Ton": "m",
            "Bogen": "m",
            "Fläche": "f",
            "Takt": "m",
            "Spur": "f",
            "Punkt": "m",
        },
    },
    "stadt": {
        "label": "Stadt · Alltag",
        "description": "Innenhof, Brücke, Weg – deutsche Alltags-Orte ohne Ortsnamen.",
        "w1_stems": ["still", "hell", "klein", "ruhig", "gerade", "fein"],
        "w2": ["Innenhof", "Brücke", "Markt", "Weg", "Platz", "Gasse", "Treppe", "Laterne"],
        "w3": ["Ecke", "Bogen", "Pflaster", "Rand", "Blick", "Pfad", "Kante", "Spur"],
        "w3_gender": {
            "Ecke": "f",
            "Bogen": "m",
            "Pflaster": "n",
            "Rand": "m",
            "Blick": "m",
            "Pfad": "m",
            "Kante": "f",
            "Spur": "f",
        },
    },
}


def _generate_pseudonym_once(pool_id: str) -> str:
    pool = PSEUDONYM_POOLS.get(pool_id) or PSEUDONYM_POOLS["neutral"]
    w2 = secrets.choice(pool["w2"])  # type: ignore[arg-type]
    w3 = secrets.choice(pool["w3"])  # type: ignore[arg-type]
    genders = pool.get("w3_gender") or {}
    gender = "m"
    if isinstance(genders, dict):
        gender = str(genders.get(w3, "m"))
    w1_stems = pool.get("w1_stems") or []
    stem = secrets.choice(w1_stems)  # type: ignore[arg-type]
    w1 = _adj_strong_nom(str(stem), gender)
    return f"{w1} {_compose_compound(str(w2), str(w3))}"


def generate_pseudonym_suggestions(*, pool_id: str, n: int, db: Session | None = None) -> list[str]:
    n = max(1, min(int(n), 30))
    out: list[str] = []
    seen: set[str] = set()

    existing: set[str] = set()
    if db is not None:
        try:
            existing = {row[0] for row in db.query(User.pseudonym).all() if row and row[0]}
        except Exception:
            existing = set()

    for _ in range(400):
        if len(out) >= n:
            break
        cand = _generate_pseudonym_once(pool_id).strip()
        if not cand:
            continue
        if len(cand) > 32:
            continue
        if cand in seen:
            continue
        if existing and cand in existing:
            continue
        seen.add(cand)
        out.append(cand)
    return out


def is_allowed_pseudonym(pseudo: str) -> bool:
    """Reject free-form pseudonyms; only exact combinations generated from the configured allowlist pools are accepted."""
    if not pseudo:
        return False
    s = pseudo.strip()
    if len(s) > 64:
        return False
    if " " not in s:
        return False
    w1, rest = s.split(" ", 1)
    w1 = w1.strip()
    rest = rest.strip()
    if not w1 or not rest:
        return False

    for cfg in PSEUDONYM_POOLS.values():
        w2_list = cfg.get("w2") or []
        w3_list = cfg.get("w3") or []
        w1_stems = cfg.get("w1_stems") or []
        genders = cfg.get("w3_gender") or {}
        for w3 in w3_list:
            # Determine grammatical gender from the compound head noun (`w3`).
            gender = "m"
            if isinstance(genders, dict):
                gender = str(genders.get(w3, "m"))

            # Generate valid adjective forms for the selected grammatical gender.
            allowed_w1_new = set()
            for st in w1_stems:
                allowed_w1_new.add(_adj_strong_nom(str(st), gender))

            # Rebuild the compound without internal capitalization and with the configured linker.
            for w2 in w2_list:
                new_rest = _compose_compound(str(w2), str(w3))
                if rest == new_rest and w1 in allowed_w1_new:
                    return True

    return False


# ---------------------------------------------------------------------------
# MiniMD editor path
# ---------------------------------------------------------------------------
# The editor works directly against the canonical MiniMD source text.
# Product styling renders inserts and deletions through the shared diff classes.
#
# Historical note:

# ---------------------------------------------------------------------------

import html as _html


def _minimd_block_order() -> list[str]:
    out = list(getattr(settings, "MINIMD_BLOCKS_ORDER", []) or [])
    if out:
        return out
    return ["kurzinfo", "story", "einleitung", "juristisch", "juristisch2", "anmerkung"]

_MERGE_BLOCK_ORDER = tuple(_minimd_block_order())

def _mm_title_line(title: str) -> str:
    title = (title or "").strip()
    tpl = str(getattr(settings, "MINIMD_TITLE_TEMPLATE", "### Artikel-Titel: {title} ###") or "### Artikel-Titel: {title} ###")
    return tpl.format(title=title)


def _mm_toc_line(toc: str) -> str:
    toc = (toc or "").strip()
    tpl = str(getattr(settings, "MINIMD_TITLE_TOC", "### Artikel-Kurz: {toc} ###") or "### Artikel-Kurz: {toc} ###")
    return tpl.format(toc=toc)


def _meta_value_line(label: str, value: object) -> str:
    return f"$ {str(label or '').strip()}: $ {str(value or '').strip()}"


def _bool_to_ja_nein(value: object) -> str:
    return "Ja" if bool(value) else "Nein"


def _article_meta_minimd_body(article: object) -> str:
    """Build the persisted MiniMD body for the regular `meta` block. This helper is for bootstrap/repair and release work; normal render/diff paths read the stored block directly."""
    public_code = str(getattr(article, "public_code", "") or "").strip()
    title = str(getattr(article, "title", "") or "").strip()
    toc_title = str(getattr(article, "toc_title", "") or "").strip()
    show_in_toc = bool(getattr(article, "show_in_toc", False))
    return "\n".join([
        _meta_value_line("Artikel-Kennung", public_code),
        _meta_value_line("Artikel-Titel", title),
        _meta_value_line("Artikel-Kurztitel", toc_title),
        _meta_value_line("Artikel im Inhaltsverzeichnis", _bool_to_ja_nein(show_in_toc)),
        _meta_value_line("Einfügen nach folgender Artikel-Kennung", ""),
    ])


def _article_meta_minimd_block_for_backfill_or_release(article: object) -> str:
    body = _article_meta_minimd_body(article)
    return _mm_block_start("meta") + "\n" + body + "\n" + _mm_block_end("meta") + "\n\n"


def _ensure_article_version_meta_block(version: object, *, overwrite: bool = False) -> bool:
    """Backfill a missing `content_blocks["meta"]` once from article fields and report whether the version row changed."""
    if version is None:
        return False
    article = getattr(version, "article", None)
    if article is None:
        return False
    content = getattr(version, "content_blocks", None)
    if isinstance(content, dict):
        blocks = dict(content)
    else:
        blocks = {}
    current = str(blocks.get("meta") or "")
    if current.strip() and not bool(overwrite):
        return False
    blocks["meta"] = _article_meta_minimd_body(article)
    setattr(version, "content_blocks", blocks)
    return True


def _mm_block_start(name: str) -> str:
    tpl = str(getattr(settings, "MINIMD_BLOCK_START_TEMPLATE", "### start: {name} ###") or "### start: {name} ###")
    return tpl.format(name=name)


def _mm_block_end(name: str) -> str:
    tpl = str(getattr(settings, "MINIMD_BLOCK_END_TEMPLATE", "### end: {name} ###") or "### end: {name} ###")
    return tpl.format(name=name)


def html_to_minimd(html_text: str) -> str:
    """Thin compatibility wrapper; canonical conversion lives in indiff.py."""
    return code.html_to_minimd_text("_inline", str(html_text or ""))


def article_version_to_minimd(version) -> tuple[str, list[str]]:
    """Canonical article MiniMD via the shared indiff.py format bridge."""
    return code.content_blocks_to_minimd(
        getattr(version, "content_blocks", None),
        block_order=_minimd_block_order(),
    )


def _is_published_article_version(v: "ArticleVersion") -> bool:
    """Return whether an article version is public, preferring explicit publication fields and falling back to the configured version-label heuristic."""
    st = str(getattr(v, "status", "") or "").strip().lower()
    if st in {"published", "veröffentlicht"}:
        return True
    if getattr(v, "published_at", None) is not None:
        return True
    try:
        rx = getattr(settings, "PUBLISHED_VERSION_LABEL_REGEX", r"^v\d{4}-\d{2}-\d{2}$")
        if rx:
            return re.match(rx, str(getattr(v, "version_label", "") or "")) is not None
    except Exception:
        return False
    return False


@app.get("/api/articles/{article_id}/versions/{version_id}/minimd", response_model=ArticleMiniMdOut)
def api_article_version_minimd(
    article_id: int,
    version_id: int,
    db: Session = Depends(get_db),
    current_user: User | None = Depends(get_current_user_optional),
) -> ArticleMiniMdOut:
    """Return canonical MiniMD for an article version. Anonymous access is restricted to published versions; protected drafts require authentication."""
    v = (
        db.query(ArticleVersion)
        .filter(ArticleVersion.id == version_id, ArticleVersion.article_id == article_id)
        .one_or_none()
    )
    if v is None:
        raise HTTPException(status_code=404, detail="Version nicht gefunden")
       
    # Comment v2: public/private boundary
    # Anonymous access is limited to published versions.
    # Unpublished versions require owner/admin authorization.
    if not _is_published_article_version(v):
        if current_user is None:
            raise HTTPException(status_code=403, detail="MiniMD ist nur für veröffentlichte Versionen öffentlich")
        is_admin = bool(getattr(current_user, "is_admin", False))
        is_owner = (getattr(v, "created_by_user_id", None) == getattr(current_user, "id", None))
        if not (is_admin or is_owner):
            raise HTTPException(status_code=403, detail="Kein Zugriff auf diese Version")
 
    minimd, blocks = article_version_to_minimd(v)
    return ArticleMiniMdOut(article_id=int(article_id), version_id=int(version_id), minimd=minimd, blocks=blocks)


# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------

def render_comment_markup(raw: str) -> str:
    return code.minimd_to_html_text("_comment", str(raw or ""))


# -----------------------------------------------------------------------------
# Voting constants and aggregation
# ---------------------------------------------------------------------------

ALLOWED_MAIN_VOTES = ["✅", "🟢", "🟡", "🟠", "🔴"]
# Keep the reaction vocabulary deliberately small and quality-focused.
# Extend the supported reaction set here and in the coordinated UI render paths.
ALLOWED_FLAG_EMOJIS = ["🧭", "✍️", "🧩", "⚖️", "🚩"]

_VOTE_AUTO_CONFIRM_PUBLIC_STATS_LOCK = threading.Lock()
_VOTE_AUTO_CONFIRM_PUBLIC_STATS_LAST_RUN_AT = 0.0
_PUBLIC_PROJECT_STATS_CACHE_LOCK = threading.Lock()
_PUBLIC_PROJECT_STATS_CACHE: Dict[int, Dict[str, Any]] = {}
_PUBLIC_TRAFFIC_STATS_CACHE_LOCK = threading.Lock()
_PUBLIC_TRAFFIC_STATS_CACHE: Dict[str, Dict[str, Any]] = {}
_PUBLIC_ARTICLES_CACHE_LOCK = threading.Lock()
_PUBLIC_ARTICLES_LIST_CACHE: Dict[str, Any] = {}
_PUBLIC_ARTICLES_SSR_CACHE: Dict[str, Any] = {}
_PUBLIC_ARTICLE_DETAIL_CACHE: Dict[int, Dict[str, Any]] = {}
_ADMIN_RUNTIME_CACHE_LOCK = threading.Lock()
_ADMIN_RUNTIME_CACHE: Dict[str, Dict[str, Any]] = {}
_NEXT_DRAFT_LAYER_CACHE_LOCK = threading.Lock()
_NEXT_DRAFT_LAYER_CACHE: Dict[str, Dict[str, Any]] = {}


def _merge_preview_max_concurrent() -> int:
    try:
        return max(1, int(getattr(settings, "MERGE_PREVIEW_MAX_CONCURRENT", 1) or 1))
    except Exception:
        return 1


_MERGE_PREVIEW_SEMAPHORE = threading.BoundedSemaphore(_merge_preview_max_concurrent())


def _merge_preview_max_comments() -> int:
    try:
        return max(1, int(getattr(settings, "MERGE_PREVIEW_MAX_COMMENTS", 60) or 60))
    except Exception:
        return 60


def _merge_preview_max_total_chars() -> int:
    try:
        return max(50_000, int(getattr(settings, "MERGE_PREVIEW_MAX_TOTAL_CHARS", 750_000) or 750_000))
    except Exception:
        return 750_000


def _merge_preview_time_budget_seconds() -> float:
    try:
        return max(2.0, float(getattr(settings, "MERGE_PREVIEW_TIME_BUDGET_SECONDS", 15.0) or 15.0))
    except Exception:
        return 15.0


def _merge_preview_try_acquire() -> bool:
    try:
        return bool(_MERGE_PREVIEW_SEMAPHORE.acquire(blocking=False))
    except Exception:
        return True


def _merge_preview_release() -> None:
    try:
        _MERGE_PREVIEW_SEMAPHORE.release()
    except Exception:
        pass


def _merge_preview_raise_if_request_too_large(*, cids: list[int]) -> None:
    max_comments = _merge_preview_max_comments()
    if len(list(cids or [])) > max_comments:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail={
                "error": "merge_preview_too_many_comments",
                "max_comments": int(max_comments),
                "count": int(len(list(cids or []))),
            },
        )


def _merge_preview_patch_payload_chars(comment: Comment) -> int:
    try:
        payload = comment.patch_payload if isinstance(getattr(comment, "patch_payload", None), dict) else {}
        parts = payload.get("parts") if isinstance(payload, dict) else []
        total = 0
        if isinstance(parts, list):
            for p in parts:
                if not isinstance(p, dict):
                    continue
                total += len(str(p.get("old_text") or ""))
                total += len(str(p.get("new_text") or ""))
        return int(total)
    except Exception:
        return 0


def _merge_preview_raise_if_loaded_payload_too_large(article_row: Article, comments: list[Comment]) -> None:
    max_chars = _merge_preview_max_total_chars()
    total = 0
    try:
        v = getattr(article_row, "current_version", None)
        blocks = getattr(v, "content_blocks", None) if v is not None else None
        if isinstance(blocks, dict):
            total += sum(len(str(x or "")) for x in blocks.values())
        elif blocks is not None:
            total += len(str(blocks))
    except Exception:
        pass
    for c in list(comments or []):
        total += _merge_preview_patch_payload_chars(c)
    if total > max_chars:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail={
                "error": "merge_preview_payload_too_large",
                "max_total_chars": int(max_chars),
                "estimated_total_chars": int(total),
            },
        )


def _merge_preview_raise_if_time_budget_exceeded(started_at: float) -> None:
    budget = _merge_preview_time_budget_seconds()
    elapsed = time.monotonic() - float(started_at or time.monotonic())
    if elapsed > budget:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "error": "merge_preview_time_budget_exceeded",
                "budget_seconds": float(budget),
                "elapsed_seconds": round(float(elapsed), 3),
            },
        )


def _merge_preview_busy_exception() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail={
            "error": "merge_preview_busy",
            "message": "Merge-Preview ist gerade ausgelastet. Bitte kurz später erneut versuchen.",
        },
    )


def _normalize_unique_positive_ints(raw_values: Any) -> list[int]:
    if not isinstance(raw_values, list):
        raise HTTPException(status_code=400, detail="cids must be a list")
    out: list[int] = []
    seen: set[int] = set()
    for x in raw_values:
        try:
            n = int(x)
        except Exception:
            continue
        if n > 0 and n not in seen:
            seen.add(n)
            out.append(n)
    return out


def _normalize_flags(flags: list[str] | None) -> list[str]:
    """Filter and deduplicate reaction flags to the supported emoji set."""
    if not flags:
        return []
    cleaned: list[str] = []
    seen: set[str] = set()
    for f in flags:
        if f in ALLOWED_FLAG_EMOJIS and f not in seen:
            seen.add(f)
            cleaned.append(f)
    return cleaned


def confirm_article_votes(
    db: Session,
    *,
    vote_ids: list[int] | None = None,
    article_id: int | None = None,
    user_id: int | None = None,
    version_id: int | None = None,
    created_before: datetime | None = None,
    include_statuses: list[str] | None = None,
    now: datetime | None = None,
    commit: bool = True,
) -> int:
    """Batch-confirm article votes using optional vote/article/user/version/date/status filters and return the number of updated rows."""
    if now is None:
        now = datetime.utcnow()

    statuses = include_statuses or ["pending"]

    q = db.query(ArticleVote)

    if vote_ids:
        q = q.filter(ArticleVote.id.in_(vote_ids))
    if article_id is not None:
        q = q.filter(ArticleVote.article_id == article_id)
    if user_id is not None:
        q = q.filter(ArticleVote.user_id == user_id)
    if version_id is not None:
        q = q.filter(ArticleVote.version_id == version_id)
    if created_before is not None:
        q = q.filter(ArticleVote.created_at <= created_before)

    q = q.filter(ArticleVote.status.in_(statuses))

    updated = q.update(
        {"status": "confirmed", "confirmed_at": now},
        synchronize_session=False,
    )

    if commit and updated:
        _commit_db(db)

    return int(updated or 0)


def _auto_confirm_votes_for_article(
    db: Session,
    article: Article,
    now: datetime | None = None,
) -> None:
    """Promote eligible pending votes to confirmed after the configured delay unless the article is frozen."""
    # Never auto-confirm votes while the article is frozen.
    if getattr(article, "vote_freeze", False):
        return

    if now is None:
        now = datetime.utcnow()

    try:
        delay_minutes = int(getattr(settings, "VOTE_CONFIRM_DELAY_MINUTES", 10) or 0)
    except Exception:
        delay_minutes = 10

    cutoff = now if delay_minutes <= 0 else now - timedelta(minutes=delay_minutes)

    confirm_article_votes(
        db,
        article_id=article.id,
        created_before=cutoff,
        include_statuses=["pending"],
        now=now,
        commit=True,
    )


def _auto_confirm_due_article_votes_for_public_stats(
    db: Session,
    now: datetime | None = None,
) -> int:
    """Confirm due article votes for public statistics in the background while excluding frozen articles; return the number confirmed."""
    if not bool(getattr(settings, "VOTE_AUTO_CONFIRM_PUBLIC_STATS_ENABLED", True)):
        return 0

    try:
        delay_minutes = int(getattr(settings, "VOTE_CONFIRM_DELAY_MINUTES", 10) or 0)
    except Exception:
        delay_minutes = 10
    try:
        interval_seconds = int(getattr(settings, "VOTE_AUTO_CONFIRM_PUBLIC_STATS_INTERVAL_SECONDS", 60) or 0)
    except Exception:
        interval_seconds = 60
    interval_seconds = max(0, interval_seconds)

    global _VOTE_AUTO_CONFIRM_PUBLIC_STATS_LAST_RUN_AT

    mono_now = time.monotonic()
    with _VOTE_AUTO_CONFIRM_PUBLIC_STATS_LOCK:
        if (
            interval_seconds > 0
            and mono_now - float(_VOTE_AUTO_CONFIRM_PUBLIC_STATS_LAST_RUN_AT or 0.0) < interval_seconds
        ):
            return 0
        _VOTE_AUTO_CONFIRM_PUBLIC_STATS_LAST_RUN_AT = mono_now

    if now is None:
        now = datetime.utcnow()

    cutoff = now if delay_minutes <= 0 else now - timedelta(minutes=delay_minutes)

    try:
        batch_limit = int(getattr(settings, "VOTE_AUTO_CONFIRM_PUBLIC_STATS_BATCH_LIMIT", 500) or 500)
    except Exception:
        batch_limit = 500
    batch_limit = max(1, batch_limit)

    try:
        rows = (
            db.query(ArticleVote.id)
            .join(Article, Article.id == ArticleVote.article_id)
            .filter(ArticleVote.status == "pending")
            .filter(ArticleVote.created_at <= cutoff)
            .filter(Article.vote_freeze == False)  # noqa: E712
            .order_by(ArticleVote.created_at.asc(), ArticleVote.id.asc())
            .limit(batch_limit)
            .all()
        )
        vote_ids = [int(row[0]) for row in rows if row and row[0] is not None]
        if not vote_ids:
            return 0
        return confirm_article_votes(
            db,
            vote_ids=vote_ids,
            include_statuses=["pending"],
            now=now,
            commit=True,
        )
    except Exception:
        _rollback_db_quietly(db)
        return 0


def _auto_confirm_public_stats_background_loop() -> None:
    while True:
        try:
            interval_seconds = int(getattr(settings, "VOTE_AUTO_CONFIRM_PUBLIC_STATS_INTERVAL_SECONDS", 60) or 60)
        except Exception:
            interval_seconds = 60
        interval_seconds = max(15, interval_seconds)
        try:
            time.sleep(interval_seconds)
            if not bool(getattr(settings, "VOTE_AUTO_CONFIRM_PUBLIC_STATS_BACKGROUND_ENABLED", True)):
                continue
            if not bool(getattr(settings, "VOTE_AUTO_CONFIRM_PUBLIC_STATS_ENABLED", True)):
                continue
            db = SessionLocal()
            try:
                with SQLITE_WRITE_LOCK:
                    confirmed = _auto_confirm_due_article_votes_for_public_stats(db, now=datetime.utcnow())
                    if confirmed:
                        _clear_public_runtime_caches()
            finally:
                db.close()
        except Exception:
            pass


def _start_public_stats_vote_confirm_thread() -> None:
    if not bool(getattr(settings, "VOTE_AUTO_CONFIRM_PUBLIC_STATS_BACKGROUND_ENABLED", True)):
        return
    if not bool(getattr(settings, "VOTE_AUTO_CONFIRM_PUBLIC_STATS_ENABLED", True)):
        return
    if getattr(_start_public_stats_vote_confirm_thread, "_started", False):
        return
    _start_public_stats_vote_confirm_thread._started = True  # type: ignore[attr-defined]
    t = threading.Thread(
        target=_auto_confirm_public_stats_background_loop,
        name="web_vote_confirm_public_stats",
        daemon=True,
    )
    t.start()


app.add_event_handler("startup", _start_public_stats_vote_confirm_thread)


def _public_stats_cache_seconds() -> int:
    try:
        return max(0, int(getattr(settings, "PUBLIC_STATS_CACHE_SECONDS", 60) or 0))
    except Exception:
        return 60


def _public_project_stats_to_cache_payload(out: PublicProjectStatsOut) -> Dict[str, Any]:
    if hasattr(out, "model_dump"):
        return dict(out.model_dump(mode="json"))
    if hasattr(out, "dict"):
        return dict(out.dict())
    return dict(out)


def _public_project_stats_from_cache(days: int) -> PublicProjectStatsOut | None:
    ttl = _public_stats_cache_seconds()
    if ttl <= 0:
        return None
    key = int(days)
    now = time.monotonic()
    with _PUBLIC_PROJECT_STATS_CACHE_LOCK:
        row = dict(_PUBLIC_PROJECT_STATS_CACHE.get(key) or {})
    if not row:
        return None
    age = now - float(row.get("stored_at") or 0.0)
    if age < 0 or age > ttl:
        return None
    data = row.get("data")
    if not isinstance(data, dict):
        return None
    try:
        return PublicProjectStatsOut(**data)
    except Exception:
        return None


def _public_project_stats_store_cache(days: int, out: PublicProjectStatsOut) -> None:
    ttl = _public_stats_cache_seconds()
    if ttl <= 0:
        return
    with _PUBLIC_PROJECT_STATS_CACHE_LOCK:
        _PUBLIC_PROJECT_STATS_CACHE[int(days)] = {
            "stored_at": time.monotonic(),
            "data": _public_project_stats_to_cache_payload(out),
        }


def _traffic_stats_cache_key(kind: str, days: int) -> str:
    return f"{str(kind or '').strip()}:{int(days)}"


def _traffic_stats_from_cache(kind: str, days: int) -> dict[str, Any] | None:
    ttl = _public_stats_cache_seconds()
    if ttl <= 0:
        return None
    key = _traffic_stats_cache_key(kind, days)
    now = time.monotonic()
    with _PUBLIC_TRAFFIC_STATS_CACHE_LOCK:
        row = dict(_PUBLIC_TRAFFIC_STATS_CACHE.get(key) or {})
    if not row:
        return None
    age = now - float(row.get("stored_at") or 0.0)
    if age < 0 or age > ttl:
        return None
    data = row.get("data")
    if not isinstance(data, dict):
        return None
    return dict(data)


def _traffic_stats_store_cache(kind: str, days: int, data: dict[str, Any]) -> None:
    ttl = _public_stats_cache_seconds()
    if ttl <= 0 or not isinstance(data, dict):
        return
    key = _traffic_stats_cache_key(kind, days)
    with _PUBLIC_TRAFFIC_STATS_CACHE_LOCK:
        _PUBLIC_TRAFFIC_STATS_CACHE[key] = {
            "stored_at": time.monotonic(),
            "data": dict(data),
        }


def _public_traffic_stats_to_cache_payload(out: PublicTrafficStatsOut) -> dict[str, Any]:
    if hasattr(out, "model_dump"):
        return dict(out.model_dump(mode="json"))
    if hasattr(out, "dict"):
        return dict(out.dict())
    return dict(out)


def _admin_runtime_cache_seconds() -> int:
    try:
        return max(0, int(getattr(settings, "ADMIN_RUNTIME_CACHE_SECONDS", 30) or 0))
    except Exception:
        return 30


def _cache_payload(value: Any) -> Any:
    """Return a short JSON-compatible copy suitable for in-process runtime caches."""
    if hasattr(value, "model_dump"):
        try:
            return _cache_payload(value.model_dump(mode="json"))
        except Exception:
            try:
                return _cache_payload(value.model_dump())
            except Exception:
                pass
    if hasattr(value, "dict"):
        try:
            return _cache_payload(value.dict())
        except Exception:
            pass
    if isinstance(value, dict):
        return {str(k): _cache_payload(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_cache_payload(v) for v in value]
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


def _admin_runtime_cache_get(key: str) -> Dict[str, Any] | None:
    ttl = _admin_runtime_cache_seconds()
    if ttl <= 0:
        return None
    cache_key = str(key or "").strip()
    if not cache_key:
        return None
    now = time.monotonic()
    with _ADMIN_RUNTIME_CACHE_LOCK:
        row = dict(_ADMIN_RUNTIME_CACHE.get(cache_key) or {})
    if not row:
        return None
    age = now - float(row.get("stored_at") or 0.0)
    if age < 0 or age > ttl:
        return None
    data = row.get("data")
    if not isinstance(data, dict):
        return None
    return dict(data)


def _admin_runtime_cache_set(key: str, data: Dict[str, Any]) -> None:
    ttl = _admin_runtime_cache_seconds()
    if ttl <= 0 or not isinstance(data, dict):
        return
    cache_key = str(key or "").strip()
    if not cache_key:
        return
    with _ADMIN_RUNTIME_CACHE_LOCK:
        _ADMIN_RUNTIME_CACHE[cache_key] = {
            "stored_at": time.monotonic(),
            "data": dict(_cache_payload(data)),
        }


def _clear_admin_runtime_caches() -> None:
    try:
        with _ADMIN_RUNTIME_CACHE_LOCK:
            _ADMIN_RUNTIME_CACHE.clear()
    except Exception:
        pass


def _next_draft_layer_cache_seconds() -> int:
    try:
        return max(0, int(getattr(settings, "NEXT_DRAFT_LAYER_CACHE_SECONDS", 600) or 0))
    except Exception:
        return 600


def _next_draft_layer_cache_key(article_id: int, version_id: int | None) -> str:
    return f"next_draft:{int(article_id)}:{int(version_id or 0)}"


def _next_draft_layer_from_cache(article_id: int, version_id: int | None) -> Dict[str, Any] | None:
    ttl = _next_draft_layer_cache_seconds()
    if ttl <= 0:
        return None
    key = _next_draft_layer_cache_key(article_id, version_id)
    now = time.monotonic()
    with _NEXT_DRAFT_LAYER_CACHE_LOCK:
        row = dict(_NEXT_DRAFT_LAYER_CACHE.get(key) or {})
    if not row:
        return None
    age = now - float(row.get("stored_at") or 0.0)
    if age < 0 or age > ttl:
        return None
    data = row.get("data")
    if not isinstance(data, dict):
        return None
    out = dict(data)
    try:
        out["cache"] = {
            **dict(out.get("cache") or {}),
            "hit": True,
            "age_seconds": int(age),
            "ttl_seconds": int(ttl),
        }
    except Exception:
        pass
    return out


def _next_draft_layer_store_cache(article_id: int, version_id: int | None, data: Dict[str, Any]) -> None:
    ttl = _next_draft_layer_cache_seconds()
    if ttl <= 0 or not isinstance(data, dict):
        return
    key = _next_draft_layer_cache_key(article_id, version_id)
    with _NEXT_DRAFT_LAYER_CACHE_LOCK:
        _NEXT_DRAFT_LAYER_CACHE[key] = {
            "stored_at": time.monotonic(),
            "data": dict(_cache_payload(data)),
        }


def _public_articles_cache_seconds() -> int:
    try:
        return max(0, int(getattr(settings, "PUBLIC_ARTICLES_CACHE_SECONDS", 30) or 0))
    except Exception:
        return 30


def _article_out_to_cache_payload(out: ArticleOut) -> Dict[str, Any]:
    if hasattr(out, "model_dump"):
        return dict(out.model_dump(mode="json"))
    if hasattr(out, "dict"):
        return dict(out.dict())
    return dict(out)


def _article_out_from_cache_payload(data: Dict[str, Any]) -> ArticleOut | None:
    if not isinstance(data, dict):
        return None
    try:
        return ArticleOut(**data)
    except Exception:
        return None


def _public_articles_list_from_cache() -> List[ArticleOut] | None:
    ttl = _public_articles_cache_seconds()
    if ttl <= 0:
        return None
    now = time.monotonic()
    with _PUBLIC_ARTICLES_CACHE_LOCK:
        row = dict(_PUBLIC_ARTICLES_LIST_CACHE or {})
    if not row:
        return None
    age = now - float(row.get("stored_at") or 0.0)
    if age < 0 or age > ttl:
        return None
    items = row.get("data")
    if not isinstance(items, list):
        return None
    out: List[ArticleOut] = []
    for item in items:
        parsed = _article_out_from_cache_payload(item)
        if parsed is None:
            return None
        out.append(parsed)
    return out


def _public_articles_store_list_cache(items: List[ArticleOut]) -> None:
    ttl = _public_articles_cache_seconds()
    if ttl <= 0:
        return
    with _PUBLIC_ARTICLES_CACHE_LOCK:
        _PUBLIC_ARTICLES_LIST_CACHE.clear()
        _PUBLIC_ARTICLES_LIST_CACHE.update({
            "stored_at": time.monotonic(),
            "data": [_article_out_to_cache_payload(x) for x in list(items or [])],
        })
        _PUBLIC_ARTICLE_DETAIL_CACHE.clear()
        for x in list(items or []):
            try:
                aid = int(getattr(x, "id", 0) or 0)
            except Exception:
                aid = 0
            if aid > 0:
                _PUBLIC_ARTICLE_DETAIL_CACHE[aid] = {
                    "stored_at": time.monotonic(),
                    "data": _article_out_to_cache_payload(x),
                }


def _public_article_detail_from_cache(article_id: int) -> ArticleOut | None:
    ttl = _public_articles_cache_seconds()
    if ttl <= 0:
        return None
    now = time.monotonic()
    with _PUBLIC_ARTICLES_CACHE_LOCK:
        row = dict(_PUBLIC_ARTICLE_DETAIL_CACHE.get(int(article_id)) or {})
    if not row:
        return None
    age = now - float(row.get("stored_at") or 0.0)
    if age < 0 or age > ttl:
        return None
    data = row.get("data")
    if not isinstance(data, dict):
        return None
    return _article_out_from_cache_payload(data)


def _public_article_store_detail_cache(article_id: int, out: ArticleOut) -> None:
    ttl = _public_articles_cache_seconds()
    if ttl <= 0:
        return
    with _PUBLIC_ARTICLES_CACHE_LOCK:
        _PUBLIC_ARTICLE_DETAIL_CACHE[int(article_id)] = {
            "stored_at": time.monotonic(),
            "data": _article_out_to_cache_payload(out),
        }


def _clear_public_runtime_caches() -> None:
    """Invalidate public runtime caches after writes. Cache invalidation is best-effort and must never break the underlying write operation."""
    _clear_admin_runtime_caches()
    try:
        with _NEXT_DRAFT_LAYER_CACHE_LOCK:
            _NEXT_DRAFT_LAYER_CACHE.clear()
    except Exception:
        pass
    try:
        with _PUBLIC_PROJECT_STATS_CACHE_LOCK:
            _PUBLIC_PROJECT_STATS_CACHE.clear()
    except Exception:
        pass
    try:
        with _PUBLIC_TRAFFIC_STATS_CACHE_LOCK:
            _PUBLIC_TRAFFIC_STATS_CACHE.clear()
    except Exception:
        pass
    try:
        with _PUBLIC_ARTICLES_CACHE_LOCK:
            _PUBLIC_ARTICLES_LIST_CACHE.clear()
            _PUBLIC_ARTICLE_DETAIL_CACHE.clear()
            _PUBLIC_ARTICLES_SSR_CACHE.clear()
    except Exception:
        pass

 
def _aggregate_votes_for_article(
    db: Session,
    article_id: int,
) -> ArticleVoteSummaryOut:
    """Aggregate raw and trust-weighted article votes plus unweighted reactions while preserving legacy raw response fields for compatibility."""
    article = db.query(Article).filter(Article.id == article_id).first()
    if not article:
        raise HTTPException(status_code=404, detail='Artikel nicht gefunden')

    # Hot read path: keep summary aggregation strictly read-only.
    # Auto-confirm performs UPDATE/COMMIT and can lock SQLite during page load.
    # It is intentionally disabled here for now.

    rows = (
        db.query(ArticleVote, User)
        .join(User, User.id == ArticleVote.user_id)
        .filter(ArticleVote.article_id == article_id)
        .filter(ArticleVote.status.in_(['confirmed', 'pending']))
        .all()
    )
    # Compatibility rule: legacy `vote.flags` is not a source for new reaction backfills.

    from collections import Counter
    def _polarity_from_counts(counts: dict[str, int]) -> tuple[int, int, int, int, float | None]:
        pos = int(counts.get("✅", 0) + counts.get("🟢", 0))
        neu = int(counts.get("🟡", 0))
        neg = int(counts.get("🟠", 0) + counts.get("🔴", 0))
        denom = int(pos + neu + neg)
        approval = (100.0 * float(pos) / float(denom)) if denom > 0 else None
        return pos, neu, neg, denom, approval

    per_version: dict[int, dict[str, object]] = {}

    # overall
    total_votes_raw = 0
    total_votes_weighted = 0
    main_counts_raw = Counter()
    main_counts_weighted = Counter()

    for v, u in rows:
        total_votes_raw += 1
        w = int(_vote_weight_for_user(u) or 0)
        if w < 0:
            w = 0
        total_votes_weighted += w

        bucket = per_version.setdefault(
            int(v.version_id),
            {
                "total_votes": 0,
                "votes_by_main": Counter(),
                "weighted_total": 0,
                "weighted_votes_by_main": Counter(),
                "reactions_count": Counter(),
            },
        )
        bucket["total_votes"] = int(bucket["total_votes"]) + 1
        bucket["weighted_total"] = int(bucket["weighted_total"]) + w

        mv = str(getattr(v, "main_vote", "") or "")
        if mv:
            bucket["votes_by_main"][mv] += 1
            bucket["weighted_votes_by_main"][mv] += w
            main_counts_raw[mv] += 1
            main_counts_weighted[mv] += w

    # Unweighted reactions per version.
    rrows = (
        db.query(ArticleReaction.version_id, ArticleReaction.reaction_emoji, func.count(ArticleReaction.id))
        .filter(ArticleReaction.article_id == article_id)
        .group_by(ArticleReaction.version_id, ArticleReaction.reaction_emoji)
        .all()
    )
    for vid, emo, cnt in rrows:
        bucket = per_version.setdefault(
            int(vid),
            {
                "total_votes": 0,
                "votes_by_main": Counter(),
                "weighted_total": 0,
                "weighted_votes_by_main": Counter(),
                "reactions_count": Counter(),
            },
        )
        es = str(emo)
        if es:
            bucket["reactions_count"][es] += int(cnt or 0)
 

    versions_out: list[dict[str, object]] = []
    for version in article.versions:
        stats = per_version.get(int(version.id))
        if not stats:
            stats = {
                "total_votes": 0,
                "votes_by_main": Counter(),
                "weighted_total": 0,
                "weighted_votes_by_main": Counter(),
                "reactions_count": Counter(),
            }
        raw_by_main = dict(stats["votes_by_main"])
        w_by_main = dict(stats["weighted_votes_by_main"])

        raw_pos, raw_neu, raw_neg, _raw_denom, raw_appr = _polarity_from_counts(raw_by_main)
        w_pos, w_neu, w_neg, _w_denom, w_appr = _polarity_from_counts(w_by_main)

        # Majority emoji from raw votes.
        maj = None
        if raw_by_main:
            maj = max(raw_by_main.items(), key=lambda kv: (int(kv[1]), kv[0]))[0]
        versions_out.append(
            {
                "version_id": int(version.id),
                "version_label": version.version_label,
                # Legacy/raw compatibility fields.
                "total_votes": int(stats["total_votes"]),
                "votes_by_main": raw_by_main,
                # Explicit B1 fields.
                "raw_positive": raw_pos,
                "raw_neutral": raw_neu,
                "raw_negative": raw_neg,
                "raw_total": int(stats["total_votes"]),
                "raw_approval_percent": raw_appr,
                "weighted_positive": w_pos,
                "weighted_neutral": w_neu,
                "weighted_negative": w_neg,
                "weighted_total": int(stats["weighted_total"]),
                "weighted_approval_percent": w_appr,
                "weighted_votes_by_main": w_by_main,
                # Reactions..
                "reactions_count": dict(stats.get("reactions_count", {})),
                "majority_emoji": maj,
            }
        )

    versions_out.sort(key=lambda v: v["version_id"])

    # Overall polarity fields.
    raw_pos, raw_neu, raw_neg, _raw_denom, raw_appr = _polarity_from_counts(dict(main_counts_raw))
    w_pos, w_neu, w_neg, _w_denom, w_appr = _polarity_from_counts(dict(main_counts_weighted))
 

    summary = {
        "article_id": int(article.id),
        # Legacy/raw compatibility fields.
        "total_votes": int(total_votes_raw),
        # Explicit B1 fields.
        "raw_positive": raw_pos,
        "raw_neutral": raw_neu,
        "raw_negative": raw_neg,
        "raw_total": int(total_votes_raw),
        "raw_approval_percent": raw_appr,
        "weighted_positive": w_pos,
        "weighted_neutral": w_neu,
        "weighted_negative": w_neg,
        "weighted_total": int(total_votes_weighted),
        "weighted_approval_percent": w_appr,
        "weighted_votes_by_main": dict(main_counts_weighted),
        "versions": versions_out,
    }
    return ArticleVoteSummaryOut(**summary)  


# --- Explicit bootstrap / maintenance helper -------------------------------



# --- API endpoints ---------------------------------------------------------


@app.get("/api/health", response_model=HealthOut)
def healthcheck() -> HealthOut:
    """Return the minimal application health response."""
    return HealthOut(status="ok")


@app.get("/api/public/traffic-stats", response_model=PublicTrafficStatsOut)
def get_public_traffic_stats(
    days: int = Query(30, ge=1, le=365, description="Zeitraum in Tagen (UTC-Tage)."),
    db: Session = Depends(get_db),
) -> PublicTrafficStatsOut:
    """Return aggregated daily traffic statistics without exposing IP addresses, user agents, or visitor keys."""
    days_int = max(1, min(int(days), 365))
    cached = _traffic_stats_from_cache("traffic", days_int)
    if cached is not None:
        try:
            return PublicTrafficStatsOut(**cached)
        except Exception:
            pass

    now = datetime.utcnow()
    start = now - timedelta(days=days_int - 1)
    start_day = date(start.year, start.month, start.day)

    dates = [(start_day + timedelta(days=i)).isoformat() for i in range(days_int)]
    idx = {d: i for i, d in enumerate(dates)}

    pv = [0] * days_int
    ve = [0] * days_int
    pv_li = [0] * days_int

    rows = (
        db.query(MetricsDaily)
        .filter(MetricsDaily.day >= start_day)
        .all()
    )
    for r in rows:
        ds = r.day.isoformat() if r.day else None
        if not ds or ds not in idx:
            continue
        i = idx[ds]
        pv[i] = int(r.pageviews or 0)
        ve[i] = int(r.visitors_est or 0)
        pv_li[i] = int(r.pageviews_logged_in or 0)

    totals = {
        "pageviews": int(sum(pv)),
        "visitors_est": int(sum(ve)),
        "pageviews_logged_in": int(sum(pv_li)),
    }

    out = PublicTrafficStatsOut(
        days=days_int,
        totals=totals,
        series={
            "dates": dates,
            "pageviews": pv,
            "visitors_est": ve,
            "pageviews_logged_in": pv_li,
        },
    )
    _traffic_stats_store_cache("traffic", days_int, _public_traffic_stats_to_cache_payload(out))
    return out


@app.get("/api/public/stats-config", response_model=dict)
def get_public_stats_config() -> dict:
    """Return the frontend configuration for the homepage statistics panel."""
    kpi_keys = list(getattr(settings, "PUBLIC_STATS_KPI_KEYS", []) or [])
    kpi_labels = dict(getattr(settings, "PUBLIC_STATS_KPI_LABELS", {}) or {})
    kpi_show_total = bool(getattr(settings, "PUBLIC_STATS_KPI_SHOW_TOTAL", True))
    kpi_show_delta = bool(getattr(settings, "PUBLIC_STATS_KPI_SHOW_DELTA", True))

    show_mood = bool(getattr(settings, "PUBLIC_STATS_SHOW_MOOD", True))

    metric_keys = list(getattr(settings, "PUBLIC_STATS_GRAPH_METRIC_KEYS", []) or [])
    metric_labels = dict(getattr(settings, "PUBLIC_STATS_GRAPH_METRIC_LABELS", {}) or {})
    include_main = bool(getattr(settings, "PUBLIC_STATS_GRAPH_INCLUDE_MAIN_VOTES", True))
    include_flags = bool(getattr(settings, "PUBLIC_STATS_GRAPH_INCLUDE_FLAGS", True))
    default_selected = list(getattr(settings, "PUBLIC_STATS_GRAPH_DEFAULT_SELECTED", []) or [])

    days_default = int(getattr(settings, "PUBLIC_STATS_DAYS_DEFAULT", 30) or 30)

    return {
        "days_default": days_default,
        "mood": {"enabled": show_mood},
        "kpis": {
            "keys": kpi_keys,
            "labels": kpi_labels,
            "show_total": kpi_show_total,
            "show_delta": kpi_show_delta,
        },
        "graph": {
            "metric_keys": metric_keys,
            "metric_labels": metric_labels,
            "include_main_votes": include_main,
            "include_flags": include_flags,
            "default_selected": default_selected,
        },
    }


@app.get("/api/public/metrics/daily")
def get_public_metrics_daily(days: int = 30, db: Session = Depends(get_db)):
    days = int(days or 30)
    if days < 1:
        days = 1
    if days > 365:
        days = 365

    cached = _traffic_stats_from_cache("metrics_daily", days)
    if cached is not None:
        return cached

    # UTC-based day buckets.
    end_day = date.today()
    start_day = end_day - timedelta(days=days - 1)

    rows = (
        db.query(MetricsDaily)
        .filter(MetricsDaily.day >= start_day, MetricsDaily.day <= end_day)
        .order_by(MetricsDaily.day.asc())
        .all()
    )

    by_day = {str(r.day): r for r in rows}
    series = []
    for i in range(days):
        d = start_day + timedelta(days=i)
        key = str(d)
        r = by_day.get(key)
        series.append(
            {
                "day": key,
                "pageviews": int(getattr(r, "pageviews", 0) or 0) if r else 0,
                "pageviews_logged_in": int(getattr(r, "pageviews_logged_in", 0) or 0) if r else 0,
                "visitors_est": int(getattr(r, "visitors_est", 0) or 0) if r else 0,
            }
        )

    out = {"days": days, "series": series}
    _traffic_stats_store_cache("metrics_daily", days, out)
    return out


@app.get("/api/public/project-stats", response_model=PublicProjectStatsOut)
def get_public_project_stats(
    days: int = Query(30, ge=1, le=365, description="Zeitraum in Tagen (für Aktivität/Verlauf)."),
    db: Session = Depends(get_db),
) -> PublicProjectStatsOut:
    """Return aggregated public project statistics, deltas, sentiment, and daily series. The request path is read-only; due vote confirmation runs separately in the background."""
    days_int = int(days)
    cached = _public_project_stats_from_cache(days_int)
    if cached is not None:
        return cached

    now = datetime.utcnow()

    # Selected time range.
    start_day = now.date() - timedelta(days=int(days) - 1)
    start_dt = datetime.combine(start_day, datetime.min.time())

    # --- Delta over the selected recent period ---
    delta_votes = (
        db.query(func.count(ArticleVote.id))
        .filter(ArticleVote.status == "confirmed", ArticleVote.created_at >= start_dt)
        .scalar()
        or 0
    )
    delta_users = db.query(func.count(User.id)).filter(User.created_at >= start_dt).scalar() or 0
    delta_comments = db.query(func.count(Comment.id)).filter(Comment.created_at >= start_dt).scalar() or 0
    delta_reviews = db.query(func.count(Review.id)).filter(Review.created_at >= start_dt).scalar() or 0

    # --- Lifetime totals ---
    total_votes = (
        db.query(func.count(ArticleVote.id))
        .filter(ArticleVote.status == "confirmed")
        .scalar()
        or 0
    )
    total_users = db.query(func.count(User.id)).scalar() or 0
    total_comments = db.query(func.count(Comment.id)).scalar() or 0
    total_reviews = db.query(func.count(Review.id)).scalar() or 0

    # --- Sentiment over the selected recent period ---
    mood_rows_period = (
        db.query(ArticleVote.main_vote, func.count(ArticleVote.id))
        .filter(ArticleVote.status == "confirmed", ArticleVote.created_at >= start_dt)
        .group_by(ArticleVote.main_vote)
        .all()
    )
    mood_by_main_period = {"✅": 0, "🟢": 0, "🟡": 0, "🟠": 0, "🔴": 0}
    for mv, cnt in mood_rows_period:
        if mv in mood_by_main_period:
            mood_by_main_period[mv] = int(cnt or 0)
    mood_total_period = int(sum(mood_by_main_period.values()))
    approve_period = (mood_by_main_period.get("✅", 0) + mood_by_main_period.get("🟢", 0))
    approval_percent_period = (100.0 * approve_period / mood_total_period) if mood_total_period > 0 else 0.0

    # --- Lifetime sentiment ---
    mood_rows_total = (
        db.query(ArticleVote.main_vote, func.count(ArticleVote.id))
        .filter(ArticleVote.status == "confirmed")
        .group_by(ArticleVote.main_vote)
        .all()
    )
    mood_by_main_total = {"✅": 0, "🟢": 0, "🟡": 0, "🟠": 0, "🔴": 0}
    for mv, cnt in mood_rows_total:
        if mv in mood_by_main_total:
            mood_by_main_total[mv] = int(cnt or 0)
    mood_total_total = int(sum(mood_by_main_total.values()))
    approve_total = (mood_by_main_total.get("✅", 0) + mood_by_main_total.get("🟢", 0))
    approval_percent_total = (100.0 * approve_total / mood_total_total) if mood_total_total > 0 else 0.0

    # --- Daily series ---
    dates = [(start_day + timedelta(days=i)).isoformat() for i in range(int(days))]
    idx = {d: i for i, d in enumerate(dates)}

    # Main votes series
    main_keys = ["✅", "🟢", "🟡", "🟠", "🔴"]
    series_main = {k: [0] * int(days) for k in main_keys}

    dcol = func.date(ArticleVote.created_at)
    rows_main = (
        db.query(dcol.label("d"), ArticleVote.main_vote, func.count(ArticleVote.id))
        .filter(ArticleVote.status == "confirmed", ArticleVote.created_at >= start_dt)
        .group_by(dcol, ArticleVote.main_vote)
        .all()
    )
    for d, mv, cnt in rows_main:
        ds = str(d)
        if ds in idx and mv in series_main:
            series_main[mv][idx[ds]] = int(cnt or 0)

    # Public flag series includes quality markers only; the bookmark marker remains excluded.
    flag_keys = ["🧭", "✍️", "🧩", "⚖️"]
    series_flags = {k: [0] * int(days) for k in flag_keys}

    # Legacy v0.9 rows may still store flags as a JSON list on `ArticleVote`.
    # Aggregate in Python to keep this path database-agnostic across SQLite/PostgreSQL.
    rows_flags = (
        db.query(dcol.label("d"), ArticleVote.flags)
        .filter(
            ArticleVote.status == "confirmed",
            ArticleVote.created_at >= start_dt,
            ArticleVote.flags.isnot(None),
        )
        .all()
    )
    for d, fl in rows_flags:
        ds = str(d)
        if ds not in idx:
            continue
        if not fl:
            continue
        # Accept malformed legacy flag shapes defensively when reading historical rows.
        if isinstance(fl, str):
            fl_iter = [fl]
        elif isinstance(fl, (list, tuple)):
            fl_iter = fl
        else:
            continue
        for fv in fl_iter:
            if fv in series_flags:
                series_flags[fv][idx[ds]] += 1

    # Metric series assembled dynamically.
    metric_keys = set(getattr(settings, "PUBLIC_STATS_GRAPH_METRIC_KEYS", []) or [])
    metric_keys.update({"votes_total", "users", "comments", "reviews"})
    series_metrics = {k: [0] * int(days) for k in metric_keys}

    # `votes_total` is the sum of all main votes for each day.
    if "votes_total" in series_metrics:
        for i in range(int(days)):
            series_metrics["votes_total"][i] = int(sum(series_main[k][i] for k in main_keys))

    def _fill_metric(model_cls, created_col, key: str) -> None:
        if key not in series_metrics:
            return
        dcol_any = func.date(created_col)
        mrows = (
            db.query(dcol_any.label("d"), func.count(model_cls.id))
            .filter(created_col >= start_dt)
            .group_by(dcol_any)
            .all()
        )
        for d, cnt in mrows:
            ds = str(d)
            if ds in idx:
                series_metrics[key][idx[ds]] = int(cnt or 0)

    _fill_metric(User, User.created_at, "users")
    _fill_metric(Comment, Comment.created_at, "comments")
    _fill_metric(Review, Review.created_at, "reviews")

    # Add traffic metrics when `metrics_daily` data is available.
    if (
        "pageviews" in series_metrics
        or "pageviews_logged_in" in series_metrics
        or "visitors_est" in series_metrics
    ):
        md_rows = (
            db.query(MetricsDaily)
            .filter(MetricsDaily.day >= start_day)
            .all()
        )
        for r in md_rows:
            ds = r.day.isoformat() if getattr(r, "day", None) else ""
            if ds in idx:
                if "pageviews" in series_metrics:
                    series_metrics["pageviews"][idx[ds]] = int(getattr(r, "pageviews", 0) or 0)
                if "pageviews_logged_in" in series_metrics:
                    series_metrics["pageviews_logged_in"][idx[ds]] = int(getattr(r, "pageviews_logged_in", 0) or 0)
                if "visitors_est" in series_metrics:
                    series_metrics["visitors_est"][idx[ds]] = int(getattr(r, "visitors_est", 0) or 0)

    out = PublicProjectStatsOut(
        days=days_int,
        totals={
            "votes": int(total_votes),
            "users": int(total_users),
            "comments": int(total_comments),
            "reviews": int(total_reviews),
        },
        delta={"votes": int(delta_votes), "users": int(delta_users), "comments": int(delta_comments), "reviews": int(delta_reviews)},
        mood={"total": mood_total_period, "by_main": mood_by_main_period, "approval_percent": float(approval_percent_period)},
        mood_total={"total": mood_total_total, "by_main": mood_by_main_total, "approval_percent": float(approval_percent_total)},
        series={"dates": dates, "main": series_main, "flags": series_flags, "metrics": series_metrics},
    )
    _public_project_stats_store_cache(days_int, out)
    return out


@app.get("/api/pseudonym/pools", response_model=dict)
def list_pseudonym_pools() -> dict:
    pools = []
    for pool_id, cfg in PSEUDONYM_POOLS.items():
        pools.append(
            {
                "id": pool_id,
                "label": str(cfg.get("label", pool_id)),
                "description": str(cfg.get("description", "")),
            }
        )
    pools.sort(key=lambda p: p["id"])
    return {"pools": pools}


@app.get("/api/pseudonym/suggestions", response_model=dict)
def get_pseudonym_suggestions(
    pool: str = Query("neutral", description="Pool-ID"),
    n: int = Query(10, ge=1, le=30, description="Anzahl Vorschläge"),
    db: Session = Depends(get_db),
) -> dict:
    suggestions = generate_pseudonym_suggestions(pool_id=pool, n=n, db=db)
    return {"pool": pool, "suggestions": suggestions}
 

@app.get("/api/articles", response_model=List[ArticleOut])
def list_articles(db: Session = Depends(get_db)) -> List[ArticleOut]:
    """Return all publicly visible articles with the current version and vote/reaction aggregates."""
    cached = _public_articles_list_from_cache()
    if cached is not None:
        return cached

    articles = db.query(Article).order_by(*_article_ordering()).all()
    public_articles = []
    for art in articles:
        if _article_public_visibility_status(db, art) != "hidden":
            public_articles.append(art)
    out = [_build_article_out(db, art) for art in public_articles]
    _public_articles_store_list_cache(out)
    return out
 

def _build_article_out(db: Session, art: Article) -> ArticleOut:
    """Erzeugt ein ArticleOut inkl. vote_summary + majority_emoji."""
    out = ArticleOut.model_validate(art)
    out.display_label = _article_display_label(art)
    out.toc_group_label = _article_toc_group_label(art)
    visibility = _article_public_visibility_dict(db, art)
    out.visibility_status = str(visibility.get("visibility_status") or "hidden")
    out.visibility_reason = visibility.get("visibility_reason")
    out.public_comment_ids = list(visibility.get("public_comment_ids") or [])

    # API contract (FEATURES_v09/tests): selected content blocks are always present,
    # Keep response defaults stable for older imported/database rows with missing keys.
    if out.current_version and out.current_version.content_blocks is not None:
        blocks = dict(out.current_version.content_blocks or {})
        for key in ("kurzinfo", "story", "einleitung", "juristisch", "anmerkung"):
            blocks.setdefault(key, "")
        # Optional blocks for ordinances, annexes, and similar content.
        blocks.setdefault("juristisch2", blocks.get("juristisch2", ""))
        out.current_version.content_blocks = blocks

    summary = _aggregate_votes_for_article(db, art.id)
    out.vote_summary = summary

    
    majority_emoji: str | None = None
    current_version_id = getattr(art, "current_version_id", None)
    if current_version_id is not None and summary.versions:
        version_stats = next(
            (v for v in summary.versions if v.version_id == current_version_id),
            None,
        )
        if version_stats and version_stats.votes_by_main:
            majority_emoji = max(
                version_stats.votes_by_main.items(),
                key=lambda kv: kv[1],
            )[0]
    out.majority_emoji = majority_emoji
    return out


def _html_unified_diff(a: str, b: str, *, from_label: str, to_label: str) -> str:
    """Build a minimal escaped HTML unified diff inside a `<pre>` element."""
    a_lines = (a or "").splitlines()
    b_lines = (b or "").splitlines()
    diff_lines = difflib.unified_diff(
        a_lines,
        b_lines,
        fromfile=from_label,
        tofile=to_label,
        lineterm="",
    )
    diff_text = "\n".join(diff_lines)
    return f"<pre class=\"diff\">{html.escape(diff_text)}</pre>"


@app.get("/api/articles/{article_id}", response_model=ArticleOut)
def get_article(article_id: int, db: Session = Depends(get_db)) -> ArticleOut:
    """Return one article with its current version and aggregate vote state."""
    cached = _public_article_detail_from_cache(int(article_id))
    if cached is not None:
        return cached

    art = db.query(Article).filter(Article.id == article_id).one_or_none()
    if art is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Artikel nicht gefunden")
    if _article_public_visibility_status(db, art) == "hidden":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Artikel nicht gefunden")
    out = _build_article_out(db, art)
    _public_article_store_detail_cache(int(article_id), out)
    return out


@app.get("/api/articles/{article_id}/versions", response_model=ArticleVersionsOut)
def get_article_versions(article_id: int, db: Session = Depends(get_db)) -> ArticleVersionsOut:
    """Return metadata for all versions of an article."""
    art = db.query(Article).filter(Article.id == article_id).one_or_none()
    if art is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Artikel nicht gefunden")

    versions = [ArticleVersionMetaOut.model_validate(v) for v in art.versions]
    return ArticleVersionsOut(
        article_id=art.id,
        current_version_id=art.current_version_id,
        versions=versions,
    )


@app.get("/api/articles/{article_id}/diff", response_model=ArticleDiffOut)
def get_article_diff(
    article_id: int,
    from_version_id: int = Query(..., alias="from"),
    to_version_id: int = Query(..., alias="to"),
    db: Session = Depends(get_db),
) -> ArticleDiffOut:
    """Return a block-wise diff between two versions of an article."""
    art = db.query(Article).filter(Article.id == article_id).one_or_none()
    if art is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Artikel nicht gefunden")

    v_from = (
        db.query(ArticleVersion)
        .filter(ArticleVersion.id == from_version_id, ArticleVersion.article_id == article_id)
        .one_or_none()
    )
    v_to = (
        db.query(ArticleVersion)
        .filter(ArticleVersion.id == to_version_id, ArticleVersion.article_id == article_id)
        .one_or_none()
    )
    if v_from is None or v_to is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Version(en) nicht gefunden")

    blocks_from = v_from.content_blocks or {}
    blocks_to = v_to.content_blocks or {}
    keys = sorted(set(blocks_from.keys()) | set(blocks_to.keys()))

    diff_blocks: dict[str, str] = {}
    for key in keys:
        a = str(blocks_from.get(key, ""))
        b = str(blocks_to.get(key, ""))
        diff_blocks[key] = _html_unified_diff(
            a,
            b,
            from_label=f"v{from_version_id}:{key}",
            to_label=f"v{to_version_id}:{key}",
        )

    return ArticleDiffOut(
        article_id=art.id,
        from_version_id=from_version_id,
        to_version_id=to_version_id,
        diff_blocks=diff_blocks,
    )


    
    
# ---------------------------------------------------------------------------
# Admin dashboard, exports, and editorial operations
# ---------------------------------------------------------------------------

_CURRENT_TOPICS_KEY = "current_topics"
_CURRENT_TOPICS_MAX_ITEMS = 6


def _normalize_current_topics_items(raw_items: Any, *, strict: bool) -> List[Dict[str, str]]:
    if raw_items is None:
        raw_items = []
    if not isinstance(raw_items, list):
        if strict:
            raise HTTPException(status_code=400, detail="items must be a list")
        return []
    if strict and len(raw_items) > _CURRENT_TOPICS_MAX_ITEMS:
        raise HTTPException(status_code=400, detail=f"max {_CURRENT_TOPICS_MAX_ITEMS} topics allowed")

    out: List[Dict[str, str]] = []
    for raw in raw_items[:_CURRENT_TOPICS_MAX_ITEMS]:
        if not isinstance(raw, dict):
            if strict:
                raise HTTPException(status_code=400, detail="each topic must be an object")
            continue

        title = str(raw.get("title") or "").strip()
        text_value = str(raw.get("text") or "").strip()
        link_url = str(raw.get("link_url") or "").strip()
        link_label = str(raw.get("link_label") or "").strip()

        if not title and not text_value and not link_url and not link_label:
            continue
        if not title or not text_value:
            if strict:
                raise HTTPException(status_code=400, detail="title and text are required for every topic")
            continue
        if len(title) > 120 or len(text_value) > 700 or len(link_url) > 300 or len(link_label) > 80:
            if strict:
                raise HTTPException(status_code=400, detail="topic field too long")
            continue

        if link_url:
            valid_relative = (link_url.startswith("/") and not link_url.startswith("//")) or link_url.startswith("#")
            parsed = urlparse(link_url)
            valid_http = parsed.scheme in {"http", "https"} and bool(parsed.netloc)
            if not (valid_relative or valid_http):
                if strict:
                    raise HTTPException(status_code=400, detail="link_url must be relative or http(s)")
                link_url = ""
                link_label = ""
            elif not link_label:
                link_label = "Mehr erfahren"
        else:
            link_label = ""

        out.append({
            "title": title,
            "text": text_value,
            "link_url": link_url,
            "link_label": link_label,
        })
    return out


def _current_topics_state(db: Session) -> Dict[str, Any]:
    try:
        row = db.query(SystemConfig).filter(SystemConfig.key == _CURRENT_TOPICS_KEY).one_or_none()
    except Exception:
        return {"items": [], "version": 0, "updated_at": None, "updated_at_label": None}

    if row is None:
        return {"items": [], "version": 0, "updated_at": None, "updated_at_label": None}

    raw = row.value_json if isinstance(row.value_json, dict) else {}
    items = _normalize_current_topics_items(raw.get("items"), strict=False)
    updated_at = getattr(row, "updated_at", None)
    return {
        "items": items,
        "version": int(getattr(row, "version", 0) or 0),
        "updated_at": updated_at.isoformat(timespec="seconds") if isinstance(updated_at, datetime) else None,
        "updated_at_label": updated_at.strftime("%d.%m.%Y") if isinstance(updated_at, datetime) else None,
    }


def _save_current_topics_state(db: Session, payload: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="invalid payload")
    items = _normalize_current_topics_items(payload.get("items"), strict=True)

    row = db.query(SystemConfig).filter(SystemConfig.key == _CURRENT_TOPICS_KEY).one_or_none()
    expected_version = payload.get("version")
    if expected_version is not None:
        try:
            expected_version = int(expected_version)
        except Exception:
            raise HTTPException(status_code=400, detail="invalid version")

    if row is None:
        if expected_version not in (None, 0):
            raise HTTPException(status_code=409, detail="current_topics changed; reload first")
        row = SystemConfig(
            key=_CURRENT_TOPICS_KEY,
            value_json={"items": items},
            version=1,
            updated_at=datetime.utcnow(),
        )
        db.add(row)
    else:
        current_version = int(getattr(row, "version", 0) or 0)
        if expected_version is not None and expected_version != current_version:
            raise HTTPException(status_code=409, detail="current_topics changed; reload first")
        row.value_json = {"items": items}
        row.version = current_version + 1
        row.updated_at = datetime.utcnow()
        db.add(row)

    _commit_db(db)
    db.refresh(row)
    return _current_topics_state(db)


def _as_relpath(pth: str) -> str:
    try:
        rp = Path(pth).resolve().relative_to(BASE_DIR.resolve())
        return str(rp).replace("\\", "/")
    except Exception:
        return str(pth)


@app.get("/api/admin/stats", response_model=AdminStatsOut)
def admin_stats(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)) -> AdminStatsOut:
    require_admin(current_user)
    cached = _admin_runtime_cache_get("stats")
    if cached is not None:
        return AdminStatsOut(**cached)

    users_total = int(db.query(func.count(User.id)).scalar() or 0)
    articles_total = int(db.query(func.count(Article.id)).scalar() or 0)
    article_versions_total = int(db.query(func.count(ArticleVersion.id)).scalar() or 0)
    votes_total = int(db.query(func.count(ArticleVote.id)).scalar() or 0)
    votes_confirmed_total = int(db.query(func.count(ArticleVote.id)).filter(ArticleVote.status == "confirmed").scalar() or 0)
    votes_pending_total = int(db.query(func.count(ArticleVote.id)).filter(ArticleVote.status == "pending").scalar() or 0)
    comments_total = int(db.query(func.count(Comment.id)).scalar() or 0)
    comments_by_status_rows = db.query(Comment.status, func.count(Comment.id)).group_by(Comment.status).all()
    comments_by_status = {str(st): int(cnt) for st, cnt in comments_by_status_rows if st}
    reviews_total = int(db.query(func.count(Review.id)).scalar() or 0)
    out = AdminStatsOut(
        users_total=users_total,
        articles_total=articles_total,
        article_versions_total=article_versions_total,
        votes_total=votes_total,
        votes_confirmed_total=votes_confirmed_total,
        votes_pending_total=votes_pending_total,
        comments_total=comments_total,
        comments_by_status=comments_by_status,
        reviews_total=reviews_total,
    )
    _admin_runtime_cache_set("stats", _cache_payload(out))
    return out




def _publish_comment_inplace(comment: Comment, actor: User) -> None:
    """Publish a comment in place. The operation is idempotent for already-published comments, rejects terminal states, and keeps `new_article` content as a draft shell exposed through its published comment."""
    st = str(getattr(comment, "status", "") or "").strip().lower()
    if st in ("veröffentlicht", "veroeffentlicht"):
        return
    if st in ("archiviert", "integriert", "gelöscht", "geloescht"):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Kommentar kann nicht veröffentlicht werden")

    comment.status = "veröffentlicht"
    comment.lifecycle_status = "published"
    comment.published_at = datetime.utcnow()

    # Initialize the public author label from the pseudonym when no explicit label exists.
    try:
        if not (getattr(comment, "public_author_label", None) or "").strip():
            comment.public_author_label = (getattr(actor, "pseudonym", None) or "").strip() or "Admin"
    except Exception:
        pass


@app.post("/api/admin/comments/{comment_id}/publish", response_model=CommentOut)
def admin_publish_comment_startphase(
    comment_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> CommentOut:
    """Start-phase admin action that publishes a review comment under the deliberately narrow early-user rule."""
    require_admin(current_user)

    users_total = int(
        db.query(func.count(User.id))
        .filter(User.is_deleted == False)  # noqa: E712
        .scalar()
        or 0
    )
    if users_total >= 12:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Admin-Veröffentlichung ist nur in der Startphase mit weniger als 12 Nutzern möglich",
        )

    comment = db.query(Comment).filter(Comment.id == comment_id).one_or_none()
    if comment is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Kommentar nicht gefunden")

    if review_code.status_key(getattr(comment, "status", "")) != "review":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Nur Kommentare im Review können so veröffentlicht werden")

    rollup = _review_rollup(db, int(comment.id))
    positive_reviews = int((rollup or {}).get("approve") or 0)
    if positive_reviews < 1:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Mindestens ein positives Review ist erforderlich")

    _sync_comment_diff_state_from_indiff(db, comment)
    _publish_comment_inplace(comment, current_user)
    comment.policy_status = "admin_startphase"
    comment.candidate_status = "admin_published_startphase"
    comment.candidate_reasons = {
        **(comment.candidate_reasons or {}),
        "admin_startphase_publish": {
            "at": _utc_now_iso(),
            "admin_user_id": int(current_user.id),
            "users_total": int(users_total),
            "positive_reviews": int(positive_reviews),
            "review_count": int((rollup or {}).get("total") or 0),
        },
    }

    db.add(comment)
    _commit_db(db)
    db.refresh(comment)
    _sync_comment_diff_state_from_indiff(db, comment)
    _clear_public_runtime_caches()
    return _comment_out(comment)



def _git_current_ref(cwd: Path) -> str:
    """Return the current local Git ref, preferring an exact tag and falling back to `git describe` or `unknown`."""
    return _git_describe_ref(cwd, fallback="unknown")



@app.get("/api/admin/web/versions", response_model=AdminWebVersionsOut)
def admin_web_versions(
    force: int = Query(default=0, ge=0, le=1),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> AdminWebVersionsOut:
    require_admin(current_user)
    current_ref = _git_current_ref(BASE_DIR)
    state = _read_admin_web_state()
    stable_refs = set(state.get("stable_refs") or [])
    gh = _fetch_github_versions(force=bool(force))
    raw_items = gh.get("items") or []
    avail: List[Dict[str, Any]] = []
    for it in raw_items:
        if not isinstance(it, dict):
            continue
        ref = str(it.get("ref") or "").strip()
        if not ref:
            continue
        avail.append({"ref": ref, "sha": it.get("sha"), "is_stable": ref in stable_refs})
    latest_ref = avail[0]["ref"] if avail else None
    update_available = bool(latest_ref and latest_ref != current_ref)
    return AdminWebVersionsOut(
        current_version=current_ref,
        current_ref=current_ref,
        current_is_stable=current_ref in stable_refs,
        latest_ref=latest_ref,
        update_available=update_available,
        available_versions=avail,
        last_checked_at=gh.get("last_checked_at"),
        cache_age_sec=gh.get("cache_age_sec"),
        source=gh.get("source"),
    )


@app.post("/api/admin/web/stable")
def admin_web_set_stable(
    payload: AdminWebStableRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    require_admin(current_user)
    current_ref = _git_current_ref(BASE_DIR)
    state = _read_admin_web_state()
    stable_refs = set(state.get("stable_refs") or [])
    if payload.stable:
        stable_refs.add(current_ref)
    else:
        stable_refs.discard(current_ref)
    _write_admin_web_state({"stable_refs": sorted(stable_refs)})
    # Best-effort: mirror stable flag into deploy history file (human-readable audit).
    try:
        _record_deploy_stable_toggle(current_ref, bool(payload.stable))
    except Exception:
        # Admin-side process operations are best-effort; failures should remain operationally visible.
        pass
    return {"ok": True, "current_ref": current_ref, "stable": bool(payload.stable)}

def _delayed_exit(delay_sec: float = 0.8) -> None:
    # An API-triggered process exit is intentional admin-operations behavior,
    # but it remains high-risk and requires UI confirmation, audit logging, and rate-limit review.
    try:
        time.sleep(max(0.1, float(delay_sec)))
    finally:
        os._exit(0)

@app.post("/api/admin/web/upgrade", response_model=AdminWebUpgradeOut, status_code=status.HTTP_202_ACCEPTED)
def admin_web_upgrade(
    payload: AdminWebUpgradeRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> AdminWebUpgradeOut:
    require_admin(current_user)
    ref = (payload.ref or "").strip()
    if not ref:
        raise HTTPException(status_code=400, detail="ref required")
    gh = _fetch_github_versions(force=True)
    known = {str(it.get("ref")) for it in (gh.get("items") or []) if isinstance(it, dict) and it.get("ref")}
    if not bool(gh.get("fetch_ok")):
        raise HTTPException(status_code=503, detail="remote tags could not be verified")
    if not known or ref not in known:
        raise HTTPException(status_code=400, detail="unknown ref (not in remote tags)")

    git_status_code, git_status_out, git_status_err = _run_git(
        ["status", "--porcelain", "--untracked-files=no"],
        timeout=8,
    )
    if git_status_code != 0:
        raise HTTPException(
            status_code=503,
            detail=f"git working tree could not be checked: {(git_status_err or git_status_out).strip()}",
        )
    if git_status_out.strip():
        raise HTTPException(
            status_code=409,
            detail="tracked local changes prevent web update; commit or revert them first",
        )

    # Persist desired ref for systemd ExecStartPre (git checkout happens on restart).
    current_ref = _git_current_ref(BASE_DIR)
    state = _read_admin_web_state()
    stable_refs = set(state.get("stable_refs") or [])
    try:
        _record_deploy_upgrade(current_ref, ref, new_is_stable=(ref in stable_refs))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"deploy_ref write failed: {e}")
 

    # Schedule the restart only after the HTTP response has been safely returned.
    # Otherwise the admin frontend may observe the expected nginx 502 during restart
    # as an upgrade failure even though the deploy ref was already persisted.
    background_tasks.add_task(_delayed_exit, 1.6)
    return AdminWebUpgradeOut(ok=True, ref=ref, detail="deploy_ref updated — restart scheduled")


@app.get("/api/admin/exports", response_model=List[AdminExportMetaOut])
def admin_list_exports(limit: int = Query(default=10, ge=1, le=50), db: Session = Depends(get_db), current_user: User = Depends(get_current_user)) -> List[AdminExportMetaOut]:
    """List snapshot HTML files that actually exist on disk; snapshot files on disk are the source of truth for export runs."""
    require_admin(current_user)
    rows = export_snapshot.list_snapshot_exports(base_dir=BASE_DIR, limit=int(limit))
    out: List[AdminExportMetaOut] = []
    for idx, row in enumerate(rows, start=1):
        rel_paths = dict(row.get("relative_paths") or {})
        download_urls = {
            str(k): "/api/admin/files?path=" + quote(str(v))
            for k, v in rel_paths.items()
            if v
        }
        out.append(
            AdminExportMetaOut(
                id=int(idx),
                report_type=str(row.get("report_type") or ""),
                period_start=row.get("period_start"),
                period_end=row.get("period_end"),
                generated_at=row.get("generated_at"),
                file_names=dict(row.get("file_names") or {}),
                download_urls=download_urls,
            )
        )
    return out


@app.post("/api/admin/exports/run", response_model=AdminExportRunOut)
def admin_run_export(mode: str = Query(default="release"), db: Session = Depends(get_db), current_user: User = Depends(get_current_user)) -> AdminExportRunOut:
    require_admin(current_user)
    m = (mode or "").strip().lower()
    if m not in {"release", "monthly"}:
        raise HTTPException(status_code=400, detail="mode must be release or monthly")
    if m == "release":
        paths = export_snapshot.export_snapshot_release()
    else:
        paths = export_snapshot.export_snapshot_monthly()
    rel_paths: dict[str, str] = {k: _as_relpath(v) for k, v in (paths or {}).items() if v}
    return AdminExportRunOut(mode=m, paths=rel_paths)


@app.get("/api/admin/files")
def admin_download_file(path: str = Query(...), db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    require_admin(current_user)
    raw = (path or "").strip().lstrip("/")
    if not raw:
        raise HTTPException(status_code=400, detail="path required")
    if raw.startswith("..") or "/../" in raw or "\\..\\" in raw:
        raise HTTPException(status_code=400, detail="invalid path")
    full = (BASE_DIR / raw).resolve()
    base = (BASE_DIR / "versions").resolve()
    if base != full and base not in full.parents:
        raise HTTPException(status_code=403, detail="forbidden")
    if not full.is_file():
        raise HTTPException(status_code=404, detail="file not found")
    # Public exports never contain database copies. Legacy HTML/JSON/PDF/TeX may remain readable,
    # but database files are never served.
    allowed_ext = {".json", ".html", ".pdf", ".tex"}
    if full.suffix.lower() not in allowed_ext:
        raise HTTPException(status_code=403, detail="filetype not allowed")
    return FileResponse(path=full, filename=full.name)


@app.get("/api/admin/current-topics", response_model=dict)
def admin_get_current_topics(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Dict[str, Any]:
    require_admin(current_user)
    return _current_topics_state(db)


@app.put("/api/admin/current-topics", response_model=dict)
def admin_put_current_topics(
    payload: Dict[str, Any],
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Dict[str, Any]:
    require_admin(current_user)
    return _save_current_topics_state(db, payload)


# ---------------------------------------------------------------------------
# Admin overview helpers (dashboard lists)
# ---------------------------------------------------------------------------

def _mask_email(email: str | None) -> str | None:
    if not email:
        return None
    e = str(email).strip()
    if "@" not in e:
        return e[:2] + "***"
    name, dom = e.split("@", 1)
    if len(name) <= 2:
        masked = name[:1] + "***"
    else:
        masked = name[:2] + "***" + name[-1:]
    return masked + "@" + dom


def _mask_plz(plz: str | None) -> str | None:
    if not plz:
        return None
    p = re.sub(r"\s+", "", str(plz))
    if len(p) <= 2:
        return p + "***"
    return p[:2] + "***"




def _admin_review_monitoring_by_user(db: Session) -> Dict[int, Dict[str, Any]]:
    """Aggregate review behavior per user for admin diagnostics only; it does not make sanctions or review-gate decisions."""
    rows = (
        db.query(Review, Comment)
        .join(Comment, Comment.id == Review.comment_id)
        .all()
    )
    by_user: Dict[int, Dict[str, Any]] = {}
    by_comment: Dict[int, List[Dict[str, Any]]] = {}

    for review, comment in rows:
        uid = int(getattr(review, "reviewer_id", 0) or 0)
        cid = int(getattr(review, "comment_id", 0) or 0)
        if uid <= 0 or cid <= 0:
            continue
        try:
            normalized = review_code.normalize_review_from_model(review)
            effect = review_code.classify_review_effect(normalized)
        except Exception:
            continue

        bucket = by_user.setdefault(
            uid,
            {
                "accept": 0,
                "revise": 0,
                "reject": 0,
                "report": 0,
                "negative": 0,
                "alignment_total": 0,
                "alignment_hits": 0,
                "slider_deviation_sum": 0.0,
                "slider_deviation_count": 0,
            },
        )

        decision = str(normalized.decision or "").strip().lower()
        reported = bool(getattr(normalized, "report_triggered", False) or effect.reported or effect.critical)
        if reported:
            bucket["report"] += 1
            bucket["negative"] += 1
        elif decision == review_code.DECISION_ACCEPT:
            bucket["accept"] += 1
        elif decision == review_code.DECISION_REJECT:
            bucket["reject"] += 1
            bucket["negative"] += 1
        elif decision == review_code.DECISION_CORRECT:
            bucket["revise"] += 1
            bucket["negative"] += 1

        by_comment.setdefault(cid, []).append(
            {
                "user_id": uid,
                "decision": decision,
                "reported": reported,
                "sliders": dict(normalized.sliders or {}),
                "comment_status": str(getattr(comment, "status", "") or "").strip().lower(),
                "candidate_status": str(getattr(comment, "candidate_status", "") or "").strip().lower(),
                "policy_status": str(getattr(comment, "policy_status", "") or "").strip().lower(),
            }
        )

    for cid, items in by_comment.items():
        if not items:
            continue
        sample = items[0]
        c_status = str(sample.get("comment_status") or "")
        c_candidate = str(sample.get("candidate_status") or "")
        c_policy = str(sample.get("policy_status") or "")
        outcome: str | None = None
        if c_status in {"veröffentlicht", "veroeffentlicht", "published"} or c_candidate == "accepted":
            outcome = "positive"
        elif c_status in {"abgelehnt", "rejected"} or c_candidate in {"rejected", "revision_requested", "moderation_needed", "admin_warning"} or c_policy == "admin_warning":
            outcome = "negative"

        if outcome:
            for item in items:
                uid = int(item.get("user_id") or 0)
                bucket = by_user.get(uid)
                if not bucket:
                    continue
                decision = str(item.get("decision") or "")
                reported = bool(item.get("reported"))
                review_side = "positive" if decision == review_code.DECISION_ACCEPT and not reported else "negative"
                bucket["alignment_total"] += 1
                if review_side == outcome:
                    bucket["alignment_hits"] += 1

        if len(items) >= 2:
            means: Dict[str, float] = {}
            for key in review_code.SLIDER_KEYS:
                values = []
                for item in items:
                    try:
                        values.append(float((item.get("sliders") or {}).get(key, 0)))
                    except Exception:
                        pass
                if values:
                    means[key] = sum(values) / float(len(values))
            for item in items:
                uid = int(item.get("user_id") or 0)
                bucket = by_user.get(uid)
                if not bucket:
                    continue
                diffs = []
                for key, mean in means.items():
                    try:
                        diffs.append(abs(float((item.get("sliders") or {}).get(key, 0)) - float(mean)))
                    except Exception:
                        pass
                if diffs:
                    bucket["slider_deviation_sum"] += sum(diffs) / float(len(diffs))
                    bucket["slider_deviation_count"] += 1

    out: Dict[int, Dict[str, Any]] = {}
    for uid, b in by_user.items():
        total = int(b.get("accept", 0) + b.get("revise", 0) + b.get("reject", 0) + b.get("report", 0))
        negative = int(b.get("negative", 0) or 0)
        neg_ratio = round(float(negative) / float(total), 3) if total > 0 else None
        align_total = int(b.get("alignment_total", 0) or 0)
        align_hits = int(b.get("alignment_hits", 0) or 0)
        align_rate = round(float(align_hits) / float(align_total), 3) if align_total > 0 else None
        dev_count = int(b.get("slider_deviation_count", 0) or 0)
        dev_avg = round(float(b.get("slider_deviation_sum", 0.0)) / float(dev_count), 2) if dev_count > 0 else None

        reasons: List[str] = []
        flag = "ok"
        if total >= 8 and neg_ratio is not None and neg_ratio >= 0.85:
            reasons.append("sehr hohe Negativquote")
        elif total >= 5 and neg_ratio is not None and neg_ratio >= 0.70:
            reasons.append("hohe Negativquote")
        if align_total >= 8 and align_rate is not None and align_rate < 0.35:
            reasons.append("niedrige Trefferquote")
        elif align_total >= 5 and align_rate is not None and align_rate < 0.50:
            reasons.append("Trefferquote beobachten")
        if dev_count >= 5 and dev_avg is not None and dev_avg >= 3.0:
            reasons.append("hohe Slider-Abweichung")

        if any(r in reasons for r in ("sehr hohe Negativquote", "niedrige Trefferquote")) or (len(reasons) >= 2 and total >= 5):
            flag = "auffällig"
        elif reasons:
            flag = "beobachten"

        out[int(uid)] = {
            "accept": int(b.get("accept", 0) or 0),
            "revise": int(b.get("revise", 0) or 0),
            "reject": int(b.get("reject", 0) or 0),
            "report": int(b.get("report", 0) or 0),
            "negative": negative,
            "negative_ratio": neg_ratio,
            "alignment_total": align_total,
            "alignment_hits": align_hits,
            "alignment_rate": align_rate,
            "slider_deviation_avg": dev_avg,
            "quality_flag": flag,
            "quality_reasons": reasons,
        }
    return out


@app.get("/api/admin/overview/users", response_model=AdminUsersOverviewOut)
def admin_overview_users(
    include_personal: int = Query(default=0, ge=0, le=1, description="Wenn 1, liefert E-Mail + PLZ unmaskiert."),
    limit: int = Query(default=200, ge=1, le=2000),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> AdminUsersOverviewOut:
    require_admin(current_user)

    show_personal = bool(include_personal == 1)
    cache_key = f"overview_users:masked:{int(limit)}"
    if not show_personal:
        cached = _admin_runtime_cache_get(cache_key)
        if cached is not None:
            return AdminUsersOverviewOut(**cached)

    users = db.query(User).order_by(User.created_at.desc()).limit(int(limit)).all()

    vote_rows = db.query(ArticleVote.user_id, func.count(ArticleVote.id)).group_by(ArticleVote.user_id).all()
    comment_rows = db.query(Comment.user_id, func.count(Comment.id)).group_by(Comment.user_id).all()
    review_rows = db.query(Review.reviewer_id, func.count(Review.id)).group_by(Review.reviewer_id).all()
    review_monitoring = _admin_review_monitoring_by_user(db)

    votes_by_user = {int(uid): int(cnt or 0) for uid, cnt in vote_rows}
    comments_by_user = {int(uid): int(cnt or 0) for uid, cnt in comment_rows}
    reviews_by_user = {int(uid): int(cnt or 0) for uid, cnt in review_rows}

    items = []
    for u in users:
        rm = review_monitoring.get(int(u.id), {})
        items.append(
            {
                "id": int(u.id),
                "pseudonym": str(u.pseudonym or ""),
                "email": (str(u.email) if show_personal else _mask_email(u.email)),
                "plz": (str(u.plz) if show_personal else _mask_plz(u.plz)),
                "trust_level": int(u.trust_level or 0),
                "is_admin": bool(u.is_admin),
                "created_at": u.created_at,
                "is_deleted": bool(getattr(u, "is_deleted", False)),
                "votes_total": int(votes_by_user.get(int(u.id), 0)),
                "comments_total": int(comments_by_user.get(int(u.id), 0)),
                "reviews_total": int(reviews_by_user.get(int(u.id), 0)),
                "review_accept_total": int(rm.get("accept", 0) or 0),
                "review_revise_total": int(rm.get("revise", 0) or 0),
                "review_reject_total": int(rm.get("reject", 0) or 0),
                "review_report_total": int(rm.get("report", 0) or 0),
                "review_negative_total": int(rm.get("negative", 0) or 0),
                "review_negative_ratio": rm.get("negative_ratio"),
                "review_alignment_total": int(rm.get("alignment_total", 0) or 0),
                "review_alignment_hits": int(rm.get("alignment_hits", 0) or 0),
                "review_alignment_rate": rm.get("alignment_rate"),
                "review_slider_deviation_avg": rm.get("slider_deviation_avg"),
                "review_quality_flag": str(rm.get("quality_flag", "ok") or "ok"),
                "review_quality_reasons": list(rm.get("quality_reasons", []) or []),
            }
        )

    out = AdminUsersOverviewOut(
        meta={
            "generated_at": datetime.utcnow(),
            "note": (
                "Personenbezogene Daten sind im Admin-Panel nur für berechtigte Zwecke zu nutzen. "
                "Standard ist maskiert; unmaskiert nur nach Aktivierung."
                if not show_personal
                else "Unmaskierte Ansicht aktiv."
            ),
        },
        items=items,
    )
    if not show_personal:
        _admin_runtime_cache_set(cache_key, _cache_payload(out))
    return out


@app.get("/api/admin/overview/articles", response_model=AdminArticlesOverviewOut)
def admin_overview_articles(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> AdminArticlesOverviewOut:
    require_admin(current_user)

    cached = _admin_runtime_cache_get("overview_articles")
    if cached is not None:
        return AdminArticlesOverviewOut(**cached)

    articles = db.query(Article).order_by(*_article_ordering()).all()

    ver_rows = db.query(ArticleVersion.article_id, func.count(ArticleVersion.id)).group_by(ArticleVersion.article_id).all()
    versions_total = {int(aid): int(cnt or 0) for aid, cnt in ver_rows}

    vote_rows = (
        db.query(ArticleVote.article_id, ArticleVote.main_vote, func.count(ArticleVote.id))
        .join(Article, Article.id == ArticleVote.article_id)
        .filter(ArticleVote.status == "confirmed")
        .filter(ArticleVote.version_id == Article.current_version_id)
        .group_by(ArticleVote.article_id, ArticleVote.main_vote)
        .all()
    )
    votes_by_article = {}
    for aid, main, cnt in vote_rows:
        votes_by_article.setdefault(int(aid), {})[str(main)] = int(cnt or 0)

    c_rows = db.query(Comment.article_id, Comment.status, func.count(Comment.id)).group_by(Comment.article_id, Comment.status).all()
    comments_by_article = {}
    for aid, st, cnt in c_rows:
        comments_by_article.setdefault(int(aid), {})[str(st)] = int(cnt or 0)

    items = []
    for a in articles:
        by_main = votes_by_article.get(int(a.id), {})
        total_votes = int(sum(by_main.values()))
        approval = None
        if total_votes > 0:
            yes = int(by_main.get("✅", 0) + by_main.get("🟢", 0))
            approval = round(100.0 * yes / float(total_votes), 2)

        c_by = comments_by_article.get(int(a.id), {})
        c_total = int(sum(c_by.values()))
        pending = int(c_by.get("review", 0) + c_by.get("korrigieren", 0) + c_by.get("entwurf", 0))

        cv = None
        try:
            cv = a.current_version
        except Exception:
            cv = None

        items.append(
            {
                "id": int(a.id),
                "slug": str(a.slug),
                "public_code": str(a.public_code),
                "sort_order": int(a.sort_order),
                "toc_parent_id": int(a.toc_parent_id) if a.toc_parent_id is not None else None,
                "title": str(a.title),
                "type": str(a.type),
                "display_label": _article_display_label(a),
                "toc_group_label": _article_toc_group_label(a),
                "current_version_id": int(a.current_version_id) if a.current_version_id else None,
                "current_version_label": (str(cv.version_label) if cv and getattr(cv, "version_label", None) else None),
                "versions_total": int(versions_total.get(int(a.id), 0)),
                "votes_total": total_votes,
                "votes_by_main": by_main,
                "approval_percent": approval,
                "comments_total": c_total,
                "comments_by_status": c_by,
                "pending_changes": pending,
                "updated_at": getattr(a, "updated_at", None),
            }
        )

    out = AdminArticlesOverviewOut(meta={"generated_at": datetime.utcnow()}, items=items)
    _admin_runtime_cache_set("overview_articles", _cache_payload(out))
    return out


def _admin_json_dict(value: Any) -> Dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _admin_comment_open_url(article_id: int, comment_id: int) -> str:
    return f"/?kgg_jump=comment&article_id={int(article_id)}&comment_id={int(comment_id)}#artikel-{int(article_id)}"


def _admin_comment_text_blob(comment: Comment) -> str:
    parts = [
        getattr(comment, "status", None),
        getattr(comment, "lifecycle_status", None),
        getattr(comment, "policy_status", None),
        getattr(comment, "candidate_status", None),
        getattr(comment, "comment_mode", None),
    ]
    for value in (getattr(comment, "candidate_reasons", None), getattr(comment, "patch_stats", None)):
        try:
            parts.append(json.dumps(value or {}, ensure_ascii=False, sort_keys=True))
        except Exception:
            parts.append(str(value or ""))
    return " ".join(str(p or "") for p in parts).lower()


def _admin_comment_conflict_flags(comment: Comment) -> List[str]:
    flags: List[str] = []
    policy = str(getattr(comment, "policy_status", "") or "").strip().lower()
    if policy and policy not in {"ok", "none", "clear"}:
        flags.append("policy")
    text = _admin_comment_text_blob(comment)
    if re.search(r"conflict|konflikt|locate|failure|invalid|error|block", text):
        flags.append("patch")
    return sorted(set(flags))


def _admin_comment_report_flags(comment: Comment) -> List[str]:
    text = _admin_comment_text_blob(comment)
    flags: List[str] = []
    if re.search(r"report|meldung|abuse|spam", text):
        flags.append("report")
    if re.search(r"datenschutz|personenbezogen|personal[_ -]?data", text):
        flags.append("datenschutz")
    if re.search(r"recht|illegal|copyright|urheber", text):
        flags.append("recht")
    return sorted(set(flags))


def _admin_comment_candidate_flags(comment: Comment) -> Dict[str, bool]:
    reasons = _admin_json_dict(getattr(comment, "candidate_reasons", None))
    text = _admin_comment_text_blob(comment)
    candidate_status = str(getattr(comment, "candidate_status", "") or "").lower()
    gold = bool(reasons.get("gold_review_candidate") or reasons.get("gold_review_marked") or "gold" in text)
    release = bool(
        reasons.get("release_candidate")
        or reasons.get("next_draft_candidate")
        or candidate_status in {"next-draft", "next_draft", "release_candidate", "integriert"}
    )
    delay = bool(reasons.get("release_delay_candidate") or reasons.get("release_delay_count") or "delay" in text)
    return {"gold": gold, "release": release, "delay": delay}


def _admin_comment_priority(
    comment: Comment,
    *,
    review_state: Dict[str, Any],
    review_rollup: Dict[str, int],
    required_reviews: Optional[int],
    conflict_flags: List[str],
    report_flags: List[str],
    candidate_flags: Dict[str, bool],
) -> Dict[str, Any]:
    if conflict_flags or report_flags:
        return {"rank": 1, "label": "🔴", "title": "kritisch"}
    total = int((review_rollup or {}).get("total") or 0)
    approve = int((review_rollup or {}).get("approve") or 0)
    reject = int((review_rollup or {}).get("reject") or 0)
    publish_ready = bool((review_state or {}).get("publish_ready"))
    reject_ready = bool((review_state or {}).get("reject_ready"))
    if publish_ready or reject_ready or (required_reviews and total >= int(required_reviews)) or (approve >= 1 and reject >= 1):
        return {"rank": 2, "label": "🟠", "title": "entscheidungsreif"}
    if str(getattr(comment, "status", "") or "").lower() == "review" or total > 0:
        return {"rank": 3, "label": "🟡", "title": "Review"}
    if candidate_flags.get("gold") or candidate_flags.get("release") or candidate_flags.get("delay"):
        return {"rank": 4, "label": "🟢", "title": "Kandidat"}
    return {"rank": 9, "label": "—", "title": "normal"}


def _admin_comment_row_dict(
    db: Session,
    comment: Comment,
    article: Article,
    version: ArticleVersion,
    user: User,
    *,
    vote_counts: Optional[Dict[str, int]] = None,
    vote_approval_percent: Optional[float] = None,
    reaction_counts: Optional[Dict[str, int]] = None,
    review_rollup: Optional[Dict[str, int]] = None,
    required_reviews: Optional[int] = None,
) -> Dict[str, Any]:
    """Build the canonical admin comment row shared by comment and review queues."""
    c = comment
    a = article
    v = version
    u = user

    roll = review_rollup or {"total": 0, "approve": 0, "revise": 0, "reject": 0}
    req = required_reviews
    if req is None and str(getattr(c, "status", "") or "").strip().lower() == "review":
        req = _required_reviews_for_comment(c)

    try:
        review_state = _review_state_for_comment(db, c)
    except Exception:
        review_state = {}

    candidate_reasons = _admin_json_dict(getattr(c, "candidate_reasons", None))
    patch_stats = _admin_json_dict(getattr(c, "patch_stats", None))
    conflict_flags = _admin_comment_conflict_flags(c)
    report_flags = _admin_comment_report_flags(c)
    candidate_flags = _admin_comment_candidate_flags(c)
    prio = _admin_comment_priority(
        c,
        review_state=review_state,
        review_rollup=roll,
        required_reviews=req,
        conflict_flags=conflict_flags,
        report_flags=report_flags,
        candidate_flags=candidate_flags,
    )

    return {
        "id": int(c.id),
        "article_id": int(a.id),
        "article_slug": str(a.slug),
        "article_title": str(a.title),
        "version_id": int(v.id),
        "version_label": getattr(v, "version_label", None),
        "status": str(c.status),
        "lifecycle_status": getattr(c, "lifecycle_status", None),
        "policy_status": getattr(c, "policy_status", None),
        "candidate_status": getattr(c, "candidate_status", None),
        "candidate_reasons": candidate_reasons,
        "candidate_score": getattr(c, "candidate_score", None),
        "comment_mode": str(getattr(c, "comment_mode", "change") or "change"),
        "patch_stats": patch_stats,
        "created_at": c.created_at,
        "updated_at": getattr(c, "updated_at", None),
        "user_id": int(u.id),
        "user_pseudonym": str(u.pseudonym or ""),
        "vote_counts": vote_counts or {},
        "vote_approval_percent": vote_approval_percent,
        "reaction_counts": reaction_counts or {},
        "required_reviews": req,
        "review_rollup": roll,
        "review_state": review_state,
        "review_trust_signal": str((review_state or {}).get("trust_review_signal") or ""),
        "review_trust_net_score": (review_state or {}).get("trust_net_score"),
        "admin_priority_rank": int(prio.get("rank") or 9),
        "admin_priority_label": str(prio.get("label") or "—"),
        "admin_priority_title": str(prio.get("title") or "normal"),
        "gold_review_candidate": bool(candidate_flags.get("gold")),
        "release_candidate": bool(candidate_flags.get("release")),
        "release_delay_candidate": bool(candidate_flags.get("delay")),
        "conflict_flags": conflict_flags,
        "report_flags": report_flags,
        "open_url": _admin_comment_open_url(a.id, c.id),
    }


def _admin_comment_vote_aggregates(db: Session, comment_ids: List[int]) -> tuple[Dict[int, Dict[str, int]], Dict[int, float | None]]:
    """Return raw comment-vote counts and approval percentages for admin tables."""
    counts: Dict[int, Dict[str, int]] = {}
    approval: Dict[int, float | None] = {}
    ids = [int(x) for x in list(comment_ids or []) if int(x) > 0]
    if not ids:
        return counts, approval

    rows = (
        db.query(CommentVote.comment_id, CommentVote.main_vote, func.count(CommentVote.id))
        .filter(CommentVote.comment_id.in_(ids))
        .filter(CommentVote.status.in_(["confirmed", "pending"]))
        .group_by(CommentVote.comment_id, CommentVote.main_vote)
        .all()
    )
    for cid, main_vote, cnt in rows:
        counts.setdefault(int(cid), {})[str(main_vote)] = int(cnt or 0)

    for cid, dist in counts.items():
        pos = int(dist.get("✅", 0)) + int(dist.get("🟢", 0))
        neu = int(dist.get("🟡", 0))
        neg = int(dist.get("🟠", 0)) + int(dist.get("🔴", 0))
        total = pos + neu + neg
        approval[int(cid)] = (100.0 * float(pos) / float(total)) if total > 0 else None
    return counts, approval


@app.get("/api/admin/overview/comments", response_model=AdminCommentsOverviewOut)
def admin_overview_comments(
    hide_done: int = Query(default=1, ge=0, le=1, description="Wenn 1, blendet veröffentlichte/archivierte aus."),
    sort: str = Query(default="newest", description="newest|oldest|approval"),
    limit: int = Query(default=250, ge=1, le=2000),
    offset: int = Query(default=0, ge=0, le=200000),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> AdminCommentsOverviewOut:
    require_admin(current_user)

    cache_key = f"overview_comments:{int(hide_done)}:{str(sort or '').strip()}:{int(limit)}:{int(offset)}"
    cached = _admin_runtime_cache_get(cache_key)
    if cached is not None:
        return AdminCommentsOverviewOut(**cached)

    q = (
        db.query(Comment, Article, ArticleVersion, User)
        .join(Article, Article.id == Comment.article_id)
        .join(ArticleVersion, ArticleVersion.id == Comment.version_id)
        .join(User, User.id == Comment.user_id)
    )
    if bool(hide_done == 1):
        # A published `new_article` comment remains part of the active public state:
        # the published comment is the visibility anchor for the proposed article,
        # so it stays in the normal public comment set until its state changes.
        q = q.filter(
            (Comment.status.notin_(["veröffentlicht", "veroeffentlicht", "published", "archiviert"]))
            | ((Comment.comment_mode == "new_article") & (Comment.status.in_(["veröffentlicht", "veroeffentlicht", "published"])))
        )

    if sort == "oldest":
        q = q.order_by(Comment.created_at.asc())
    else:
        q = q.order_by(Comment.created_at.desc())

    rows = q.offset(int(offset)).limit(int(limit)).all()
    comment_ids = [int(c.id) for (c, _a, _v, _u) in rows]
    vote_counts, vote_approval = _admin_comment_vote_aggregates(db, comment_ids)

    reaction_counts: dict[int, dict[str, int]] = {}
    if comment_ids:
        erows = (
            db.query(CommentReaction.comment_id, CommentReaction.reaction_emoji, func.count(CommentReaction.id))
            .filter(CommentReaction.comment_id.in_(comment_ids))
            .group_by(CommentReaction.comment_id, CommentReaction.reaction_emoji)
            .all()
        )
        for cid, emo, cnt in erows:
            reaction_counts.setdefault(int(cid), {})[str(emo)] = int(cnt or 0)

    rollup_by_comment: dict[int, dict[str, int]] = {}
    if comment_ids:
        vrows = (
            db.query(Review.comment_id, Review.recommendation, func.count(Review.id))
            .filter(Review.comment_id.in_(comment_ids))
            .group_by(Review.comment_id, Review.recommendation)
            .all()
        )
        tmp: dict[int, dict[str, int]] = {}
        for cid, rec, cnt in vrows:
            tmp.setdefault(int(cid), {})[str(rec)] = int(cnt or 0)
        for cid, dist in tmp.items():
            a = r = x = 0
            for rec, cnt in dist.items():
                key = (str(rec) if rec is not None else "").strip().lower()
                if key in _APPROVE_RECS:
                    a += int(cnt or 0)
                elif key in _REVISE_RECS:
                    r += int(cnt or 0)
                elif key in _REJECT_RECS:
                    x += int(cnt or 0)
            rollup_by_comment[int(cid)] = {"total": int(a + r + x), "approve": a, "revise": r, "reject": x}

    items = []
    for (c, a, v, u) in rows:
        roll = rollup_by_comment.get(int(c.id), {"total": 0, "approve": 0, "revise": 0, "reject": 0})
        req = _required_reviews_for_comment(c) if c.status == "review" else None
        items.append(
            _admin_comment_row_dict(
                db,
                c,
                a,
                v,
                u,
                vote_counts=vote_counts.get(int(c.id), {}),
                vote_approval_percent=vote_approval.get(int(c.id)),
                reaction_counts=reaction_counts.get(int(c.id), {}),
                review_rollup=roll,
                required_reviews=req,
            )
        )

    if sort == "approval":
        items.sort(
            key=lambda it: (
                it.get("vote_approval_percent") is None,
                -(it.get("vote_approval_percent") or -1.0),
                -it.get("created_at").timestamp(),
            )
        )

    out = AdminCommentsOverviewOut(meta={"generated_at": datetime.utcnow()}, items=items)
    _admin_runtime_cache_set(cache_key, _cache_payload(out))
    return out


@app.get("/api/admin/comment-changes")
def admin_comment_changes(
    article_id: int | None = Query(default=None, ge=1),
    limit_comments: int = Query(default=250, ge=1, le=1000),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Dict[str, Any]:
    require_admin(current_user)

    q = db.query(Comment).order_by(Comment.created_at.desc())
    if article_id is not None:
        q = q.filter(Comment.article_id == int(article_id))
    q = q.filter(Comment.status.notin_(["gelöscht", "geloescht", "archiviert"]))

    comments = q.limit(int(limit_comments)).all()
    grouped: dict[tuple[int, int], list[Comment]] = {}
    for c in comments:
        aid = int(getattr(c, "article_id", 0) or 0)
        vid = int(getattr(c, "version_id", 0) or 0)
        if aid <= 0 or vid <= 0:
            continue
        grouped.setdefault((aid, vid), []).append(c)

    items: list[dict[str, Any]] = []
    locate_failures: list[dict[str, Any]] = []

    for (aid, vid), group in grouped.items():
        article = db.query(Article).filter(Article.id == aid).one_or_none()
        version = (
            db.query(ArticleVersion)
            .filter(ArticleVersion.id == vid, ArticleVersion.article_id == aid)
            .one_or_none()
        )
        if article is None or version is None:
            continue

        _baseline_article_minimd, baseline_old_minimd_by_block = _article_version_minimd_bundle(version)
        baseline_blocks = _site_wrapped_blocks_from_version(version)
        result = code.compose_article_merge_preview(
            block_order=list(_minimd_block_order()),
            baseline_old_minimd_by_block=dict(baseline_old_minimd_by_block or {}),
            baseline_html_by_block=dict(baseline_blocks or {}),
            comments=[_comment_to_indiff_input(c) for c in list(group or [])],
            fallback_block_key="juristisch",
        )

        by_cid = {int(getattr(c, "id", 0) or 0): c for c in list(group or [])}
        locate_failures.extend(list(result.locate_failures or []))

        for row in list(result.comment_cards_table or []):
            rec = dict(row or {})
            cid = int(rec.get("cid") or 0)
            c = by_cid.get(cid)
            items.append(
                {
                    "cid": cid,
                    "comment_status": str(getattr(c, "status", "") or "") if c is not None else "",
                    "comment_created_at": getattr(c, "created_at", None) if c is not None else None,
                    "article_id": int(aid),
                    "article_slug": str(getattr(article, "slug", "") or ""),
                    "article_title": str(getattr(article, "title", "") or ""),
                    "version_id": int(vid),
                    "version_label": str(getattr(version, "version_label", "") or ""),
                    "part_id": str(rec.get("part_id") or ""),
                    "owner_id": str(rec.get("owner_id") or ""),
                    "block_key": str(rec.get("block_key") or ""),
                    "span_id": str(rec.get("span_id") or ""),
                    "kind": str(rec.get("kind") or ""),
                    "subkind": str(rec.get("subkind") or ""),
                    "source_start": int(rec.get("source_start") or 0),
                    "source_end": int(rec.get("source_end") or 0),
                    "default_state": str(rec.get("default_state") or "normal"),
                    "recommended_state": str(rec.get("recommended_state") or "normal"),
                    "group_id": str(rec.get("group_id") or ""),
                    "group_type": str(rec.get("group_type") or ""),
                    "is_winner": bool(rec.get("is_winner") or False),
                    "html_fragment": str(rec.get("html_fragment") or ""),
                }
            )

    items.sort(
        key=lambda r: (
            -int(r.get("cid") or 0),
            str(r.get("block_key") or ""),
            int(r.get("source_start") or 0),
            str(r.get("kind") or ""),
            str(r.get("span_id") or ""),
        )
    )

    return {
        "meta": {
            "generated_at": datetime.utcnow(),
            "comments_total": int(len(comments)),
            "rows_total": int(len(items)),
            "locate_failures_total": int(len(locate_failures)),
        },
        "items": items,
        "locate_failures": list(locate_failures or []),
    }


def _comment_can_be_hard_deleted(db: Session, comment: Comment) -> bool:
    cid = int(getattr(comment, "id", 0) or 0)
    if cid <= 0:
        return False
    st = str(getattr(comment, "status", "") or "").strip().lower()
    lc = str(getattr(comment, "lifecycle_status", "") or "").strip().lower()
    if st != "entwurf" or lc != "draft":
        return False

    has_reviews = db.query(Review.id).filter(Review.comment_id == cid).first() is not None
    has_votes = db.query(CommentVote.id).filter(CommentVote.comment_id == cid).first() is not None
    has_reactions = db.query(CommentReaction.id).filter(CommentReaction.comment_id == cid).first() is not None
    has_children = db.query(Comment.id).filter(Comment.parent_comment_id == cid).first() is not None

    return not (has_reviews or has_votes or has_reactions or has_children)


@app.get("/api/admin/overview/reviews/queue", response_model=AdminReviewQueueOut)
def admin_overview_review_queue(
    limit: int = Query(default=200, ge=1, le=2000),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> AdminReviewQueueOut:
    require_admin(current_user)

    cache_key = f"overview_review_queue:{int(limit)}"
    cached = _admin_runtime_cache_get(cache_key)
    if cached is not None:
        return AdminReviewQueueOut(**cached)

    rows = (
        db.query(Comment, Article, ArticleVersion, User)
        .join(Article, Article.id == Comment.article_id)
        .join(ArticleVersion, ArticleVersion.id == Comment.version_id)
        .join(User, User.id == Comment.user_id)
        # Historical rows may contain differently cased status values such as `Review`.
        .filter(func.lower(Comment.status) == "review")
        .order_by(Comment.created_at.asc())
        .limit(int(limit))
        .all()
    )

    comment_ids = [int(c.id) for (c, _a, _v, _u) in rows]
    rollups: dict[int, dict] = {}
    vote_counts, vote_approval = _admin_comment_vote_aggregates(db, comment_ids)
    reaction_counts: dict[int, dict[str, int]] = {}

    if comment_ids:
        erows = (
            db.query(CommentReaction.comment_id, CommentReaction.reaction_emoji, func.count(CommentReaction.id))
            .filter(CommentReaction.comment_id.in_(comment_ids))
            .group_by(CommentReaction.comment_id, CommentReaction.reaction_emoji)
            .all()
        )
        for cid, emo, cnt in erows:
            reaction_counts.setdefault(int(cid), {})[str(emo)] = int(cnt or 0)

    if comment_ids:
        vrows = (
            db.query(Review.comment_id, Review.recommendation, func.count(Review.id))
            .filter(Review.comment_id.in_(comment_ids))
            .group_by(Review.comment_id, Review.recommendation)
            .all()
        )
        tmp: dict[int, dict[str, int]] = {}
        for cid, rec, cnt in vrows:
            tmp.setdefault(int(cid), {})[str(rec)] = int(cnt or 0)
        for cid, dist in tmp.items():
            a = r = x = 0
            for rec, cnt in dist.items():
                key = (str(rec) if rec is not None else "").strip().lower()
                if key in _APPROVE_RECS:
                    a += int(cnt or 0)
                elif key in _REVISE_RECS:
                    r += int(cnt or 0)
                elif key in _REJECT_RECS:
                    x += int(cnt or 0)
            rollups[int(cid)] = {"total": int(a + r + x), "approve": a, "revise": r, "reject": x}

    items = []
    for (c, a, v, u) in rows:
        req = _required_reviews_for_comment(c)
        roll = rollups.get(int(c.id), {"total": 0, "approve": 0, "revise": 0, "reject": 0})
        total = int(roll.get("total") or 0)
        open_needed = max(0, int(req - total))
        items.append(
            {
                "comment": _admin_comment_row_dict(
                    db,
                    c,
                    a,
                    v,
                    u,
                    vote_counts=vote_counts.get(int(c.id), {}),
                    vote_approval_percent=vote_approval.get(int(c.id)),
                    reaction_counts=reaction_counts.get(int(c.id), {}),
                    review_rollup=roll,
                    required_reviews=int(req),
                ),
                "open_reviews_needed": int(open_needed),
            }
        )

    out = AdminReviewQueueOut(meta={"generated_at": datetime.utcnow()}, total_in_review=int(len(items)), items=items)
    _admin_runtime_cache_set(cache_key, _cache_payload(out))
    return out


@app.get("/api/admin/overview/votes/distribution", response_model=AdminVoteDistributionOut)
def admin_overview_vote_distribution(
    days: int = Query(default=30, ge=7, le=365),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> AdminVoteDistributionOut:
    require_admin(current_user)

    cache_key = f"overview_vote_distribution:{int(days)}"
    cached = _admin_runtime_cache_get(cache_key)
    if cached is not None:
        return AdminVoteDistributionOut(**cached)

    now = datetime.utcnow()
    start = now - timedelta(days=int(days))

    day_expr = func.date(ArticleVote.created_at)
    rows = (
        db.query(day_expr, ArticleVote.main_vote, func.count(ArticleVote.id))
        .filter(ArticleVote.status == "confirmed")
        .filter(ArticleVote.created_at >= start)
        .group_by(day_expr, ArticleVote.main_vote)
        .all()
    )
    by_day: dict[str, dict[str, int]] = {}
    totals: dict[str, int] = {}
    for day, mv, cnt in rows:
        d = str(day)
        by_day.setdefault(d, {})[str(mv)] = int(cnt or 0)
        totals[d] = int(totals.get(d, 0) + int(cnt or 0))

    urows = (
        db.query(day_expr, func.count(func.distinct(ArticleVote.user_id)))
        .filter(ArticleVote.status == "confirmed")
        .filter(ArticleVote.created_at >= start)
        .group_by(day_expr)
        .all()
    )
    distinct_users = {str(day): int(cnt or 0) for day, cnt in urows}

    ltrows = (
        db.query(day_expr, func.count(ArticleVote.id))
        .join(User, User.id == ArticleVote.user_id)
        .filter(ArticleVote.status == "confirmed")
        .filter(ArticleVote.created_at >= start)
        .filter(User.trust_level <= 1)
        .group_by(day_expr)
        .all()
    )
    low_trust = {str(day): int(cnt or 0) for day, cnt in ltrows}

    new_cut = now - timedelta(days=7)
    nurows = (
        db.query(day_expr, func.count(ArticleVote.id))
        .join(User, User.id == ArticleVote.user_id)
        .filter(ArticleVote.status == "confirmed")
        .filter(ArticleVote.created_at >= start)
        .filter(User.created_at >= new_cut)
        .group_by(day_expr)
        .all()
    )
    new_users = {str(day): int(cnt or 0) for day, cnt in nurows}

    series = []
    for i in range(int(days) - 1, -1, -1):
        d = (now.date() - timedelta(days=i)).isoformat()
        by_main = by_day.get(d, {})
        total = int(totals.get(d, 0))
        approval = None
        if total > 0:
            yes = int(by_main.get("✅", 0) + by_main.get("🟢", 0))
            approval = round(100.0 * yes / float(total), 2)
        series.append(
            {
                "day": d,
                "total": total,
                "by_main": by_main,
                "distinct_users": int(distinct_users.get(d, 0)),
                "low_trust_votes": int(low_trust.get(d, 0)),
                "new_user_votes": int(new_users.get(d, 0)),
                "approval_percent": approval,
                "anomaly": False,
            }
        )

    totals_list = [int(it["total"]) for it in series]
    for idx in range(len(series)):
        prev = totals_list[max(0, idx - 7):idx]
        if len(prev) < 4:
            continue
        prev_sorted = sorted(prev)
        med = prev_sorted[len(prev_sorted) // 2]
        if med <= 0:
            continue
        if series[idx]["total"] >= 20 and series[idx]["total"] > 3 * med:
            series[idx]["anomaly"] = True

    out = AdminVoteDistributionOut(
        meta={"generated_at": now, "note": "Heuristik: anomaly=true markiert auffällige Spike-Tage (nur Hinweis, kein Beweis)."},
        days=int(days),
        series=series,
    )
    _admin_runtime_cache_set(cache_key, _cache_payload(out))
    return out


# ---------------------------------------------------------------------------
# Reviews — Review-v2/trust gate; domain logic lives in `review.py`
# ---------------------------------------------------------------------------


def _required_reviews_for_comment(comment: Comment) -> int:
    """Compatibility wrapper exposing the review requirement used by existing admin/overview paths."""
    return int(review_code.base_required_reviews_for_comment(comment, settings))


def _review_rows_for_comment(db: Session, comment_id: int) -> list[Review]:
    return (
        db.query(Review)
        .filter(Review.comment_id == int(comment_id))
        .order_by(Review.created_at.asc(), Review.id.asc())
        .all()
    )


def _review_state_for_comment(db: Session, comment: Comment) -> dict[str, Any]:
    state = review_code.compute_comment_review_state(comment, _review_rows_for_comment(db, comment.id), settings)
    return review_code.review_state_to_dict(state)


def _review_rollup(db: Session, comment_id: int) -> dict:
    """Build the legacy-compatible review rollup from the canonical Review-v2 state."""
    comment = db.query(Comment).filter(Comment.id == int(comment_id)).one_or_none()
    if comment is None:
        return {"total": 0, "approve": 0, "revise": 0, "reject": 0}
    state = review_code.compute_comment_review_state(comment, _review_rows_for_comment(db, comment.id), settings)
    return {
        "total": int(state.review_count),
        "approve": int(state.positive_count),
        "revise": int(getattr(state, "revise_count", 0) or 0),
        "reject": int(getattr(state, "reject_count", 0) or 0),
        "review_state": review_code.review_state_to_dict(state),
    }


def _apply_review_transition(comment: Comment, transition: review_code.ReviewTransition) -> None:
    """Apply the comment-state transition calculated by `review.py` in place."""
    now = datetime.utcnow()
    if transition.status:
        old_status = str(getattr(comment, "status", "") or "")
        comment.status = transition.status
        if transition.status == "veröffentlicht" and old_status != "veröffentlicht":
            comment.published_at = now
    if transition.lifecycle_status:
        comment.lifecycle_status = transition.lifecycle_status
    if transition.policy_status:
        comment.policy_status = transition.policy_status
    if transition.candidate_status is not None:
        comment.candidate_status = transition.candidate_status
    if transition.candidate_reasons:
        comment.candidate_reasons = dict(transition.candidate_reasons)


def _enforce_reviewer_daily_limit(db: Session, current_user: User) -> None:
    """Enforce the per-user UTC-day review quota for the reviewer's trust tier."""
    trust = review_code.get_effective_trust(current_user, settings)
    limit = int(review_code.get_review_limit_for_trust(trust, settings) or 0)
    if limit <= 0:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="Heute sind keine Reviews mehr möglich")
    start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    count = (
        db.query(func.count(Review.id))
        .filter(Review.reviewer_id == current_user.id, Review.created_at >= start)
        .scalar()
        or 0
    )
    if int(count) >= int(limit):
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=f"Tageslimit für Reviews erreicht ({limit}/Tag)")


def _review_candidate_query_for_user(
    db: Session,
    current_user: User,
    *,
    test_statuses: list[str],
    include_already_reviewed: bool = False,
    test_only: bool = False,
):
    """Build the base review-candidate query. Normal cases are shown once per user; published test cases may be recycled only when no unreviewed gate/test cases remain."""
    reviewed_subq = db.query(Review.comment_id).filter(Review.reviewer_id == current_user.id)
    gate_filter = func.lower(Comment.status) == "review"
    test_filter = (
        func.lower(Comment.status).in_(["veröffentlicht", "veroeffentlicht", "published"])
        & Comment.candidate_status.in_(test_statuses)
    )
    q = db.query(Comment)
    if not include_already_reviewed:
        q = q.filter(~Comment.id.in_(reviewed_subq))
    q = q.filter(test_filter if test_only else (gate_filter | test_filter))
    if not bool(getattr(current_user, "is_admin", False)) or not bool(getattr(settings, "ADMIN_ALLOW_SELF_REVIEW", True)):
        q = q.filter(Comment.user_id != current_user.id)
    q = q.filter((Comment.candidate_status == None) | (~Comment.candidate_status.in_(["review_stop", "admin_warning", "moderation_needed", "revision_requested"])))  # noqa: E711
    return q


def _has_unreviewed_review_case_for_user(db: Session, current_user: User, *, test_statuses: list[str]) -> bool:
    return (
        _review_candidate_query_for_user(db, current_user, test_statuses=test_statuses, include_already_reviewed=False)
        .with_entities(Comment.id)
        .first()
        is not None
    )


def _allow_repeat_test_review_when_queue_empty(
    db: Session,
    current_user: User,
    *,
    comment: Comment,
    context: str,
    already_reviewed: bool,
    test_statuses: list[str],
) -> bool:
    if not already_reviewed or context != review_code.CONTEXT_TEST:
        return False
    return not _has_unreviewed_review_case_for_user(db, current_user, test_statuses=test_statuses)


@app.get("/api/reviews/next", response_model=ReviewNextOut)
def reviews_next(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ReviewNextOut:
    """Return a randomized next eligible review case for the current user."""
    test_statuses = review_code.cfg_str_list(
        settings,
        "REVIEW_TEST_POOL_CANDIDATE_STATUSES",
        review_code.DEFAULT_REVIEW_TEST_POOL_CANDIDATE_STATUSES,
    )
    comment = (
        _review_candidate_query_for_user(db, current_user, test_statuses=test_statuses, include_already_reviewed=False)
        .order_by(func.random())
        .first()
    )
    if comment is None:
        # Empty-queue compatibility: published test/calibration cases may be reused
        
        comment = (
            _review_candidate_query_for_user(
                db,
                current_user,
                test_statuses=test_statuses,
                include_already_reviewed=True,
                test_only=True,
            )
            .order_by(func.random())
            .first()
        )
    if comment is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Kein Review-Fall verfügbar")
    context = review_code.review_context_for_comment(comment, settings)
    state = review_code.compute_comment_review_state(comment, _review_rows_for_comment(db, comment.id), settings)
    hint = (
        "Prüfe, ob dieser Kommentar veröffentlicht werden kann."
        if context == review_code.CONTEXT_GATE
        else "Dieser Kommentar ist bereits veröffentlicht. Deine Bewertung hilft bei Kalibrierung, Priorisierung und Release-Vorbereitung."
    )
    return ReviewNextOut(comment=_comment_out(comment), context=context, review_state=review_code.review_state_to_dict(state), ui_hint=hint)


@app.post("/api/reviews", response_model=ReviewOut)
def create_review(
    payload: ReviewCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ReviewOut:
    """Create a Review-v2 record for an eligible gate or test comment."""
    comment = db.query(Comment).filter(Comment.id == payload.comment_id).one_or_none()
    if comment is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Kommentar nicht gefunden")

    already_reviewed = (
        db.query(Review.id)
        .filter(Review.comment_id == comment.id, Review.reviewer_id == current_user.id)
        .first()
        is not None
    )
    context = review_code.review_context_for_comment(comment, settings)
    test_statuses = review_code.cfg_str_list(
        settings,
        "REVIEW_TEST_POOL_CANDIDATE_STATUSES",
        review_code.DEFAULT_REVIEW_TEST_POOL_CANDIDATE_STATUSES,
    )
    allow_repeat_test_review = _allow_repeat_test_review_when_queue_empty(
        db,
        current_user,
        comment=comment,
        context=context,
        already_reviewed=already_reviewed,
        test_statuses=test_statuses,
    )
    if not review_code.is_reviewable_for_user(
        comment,
        current_user,
        has_reviewed=(already_reviewed and not allow_repeat_test_review),
        settings=settings,
    ):
        if already_reviewed and not allow_repeat_test_review:
            detail = "Du hast diesen Kommentar bereits reviewed"
        elif int(comment.user_id or 0) == int(current_user.id or 0):
            detail = "Eigene Kommentare können nicht reviewed werden"
        elif context == review_code.CONTEXT_NONE:
            detail = "Kommentar ist nicht im Review-Pool"
        else:
            detail = "Kommentar kann nicht reviewed werden"
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=detail)
    try:
        normalized = review_code.normalize_review_payload(payload, context=context)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))

    _enforce_reviewer_daily_limit(db, current_user)
    review = Review(
        comment_id=comment.id,
        reviewer_id=current_user.id,
        checks=payload.checks,
        structured_checks=review_code.normalized_review_to_structured_checks(normalized),
        reviewer_trust_at_time=review_code.get_effective_trust(current_user, settings),
        recommendation=normalized.decision,
        review_note=None,
        visible_to_public=False,
        created_at=datetime.utcnow(),
    )
    db.add(review)
    db.flush()
    state = review_code.compute_comment_review_state(comment, _review_rows_for_comment(db, comment.id), settings)
    _apply_review_transition(comment, review_code.decide_comment_transition(comment, state))

    db.add(comment)
    _commit_db(db)
    db.refresh(review)
    _clear_public_runtime_caches()
    out = ReviewOut.model_validate(review)
    out.review_state = review_code.review_state_to_dict(state)
    out.comment_status = str(comment.status or "")
    return out


@app.get("/api/comments/{comment_id}/reviews", response_model=CommentReviewsOut)
def list_comment_reviews(
    comment_id: int,
    db: Session = Depends(get_db),
    current_user: User | None = Depends(get_current_user_optional),
) -> CommentReviewsOut:
    """Return reviews associated with one comment."""
    comment = db.query(Comment).filter(Comment.id == comment_id).one_or_none()
    if comment is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Kommentar nicht gefunden")

    q = db.query(Review).filter(Review.comment_id == comment.id).order_by(Review.created_at.asc())

    if current_user is None:
        q = q.filter(Review.visible_to_public == True)  # noqa: E712
    else:
        if not current_user.is_admin and comment.user_id != current_user.id:
            q = q.filter(Review.visible_to_public == True)  # noqa: E712

    reviews = q.all()
    return CommentReviewsOut(
        comment_id=comment.id,
        reviews=[ReviewOut.model_validate(r) for r in reviews],
    )


@app.post("/api/auth/magic-link", response_model=dict)
def request_magic_link(
    request: Request,
    payload: MagicLinkRequest,
    db: Session = Depends(get_db),
    x_forwarded_for: str | None = Header(default=None, alias="X-Forwarded-For"),
    user_agent: str | None = Header(default=None, alias="User-Agent"),
) -> dict:
    """Create a single-use login token and, when configured, send the browser magic-link by e-mail. Development may additionally expose a direct callback URL."""
    from secrets import token_urlsafe

    now = datetime.utcnow()

    email_str = str(payload.email).lower()

    # ------------------------------------------------------------------
    # Disposable-email detection
    # ------------------------------------------------------------------
    domain = email_str.split("@", 1)[-1]

    if settings.DISPOSABLE_EMAIL_POLICY and settings.DISPOSABLE_EMAIL_DOMAINS:
        disposable_domains_lower = {d.lower() for d in settings.DISPOSABLE_EMAIL_DOMAINS}
        if domain in disposable_domains_lower:
            if settings.DISPOSABLE_EMAIL_POLICY == "reject":
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=(
                        "Wegwerf-E-Mail-Adressen werden für die Teilnahme nicht unterstützt. "
                        "Bitte nutze eine reguläre E-Mail-Adresse."
                    ),
                )
            

    # Trust `X-Forwarded-For` only when the deployment explicitly enables it.
    direct_ip = (request.client.host if request.client else None) or None
    if settings.ANALYTICS_TRUST_X_FORWARDED_FOR:
        forwarded_ip = (x_forwarded_for or "").split(",")[0].strip()
        ip = forwarded_ip or direct_ip
    else:
        ip = direct_ip

    # ------------------------------------------------------------------
    
    # ------------------------------------------------------------------
    one_hour_ago = now - timedelta(hours=1)

    if settings.MAGIC_LINK_MAX_PER_EMAIL_PER_HOUR > 0:
        count_email = (
            db.query(LoginToken)
            .filter(
                LoginToken.email == email_str,
                LoginToken.created_at >= one_hour_ago,
            )
            .count()
        )
        if count_email >= settings.MAGIC_LINK_MAX_PER_EMAIL_PER_HOUR:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=(
                    "Zu viele Magic-Link-Anfragen für diese E-Mail-Adresse in kurzer Zeit. "
                    "Bitte versuche es später erneut."
                ),
            )

    if ip and settings.MAGIC_LINK_MAX_PER_IP_PER_HOUR > 0:
        count_ip = (
            db.query(LoginToken)
            .filter(
                LoginToken.ip_address == ip,
                LoginToken.created_at >= one_hour_ago,
            )
            .count()
        )
        if count_ip >= settings.MAGIC_LINK_MAX_PER_IP_PER_HOUR:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=(
                    "Zu viele Magic-Link-Anfragen von dieser IP-Adresse in kurzer Zeit. "
                    "Bitte versuche es später erneut."
                ),
            )

    mail_enabled = bool(settings.AUTH_MAGIC_LINK_EMAIL_ENABLED)
    if mail_enabled:
        try:
            validate_magic_link_mail_config()
        except Exception as exc:
            if not settings.AUTH_EXPOSE_LOGIN_URL:
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="Mailversand ist serverseitig nicht vollständig konfiguriert.",
                ) from exc
            mail_enabled = False
    elif not settings.AUTH_EXPOSE_LOGIN_URL:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Magic-Link-Anmeldung ist serverseitig nicht vollständig konfiguriert.",
        )

    token_value = token_urlsafe(32)
    expires_at = now + timedelta(
        minutes=settings.MAGIC_LINK_TOKEN_LIFETIME_MINUTES,
    )

    login_token = LoginToken(
        email=email_str,
        token=token_value,
        created_at=now,
        expires_at=expires_at,
        ip_address=ip,
        user_agent=user_agent,
    )
    db.add(login_token)
    _commit_db(db)

    # Development/tests may expose a relative callback URL directly to the client.
    login_url = f"/api/auth/callback?token={token_value}"

    # Public browser link used in the e-mail flow.
    base_url = (settings.PUBLIC_BASE_URL or "").strip().rstrip("/")
    if not base_url:
        # Development fallback: derive the public base URL from the request.
        base_url = str(request.base_url).rstrip("/")

    complete_path = (settings.AUTH_MAGIC_LINK_COMPLETE_PATH or "/auth/complete").strip()
    if not complete_path.startswith("/"):
        complete_path = "/" + complete_path

    magic_link = f"{base_url}{complete_path}?token={token_value}"

    email_sent = False

    if mail_enabled:
        try:
            send_magic_link_mail(user_email=email_str, magic_link=magic_link)
            email_sent = True
        except Exception as e:
            # Development may still continue through the exposed direct callback URL.
            # In production, failure to deliver the configured e-mail must fail explicitly rather than strand the user.
            if not settings.AUTH_EXPOSE_LOGIN_URL:
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="Mailversand fehlgeschlagen. Bitte später erneut versuchen.",
                ) from e

    detail = (
        "Magic-Link erstellt. Bitte prüfe dein Postfach (ggf. Spam)."
        if email_sent
        else "Magic-Link erstellt (DEV/ohne Mailversand)."
    )

    out: dict = {
        "detail": detail,
        "expires_at": expires_at.isoformat() + "Z",
        "email_sent": email_sent,
    }
    if settings.AUTH_EXPOSE_LOGIN_URL:
        out["login_url"] = login_url

    return out


@app.get("/auth/complete", include_in_schema=False)
def auth_complete_page() -> HTMLResponse:
    """Serve the browser bridge that exchanges the e-mail token for the API callback result and stores the resulting JWT client-side."""
    project_name = html.escape(str(getattr(settings, "PROJECT_NAME", "") or "Webprojekt"))
    html_doc = f"""
<!doctype html>
<html lang="de">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <meta name="robots" content="noindex, nofollow" />
    <meta http-equiv="Cache-Control" content="no-store" />
    <title>{project_name} – Anmeldung</title>
    <link rel="stylesheet" href="/static/style.css" />
  </head>
  <body>
    <main style="max-width: 720px; margin: 40px auto; padding: 0 16px;">
      <h1>{project_name} – Anmeldung</h1>
      <p id="auth-complete-status">Bitte warten …</p>
      <p class="klein">
        Falls die Weiterleitung nicht klappt: öffne die Hauptseite neu und prüfe dein Postfach erneut.
      </p>
    </main>
    <script src="/static/auth_complete.js"></script>
  </body>
</html>
"""
    resp = HTMLResponse(content=html_doc)
    return _add_noindex_headers(resp)


@app.get("/api/auth/callback", response_model=AuthCallbackResponse)
def auth_callback(
    token: str = Query(..., description="Magic-Link-Token"),
    db: Session = Depends(get_db),
) -> AuthCallbackResponse:
    """Consume a magic-link token. Existing users receive a JWT; new users receive a signup token and complete profile creation separately."""
    now = datetime.utcnow()

    login_token = db.query(LoginToken).filter(LoginToken.token == token).first()
    if login_token is None:
        raise _auth_error("token_invalid", "Dieser Anmeldelink ist ungültig oder unbekannt.")
    if login_token.used_at is not None:
        raise _auth_error("token_used", "Dieser Anmeldelink wurde bereits verwendet.")
    if login_token.expires_at < now:
        raise _auth_error("token_expired", "Dieser Anmeldelink ist abgelaufen. Bitte fordere einen neuen Link an.")

    # Resolve an existing user when present.
    user: User | None = None
    if login_token.user_id is not None:
        user = db.get(User, login_token.user_id)
    if user is None:
        user = db.query(User).filter(User.email == login_token.email).first()

    
    if user is None:
        return AuthCallbackResponse(
            signup_required=True,
            signup_token=login_token.token,
        )

    updated = (
        db.query(LoginToken)
        .filter(LoginToken.id == login_token.id)
        .filter(LoginToken.used_at.is_(None))
        .filter(LoginToken.expires_at >= now)
        .update(
            {LoginToken.user_id: user.id, LoginToken.used_at: now},
            synchronize_session=False,
        )
    )
    if int(updated or 0) != 1:
        _rollback_db_quietly(db)
        raise _auth_error("token_used", "Dieser Anmeldelink wurde bereits verwendet.")
    _commit_db(db)

    access_token = create_access_token(user_id=user.id)
    return AuthCallbackResponse(access_token=access_token, token_type="bearer", user=UserOut.model_validate(user))


@app.post("/api/auth/complete-signup", response_model=AuthCallbackResponse)
def auth_complete_signup(
    payload: CompleteSignupRequest,
    db: Session = Depends(get_db),
) -> AuthCallbackResponse:
    """Complete registration after e-mail verification and issue the authenticated session token."""
    now = datetime.utcnow()

    login_token = db.query(LoginToken).filter(LoginToken.token == payload.signup_token).first()
    if login_token is None:
        raise _auth_error("token_invalid", "Registrierungslink nicht gefunden.", status_code=status.HTTP_404_NOT_FOUND)

    if login_token.used_at is not None:
        raise _auth_error("token_used", "Dieser Registrierungslink wurde bereits verwendet.")

    if login_token.expires_at < now:
        raise _auth_error("token_expired", "Dieser Registrierungslink ist abgelaufen. Bitte fordere einen neuen Link an.")

    pseudonym = str(payload.pseudonym or "").strip()
    if (not pseudonym) or (not is_allowed_pseudonym(pseudonym)):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Ungültiges Pseudonym. Bitte wähle einen Vorschlag aus.",
        )

    # The e-mail embedded in the verified token is the source of truth.
    email_norm = str(login_token.email).lower()

    user = db.query(User).filter(User.email == email_norm).first()

    owner = db.query(User).filter(User.pseudonym == pseudonym).first()
    if owner is not None and (user is None or owner.id != user.id):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Dieses Pseudonym ist bereits vergeben. Bitte wähle einen anderen Vorschlag.",
        )

    if user is None:
        user = User(
            email=email_norm,
            pseudonym=pseudonym,
            plz=payload.plz,
            trust_level=1,
            is_admin=False,
        )
        db.add(user)
        db.flush()
    else:
        if payload.plz and not user.plz:
            user.plz = payload.plz

    updated = (
        db.query(LoginToken)
        .filter(LoginToken.id == login_token.id)
        .filter(LoginToken.used_at.is_(None))
        .filter(LoginToken.expires_at >= now)
        .update(
            {LoginToken.user_id: user.id, LoginToken.used_at: now},
            synchronize_session=False,
        )
    )
    if int(updated or 0) != 1:
        _rollback_db_quietly(db)
        raise _auth_error("token_used", "Dieser Registrierungslink wurde bereits verwendet.")
    _commit_db(db)

    access_token = create_access_token(user_id=user.id)
    user_out = UserOut.model_validate(user)
    return AuthCallbackResponse(
        access_token=access_token,
        token_type="bearer",
        user=user_out,
    )


@app.post("/api/auth/refresh", response_model=AuthCallbackResponse)
def auth_refresh(current_user: User = Depends(get_current_user)) -> AuthCallbackResponse:
    """Issue a fresh access token for an already authenticated user; no refresh cookie or separate refresh token is used."""
    access_token = create_access_token(user_id=current_user.id)
    user_out = UserOut.model_validate(current_user)
    return AuthCallbackResponse(
        access_token=access_token,
        token_type="bearer",
        user=user_out,
    )


@app.get("/api/me", response_model=UserOut)
def get_me(current_user: User = Depends(get_current_user)) -> UserOut:
    """Return the profile of the currently authenticated user."""
    return UserOut.model_validate(current_user)


@app.post("/api/me/delete-request", response_model=AccountDeleteStatusOut)
def me_delete_request(
    payload: AccountDeleteRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> AccountDeleteStatusOut:
    """Schedule, cancel, or immediately execute account anonymization according to the requested action."""
    action = (payload.action or "").strip().lower()
    now = datetime.utcnow()

    
    if getattr(current_user, "is_deleted", False) or getattr(current_user, "deleted_at", None) is not None:
        return AccountDeleteStatusOut(status="deleted", scheduled_for=None)

    if action == "schedule":
        # idempotent
        scheduled_for = getattr(current_user, "deletion_scheduled_for", None)
        if scheduled_for is None:
            scheduled_for = _schedule_account_delete(db, current_user, now=now)
            _commit_db(db)
            db.refresh(current_user)
        return AccountDeleteStatusOut(status="scheduled", scheduled_for=scheduled_for)

    if action == "cancel":
        _cancel_account_delete(db, current_user)
        _commit_db(db)
        db.refresh(current_user)
        return AccountDeleteStatusOut(status="none", scheduled_for=None)

    if action == "delete_now":
        # Immediate deletion bypasses the grace period and is irreversible.
        _anonymize_and_deactivate_user(db, current_user, now=now, reason="delete_now")
        _commit_db(db)
        return AccountDeleteStatusOut(status="deleted", scheduled_for=None)

    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail="Invalid action",
    )


@app.post("/api/me/pseudonym", response_model=UserOut)
def update_my_pseudonym(
    payload: UpdatePseudonymRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> UserOut:
    """Update the current user pseudonym, restricted to the generated allowlist and uniqueness rules."""
    pseudo = (payload.pseudonym or "").strip()
    if not is_allowed_pseudonym(pseudo):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Ungültiges Pseudonym. Bitte wähle einen Vorschlag aus.",
        )

    owner = db.query(User).filter(User.pseudonym == pseudo).first()
    if owner is not None and owner.id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Dieses Pseudonym ist bereits vergeben. Bitte würfle neu.",
        )

    current_user.pseudonym = pseudo
    db.add(current_user)
    _commit_db(db)
    db.refresh(current_user)
    return UserOut.model_validate(current_user)


@app.post("/api/me/plz", response_model=UserOut)
def update_my_plz(
    payload: UpdatePlzRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> UserOut:
    """Update the current user German postal code after schema-level validation."""
    current_user.plz = payload.plz
    db.add(current_user)
    _commit_db(db)
    db.refresh(current_user)
    return UserOut.model_validate(current_user)




@app.get("/api/me/review-stats", response_model=UserReviewStatsOut)
def get_me_review_stats(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> UserReviewStatsOut:
    """Return compact review/comment statistics used by the authenticated user dashboard."""
    user_id = current_user.id

    # User-authored comments.
    total_comments = (
        db.query(Comment)
        .filter(Comment.user_id == user_id)
        .count()
    )

    rows = (
        db.query(Comment.status, func.count(Comment.id))
        .filter(Comment.user_id == user_id)
        .group_by(Comment.status)
        .all()
    )
    comments_by_status: dict[str, int] = {
        status: count for status, count in rows
    }

    last_comment_created_at = None
    if total_comments:
        last_comment_created_at = (
            db.query(Comment.created_at)
            .filter(Comment.user_id == user_id)
            .order_by(Comment.created_at.desc())
            .limit(1)
            .scalar()
        )

    # User review activity.
    total_reviews = (
        db.query(Review)
        .filter(Review.reviewer_id == user_id)
        .count()
    )

    last_review_at = None
    if total_reviews:
        last_review_at = (
            db.query(Review.created_at)
            .filter(Review.reviewer_id == user_id)
            .order_by(Review.created_at.desc())
            .limit(1)
            .scalar()
        )

    reviews_since_last_comment = None
    if last_comment_created_at is not None and total_reviews:
        reviews_since_last_comment = (
            db.query(Review)
            .filter(
                Review.reviewer_id == user_id,
                Review.created_at > last_comment_created_at,
            )
            .count()
        )

    return UserReviewStatsOut(
        total_comments=total_comments,
        comments_by_status=comments_by_status,
        total_reviews=total_reviews,
        reviews_since_last_comment=reviews_since_last_comment,
        gold_hits=None,
        gold_misses=None,
        accuracy_ratio=None,
        last_review_at=last_review_at,
        last_comment_created_at=last_comment_created_at,
    )









# ---------------------------------------------------------------------------
# Voting API
# ---------------------------------------------------------------------------


@app.post(
    "/api/articles/{article_id}/vote",
    response_model=ArticleVoteSummaryOut,
)
def vote_for_article(
    article_id: int,
    payload: ArticleVoteRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ArticleVoteSummaryOut:
    """Create or update the authenticated user vote for the current version of an article."""
    article = (
        db.query(Article)
        .filter(Article.id == article_id)
        .first()
    )
    if not article or not article.current_version:
        raise HTTPException(
            status_code=404,
            detail="Artikel oder aktuelle Version nicht gefunden",
        )

    # Respect a temporary voting freeze.
    if getattr(article, "vote_freeze", False):
        raise HTTPException(
            status_code=status.HTTP_423_LOCKED,
            detail="Voting für diesen Artikel ist vorübergehend gesperrt.",
        )

    if payload.main_vote not in ALLOWED_MAIN_VOTES:
        raise HTTPException(
            status_code=400,
            detail="Ungültiges main_vote-Emoji",
        )

    # Comment v2: emoji reactions/bookmarks use the separate article-reaction endpoint.
    
    flags = []

    now = datetime.utcnow()

    
    if settings.MAX_VOTES_PER_USER_PER_24H > 0:
        since_24h = now - timedelta(hours=24)
        votes_last_24h = (
            db.query(ArticleVote)
            .filter(
                ArticleVote.user_id == current_user.id,
                ArticleVote.created_at >= since_24h,
            )
            .count()
        )
        if votes_last_24h >= settings.MAX_VOTES_PER_USER_PER_24H:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=(
                    "Du hast das maximale Voting-Kontingent für die letzten 24 Stunden erreicht. "
                    "Bitte mache eine kurze Pause."
                ),
            )

    try:
        vote_confirm_delay_minutes = int(getattr(settings, "VOTE_CONFIRM_DELAY_MINUTES", 10) or 0)
    except Exception:
        vote_confirm_delay_minutes = 10
    confirm_immediately = vote_confirm_delay_minutes <= 0

    vote = (
        db.query(ArticleVote)
        .filter(
            ArticleVote.user_id == current_user.id,
            ArticleVote.article_id == article.id,
            ArticleVote.version_id == article.current_version.id,
        )
        .one_or_none()
    )

    if vote is None:
        vote = ArticleVote(
            user_id=current_user.id,
            article_id=article.id,
            version_id=article.current_version.id,
            main_vote=payload.main_vote,
            flags=flags,
            status="confirmed" if confirm_immediately else "pending",
            created_at=now,
            confirmed_at=now if confirm_immediately else None,
        )
        db.add(vote)
    else:
        vote.main_vote = payload.main_vote
        vote.flags = flags
        # Updating a vote restarts its confirmation delay; otherwise an old pending vote
        # could become confirmed immediately after being changed.
        vote.created_at = now
        vote.status = "confirmed" if confirm_immediately else "pending"
        vote.confirmed_at = now if confirm_immediately else None

    _commit_db(db)

    # Vote-spike detection and automatic freeze:
    # A large vote spike on one article within the configured window
    # activates `vote_freeze` and marks votes in that window
    # as `ignored_spike` so they do not enter public statistics.
    if (
        settings.VOTE_SPIKE_WINDOW_MINUTES > 0
        and settings.VOTE_SPIKE_THRESHOLD_PER_ARTICLE > 0
        and not getattr(article, "vote_freeze", False)
    ):
        window_start = now - timedelta(
            minutes=settings.VOTE_SPIKE_WINDOW_MINUTES,
        )
        window_end = now

        recent_count = (
            db.query(ArticleVote)
            .filter(
                ArticleVote.article_id == article.id,
                ArticleVote.created_at >= window_start,
            )
            .count()
        )

        if recent_count >= settings.VOTE_SPIKE_THRESHOLD_PER_ARTICLE:
            article.vote_freeze = True

            # Mark all votes in the detected spike window as `ignored_spike`
            # so they are excluded from aggregate statistics.
            votes_in_window = (
                db.query(ArticleVote)
                .filter(
                    ArticleVote.article_id == article.id,
                    ArticleVote.created_at >= window_start,
                )
                .all()
            )

            affected_user_ids: set[int] = set()
            for v in votes_in_window:
                if v.status != "ignored_spike":
                    v.status = "ignored_spike"
                    v.confirmed_at = None
                    affected_user_ids.add(v.user_id)
                    db.add(v)

            
            if settings.VOTE_SPIKE_LOG_USER_EVENTS:
                
                db.add(
                    UserEvent(
                        user_id=current_user.id,
                        event_type="vote_spike_detected",
                        payload={
                            "article_id": article.id,
                            "recent_votes": recent_count,
                            "window_start": window_start.isoformat(),
                            "window_end": window_end.isoformat(),
                            "window_minutes": settings.VOTE_SPIKE_WINDOW_MINUTES,
                        },
                    )
                )

                
                message = (
                    "Wegen Auffälligkeiten im Voting wurden deine Stimmen "
                    "im Zeitraum "
                    f"{window_start.isoformat()} bis {window_end.isoformat()} "
                    "für diesen Artikel nicht in die offizielle Statistik übernommen."
                )
                for uid in affected_user_ids:
                    db.add(
                        UserEvent(
                            user_id=uid,
                            event_type="vote_ignored_spike",
                            payload={
                                "article_id": article.id,
                                "window_start": window_start.isoformat(),
                                "window_end": window_end.isoformat(),
                                "message": message,
                            },
                        )
                    )

            db.add(article)
            _commit_db(db)

    _clear_public_runtime_caches()

    
    return _aggregate_votes_for_article(db, article.id)


@app.get(
    "/api/articles/{article_id}/votes/summary",
    response_model=ArticleVoteSummaryOut,
)
def get_article_votes_summary(
    article_id: int,
    db: Session = Depends(get_db),
) -> ArticleVoteSummaryOut:
    """Return aggregate article sentiment across all versions and for each version."""
    return _aggregate_votes_for_article(db, article_id)


@app.get(
    "/api/me/article-votes",
    response_model=List[PersonalArticleVoteOut],
)
def list_my_article_votes(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> List[PersonalArticleVoteOut]:
    """Return the authenticated user's personal main votes; reactions/bookmarks are exposed separately."""
    votes = (
        db.query(ArticleVote)
        .filter(ArticleVote.user_id == current_user.id)
        # The user should see their own submitted vote immediately in the UI.
        .filter(ArticleVote.status.in_(["confirmed", "pending", "ignored_spike"]))
        .all()
    )

    # Reactions. come from `ArticleReaction`; legacy `ArticleVote.flags` is read only for compatibility unioning.
    result: List[PersonalArticleVoteOut] = []
    for v in votes:
        result.append(
            PersonalArticleVoteOut(
                article_id=v.article_id,
                version_id=v.version_id,
                main_vote=v.main_vote,
            )
        )
    return result
    

# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------

def _comment_word_count(c: Comment) -> int:
    try:
        t = str(getattr(c, "proposal_text", "") or "")
        e = str(getattr(c, "explanation", "") or "")
        s = (t + "\n" + e).strip()
        if not s:
            return 0
        return len([w for w in re.split(r"\s+", s) if w])
    except Exception:
        return 0


def _comment_nextdraft_bucket(word_count: int) -> str:
    try:
        small_max = int(getattr(settings, "COMMENT_NEXTDRAFT_WORDS_SMALL_MAX", 40) or 40)
        med_max = int(getattr(settings, "COMMENT_NEXTDRAFT_WORDS_MEDIUM_MAX", 120) or 120)
    except Exception:
        small_max = 40
        med_max = 120
    if word_count <= small_max:
        return "small"
    if word_count <= med_max:
        return "medium"
    return "large"


def _comment_nextdraft_thresholds(bucket: str) -> tuple[int, float]:
    b = (bucket or "").strip().lower()
    if b == "small":
        return (
            int(
                getattr(
                    settings,
                    "COMMENT_POLICY_WEIGHTED_MIN_SUPPORT_SMALL",
                    getattr(settings, "COMMENT_NEXTDRAFT_MIN_VOTES_SMALL", 10),
                )
                or 10
            ),
            float(
                getattr(
                    settings,
                    "COMMENT_POLICY_WEIGHTED_APPROVAL_THRESHOLD_SMALL",
                    getattr(settings, "COMMENT_NEXTDRAFT_MIN_APPROVAL_PERCENT_SMALL", 90.0),
                )
                or 90.0
            ),
        )
    if b == "medium":
        return (
            int(
                getattr(
                    settings,
                    "COMMENT_POLICY_WEIGHTED_MIN_SUPPORT_MEDIUM",
                    getattr(settings, "COMMENT_NEXTDRAFT_MIN_VOTES_MEDIUM", 10),
                )
                or 10
            ),
            float(
                getattr(
                    settings,
                    "COMMENT_POLICY_WEIGHTED_APPROVAL_THRESHOLD_MEDIUM",
                    getattr(settings, "COMMENT_NEXTDRAFT_MIN_APPROVAL_PERCENT_MEDIUM", 90.0),
                )
                or 90.0
            ),
        )
    return (
        int(
            getattr(
                settings,
                "COMMENT_POLICY_WEIGHTED_MIN_SUPPORT_LARGE",
                getattr(settings, "COMMENT_NEXTDRAFT_MIN_VOTES_LARGE", 10),
            )
            or 10
        ),
        float(
            getattr(
                settings,
                "COMMENT_POLICY_WEIGHTED_APPROVAL_THRESHOLD_LARGE",
                getattr(settings, "COMMENT_NEXTDRAFT_MIN_APPROVAL_PERCENT_LARGE", 90.0),
            )
            or 90.0
        ),
    )


def _comment_nextdraft_disapproval_cap(bucket: str) -> float:
    b = (bucket or "").strip().lower()
    if b == "small":
        return float(
            getattr(
                settings,
                "COMMENT_POLICY_WEIGHTED_DISAPPROVAL_CAP_SMALL",
                getattr(settings, "COMMENT_NEXTDRAFT_MAX_DISAPPROVAL_PERCENT_SMALL", 10.0),
            )
            or 10.0
        )
    if b == "medium":
        return float(
            getattr(
                settings,
                "COMMENT_POLICY_WEIGHTED_DISAPPROVAL_CAP_MEDIUM",
                getattr(settings, "COMMENT_NEXTDRAFT_MAX_DISAPPROVAL_PERCENT_MEDIUM", 10.0),
            )
            or 10.0
        )
    return float(
        getattr(
            settings,
            "COMMENT_POLICY_WEIGHTED_DISAPPROVAL_CAP_LARGE",
            getattr(settings, "COMMENT_NEXTDRAFT_MAX_DISAPPROVAL_PERCENT_LARGE", 10.0),
        )
        or 10.0
    )


def _precedence_time_epoch(v: Any) -> float:
    try:
        if isinstance(v, datetime):
            return float(v.timestamp())
        s = str(v or "").strip()
        if not s:
            return 0.0
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return float(dt.timestamp())
    except Exception:
        return 0.0


def _precedence_key_for_cid(cid: int, precedence_by_cid: dict[int, dict[str, Any]]) -> tuple[int, int, float, int]:
    rec = precedence_by_cid.get(int(cid), {}) if isinstance(precedence_by_cid, dict) else {}
    compat_raw = rec.get("compatible_with_baseline")
    if compat_raw is True:
        compat_rank = 2
    elif compat_raw is None:
        compat_rank = 1
    else:
        compat_rank = 0
    weighted_score = int(rec.get("weighted_score") or 0)
    primary_time_epoch = _precedence_time_epoch(rec.get("primary_time"))
    # Earlier time wins: invert epoch so earlier timestamps sort higher with >= comparison.
    primary_time_rank = -float(primary_time_epoch)
    # Tie-break: lower cid wins (stable/deterministic).
    cid_tie = -int(cid)
    # Final precedence tuple: compatible_with_baseline, weighted_score, earlier_time, cid.
    return (int(compat_rank), int(weighted_score), float(primary_time_rank), int(cid_tie))


def _choose_overlap_winner(
    a: dict[str, Any],
    b: dict[str, Any],
    precedence_by_cid: dict[int, dict[str, Any]],
    precedence_enabled: bool,
    precedence_mode: str,
) -> dict[str, Any]:
    if precedence_enabled and precedence_mode == "global_comment_precedence":
        cid_a = int(a.get("cid") or 0)
        cid_b = int(b.get("cid") or 0)
        ka = _precedence_key_for_cid(cid_a, precedence_by_cid)
        kb = _precedence_key_for_cid(cid_b, precedence_by_cid)
        return a if ka >= kb else b
    # Explicit fallback mode.
    return a if (int(a["cid"]), str(a["part_id"])) <= (int(b["cid"]), str(b["part_id"])) else b


def _vote_weight_for_user(u: User) -> int:
    """Return the shared trust-based vote weight used for article and comment qualification, with a minimum weight of one."""
    try:
        w = int(getattr(u, "trust_level", 1) or 1)
    except Exception:
        w = 1
    if w < 1:
        w = 1
    return w



# ---------------------------------------------------------------------------
# Reactions. API (emoji/bookmark), separate from votes
# ---------------------------------------------------------------------------

@app.post('/api/articles/{article_id}/reactions/toggle', response_model=ReactionToggleOut)
def toggle_article_reaction(
    article_id: int,
    payload: ReactionToggleRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ReactionToggleOut:
    article = db.query(Article).filter(Article.id == article_id).first()
    if not article or not article.current_version:
        raise HTTPException(status_code=404, detail='Artikel oder aktuelle Version nicht gefunden')
    emo = str(payload.emoji)
    if emo not in ALLOWED_FLAG_EMOJIS:
        raise HTTPException(status_code=400, detail='Ungültiges Emoji')

    existing = (
        db.query(ArticleReaction)
        .filter(ArticleReaction.article_id == article.id)
        .filter(ArticleReaction.version_id == article.current_version.id)
        .filter(ArticleReaction.user_id == current_user.id)
        .filter(ArticleReaction.reaction_emoji == emo)
        .one_or_none()
    )

    active = False
    if existing is None:
        db.add(ArticleReaction(article_id=article.id, version_id=article.current_version.id, user_id=current_user.id, reaction_emoji=emo, created_at=datetime.utcnow()))
        active = True
    else:
        db.delete(existing)
        active = False

    _commit_db(db)
    _clear_public_runtime_caches()

    # Return the current user reaction set for this version.
    emojis = [
        str(r.reaction_emoji)
        for r in (
            db.query(ArticleReaction)
            .filter(ArticleReaction.article_id == article.id)
            .filter(ArticleReaction.version_id == article.current_version.id)
            .filter(ArticleReaction.user_id == current_user.id)
            .all()
        )
    ]
    return ReactionToggleOut(active=bool(active), emojis=emojis)


@app.get('/api/me/article-reactions', response_model=list[MyArticleReactionsOut])
def list_my_article_reactions(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[MyArticleReactionsOut]:

    rows = (
        db.query(ArticleReaction)
        .filter(ArticleReaction.user_id == current_user.id)
        .all()
    )
    by_key: dict[tuple[int,int], list[str]] = {}
    for r in rows:
        key = (int(r.article_id), int(r.version_id))
        by_key.setdefault(key, []).append(str(r.reaction_emoji))
    out: list[MyArticleReactionsOut] = []
    for (aid, vid), emojis in by_key.items():
        out.append(MyArticleReactionsOut(article_id=aid, version_id=vid, emojis=sorted(list(set(emojis)))))
    out.sort(key=lambda x: (x.article_id, x.version_id))
    return out

 
@app.get('/api/me/comment-reactions', response_model=list[MyCommentReactionsOut])
def list_my_comment_reactions(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[MyCommentReactionsOut]:
    
    rows = (
        db.query(CommentReaction, Comment)
        .join(Comment, Comment.id == CommentReaction.comment_id)
        .filter(CommentReaction.user_id == current_user.id)
        .all()
    )

    by_comment: dict[int, dict[str, Any]] = {}
    for rr, c in rows:
        try:
            cid = int(rr.comment_id)
        except Exception:
            continue
        rec = by_comment.setdefault(
            cid,
            {
                "comment_id": cid,
                "article_id": int(getattr(c, "article_id", 0) or 0),
                "version_id": int(getattr(c, "version_id", 0) or 0),
                "emojis": [],
            },
        )
        try:
            rec["emojis"].append(str(getattr(rr, "reaction_emoji", "")))
        except Exception:
            pass

    out: list[MyCommentReactionsOut] = []
    for cid, rec in by_comment.items():
        emojis = sorted(list(set([e for e in (rec.get("emojis") or []) if e])))
        out.append(
            MyCommentReactionsOut(
                comment_id=int(rec.get("comment_id") or cid),
                article_id=int(rec.get("article_id") or 0),
                version_id=int(rec.get("version_id") or 0),
                emojis=emojis,
            )
        )
    out.sort(key=lambda x: (x.article_id, x.version_id, x.comment_id))
    return out


@app.post('/api/comments/{comment_id}/reactions/toggle', response_model=ReactionToggleOut)
def toggle_comment_reaction(
    comment_id: int,
    payload: ReactionToggleRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ReactionToggleOut:
    comment = db.query(Comment).filter(Comment.id == comment_id).one_or_none()
    if comment is None:
        raise HTTPException(status_code=404, detail='Kommentar nicht gefunden')
    st = str(getattr(comment, 'status', '') or '').strip().lower()
    if st not in {'review', 'veröffentlicht'}:
        raise HTTPException(status_code=409, detail='Auf diesen Kommentar kann derzeit nicht reagiert werden')
    emo = str(payload.emoji)
    if emo not in ALLOWED_FLAG_EMOJIS:
        raise HTTPException(status_code=400, detail='Ungültiges Emoji')

    existing = (
        db.query(CommentReaction)
        .filter(CommentReaction.comment_id == comment.id)
        .filter(CommentReaction.user_id == current_user.id)
        .filter(CommentReaction.reaction_emoji == emo)
        .one_or_none()
    )
    active = False
    if existing is None:
        db.add(CommentReaction(comment_id=comment.id, user_id=current_user.id, reaction_emoji=emo, created_at=datetime.utcnow()))
        active = True
    else:
        db.delete(existing)
        active = False

    # Compatibility writer: keep legacy `vote.flags` synchronized when a vote row exists.
    v = (
        db.query(CommentVote)
        .filter(CommentVote.user_id == current_user.id)
        .filter(CommentVote.comment_id == comment.id)
        .one_or_none()
    )
    if v is not None:
        fl = list(v.flags or [])
        if active and emo not in fl:
            fl.append(emo)
        if (not active) and emo in fl:
            fl = [x for x in fl if x != emo]
        v.flags = _normalize_flags(fl)
        db.add(v)

    _commit_db(db)
    _clear_public_runtime_caches()

    emojis = [
        str(r.reaction_emoji)
        for r in (
            db.query(CommentReaction)
            .filter(CommentReaction.comment_id == comment.id)
            .filter(CommentReaction.user_id == current_user.id)
            .all()
        )
    ]
    return ReactionToggleOut(active=bool(active), emojis=emojis)


def _comment_weight_for_user(u: User) -> int:
    """Alias the shared vote-weight policy for comment Next-Draft/approval calculations."""
    return _vote_weight_for_user(u)


def _norm_match_text(s: str) -> str:
    """Normalize text for deterministic matching by normalizing line endings, collapsing whitespace, and trimming edges."""
    s = (s or "").replace("\r\n", "\n").replace("\r", "\n")
    s = re.sub(r"\s+", " ", s, flags=re.UNICODE).strip()
    return s


def _minimd_blocks_map(minimd: str) -> dict[str, str]:
    """Extract MiniMD block bodies between start/end markers; fall back to a single `_all` block when markers are absent."""
    txt = minimd or ""
    start_tpl = str(getattr(settings, "MINIMD_BLOCK_START_TEMPLATE", "### start: {name} ###") or "### start: {name} ###").rstrip("\n")
    start_prefix = start_tpl.split("{name}")[0]
    if not start_prefix or start_prefix not in txt:
        return {"_all": txt}

    t = txt.replace("\r\n", "\n").replace("\r", "\n")
    lines = t.split("\n")
    end_tpl = str(getattr(settings, "MINIMD_BLOCK_END_TEMPLATE", "### end: {name} ###") or "### end: {name} ###").rstrip("\n")
    start_pat = re.escape(start_tpl).replace(r"\{name\}", r"(?P<name>[^#\n]+)")
    end_pat = re.escape(end_tpl).replace(r"\{name\}", r"(?P<name>[^#\n]+)")
    start_re = re.compile(r"^\s*" + start_pat + r"\s*$", re.IGNORECASE)
    end_re = re.compile(r"^\s*" + end_pat + r"\s*$", re.IGNORECASE)

    raw_blocks: dict[str, str] = {}
    cur: str | None = None
    buf: list[str] = []
    block_order = _minimd_block_order()
    top_level_names = {str(bn).strip().lower() for bn in block_order}

    def flush() -> None:
        nonlocal cur, buf
        if not cur:
            return
        body = "\n".join(buf)
        raw_blocks[cur] = body
        buf = []

    for line in lines:
        mS = start_re.match(line)
        if mS:
            start_key = str(mS.group("name") or "").strip().lower()
            if start_key in top_level_names:
                if cur is None:
                    cur = start_key
                    buf = []
                    continue
                if start_key != cur:
                    # Compatibility repair for a missing end marker on the previous top-level block:
                    
                    flush()
                    cur = start_key
                    buf = []
                    continue
            if cur:
                # Nested scopes such as storybox/details/pre/table belong to the current
                
                buf.append(line)
            continue
        mE = end_re.match(line)
        if mE:
            end_key = str(mE.group("name") or "").strip().lower()
            if cur and end_key == cur:
                flush()
                cur = None
                continue
            if cur:
                buf.append(line)
            continue
        if cur:
            buf.append(line)
    flush()

    out: dict[str, str] = {"_all": txt}
    for bn in block_order:
        key = str(bn).strip().lower()
        if key in raw_blocks:
            out[bn] = str(raw_blocks[key])
    return out

def _sha256_hex_text(s: str) -> str:
    return hashlib.sha256((s or "").encode("utf-8")).hexdigest()


_COMMENT_MODES_V2 = {"change", "new_article", "delete_article"}
_NEW_ARTICLE_INSERT_AFTER_META_LABEL = "Einfügen nach folgender Artikel-Kennung"


def _normalize_comment_mode(raw: object) -> str:
    mode = str(raw or "change").strip() or "change"
    if mode not in _COMMENT_MODES_V2:
        raise HTTPException(status_code=422, detail="Ungültiger Kommentar-Modus.")
    return mode


def _require_comment_feature_trust(user: User, mode: str) -> None:
    trust = int(getattr(user, "trust_level", 0) or 0)
    if mode == "new_article":
        required = int(getattr(settings, "COMMENT_NEW_ARTICLE_MIN_TRUST", 20) or 20)
        if trust < required:
            raise HTTPException(status_code=403, detail=f"Neue Artikel sind ab Trust-Level {required} verfügbar.")
    elif mode == "delete_article":
        required = int(getattr(settings, "COMMENT_DELETE_ARTICLE_MIN_TRUST", 20) or 20)
        if trust < required:
            raise HTTPException(status_code=403, detail=f"Artikel-Löschanträge sind ab Trust-Level {required} verfügbar.")


def _new_article_template_minimd() -> str:
    lines: list[str] = []
    lines.append(_mm_block_start("meta"))
    for label, value in [
        ("Artikel-Kennung", ""),
        ("Artikel-Titel", ""),
        ("Artikel-Kurztitel", ""),
        ("Artikel im Inhaltsverzeichnis", "nein"),
        (_NEW_ARTICLE_INSERT_AFTER_META_LABEL, ""),
    ]:
        lines.append(_meta_value_line(label, value))
    lines.append(_mm_block_end("meta"))
    lines.append("")
    for block_key in _minimd_block_order():
        if str(block_key) == "meta":
            continue
        lines.append(_mm_block_start(str(block_key)))
        lines.append(_mm_block_end(str(block_key)))
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _parse_minimd_meta_lines(meta_text: str) -> dict[str, str]:
    rx = re.compile(str(getattr(settings, "MINIMD_META_LINE_RE", r"^\$ (?P<label>[^$:\n]+): \$ ?(?P<value>.*)$") or r"^\$ (?P<label>[^$:\n]+): \$ ?(?P<value>.*)$"))
    out: dict[str, str] = {}
    for line in str(meta_text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        m = rx.match(line.strip())
        if not m:
            continue
        label = str(m.group("label") or "").strip()
        value = str(m.group("value") or "").strip()
        if label:
            out[label] = value
    return out


def _extract_new_article_meta(minimd: str) -> dict[str, str]:
    blocks = _minimd_blocks_map(str(minimd or ""))
    meta = _parse_minimd_meta_lines(str(blocks.get("meta") or ""))
    required = ["Artikel-Kennung", "Artikel-Titel", _NEW_ARTICLE_INSERT_AFTER_META_LABEL]
    missing = [label for label in required if not str(meta.get(label) or "").strip()]
    if missing:
        raise HTTPException(
            status_code=422,
            detail="Neuer Artikel unvollständig: " + ", ".join(missing),
        )
    return meta


def _meta_bool_value(value: object) -> bool:
    s = str(value or "").strip().lower()
    return s in {"1", "true", "yes", "ja", "j", "on", "anzeigen", "sichtbar"}


def _derive_article_type_from_public_code(public_code: str) -> str:
    s = str(public_code or "").strip().lower()
    if re.match(r"^(vo|verordnung)\b", s):
        return "verordnung"
    return "gesetz"


def _slugify_article(public_code: str, title: str) -> str:
    raw = f"{public_code} {title}".strip().lower()
    repl = {
        "ä": "ae",
        "ö": "oe",
        "ü": "ue",
        "ß": "ss",
    }
    for a, b in repl.items():
        raw = raw.replace(a, b)
    raw = re.sub(r"[^a-z0-9]+", "-", raw).strip("-")
    return raw or "artikel"


def _unique_article_slug(db: Session, public_code: str, title: str) -> str:
    base = _slugify_article(public_code, title)
    slug = base
    i = 2
    while db.query(Article).filter(Article.slug == slug).first() is not None:
        slug = f"{base}-{i}"
        i += 1
    return slug


def _find_article_by_public_code(db: Session, public_code: str) -> Article:
    code_value = str(public_code or "").strip()
    article = db.query(Article).filter(Article.public_code == code_value).one_or_none()
    if article is None:
        raise HTTPException(status_code=422, detail=f"Einfügeposition nicht gefunden: {code_value}")
    return article


def _shift_sort_order_after_article(db: Session, article: Article) -> int:
    base_order = int(getattr(article, "sort_order", 0) or 0)
    (
        db.query(Article)
        .filter(Article.sort_order > base_order)
        .update({Article.sort_order: Article.sort_order + 1}, synchronize_session=False)
    )
    return base_order + 1


def _new_article_baseline_blocks() -> dict[str, str]:
    blocks = {str(block_key): "" for block_key in _minimd_block_order()}
    # Comment-v2 `meta` always contains the complete metadata form, so
    # `new_article` patches change values behind already-protected metadata labels.
    # existing `$ …: $` metadata labels rather than inserting new labels.
    blocks["meta"] = "\n".join([
        _meta_value_line("Artikel-Kennung", ""),
        _meta_value_line("Artikel-Titel", ""),
        _meta_value_line("Artikel-Kurztitel", ""),
        _meta_value_line("Artikel im Inhaltsverzeichnis", "nein"),
        _meta_value_line(_NEW_ARTICLE_INSERT_AFTER_META_LABEL, ""),
    ])
    return blocks


def _line_spans(text_value: str) -> list[tuple[int, int, str]]:
    src = str(text_value or "")
    spans: list[tuple[int, int, str]] = []
    pos = 0
    for chunk in src.splitlines(keepends=True):
        line = chunk.rstrip("\n").rstrip("\r")
        start = pos
        end = pos + len(line)
        spans.append((int(start), int(end), line))
        pos += len(chunk)
    return spans


def _build_meta_patch_parts_from_baseline(
    *,
    old_text: str,
    new_text: str,
    block_key: str,
) -> list[dict[str, Any]]:
    old_lines = _line_spans(old_text)
    new_lines = _line_spans(new_text)
    old_by_label: dict[str, tuple[int, int, str]] = {}
    new_by_label: dict[str, str] = {}
    rx = str(getattr(settings, "MINIMD_META_LINE_RE", r"^\$ (?P<label>[^$:\n]+): \$ ?(?P<value>.*)$") or r"^\$ (?P<label>[^$:\n]+): \$ ?(?P<value>.*)$")
    line_re = re.compile(rx)

    for start, end, line in old_lines:
        m = line_re.match(str(line or ""))
        if not m:
            continue
        old_by_label[str(m.group("label") or "").strip()] = (int(start), int(end), str(line or ""))
    for _start, _end, line in new_lines:
        m = line_re.match(str(line or ""))
        if not m:
            continue
        new_by_label[str(m.group("label") or "").strip()] = str(line or "")

    parts: list[dict[str, Any]] = []
    for label, old_row in old_by_label.items():
        new_line = str(new_by_label.get(label) or "")
        old_line = str(old_row[2] or "")
        if not new_line or new_line == old_line:
            continue
        parts.append(
            {
                "part_id": f"p_{block_key}_{len(parts) + 1}",
                "block_key": str(block_key),
                "old_text": old_line,
                "new_text": new_line,
                "sel_start": int(old_row[0]),
                "sel_end": int(old_row[1]),
                "baseline_hash": _sha256_hex_text(old_text),
            }
        )
    return parts


def _build_new_article_patch_payload(
    minimd: str,
    *,
    baseline_blocks: dict[str, str],
    version_id: int = 0,
) -> dict[str, Any]:
    block_map = _minimd_blocks_map(str(minimd or ""))
    parts: list[dict[str, Any]] = []
    for block_key in _minimd_block_order():
        key = str(block_key)
        old_text = str((baseline_blocks or {}).get(key) or "")
        new_text = str(block_map.get(key) or "")
        if old_text == new_text:
            continue
        if key == "meta":
            parts.extend(
                _build_meta_patch_parts_from_baseline(
                    old_text=old_text,
                    new_text=new_text,
                    block_key=key,
                )
            )
            continue
        parts.append(
            {
                "part_id": f"p_{key}",
                "block_key": key,
                "old_text": old_text,
                "new_text": new_text,
                "sel_start": 0,
                "sel_end": len(old_text),
                "baseline_hash": _sha256_hex_text(old_text),
            }
        )
    if not parts:
        raise HTTPException(status_code=422, detail="Neuer Artikel enthält keine speicherbaren Inhalte.")
    return {
        "version": 2,
        "base_version_id": int(version_id or 0),
        "exact_selection": True,
        "editor": {"kind": "new_article", "synthetic": True},
        "parts": parts,
    }


def _build_delete_article_patch_payload(version: ArticleVersion) -> dict[str, Any]:
    _minimd, block_map = _article_version_minimd_bundle(version)
    parts: list[dict[str, Any]] = []
    for block_key in ("kurzinfo", "story", "einleitung", "juristisch", "juristisch2", "anmerkung"):
        old_text = str(block_map.get(block_key) or "")
        if not old_text.strip():
            continue
        parts.append(
            {
                "part_id": f"p_{block_key}_delete",
                "block_key": block_key,
                "old_text": old_text,
                "new_text": "",
                "sel_start": 0,
                "sel_end": len(old_text),
                "baseline_hash": _sha256_hex_text(old_text),
            }
        )
    if not parts:
        raise HTTPException(status_code=422, detail="Artikel enthält keine löschbaren Inhaltsblöcke.")
    return {
        "version": 2,
        "base_version_id": int(getattr(version, "id", 0) or 0),
        "exact_selection": True,
        "editor": {"kind": "delete_article", "synthetic": True},
        "parts": parts,
    }


def _validate_exact_selection_patch_payload(
    *,
    patch_payload: dict | None,
    version: "ArticleVersion",
) -> dict:
    """Strict exact-selection validation for new/edited patches via indiff.py."""
    if not isinstance(patch_payload, dict):
        raise HTTPException(status_code=400, detail="Patch invalid: missing patch_payload.")
    parts = patch_payload.get("parts")
    if not isinstance(parts, list) or not parts:
        raise HTTPException(status_code=400, detail="Patch invalid: missing parts.")

    minimd, _ = article_version_to_minimd(version)
    block_map = _minimd_blocks_map(minimd)
    block_map.pop("_all", None)

    comment_input = code.PersistedCommentInput(
        cid=0,
        parts=[
            code.PersistedCommentPartInput(
                part_id=str((p or {}).get("part_id") or f"p{idx+1}"),
                block_key=str((p or {}).get("block_key") or (p or {}).get("block_id") or ""),
                old_text=str((p or {}).get("old_text") or ""),
                new_text=str((p or {}).get("new_text") or ""),
                sel_start=((int((p or {}).get("sel_start")) if (p or {}).get("sel_start") is not None else None)),
                sel_end=((int((p or {}).get("sel_end")) if (p or {}).get("sel_end") is not None else None)),
                baseline_hash=str((p or {}).get("baseline_hash") or ""),
            )
            for idx, p in enumerate(parts)
            if isinstance(p, dict)
        ],
    )

    exact = code.validate_exact_selection_patch_payload(
        baseline_old_minimd_by_block={str(k): str(v or "") for k, v in dict(block_map or {}).items()},
        comment=comment_input,
        fallback_block_key="juristisch",
        require_baseline_hash=True,
        require_selection=True,
    )
    if not bool(exact.ok):
        first = dict((exact.errors or [{}])[0] or {})
        code_ = str(first.get("code") or "invalid_patch")
        detail_map = {
            "empty_patch_payload": "Patch invalid: missing parts.",
            "unknown_block_key": f"Patch invalid: unknown block_key '{first.get('block_key')}'.",
            "missing_selection": "Patch invalid: missing sel_start/sel_end. Recreate selection.",
            "selection_out_of_bounds": f"Patch invalid: part {first.get('part_id')} selection offsets out of bounds.",
            "selection_text_mismatch": "Patch invalid: selection text mismatch.",
            "missing_baseline_hash": f"Patch invalid: part {first.get('part_id')} missing baseline_hash.",
            "baseline_hash_mismatch": "Baseline changed; recreate selection.",
        }
        raise HTTPException(status_code=400, detail=detail_map.get(code_, f"Patch invalid: {code_}."))

    def _local_old_change_window(old_text: str, new_text: str) -> tuple[int, int]:
        if str(old_text or "") == str(new_text or ""):
            return 0, 0
        sm = difflib.SequenceMatcher(a=str(old_text or ""), b=str(new_text or ""), autojunk=False)
        start = None
        end_old = 0
        for tag, i1, i2, _j1, _j2 in sm.get_opcodes():
            if tag == "equal":
                continue
            if start is None:
                start = int(i1)
            end_old = int(i2)
        if start is None:
            return 0, 0
        return int(start), int(end_old)

    for part in list(exact.normalized_comment.parts or []):
        block_key = str(getattr(part, "block_key", "") or "")
        block_text = str((block_map or {}).get(block_key) or "")
        old_text = str(getattr(part, "old_text", "") or "")
        new_text = str(getattr(part, "new_text", "") or "")
        if not block_text or not old_text or old_text == new_text:
            continue
        if part.sel_start is None or part.sel_end is None:
            continue
        sel_start = int(part.sel_start)
        sel_end = int(part.sel_end)
        if sel_start != 0 or sel_end != len(block_text):
            continue
        local_start, local_end = _local_old_change_window(old_text, new_text)
        if local_start == 0 and local_end == len(old_text):
            continue
        raise HTTPException(
            status_code=400,
            detail=f"Patch invalid: part {getattr(part, 'part_id', '')} uses full-block selection for local change. Recreate selection.",
        )

    out_payload = dict(patch_payload)
    out_payload["version"] = 2
    out_payload["base_version_id"] = int(getattr(version, "id", 0) or 0)
    out_payload["exact_selection"] = True
    out_payload["parts"] = [asdict(p) for p in list(exact.normalized_comment.parts or [])]
    return out_payload



def _comment_conflict_info(db: Session, comment: Comment) -> tuple[int, list[str]]:
    """Perform a conservative deterministic conflict check by requiring every normalized `old_text` fragment to be present in its targeted base block; return conflict count and affected parts."""
    try:
        payload = comment.patch_payload if isinstance(getattr(comment, "patch_payload", None), dict) else None
        parts = payload.get("parts") if isinstance(payload, dict) else None
        if not isinstance(parts, list) or not parts:
            return 0, []

        v = (
            db.query(ArticleVersion)
            .filter(ArticleVersion.id == comment.version_id)
            .one_or_none()
        )
        if v is None:
            return 0, []

        minimd, _ = article_version_to_minimd(v)
        blocks = _minimd_blocks_map(minimd)
        all_norm = _norm_match_text(blocks.get("_all", ""))

        conflicts: list[str] = []
        for idx, p in enumerate(parts):
            if not isinstance(p, dict):
                continue
            old_text = p.get("old_text")
            if not isinstance(old_text, str) or not old_text.strip():
                continue

            pid = str(p.get("part_id") or f"p{idx+1}")
            bn = p.get("block_key") or p.get("block_id")
            target = None
            if isinstance(bn, str) and bn in blocks:
                target = _norm_match_text(blocks.get(bn, ""))
            if not target:
                target = all_norm

            if _norm_match_text(old_text) not in target:
                conflicts.append(pid)

        return len(conflicts), conflicts
    except Exception:
        return 0, []


def _aggregate_votes_for_comment(db: Session, comment: Comment) -> CommentVoteSummaryOut:
    if not comment:
        raise HTTPException(status_code=404, detail="Kommentar nicht gefunden")

    rows = (
        db.query(CommentVote, User)
        .join(User, User.id == CommentVote.user_id)
        .filter(CommentVote.comment_id == comment.id)
        .filter(CommentVote.status.in_(["confirmed", "pending", "ignored_spike"]))
        .all()
    )

    from collections import Counter
    # Raw values exposed to the UI.
    main_counts_raw = Counter()
    total_votes = 0

    
    main_counts_weighted = Counter()
    effective_total = 0

    for v, u in rows:
        total_votes += 1
        w = _comment_weight_for_user(u)
        effective_total += w

        mv = str(getattr(v, "main_vote", "") or "")
        if mv:
            main_counts_raw[mv] += 1
            main_counts_weighted[mv] += w

    
    reactions = Counter()
    try:
        erows = (
            db.query(CommentReaction.reaction_emoji, func.count(CommentReaction.id))
            .filter(CommentReaction.comment_id == comment.id)
            .group_by(CommentReaction.reaction_emoji)
            .all()
        )
        for emo, cnt in erows:
            es = str(emo)
            if es:
                reactions[es] += int(cnt or 0)
    except Exception:
        
        reactions = Counter()

    
    reviews_total = 0
    reviews_positive = 0
    try:
        rrows = (
            db.query(Review.recommendation, func.count(Review.id))
            .filter(Review.comment_id == comment.id)
            .filter(Review.visible_to_public == True)  # noqa: E712
            .group_by(Review.recommendation)
            .all()
        )
        for rec, cnt in rrows:
            n = int(cnt or 0)
            reviews_total += n
            key = (str(rec) if rec is not None else "").strip().lower()
            if key in _APPROVE_RECS:
                reviews_positive += n
    except Exception:
        reviews_total = 0
        reviews_positive = 0

    # Raw approval exposed to the UI.
    pos = int(main_counts_raw.get("✅", 0) + main_counts_raw.get("🟢", 0))
    neu = int(main_counts_raw.get("🟡", 0))
    neg = int(main_counts_raw.get("🟠", 0) + main_counts_raw.get("🔴", 0))
    denom = pos + neu + neg
    approval_raw = (100.0 * float(pos) / float(denom)) if denom > 0 else None

    # Weighted qualification signal.
    w_pos = int(main_counts_weighted.get("✅", 0) + main_counts_weighted.get("🟢", 0))
    w_neu = int(main_counts_weighted.get("🟡", 0))
    w_neg = int(main_counts_weighted.get("🟠", 0) + main_counts_weighted.get("🔴", 0))
    w_denom = w_pos + w_neu + w_neg
    approval_weighted = (100.0 * float(w_pos) / float(w_denom)) if w_denom > 0 else None

    wc = _comment_word_count(comment)
    bucket = _comment_nextdraft_bucket(wc)
    min_votes, min_ap = _comment_nextdraft_thresholds(bucket)
    _disapproval_cap = _comment_nextdraft_disapproval_cap(bucket)
    # B2: exclude deterministic server-side conflicts.
    conflict_count, conflict_parts = _comment_conflict_info(db, comment)

    disapproval_weighted = (100.0 * float(w_neg) / float(w_denom)) if w_denom > 0 else None
    qualified_votes = (
        (approval_weighted is not None)
        and (approval_weighted >= float(min_ap))
        and (effective_total >= int(min_votes))
        and (disapproval_weighted is not None)
        and (disapproval_weighted <= float(_disapproval_cap))
    )

    # Strict Next-Draft eligibility: weighted thresholds + conflict-free.
    qualified = bool(qualified_votes and (conflict_count == 0))

    return CommentVoteSummaryOut(
        comment_id=comment.id,

        # Legacy/raw compatibility fields.
        total_votes=int(total_votes),
        votes_by_main=dict(main_counts_raw),

        # Explicit B1 fields.
        raw_positive=int(pos),
        raw_neutral=int(neu),
        raw_negative=int(neg),
        raw_total=int(total_votes),
        raw_approval_percent=approval_raw,

        weighted_positive=int(w_pos),
        weighted_neutral=int(w_neu),
        weighted_negative=int(w_neg),
        weighted_total=int(effective_total),
        weighted_approval_percent=approval_weighted,
        weighted_votes_by_main=dict(main_counts_weighted),

        
        approval_percent=approval_raw,

        reactions_count=dict(reactions),
        
        reviews_total=int(reviews_total),
        reviews_positive=int(reviews_positive),
        qualified_for_next_release=bool(qualified),
        bucket=bucket,

        conflict_count=int(conflict_count),
        conflict_parts=[str(x) for x in (conflict_parts or [])],
    )


def _comment_compatible_with_baseline(db: Session, comment: Comment) -> bool | None:
    """Returns None when baseline compatibility is not determinable from available patch/version data."""
    try:
        payload = comment.patch_payload if isinstance(getattr(comment, "patch_payload", None), dict) else None
        parts = payload.get("parts") if isinstance(payload, dict) else None
        if not isinstance(parts, list) or not parts:
            return None
        v = (
            db.query(ArticleVersion.id)
            .filter(ArticleVersion.id == int(getattr(comment, "version_id", 0) or 0))
            .one_or_none()
        )
        if v is None:
            return None
        conflict_count, _ = _comment_conflict_info(db, comment)
        return bool(int(conflict_count or 0) == 0)
    except Exception:
        return None


def _comment_primary_time(comment: Comment) -> tuple[datetime | None, str]:
    try:
        pub = getattr(comment, "published_at", None)
        if isinstance(pub, datetime):
            return pub, "published_at"
    except Exception:
        pass
    try:
        created = getattr(comment, "created_at", None)
        if isinstance(created, datetime):
            return created, "created_at"
    except Exception:
        pass
    return None, "created_at"


def _comment_precedence_record(db: Session, comment: Comment) -> dict[str, Any]:
    summary = _aggregate_votes_for_comment(db, comment)
    w_pos = int(getattr(summary, "weighted_positive", 0) or 0)
    w_neu = int(getattr(summary, "weighted_neutral", 0) or 0)
    w_neg = int(getattr(summary, "weighted_negative", 0) or 0)
    w_denom = int(w_pos + w_neu + w_neg)
    w_approval = getattr(summary, "weighted_approval_percent", None)
    w_disapproval = (100.0 * float(w_neg) / float(w_denom)) if w_denom > 0 else None
    primary_dt, primary_src = _comment_primary_time(comment)
    compat = _comment_compatible_with_baseline(db, comment)
    return {
        "cid": int(getattr(comment, "id", 0) or 0),
        "status": str(getattr(comment, "status", "") or ""),
        "compatible_with_baseline": compat,
        "weighted_positive": int(w_pos),
        "weighted_negative": int(w_neg),
        "weighted_score": int(w_pos - w_neg),
        "weighted_approval_percent": (float(w_approval) if w_approval is not None else None),
        "weighted_disapproval_percent": (float(w_disapproval) if w_disapproval is not None else None),
        "primary_time": (primary_dt.isoformat() if isinstance(primary_dt, datetime) else None),
        "primary_time_field": str(primary_src),
        # Computed eligibility is intentionally separate from global precedence.
        "eligible_for_next_draft": bool(getattr(summary, "qualified_for_next_release", False)),
    }


def _build_precedence_records_for_comments(db: Session, comments: list[Comment]) -> dict[int, dict[str, Any]]:
    out: dict[int, dict[str, Any]] = {}
    for c in comments or []:
        if c is None:
            continue
        cid = int(getattr(c, "id", 0) or 0)
        if cid <= 0:
            continue
        out[cid] = _comment_precedence_record(db, c)
    return out


@app.post("/api/comments/{comment_id}/vote", response_model=CommentVoteSummaryOut)
def vote_for_comment(
    comment_id: int,
    payload: CommentVoteRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> CommentVoteSummaryOut:
    comment = db.query(Comment).filter(Comment.id == comment_id).one_or_none()
    if comment is None:
        raise HTTPException(status_code=404, detail="Kommentar nicht gefunden")

    # Authors normally cannot vote on their own comments to avoid self-voting bias.
    # Administrators are the explicit exception for testing, moderation, and bootstrap work.
    if comment.user_id == current_user.id and not bool(getattr(current_user, "is_admin", False)):
        raise HTTPException(status_code=409, detail="Autor kann nicht für eigenen Kommentar abstimmen")
 
 
    st = str(getattr(comment, "status", "") or "").strip().lower()
    if st not in {"review", "veröffentlicht"}:
        raise HTTPException(status_code=409, detail="Auf diesen Kommentar kann derzeit nicht gevotet werden")

    if payload.main_vote not in ALLOWED_MAIN_VOTES:
        raise HTTPException(status_code=400, detail="Ungültiges main_vote-Emoji")

    # Comment v2: vote rows store only the main vote; reactions/bookmarks use the separate reaction endpoint.
 
    # Compatibility: `CommentVote` still carries the legacy `flags` field; comment/reaction v2
    
    flags = []

    now = datetime.utcnow()

    max_votes = int(getattr(settings, "MAX_COMMENT_VOTES_PER_USER_PER_24H", 0) or 0)
    if max_votes > 0:
        since_24h = now - timedelta(hours=24)
        votes_last_24h = (
            db.query(CommentVote)
            .filter(CommentVote.user_id == current_user.id)
            .filter(CommentVote.created_at >= since_24h)
            .count()
        )
        if votes_last_24h >= max_votes:
            raise HTTPException(status_code=429, detail="Voting-Limit erreicht. Bitte später erneut versuchen.")

    vote = (
        db.query(CommentVote)
        .filter(CommentVote.user_id == current_user.id)
        .filter(CommentVote.comment_id == comment.id)
        .one_or_none()
    )

    if vote is None:
        vote = CommentVote(
            user_id=current_user.id,
            comment_id=comment.id,
            article_id=comment.article_id,
            version_id=comment.version_id,
            main_vote=payload.main_vote,
            flags=flags,
            status="pending",
            created_at=now,
            confirmed_at=None,
        )
        db.add(vote)
    else:
        vote.main_vote = payload.main_vote
        vote.flags = flags
        vote.status = "pending"
        vote.confirmed_at = None

    _commit_db(db)
    _clear_public_runtime_caches()
    return _aggregate_votes_for_comment(db, comment)


@app.get("/api/comments/{comment_id}/votes/summary", response_model=CommentVoteSummaryOut)
def get_comment_votes_summary(comment_id: int, db: Session = Depends(get_db)) -> CommentVoteSummaryOut:
    comment = db.query(Comment).filter(Comment.id == comment_id).one_or_none()
    if comment is None:
        raise HTTPException(status_code=404, detail="Kommentar nicht gefunden")
    return _aggregate_votes_for_comment(db, comment)


@app.get("/api/me/comment-votes", response_model=list[PersonalCommentVoteOut])
def list_my_comment_votes(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[PersonalCommentVoteOut]:
    votes = (
        db.query(CommentVote)
        .filter(CommentVote.user_id == current_user.id)
        .filter(CommentVote.status.in_(["confirmed", "pending", "ignored_spike"]))
        .all()
    )

    # Comment reactions come from `CommentReaction`; legacy `CommentVote.flags` is read only for compatibility unioning.
    out: list[PersonalCommentVoteOut] = []
    for v in votes:
        out.append(PersonalCommentVoteOut(
            comment_id=v.comment_id,
            article_id=v.article_id,
            version_id=v.version_id,
            main_vote=v.main_vote,
        ))
    return out


# ---------------------------------------------------------------------------
# Comments backend workflow
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Comment v2 – Patch-Stats Helper
# ---------------------------------------------------------------------------

from difflib import SequenceMatcher



def _compute_patch_stats(old_text: str, new_text: str) -> dict:
    """Compute robust coarse change statistics used by UI, eligibility and release diagnostics."""
    old_text = old_text or ""
    new_text = new_text or ""

    old_chars = len(old_text)
    new_chars = len(new_text)

    # Stable word-count calculation.
    old_tokens = [w for w in re.split(r"\s+", old_text.strip()) if w]
    new_tokens = [w for w in re.split(r"\s+", new_text.strip()) if w]
    old_words = len(old_tokens)
    new_words = len(new_tokens)

    # Character diff is a secondary metric.
    sm_chars = SequenceMatcher(a=old_text, b=new_text)
    opcodes_chars = sm_chars.get_opcodes()
    inserts_c = deletes_c = replaces_c = 0
    for tag, i1, i2, j1, j2 in opcodes_chars:
        if tag == "insert":
            inserts_c += (j2 - j1)
        elif tag == "delete":
            deletes_c += (i2 - i1)
        elif tag == "replace":
            replaces_c += max(i2 - i1, j2 - j1)

    changed_chars = inserts_c + deletes_c + replaces_c

    # Word-level diff is the source of truth for the comment change-size estimate.
    sm_words = SequenceMatcher(a=old_tokens, b=new_tokens)
    opcodes_words = sm_words.get_opcodes()
    ins_w = del_w = rep_w = 0
    for tag, i1, i2, j1, j2 in opcodes_words:
        if tag == "insert":
            ins_w += (j2 - j1)
        elif tag == "delete":
            del_w += (i2 - i1)
        elif tag == "replace":
            rep_w += max(i2 - i1, j2 - j1)

    changed_words = int(ins_w + del_w + rep_w)
    word_delta = int(abs(new_words - old_words))

    
    
    if changed_words <= 20:
        bucket = "small"
    elif changed_words <= 200:
        bucket = "medium"
    else:
        bucket = "large"

    return {
        'old_chars': old_chars,
        'new_chars': new_chars,
        'changed_chars': int(changed_chars),
        'old_words': int(old_words),
        'new_words': int(new_words),
        'word_delta': int(word_delta),
        'changed_words': int(changed_words),
        'change_size_bucket': bucket,
    }


def _diff_html_words(old_text: str, new_text: str) -> str:
    """Build a minimal word-level HTML diff using the shared `.minimd-ins` / `.minimd-del` classes."""
    old_tokens = [w for w in re.split(r"\s+", (old_text or "").strip()) if w]
    new_tokens = [w for w in re.split(r"\s+", (new_text or "").strip()) if w]
    sm = SequenceMatcher(a=old_tokens, b=new_tokens)

    out: list[str] = []

    def _wrap(cls: str, seg: str) -> str:
        seg = (seg or "").strip()
        if not seg:
            return ""
        return f'<span class="{cls}">{seg}</span>'

    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            out.extend(html.escape(w) for w in old_tokens[i1:i2])
        elif tag == "delete":
            seg = " ".join(html.escape(w) for w in old_tokens[i1:i2])
            w = _wrap("minimd-del", seg)
            if w:
                out.append(w)
        elif tag == "insert":
            seg = " ".join(html.escape(w) for w in new_tokens[j1:j2])
            w = _wrap("minimd-ins", seg)
            if w:
                out.append(w)
        elif tag == "replace":
            seg_old = " ".join(html.escape(w) for w in old_tokens[i1:i2])
            seg_new = " ".join(html.escape(w) for w in new_tokens[j1:j2])
            w1 = _wrap("minimd-del", seg_old)
            w2 = _wrap("minimd-ins", seg_new)
            if w1:
                out.append(w1)
            if w2:
                out.append(w2)

    return " ".join([s for s in out if s]).strip()
 
def _strip_minimd_markers(s: str) -> str:
    """Strip MiniMD container markers for compact comment-diff display."""
    if not s:
        return ""
    t = str(s)
    # Strip block markers: `### start: X ###` / `### end: X ###`.
    t = re.sub(r"###\s*start:\s*[^#]+###", "", t, flags=re.IGNORECASE)
    t = re.sub(r"###\s*end:\s*[^#]+###", "", t, flags=re.IGNORECASE)
    
    t = re.sub(r"###\s*Artikel-Titel:\s*[^#]+###", "", t, flags=re.IGNORECASE)
    # Normalize whitespace.
    t = re.sub(r"[ \t]+\n", "\n", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip()


def _sentence_window(s: str, idx: int, max_chars: int = 260) -> str:
    """Return a bounded context window around an index, preferring sentence boundaries."""
    if not s:
        return ""
    n = len(s)
    try:
        idx = int(idx)
    except Exception:
        idx = 0
    idx = max(0, min(idx, n))

    def is_bound(ch: str) -> bool:
        return ch in ".!?\n"

    left = idx
    right = idx

    while left > 0 and not is_bound(s[left - 1]) and (idx - left) < max_chars:
        left -= 1
    while right < n and not is_bound(s[right]) and (right - idx) < max_chars:
        right += 1
    while right < n and is_bound(s[right]) and (right - left) < (max_chars + 30):
        right += 1

    out = s[left:right].strip()
    cap = max_chars * 2
    if len(out) > cap:
        out = out[:cap].rstrip() + "…"
    return out


def _first_change_index(old: str, new: str) -> int:
    """Heuristically locate the first changed position in the new text."""
    try:
        sm = SequenceMatcher(a=(old or ""), b=(new or ""))
        for tag, i1, i2, j1, j2 in sm.get_opcodes():
            if tag != "equal":
                return int(j1)
    except Exception:
        pass
    return 0


def _make_compact_diff_html(old_text: str, new_text: str, max_chars: int = 260) -> str:
    """Build a compact HTML diff limited to the affected sentence or local section."""
    o = _strip_minimd_markers(old_text or "")
    n = _strip_minimd_markers(new_text or "")
    if not o and not n:
        return ""
    if o == n:
        # Deliberately avoid a full-document fallback view here.
        return "<span class='klein'>(keine Änderungen erkannt)</span>"
    j = _first_change_index(o, n)
    o_win = _sentence_window(o, j, max_chars=max_chars)
    n_win = _sentence_window(n, j, max_chars=max_chars)
    return _diff_html_words(o_win, n_win)


def _normalize_patch_payload_v2(payload_patch: dict | None, payload_anchor: dict | None, proposal_text: str | None) -> dict | None:
    """Normalize patch data to the canonical v2 multipart shape while retaining readable support for legacy single-part payloads and anchor/proposal fallbacks."""
    patch = payload_patch if isinstance(payload_patch, dict) else None
    anchor = payload_anchor if isinstance(payload_anchor, dict) else None


    def _norm_block_key(v) -> str:
        """Normalize `block_key`/`block_id` to the canonical block key required by the v2 patch contract, using the established fallback when no mapping is known."""
        s = ("" if v is None else str(v)).strip().lower()
        if not s:
            return "juristisch"
        
        if s in {"_all", "all"}:
            return "juristisch"
        return s

    # 1) Already in canonical v2 `parts[]` form.
    if patch and isinstance(patch.get("parts"), list):
        parts_in = patch.get("parts") or []

        # comment/reaction v2 inline merge/preview compatibility:
        # Compatibility repair: some older frontend builds send one patch part whose old/new text
        # contains a complete MiniMD document with block markers while claiming one block key.
        # That shape cannot be applied deterministically to a block-local baseline.
        # The frontend composes block-by-block, so each baseline contains only that block body
        # and can never match a full-document `old_text` inside one block.
        #
        # When this exact legacy shape is detected, split it server-side into
        # block-local parts. This affects only normalization and preserves the v2 contract.
        try:
            if (
                isinstance(parts_in, list)
                and len(parts_in) == 1
                and isinstance(parts_in[0], dict)
                and _looks_like_full_minimd_doc(parts_in[0].get("old_text"))
                and _looks_like_full_minimd_doc(parts_in[0].get("new_text"))
            ):
                full_old = str(parts_in[0].get("old_text") or "")
                full_new = str(parts_in[0].get("new_text") or "")
                base_blocks = _minimd_blocks_map(full_old)
                prop_blocks = _minimd_blocks_map(full_new)
                split_parts: list[dict] = []
                for bn in _minimd_block_order():
                    a = str((base_blocks.get(bn) or "") if isinstance(base_blocks, dict) else "")
                    # Missing proposal blocks do not imply deletion; never wipe a baseline block implicitly.
                    # instead preserve the baseline text.
                    b = prop_blocks.get(bn, a) if isinstance(prop_blocks, dict) else a
                    b = str(b if isinstance(b, str) else a)
                    if a == b:
                        continue
                    split_parts.append(
                        {
                            "part_id": f"p{len(split_parts)+1}",
                            "block_key": str(bn),
                            "old_text": a,
                            "new_text": b,
                            "meta": {"source": "server_split_full_minimd", "kind": "minimd_blocks"},
                        }
                    )
                if split_parts:
                    out = dict(patch)
                    out["version"] = 2
                    out["parts"] = split_parts
                    return out
        except Exception:
            # If compatibility splitting fails, continue through the normal defensive normalization path.
            pass
        parts_out: list[dict] = []
        for i, p in enumerate(parts_in):
            if not isinstance(p, dict):
                continue
            old_text = p.get("old_text")
            new_text = p.get("new_text")
            if not isinstance(old_text, str) or not isinstance(new_text, str):
                
                continue
            part_id = p.get("part_id") if isinstance(p.get("part_id"), str) and p.get("part_id").strip() else f"p{i+1}"
            out = dict(p)
            out["part_id"] = part_id
            out["old_text"] = old_text
            out["new_text"] = new_text
            # API contract: every normalized part must contain `block_key`.
            if "block_key" not in out or not str(out.get("block_key") or "").strip():
                out["block_key"] = _norm_block_key(
                    out.get("block_id")
                    or (anchor.get("block_key") if anchor else None)
                    or (anchor.get("block_id") if anchor else None)
                )
            else:
                out["block_key"] = _norm_block_key(out.get("block_key"))
            parts_out.append(out)

        if not parts_out:
            return None

        out = dict(patch)
        out["version"] = 2
        out["parts"] = parts_out
        return out

    return None


    def _looks_like_full_minimd(s: str) -> bool:
        # Full-document MiniMD normally contains title metadata and block markers.
        if not isinstance(s, str):
            return False
        t = s
        return (
            "### Artikel-Titel:" in t
            and "### start:" in t
            and "### end:" in t
        )


def _looks_like_full_minimd_doc(text: str | None) -> bool:
    """Heuristik: erkennt ob proposal_text wie ein kompletter MiniMD-Dokumenttext aussieht."""
    if not isinstance(text, str):
        return False
    t = text.strip()
    if not t:
        return False
    if "### start:" in t:
        return True
    if t.startswith("### Artikel-Titel:"):
        return True
    if t.count("###") >= 6 and ("start:" in t or "end:" in t):
        return True
    return False


def _article_version_minimd_bundle(version: "ArticleVersion") -> tuple[str, dict[str, str]]:
    minimd, _ = article_version_to_minimd(version)
    block_map = _minimd_blocks_map(minimd)
    block_map.pop("_all", None)
    return str(minimd or ""), {str(k): str(v or "") for k, v in dict(block_map or {}).items()}


def _comment_to_indiff_input(comment: Comment) -> code.PersistedCommentInput:
    pp = comment.patch_payload if isinstance(getattr(comment, "patch_payload", None), dict) else {}
    raw_parts = pp.get("parts") if isinstance(pp.get("parts"), list) else []
    parts: list[code.PersistedCommentPartInput] = []
    for idx, p in enumerate(raw_parts):
        if not isinstance(p, dict):
            continue
        parts.append(
            code.PersistedCommentPartInput(
                part_id=str(p.get("part_id") or f"p{idx+1}"),
                block_key=str(p.get("block_key") or p.get("block_id") or ""),
                old_text=str(p.get("old_text") or ""),
                new_text=str(p.get("new_text") or ""),
                sel_start=(int(p.get("sel_start")) if p.get("sel_start") is not None else None),
                sel_end=(int(p.get("sel_end")) if p.get("sel_end") is not None else None),
                baseline_hash=str(p.get("baseline_hash") or ""),
            )
        )
    return code.PersistedCommentInput(
        cid=int(getattr(comment, "id", 0) or 0),
        parts=parts,
    )


def _sync_comment_diff_state_from_indiff(
    db: Session | None,
    comment: Comment,
    *,
    comment_minimd_text: str | None = None,
) -> None:
    if db is None:
        return
    if not isinstance(getattr(comment, "patch_payload", None), dict):
        return

    pp = comment.patch_payload if isinstance(getattr(comment, "patch_payload", None), dict) else None
    if not isinstance(pp, dict):
        return

    version = (
        db.query(ArticleVersion)
        .filter(
            ArticleVersion.id == int(getattr(comment, "version_id", 0) or 0),
            ArticleVersion.article_id == int(getattr(comment, "article_id", 0) or 0),
        )
        .one_or_none()
    )
    if version is None:
        return

    baseline_article_minimd, baseline_old_minimd_by_block = _article_version_minimd_bundle(version)
    comment_input = _comment_to_indiff_input(comment)

    prep = code.prepare_comment_submission(
        article_version_id=int(getattr(version, "id", 0) or 0),
        current_version_id=int(getattr(version, "id", 0) or 0),
        baseline_article_minimd=str(baseline_article_minimd or ""),
        baseline_old_minimd_by_block=dict(baseline_old_minimd_by_block or {}),
        comment=comment_input,
        fallback_block_key="juristisch",
        comment_minimd_text=comment_minimd_text,
    )

    exact = code.validate_exact_selection_patch_payload(
        baseline_old_minimd_by_block=dict(baseline_old_minimd_by_block or {}),
        comment=prep.normalized_comment,
        fallback_block_key="juristisch",
        require_baseline_hash=True,
        require_selection=True,
    )

    pp = dict(comment.patch_payload or {})
    pp["version"] = 2
    pp["base_version_id"] = int(getattr(version, "id", 0) or 0)
    pp["exact_selection"] = True
    pp["parts"] = [asdict(p) for p in list(prep.normalized_comment.parts or [])]
    pp.pop("semantic_events", None)
    pp.pop("validation", None)
    pp.pop("diff_html", None)
    pp.pop("diff_html_compact", None)
    pp.pop("patch_stats", None)
    pp.pop("diff_box_data", None)
    pp["exact_selection_validation"] = {
        "ok": bool(exact.ok),
        "errors": list(exact.errors or []),
        "warnings": list(exact.warnings or []),
        "block_hash_by_key": dict(exact.block_hash_by_key or {}),
    }

    comment.patch_payload = pp
    comment.patch_stats = None


def _comment_out(comment: Comment, *, sync_diff_state: bool = False) -> CommentOut:
    """Build the canonical production `CommentOut` representation through `indiff.py`."""
    if sync_diff_state:
        _sync_comment_diff_state_from_indiff(object_session(comment), comment)
    base = CommentOut.model_validate(comment).model_dump()
    base["comment_mode"] = str(getattr(comment, "comment_mode", "change"))
    base["structure_payload"] = getattr(comment, "structure_payload", None)
    return CommentOut(**base)


def _comment_change_size_bucket(comment: Comment) -> str:
    """Return the change-size bucket used by submit and review gates."""
    raw = getattr(comment, "patch_stats", None)
    if isinstance(raw, dict):
        for key in ("size_bucket", "change_size_bucket", "bucket"):
            val = str(raw.get(key) or "").strip().lower()
            if val in {"small", "medium", "large"}:
                return val
    return "small"


def _count_completed_reviews_by_user(db: Session, user_id: int) -> int:
    """Count completed reviews for a user as a submit-gate prerequisite."""
    try:
        return int(
            db.query(func.count(Review.id))
            .filter(Review.reviewer_id == int(user_id))
            .scalar()
            or 0
        )
    except Exception:
        return 0


def _format_wait_seconds_de(seconds: int) -> str:
    seconds = max(0, int(seconds or 0))
    if seconds <= 0:
        return "0 Sekunden"
    minutes = seconds // 60
    rest = seconds % 60
    if minutes and rest:
        return f"{minutes} Minuten {rest} Sekunden"
    if minutes:
        return f"{minutes} Minuten"
    return f"{rest} Sekunden"


def _jsonable(v):
    """Convert Pydantic models (or similar) into plain JSON-serializable dicts/lists."""
    if v is None:
        return None
    # Pydantic v2
    md = getattr(v, "model_dump", None)
    if callable(md):
        try:
            return md()
        except TypeError:
            return md(mode="json")
    # Pydantic v1
    dct = getattr(v, "dict", None)
    if callable(dct):
        return dct()
    return v


def _pick_indiff_recommended_state(states: list[str] | None, fallback: str = "normal") -> str:
    vals = [str(x or "").strip() for x in list(states or []) if str(x or "").strip()]
    if not vals:
        return str(fallback or "normal")
    if "none" in vals:
        return "none"
    if "warn" in vals:
        return "warn"
    if "ghost" in vals:
        return "ghost"
    if "normal" in vals:
        return "normal"
    return str(fallback or "normal")


def _build_indiff_decoration_table(res: Any) -> dict[str, Any]:
    style_table = list(getattr(res, "style_table", []) or [])
    interaction_table = list(getattr(res, "interaction_table", []) or [])

    span_state_by_id: dict[str, str] = {}
    for row in interaction_table:
        rec = dict(row or {})
        state_map = rec.get("recommended_state_map") if isinstance(rec.get("recommended_state_map"), dict) else {}
        for span_id, state in dict(state_map or {}).items():
            sid = str(span_id or "").strip()
            if not sid:
                continue
            span_state_by_id[sid] = str(state or "normal")

    part_state_by_id: dict[str, str] = {}
    for row in style_table:
        rec = dict(row or {})
        default_state = str(rec.get("default_state") or "normal")
        span_ids = [str(x or "").strip() for x in list(rec.get("span_ids") or []) if str(x or "").strip()]
        part_ids = [str(x or "").strip() for x in list(rec.get("part_ids") or []) if str(x or "").strip()]
        recommended = _pick_indiff_recommended_state(
            [span_state_by_id.get(sid, default_state) for sid in span_ids],
            fallback=default_state,
        )
        for part_id in part_ids:
            part_state_by_id[part_id] = str(recommended or default_state or "normal")

    return {
        "span_state_by_id": dict(span_state_by_id),
        "part_state_by_id": dict(part_state_by_id),
    }


def _group_comment_card_rows_by_cid(
    comment_cards_table: list[dict[str, Any]] | None,
) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {}
    for row in list(comment_cards_table or []):
        rec = dict(row or {})
        cid = int(rec.get("cid") or 0)
        if cid <= 0:
            continue
        key = str(cid)
        out.setdefault(key, []).append(rec)
    for key, rows in list(out.items()):
        rows.sort(
            key=lambda r: (
                str(r.get("block_key") or ""),
                str(r.get("row_sort_key") or ""),
                int(r.get("source_start") or 0),
                str(r.get("span_id") or ""),
            )
        )
        out[key] = rows
    return out


def _render_comment_cards_by_cid_from_table(
    comment_cards_table: list[dict[str, Any]] | None,
    *,
    expected_cids: list[int] | None = None,
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, str]]:
    rows_by_cid = _group_comment_card_rows_by_cid(list(comment_cards_table or []))
    html_by_cid: dict[str, str] = {}

    expected_keys = [str(int(x)) for x in list(expected_cids or []) if int(x or 0) > 0]
    all_keys = list(dict.fromkeys(expected_keys + list(rows_by_cid.keys())))
    for cid_key in all_keys:
        one_cid_table = list(rows_by_cid.get(cid_key) or [])
        html_by_cid[cid_key] = str(code._render_comment_cards_html_from_table(one_cid_table) or "")
    return rows_by_cid, html_by_cid


def _compose_comment_cards_payload_for_comments(
    db: Session,
    comments: list[Comment],
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, str]]:
    rows_by_cid_all: dict[str, list[dict[str, Any]]] = {}
    html_by_cid_all: dict[str, str] = {}
    if not comments:
        return rows_by_cid_all, html_by_cid_all

    grouped: dict[tuple[int, int], list[Comment]] = {}
    for c in list(comments or []):
        aid = int(getattr(c, "article_id", 0) or 0)
        vid = int(getattr(c, "version_id", 0) or 0)
        cid = int(getattr(c, "id", 0) or 0)
        if aid <= 0 or vid <= 0 or cid <= 0:
            continue
        grouped.setdefault((aid, vid), []).append(c)

    for (aid, vid), group in list(grouped.items()):
        version = (
            db.query(ArticleVersion)
            .filter(
                ArticleVersion.id == int(vid),
                ArticleVersion.article_id == int(aid),
            )
            .one_or_none()
        )
        if version is None:
            continue

        _baseline_article_minimd, baseline_old_minimd_by_block = _article_version_minimd_bundle(version)
        baseline_blocks = _site_wrapped_blocks_from_version(version)
        block_order = list(_minimd_block_order())
        comments_in = [_comment_to_indiff_input(c) for c in list(group or [])]

        result = code.compose_article_merge_preview(
            block_order=list(block_order),
            baseline_old_minimd_by_block={bk: str((baseline_old_minimd_by_block or {}).get(bk) or "") for bk in block_order},
            baseline_html_by_block=dict(baseline_blocks or {}),
            comments=list(comments_in),
            fallback_block_key="juristisch",
        )

        group_cids = [int(getattr(c, "id", 0) or 0) for c in list(group or []) if int(getattr(c, "id", 0) or 0) > 0]
        rows_by_cid, html_by_cid = _render_comment_cards_by_cid_from_table(
            list(result.comment_cards_table or []),
            expected_cids=list(group_cids),
        )
        rows_by_cid_all.update(dict(rows_by_cid or {}))
        html_by_cid_all.update(dict(html_by_cid or {}))

    return rows_by_cid_all, html_by_cid_all


@app.post("/api/comments/new-article-draft", response_model=dict)
def create_new_article_draft_comment(
    payload: NewArticleDraftCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """Create a draft article and its accompanying comment from a validated MiniMD new-article proposal."""
    mode = "new_article"
    _require_comment_feature_trust(current_user, mode)

    minimd = str(getattr(payload, "minimd", "") or "")
    meta = _extract_new_article_meta(minimd)
    public_code = str(meta.get("Artikel-Kennung") or "").strip()
    title = str(meta.get("Artikel-Titel") or "").strip()
    toc_title = str(meta.get("Artikel-Kurztitel") or "").strip() or None
    show_in_toc = _meta_bool_value(meta.get("Artikel im Inhaltsverzeichnis"))
    insert_after_code = str(meta.get(_NEW_ARTICLE_INSERT_AFTER_META_LABEL) or "").strip()

    if db.query(Article).filter(Article.public_code == public_code).one_or_none() is not None:
        raise HTTPException(status_code=409, detail=f"Artikel-Kennung existiert bereits: {public_code}")

    insert_after = _find_article_by_public_code(db, insert_after_code)
    sort_order = _shift_sort_order_after_article(db, insert_after)

    article = Article(
        slug=_unique_article_slug(db, public_code, title),
        public_code=public_code,
        sort_order=sort_order,
        title=title,
        type=_derive_article_type_from_public_code(public_code),
        show_in_toc=bool(show_in_toc),
        toc_title=toc_title,
    )
    db.add(article)
    db.flush()

    baseline_blocks = _new_article_baseline_blocks()
    version = ArticleVersion(
        article_id=int(article.id),
        version_label=f"draft-new-article-{int(article.id)}",
        content_blocks=baseline_blocks,
        status="draft",
        published_at=None,
        created_by_user_id=int(current_user.id),
    )
    db.add(version)
    db.flush()
    article.current_version_id = int(version.id)
    db.add(article)
    db.flush()

    patch_payload = _build_new_article_patch_payload(
        minimd,
        baseline_blocks=baseline_blocks,
        version_id=int(version.id),
    )
    patch_payload = _validate_exact_selection_patch_payload(
        patch_payload=patch_payload,
        version=version,
    )

    comment = Comment(
        article_id=int(article.id),
        version_id=int(version.id),
        user_id=int(current_user.id),
        type="standard",
        anchor=None,
        comment_mode="new_article",
        structure_payload=_jsonable(getattr(payload, "structure_payload", None)),
        comment_category="general",
        anchor_payload=None,
        patch_payload=patch_payload,
        llm_assisted=bool(getattr(payload, "llm_assisted", False)),
        llm_context_hash=getattr(payload, "llm_context_hash", None),
        lifecycle_status="draft",
        policy_status="ok",
        proposal_text=minimd,
        explanation=getattr(payload, "explanation", None),
        impact_scores=getattr(payload, "impact_scores", None),
        sources=getattr(payload, "sources", None),
        status="entwurf",
    )
    db.add(comment)
    _commit_db(db)
    _clear_public_runtime_caches()
    db.refresh(article)
    db.refresh(version)
    db.refresh(comment)
    _sync_comment_diff_state_from_indiff(db, comment)

    return {
        "comment": _comment_out(comment).model_dump(mode="json"),
        "article": _build_article_out(db, article).model_dump(mode="json"),
        "activated_comment_id": int(comment.id),
        "article_id": int(article.id),
        "version_id": int(version.id),
    }


@app.post(
    "/api/comments",
    response_model=CommentOut,
)
def create_comment(
    payload: CommentCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> CommentOut:
    """Create a new draft comment/change proposal for an article version."""
    dev_quota_bypass = bool(
        getattr(settings, "KGG_DEV_DISABLE_COMMENT_QUOTA", False)
        and bool(getattr(settings, "AUTH_EXPOSE_LOGIN_URL", False))
    )
    if settings.MAX_COMMENTS_PER_USER_PER_24H > 0 and not dev_quota_bypass:
        now = datetime.utcnow()
        since_24h = now - timedelta(hours=24)
        comments_last_24h = (
            db.query(Comment)
            .filter(
                Comment.user_id == current_user.id,
                Comment.created_at >= since_24h,
            )
            .count()
        )
        if comments_last_24h >= settings.MAX_COMMENTS_PER_USER_PER_24H:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=(
                    "Du hast das maximale Kommentar-Kontingent "
                    "für die letzten 24 Stunden erreicht. "
                    "Bitte fasse dich kürzer oder nutze bestehende Kommentare."
                ),
            )

    mode = _normalize_comment_mode(getattr(payload, "comment_mode", "change"))
    _require_comment_feature_trust(current_user, mode)
    if mode == "new_article":
        raise HTTPException(
            status_code=400,
            detail="Neue Artikel werden über /api/comments/new-article-draft angelegt.",
        )

    
    article = db.query(Article).filter(Article.id == payload.article_id).one_or_none()
    if article is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Artikel nicht gefunden",
        )

    version = (
        db.query(ArticleVersion)
        .filter(
            ArticleVersion.id == payload.version_id,
            ArticleVersion.article_id == article.id,
        )
        .one_or_none()
    )
    if version is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Artikelversion passt nicht zum Artikel oder existiert nicht",
        )

    comment = Comment(
        article_id=article.id,
        version_id=version.id,
        user_id=current_user.id,
        type=payload.type or "standard",
        anchor=payload.anchor,
        comment_mode=mode,
        structure_payload=_jsonable(payload.structure_payload),
        comment_category=getattr(payload, "comment_category", None) or "general",
        # Compatibility: deployed frontends may still send `anchor` instead of `anchor_payload`.
        # Mirror it into `anchor_payload` so compatibility and fallback paths share one representation.
        anchor_payload=_jsonable(
            getattr(payload, "anchor_payload", None)
            if getattr(payload, "anchor_payload", None) is not None
            else getattr(payload, "anchor", None)
        ),
        patch_payload=_jsonable(getattr(payload, "patch_payload", None)),
        llm_assisted=bool(getattr(payload, "llm_assisted", False)),
        llm_context_hash=getattr(payload, "llm_context_hash", None),
        lifecycle_status="draft",
        policy_status="ok",
        proposal_text=payload.proposal_text,
        explanation=payload.explanation,
        impact_scores=payload.impact_scores,
        sources=payload.sources,
        parent_comment_id=payload.parent_comment_id,
        status="entwurf",
    )

    # Strict exact-selection pipeline for text changes. Delete-article proposals create
    # Build the full deletion patch server-side so clients do not need a free-form patch field.
    if mode == "delete_article":
        comment.patch_payload = _validate_exact_selection_patch_payload(
            patch_payload=_build_delete_article_patch_payload(version),
            version=version,
        )
        if not str(comment.proposal_text or "").strip():
            comment.proposal_text = "Antrag zur Artikel-Löschung"
    else:
        if getattr(payload, "patch_payload", None) is None:
            raise HTTPException(
                status_code=400,
                detail="Patch invalid: missing patch_payload with exact selection metadata.",
            )
        comment.patch_payload = _validate_exact_selection_patch_payload(
            patch_payload=_jsonable(getattr(payload, "patch_payload", None)),
            version=version,
        )

    _sync_comment_diff_state_from_indiff(db, comment)

    db.add(comment)
    _commit_db(db)
    db.refresh(comment)
    _sync_comment_diff_state_from_indiff(db, comment)
    _clear_public_runtime_caches()
    return _comment_out(comment)


@app.put(
    "/api/comments/{comment_id}",
    response_model=CommentOut,
)
def update_comment(
    comment_id: int,
    payload: CommentUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> CommentOut:
    """Update an existing draft comment; only its author may edit it while it remains in draft status."""
    comment = db.query(Comment).filter(Comment.id == comment_id).one_or_none()
    if comment is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Kommentar nicht gefunden",
        )

    if comment.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Du darfst diesen Kommentar nicht bearbeiten",
        )

    if comment.status != "entwurf":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Nur Kommentarentwürfe können bearbeitet werden",
        )

    if payload.type is not None:
        comment.type = payload.type
    if payload.anchor is not None:
        comment.anchor = payload.anchor
    old_mode = _normalize_comment_mode(getattr(comment, "comment_mode", "change"))
    if getattr(payload, "comment_mode", None) is not None:
        new_mode = _normalize_comment_mode(payload.comment_mode)
        _require_comment_feature_trust(current_user, new_mode)
        if new_mode == "new_article" and old_mode != "new_article":
            raise HTTPException(
                status_code=400,
                detail="Neue Artikel werden über /api/comments/new-article-draft angelegt.",
            )
        comment.comment_mode = new_mode
    mode = _normalize_comment_mode(getattr(comment, "comment_mode", "change"))
    if getattr(payload, "structure_payload", None) is not None:
        comment.structure_payload = _jsonable(payload.structure_payload)
    if getattr(payload, 'comment_category', None) is not None:
        comment.comment_category = payload.comment_category
    if getattr(payload, 'anchor_payload', None) is not None:
        comment.anchor_payload = _jsonable(payload.anchor_payload)
    else:
        # Keep `anchor_payload` synchronized when a compatibility client updates only `anchor`.
        if payload.anchor is not None:
            comment.anchor_payload = _jsonable(payload.anchor)
    if getattr(payload, 'patch_payload', None) is not None or mode == "delete_article":
        v = (
            db.query(ArticleVersion)
            .filter(
                ArticleVersion.id == int(comment.version_id),
                ArticleVersion.article_id == int(comment.article_id),
            )
            .one_or_none()
        )
        if v is None:
            raise HTTPException(status_code=400, detail="Artikelversion nicht gefunden.")
        if mode == "delete_article" and getattr(payload, 'patch_payload', None) is None:
            comment.patch_payload = _validate_exact_selection_patch_payload(
                patch_payload=_build_delete_article_patch_payload(v),
                version=v,
            )
            if not str(comment.proposal_text or "").strip():
                comment.proposal_text = "Antrag zur Artikel-Löschung"
        else:
            comment.patch_payload = _validate_exact_selection_patch_payload(
                patch_payload=_jsonable(payload.patch_payload),
                version=v,
            )
    if getattr(payload, 'llm_assisted', None) is not None:
        comment.llm_assisted = bool(payload.llm_assisted)
    if getattr(payload, 'llm_context_hash', None) is not None:
        comment.llm_context_hash = payload.llm_context_hash
    if payload.proposal_text is not None:
        comment.proposal_text = payload.proposal_text
    if payload.explanation is not None:
        comment.explanation = payload.explanation
    if payload.impact_scores is not None:
        comment.impact_scores = payload.impact_scores
    if payload.sources is not None:
        comment.sources = payload.sources

    _sync_comment_diff_state_from_indiff(db, comment)

    db.add(comment)
    _commit_db(db)
    db.refresh(comment)
    _sync_comment_diff_state_from_indiff(db, comment)
    return _comment_out(comment)
    


# ---------------------------------------------------------------------------
# Private patch-draft operations: list, fork/version, and discard
# ---------------------------------------------------------------------------


@app.get("/api/me/comments/drafts", response_model=List[CommentOut])
def api_me_comment_drafts(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0, le=10_000),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> List[CommentOut]:
    """Return the authenticated user's private comment drafts."""
    rows = (
        db.query(Comment)
        .filter(
            Comment.user_id == current_user.id,
            Comment.lifecycle_status == "draft",
            Comment.status == "entwurf",
        )
        .order_by(Comment.updated_at.desc(), Comment.id.desc())
        .offset(int(offset))
        .limit(int(limit))
        .all()
    )
    return [_comment_out(c) for c in rows]
 
 
@app.get("/api/me/comments/overview", response_model=List[CommentOverviewItemOut])
def api_me_comments_overview(
    limit: int = Query(200, ge=1, le=500),
    offset: int = Query(0, ge=0, le=10_000),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    q = (
        db.query(Comment, Article, ArticleVersion)
        .join(Article, Comment.article_id == Article.id)
        .join(ArticleVersion, Comment.version_id == ArticleVersion.id)
        .filter(Comment.user_id == current_user.id)
        .order_by(Comment.updated_at.desc(), Comment.id.desc())
        .offset(int(offset))
        .limit(int(limit))
    )
    rows = q.all()
    comment_ids = [c.id for (c, _, _) in rows]

    # Aggregates: reviews and reactions.
    reviews_map = {cid: {"total":0,"approve":0,"revise":0,"reject":0} for cid in comment_ids}
    reactions_map: dict[int, dict[str,int]] = {cid: {} for cid in comment_ids}

    if comment_ids:
        # Review rollup.
        rr = (
            db.query(Review.comment_id, Review.recommendation, func.count(Review.id))
            .filter(Review.comment_id.in_(comment_ids))
            .group_by(Review.comment_id, Review.recommendation)
            .all()
        )
        for cid, rec, cnt in rr:
            d = reviews_map.setdefault(cid, {"total":0,"approve":0,"revise":0,"reject":0})
            d["total"] += int(cnt or 0)
            key = (str(rec) if rec is not None else "").strip().lower()
            if key in _APPROVE_RECS: d["approve"] += int(cnt or 0)
            elif key in _REVISE_RECS: d["revise"] += int(cnt or 0)
            elif key in _REJECT_RECS: d["reject"] += int(cnt or 0)

        # Reactions.
        erows = (
            db.query(CommentReaction.comment_id, CommentReaction.reaction_emoji, func.count(CommentReaction.id))
            .filter(CommentReaction.comment_id.in_(comment_ids))
            .group_by(CommentReaction.comment_id, CommentReaction.reaction_emoji)
            .all()
        )
        for cid, emoji, cnt in erows:
            reactions_map.setdefault(cid, {})[str(emoji)] = int(cnt or 0)

    out = []
    for c, a, v in rows:
        req = _required_reviews_for_comment(c)
        roll = reviews_map.get(c.id, {"total":0,"approve":0,"revise":0,"reject":0})
        out.append(CommentOverviewItemOut(
            id=c.id,
            article_id=c.article_id,
            article_slug=a.slug,
            article_title=a.title,
            version_id=c.version_id,
            version_label=v.version_label,
            status=c.status,
            lifecycle_status=c.lifecycle_status,
            policy_status=c.policy_status,
            candidate_status=c.candidate_status,
            comment_mode=str(getattr(c, "comment_mode", "change") or "change"),
            structure_payload=getattr(c, "structure_payload", None),
            proposal_text=c.proposal_text,
            explanation=c.explanation,
            sources=c.sources,
            updated_at=c.updated_at,
            created_at=c.created_at,
            published_at=c.published_at,
            anchor=c.anchor,
            anchor_payload=c.anchor_payload,
            impact_scores=c.impact_scores,
            patch_stats=c.patch_stats,
            llm_assisted=bool(getattr(c, "llm_assisted", False)),
            llm_context_hash=getattr(c, "llm_context_hash", None),
            reviews_required=req,
            reviews_total=roll["total"],
            reviews_approve=roll["approve"],
            reviews_revise=roll["revise"],
            reviews_reject=roll["reject"],
            reactions=reactions_map.get(c.id, {}),
        ))
    return out

@app.get("/api/me/comments/{comment_id}", response_model=CommentOut)
def api_me_comment_get(
    comment_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    c = db.query(Comment).filter(Comment.id == comment_id).one_or_none()
    if not c:
        raise HTTPException(status_code=404, detail="Kommentar nicht gefunden")
    if c.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Nicht erlaubt")
    return _comment_out(c)


@app.post("/api/comments/{comment_id}/fork", response_model=CommentForkResponse)
def api_comment_fork(
    comment_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> CommentForkResponse:
    """Fork a draft into a child draft linked through `parent_comment_id`."""
    parent = db.query(Comment).filter(Comment.id == comment_id).first()
    if not parent:
        raise HTTPException(status_code=404, detail="Kommentar nicht gefunden.")
    if parent.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Nicht erlaubt.")
    if parent.status != "entwurf" or parent.lifecycle_status != "draft":
        raise HTTPException(status_code=400, detail="Nur Drafts können versioniert werden.")

    child = Comment(
        article_id=parent.article_id,
        version_id=parent.version_id,
        user_id=parent.user_id,
        type=parent.type,
        anchor=parent.anchor,
        comment_mode=parent.comment_mode,
        structure_payload=parent.structure_payload,
        comment_category=parent.comment_category,
        anchor_payload=parent.anchor_payload,
        patch_payload=parent.patch_payload,
        patch_stats=parent.patch_stats,
        llm_assisted=parent.llm_assisted,
        llm_context_hash=parent.llm_context_hash,
        lifecycle_status="draft",
        policy_status=parent.policy_status or "ok",
        proposal_text=parent.proposal_text,
        explanation=parent.explanation,
        impact_scores=parent.impact_scores,
        sources=parent.sources,
        status="entwurf",
        parent_comment_id=parent.id,
    )

    db.add(child)
    _commit_db(db)
    db.refresh(child)
    return CommentForkResponse(id=child.id, parent_comment_id=parent.id, status=child.status)


@app.post("/api/comments/{comment_id}/discard", response_model=CommentDiscardResponse)
def api_comment_discard(
    comment_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> CommentDiscardResponse:
    """Discard an owned draft: untouched drafts are hard-deleted; other drafts are retained as soft-discarded history."""
    c = db.query(Comment).filter(Comment.id == comment_id).first()
    if not c:
        raise HTTPException(status_code=404, detail="Kommentar nicht gefunden.")
    if c.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Nicht erlaubt.")
    if c.status != "entwurf" or c.lifecycle_status != "draft":
        raise HTTPException(status_code=400, detail="Nur Drafts können verworfen werden.")

    if _comment_can_be_hard_deleted(db, c):
        cid = int(c.id)
        db.delete(c)
        _commit_db(db)
        _clear_public_runtime_caches()
        return CommentDiscardResponse(id=cid, status="hard_deleted")

    c.status = "archiviert"
    c.lifecycle_status = "discarded"
    db.add(c)
    _commit_db(db)
    db.refresh(c)
    _clear_public_runtime_caches()
    return CommentDiscardResponse(id=c.id, status=c.status)


@app.post("/api/comments/{comment_id}/archive", response_model=CommentOut)
def api_comment_archive(
    comment_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> CommentOut:
    """Archive a comment as owner or admin while preserving integrated terminal state."""
    c = db.query(Comment).filter(Comment.id == comment_id).one_or_none()
    if c is None:
        raise HTTPException(status_code=404, detail="Kommentar nicht gefunden.")

    is_admin = bool(getattr(current_user, "is_admin", False))
    is_owner = (int(getattr(c, "user_id", 0) or 0) == int(getattr(current_user, "id", 0) or 0))
    if not (is_admin or is_owner):
        raise HTTPException(status_code=403, detail="Nicht erlaubt.")

    st = str(getattr(c, "status", "") or "").strip().lower()
    if st in {"integriert"}:
        raise HTTPException(status_code=409, detail="Integrierte Kommentare können nicht archiviert werden.")

    c.status = "archiviert"
    c.lifecycle_status = "archived"
    c.candidate_status = "archived"
    db.add(c)
    _commit_db(db)
    db.refresh(c)
    _sync_comment_diff_state_from_indiff(db, c)
    _clear_public_runtime_caches()
    return _comment_out(c)


@app.post("/api/comments/{comment_id}/delete", response_model=CommentDiscardResponse)
def api_comment_delete(
    comment_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> CommentDiscardResponse:
    """Delete or archive a comment according to its publication/history state; published comments are never hard-deleted."""
    c = db.query(Comment).filter(Comment.id == comment_id).one_or_none()
    if c is None:
        raise HTTPException(status_code=404, detail="Kommentar nicht gefunden.")

    is_admin = bool(getattr(current_user, "is_admin", False))
    is_owner = (getattr(c, "user_id", None) == getattr(current_user, "id", None))
    if not (is_admin or is_owner):
        raise HTTPException(status_code=403, detail="Nicht erlaubt.")

    st = str(getattr(c, "status", "") or "").strip().lower()
    if st in {"integriert"}:
        raise HTTPException(status_code=409, detail="Integrierte Kommentare können nicht gelöscht werden.")

    if _comment_can_be_hard_deleted(db, c):
        cid = int(c.id)
        db.delete(c)
        _commit_db(db)
        _clear_public_runtime_caches()
        return CommentDiscardResponse(id=cid, status="hard_deleted")

    # Previously public comments are never hard-deleted; archive them instead.
    if st in {"veröffentlicht", "veroeffentlicht"}:
        c.status = "archiviert"
        c.lifecycle_status = "archived"
        c.candidate_status = "archived"
    else:
        c.status = "gelöscht"
        c.lifecycle_status = "deleted"
        c.candidate_status = "deleted"

    db.add(c)
    _commit_db(db)
    _clear_public_runtime_caches()
    return CommentDiscardResponse(id=int(c.id), status=str(c.status or "deleted"))


@app.post(
    "/api/comments/{comment_id}/submit",
    response_model=CommentOut,
)
def submit_comment(
    comment_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> CommentOut:
    """Submit a draft comment to the review workflow."""
    comment = db.query(Comment).filter(Comment.id == comment_id).one_or_none()
    if comment is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Kommentar nicht gefunden",
        )

    if comment.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Du darfst diesen Kommentar nicht einreichen",
        )

    if comment.status != "entwurf":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Nur Kommentarentwürfe können eingereicht werden",
        )

    if _release_submit_blocked():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Release-Vorbereitung läuft. Entwürfe können weiter bearbeitet werden, "
                "aber bis zum Abschluss oder Abbruch nicht neu zur Review eingereicht werden."
            ),
        )

    # Submit gate: an author must satisfy the configured review-experience
    # and cooldown requirements before submitting a comment for review.
    if getattr(settings, "SUBMIT_REVIEW_GATE_ENABLED", True):
        bucket = _comment_change_size_bucket(comment)
        required_reviews = review_code.get_submit_gate_required(bucket, settings)
        completed_reviews = _count_completed_reviews_by_user(db, int(current_user.id))
        if completed_reviews < required_reviews:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    "Vor dem Einreichen brauchst du erst "
                    f"{required_reviews} abgeschlossene Reviews. "
                    f"Aktuell: {completed_reviews}."
                ),
            )

    cooldown_seconds = int(getattr(settings, "SUBMIT_REVIEW_COOLDOWN_SECONDS", 0) or 0)
    if cooldown_seconds > 0:
        last_saved_at = getattr(comment, "updated_at", None) or getattr(comment, "created_at", None)
        if last_saved_at is not None:
            now = datetime.utcnow()
            wait_until = last_saved_at + timedelta(seconds=cooldown_seconds)
            if now < wait_until:
                remaining = int((wait_until - now).total_seconds()) + 1
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=(
                        "Bitte lies deinen Entwurf noch einmal durch. "
                        "Einreichen ist möglich in "
                        f"{_format_wait_seconds_de(remaining)}."
                    ),
                )

    # Author impact scores are required before review submission.
    required = ("goal", "clarity", "practical", "legal")
    scores = comment.impact_scores
    missing = []
    if not isinstance(scores, dict):
        missing = list(required)
    else:
        for k in required:
            v = scores.get(k, None)
            if isinstance(v, bool):
                missing.append(k)
            elif not isinstance(v, int) or v < 0 or v > 4:
                missing.append(k)

    if missing:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                "Bitte fülle vor dem Einreichen die eigene Einschätzung vollständig aus "
                "(Zielbeitrag, Verständlichkeit, Praxisbezug, Rechtssicherheit: jeweils 0 bis 4)."
            ),
        )

    # Ensure canonical patch/diff data exists before the comment leaves draft state.
    _sync_comment_diff_state_from_indiff(db, comment)

    comment.status = "review"
    comment.lifecycle_status = "submitted"

    db.add(comment)
    _commit_db(db)
    db.refresh(comment)
    _sync_comment_diff_state_from_indiff(db, comment)
    return _comment_out(comment)


@app.get(
    "/api/articles/{article_id}/comments",
    response_model=List[CommentOut],
)
def list_article_comments(
    article_id: int,
    status_filter: str | None = Query(
        default=None,
        alias="status",
        description="Status-Filter, z. B. 'entwurf', 'review', 'veröffentlicht'.",
    ),
    version_id: int | None = Query(
        default=None,
        description="Optional: auf eine konkrete Artikelversion einschränken.",
    ),
    current_user: User | None = Depends(get_current_user_optional),
    db: Session = Depends(get_db),
) -> List[CommentOut]:
    """Return article comments subject to the public/private status and ownership boundaries of the comment workflow."""
    query = db.query(Comment).filter(Comment.article_id == article_id)

    # Persisted `gelöscht` and `archiviert` states are never public.
    
    def _restrict_to_owner_if_not_admin(q):
        if current_user is None:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Nicht öffentlich")
        if not bool(getattr(current_user, "is_admin", False)):
            q = q.filter(Comment.user_id == current_user.id)
        return q

    # Public/private boundary: draft comments are never public.
    
    
    if status_filter:
        sf = str(status_filter or "").strip().lower()
        if sf == "entwurf":
            if current_user is None:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Entwürfe sind nicht öffentlich",
                )
            if not bool(getattr(current_user, "is_admin", False)):
                query = query.filter(Comment.user_id == current_user.id)
        
        if sf in ("gelöscht", "geloescht", "archiviert"):
            query = _restrict_to_owner_if_not_admin(query)

        if sf == "review":
            if current_user is None:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Review-Kommentare sind nicht öffentlich",
                )
            if not bool(getattr(current_user, "is_admin", False)):
                
                query = query.filter(Comment.user_id == current_user.id)
        query = query.filter(Comment.status == status_filter)
    else:
        # Default visibility rules:
        
        
        #
        # Review-state comments must not appear automatically below public articles;
        # they are exposed only through explicit review-flow requests.
        query = query.filter(Comment.status.in_(_public_comment_statuses()))

    if version_id is not None:
        query = query.filter(Comment.version_id == version_id)

    comments = query.order_by(Comment.created_at.desc()).all()
    rows_by_cid, html_by_cid = _compose_comment_cards_payload_for_comments(db, comments)

    out: list[CommentOut] = []
    
    for c in comments:
        base = _comment_out(c).model_dump()
        cid_key = str(int(getattr(c, "id", 0) or 0))
        pp = dict(base.get("patch_payload") or {})
        pp["comment_card_rows"] = list(rows_by_cid.get(cid_key) or [])
        pp["comment_card_html"] = str(html_by_cid.get(cid_key) or "")
        base["patch_payload"] = pp
        try:
            summary = _aggregate_votes_for_comment(db, c)
        except Exception:
            summary = None
        if summary is not None:
            # Single source of truth: weighted comment-vote model.
            base["qualified_for_next_release"] = bool(getattr(summary, "qualified_for_next_release", False))
            base["votes_total"] = int(getattr(summary, "raw_total", None) or getattr(summary, "total_votes", 0) or 0)
            base["effective_total_votes"] = int(getattr(summary, "weighted_total", None) or getattr(summary, "effective_total_votes", 0) or 0)
            base["votes_by_main"] = dict(getattr(summary, "votes_by_main", None) or {})
            base["flags_count"] = dict(getattr(summary, "reactions_count", None) or {})
            ap_w = getattr(summary, "weighted_approval_percent", None)
            if ap_w is None:
                ap_w = getattr(summary, "approval_percent", None)
            base["approval_percent"] = ap_w
            base["next_draft_bucket"] = getattr(summary, "bucket", None)
        else:
            base["qualified_for_next_release"] = False
            base["approval_percent"] = None

        
        st = str(getattr(c, "status", "") or "").strip().lower()

        # Reviewer-facing author anonymity:
        # Non-public review/draft comments remain anonymous to other authenticated reviewers.
        # - owner/admin may see the author identity
        if st != "veröffentlicht" and current_user is not None:
            is_admin = bool(getattr(current_user, "is_admin", False))
            is_owner = (getattr(current_user, "id", None) == getattr(c, "user_id", None))
            if (not is_admin) and (not is_owner):
                base["user_id"] = 0
                base["public_author_label"] = base.get("public_author_label") or "Anonym"

        out.append(CommentOut(**base))

    return out


def _qualified_next_draft_comments(
    db: Session,
    comments: list[Comment],
) -> tuple[list[Comment], dict[int, CommentVoteSummaryOut]]:
    """Qualify comments once using the same vote policy as the public Next-Draft view."""
    qualified: list[Comment] = []
    summaries_by_cid: dict[int, CommentVoteSummaryOut] = {}
    for c in list(comments or []):
        try:
            summary = _aggregate_votes_for_comment(db, c)
        except Exception:
            summary = None
        if not summary or not bool(getattr(summary, "qualified_for_next_release", False)):
            continue
        cid = int(getattr(c, "id", 0) or 0)
        qualified.append(c)
        summaries_by_cid[cid] = summary
    return qualified, summaries_by_cid


def _resolve_next_draft_comments(
    db: Session,
    *,
    target_version: ArticleVersion,
    qualified: list[Comment],
) -> Dict[str, Any]:
    """Resolve precedence, conflicts, and final materialization for Next-Draft without writes."""
    precedence_by_cid = _build_precedence_records_for_comments(db, qualified)
    ordered = sorted(
        qualified,
        key=lambda c: _precedence_key_for_cid(
            int(getattr(c, "id", 0) or 0), precedence_by_cid
        ),
        reverse=True,
    )

    _article_minimd, baseline_blocks = _article_version_minimd_bundle(target_version)
    block_order = list(_MERGE_BLOCK_ORDER)

    def _materialize(rows: list[Comment]):
        return code.materialize_article_with_comments(
            block_order=block_order,
            baseline_old_minimd_by_block=dict(baseline_blocks or {}),
            comments=[_comment_to_indiff_input(row) for row in rows],
            require_baseline_hash=True,
        )

    whole = _materialize(ordered)
    conflict_pairs: set[frozenset[int]] = set()
    for conflict in list(getattr(whole, "conflicts", None) or []):
        if not isinstance(conflict, dict):
            continue
        left = conflict.get("left") if isinstance(conflict.get("left"), dict) else {}
        right = conflict.get("right") if isinstance(conflict.get("right"), dict) else {}
        left_cid = int(left.get("cid") or 0)
        right_cid = int(right.get("cid") or 0)
        if left_cid > 0 and right_cid > 0 and left_cid != right_cid:
            conflict_pairs.add(frozenset((left_cid, right_cid)))

    if bool(getattr(whole, "ok", False)):
        selected = list(ordered)
        materialized = whole
    else:
        selected = []
        selected_ids: set[int] = set()
        for c in ordered:
            cid = int(getattr(c, "id", 0) or 0)
            if any(frozenset((cid, prev)) in conflict_pairs for prev in selected_ids):
                continue
            selected.append(c)
            selected_ids.add(cid)

        materialized = _materialize(selected)
        if not bool(getattr(materialized, "ok", False)):
            safe: list[Comment] = []
            for c in ordered:
                trial = [*safe, c]
                trial_result = _materialize(trial)
                if bool(getattr(trial_result, "ok", False)):
                    safe.append(c)
            selected = safe
            materialized = _materialize(selected)

    selected_ids = {
        int(getattr(c, "id", 0) or 0)
        for c in selected
        if int(getattr(c, "id", 0) or 0) > 0
    }
    excluded = [
        c for c in ordered
        if int(getattr(c, "id", 0) or 0) not in selected_ids
    ]
    conflict_pairs_out = sorted(
        [sorted(int(x) for x in pair) for pair in conflict_pairs],
        key=lambda pair: tuple(pair),
    )
    return {
        "ordered": list(ordered),
        "selected": list(selected),
        "excluded": list(excluded),
        "conflict_pairs": conflict_pairs_out,
        "initial_materialization": whole,
        "materialized": materialized,
    }


def _select_next_draft_comments(
    db: Session,
    *,
    target_version: ArticleVersion,
    qualified: list[Comment],
) -> list[Comment]:
    """Compatibility wrapper returning only the final selected Next-Draft comments."""
    resolved = _resolve_next_draft_comments(
        db,
        target_version=target_version,
        qualified=qualified,
    )
    return list(resolved.get("selected") or [])


@app.get(
    "/api/articles/{article_id}/next-draft",
    response_model=List[CommentOut],
)
def list_article_next_draft_comments(
    article_id: int,
    version_id: int | None = Query(
        default=None,
        description=(
            "Optional: auf eine konkrete Artikelversion einschränken. "
            "Default ist die aktuelle (published) Version des Artikels."
        ),
    ),
    db: Session = Depends(get_db),
    current_user: User | None = Depends(get_current_user_optional),
) -> List[CommentOut]:
    """Return public, qualified, conflict-free Next-Draft candidates for an article."""
    article = db.query(Article).filter(Article.id == article_id).one_or_none()
    if article is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Artikel nicht gefunden")

    if version_id is None:
        try:
            if getattr(article, "current_version_id", None) is not None:
                version_id = int(article.current_version_id)
        except Exception:
            version_id = None

    q = db.query(Comment).filter(
        Comment.article_id == article_id,
        Comment.status.in_(_next_draft_comment_statuses()),
    )
    if version_id is not None:
        q = q.filter(Comment.version_id == int(version_id))

    comments = q.all()

    # First compute individual qualification; central selection/conflict resolution follows afterward.
    qualified, summaries_by_cid = _qualified_next_draft_comments(db, comments)

    target_version = (
        db.query(ArticleVersion)
        .filter(
            ArticleVersion.id == int(version_id),
            ArticleVersion.article_id == int(article_id),
        )
        .one_or_none()
        if version_id is not None
        else None
    )
    if target_version is None:
        return []

    comments = _select_next_draft_comments(
        db,
        target_version=target_version,
        qualified=qualified,
    )

    out: list[CommentOut] = []
    for c in comments:
        st = str(getattr(c, "status", "") or "").strip().lower()
        summary = summaries_by_cid.get(int(getattr(c, "id", 0) or 0))
        if summary is None:
            continue

        base = _comment_out(c).model_dump()

        # Reviewer-facing author anonymity:
        # Reviewers may see the content of a non-public review comment without seeing its author.
        # This keeps review access separate from author identity disclosure.
        is_admin = bool(getattr(current_user, "is_admin", False)) if current_user is not None else False
        is_owner = (current_user is not None and getattr(current_user, "id", None) == getattr(c, "user_id", None))
        if st != "veröffentlicht" and current_user is not None and (not is_admin) and (not is_owner):
            base["user_id"] = 0
            
            base["public_author_label"] = base.get("public_author_label") or "Anonym"
            base["export_anonymized_id"] = base.get("export_anonymized_id") or ("anon-" + str(getattr(c, "id", "")))

        # Embed the key vote-summary fields to avoid an N+1 summary request pattern in the frontend.
        
        base["qualified_for_next_release"] = True
        base["votes_total"] = int(getattr(summary, "raw_total", None) or getattr(summary, "total_votes", 0) or 0)
        base["effective_total_votes"] = int(getattr(summary, "weighted_total", None) or getattr(summary, "effective_total_votes", 0) or 0)
        base["votes_by_main"] = dict(getattr(summary, "votes_by_main", None) or {})
        base["flags_count"] = dict(getattr(summary, "reactions_count", None) or {})
        ap = getattr(summary, "weighted_approval_percent", None)
        if ap is None:
            ap = getattr(summary, "approval_percent", None)
        base["approval_percent"] = ap
        base["next_draft_bucket"] = getattr(summary, "bucket", None)

        out.append(CommentOut(**base))

    return out


def _merge_preview_response_for_ordered_comments(
    *,
    aid: int,
    article_row: Article,
    ordered_comments: list[Comment],
    article_version: ArticleVersion | None = None,
) -> Dict[str, Any]:
    """Compose the merge-preview payload from already-loaded article/comment objects so database reads and `indiff.py` composition remain separate and cacheable."""
    cids: list[int] = []
    by_cid: dict[int, Comment] = {}
    for c in list(ordered_comments or []):
        cid = int(getattr(c, "id", 0) or 0)
        if cid <= 0 or cid in by_cid:
            continue
        cids.append(cid)
        by_cid[cid] = c

    if not cids:
        return {
            "ok": True,
            "aid": int(aid),
            "cids_in_apply_order": [],
            "merged_marked_html_full": "",
            "comment_cards_html": "",
            "comment_cards_table": [],
            "comment_card_rows_by_cid": {},
            "comment_cards_by_cid_html": {},
            "applied_parts": [],
            "counts": {"applied": 0, "unapplied": 0},
            "inline_engine_indiff": True,
            "indiff_block_results": {},
        }

    target_version = article_version if article_version is not None else getattr(article_row, "current_version", None)
    if article_row is None or target_version is None:
        raise HTTPException(status_code=404, detail="article_version_not_found")

    mm_full, _ = article_version_to_minimd(target_version)
    mm_blocks = _minimd_blocks_map(mm_full)
    block_order = list(_MERGE_BLOCK_ORDER)

    rendered_baseline_blocks = _site_wrapped_blocks_from_version(target_version)
    baseline_blocks: dict[str, str] = {
        bk: str(rendered_baseline_blocks.get(str(bk)) or _wrap_block(str(bk), ""))
        for bk in block_order
    }

    comments_in: list[code.PersistedCommentInput] = []
    for cid in cids:
        c = by_cid[int(cid)]
        payload = c.patch_payload if isinstance(getattr(c, "patch_payload", None), dict) else {}
        parts_raw = (payload or {}).get("parts") if isinstance(payload, dict) else None
        if not isinstance(parts_raw, list):
            parts_raw = []
        norm_parts: list[code.PersistedCommentPartInput] = []
        for idx, p in enumerate(parts_raw):
            if not isinstance(p, dict):
                continue
            sel_start = p.get("sel_start")
            sel_end = p.get("sel_end")
            norm_parts.append(
                code.PersistedCommentPartInput(
                    part_id=str(p.get("part_id") or f"p{idx+1}"),
                    block_key=str(_merge_norm_block_key(p.get("block_key") or p.get("block_id")) or ""),
                    old_text=str(p.get("old_text") or ""),
                    new_text=str(p.get("new_text") or ""),
                    sel_start=(int(sel_start) if sel_start is not None else None),
                    sel_end=(int(sel_end) if sel_end is not None else None),
                    baseline_hash=str(p.get("baseline_hash") or ""),
                )
            )
        comments_in.append(code.PersistedCommentInput(cid=int(cid), parts=list(norm_parts)))

    _merge_preview_raise_if_loaded_payload_too_large(article_row, [by_cid[cid] for cid in cids])

    result = code.compose_article_merge_preview(
        block_order=list(block_order),
        baseline_old_minimd_by_block={bk: str(mm_blocks.get(bk) or "") for bk in block_order},
        baseline_html_by_block=dict(baseline_blocks),
        comments=list(comments_in),
        fallback_block_key="juristisch",
    )

    rows_by_cid, html_by_cid = _render_comment_cards_by_cid_from_table(
        list(result.comment_cards_table or []),
        expected_cids=list(cids),
    )

    if result.locate_failures:
        raise HTTPException(
            status_code=500,
            detail={
                "error": "published_part_locate_fail",
                "counts": dict(result.counts or {}),
                "applied_parts": list(result.applied_parts or []),
                "unapplied": list(result.locate_failures or []),
            },
        )

    return {
        "ok": True,
        "aid": int(aid),
        "cids_in_apply_order": list(cids),
        "merged_marked_html_full": str(result.merged_marked_html_full or ""),
        "comment_cards_html": str(result.comment_cards_html or ""),
        "comment_cards_table": list(result.comment_cards_table or []),
        "comment_card_rows_by_cid": dict(rows_by_cid or {}),
        "comment_cards_by_cid_html": dict(html_by_cid or {}),
        "applied_parts": list(result.applied_parts or []),
        "counts": dict(result.counts or {}),
        "inline_engine_indiff": True,
        "indiff_block_results": {
            str(bk): {
                "block_key": str(bk),
                "baseline_html": str(getattr(res, "baseline_html", "") or ""),
                "composed_html": str(getattr(res, "composed_html", "") or ""),
                "diagnostics": dict((res.diagnostics or {})),
                "decoration_table": _build_indiff_decoration_table(res),
                "interaction_table": list(res.interaction_table or []),
                "span_registry": list(res.span_registry or []),
                "style_table": list(res.style_table or []),
                "normalized_changes": list(getattr(res, "normalized_changes", []) or []),
                "operations": [
                    {
                        "op_id": str(op.op_id),
                        "block_key": str(op.block_key),
                        "op_type": str(op.op_type),
                        "anchor_source_start": int(op.anchor_source_start),
                        "anchor_source_end": int(op.anchor_source_end),
                        "payload_before": str(op.payload_before or ""),
                        "payload_inside_start": str(op.payload_inside_start or ""),
                        "payload_inside_end": str(op.payload_inside_end or ""),
                        "payload_after": str(op.payload_after or ""),
                        "meta": dict(op.meta or {}),
                    }
                    for op in list(res.operations or [])
                ],
                "delete_segments": list(res.delete_segments or []),
                "insert_points": list(res.insert_points or []),
                "comment_card_rows": list(res.comment_card_rows or []),
                "anchor_audit_rows": list(res.anchor_audit_rows or []),
            }
            for bk, res in dict(result.block_results or {}).items()
        },
    }


def _next_draft_source_fingerprint(article_id: int, version_id: int | None, comments: list[Comment]) -> str:
    rows: list[str] = [f"aid={int(article_id)}", f"vid={int(version_id or 0)}"]
    for c in list(comments or []):
        cid = int(getattr(c, "id", 0) or 0)
        updated = getattr(c, "updated_at", None)
        published = getattr(c, "published_at", None)
        rows.append(
            "|".join([
                str(cid),
                str(getattr(c, "status", "") or ""),
                str(getattr(c, "candidate_status", "") or ""),
                str(updated.isoformat() if isinstance(updated, datetime) else updated or ""),
                str(published.isoformat() if isinstance(published, datetime) else published or ""),
            ])
        )
    raw = "\n".join(rows)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


@app.get("/api/articles/{article_id}/layers/next-draft", response_model=dict)
def get_article_next_draft_layer(
    article_id: int,
    version_id: int | None = Query(
        default=None,
        description="Optional: auf eine konkrete Artikelversion einschränken. Default ist die aktuelle Version.",
    ),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Return the short-lived cached public Next-Draft layer. Cache hits avoid comment/vote/merge database work; misses perform read-only selection followed by in-memory composition."""
    article = db.query(Article).filter(Article.id == int(article_id)).one_or_none()
    if article is None or article.current_version is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Artikel nicht gefunden")
    if _article_public_visibility_status(db, article) == "hidden":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Artikel nicht gefunden")

    if version_id is None:
        try:
            version_id = int(article.current_version_id) if article.current_version_id is not None else None
        except Exception:
            version_id = None

    cached = _next_draft_layer_from_cache(int(article_id), int(version_id or 0))
    if cached is not None:
        return cached

    q = db.query(Comment).filter(
        Comment.article_id == int(article_id),
        Comment.status.in_(_next_draft_comment_statuses()),
    )
    if version_id is not None:
        q = q.filter(Comment.version_id == int(version_id))

    candidates = q.order_by(Comment.updated_at.desc(), Comment.id.desc()).all()
    qualified, _summaries_by_cid = _qualified_next_draft_comments(db, candidates)

    target_version = (
        db.query(ArticleVersion)
        .filter(
            ArticleVersion.id == int(version_id),
            ArticleVersion.article_id == int(article_id),
        )
        .one_or_none()
        if version_id is not None
        else None
    )
    selected = (
        _select_next_draft_comments(
            db,
            target_version=target_version,
            qualified=qualified,
        )
        if target_version is not None
        else []
    )

    cids = [int(getattr(c, "id", 0) or 0) for c in selected if int(getattr(c, "id", 0) or 0) > 0]
    _merge_preview_raise_if_request_too_large(cids=list(cids))

    started_at = time.monotonic()
    if not _merge_preview_try_acquire():
        raise _merge_preview_busy_exception()
    try:
        payload = _merge_preview_response_for_ordered_comments(
            aid=int(article_id),
            article_row=article,
            ordered_comments=list(selected),
            article_version=target_version,
        )
        _merge_preview_raise_if_time_budget_exceeded(started_at)
    finally:
        _merge_preview_release()
    ttl = _next_draft_layer_cache_seconds()
    generated_at = datetime.utcnow()
    payload.update({
        "layer_type": "next_draft",
        "layer_id": "ND",
        "article_id": int(article_id),
        "version_id": int(version_id or 0),
        "comment_ids": list(cids),
        "is_empty": not bool(cids),
        "generated_at": generated_at.isoformat() + "Z",
        "expires_in_seconds": int(ttl),
        "source_hash": _next_draft_source_fingerprint(int(article_id), int(version_id or 0), list(candidates or [])),
        "cache": {"hit": False, "ttl_seconds": int(ttl)},
    })
    _next_draft_layer_store_cache(int(article_id), int(version_id or 0), payload)
    return payload


@app.get("/api/articles/{article_id}/layers/last-version", response_model=dict)
def get_article_last_version_layer(
    article_id: int,
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Return the public previous-published to current-published version layer, rendered exclusively through `indiff.py`."""
    article = db.query(Article).filter(Article.id == int(article_id)).one_or_none()
    if article is None or article.current_version is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Artikel nicht gefunden")
    if _article_public_visibility_status(db, article) == "hidden":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Artikel nicht gefunden")

    current_version = article.current_version
    if not _is_published_article_version(current_version):
        return {
            "ok": True,
            "layer_type": "last_version",
            "layer_id": "LW",
            "article_id": int(article_id),
            "current_version_id": None,
            "previous_version_id": None,
            "is_empty": True,
            "inline_engine_indiff": True,
            "indiff_block_results": {},
        }

    published_versions = [
        v for v in list(getattr(article, "versions", None) or [])
        if _is_published_article_version(v)
    ]
    published_versions.sort(key=lambda v: int(getattr(v, "id", 0) or 0))
    current_id = int(getattr(current_version, "id", 0) or 0)
    current_idx = next(
        (idx for idx, v in enumerate(published_versions) if int(getattr(v, "id", 0) or 0) == current_id),
        -1,
    )
    previous_version = published_versions[current_idx - 1] if current_idx > 0 else None
    if previous_version is None:
        return {
            "ok": True,
            "layer_type": "last_version",
            "layer_id": "LW",
            "article_id": int(article_id),
            "current_version_id": current_id,
            "current_version_label": str(getattr(current_version, "version_label", "") or ""),
            "previous_version_id": None,
            "is_empty": True,
            "inline_engine_indiff": True,
            "indiff_block_results": {},
        }

    _old_doc, old_blocks = _article_version_minimd_bundle(previous_version)
    _new_doc, new_blocks = _article_version_minimd_bundle(current_version)
    block_results = code.compose_article_version_diff(
        block_order=list(_MERGE_BLOCK_ORDER),
        old_minimd_by_block=dict(old_blocks or {}),
        new_minimd_by_block=dict(new_blocks or {}),
    )

    serialized_blocks: dict[str, dict[str, Any]] = {}
    changed_blocks = 0
    for bk, res in dict(block_results or {}).items():
        diagnostics = dict(getattr(res, "diagnostics", None) or {})
        if int(diagnostics.get("change_requests_total") or 0) > 0:
            changed_blocks += 1
        serialized_blocks[str(bk)] = {
            "block_key": str(bk),
            "baseline_html": str(getattr(res, "baseline_html", "") or ""),
            "composed_html": str(getattr(res, "composed_html", "") or ""),
            "diagnostics": diagnostics,
            "interaction_table": list(getattr(res, "interaction_table", None) or []),
            "span_registry": list(getattr(res, "span_registry", None) or []),
            "style_table": list(getattr(res, "style_table", None) or []),
            "normalized_changes": list(getattr(res, "normalized_changes", None) or []),
            "operations": [
                {
                    "op_id": str(op.op_id),
                    "block_key": str(op.block_key),
                    "op_type": str(op.op_type),
                    "anchor_source_start": int(op.anchor_source_start),
                    "anchor_source_end": int(op.anchor_source_end),
                    "payload_before": str(op.payload_before or ""),
                    "payload_inside_start": str(op.payload_inside_start or ""),
                    "payload_inside_end": str(op.payload_inside_end or ""),
                    "payload_after": str(op.payload_after or ""),
                    "meta": dict(op.meta or {}),
                }
                for op in list(getattr(res, "operations", None) or [])
            ],
            "delete_segments": list(getattr(res, "delete_segments", None) or []),
            "insert_points": list(getattr(res, "insert_points", None) or []),
            "comment_card_rows": [],
            "anchor_audit_rows": list(getattr(res, "anchor_audit_rows", None) or []),
        }

    return {
        "ok": True,
        "layer_type": "last_version",
        "layer_id": "LW",
        "article_id": int(article_id),
        "current_version_id": current_id,
        "current_version_label": str(getattr(current_version, "version_label", "") or ""),
        "previous_version_id": int(previous_version.id),
        "previous_version_label": str(getattr(previous_version, "version_label", "") or ""),
        "is_empty": changed_blocks == 0,
        "inline_engine_indiff": True,
        "indiff_block_results": serialized_blocks,
    }


_RELEASE_PREVIEW_TERMINAL_STATUSES = {
    "archiviert",
    "integriert",
    "gelöscht",
    "geloescht",
    "abgelehnt",
    "rejected",
}
_RELEASE_PREVIEW_TERMINAL_LIFECYCLES = {"archived", "integrated", "deleted", "rejected"}


def _release_preview_comment_is_active(comment: Comment) -> bool:
    status_key = str(getattr(comment, "status", "") or "").strip().lower()
    lifecycle_key = str(getattr(comment, "lifecycle_status", "") or "").strip().lower()
    return (
        status_key not in _RELEASE_PREVIEW_TERMINAL_STATUSES
        and lifecycle_key not in _RELEASE_PREVIEW_TERMINAL_LIFECYCLES
    )


def _build_release_preview(db: Session) -> Dict[str, Any]:
    """Build the current release cut read-only using the same qualification, precedence, conflict, and materialization logic as public Next-Draft."""
    items: list[dict[str, Any]] = []
    totals = {
        "articles_checked": 0,
        "articles_with_qualified": 0,
        "articles_would_change": 0,
        "qualified_comments": 0,
        "selected_comments": 0,
        "excluded_comments": 0,
        "selection_conflicts": 0,
        "rebase_safe": 0,
        "rebase_would_archive": 0,
        "blocking_articles": 0,
    }

    articles = db.query(Article).order_by(*_article_ordering()).all()
    for article in articles:
        target_version = getattr(article, "current_version", None)
        if target_version is None:
            continue
        totals["articles_checked"] += 1
        article_id = int(getattr(article, "id", 0) or 0)
        version_id = int(getattr(target_version, "id", 0) or 0)
        if article_id <= 0 or version_id <= 0:
            continue

        candidates = (
            db.query(Comment)
            .filter(
                Comment.article_id == article_id,
                Comment.version_id == version_id,
                Comment.status.in_(_next_draft_comment_statuses()),
            )
            .order_by(Comment.updated_at.desc(), Comment.id.desc())
            .all()
        )
        qualified, _summaries_by_cid = _qualified_next_draft_comments(db, candidates)
        if not qualified:
            continue

        totals["articles_with_qualified"] += 1
        resolved = _resolve_next_draft_comments(
            db,
            target_version=target_version,
            qualified=qualified,
        )
        ordered = list(resolved.get("ordered") or [])
        selected = list(resolved.get("selected") or [])
        excluded = list(resolved.get("excluded") or [])
        conflict_pairs = list(resolved.get("conflict_pairs") or [])
        initial_materialization = resolved.get("initial_materialization")
        materialized = resolved.get("materialized")

        ordered_ids = [int(getattr(c, "id", 0) or 0) for c in ordered]
        selected_ids = [int(getattr(c, "id", 0) or 0) for c in selected]
        selected_id_set = {cid for cid in selected_ids if cid > 0}
        excluded_ids = [int(getattr(c, "id", 0) or 0) for c in excluded]

        totals["qualified_comments"] += len(ordered_ids)
        totals["selected_comments"] += len(selected_ids)
        totals["excluded_comments"] += len(excluded_ids)
        totals["selection_conflicts"] += len(conflict_pairs)

        materialization_ok = bool(getattr(materialized, "ok", False))
        would_change = bool(selected_ids and materialization_ok)
        if would_change:
            totals["articles_would_change"] += 1
        if not materialization_ok:
            totals["blocking_articles"] += 1

        rebase_rows: list[dict[str, Any]] = []
        if would_change:
            _old_article_minimd, old_baseline = _article_version_minimd_bundle(target_version)
            _persisted_blocks, _persisted_minimd, new_baseline = _release_persisted_bundle_from_materialized(materialized)
            remaining = (
                db.query(Comment)
                .filter(
                    Comment.article_id == article_id,
                    Comment.version_id == version_id,
                )
                .order_by(Comment.id.asc())
                .all()
            )
            for comment in remaining:
                cid = int(getattr(comment, "id", 0) or 0)
                if cid <= 0 or cid in selected_id_set or not _release_preview_comment_is_active(comment):
                    continue
                rebase = code.check_comment_base_rebase(
                    block_order=list(_MERGE_BLOCK_ORDER),
                    old_baseline_minimd_by_block=dict(old_baseline or {}),
                    new_baseline_minimd_by_block=dict(new_baseline or {}),
                    comment=_comment_to_indiff_input(comment),
                    fallback_block_key="juristisch",
                )
                safe = bool(getattr(rebase, "safe", False))
                if safe:
                    totals["rebase_safe"] += 1
                else:
                    totals["rebase_would_archive"] += 1
                rebase_rows.append({
                    "comment_id": cid,
                    "status": str(getattr(comment, "status", "") or ""),
                    "lifecycle_status": str(getattr(comment, "lifecycle_status", "") or ""),
                    "comment_mode": str(getattr(comment, "comment_mode", "change") or "change"),
                    "safe": safe,
                    "rebase_status": str(getattr(rebase, "status", "") or ""),
                    "error_codes": [
                        str(row.get("code") or "")
                        for row in list(getattr(rebase, "errors", None) or [])
                        if isinstance(row, dict) and str(row.get("code") or "").strip()
                    ],
                })

        initial_errors = [
            str(row.get("code") or "")
            for row in list(getattr(initial_materialization, "errors", None) or [])
            if isinstance(row, dict) and str(row.get("code") or "").strip()
        ]
        final_errors = [
            str(row.get("code") or "")
            for row in list(getattr(materialized, "errors", None) or [])
            if isinstance(row, dict) and str(row.get("code") or "").strip()
        ]
        items.append({
            "article_id": article_id,
            "public_code": str(getattr(article, "public_code", "") or ""),
            "title": str(getattr(article, "title", "") or ""),
            "version_id": version_id,
            "version_label": str(getattr(target_version, "version_label", "") or ""),
            "qualified_comment_ids": ordered_ids,
            "selected_comment_ids": selected_ids,
            "selected_comments": [
                {
                    "comment_id": int(getattr(c, "id", 0) or 0),
                    "comment_mode": str(getattr(c, "comment_mode", "change") or "change"),
                }
                for c in selected
            ],
            "excluded_comment_ids": excluded_ids,
            "conflict_pairs": conflict_pairs,
            "initial_error_codes": initial_errors,
            "materialization_ok": materialization_ok,
            "final_error_codes": final_errors,
            "would_change": would_change,
            "rebase": rebase_rows,
        })

    return {
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "read_only": True,
        "ready": int(totals["blocking_articles"]) == 0,
        "totals": totals,
        "articles": items,
    }


def _release_materialized_content_blocks(materialized: Any) -> dict[str, str]:
    """Convert an `indiff` materialization result into persistable `ArticleVersion.content_blocks`."""
    if materialized is None or not bool(getattr(materialized, "ok", False)):
        raise RuntimeError("release_materialization_invalid")
    minimd_by_block = dict(getattr(materialized, "minimd_by_block", None) or {})
    out: dict[str, str] = {}
    for block_key in _minimd_block_order():
        key = str(block_key)
        body = str(minimd_by_block.get(key) or "")
        if key == "meta":
            out[key] = body
        else:
            out[key] = code.minimd_to_html_text(key, body) if body else ""
    return out


def _release_persisted_bundle_from_materialized(materialized: Any) -> tuple[dict[str, str], str, dict[str, str]]:
    """Build the persisted content-block bundle and verify its canonical MiniMD roundtrip representation."""
    content_blocks = _release_materialized_content_blocks(materialized)
    article_minimd, _order = code.content_blocks_to_minimd(
        dict(content_blocks),
        block_order=_minimd_block_order(),
    )
    block_map = _minimd_blocks_map(str(article_minimd or ""))
    block_map.pop("_all", None)
    return (
        dict(content_blocks),
        str(article_minimd or ""),
        {str(k): str(v or "") for k, v in dict(block_map or {}).items()},
    )


def _release_apply_meta_to_article(db: Session, article: Article, meta_minimd: str) -> None:
    """Apply an accepted persisted `meta` block to the corresponding article identity/TOC fields."""
    meta = _parse_minimd_meta_lines(str(meta_minimd or ""))
    new_public_code = str(meta.get("Artikel-Kennung") or "").strip()
    new_title = str(meta.get("Artikel-Titel") or "").strip()
    new_toc_title = str(meta.get("Artikel-Kurztitel") or "").strip() or None

    if new_public_code and new_public_code != str(getattr(article, "public_code", "") or ""):
        duplicate = (
            db.query(Article.id)
            .filter(
                Article.public_code == new_public_code,
                Article.id != int(article.id),
            )
            .first()
        )
        if duplicate is not None:
            raise RuntimeError(f"release_meta_public_code_conflict:{new_public_code}")
        article.public_code = new_public_code
        article.type = _derive_article_type_from_public_code(new_public_code)
        article.slug = _unique_article_slug(db, new_public_code, new_title or str(article.title or ""))

    if new_title:
        article.title = new_title
    article.toc_title = new_toc_title
    if "Artikel im Inhaltsverzeichnis" in meta:
        article.show_in_toc = _meta_bool_value(meta.get("Artikel im Inhaltsverzeichnis"))


def _release_patch_payload_from_rebase(comment: Comment, rebased_comment: Any, *, new_version_id: int) -> dict[str, Any]:
    payload = dict(comment.patch_payload or {}) if isinstance(getattr(comment, "patch_payload", None), dict) else {}
    payload["version"] = 2
    payload["base_version_id"] = int(new_version_id)
    payload["exact_selection"] = True
    payload["parts"] = [asdict(part) for part in list(getattr(rebased_comment, "parts", None) or [])]
    payload.pop("semantic_events", None)
    payload.pop("validation", None)
    payload.pop("diff_html", None)
    payload.pop("diff_html_compact", None)
    payload.pop("patch_stats", None)
    payload.pop("diff_box_data", None)
    payload.pop("exact_selection_validation", None)
    return payload


def _release_update_comment_anchor_from_patch(comment: Comment) -> None:
    payload = comment.patch_payload if isinstance(getattr(comment, "patch_payload", None), dict) else {}
    parts = payload.get("parts") if isinstance(payload.get("parts"), list) else []
    first = next((dict(row) for row in parts if isinstance(row, dict)), None)
    if not first:
        return
    anchor = {
        "block_key": str(first.get("block_key") or ""),
        "selected_text": str(first.get("old_text") or ""),
        "sel_start": first.get("sel_start"),
        "sel_end": first.get("sel_end"),
    }
    comment.anchor = dict(anchor)
    comment.anchor_payload = dict(anchor)


def _release_plan_fingerprint(rows: list[dict[str, Any]]) -> str:
    canonical = [
        {
            "article_id": int(row.get("article_id") or 0),
            "base_version_id": int(row.get("base_version_id") or 0),
            "selected_comment_ids": [int(x) for x in list(row.get("selected_comment_ids") or [])],
            "result_sha256": str(row.get("result_sha256") or ""),
        }
        for row in sorted(list(rows or []), key=lambda x: int(x.get("article_id") or 0))
    ]
    raw = json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _release_build_execution_plan(db: Session) -> dict[str, Any]:
    """Rebuild the write-side release execution plan from the current database state."""
    preview = _build_release_preview(db)
    if not bool(preview.get("ready", False)):
        raise RuntimeError("release_preview_not_ready")

    rows: list[dict[str, Any]] = []
    for item in list(preview.get("articles") or []):
        if not bool(item.get("would_change", False)):
            continue
        article_id = int(item.get("article_id") or 0)
        base_version_id = int(item.get("version_id") or 0)
        selected_ids = [int(x) for x in list(item.get("selected_comment_ids") or []) if int(x or 0) > 0]
        if article_id <= 0 or base_version_id <= 0 or not selected_ids:
            raise RuntimeError(f"release_plan_invalid:{article_id}:{base_version_id}")

        article = db.query(Article).filter(Article.id == article_id).one_or_none()
        target_version = (
            db.query(ArticleVersion)
            .filter(
                ArticleVersion.id == base_version_id,
                ArticleVersion.article_id == article_id,
            )
            .one_or_none()
        )
        if article is None or target_version is None or int(getattr(article, "current_version_id", 0) or 0) != base_version_id:
            raise RuntimeError(f"release_base_changed:{article_id}")

        selected = (
            db.query(Comment)
            .filter(Comment.id.in_(selected_ids))
            .order_by(Comment.id.asc())
            .all()
        )
        selected_by_id = {int(c.id): c for c in selected}
        selected = [selected_by_id[cid] for cid in selected_ids if cid in selected_by_id]
        if len(selected) != len(selected_ids):
            raise RuntimeError(f"release_selected_comment_missing:{article_id}")
        if any(int(c.article_id) != article_id or int(c.version_id) != base_version_id for c in selected):
            raise RuntimeError(f"release_selected_comment_base_mismatch:{article_id}")

        _old_minimd, old_baseline = _article_version_minimd_bundle(target_version)
        materialized = code.materialize_article_with_comments(
            block_order=list(_MERGE_BLOCK_ORDER),
            baseline_old_minimd_by_block=dict(old_baseline or {}),
            comments=[_comment_to_indiff_input(c) for c in selected],
            fallback_block_key="juristisch",
            require_baseline_hash=True,
        )
        if not bool(getattr(materialized, "ok", False)):
            raise RuntimeError(f"release_materialization_failed:{article_id}")

        content_blocks, persisted_minimd, new_baseline = _release_persisted_bundle_from_materialized(materialized)
        result_sha256 = hashlib.sha256(persisted_minimd.encode("utf-8")).hexdigest()
        modes = [str(getattr(c, "comment_mode", "change") or "change") for c in selected]
        if "new_article" in modes and "delete_article" in modes:
            raise RuntimeError(f"release_mode_conflict:{article_id}")

        rows.append({
            "article": article,
            "target_version": target_version,
            "old_baseline": dict(old_baseline or {}),
            "materialized": materialized,
            "content_blocks": dict(content_blocks),
            "new_baseline": dict(new_baseline),
            "persisted_minimd": str(persisted_minimd),
            "selected": selected,
            "selected_comment_ids": list(selected_ids),
            "article_id": article_id,
            "base_version_id": base_version_id,
            "result_sha256": result_sha256,
            "delete_article": "delete_article" in modes,
        })

    if not rows:
        raise RuntimeError("release_no_changes")

    public_rows = [
        {
            "article_id": int(row["article_id"]),
            "base_version_id": int(row["base_version_id"]),
            "selected_comment_ids": list(row["selected_comment_ids"]),
            "result_sha256": str(row["result_sha256"]),
            "delete_article": bool(row["delete_article"]),
        }
        for row in rows
    ]
    return {
        "preview": preview,
        "rows": rows,
        "public_rows": public_rows,
        "fingerprint": _release_plan_fingerprint(public_rows),
    }


def _execute_release_cut(
    db: Session,
    *,
    performed_by_user_id: int | None = None,
    release_now: datetime | None = None,
    version_label: str | None = None,
    snapshot_base_dir: Path | str | None = None,
) -> dict[str, Any]:
    """Execute the release cut with one database commit: acquire the write boundary, rebuild the plan, create the pre-release snapshot, prepare new versions/integration/rebase changes, and roll back plus remove a newly created snapshot on pre-commit failure."""
    if release_now is None:
        release_now_aware = datetime.now(timezone.utc)
    elif release_now.tzinfo is None:
        release_now_aware = release_now.replace(tzinfo=timezone.utc)
    else:
        release_now_aware = release_now.astimezone(timezone.utc)
    release_now_db = release_now_aware.replace(tzinfo=None)
    release_label = str(version_label or release_now_aware.strftime("release-%Y%m%d-%H%M%S")).strip()
    if not release_label:
        raise RuntimeError("release_version_label_empty")

    snapshot_path: Path | None = None
    committed = False
    result: dict[str, Any] = {}

    with SQLITE_WRITE_LOCK:
        try:
            # SQLite: block competing writers from other processes through the release commit boundary.
            # FastAPI dependencies may already have opened a read transaction on this session;
            # close that transaction before entering the explicit release write boundary.
            # the existing read transaction is deliberately ended before the exclusive release cut.
            if str(getattr(engine.dialect, "name", "") or "").lower() == "sqlite":
                if db.in_transaction():
                    db.rollback()
                db.execute(text("BEGIN IMMEDIATE"))

            plan = _release_build_execution_plan(db)
            plan_rows = list(plan.get("rows") or [])
            plan_fingerprint = str(plan.get("fingerprint") or "")

            for row in plan_rows:
                exists = (
                    db.query(ArticleVersion.id)
                    .filter(
                        ArticleVersion.article_id == int(row["article_id"]),
                        ArticleVersion.version_label == release_label,
                    )
                    .first()
                )
                if exists is not None:
                    raise RuntimeError(f"release_version_label_exists:{row['article_id']}:{release_label}")

            snapshot_root = Path(snapshot_base_dir).resolve() if snapshot_base_dir is not None else BASE_DIR
            try:
                snapshot_result = export_snapshot.export_snapshot_release(
                    base_dir=snapshot_root,
                    now=release_now_aware,
                    web_version=_git_describe_ref(BASE_DIR),
                )
            except TypeError as exc:
                raise RuntimeError(
                    "release_snapshot_exporter_incompatible: expected v10 read-only exporter API"
                ) from exc
            snapshot_raw = str((snapshot_result or {}).get("html_path") or "").strip()
            if not snapshot_raw:
                raise RuntimeError("release_snapshot_missing_path")
            snapshot_path = Path(snapshot_raw).resolve()
            if not snapshot_path.is_file():
                raise RuntimeError("release_snapshot_missing_file")

            article_results: list[dict[str, Any]] = []
            total_rebased = 0
            total_archived = 0
            total_integrated = 0

            for row in plan_rows:
                article: Article = row["article"]
                target_version: ArticleVersion = row["target_version"]
                materialized = row["materialized"]
                selected: list[Comment] = list(row["selected"])
                selected_ids = {int(c.id) for c in selected}
                delete_article = bool(row["delete_article"])

                content_blocks = dict(row["content_blocks"])
                if not delete_article:
                    _release_apply_meta_to_article(db, article, str(content_blocks.get("meta") or ""))

                new_version = ArticleVersion(
                    article_id=int(article.id),
                    version_label=release_label,
                    content_blocks=dict(content_blocks),
                    status="archived" if delete_article else "published",
                    published_at=None if delete_article else release_now_db,
                    created_at=release_now_db,
                    created_by_user_id=(int(performed_by_user_id) if performed_by_user_id is not None else None),
                )
                db.add(new_version)
                db.flush()

                old_version_id = int(target_version.id)
                article.current_version_id = int(new_version.id)
                article.updated_at = release_now_db
                db.add(article)

                for comment in selected:
                    comment.status = "integriert"
                    comment.lifecycle_status = "integrated"
                    comment.updated_at = release_now_db
                    db.add(comment)
                    total_integrated += 1

                new_baseline = dict(row["new_baseline"] or {})
                remaining = (
                    db.query(Comment)
                    .filter(
                        Comment.article_id == int(article.id),
                        Comment.version_id == old_version_id,
                    )
                    .order_by(Comment.id.asc())
                    .all()
                )
                article_rebased = 0
                article_archived = 0
                article_rebased_ids: list[int] = []
                article_archived_ids: list[int] = []
                for comment in remaining:
                    cid = int(getattr(comment, "id", 0) or 0)
                    if cid <= 0 or cid in selected_ids or not _release_preview_comment_is_active(comment):
                        continue

                    if delete_article:
                        safe = False
                        rebase = None
                    else:
                        rebase = code.check_comment_base_rebase(
                            block_order=list(_MERGE_BLOCK_ORDER),
                            old_baseline_minimd_by_block=dict(row["old_baseline"] or {}),
                            new_baseline_minimd_by_block=dict(new_baseline or {}),
                            comment=_comment_to_indiff_input(comment),
                            fallback_block_key="juristisch",
                        )
                        safe = bool(getattr(rebase, "safe", False))

                    if safe and rebase is not None and getattr(rebase, "rebased_comment", None) is not None:
                        comment.version_id = int(new_version.id)
                        comment.patch_payload = _release_patch_payload_from_rebase(
                            comment,
                            rebase.rebased_comment,
                            new_version_id=int(new_version.id),
                        )
                        materialized_comment = getattr(rebase, "materialized", None)
                        if materialized_comment is not None:
                            comment.proposal_text = str(getattr(materialized_comment, "article_minimd", None) or comment.proposal_text or "")
                        comment.updated_at = release_now_db
                        _release_update_comment_anchor_from_patch(comment)
                        db.add(comment)
                        db.flush()
                        _sync_comment_diff_state_from_indiff(
                            db,
                            comment,
                            comment_minimd_text=str(comment.proposal_text or ""),
                        )
                        article_rebased += 1
                        article_rebased_ids.append(cid)
                        total_rebased += 1
                    else:
                        comment.status = "archiviert"
                        comment.lifecycle_status = "archived"
                        comment.updated_at = release_now_db
                        db.add(comment)
                        article_archived += 1
                        article_archived_ids.append(cid)
                        total_archived += 1

                preview_item = next(
                    (
                        item for item in list((plan.get("preview") or {}).get("articles") or [])
                        if int(item.get("article_id") or 0) == int(article.id)
                    ),
                    {},
                )
                article_results.append({
                    "article_id": int(article.id),
                    "old_version_id": old_version_id,
                    "new_version_id": int(new_version.id),
                    "version_label": release_label,
                    "version_status": str(new_version.status),
                    "result_sha256": str(row["result_sha256"]),
                    "qualified_comment_ids": [int(x) for x in list(preview_item.get("qualified_comment_ids") or [])],
                    "selected_comment_ids": sorted(selected_ids),
                    "excluded_comment_ids": [int(x) for x in list(preview_item.get("excluded_comment_ids") or [])],
                    "rebased_comment_ids": sorted(article_rebased_ids),
                    "archived_comment_ids": sorted(article_archived_ids),
                    "rebased_comments": article_rebased,
                    "archived_comments": article_archived,
                    "deleted_from_public": delete_article,
                })

            execution_by_article_id = {
                int(row.get("article_id") or 0): dict(row)
                for row in list(article_results or [])
                if int(row.get("article_id") or 0) > 0
            }
            comment_history_articles: list[dict[str, Any]] = []
            for preview_item in list((plan.get("preview") or {}).get("articles") or []):
                article_id = int(preview_item.get("article_id") or 0)
                if article_id <= 0:
                    continue
                executed = execution_by_article_id.get(article_id, {})
                comment_history_articles.append({
                    "article_id": article_id,
                    "version_id": int(preview_item.get("version_id") or 0),
                    "qualified_comment_ids": [int(x) for x in list(preview_item.get("qualified_comment_ids") or [])],
                    "selected_comment_ids": [int(x) for x in list(preview_item.get("selected_comment_ids") or [])],
                    "excluded_comment_ids": [int(x) for x in list(preview_item.get("excluded_comment_ids") or [])],
                    "rebased_comment_ids": [int(x) for x in list(executed.get("rebased_comment_ids") or [])],
                    "archived_comment_ids": [int(x) for x in list(executed.get("archived_comment_ids") or [])],
                })

            config_material = {
                "small": {
                    "thresholds": list(_comment_nextdraft_thresholds("small")),
                    "max_disapproval": _comment_nextdraft_disapproval_cap("small"),
                },
                "medium": {
                    "thresholds": list(_comment_nextdraft_thresholds("medium")),
                    "max_disapproval": _comment_nextdraft_disapproval_cap("medium"),
                },
                "large": {
                    "thresholds": list(_comment_nextdraft_thresholds("large")),
                    "max_disapproval": _comment_nextdraft_disapproval_cap("large"),
                },
                "comment_statuses": list(_next_draft_comment_statuses()),
            }
            config_hash = hashlib.sha256(
                json.dumps(config_material, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
            ).hexdigest()

            release_run = ReleaseRun(
                created_at=release_now_db,
                performed_by_user_id=(int(performed_by_user_id) if performed_by_user_id is not None else None),
                from_draft_version_label=None,
                to_current_version_label=release_label,
                config_hash=config_hash,
                policy_snapshot={
                    "resolver": "next-draft-shared-v1",
                    "plan_fingerprint": plan_fingerprint,
                    "preview_totals": dict((plan.get("preview") or {}).get("totals") or {}),
                },
                details={
                    "snapshot_html": str(snapshot_path),
                    "articles": [dict(x) for x in article_results],
                    "comment_history_articles": [dict(x) for x in comment_history_articles],
                    "integrated_comments": int(total_integrated),
                    "rebased_comments": int(total_rebased),
                    "archived_comments": int(total_archived),
                },
            )
            db.add(release_run)
            db.flush()
            db.commit()
            committed = True

            result = {
                "ok": True,
                "release_run_id": int(release_run.id),
                "version_label": release_label,
                "released_at": release_now_aware.isoformat(),
                "plan_fingerprint": plan_fingerprint,
                "snapshot_html": str(snapshot_path),
                "articles": article_results,
                "totals": {
                    "articles_changed": len(article_results),
                    "integrated_comments": int(total_integrated),
                    "rebased_comments": int(total_rebased),
                    "archived_comments": int(total_archived),
                },
            }
        except Exception:
            _rollback_db_quietly(db)
            if snapshot_path is not None and not committed:
                try:
                    if snapshot_path.is_file():
                        snapshot_path.unlink()
                except OSError:
                    pass
            raise

    if committed:
        _clear_public_runtime_caches()
    return result


def _release_admin_state_payload(db: Session) -> Dict[str, Any]:
    st = _release_admin_state()
    last_run = db.query(ReleaseRun).order_by(ReleaseRun.id.desc()).first()
    return {
        "state": str(st.get("release_state") or "normal"),
        "release_focus_started_at": st.get("release_focus_started_at"),
        "release_focus_started_by_user_id": st.get("release_focus_started_by_user_id"),
        "updated_at": st.get("updated_at"),
        "submit_to_review_blocked": _release_submit_blocked(),
        "freeze_enabled": bool(getattr(settings, "RELEASE_FREEZE_ENABLED", True)),
        "last_release": (
            {
                "release_run_id": int(last_run.id),
                "created_at": last_run.created_at,
                "version_label": str(last_run.to_current_version_label or ""),
            }
            if last_run is not None
            else None
        ),
    }



@app.get("/api/admin/release/state", response_model=dict)
def admin_release_state(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Dict[str, Any]:
    require_admin(current_user)
    return _release_admin_state_payload(db)


@app.get("/api/admin/release/comment-history", response_model=dict)
def admin_release_comment_history(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Dict[str, Any]:
    """Return known release-history facts for comments without inventing facts for legacy releases that did not record them."""
    require_admin(current_user)
    runs = db.query(ReleaseRun).order_by(ReleaseRun.id.asc()).all()
    comments: dict[str, dict[str, Any]] = {}
    incomplete_legacy_runs = 0

    def _ids(row: dict[str, Any], key: str) -> list[int]:
        if key not in row or not isinstance(row.get(key), list):
            return []
        out: list[int] = []
        for raw in list(row.get(key) or []):
            try:
                value = int(raw)
            except Exception:
                continue
            if value > 0 and value not in out:
                out.append(value)
        return out

    for run in runs:
        details = dict(run.details or {}) if isinstance(run.details, dict) else {}
        if isinstance(details.get("comment_history_articles"), list):
            rows = [dict(x) for x in details.get("comment_history_articles") or [] if isinstance(x, dict)]
            complete = True
        else:
            rows = [dict(x) for x in details.get("articles") or [] if isinstance(x, dict)]
            complete = False
            incomplete_legacy_runs += 1

        for row in rows:
            qualified = set(_ids(row, "qualified_comment_ids")) if "qualified_comment_ids" in row else set()
            selected = set(_ids(row, "selected_comment_ids")) if "selected_comment_ids" in row else set()
            excluded = set(_ids(row, "excluded_comment_ids")) if "excluded_comment_ids" in row else set()
            rebased = set(_ids(row, "rebased_comment_ids")) if "rebased_comment_ids" in row else set()
            archived = set(_ids(row, "archived_comment_ids")) if "archived_comment_ids" in row else set()
            all_ids = qualified | selected | excluded | rebased | archived

            for cid in sorted(all_ids):
                key = str(cid)
                rec = comments.setdefault(key, {
                    "comment_id": cid,
                    "qualified_count": 0,
                    "selected_count": 0,
                    "excluded_count": 0,
                    "rebased_count": 0,
                    "archived_count": 0,
                    "skipped_count": 0,
                    "latest_action": None,
                    "runs": [],
                })
                facts = {
                    "qualified": cid in qualified,
                    "selected": cid in selected,
                    "excluded": cid in excluded,
                    "rebased": cid in rebased,
                    "archived": cid in archived,
                }
                if facts["qualified"]:
                    rec["qualified_count"] += 1
                if facts["selected"]:
                    rec["selected_count"] += 1
                if facts["excluded"]:
                    rec["excluded_count"] += 1
                if facts["rebased"]:
                    rec["rebased_count"] += 1
                if facts["archived"]:
                    rec["archived_count"] += 1
                if facts["qualified"] and not facts["selected"]:
                    rec["skipped_count"] += 1

                action = (
                    "archived" if facts["archived"] else
                    "rebased" if facts["rebased"] else
                    "selected" if facts["selected"] else
                    "excluded" if facts["excluded"] else
                    "qualified" if facts["qualified"] else None
                )
                if action:
                    rec["latest_action"] = action
                rec["runs"].append({
                    "release_run_id": int(run.id),
                    "version_label": str(run.to_current_version_label or ""),
                    "created_at": run.created_at,
                    "article_id": int(row.get("article_id") or 0),
                    "facts": facts,
                    "history_complete": complete,
                })

    return {
        "generated_at": datetime.utcnow(),
        "release_runs": len(runs),
        "incomplete_legacy_runs": int(incomplete_legacy_runs),
        "comments": comments,
    }


@app.post("/api/admin/release/prepare", response_model=dict)
def admin_release_prepare(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Dict[str, Any]:
    require_admin(current_user)
    if not bool(getattr(settings, "RELEASE_FREEZE_ENABLED", True)):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Release-Vorbereitung ist deaktiviert")
    action_acquired, action_lock_file = _release_admin_action_try_acquire()
    if not action_acquired:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Eine Release-Aktion läuft bereits")
    try:
        current = _release_admin_state()
        current_state = str(current.get("release_state") or "normal")
        if current_state == "finalizing":
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Release wird bereits durchgeführt")
        if current_state != "release_focus":
            preview = _build_release_preview(db)
            totals = dict(preview.get("totals") or {})
            if not bool(preview.get("ready", False)):
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Release-Preview enthält blockierende Artikel")
            if int(totals.get("selected_comments") or 0) <= 0:
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Keine releasefähigen Änderungen vorhanden")
            now = datetime.utcnow()
            with _ADMIN_SETTINGS_LOCK:
                _write_admin_settings({
                    "release_state": "release_focus",
                    "release_focus_started_at": now,
                    "release_focus_started_by_user_id": int(current_user.id),
                })
        else:
            preview = _build_release_preview(db)
        return {
            "ok": True,
            "workflow": _release_admin_state_payload(db),
            "preview": preview,
        }
    finally:
        _release_admin_action_release(action_lock_file)


@app.post("/api/admin/release/cancel", response_model=dict)
def admin_release_cancel(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Dict[str, Any]:
    require_admin(current_user)
    action_acquired, action_lock_file = _release_admin_action_try_acquire()
    if not action_acquired:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Eine Release-Aktion läuft bereits")
    try:
        # A genuinely running release already holds the cross-process action lock.
        # Therefore a visible `finalizing` state here is stale state left after a process interruption
        # and may be reset safely by this recovery path.
        with _ADMIN_SETTINGS_LOCK:
            _write_admin_settings({
                "release_state": "normal",
                "release_focus_started_at": None,
                "release_focus_started_by_user_id": None,
            })
        return {"ok": True, "workflow": _release_admin_state_payload(db)}
    finally:
        _release_admin_action_release(action_lock_file)


@app.post("/api/admin/release/execute", response_model=dict)
def admin_release_execute(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Dict[str, Any]:
    require_admin(current_user)
    action_acquired, action_lock_file = _release_admin_action_try_acquire()
    if not action_acquired:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Eine Release-Aktion läuft bereits")
    try:
        current = _release_admin_state()
        if str(current.get("release_state") or "normal") != "release_focus":
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Release muss zuerst vorbereitet werden")
        with _ADMIN_SETTINGS_LOCK:
            _write_admin_settings({"release_state": "finalizing"})
        try:
            result = _execute_release_cut(db, performed_by_user_id=int(current_user.id))
        except Exception as exc:
            with _ADMIN_SETTINGS_LOCK:
                _write_admin_settings({"release_state": "release_focus"})
            if isinstance(exc, HTTPException):
                raise
            if isinstance(exc, (RuntimeError, FileExistsError, ValueError)):
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
            raise
        with _ADMIN_SETTINGS_LOCK:
            _write_admin_settings({
                "release_state": "normal",
                "release_focus_started_at": None,
                "release_focus_started_by_user_id": None,
            })
        return {
            "ok": True,
            "release": result,
            "workflow": _release_admin_state_payload(db),
        }
    finally:
        _release_admin_action_release(action_lock_file)


@app.get("/api/admin/release/preview", response_model=dict)
def admin_release_preview(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Dict[str, Any]:
    """Return the current admin release preflight without database writes."""
    require_admin(current_user)
    out = _build_release_preview(db)
    out["workflow"] = _release_admin_state_payload(db)
    return out

# --- Static files (minimal, explicit serving surface) ----------------------
# Do not expose the complete project directory under `/static`.

def _safe_file_response(path: Path, *, media_type: str | None = None) -> FileResponse:
    """Return a repository-local file safely, rejecting traversal and missing paths with 404 instead of leaking filesystem errors."""
    try:
        resolved = path.resolve()
    except Exception:
        raise HTTPException(status_code=404, detail="Not found")

    base = BASE_DIR.resolve()
    if resolved != base and base not in resolved.parents:
        raise HTTPException(status_code=404, detail="Not found")

    if not resolved.is_file():
        raise HTTPException(status_code=404, detail="Not found")

    return FileResponse(path=resolved, media_type=media_type)

def _add_noindex_headers(resp: Response) -> Response:
    """Mark a response as non-indexable and non-cacheable where appropriate."""
    try:
        resp.headers.setdefault("X-Robots-Tag", "noindex, nofollow")
        resp.headers.setdefault("Cache-Control", "no-store")
    except Exception:
        pass
    return resp

@app.get("/static/style.css", include_in_schema=False)
def serve_style():
    return _safe_file_response(BASE_DIR / "style.css", media_type="text/css")

@app.get("/static/script.js", include_in_schema=False)
def serve_script():
    return _safe_file_response(BASE_DIR / "script.js", media_type="application/javascript")

    
@app.get("/static/auth_complete.js", include_in_schema=False)
def serve_auth_complete_js():
    return _safe_file_response(BASE_DIR / "auth_complete.js", media_type="application/javascript")
 
@app.get("/static/admin.js", include_in_schema=False)
def serve_admin_js():
    # Admin authentication uses only the normal magic-link/JWT flow.
    
    return _safe_file_response(BASE_DIR / "admin.js", media_type="application/javascript")





def _ensure_block_html(raw: str) -> str:
    """Normalize historical article block content for SSR: keep stored HTML, but escape and wrap plain-text legacy blocks so `|safe` cannot interpret them as markup."""
    s = (raw or "")
    if not s.strip():
        return ""
    if "<" not in s:
        return f"<p>{_html.escape(s)}</p>"
    return s

def _wrap_block(name: str, raw: str) -> str:
    """Wrap SSR block HTML with the DOM classes required by frontend toggles and inline-layer application."""
    inner = _ensure_block_html(raw or "")
    cls = f"content-block {name}"
    if str(name) == "meta":
        if not inner:
            return f'<section class="{cls} is-empty" data-block="{name}" hidden></section>'
        return f'<section class="{cls}" data-block="{name}" hidden>{inner}</section>'
    if not inner:
        return f'<section class="{cls} is-empty" data-block="{name}"></section>'
    return f'<section class="{cls}" data-block="{name}">{inner}</section>'


# NOTE:
# Semantic canonical MiniMD truth is the trusted foundation.
# The remaining live V3/merge HTML path below is temporary and scheduled for full replacement.
# Baseline site rendering should be kept separate until rebuild cutover.
def _merge_norm_block_key(v: Any) -> str:
    s = str(v or "").strip().lower()
    # Legacy persisted payloads may still use the plural block key `anmerkungen`.
    # The canonical persisted MiniMD block name remains `anmerkung`.
    if s in set(_minimd_block_order()):
        return s
    return ""


def _comment_status_key(v: Any) -> str:
    return str(v or "").strip().lower()


def _current_user_is_comment_owner_or_admin(comment: Comment, current_user: User | None) -> bool:
    if current_user is None:
        return False
    if bool(getattr(current_user, "is_admin", False)):
        return True
    return int(getattr(comment, "user_id", 0) or 0) == int(getattr(current_user, "id", 0) or 0)


def _comment_visible_for_merge_preview(comment: Comment, current_user: User | None) -> bool:
    """Enforce merge-preview visibility: public comments are open; private comments require owner/admin access, except currently reviewable comments visible to the authenticated reviewer."""
    st = _comment_status_key(getattr(comment, "status", ""))
    if st in {"veröffentlicht", "veroeffentlicht", "published"}:
        return True
    if _current_user_is_comment_owner_or_admin(comment, current_user):
        return True
    if current_user is None:
        return False
    try:
        db = object_session(comment)
        already_reviewed = False
        if db is not None:
            already_reviewed = (
                db.query(Review.id)
                .filter(Review.comment_id == comment.id, Review.reviewer_id == current_user.id)
                .first()
                is not None
            )
        return review_code.is_reviewable_for_user(
            comment,
            current_user,
            has_reviewed=already_reviewed,
            settings=settings,
        )
    except Exception:
        return False


@app.get("/", include_in_schema=False)
def serve_index(request: Request, db: Session = Depends(get_db)):
    ssr_articles = _get_public_articles_for_ssr(db)
    current_topics = _current_topics_state(db)
    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "ssr_articles": ssr_articles,
            "current_topics": current_topics,
        },
    )

@app.get("/entwurf", include_in_schema=False)
def serve_entwurf(
    request: Request,
    aid: int | None = Query(default=None),
    db: Session = Depends(get_db),
):
    ssr_articles = _get_public_articles_for_ssr(db)
    if isinstance(aid, int) and aid > 0:
        ssr_articles = [a for a in ssr_articles if int(a.get("id") or 0) == aid]
    return templates.TemplateResponse(
        "entwurf.html",
        {
            "request": request,
            "ssr_articles": ssr_articles,
        },
    )


@app.post("/api/merge-preview/multi", include_in_schema=False)
def api_merge_preview_multi(
    request: Request,
    body: Dict[str, Any],
    db: Session = Depends(get_db),
    current_user: User | None = Depends(get_current_user_optional),
) -> Dict[str, Any]:
    merge_preview_guard_acquired = False
    started_at = time.monotonic()
    try:
        aid = int(body.get("aid") or 0)
        cids_raw = body.get("cids_in_apply_order") or body.get("cids") or []
        if not isinstance(cids_raw, list):
            raise HTTPException(status_code=400, detail="cids must be a list")

        cids = _normalize_unique_positive_ints(cids_raw)

        if aid <= 0:
            raise HTTPException(status_code=400, detail="aid missing/invalid")

        if not cids:
            return {
                "ok": True,
                "aid": aid,
                "cids_in_apply_order": [],
                "merged_marked_html_full": "",
                "comment_cards_html": "",
                "comment_cards_table": [],
                "comment_card_rows_by_cid": {},
                "comment_cards_by_cid_html": {},
                "applied_parts": [],
                "counts": {"applied": 0, "unapplied": 0},
                "inline_engine_indiff": True,
                "indiff_block_results": {},
            }

        _merge_preview_raise_if_request_too_large(cids=list(cids))
        if not _merge_preview_try_acquire():
            raise _merge_preview_busy_exception()
        merge_preview_guard_acquired = True

        article_row = db.query(Article).filter(Article.id == aid).one_or_none()
        if article_row is None or article_row.current_version is None:
            raise HTTPException(status_code=404, detail="article_version_not_found")

        mm_full, _ = article_version_to_minimd(article_row.current_version)
        mm_blocks = _minimd_blocks_map(mm_full)

        rows = (
            db.query(Comment)
            .filter(Comment.article_id == aid, Comment.id.in_(cids))
            .all()
        )
        by_cid = {int(c.id): c for c in rows if c is not None}
        missing = [cid for cid in cids if cid not in by_cid]
        if missing:
            raise HTTPException(status_code=404, detail={"error": "comments_missing", "cids": missing})

        forbidden = [
            cid
            for cid in cids
            if cid in by_cid and not _comment_visible_for_merge_preview(by_cid[cid], current_user)
        ]
        if forbidden:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "error": "comments_not_visible_for_merge_preview",
                    "cids": forbidden,
                },
            )

        block_order = list(_MERGE_BLOCK_ORDER)
        # Comment-v2 metadata baseline:
        # `baseline_old_minimd_by_block` comes from `article_version_to_minimd()`
        # and includes the synthetic/persisted `meta` block. The HTML baseline must
        # use the same block source as SSR; otherwise `meta` is empty during composition
        # and `indiff.py` can render only the changed fragment.
        rendered_baseline_blocks = _site_wrapped_blocks_from_version(article_row.current_version)
        baseline_blocks: dict[str, str] = {
            bk: str(rendered_baseline_blocks.get(str(bk)) or _wrap_block(str(bk), ""))
            for bk in block_order
        }

        comments_in: list[code.PersistedCommentInput] = []
        for cid in cids:
            c = by_cid[int(cid)]
            payload = c.patch_payload if isinstance(getattr(c, "patch_payload", None), dict) else {}
            parts_raw = (payload or {}).get("parts") if isinstance(payload, dict) else None
            if not isinstance(parts_raw, list):
                parts_raw = []
            norm_parts: list[code.PersistedCommentPartInput] = []
            for idx, p in enumerate(parts_raw):
                if not isinstance(p, dict):
                    continue
                sel_start = p.get("sel_start")
                sel_end = p.get("sel_end")
                norm_parts.append(
                    code.PersistedCommentPartInput(
                        part_id=str(p.get("part_id") or f"p{idx+1}"),
                        block_key=str(_merge_norm_block_key(p.get("block_key") or p.get("block_id")) or ""),
                        old_text=str(p.get("old_text") or ""),
                        new_text=str(p.get("new_text") or ""),
                        sel_start=(int(sel_start) if sel_start is not None else None),
                        sel_end=(int(sel_end) if sel_end is not None else None),
                        baseline_hash=str(p.get("baseline_hash") or ""),
                    )
                )
            comments_in.append(code.PersistedCommentInput(cid=int(cid), parts=list(norm_parts)))

        _merge_preview_raise_if_loaded_payload_too_large(article_row, [by_cid[cid] for cid in cids])

        result = code.compose_article_merge_preview(
            block_order=list(block_order),
            baseline_old_minimd_by_block={bk: str(mm_blocks.get(bk) or "") for bk in block_order},
            baseline_html_by_block=dict(baseline_blocks),
            comments=list(comments_in),
            fallback_block_key="juristisch",
        )

        rows_by_cid, html_by_cid = _render_comment_cards_by_cid_from_table(
            list(result.comment_cards_table or []),
            expected_cids=list(cids),
        )

        if result.locate_failures:
            raise HTTPException(
                status_code=500,
                detail={
                    "error": "published_part_locate_fail",
                    "counts": dict(result.counts or {}),
                    "applied_parts": list(result.applied_parts or []),
                    "unapplied": list(result.locate_failures or []),
                },
            )

        _merge_preview_raise_if_time_budget_exceeded(started_at)

        return {
            "ok": True,
            "aid": aid,
            "cids_in_apply_order": cids,
            "merged_marked_html_full": str(result.merged_marked_html_full or ""),
            "comment_cards_html": str(result.comment_cards_html or ""),
            "comment_cards_table": list(result.comment_cards_table or []),
            "comment_card_rows_by_cid": dict(rows_by_cid or {}),
            "comment_cards_by_cid_html": dict(html_by_cid or {}),
            "applied_parts": list(result.applied_parts or []),
            "counts": dict(result.counts or {}),
            "inline_engine_indiff": True,
            "indiff_block_results": {
                str(bk): {
                    "block_key": str(bk),
                    "baseline_html": str(getattr(res, "baseline_html", "") or ""),
                    "composed_html": str(getattr(res, "composed_html", "") or ""),
                    "diagnostics": dict((res.diagnostics or {})),
                    "decoration_table": _build_indiff_decoration_table(res),
                    "interaction_table": list(res.interaction_table or []),
                    "span_registry": list(res.span_registry or []),
                    "style_table": list(res.style_table or []),
                    "normalized_changes": list(getattr(res, "normalized_changes", []) or []),
                    "operations": [
                        {
                            "op_id": str(op.op_id),
                            "block_key": str(op.block_key),
                            "op_type": str(op.op_type),
                            "anchor_source_start": int(op.anchor_source_start),
                            "anchor_source_end": int(op.anchor_source_end),
                            "payload_before": str(op.payload_before or ""),
                            "payload_inside_start": str(op.payload_inside_start or ""),
                            "payload_inside_end": str(op.payload_inside_end or ""),
                            "payload_after": str(op.payload_after or ""),
                            "meta": dict(op.meta or {}),
                        }
                        for op in list(res.operations or [])
                    ],
                    "delete_segments": list(res.delete_segments or []),
                    "insert_points": list(res.insert_points or []),
                    "comment_card_rows": list(res.comment_card_rows or []),
                    "anchor_audit_rows": list(res.anchor_audit_rows or []),
                }
                for bk, res in dict(result.block_results or {}).items()
            },
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"merge_preview_error: {e}")
    finally:
        if merge_preview_guard_acquired:
            _merge_preview_release()

def _site_wrapped_blocks_from_version(v: ArticleVersion | None) -> dict[str, str]:
    b = (v.content_blocks or {}) if (v is not None and isinstance(v.content_blocks, dict)) else {}
    meta_minimd = str(b.get("meta") or "")
    try:
        meta_html = code.minimd_to_html_text("meta", meta_minimd) if meta_minimd.strip() else ""
    except Exception:
        meta_html = html.escape(meta_minimd).replace("\n", "<br>") if meta_minimd.strip() else ""
    out = {
        "meta": _wrap_block("meta", meta_html),
        "kurzinfo": _wrap_block("kurzinfo", b.get("kurzinfo") or ""),
        "story": _wrap_block("story", b.get("story") or ""),
        "einleitung": _wrap_block("einleitung", b.get("einleitung") or ""),
        "juristisch": _wrap_block("juristisch", b.get("juristisch") or ""),
        "juristisch2": _wrap_block("juristisch2", b.get("juristisch2") or ""),
        "anmerkung": _wrap_block("anmerkung", b.get("anmerkung") or ""),
    }
    return out


# COMMENT_V2_META_RELEASE:
# `block_key == "meta"` is a normal persisted MiniMD/comment/diff block.
# During release integration, an accepted `meta` block is deliberately mirrored
# into `Article.public_code/title/toc_title/show_in_toc`; the stored version block
# is then updated for the new version rather than rebuilt during normal rendering.
# Do not regenerate it implicitly in the normal render path.


# ============================================================================
# SSR merge deliberately disabled.
# Server-rendered article blocks return baseline wrapped blocks only.
# Active inline-diff must come exclusively from /api/merge-preview/multi.
# ============================================================================
def _site_rendered_blocks_for_article(db: Session, a: Article) -> dict[str, str]:
    wrapped = _site_wrapped_blocks_from_version(getattr(a, "current_version", None))
    return dict(wrapped)


@app.get("/a/{slug}", include_in_schema=False)
def serve_article(
    slug: str,
    request: Request,
    merge_cids: str | None = Query(default=None),
    db: Session = Depends(get_db),
):
    slug = (slug or "").strip()
    if not slug:
        raise HTTPException(status_code=404, detail="Not found")
    a = (
        db.query(Article)
        .filter(Article.slug == slug)
        .first()
    )
    if not a or not a.current_version:
        raise HTTPException(status_code=404, detail="Not found")
    v = a.current_version
    if _article_public_visibility_status(db, a) == "hidden":
        raise HTTPException(status_code=404, detail="Not found")
    rendered_blocks = _site_rendered_blocks_for_article(db, a)

    ctx = {
        "request": request,
        "article": {
            "id": a.id,
            "current_version_id": v.id,
            "slug": a.slug,
            "public_code": a.public_code,
            "sort_order": a.sort_order,
            "toc_parent_id": a.toc_parent_id,
            "title": a.title,
            "type": a.type,
            "display_label": _article_display_label(a),
            "toc_group_label": _article_toc_group_label(a),
            "toc_title": a.toc_title,
            "updated_at": a.updated_at,
            **_article_public_visibility_dict(db, a),
            # SSR templates require `current_version_id` for their version data attributes.
            "current_version_id": int(v.id),
            "content_blocks": dict(rendered_blocks),
        },
    }
    return templates.TemplateResponse("artikel.html", ctx)

def _get_public_articles_for_ssr(db: Session) -> list[dict[str, Any]]:
    """Load the public article set for server-side rendering so search engines receive the law text in the initial HTML."""
    ttl = _public_articles_cache_seconds()
    if ttl > 0:
        now_mono = time.monotonic()
        with _PUBLIC_ARTICLES_CACHE_LOCK:
            cached = dict(_PUBLIC_ARTICLES_SSR_CACHE or {})
        if cached:
            age = now_mono - float(cached.get("stored_at") or 0.0)
            data = cached.get("data")
            if age >= 0 and age <= ttl and isinstance(data, list):
                return [dict(x) for x in data if isinstance(x, dict)]

    rows = (
        db.query(Article)
        .order_by(*_article_ordering())
        .all()
    )
    out: list[dict[str, Any]] = []
    for a in rows:
        v = a.current_version
        if not v or _article_public_visibility_status(db, a) == "hidden":
            continue
        if not isinstance(v.content_blocks, dict):
            continue
        rendered_blocks = _site_rendered_blocks_for_article(db, a)
        out.append(
            {
                "id": a.id,
                "current_version_id": v.id,
                "slug": a.slug,
                "public_code": a.public_code,
                "sort_order": a.sort_order,
                "toc_parent_id": a.toc_parent_id,
                "title": a.title,
                "type": a.type,
                "display_label": _article_display_label(a),
                "toc_group_label": _article_toc_group_label(a),
                "toc_title": a.toc_title,
                "show_in_toc": bool(getattr(a, "show_in_toc", True)),
                "updated_at": a.updated_at,
                **_article_public_visibility_dict(db, a),
                "current_version_id": int(v.id),
                "content_blocks": dict(rendered_blocks),
            }
        )
    if ttl > 0:
        try:
            with _PUBLIC_ARTICLES_CACHE_LOCK:
                _PUBLIC_ARTICLES_SSR_CACHE.clear()
                _PUBLIC_ARTICLES_SSR_CACHE.update({
                    "stored_at": time.monotonic(),
                    "data": [dict(x) for x in out],
                })
        except Exception:
            pass
    return out


def _public_base_url(request: Request) -> str:
    cfg = (settings.PUBLIC_BASE_URL or "").strip()
    if cfg:
        return cfg.rstrip("/")
    return f"{request.url.scheme}://{request.url.netloc}".rstrip("/")

#
# SEO / Indexing (robots.txt + sitemap.xml)
# ---------------------------------------------------------------------------
# `robots.txt` and `sitemap.xml` are intentionally generated at runtime;
# runtime output is the only source of truth.
#
# Rationale:
# The sitemap must include current database-backed `/a/<slug>` pages.
# The base URL must remain correct behind reverse proxies and multi-domain deployments.
# The canonical host comes from `PUBLIC_BASE_URL` or the current request.
#
# Do not maintain static `robots.txt`/`sitemap.xml` files as serving sources in the repository;
# they would drift from database/runtime state.
# Any examples should use an explicit template/example filename instead.
#
 
@app.get("/robots.txt", include_in_schema=False)
def serve_robots(request: Request) -> Response:
    base = _public_base_url(request)
    lines = [
        "User-agent: *",
        "Allow: /",
        "",
        "# Nicht indexieren: Admin/Auth/API",
        "Disallow: /admin",
        "Disallow: /api/",
        "Disallow: /auth/",
        "",
        f"Sitemap: {base}/sitemap.xml",
        "",
    ]
    resp = Response("\n".join(lines), media_type="text/plain; charset=utf-8")
    resp.headers["Cache-Control"] = "public, max-age=86400"
    return resp


@app.get("/sitemap.xml", include_in_schema=False)
def serve_sitemap(request: Request, db: Session = Depends(get_db)) -> Response:
    # Dynamic sitemap: fixed public pages plus all currently public database articles.
    # Do not serve a static filesystem `sitemap.xml`.
    base = _public_base_url(request)

    urls: list[dict[str, str]] = [
        {"loc": f"{base}/", "changefreq": "weekly", "priority": "1.0"},
        {"loc": f"{base}/entwurf", "changefreq": "daily", "priority": "0.9"},
        {"loc": f"{base}/mitmachen", "changefreq": "monthly", "priority": "0.7"},
        {"loc": f"{base}/methodik", "changefreq": "monthly", "priority": "0.6"},
        {"loc": f"{base}/kontakt", "changefreq": "monthly", "priority": "0.5"},
        {"loc": f"{base}/rechtliches", "changefreq": "monthly", "priority": "0.3"},
        {"loc": f"{base}/versionen", "changefreq": "weekly", "priority": "0.4"},
    ]

    for a in _get_public_articles_for_ssr(db):
        if not bool(a.get("show_in_toc", True)):
            continue
        lastmod = ""
        try:
            raw_updated = a.get("updated_at")
            if hasattr(raw_updated, "date"):
                lastmod = raw_updated.date().isoformat()
            elif raw_updated:
                lastmod = str(raw_updated)[:10]
        except Exception:
            lastmod = ""

        entry: dict[str, str] = {
            "loc": f"{base}/a/{a.get('slug')}",
            "changefreq": "weekly",
            "priority": "0.6",
        }
        if lastmod:
            entry["lastmod"] = lastmod
        urls.append(entry)

    def _url_xml(u: dict[str, str]) -> str:
        parts = [f"<loc>{_xml_escape(u['loc'])}</loc>"]
        if u.get("lastmod"):
            parts.append(f"<lastmod>{_xml_escape(u['lastmod'])}</lastmod>")
        if u.get("changefreq"):
            parts.append(f"<changefreq>{_xml_escape(u['changefreq'])}</changefreq>")
        if u.get("priority"):
            parts.append(f"<priority>{_xml_escape(u['priority'])}</priority>")
        return "<url>" + "".join(parts) + "</url>"

    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        + "".join(_url_xml(u) for u in urls)
        + "</urlset>"
    )
    resp = Response(xml, media_type="application/xml; charset=utf-8")
    resp.headers["Cache-Control"] = "public, max-age=600"
    return resp

@app.get("/admin", include_in_schema=False)
def serve_admin(request: Request):
    resp = templates.TemplateResponse("admin.html", {"request": request})
    return _add_noindex_headers(resp)

@app.get("/kontakt", include_in_schema=False)
def serve_kontakt(request: Request):
    return templates.TemplateResponse("kontakt.html", {"request": request})


_CONTACT_INTEREST_RATE_LIMIT: Dict[str, List[float]] = {}


def _contact_interest_form_redirect(status_code: str) -> RedirectResponse:
    safe_status = re.sub(r"[^a-z0-9_-]", "", str(status_code or "")) or "error"
    return RedirectResponse(url=f"/kontakt?status={safe_status}#kontakt-formular", status_code=303)


def _contact_interest_rate_limit_ok(request: Request) -> bool:
    client_ip = _metrics_client_ip(request)
    now = time.time()
    window_seconds = 60 * 60
    bucket = [t for t in _CONTACT_INTEREST_RATE_LIMIT.get(client_ip, []) if now - t < window_seconds]
    if len(bucket) >= 5:
        _CONTACT_INTEREST_RATE_LIMIT[client_ip] = bucket
        return False
    bucket.append(now)
    _CONTACT_INTEREST_RATE_LIMIT[client_ip] = bucket
    return True


@app.post("/api/contact/interest", response_model=ContactInterestOut)
async def api_contact_interest(request: Request):
    wants_html = "text/html" in (request.headers.get("accept") or "").lower()
    try:
        content_type = (request.headers.get("content-type") or "").lower()
        if "application/json" in content_type:
            raw = await request.json()
        else:
            body = await request.body()
            parsed = parse_qs(body.decode("utf-8", "replace"), keep_blank_values=True)
            raw = {k: (v[0] if v else "") for k, v in parsed.items()}
        payload = ContactInterestRequest(**(raw or {}))
    except Exception:
        if wants_html:
            return _contact_interest_form_redirect("error")
        raise HTTPException(status_code=400, detail="Ungültige Kontaktanfrage")

    # Honeypot submissions receive a normal success response to avoid teaching bots the filter.
    if (payload.website or "").strip():
        if wants_html:
            return _contact_interest_form_redirect("sent")
        return ContactInterestOut(ok=True, message="Danke, die Anfrage wurde angenommen.")

    if not _contact_interest_rate_limit_ok(request):
        if wants_html:
            return _contact_interest_form_redirect("rate_limited")
        raise HTTPException(status_code=429, detail="Zu viele Kontaktanfragen in kurzer Zeit")

    try:
        send_interest_form_mail(
            name=payload.name,
            user_email=str(payload.email),
            organization=payload.organization or "",
            role=payload.role or "",
            interest_type=payload.interest_type,
            message=payload.message,
        )
    except Exception:
        if wants_html:
            return _contact_interest_form_redirect("mail_error")
        raise HTTPException(status_code=500, detail="Kontaktanfrage konnte nicht versendet werden")

    if wants_html:
        return _contact_interest_form_redirect("sent")
    return ContactInterestOut(ok=True, message="Danke, die Anfrage wurde versendet.")

@app.get("/rechtliches", include_in_schema=False)
def serve_legal(request: Request):
    return templates.TemplateResponse("rechtliches.html", {"request": request})

@app.get("/mitmachen", include_in_schema=False)
def serve_mitmachen(request: Request):
    return templates.TemplateResponse("mitmachen.html", {"request": request})



@app.get("/methodik", include_in_schema=False)
def serve_methodik(request: Request):
    return templates.TemplateResponse("methodik.html", {"request": request})
























@app.get("/versions/release/{filename}", include_in_schema=False)
def serve_flat_release_file(filename: str):
    """Serve one canonical public release HTML snapshot."""
    filename = (filename or "").strip()
    if not filename or not _RELEASE_HTML_FILE_RE.match(filename):
        raise HTTPException(status_code=404, detail="Not found")

    base = (BASE_DIR / "versions" / "release").resolve()
    full = (base / filename).resolve()
    if full.parent != base or not full.is_file():
        raise HTTPException(status_code=404, detail="Not found")
    return FileResponse(path=full, filename=full.name, media_type="text/html")


# ---------------------------------------------------------------------------
# LLM context downloads (public anonymized phase-1 context files)
# ---------------------------------------------------------------------------

def _llm_file_response(path: Path) -> FileResponse:
    full = Path(path).resolve()
    if not full.is_file():
        raise HTTPException(status_code=404, detail="LLM-Kontextdatei nicht gefunden")
    llm_base = (BASE_DIR / str(getattr(settings, "LLM_API_DIRNAME", "llm-api") or "llm-api")).resolve()
    if llm_base != full and llm_base not in full.parents:
        raise HTTPException(status_code=403, detail="forbidden")
    suffix = full.suffix.lower()
    if suffix == ".html":
        media_type = "text/html; charset=utf-8"
    elif suffix == ".md":
        media_type = "text/markdown; charset=utf-8"
    else:
        raise HTTPException(status_code=403, detail="forbidden")
    return FileResponse(path=full, filename=full.name, media_type=media_type)


@app.get("/api/llm/context/status")
def llm_context_status():
    return export_snapshot.llm_context_status()


@app.get("/api/llm/context/law")
def llm_context_law():
    try:
        path = export_snapshot.ensure_llm_law_context_current()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"LLM-Gesetzeskontext konnte nicht erzeugt werden: {exc}")
    return _llm_file_response(Path(path))


@app.get("/api/llm/context/article/{article_id}")
def llm_context_article(article_id: int):
    try:
        path = export_snapshot.ensure_llm_article_context_current(int(article_id))
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"LLM-Artikelkontext konnte nicht erzeugt werden: {exc}")
    return _llm_file_response(Path(path))


@app.get("/api/llm/context/instructions")
def llm_context_instructions(mode: str = Query(default="change")):
    try:
        path = export_snapshot.ensure_llm_instructions_current(str(mode or "change"))
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Für diesen Kommentar-Modus sind keine LLM-Anweisungen hinterlegt.")
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"LLM-Anweisungen konnten nicht bereitgestellt werden: {exc}")
    return _llm_file_response(Path(path))

@app.get("/versionen", include_in_schema=False)
def serve_versionen(request: Request):
    return templates.TemplateResponse(
        "versionen.html",
        {"request": request, "release_items": _list_public_release_items()},
    )



