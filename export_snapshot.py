"""KlimaGG-Web — public snapshot / transparency / LLM exporter.
Version: v2.0.0


Design contract
---------------
- Every artifact produced here is public and free of account/person identifiers.
- The database is the only place where personal data may exist.
- Release/monthly snapshots use one human-readable HTML document as archive truth.
- Transparency is part of that HTML document, not a parallel JSON report.
- No database backup, no "full" export, no separate release-comment export.
- Export operations are read-only with respect to the database.
- MiniMD conversion is delegated to indiff.py, the canonical format bridge.
- LLM API contexts are Markdown context documents containing canonical MiniMD directly, without HTML or fenced code blocks.
"""

from __future__ import annotations

__version__ = "2.0.0"

import argparse
import hashlib
import html
import json
import os
import re
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import date, datetime, time as dt_time, timedelta, timezone
from pathlib import Path
from html.parser import HTMLParser
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

from sqlalchemy import func

from config import settings
from db import get_session
from models import (
    Article,
    ArticleReaction,
    ArticleVersion,
    ArticleVote,
    Comment,
    CommentReaction,
    CommentVote,
    MetricsDaily,
    Review,
    User,
)
import indiff as code
import review as review_code


PUBLIC_MAIN_VOTES = ("✅", "🟢", "🟡", "🟠", "🔴")

BASE_DIR = Path(__file__).resolve().parent

LLM_API_DIRNAME = str(getattr(settings, "LLM_API_DIRNAME", "llm-api") or "llm-api")
LLM_FILENAME_PREFIX = str(getattr(settings, "LLM_FILENAME_PREFIX", "Project") or "Project")
LLM_LAW_CONTEXT_LABEL = str(getattr(settings, "LLM_LAW_CONTEXT_LABEL", "Gesetzeskontext") or "Gesetzeskontext")
LLM_ARTICLE_CONTEXT_LABEL = str(getattr(settings, "LLM_ARTICLE_CONTEXT_LABEL", "Artikelkontext") or "Artikelkontext")
LLM_LAW_CONTEXT_MAX_AGE_HOURS = int(getattr(settings, "LLM_LAW_CONTEXT_MAX_AGE_HOURS", 24) or 24)
LLM_ARTICLE_CONTEXT_MAX_AGE_HOURS = int(getattr(settings, "LLM_ARTICLE_CONTEXT_MAX_AGE_HOURS", 1) or 1)


# ---------------------------------------------------------------------------
# Naming / time / generic helpers
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ExportNaming:
    mode: str
    archive_subdir: str
    repo_tag: str
    versions_dir: Path
    html_name: str


@dataclass(frozen=True)
class Period:
    start: datetime
    end: datetime
    source: str


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _aware_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _db_naive(value: datetime) -> datetime:
    return _aware_utc(value).replace(tzinfo=None)  # type: ignore[union-attr]


def _safe_iso(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, datetime):
        dt = _aware_utc(value)
        assert dt is not None
        return dt.replace(microsecond=0).isoformat().replace("+00:00", "Z")
    return str(value)


def _parse_iso_datetime(value: str | None) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        return _aware_utc(datetime.fromisoformat(raw))
    except Exception:
        return None


def _day_file_tag(now: datetime) -> str:
    return _aware_utc(now).strftime("%y-%m-%d")  # type: ignore[union-attr]


def _day_folder_tag(now: datetime) -> str:
    return _aware_utc(now).strftime("%y.%m.%d")  # type: ignore[union-attr]


def _date_only(now: datetime) -> str:
    return _aware_utc(now).strftime("%Y-%m-%d")  # type: ignore[union-attr]


def _stable_json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _content_hash(data: Any) -> str:
    return hashlib.sha256(_stable_json(data).encode("utf-8")).hexdigest()


def _git_ref(base_dir: Path) -> str:
    def run(args: list[str]) -> str | None:
        try:
            proc = subprocess.run(
                ["git", *args],
                cwd=str(base_dir),
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            out = (proc.stdout or "").strip()
            return out if proc.returncode == 0 and out else None
        except Exception:
            return None

    return (
        run(["describe", "--tags", "--exact-match"])
        or run(["describe", "--tags", "--always", "--dirty"])
        or str(getattr(settings, "APP_VERSION", "unknown") or "unknown")
    )


def _settings_str_list(name: str, default: Sequence[str]) -> list[str]:
    raw = getattr(settings, name, list(default))
    if raw is None:
        return list(default)
    if isinstance(raw, str):
        out = [p.strip() for p in raw.split(",") if p.strip()]
        return out or list(default)
    if isinstance(raw, (list, tuple, set)):
        out = [str(p).strip() for p in raw if str(p).strip()]
        return out or list(default)
    return list(default)


def _public_comment_statuses() -> list[str]:
    return _settings_str_list("COMMENT_PUBLIC_STATUSES", ["veröffentlicht"])


def _is_published_version(status: Any, published_at: Any, version_label: Any) -> bool:
    st = str(status or "").strip().lower()
    if st in {"published", "veröffentlicht"}:
        return True
    if published_at is not None:
        return True
    try:
        rx = str(getattr(settings, "PUBLISHED_VERSION_LABEL_REGEX", r"^v\d{4}-\d{2}-\d{2}$") or "")
        return bool(rx and re.match(rx, str(version_label or "")))
    except Exception:
        return False


def _snapshot_mode_spec(mode: str) -> tuple[str, str, str]:
    """Return archive subdir, filename prefix and configured file tag for one snapshot stream."""
    if mode == "release":
        return (
            "release",
            "v",
            str(getattr(settings, "EXPORT_RELEASE_FILE_TAG", "document") or "document").strip(),
        )
    if mode == "monthly":
        return (
            "backup",
            "b",
            str(getattr(settings, "EXPORT_MONTHLY_FILE_TAG", "web") or "web").strip(),
        )
    raise ValueError(f"Unsupported snapshot mode: {mode}")


def _build_export_naming(base_dir: Path, mode: str, now: datetime) -> ExportNaming:
    # Archive directory and filename conventions are part of the current public snapshot contract.
    subdir, prefix_letter, repo_tag = _snapshot_mode_spec(mode)
    folder = prefix_letter + _day_folder_tag(now)
    file_prefix = prefix_letter + _day_file_tag(now)
    versions_dir = base_dir / "versions" / subdir / folder
    return ExportNaming(
        mode=mode,
        archive_subdir=subdir,
        repo_tag=repo_tag,
        versions_dir=versions_dir,
        html_name=f"{file_prefix}_{repo_tag}_entwurf.html",
    )


def _atomic_write_text(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        if tmp.stat().st_size <= 0:
            raise RuntimeError(f"Exportdatei ist leer: {tmp}")
        os.replace(tmp, path)
    except Exception:
        try:
            tmp.unlink(missing_ok=True)
        except Exception:
            pass
        raise
    return path


_DANGEROUS_HTML_TAGS = {
    "script", "iframe", "object", "embed", "form", "input", "button",
    "textarea", "select", "option", "svg", "math", "meta", "link", "style", "base",
    "img", "picture", "source", "video", "audio", "track",
}
_DANGEROUS_URL_SCHEMES = {"javascript", "vbscript", "data", "file"}


class _InertHtmlValidator(HTMLParser):
    def __init__(self, *, context: str) -> None:
        super().__init__(convert_charrefs=False)
        self.context = str(context or "HTML")
        self.errors: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._check(tag, attrs)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._check(tag, attrs)

    def _check(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag_l = str(tag or "").lower()
        if tag_l in _DANGEROUS_HTML_TAGS:
            self.errors.append(f"gefährliches HTML-Tag <{tag_l}>")
        for raw_name, raw_value in list(attrs or []):
            name = str(raw_name or "").lower()
            value = str(raw_value or "").strip()
            if name.startswith("on") or name in {"srcdoc", "formaction"}:
                self.errors.append(f"gefährliches HTML-Attribut {name}")
                continue
            if name == "style" and re.search(r"(?:url\s*\(|@import|expression\s*\()", value, flags=re.I):
                self.errors.append("ressourcenladendes/aktives style-Attribut")
                continue
            if name in {"href", "src", "action"} and value:
                m = re.match(r"^([A-Za-z][A-Za-z0-9+.-]*):", value)
                if m and str(m.group(1)).lower() in _DANGEROUS_URL_SCHEMES:
                    self.errors.append(f"gefährliches URL-Schema in {name}")


def _assert_inert_html_fragment(fragment: str, *, context: str) -> None:
    parser = _InertHtmlValidator(context=context)
    try:
        parser.feed(str(fragment or ""))
        parser.close()
    except Exception as exc:
        raise RuntimeError(f"Unsicheres/ungültiges HTML in {context}: {exc}") from exc
    if parser.errors:
        raise RuntimeError(f"Unsicheres HTML in {context}: " + "; ".join(parser.errors))


def _slugish(text: Any) -> str:
    raw = str(text or "").strip()
    raw = raw.translate(str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "Ä": "Ae", "Ö": "Oe", "Ü": "Ue", "ß": "ss"}))
    raw = re.sub(r"[^A-Za-z0-9\- ]+", "", raw)
    raw = re.sub(r"\s+", "-", raw).strip("-")
    raw = re.sub(r"-+", "-", raw)
    return raw[:96] or "Artikel"


def _article_number_token(public_code: Any, title: Any) -> str:
    for source in (str(public_code or ""), str(title or "")):
        m = re.search(r"\b(Artikel\s*[0-9]+[A-Za-z]?|VO\s*[0-9]+[A-Za-z]?)\b", source, flags=re.I)
        if m:
            token = re.sub(r"\s+", "", m.group(1))
            if token.lower().startswith("artikel"):
                return "Artikel" + token[len("Artikel"):]
            if token.lower().startswith("vo"):
                return "VO" + token[len("VO"):]
    return _slugish(public_code or title or "Artikel")


# ---------------------------------------------------------------------------
# Public-state collection — no personal rows/fields
# ---------------------------------------------------------------------------


def _empty_vote_map() -> dict[str, int]:
    return {emoji: 0 for emoji in PUBLIC_MAIN_VOTES}


def _public_comment_export_id(row: Mapping[str, Any], *, article_code: str, version_label: str) -> str:
    # Only public data goes into this stable identifier; no DB/user id is encoded.
    material = {
        "article": article_code,
        "version": version_label,
        "published_at": _safe_iso(row.get("published_at")),
        "mode": str(row.get("comment_mode") or "change"),
        "type": str(row.get("type") or "standard"),
        "proposal_text": str(row.get("proposal_text") or ""),
        "explanation": str(row.get("explanation") or ""),
        "sources": str(row.get("sources") or ""),
        "anchor": row.get("anchor"),
        "anchor_payload": row.get("anchor_payload"),
        "patch_payload": row.get("patch_payload"),
        "structure_payload": row.get("structure_payload"),
    }
    return "comment-" + hashlib.sha256(_stable_json(material).encode("utf-8")).hexdigest()[:20]


def _article_rows(db) -> list[dict[str, Any]]:
    rows = (
        db.query(
            Article.id.label("article_id"),
            Article.slug.label("slug"),
            Article.public_code.label("public_code"),
            Article.sort_order.label("sort_order"),
            Article.title.label("title"),
            Article.type.label("type"),
            Article.show_in_toc.label("show_in_toc"),
            Article.toc_title.label("toc_title"),
            Article.toc_parent_id.label("toc_parent_id"),
            Article.current_version_id.label("version_id"),
            ArticleVersion.version_label.label("version_label"),
            ArticleVersion.content_blocks.label("content_blocks"),
            ArticleVersion.status.label("version_status"),
            ArticleVersion.published_at.label("version_published_at"),
        )
        .outerjoin(ArticleVersion, ArticleVersion.id == Article.current_version_id)
        .order_by(Article.sort_order.asc(), Article.id.asc())
        .all()
    )
    return [dict(r._mapping) for r in rows]


def _public_new_article_pairs(db) -> set[tuple[int, int]]:
    rows = (
        db.query(Comment.article_id, Comment.version_id)
        .filter(Comment.comment_mode == "new_article")
        .filter(Comment.status.in_(_public_comment_statuses()))
        .distinct()
        .all()
    )
    return {(int(a), int(v)) for a, v in rows if a is not None and v is not None}


def _collect_public_articles_base(db) -> tuple[list[dict[str, Any]], dict[int, str]]:
    raw = _article_rows(db)
    new_article_pairs = _public_new_article_pairs(db)
    code_by_id = {int(r["article_id"]): str(r.get("public_code") or "") for r in raw if r.get("article_id") is not None}
    out: list[dict[str, Any]] = []
    for row in raw:
        aid = row.get("article_id")
        vid = row.get("version_id")
        if aid is None or vid is None:
            continue
        published = _is_published_version(row.get("version_status"), row.get("version_published_at"), row.get("version_label"))
        proposed = (int(aid), int(vid)) in new_article_pairs
        visibility = "published" if published else ("proposed_by_comment" if proposed else "hidden")
        if visibility == "hidden":
            continue
        raw_blocks = row.get("content_blocks")
        if not isinstance(raw_blocks, dict):
            raise ValueError(
                f"Public article {row.get('public_code') or aid!r} version "
                f"{row.get('version_label') or vid!r} has invalid content_blocks; expected an object"
            )
        blocks = dict(raw_blocks)
        article_minimd, block_order = code.content_blocks_to_minimd(blocks)
        parent_code = code_by_id.get(int(row["toc_parent_id"])) if row.get("toc_parent_id") is not None else None
        out.append(
            {
                "public_code": str(row.get("public_code") or ""),
                "slug": str(row.get("slug") or ""),
                "sort_order": int(row.get("sort_order") or 0),
                "title": str(row.get("title") or ""),
                "type": str(row.get("type") or ""),
                "show_in_toc": bool(row.get("show_in_toc")),
                "toc_title": row.get("toc_title"),
                "toc_parent_public_code": parent_code,
                "_visibility_status": visibility,
                "version_label": str(row.get("version_label") or ""),
                "version_status": str(row.get("version_status") or ""),
                "published_at": _safe_iso(row.get("version_published_at")),
                "block_order": list(block_order),
                "content_blocks": dict(blocks),
                "article_minimd": article_minimd,
                # Internal-only runtime values are removed before privacy validation/render.
                "_article_id": int(aid),
                "_version_id": int(vid),
            }
        )
    return out, code_by_id


def _aggregate_article_votes(db) -> dict[tuple[int, int], dict[str, Any]]:
    out: dict[tuple[int, int], dict[str, Any]] = {}
    for aid, vid, main, count in (
        db.query(ArticleVote.article_id, ArticleVote.version_id, ArticleVote.main_vote, func.count(ArticleVote.id))
        .filter(ArticleVote.status == "confirmed")
        .group_by(ArticleVote.article_id, ArticleVote.version_id, ArticleVote.main_vote)
        .all()
    ):
        key = (int(aid), int(vid))
        bucket = out.setdefault(key, {"votes": {"total": 0, "by_main_vote": _empty_vote_map()}, "reactions": {}})
        n = int(count or 0)
        bucket["votes"]["total"] += n
        bucket["votes"]["by_main_vote"][str(main)] = n
    for aid, vid, emoji, count in (
        db.query(ArticleReaction.article_id, ArticleReaction.version_id, ArticleReaction.reaction_emoji, func.count(ArticleReaction.id))
        .group_by(ArticleReaction.article_id, ArticleReaction.version_id, ArticleReaction.reaction_emoji)
        .all()
    ):
        key = (int(aid), int(vid))
        bucket = out.setdefault(key, {"votes": {"total": 0, "by_main_vote": _empty_vote_map()}, "reactions": {}})
        bucket["reactions"][str(emoji)] = int(count or 0)
    return out


def _public_comment_rows(db) -> list[dict[str, Any]]:
    rows = (
        db.query(
            Comment.id.label("comment_id"),
            Comment.article_id.label("article_id"),
            Comment.version_id.label("version_id"),
            Comment.type.label("type"),
            Comment.comment_mode.label("comment_mode"),
            Comment.structure_payload.label("structure_payload"),
            Comment.anchor.label("anchor"),
            Comment.anchor_payload.label("anchor_payload"),
            Comment.patch_payload.label("patch_payload"),
            Comment.patch_stats.label("patch_stats"),
            Comment.proposal_text.label("proposal_text"),
            Comment.explanation.label("explanation"),
            Comment.sources.label("sources"),
            Comment.status.label("status"),
            Comment.parent_comment_id.label("parent_comment_id"),
            Comment.published_at.label("published_at"),
        )
        .filter(Comment.status.in_(_public_comment_statuses()))
        .order_by(Comment.published_at.asc(), Comment.id.asc())
        .all()
    )
    return [dict(r._mapping) for r in rows]


def _aggregate_comment_votes(db) -> dict[int, dict[str, Any]]:
    out: dict[int, dict[str, Any]] = {}
    for cid, main, count in (
        db.query(CommentVote.comment_id, CommentVote.main_vote, func.count(CommentVote.id))
        .filter(CommentVote.status == "confirmed")
        .group_by(CommentVote.comment_id, CommentVote.main_vote)
        .all()
    ):
        key = int(cid)
        bucket = out.setdefault(key, {"votes": {"total": 0, "by_main_vote": _empty_vote_map()}, "reactions": {}})
        n = int(count or 0)
        bucket["votes"]["total"] += n
        bucket["votes"]["by_main_vote"][str(main)] = n
    for cid, emoji, count in (
        db.query(CommentReaction.comment_id, CommentReaction.reaction_emoji, func.count(CommentReaction.id))
        .group_by(CommentReaction.comment_id, CommentReaction.reaction_emoji)
        .all()
    ):
        key = int(cid)
        bucket = out.setdefault(key, {"votes": {"total": 0, "by_main_vote": _empty_vote_map()}, "reactions": {}})
        bucket["reactions"][str(emoji)] = int(count or 0)
    return out


def _attach_comments_and_aggregates(db, articles: list[dict[str, Any]]) -> None:
    article_vote_map = _aggregate_article_votes(db)
    comment_vote_map = _aggregate_comment_votes(db)
    public_comments = _public_comment_rows(db)
    article_by_pair = {(int(a["_article_id"]), int(a["_version_id"])): a for a in articles}
    export_id_by_internal: dict[int, str] = {}
    staged: list[tuple[dict[str, Any], dict[str, Any]]] = []

    for row in public_comments:
        pair = (int(row["article_id"]), int(row["version_id"]))
        article = article_by_pair.get(pair)
        if article is None:
            continue
        cid = int(row["comment_id"])
        export_id = _public_comment_export_id(row, article_code=str(article["public_code"]), version_label=str(article["version_label"]))
        export_id_by_internal[cid] = export_id
        staged.append((article, row | {"export_id": export_id}))

    for article in articles:
        pair = (int(article["_article_id"]), int(article["_version_id"]))
        article["vote_summary"] = article_vote_map.get(
            pair,
            {"votes": {"total": 0, "by_main_vote": _empty_vote_map()}, "reactions": {}},
        )
        article["comments"] = []

    for article, row in staged:
        cid = int(row["comment_id"])
        parent_internal = row.get("parent_comment_id")
        public_comment = {
            "export_id": str(row["export_id"]),
            "parent_export_id": export_id_by_internal.get(int(parent_internal)) if parent_internal is not None else None,
            "status": str(row.get("status") or "veröffentlicht"),
            "comment_mode": str(row.get("comment_mode") or "change"),
            "type": str(row.get("type") or "standard"),
            "published_at": _safe_iso(row.get("published_at")),
            "proposal_text": str(row.get("proposal_text") or ""),
            "explanation": str(row.get("explanation") or ""),
            "sources": str(row.get("sources") or ""),
            "patch_payload": row.get("patch_payload"),
            "vote_summary": comment_vote_map.get(
                cid,
                {"votes": {"total": 0, "by_main_vote": _empty_vote_map()}, "reactions": {}},
            ),
        }
        article["comments"].append(public_comment)


def _review_distribution(db, *, start: datetime | None = None, end: datetime | None = None) -> dict[str, Any]:
    q = db.query(Review.recommendation, func.count(Review.id)).group_by(Review.recommendation)
    distinct_q = db.query(func.count(func.distinct(Review.comment_id)))
    if start is not None:
        q = q.filter(Review.created_at >= _db_naive(start))
        distinct_q = distinct_q.filter(Review.created_at >= _db_naive(start))
    if end is not None:
        q = q.filter(Review.created_at < _db_naive(end))
        distinct_q = distinct_q.filter(Review.created_at < _db_naive(end))
    dist = {"annehmen": 0, "korrigieren": 0, "ablehnen": 0, "sonstige": 0}
    total = 0
    for raw, count in q.all():
        n = int(count or 0)
        total += n
        try:
            key = review_code.normalize_decision(raw)
        except Exception:
            key = "sonstige"
        dist[key] = int(dist.get(key, 0)) + n
    return {
        "total": int(total),
        "reviewed_comments": int(distinct_q.scalar() or 0),
        "by_decision": dist,
    }


def _vote_distribution(db, model, *, start: datetime | None = None, end: datetime | None = None) -> dict[str, Any]:
    q = db.query(model.main_vote, func.count(model.id)).filter(model.status == "confirmed")
    if start is not None:
        stamp = func.coalesce(model.confirmed_at, model.created_at)
        q = q.filter(stamp >= _db_naive(start))
    if end is not None:
        stamp = func.coalesce(model.confirmed_at, model.created_at)
        q = q.filter(stamp < _db_naive(end))
    q = q.group_by(model.main_vote)
    by_main = _empty_vote_map()
    total = 0
    for main, count in q.all():
        n = int(count or 0)
        total += n
        by_main[str(main)] = n
    return {"total": int(total), "by_main_vote": by_main}


def _reaction_distribution(db, model, *, start: datetime | None = None, end: datetime | None = None) -> dict[str, Any]:
    q = db.query(model.reaction_emoji, func.count(model.id))
    if start is not None:
        q = q.filter(model.created_at >= _db_naive(start))
    if end is not None:
        q = q.filter(model.created_at < _db_naive(end))
    q = q.group_by(model.reaction_emoji)
    out: dict[str, int] = {}
    total = 0
    for emoji, count in q.all():
        n = int(count or 0)
        total += n
        out[str(emoji)] = n
    return {"total": int(total), "by_emoji": dict(sorted(out.items()))}


def _traffic_stats(db, *, start: datetime | None = None, end: datetime | None = None) -> dict[str, Any]:
    q = db.query(MetricsDaily)
    start_day: date | None = None
    end_day_exclusive: date | None = None
    if start is not None:
        s = _aware_utc(start)
        assert s is not None
        # MetricsDaily only has UTC-day resolution. Avoid re-counting the previous
        # release boundary day when the boundary was not midnight.
        start_day = s.date() + (timedelta(days=1) if s.time() != dt_time(0, 0) else timedelta(0))
        q = q.filter(MetricsDaily.day >= start_day)
    if end is not None:
        e = _aware_utc(end)
        assert e is not None
        end_day_exclusive = e.date() + timedelta(days=1)
        q = q.filter(MetricsDaily.day < end_day_exclusive)
    rows = q.order_by(MetricsDaily.day.asc()).all()
    pageviews = sum(int(r.pageviews or 0) for r in rows)
    onsite = sum(int(r.onsite_seconds or 0) for r in rows)
    visitors = [int(r.visitors_est or 0) for r in rows]
    return {
        "recorded_days": len(rows),
        "pageviews": int(pageviews),
        "visitors_est_daily_average": round(sum(visitors) / len(visitors), 2) if visitors else 0.0,
        "visitors_est_daily_max": max(visitors) if visitors else 0,
        "onsite_seconds": int(onsite),
        "traffic_start_day": start_day.isoformat() if start_day else (rows[0].day.isoformat() if rows else None),
        "traffic_end_day": rows[-1].day.isoformat() if rows else None,
    }


def _collect_transparency(db, articles: list[dict[str, Any]], period: Period) -> dict[str, Any]:
    public_comment_count = sum(len(a.get("comments") or []) for a in articles)
    current_article_votes = sum(int(((a.get("vote_summary") or {}).get("votes") or {}).get("total") or 0) for a in articles)
    current_article_reactions = sum(sum(int(v) for v in (((a.get("vote_summary") or {}).get("reactions") or {}).values())) for a in articles)
    current_comment_votes = 0
    current_comment_reactions = 0
    for article in articles:
        for comment in article.get("comments") or []:
            summary = comment.get("vote_summary") or {}
            current_comment_votes += int((summary.get("votes") or {}).get("total") or 0)
            current_comment_reactions += sum(int(v) for v in (summary.get("reactions") or {}).values())

    current_state = {
        "public_articles": len(articles),
        "published_comments": int(public_comment_count),
        "article_votes_on_current_public_versions": int(current_article_votes),
        "comment_votes_on_current_public_comments": int(current_comment_votes),
        "article_reactions_on_current_public_versions": int(current_article_reactions),
        "comment_reactions_on_current_public_comments": int(current_comment_reactions),
    }

    active_users = int(db.query(func.count(User.id)).filter(User.is_deleted == False).scalar() or 0)  # noqa: E712
    total_comments_created = int(db.query(func.count(Comment.id)).scalar() or 0)
    total_versions = int(db.query(func.count(ArticleVersion.id)).scalar() or 0)
    project_totals = {
        "active_accounts": active_users,
        "article_versions_total": total_versions,
        "comments_created_total": total_comments_created,
        "article_votes_confirmed": _vote_distribution(db, ArticleVote),
        "comment_votes_confirmed": _vote_distribution(db, CommentVote),
        "article_reactions": _reaction_distribution(db, ArticleReaction),
        "comment_reactions": _reaction_distribution(db, CommentReaction),
        "reviews": _review_distribution(db),
        "traffic": _traffic_stats(db),
    }

    start_db = _db_naive(period.start)
    end_db = _db_naive(period.end)
    published_versions = int(
        db.query(func.count(ArticleVersion.id))
        .filter(ArticleVersion.published_at != None)  # noqa: E711
        .filter(ArticleVersion.published_at >= start_db, ArticleVersion.published_at < end_db)
        .scalar()
        or 0
    )
    published_comments = int(
        db.query(func.count(Comment.id))
        .filter(Comment.published_at != None)  # noqa: E711
        .filter(Comment.published_at >= start_db, Comment.published_at < end_db)
        .scalar()
        or 0
    )
    new_accounts = int(
        db.query(func.count(User.id))
        .filter(User.created_at >= start_db, User.created_at < end_db)
        .scalar()
        or 0
    )
    period_activity = {
        "published_article_versions": published_versions,
        "published_comments": published_comments,
        "new_accounts": new_accounts,
        "article_votes_confirmed": _vote_distribution(db, ArticleVote, start=period.start, end=period.end),
        "comment_votes_confirmed": _vote_distribution(db, CommentVote, start=period.start, end=period.end),
        "article_reactions": _reaction_distribution(db, ArticleReaction, start=period.start, end=period.end),
        "comment_reactions": _reaction_distribution(db, CommentReaction, start=period.start, end=period.end),
        "reviews": _review_distribution(db, start=period.start, end=period.end),
        "traffic": _traffic_stats(db, start=period.start, end=period.end),
    }
    return {
        "period": {
            "start": _safe_iso(period.start),
            "end": _safe_iso(period.end),
            "source": period.source,
        },
        "current_public_state": current_state,
        "project_totals": project_totals,
        "period_activity": period_activity,
    }


def _strip_internal_fields(articles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for article in articles:
        row = {k: v for k, v in article.items() if not str(k).startswith("_")}
        out.append(row)
    return out


FORBIDDEN_PUBLIC_KEYS = {
    "user_id",
    "reviewer_id",
    "created_by_user_id",
    "performed_by_user_id",
    "email",
    "pseudonym",
    "public_author_label",
    "trust_level",
    "reviewer_trust_at_time",
    "plz",
    "postal_code",
    "ip",
    "ip_address",
    "user_agent",
    "visitor_key",
    "token",
    "llm_context_hash",
    "candidate_reasons",
    "candidate_score",
}


def _assert_public_payload_safe(value: Any, path: str = "root") -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            key_s = str(key)
            if key_s.lower() in FORBIDDEN_PUBLIC_KEYS:
                raise RuntimeError(f"Datenschutz-Sicherheitsprüfung fehlgeschlagen: verbotener Schlüssel {path}.{key_s}")
            _assert_public_payload_safe(child, f"{path}.{key_s}")
    elif isinstance(value, (list, tuple)):
        for idx, child in enumerate(value):
            _assert_public_payload_safe(child, f"{path}[{idx}]")


def _collect_public_state(db, *, period: Period, now: datetime, include_comments: bool = True, web_version: str | None = None) -> dict[str, Any]:
    articles, _ = _collect_public_articles_base(db)
    if include_comments:
        _attach_comments_and_aggregates(db, articles)
    else:
        article_vote_map = _aggregate_article_votes(db)
        for article in articles:
            pair = (int(article["_article_id"]), int(article["_version_id"]))
            article["vote_summary"] = article_vote_map.get(
                pair,
                {"votes": {"total": 0, "by_main_vote": _empty_vote_map()}, "reactions": {}},
            )
            article["comments"] = []
    public_articles = _strip_internal_fields(articles)
    state = {
        "meta": {
            "web_version": str(web_version or _git_ref(BASE_DIR) or "unknown"),
            "generated_at": _safe_iso(now),
        },
        "articles": public_articles,
        "transparency": _collect_transparency(db, articles, period),
    }
    state["meta"]["article_count"] = len(public_articles)
    state["meta"]["comment_count"] = sum(len(a.get("comments") or []) for a in public_articles)
    state["meta"]["content_fingerprint"] = _content_hash({"articles": public_articles, "transparency": state["transparency"]})
    _assert_public_payload_safe(state)
    return state


# ---------------------------------------------------------------------------
# Transparency period resolution
# ---------------------------------------------------------------------------


_META_RE = re.compile(r'<meta\s+name="(?P<name>kgg-[^"]+)"\s+content="(?P<value>[^"]*)"\s*/?>', flags=re.I)


def _read_snapshot_meta(path: Path) -> dict[str, str]:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")[:120_000]
    except Exception:
        return {}
    return {m.group("name").lower(): html.unescape(m.group("value")) for m in _META_RE.finditer(text)}


def _previous_snapshot_end_from_files(base_dir: Path, naming: ExportNaming, now: datetime) -> datetime | None:
    """Return the newest successful snapshot time from the same archive stream."""
    root = base_dir / "versions" / naming.archive_subdir
    if not root.is_dir():
        return None
    latest: datetime | None = None
    pattern = f"*/*_{naming.repo_tag}_entwurf.html"
    for path in root.glob(pattern):
        if not path.is_file():
            continue
        meta = _read_snapshot_meta(path)
        dt = _parse_iso_datetime(meta.get("kgg-generated-at") or meta.get("kgg-period-end"))
        if dt is None or dt >= now:
            continue
        if latest is None or dt > latest:
            latest = dt
    return latest


def _snapshot_period(base_dir: Path, naming: ExportNaming, now: datetime) -> Period:
    """Use one period rule for both snapshot streams: previous same-type snapshot -> now."""
    previous = _previous_snapshot_end_from_files(base_dir, naming, now)
    if previous is not None:
        return Period(previous, now, f"previous_{naming.mode}_snapshot")
    n = _aware_utc(now)
    assert n is not None
    return Period(datetime(n.year, n.month, 1, tzinfo=timezone.utc), now, "current_month_default")


# ---------------------------------------------------------------------------
# HTML rendering
# ---------------------------------------------------------------------------


def _fmt_int(value: Any) -> str:
    try:
        return f"{int(value):,}".replace(",", ".")
    except Exception:
        return "0"


def _fmt_seconds(value: Any) -> str:
    try:
        sec = int(value or 0)
    except Exception:
        sec = 0
    if sec >= 3600:
        return f"{sec / 3600:.1f} h".replace(".", ",")
    if sec >= 60:
        return f"{sec / 60:.1f} min".replace(".", ",")
    return f"{sec} s"


def _chips(values: Mapping[str, Any]) -> str:
    return " ".join(f'<span class="chip">{html.escape(str(k))} {_fmt_int(v)}</span>' for k, v in values.items())


def _vote_summary_html(summary: Mapping[str, Any]) -> str:
    votes = summary.get("votes") or {}
    reactions = summary.get("reactions") or {}
    parts: list[str] = []
    by_main = votes.get("by_main_vote") or {}
    if int(votes.get("total") or 0) or any(int(v or 0) for v in by_main.values()):
        parts.append(f'<div><strong>Stimmen:</strong> {_chips(by_main)}</div>')
    if reactions:
        parts.append(f'<div><strong>Reaktionen:</strong> {_chips(reactions)}</div>')
    return '<div class="participation">' + "".join(parts) + "</div>" if parts else '<div class="participation muted">Keine Beteiligung.</div>'


def _render_content_blocks(article: Mapping[str, Any]) -> str:
    """Render stored public article HTML without redesigning its block structure."""
    blocks = article.get("content_blocks") or {}
    order = article.get("block_order") or list(blocks.keys())
    parts: list[str] = []
    for key in order:
        key = str(key)
        if key == "meta":
            continue
        raw = str(blocks.get(key) or "")
        if not raw.strip():
            continue
        _assert_inert_html_fragment(
            raw,
            context=f"Artikel {article.get('public_code') or ''} / Block {key}",
        )
        parts.append(
            f'<div class="block block-{html.escape(key)}" data-kgg-block="{html.escape(key, quote=True)}">\n'
            f'{raw}\n</div>'
        )
    return "\n".join(parts)


def _article_baseline_maps(article: Mapping[str, Any]) -> tuple[list[str], dict[str, str], dict[str, str]]:
    blocks = article.get("content_blocks") or {}
    order = [str(x) for x in (article.get("block_order") or list(blocks.keys()))]
    minimd_by_block: dict[str, str] = {}
    html_by_block: dict[str, str] = {}
    for key in order:
        raw = str(blocks.get(key) or "")
        if key == "meta":
            minimd_by_block[key] = raw
            html_by_block[key] = code.minimd_to_html_text("meta", raw) if raw else ""
        else:
            minimd_by_block[key] = code.html_to_minimd_text(key, raw) if raw else ""
            html_by_block[key] = raw
    return order, minimd_by_block, html_by_block


def _comment_patch_input(comment: Mapping[str, Any], *, cid: int = 1) -> code.PersistedCommentInput:
    payload = comment.get("patch_payload") if isinstance(comment.get("patch_payload"), Mapping) else {}
    raw_parts = payload.get("parts") if isinstance(payload, Mapping) else []
    parts: list[code.PersistedCommentPartInput] = []
    if isinstance(raw_parts, list):
        for idx, raw in enumerate(raw_parts):
            if not isinstance(raw, Mapping):
                continue
            sel_start = raw.get("sel_start")
            sel_end = raw.get("sel_end")
            try:
                sel_start_i = int(sel_start) if sel_start is not None else None
            except Exception:
                sel_start_i = None
            try:
                sel_end_i = int(sel_end) if sel_end is not None else None
            except Exception:
                sel_end_i = None
            parts.append(
                code.PersistedCommentPartInput(
                    part_id=str(raw.get("part_id") or f"p{idx + 1}"),
                    block_key=str(raw.get("block_key") or raw.get("block_id") or "juristisch"),
                    old_text=str(raw.get("old_text") or ""),
                    new_text=str(raw.get("new_text") or ""),
                    sel_start=sel_start_i,
                    sel_end=sel_end_i,
                    baseline_hash=str(raw.get("baseline_hash") or ""),
                )
            )
    return code.PersistedCommentInput(cid=int(cid), parts=parts)


def _comment_diff_card_html(article: Mapping[str, Any], comment: Mapping[str, Any]) -> str:
    comment_input = _comment_patch_input(comment, cid=1)
    if not list(comment_input.parts or []):
        raise ValueError(
            f"Public comment {comment.get('export_id') or ''!r} has no structured patch payload"
        )

    order, minimd_by_block, html_by_block = _article_baseline_maps(article)
    result = code.compose_article_merge_preview(
        block_order=list(order),
        baseline_old_minimd_by_block=dict(minimd_by_block),
        baseline_html_by_block=dict(html_by_block),
        comments=[comment_input],
        fallback_block_key="juristisch",
    )
    card_html = str(result.comment_cards_html or "").strip()
    if not card_html:
        raise RuntimeError(
            f"Canonical diff renderer returned no comment card for {comment.get('export_id') or ''!r}"
        )

    # cid=1 is a synthetic, non-DB renderer id. Public HTML exposes only the
    # deterministic public comment id.
    public_id = html.escape(str(comment.get("export_id") or ""), quote=True)
    card_html = re.sub(r'\sdata-kgg-cid="1"', f' data-kgg-comment-id="{public_id}"', card_html)
    _assert_inert_html_fragment(card_html, context=f"Kommentar {comment.get('export_id') or ''} / Diffkarte")
    return card_html


def _render_comment(article: Mapping[str, Any], comment: Mapping[str, Any]) -> str:
    explanation_raw = str(comment.get("explanation") or "").strip()
    explanation = code.minimd_to_html_text("_comment", explanation_raw) if explanation_raw else ""
    if explanation:
        _assert_inert_html_fragment(explanation, context=f"Kommentar {comment.get('export_id') or ''} / Begründung")
    sources = html.escape(str(comment.get("sources") or "")).replace("\n", "<br>")
    parent = comment.get("parent_export_id")
    parent_html = f'<div class="artikel-meta">Antwort auf: <code>{html.escape(str(parent))}</code></div>' if parent else ""
    return f'''<article class="archive-comment" data-kgg-comment-id="{html.escape(str(comment.get('export_id') or ''), quote=True)}">
<header class="comment-card-header"><div class="comment-id">{html.escape(str(comment.get('export_id') or 'Kommentar'))}</div>
<div><span class="chip">{html.escape(str(comment.get('comment_mode') or 'change'))}</span> <span class="chip">{html.escape(str(comment.get('type') or 'standard'))}</span></div></header>
<div class="artikel-meta">Veröffentlicht: {html.escape(str(comment.get('published_at') or '–'))}</div>
{parent_html}
<section class="comment-section"><h5>Änderung</h5>{_comment_diff_card_html(article, comment)}</section>
{f'<section class="comment-section"><h5>Begründung</h5><div>{explanation}</div></section>' if explanation else ''}
{f'<section class="comment-section"><h5>Quellen</h5><div>{sources}</div></section>' if sources else ''}
{_vote_summary_html(comment.get('vote_summary') or {})}
</article>'''


def _render_article(article: Mapping[str, Any], *, include_comments: bool) -> str:
    comments = list(article.get("comments") or [])
    comments_html = ""
    if include_comments:
        if comments:
            comments_html = (
                f'<details class="comments" data-kgg-comments-count="{len(comments)}">'
                f'<summary>Kommentare ({len(comments)})</summary>'
                + "".join(_render_comment(article, c) for c in comments)
                + "</details>"
            )
        else:
            comments_html = '<p class="comments-none muted">Keine Kommentare.</p>'
    parent = article.get("toc_parent_public_code")
    return f'''<article class="artikel artikel-{html.escape(str(article.get('type') or ''))}" data-kgg-public-code="{html.escape(str(article.get('public_code') or ''), quote=True)}" data-kgg-version="{html.escape(str(article.get('version_label') or ''), quote=True)}">
<header><h2>{html.escape(str(article.get('public_code') or ''))}{' – ' if article.get('public_code') else ''}{html.escape(str(article.get('title') or ''))}</h2>
<p class="artikel-meta">Version: {html.escape(str(article.get('version_label') or ''))} · Typ: {html.escape(str(article.get('type') or ''))}{f' · TOC-Gruppe: {html.escape(str(parent))}' if parent else ''}</p></header>
{_render_content_blocks(article)}
{_vote_summary_html(article.get('vote_summary') or {})}
{comments_html}
</article>'''


def _law_minimd_text(articles: Sequence[Mapping[str, Any]]) -> str:
    chunks = [str(a.get("article_minimd") or "").strip() for a in articles if str(a.get("article_minimd") or "").strip()]
    return "\n\n".join(chunks).strip() + ("\n" if chunks else "")


def _render_archive_minimd(articles: Sequence[Mapping[str, Any]]) -> str:
    minimd = html.escape(_law_minimd_text(articles))
    return f'''<details class="archive-minimd"><summary>Kanonischer Dokumenttext (MiniMD)</summary>
<p class="muted">Kanonische maschinenlesbare Textfassung des in diesem Snapshot dargestellten Dokuments. Kommentare und Beteiligungsdaten sind bewusst nicht Teil von MiniMD.</p>
<pre data-kgg-format="minimd" data-kgg-role="canonical-law-source"><code>{minimd}</code></pre></details>'''


def _summary_table(rows: Sequence[tuple[str, Any]]) -> str:
    return '<table class="stats-table"><tbody>' + "".join(
        f'<tr><th>{html.escape(label)}</th><td>{html.escape(str(value))}</td></tr>' for label, value in rows
    ) + "</tbody></table>"


def _vote_stat_block(label: str, data: Mapping[str, Any]) -> str:
    return f'<div class="stat-block"><strong>{html.escape(label)}:</strong> {_fmt_int(data.get("total"))}<div>{_chips(data.get("by_main_vote") or {})}</div></div>'


def _reaction_stat_block(label: str, data: Mapping[str, Any]) -> str:
    return f'<div class="stat-block"><strong>{html.escape(label)}:</strong> {_fmt_int(data.get("total"))}<div>{_chips(data.get("by_emoji") or {})}</div></div>'


def _review_stat_block(label: str, data: Mapping[str, Any]) -> str:
    dist = data.get("by_decision") or {}
    return f'<div class="stat-block"><strong>{html.escape(label)}:</strong> {_fmt_int(data.get("total"))} · {_fmt_int(data.get("reviewed_comments"))} Kommentare<div>{_chips(dist)}</div></div>'


def _traffic_block(label: str, data: Mapping[str, Any]) -> str:
    return _summary_table(
        [
            (label + " – erfasste Tage", _fmt_int(data.get("recorded_days"))),
            ("Pageviews", _fmt_int(data.get("pageviews"))),
            ("Ø Besucher-Schätzung/Tag", str(data.get("visitors_est_daily_average") or 0).replace(".", ",")),
            ("Max. Besucher-Schätzung/Tag", _fmt_int(data.get("visitors_est_daily_max"))),
            ("Onsite-Zeit", _fmt_seconds(data.get("onsite_seconds"))),
        ]
    )


def _render_transparency(transparency: Mapping[str, Any]) -> str:
    period = transparency.get("period") or {}
    current = transparency.get("current_public_state") or {}
    totals = transparency.get("project_totals") or {}
    activity = transparency.get("period_activity") or {}
    current_rows = [
        ("Öffentliche Artikel", _fmt_int(current.get("public_articles"))),
        ("Veröffentlichte Kommentare", _fmt_int(current.get("published_comments"))),
        ("Stimmen auf aktuelle Artikelfassungen", _fmt_int(current.get("article_votes_on_current_public_versions"))),
        ("Stimmen auf aktuelle öffentliche Kommentare", _fmt_int(current.get("comment_votes_on_current_public_comments"))),
        ("Artikel-Reaktionen im aktuellen Stand", _fmt_int(current.get("article_reactions_on_current_public_versions"))),
        ("Kommentar-Reaktionen im aktuellen Stand", _fmt_int(current.get("comment_reactions_on_current_public_comments"))),
    ]
    total_rows = [
        ("Aktive Accounts", _fmt_int(totals.get("active_accounts"))),
        ("Artikelversionen insgesamt", _fmt_int(totals.get("article_versions_total"))),
        ("Kommentare/Vorschläge erstellt", _fmt_int(totals.get("comments_created_total"))),
    ]
    activity_rows = [
        ("Veröffentlichte Artikelversionen", _fmt_int(activity.get("published_article_versions"))),
        ("Veröffentlichte Kommentare", _fmt_int(activity.get("published_comments"))),
        ("Neue Accounts", _fmt_int(activity.get("new_accounts"))),
    ]
    return f'''<section id="transparenz" class="transparency">
<h2>Transparenz</h2>
<p><strong>Berichtszeitraum:</strong> {html.escape(str(period.get('start') or '–'))} bis {html.escape(str(period.get('end') or '–'))} <span class="muted">({html.escape(str(period.get('source') or ''))})</span></p>
<h3>Öffentlicher Stand</h3>{_summary_table(current_rows)}
<h3>Projektweite aggregierte Statistik</h3>{_summary_table(total_rows)}
{_vote_stat_block('Bestätigte Artikelstimmen insgesamt', totals.get('article_votes_confirmed') or {})}
{_vote_stat_block('Bestätigte Kommentarstimmen insgesamt', totals.get('comment_votes_confirmed') or {})}
{_reaction_stat_block('Artikel-Reaktionen insgesamt', totals.get('article_reactions') or {})}
{_reaction_stat_block('Kommentar-Reaktionen insgesamt', totals.get('comment_reactions') or {})}
{_review_stat_block('Reviews insgesamt', totals.get('reviews') or {})}
{_traffic_block('Traffic gesamt', totals.get('traffic') or {})}
<h3>Aktivität im Berichtszeitraum</h3>{_summary_table(activity_rows)}
{_vote_stat_block('Bestätigte Artikelstimmen', activity.get('article_votes_confirmed') or {})}
{_vote_stat_block('Bestätigte Kommentarstimmen', activity.get('comment_votes_confirmed') or {})}
{_reaction_stat_block('Artikel-Reaktionen', activity.get('article_reactions') or {})}
{_reaction_stat_block('Kommentar-Reaktionen', activity.get('comment_reactions') or {})}
{_review_stat_block('Reviews', activity.get('reviews') or {})}
{_traffic_block('Traffic im Zeitraum', activity.get('traffic') or {})}
<p class="muted">Visitors sind tägliche Schätzwerte. Sie werden nicht als periodenweite Unique-Visitor-Zahl summiert. Reviews werden ausschließlich global aggregiert und keinem Kommentar oder Reviewer zugeordnet.</p>
</section>'''


_BASE_STYLE = r'''
body {
  font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
  margin: 2rem auto;
  max-width: 980px;
  padding: 0 1rem;
  line-height: 1.55;
  color: #1f2937;
  background: #ffffff;
}
.page { min-height: 100vh; }
header.page-header { margin-bottom:2rem; border-bottom:1px solid #d1d5db; padding-bottom:1rem; }
h1,h2,h3,h4,h5 { line-height: 1.25; }
.lead { color:#374151; }
.muted { color:#6b7280; }
.snapshot-meta { font-size:0.95rem; color:#374151; margin-bottom:1rem; }
.snapshot-meta span { display:inline-block; margin:0.12rem 0.75rem 0.12rem 0; }
.toc {
  margin: 1.75rem 0;
  padding: 1.25rem 1rem;
  border: 1px solid #e5e7eb;
  border-radius: 12px;
  background: #f8fbff;
}
.toc h2 { margin-top:0; }
.toc a { display:block; color:inherit; text-decoration:none; padding:0.12rem 0; }
article.artikel {
  margin: 1.75rem 0;
  padding: 1.25rem 1rem;
  border: 1px solid #e5e7eb;
  border-radius: 12px;
  background: #fafafa;
}
article.artikel header h2 { margin:0 0 0.35rem; font-size:1.28rem; }
.artikel-meta { margin:0.2rem 0; font-size:0.88rem; color:#6b7280; }
.block { margin-top:1rem; }
.block-einleitung {
  background:#eef6ff;
  padding:0.75rem;
  border-radius:8px;
}
.block-juristisch, .block-juristisch2 {
  background:#ffffff;
  padding:0.75rem;
  border-radius:8px;
  border-left:3px solid #005f99;
}
.block-anmerkung {
  background:#fff9e5;
  padding:0.75rem;
  border-radius:8px;
  border-left:3px solid #c27b00;
  font-size:0.92rem;
}
.block-kurzinfo, .block-story { padding:0.75rem; border-radius:8px; background:#f8fafc; }
.participation { margin-top:0.9rem; }
.participation > div { margin:0.25rem 0; font-size:0.92rem; }
.chip {
  display:inline-block;
  padding:0.15rem 0.5rem;
  margin:0.15rem 0.2rem 0.15rem 0;
  border-radius:999px;
  background:#eef2f7;
  font-size:0.86rem;
}
details.comments {
  margin-top:1rem;
  border-top:1px solid #dbe3ea;
  padding-top:0.7rem;
}
details.comments > summary {
  cursor:pointer;
  font-weight:700;
  color:#374151;
}
.comments-none { margin-top:1rem; }
.archive-comment {
  margin-top:1rem;
  padding:0.9rem;
  border:1px solid #dbe3ea;
  border-radius:10px;
  background:#ffffff;
}
.comment-card-header {
  display:flex;
  gap:0.75rem;
  align-items:center;
  justify-content:space-between;
  flex-wrap:wrap;
  margin-bottom:0.4rem;
}
.comment-id { font-weight:700; }
.comment-section { margin-top:0.75rem; }
.comment-section h5 { margin:0 0 0.35rem; font-size:0.95rem; }
.kgg-comment-card {
  margin:0.1rem 0 0;
  padding:0.5rem 0.6rem;
  border-radius:14px;
  border:1px solid rgba(209,213,219,0.82);
  background:rgba(255,255,255,0.86);
  max-height:calc(5 * 1.45em + 0.9rem);
  overflow-y:auto;
}
.kgg-comment-card-body { display:grid; gap:0.3rem; }
.kgg-comment-card-block { display:block; padding-top:0.15rem; }
.kgg-comment-card-block + .kgg-comment-card-block {
  border-top:1px dashed rgba(148,163,184,0.34);
  margin-top:0.3rem;
  padding-top:0.35rem;
}
.kgg-comment-card-block-badge {
  display:inline-flex;
  align-items:center;
  font-size:0.72rem;
  font-weight:800;
  line-height:1.1;
  padding:0.14rem 0.5rem;
  border-radius:999px;
  border:1px solid rgba(15,23,42,0.12);
  background:rgba(15,23,42,0.05);
  color:rgba(15,23,42,0.82);
}
.kgg-diff-row {
  display:inline-flex;
  align-items:center;
  flex-wrap:wrap;
  gap:0.45rem;
  line-height:1.28;
  min-width:0;
  margin:0.12rem 0.4rem 0.12rem 0;
  vertical-align:top;
}
.kgg-diff-row-body { flex:0 1 auto; min-width:0; word-break:break-word; }
.kgg-diff-part-badge {
  flex:0 0 auto;
  font-size:0.72rem;
  font-weight:800;
  padding:0.08rem 0.45rem;
  border-radius:999px;
  border:1px solid rgba(15,23,42,0.14);
  background:rgba(15,23,42,0.06);
  color:rgba(15,23,42,0.82);
  line-height:1.2;
}
.kgg-diff-pre,.kgg-diff-post { color:rgba(15,23,42,0.78); }
.kgg-comment-card .minimd-ins,
.kgg-comment-card ins,
.kgg-comment-card .kgg-inline-diff-ins {
  background:rgba(34,197,94,.22);
  border:1px solid rgba(34,197,94,.35);
  border-radius:8px;
  display:inline;
  padding:0;
  text-decoration:none;
}
.kgg-comment-card .minimd-del,
.kgg-comment-card del,
.kgg-comment-card .kgg-inline-diff-del,
.kgg-comment-card .minimd-warn {
  background:rgba(239,68,68,.12);
  border:1px solid rgba(239,68,68,.28);
  border-radius:8px;
  display:inline;
  padding:0;
  text-decoration:line-through;
}
.transparency {
  margin:2rem 0;
  padding:1rem;
  border:1px solid #e5e7eb;
  border-radius:12px;
  background:#fbfcfa;
}
.stats-table { border-collapse:collapse; width:100%; max-width:780px; margin:0.5rem 0 1rem; }
.stats-table th,.stats-table td { text-align:left; padding:0.35rem 0.55rem; border-bottom:1px solid #e5e7eb; }
.stats-table th { width:65%; font-weight:600; }
.stat-block { margin:0.55rem 0; }
.archive-minimd {
  margin:2rem 0;
  padding:0.8rem 0.9rem;
  border-radius:12px;
  border:1px dashed #cbd5e1;
  background:#f8fafc;
}
.archive-minimd summary { cursor:pointer; font-weight:700; }
pre {
  white-space:pre-wrap;
  overflow-wrap:anywhere;
  background:#ffffff;
  padding:0.75rem;
  border-radius:8px;
  border:1px solid #e2e8f0;
  font-size:0.84rem;
}
code { font-family:ui-monospace,SFMono-Regular,Menlo,monospace; }
.footer {
  margin-top:2.5rem;
  font-size:0.84rem;
  color:#6b7280;
  border-top:1px solid #e5e7eb;
  padding-top:1rem;
}
@media (max-width:760px) {
  body { margin:1rem auto; padding:0 0.7rem; }
  .comment-card-header { display:block; }
}
@media print {
  @page { size:A4; margin:16mm 14mm 18mm; }
  body { margin:0; padding:0; max-width:none; font-size:10.5pt; color:#111827; }
  .toc, article.artikel, .transparency { break-inside:auto; }
  details.comments > summary { display:none; }
  .kgg-comment-card { max-height:none; overflow:visible; }
  a { color:inherit; text-decoration:none; }
}
'''

def _html_page(*, title: str, intro: str, meta: Mapping[str, Any], body: str) -> str:
    generated = str(meta.get("generated_at") or "")
    period_end = str(meta.get("period_end") or generated)
    fingerprint = str(meta.get("content_fingerprint") or "")
    meta_tags = "\n".join(
        [
            f'<meta name="kgg-snapshot-type" content="{html.escape(str(meta.get("snapshot_type") or ""), quote=True)}">',
            f'<meta name="kgg-generated-at" content="{html.escape(generated, quote=True)}">',
            f'<meta name="kgg-period-start" content="{html.escape(str(meta.get("period_start") or ""), quote=True)}">',
            f'<meta name="kgg-period-end" content="{html.escape(period_end, quote=True)}">',
            f'<meta name="kgg-web-version" content="{html.escape(str(meta.get("web_version") or "unknown"), quote=True)}">',
            f'<meta name="kgg-content-fingerprint" content="{html.escape(fingerprint, quote=True)}">',
        ]
    )
    visible_meta = "".join(
        f'<span>{html.escape(str(label))}: {html.escape(str(value))}</span>'
        for label, value in [
            ("Stand", generated),
            ("Web-Version", meta.get("web_version") or "unknown"),
        ]
    )
    return f'''<!doctype html>
<html lang="de"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; img-src data:; font-src data:; base-uri 'none'; form-action 'none'; frame-ancestors 'none'">{meta_tags}<title>{html.escape(title)}</title><style>{_BASE_STYLE}</style></head>
<body><main class="page"><header class="page-header"><h1>{html.escape(title)}</h1><p class="lead">{html.escape(intro)}</p><div class="snapshot-meta">{visible_meta}</div></header>{body}<footer class="footer">Personenbezugsfreier öffentlicher Export. Die Datenbank ist der einzige Speicherort personenbezogener Daten.</footer></main></body></html>'''


def _snapshot_html(state: Mapping[str, Any], *, mode: str) -> str:
    meta = dict(state.get("meta") or {})
    trans = state.get("transparency") or {}
    period = trans.get("period") or {}
    meta.update({"snapshot_type": mode, "period_start": period.get("start"), "period_end": period.get("end")})
    articles = list(state.get("articles") or [])
    toc_links = [
        f'<a href="#a-{idx}">{html.escape(str(a.get("public_code") or ""))} – {html.escape(str(a.get("toc_title") or a.get("title") or ""))}</a>'
        for idx, a in enumerate(articles, 1)
        if bool(a.get("show_in_toc", True))
    ]
    toc = '<nav class="toc"><h2>Inhaltsverzeichnis</h2>' + "".join(toc_links) + "</nav>"
    article_html = "".join(f'<div id="a-{idx}">{_render_article(a, include_comments=True)}</div>' for idx, a in enumerate(articles, 1))
    body = (
        toc
        + '<section id="gesetz">'
        + article_html
        + "</section>"
        + _render_transparency(trans)
        + _render_archive_minimd(articles)
    )
    generated = _parse_iso_datetime(str(meta.get("generated_at") or ""))
    day = generated.strftime("%Y-%m-%d") if generated is not None else ""
    return _html_page(
        title=f"{str(getattr(settings, 'PROJECT_NAME', '') or 'Webprojekt')} – Snapshot{(' ' + day) if day else ''}",
        intro="Statische, personenbezugsfreie Momentaufnahme des öffentlichen Dokument- und Beteiligungsstands.",
        meta=meta,
        body=body,
    )


def _context_vote_lines(summary: Mapping[str, Any], *, label: str) -> list[str]:
    votes = summary.get("votes") or {}
    by_main = votes.get("by_main_vote") or {}
    vote_bits = " · ".join(f"{emoji} {int(by_main.get(emoji) or 0)}" for emoji in PUBLIC_MAIN_VOTES)
    reactions = summary.get("reactions") or {}
    reaction_bits = " · ".join(
        f"{emoji} {int(count or 0)}" for emoji, count in sorted(reactions.items())
    ) if reactions else "–"
    return [
        f"{label}-Stimmen: {int(votes.get('total') or 0)} ({vote_bits})",
        f"{label}-Reaktionen: {reaction_bits}",
    ]


def _context_text(value: Any) -> str:
    return str(value or "").replace("\r\n", "\n").replace("\r", "\n").strip()


def _comment_change_context(comment: Mapping[str, Any]) -> list[str]:
    """Render only the precise MiniMD patch fragments needed for LLM context.

    This is intentionally *not* a second MiniMD dialect. The context envelope is
    ordinary Markdown text; old/new snippets are emitted verbatim and unfenced.
    """
    mode = str(comment.get("comment_mode") or "change")
    if mode == "delete_article":
        return ["Änderung: Artikel löschen."]

    payload = comment.get("patch_payload") if isinstance(comment.get("patch_payload"), Mapping) else {}
    raw_parts = payload.get("parts") if isinstance(payload, Mapping) else []
    out: list[str] = []
    if isinstance(raw_parts, list):
        for idx, raw in enumerate(raw_parts, 1):
            if not isinstance(raw, Mapping):
                continue
            block_key = str(raw.get("block_key") or raw.get("block_id") or "juristisch")
            old_text = _context_text(raw.get("old_text"))
            new_text = _context_text(raw.get("new_text"))
            if not old_text and not new_text:
                continue
            out.extend([f"#### Änderung {idx} · Block {block_key}", ""])
            if old_text:
                out.extend(["Bisher:", old_text, ""])
            if new_text:
                out.extend(["Vorschlag:", new_text, ""])
    if out:
        while out and out[-1] == "":
            out.pop()
        return out

    raise ValueError(
        f"Public comment {comment.get('export_id') or ''!r} has no structured patch payload"
    )


def _llm_law_markdown(state: Mapping[str, Any]) -> str:
    meta = dict(state.get("meta") or {})
    articles = list(state.get("articles") or [])
    current = dict(((state.get("transparency") or {}).get("current_public_state") or {}))
    lines = [
        f"# {str(getattr(settings, 'PROJECT_NAME', '') or 'Webprojekt')} – Dokumentkontext",
        "",
        f"Stand: {meta.get('generated_at') or '–'}",
        f"Web-Version: {meta.get('web_version') or 'unknown'}",
        f"Artikel: {len(articles)}",
        f"Öffentliche Kommentare: {int(current.get('published_comments') or 0)}",
        f"Bestätigte Artikelstimmen: {int(current.get('article_votes_on_current_public_versions') or 0)}",
        f"Bestätigte Kommentarstimmen: {int(current.get('comment_votes_on_current_public_comments') or 0)}",
        f"Artikelreaktionen: {int(current.get('article_reactions_on_current_public_versions') or 0)}",
        f"Kommentarreaktionen: {int(current.get('comment_reactions_on_current_public_comments') or 0)}",
        "",
        "## Kanonischer Dokumenttext (MiniMD)",
        "",
        _law_minimd_text(articles).rstrip(),
        "",
    ]
    return "\n".join(lines)


def _llm_article_markdown(article: Mapping[str, Any], meta: Mapping[str, Any]) -> str:
    comments = list(article.get("comments") or [])
    code_label = str(article.get("public_code") or "").strip()
    title = str(article.get("title") or "").strip()
    heading = " – ".join(x for x in (code_label, title) if x) or "Artikel"
    lines = [
        f"# {str(getattr(settings, 'PROJECT_NAME', '') or 'Webprojekt')} – Artikelkontext – {heading}",
        "",
        f"Stand: {meta.get('generated_at') or '–'}",
        f"Web-Version: {meta.get('web_version') or 'unknown'}",
        f"Version: {article.get('version_label') or '–'}",
        f"Öffentliche Kommentare: {len(comments)}",
        *_context_vote_lines(article.get("vote_summary") or {}, label="Artikel"),
        "",
        "## Aktueller Artikel im MiniMD-Arbeitsformat",
        "",
        _context_text(article.get("article_minimd")),
        "",
        "## Öffentliche Änderungsvorschläge",
    ]
    if not comments:
        lines.extend(["", "Keine öffentlichen Änderungsvorschläge zu dieser Fassung."])
        return "\n".join(lines).rstrip() + "\n"

    for comment in comments:
        lines.extend([
            "",
            f"### Kommentar {comment.get('export_id') or ''}",
            f"Modus: {comment.get('comment_mode') or 'change'}",
            f"Typ: {comment.get('type') or 'standard'}",
            f"Veröffentlicht: {comment.get('published_at') or '–'}",
        ])
        if comment.get("parent_export_id"):
            lines.append(f"Antwort auf: {comment.get('parent_export_id')}")
        lines.extend(_context_vote_lines(comment.get("vote_summary") or {}, label="Kommentar"))
        explanation = _context_text(comment.get("explanation"))
        sources = _context_text(comment.get("sources"))
        if explanation:
            lines.extend(["", "Begründung:", explanation])
        if sources:
            lines.extend(["", "Quellen:", sources])
        lines.extend(["", *_comment_change_context(comment)])
    return "\n".join(lines).rstrip() + "\n"


# ---------------------------------------------------------------------------
# Snapshot export
# ---------------------------------------------------------------------------


def _ensure_release_target_free(naming: ExportNaming) -> None:
    target = naming.versions_dir / naming.html_name
    if target.exists() or (naming.versions_dir.exists() and any(naming.versions_dir.glob(f"*_{naming.repo_tag}_entwurf.html"))):
        raise FileExistsError(
            f"Release-Snapshot für diesen UTC-Tag existiert bereits: {naming.versions_dir}. "
            "Der Export überschreibt Release-Archive nicht."
        )


def _export_snapshot(
    mode: str,
    *,
    base_dir: Path | str | None = None,
    now: datetime | None = None,
    web_version: str | None = None,
) -> dict[str, str]:
    root = Path(base_dir).resolve() if base_dir is not None else BASE_DIR
    now_utc = _aware_utc(now or _utc_now())
    assert now_utc is not None
    naming = _build_export_naming(root, mode, now_utc)
    if mode == "release":
        _ensure_release_target_free(naming)

    db = get_session()
    try:
        period = _snapshot_period(root, naming, now_utc)
        state = _collect_public_state(db, period=period, now=now_utc, include_comments=True, web_version=web_version)
    finally:
        # Explicit rollback keeps the script read-only even if a future helper
        # accidentally dirties the SQLAlchemy transaction without committing.
        try:
            db.rollback()
        finally:
            db.close()

    html_text = _snapshot_html(state, mode=mode)
    html_target = naming.versions_dir / naming.html_name
    _atomic_write_text(html_target, html_text)
    return {"html_path": str(html_target)}


def export_snapshot_release(
    *,
    base_dir: Path | str | None = None,
    now: datetime | None = None,
    web_version: str | None = None,
) -> dict[str, str]:
    return _export_snapshot("release", base_dir=base_dir, now=now, web_version=web_version)


def export_snapshot_monthly(
    *,
    base_dir: Path | str | None = None,
    now: datetime | None = None,
    web_version: str | None = None,
) -> dict[str, str]:
    return _export_snapshot("monthly", base_dir=base_dir, now=now, web_version=web_version)


def export_snapshot(
    *,
    base_dir: Path | str | None = None,
    now: datetime | None = None,
    web_version: str | None = None,
) -> dict[str, str]:
    return export_snapshot_release(base_dir=base_dir, now=now, web_version=web_version)


def list_snapshot_exports(*, base_dir: Path | str | None = None, limit: int = 50) -> list[dict[str, Any]]:
    """List actual snapshot HTML files for admin/UI use; filesystem is truth."""
    root = Path(base_dir).resolve() if base_dir is not None else BASE_DIR
    rows: list[dict[str, Any]] = []
    for report_type in ("release", "monthly"):
        subdir, _prefix, repo_tag = _snapshot_mode_spec(report_type)
        pattern = f"*_{repo_tag}_entwurf.html"
        base = root / "versions" / subdir
        if not base.is_dir():
            continue
        for path in base.glob(f"*/{pattern}"):
            if not path.is_file():
                continue
            meta = _read_snapshot_meta(path)
            generated = _parse_iso_datetime(meta.get("kgg-generated-at"))
            period_start = _parse_iso_datetime(meta.get("kgg-period-start")) or generated
            period_end = _parse_iso_datetime(meta.get("kgg-period-end")) or generated
            if generated is None:
                generated = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
            if period_start is None:
                period_start = generated
            if period_end is None:
                period_end = generated
            rel = path.resolve().relative_to(root.resolve()).as_posix()
            file_names = {"html": path.name}
            relative_paths = {"html": rel}
            rows.append({
                "report_type": report_type,
                "period_start": period_start,
                "period_end": period_end,
                "generated_at": generated,
                "file_names": file_names,
                "relative_paths": relative_paths,
            })
    rows.sort(key=lambda r: r["generated_at"], reverse=True)
    return rows[: max(1, min(int(limit), 500))]


# ---------------------------------------------------------------------------
# LLM contexts
# ---------------------------------------------------------------------------


def _llm_dir(base_dir: Path) -> Path:
    path = base_dir / LLM_API_DIRNAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def _find_latest(pattern: Iterable[Path]) -> Path | None:
    files = sorted((p for p in pattern if p.is_file()), key=lambda p: p.name)
    return files[-1] if files else None


def _cooldown_active(path: Path, max_age_hours: int, now: datetime) -> bool:
    if max_age_hours <= 0 or not path.is_file():
        return False
    age = now.timestamp() - path.stat().st_mtime
    return 0 <= age < max_age_hours * 3600


def _file_fingerprint(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except Exception:
        return None


def _law_context_state(db, now: datetime, web_version: str | None = None) -> dict[str, Any]:
    dummy = Period(now, now, "llm")
    # Keep public comments in the collected state only so the header statistics
    # describe the actual public state. The law body itself remains pure MiniMD.
    return _collect_public_state(db, period=dummy, now=now, include_comments=True, web_version=web_version)


def _article_context_state(db, article_id: int, now: datetime, web_version: str | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
    dummy = Period(now, now, "llm")
    state = _collect_public_state(db, period=dummy, now=now, include_comments=True, web_version=web_version)
    # article_id is intentionally not public; resolve with one safe DB lookup to public_code.
    public_code = db.query(Article.public_code).filter(Article.id == int(article_id)).scalar()
    if public_code is None:
        raise ValueError(f"Article not found: {article_id}")
    article = next((a for a in state["articles"] if str(a.get("public_code") or "") == str(public_code)), None)
    if article is None:
        raise ValueError(f"Article is not public: {article_id}")
    meta = {
        "web_version": state.get("meta", {}).get("web_version"),
        "generated_at": _safe_iso(now),
    }
    return article, meta


def export_llm_law_context(*, force: bool = False, base_dir: Path | str | None = None, now: datetime | None = None, web_version: str | None = None) -> dict[str, str]:
    root = Path(base_dir).resolve() if base_dir is not None else BASE_DIR
    now_utc = _aware_utc(now or _utc_now())
    assert now_utc is not None
    db = get_session()
    try:
        state = _law_context_state(db, now_utc, web_version=web_version)
    finally:
        try:
            db.rollback()
        finally:
            db.close()
    llm_dir = _llm_dir(root)
    pattern = f"*_{LLM_FILENAME_PREFIX}_{LLM_LAW_CONTEXT_LABEL}.md"
    existing = _find_latest(llm_dir.glob(pattern))
    content = _llm_law_markdown(state)
    fingerprint = hashlib.sha256(content.encode("utf-8")).hexdigest()
    if existing is not None and not force and _file_fingerprint(existing) == fingerprint and _cooldown_active(existing, LLM_LAW_CONTEXT_MAX_AGE_HOURS, now_utc):
        return {"md_path": str(existing), "reused": "true"}
    target = llm_dir / f"{_date_only(now_utc)}_{LLM_FILENAME_PREFIX}_{LLM_LAW_CONTEXT_LABEL}.md"
    _atomic_write_text(target, content)
    for old in list(llm_dir.glob(pattern)):
        if old.resolve() != target.resolve():
            old.unlink(missing_ok=True)
    # Clean obsolete exporter-generated HTML contexts from this same context family.
    for old in list(llm_dir.glob(f"*_{LLM_FILENAME_PREFIX}_{LLM_LAW_CONTEXT_LABEL}.html")):
        old.unlink(missing_ok=True)
    return {"md_path": str(target), "reused": "false"}


def export_llm_article_context(article_id: int, *, force: bool = False, base_dir: Path | str | None = None, now: datetime | None = None, web_version: str | None = None) -> dict[str, str]:
    root = Path(base_dir).resolve() if base_dir is not None else BASE_DIR
    now_utc = _aware_utc(now or _utc_now())
    assert now_utc is not None
    db = get_session()
    try:
        article, meta = _article_context_state(db, int(article_id), now_utc, web_version=web_version)
    finally:
        try:
            db.rollback()
        finally:
            db.close()
    llm_dir = _llm_dir(root)
    token = _article_number_token(article.get("public_code"), article.get("title"))
    title = _slugish(article.get("toc_title") or article.get("title"))
    pattern = f"*_{LLM_FILENAME_PREFIX}_{LLM_ARTICLE_CONTEXT_LABEL}_{token}-*.md"
    existing = _find_latest(llm_dir.glob(pattern))
    content = _llm_article_markdown(article, meta)
    fingerprint = hashlib.sha256(content.encode("utf-8")).hexdigest()
    if existing is not None and not force and _file_fingerprint(existing) == fingerprint and _cooldown_active(existing, LLM_ARTICLE_CONTEXT_MAX_AGE_HOURS, now_utc):
        return {"md_path": str(existing), "reused": "true"}
    target = llm_dir / f"{_date_only(now_utc)}_{LLM_FILENAME_PREFIX}_{LLM_ARTICLE_CONTEXT_LABEL}_{token}-{title}.md"
    _atomic_write_text(target, content)
    for old in list(llm_dir.glob(pattern)):
        if old.resolve() != target.resolve():
            old.unlink(missing_ok=True)
    for old in list(llm_dir.glob(f"*_{LLM_FILENAME_PREFIX}_{LLM_ARTICLE_CONTEXT_LABEL}_{token}-*.html")):
        old.unlink(missing_ok=True)
    return {"md_path": str(target), "reused": "false"}


def ensure_llm_law_context_current() -> Path:
    return Path(export_llm_law_context(force=False)["md_path"])


def ensure_llm_article_context_current(article_id: int) -> Path:
    return Path(export_llm_article_context(int(article_id), force=False)["md_path"])


def _llm_instruction_config() -> dict[str, dict[str, str]]:
    raw = getattr(settings, "LLM_INSTRUCTIONS_BY_MODE", {}) or {}
    if not isinstance(raw, dict):
        return {}
    out: dict[str, dict[str, str]] = {}
    for mode, cfg in raw.items():
        if not isinstance(cfg, dict):
            continue
        source = str(cfg.get("source_filename") or "").strip()
        label = str(cfg.get("download_label") or "").strip()
        ui_label = str(cfg.get("ui_label") or mode).strip()
        if source and label:
            out[str(mode)] = {"source_filename": source, "download_label": label, "ui_label": ui_label}
    return out


def ensure_llm_instructions_current(mode: str = "change", *, force: bool = False) -> Path:
    mode_key = str(mode or "change").strip() or "change"
    cfg = _llm_instruction_config().get(mode_key)
    if not cfg:
        raise FileNotFoundError(f"Keine LLM-Anweisungen für Kommentar-Modus: {mode_key}")
    llm_dir = _llm_dir(BASE_DIR)
    source = llm_dir / cfg["source_filename"]
    if not source.is_file():
        raise FileNotFoundError(f"LLM-Anweisungsdatei fehlt: {source}")
    target = llm_dir / f"{_date_only(_utc_now())}_{LLM_FILENAME_PREFIX}_{cfg['download_label']}.md"
    if target.is_file() and not force:
        return target
    content = source.read_text(encoding="utf-8")
    _atomic_write_text(target, content)
    for old in llm_dir.glob(f"*_{LLM_FILENAME_PREFIX}_{cfg['download_label']}.md"):
        if old.resolve() != target.resolve():
            old.unlink(missing_ok=True)
    return target


def llm_context_status() -> dict[str, Any]:
    llm_dir = _llm_dir(BASE_DIR)
    modes: dict[str, Any] = {}
    for mode, cfg in _llm_instruction_config().items():
        modes[mode] = {
            "available": bool((llm_dir / cfg["source_filename"]).is_file()),
            "instructions_label": cfg["download_label"],
            "ui_label": cfg["ui_label"],
        }
    return {"modes": modes}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description="Erzeugt öffentliche Archiv-Snapshots als HTML und LLM-Kontexte als Markdown/MiniMD-Kontext.")
    parser.add_argument("--mode", choices=["release", "monthly", "llm-law", "llm-article"], default="release")
    parser.add_argument("--article-id", type=int, default=None)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--base-dir", type=Path, default=None, help="Abweichendes Ausgabe-Root (primär für lokale Tests).")
    parser.add_argument("--web-version", default=None, help="Optionaler Web-Versions-Override; Standard ist der Git-Ref des laufenden Web-Checkouts.")
    parser.add_argument("--now", default=None, help="Optionaler UTC/ISO-Testzeitpunkt.")
    args = parser.parse_args()
    now = _parse_iso_datetime(args.now) if args.now else None
    if args.mode == "release":
        result = export_snapshot_release(base_dir=args.base_dir, now=now, web_version=args.web_version)
    elif args.mode == "monthly":
        result = export_snapshot_monthly(base_dir=args.base_dir, now=now, web_version=args.web_version)
    elif args.mode == "llm-law":
        result = export_llm_law_context(force=args.force, base_dir=args.base_dir, now=now, web_version=args.web_version)
    else:
        if args.article_id is None:
            raise SystemExit("--article-id ist für --mode llm-article erforderlich.")
        result = export_llm_article_context(args.article_id, force=args.force, base_dir=args.base_dir, now=now, web_version=args.web_version)
    print("Export erzeugt:")
    for key, value in result.items():
        print(f"  {key}: {value}")


if __name__ == "__main__":
    main()
