#!/usr/bin/env python3
"""Build a fresh local klimagg-web demo database from demo/<profile>/*.json."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
DEFAULT_DEMO_DIR = ROOT / "demo"
DEFAULT_DATABASE = ROOT / "klimagg-web-demo.db"

VOTE_MAP = {
    1: "✅",
    0: "🟡",
    -1: "🔴",
    "1": "✅",
    "0": "🟡",
    "-1": "🔴",
    "✅": "✅",
    "🟢": "🟢",
    "🟡": "🟡",
    "🟠": "🟠",
    "🔴": "🔴",
}

RECOMMENDATION_MAP = {
    "accept": "annehmen",
    "revise": "korrigieren",
    "reject": "ablehnen",
    "open": "offen",
    "annehmen": "annehmen",
    "korrigieren": "korrigieren",
    "überarbeiten": "korrigieren",
    "ablehnen": "ablehnen",
    "offen": "offen",
}

ALLOWED_COMMENT_MODES = {"change", "new_article", "delete_article"}


class SeedError(RuntimeError):
    pass


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build a fresh local demo database from demo profile JSON files."
    )
    parser.add_argument("profile", nargs="?", default="neutral")
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Delete the target SQLite database before seeding.",
    )
    parser.add_argument(
        "--database",
        type=Path,
        default=DEFAULT_DATABASE,
        help="SQLite file to create (default: ./klimagg-web-demo.db).",
    )
    parser.add_argument(
        "--demo-dir",
        type=Path,
        default=DEFAULT_DEMO_DIR,
        help="Directory containing profile folders (default: ./demo).",
    )
    return parser.parse_args()


def load_json(path: Path) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
    except FileNotFoundError as exc:
        raise SeedError(f"Missing demo file: {path}") from exc
    except json.JSONDecodeError as exc:
        raise SeedError(f"Invalid JSON in {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise SeedError(f"Expected a JSON object in {path}")
    return data


def load_profile(demo_dir: Path, profile: str) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    base = demo_dir / profile
    project = load_json(base / "project.json")
    law = load_json(base / "law.json")
    participation = load_json(base / "participation.json")

    declared = project.get("profile")
    if declared and declared != profile:
        raise SeedError(f"project.json declares profile {declared!r}, expected {profile!r}")

    return project, law, participation


def configure_database(database: Path) -> Path:
    database = database.expanduser().resolve()
    if database.suffix.lower() not in {".db", ".sqlite", ".sqlite3"}:
        raise SeedError("Demo seeding is restricted to a local SQLite file.")

    database.parent.mkdir(parents=True, exist_ok=True)

    # Set this before importing config/db/models. Environment variables override
    # a repository .env and keep demo seeding away from the production database.
    os.environ["DATABASE_URL"] = f"sqlite:///{database.as_posix()}"
    return database


def reset_database_file(database: Path, engine: Any) -> None:
    # Validation is completed before this function is called.
    engine.dispose()
    for path in (
        database,
        Path(str(database) + "-wal"),
        Path(str(database) + "-shm"),
    ):
        if path.exists():
            path.unlink()


def import_runtime() -> dict[str, Any]:
    from db import Base, SessionLocal, engine
    from config import settings
    from models import (
        Article,
        ArticleReaction,
        ArticleVersion,
        ArticleVote,
        Comment,
        CommentReaction,
        CommentVote,
        Review,
        User,
    )
    import indiff
    import review as review_code

    return {
        "Base": Base,
        "SessionLocal": SessionLocal,
        "engine": engine,
        "settings": settings,
        "User": User,
        "Article": Article,
        "ArticleVersion": ArticleVersion,
        "ArticleVote": ArticleVote,
        "ArticleReaction": ArticleReaction,
        "Comment": Comment,
        "CommentVote": CommentVote,
        "CommentReaction": CommentReaction,
        "Review": Review,
        "indiff": indiff,
        "review_code": review_code,
    }


def require_unique(items: list[Any], label: str) -> None:
    duplicates = sorted(key for key, count in Counter(items).items() if count > 1)
    if duplicates:
        raise SeedError(f"Duplicate {label}: {duplicates}")


def normalize_trust_level(value: Any, *, minimum: int = 0, maximum: int = 25) -> int:
    try:
        raw = int(value)
    except (TypeError, ValueError) as exc:
        raise SeedError(f"Invalid trust_level: {value!r}") from exc
    if minimum <= raw <= maximum:
        return raw
    raise SeedError(f"trust_level must be in {minimum}..{maximum}, got {raw}")


def normalize_impact_scores(value: Any) -> dict[str, int] | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise SeedError("impact_scores must be an object")
    allowed = {"goal", "clarity", "practical", "legal"}
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise SeedError(f"Unknown impact score keys: {unknown}")
    out: dict[str, int] = {}
    for key in allowed:
        if key not in value:
            continue
        try:
            score = int(value[key])
        except (TypeError, ValueError) as exc:
            raise SeedError(f"Invalid impact score {key}: {value[key]!r}") from exc
        if not 0 <= score <= 4:
            raise SeedError(f"Impact score {key} must be in 0..4, got {score}")
        out[key] = score
    return out or None


def normalize_vote(value: Any) -> str:
    try:
        return VOTE_MAP[value]
    except (KeyError, TypeError) as exc:
        raise SeedError(f"Unsupported demo vote: {value!r}") from exc


def normalize_recommendation(value: Any) -> str:
    key = str(value or "").strip().lower()
    if key not in RECOMMENDATION_MAP:
        raise SeedError(f"Unsupported review recommendation: {value!r}")
    return RECOMMENDATION_MAP[key]


def normalize_sources(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, list):
        rows = [str(item).strip() for item in value if str(item).strip()]
        return "\n".join(rows) or None
    text = str(value).strip()
    return text or None


def join_demo_block(value: Any) -> str:
    if isinstance(value, list):
        return "\n\n".join(str(item).strip() for item in value if str(item).strip())
    return str(value or "").strip()


def normalize_demo_minimd(text: str) -> str:
    """Normalize the small amount of Markdown syntax used by profile fixtures."""
    out: list[str] = []
    for line in str(text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        heading = re.match(r"^#{1,6}\s+(.*)$", line)
        if heading:
            line = f"**{heading.group(1).strip()}**"
        elif line.startswith("> "):
            line = line[2:]
        out.append(line.rstrip())
    return "\n".join(out).strip()


def render_content_blocks(indiff: Any, article_data: dict[str, Any]) -> dict[str, str]:
    blocks = article_data.get("blocks") or {}
    if not isinstance(blocks, dict):
        raise SeedError(f"Invalid blocks for article {article_data.get('slug')!r}")

    rendered: dict[str, str] = {}
    for block_key, value in blocks.items():
        minimd = normalize_demo_minimd(join_demo_block(value))
        if minimd:
            rendered[str(block_key)] = indiff.minimd_to_html_text(str(block_key), minimd)
    return rendered


def canonical_minimd_by_block(indiff: Any, content_blocks: dict[str, str]) -> dict[str, str]:
    order = list(indiff.minimd_block_order())
    extras = [key for key in content_blocks.keys() if key not in order]
    result: dict[str, str] = {}
    for key in order + sorted(extras):
        raw = content_blocks.get(key) or ""
        if key == "meta":
            result[key] = str(raw).replace("\r\n", "\n").replace("\r", "\n")
        else:
            result[key] = indiff.html_to_minimd_text(key, str(raw))
    return result


def render_minimd_blocks(indiff: Any, minimd_by_block: dict[str, str]) -> dict[str, str]:
    return {
        key: (value if key == "meta" else indiff.minimd_to_html_text(key, value))
        for key, value in minimd_by_block.items()
        if value
    }


def canonicalize_patch_fragment(indiff: Any, block_key: str, text: str) -> str:
    minimd = normalize_demo_minimd(str(text or ""))
    html = indiff.minimd_to_html_text(block_key, minimd)
    return indiff.html_to_minimd_text(block_key, html)


def locate_patch_fragment(indiff: Any, block_key: str, baseline: str, source_text: str) -> tuple[int, str, bool]:
    canonical = canonicalize_patch_fragment(indiff, block_key, source_text)
    candidates: list[tuple[str, bool]] = [(canonical, False)]
    if canonical.endswith("\n"):
        candidates.append((canonical.rstrip("\n"), True))
    raw = normalize_demo_minimd(source_text)
    if raw and raw not in {item[0] for item in candidates}:
        candidates.append((raw, False))

    for candidate, stripped_newline in candidates:
        if not candidate:
            continue
        pos = baseline.find(candidate)
        if pos >= 0:
            if baseline.find(candidate, pos + 1) >= 0:
                raise SeedError(
                    f"Patch selection is ambiguous in block {block_key!r}: {candidate[:100]!r}"
                )
            return pos, candidate, stripped_newline

    raise SeedError(
        f"Patch source text not found in canonical block {block_key!r}: {source_text[:160]!r}"
    )


def build_patch_v2(
    indiff: Any,
    baseline_by_block: dict[str, str],
    change: dict[str, Any],
    *,
    part_id: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    block_key = str(change.get("block") or "").strip()
    if block_key not in baseline_by_block:
        raise SeedError(f"Unknown patch block: {block_key!r}")

    baseline = str(baseline_by_block[block_key] or "")
    start, old_text, stripped_newline = locate_patch_fragment(
        indiff,
        block_key,
        baseline,
        str(change.get("find") or ""),
    )

    new_text = canonicalize_patch_fragment(indiff, block_key, str(change.get("replace") or ""))
    if stripped_newline:
        new_text = new_text.rstrip("\n")

    block_hash = hashlib.sha256(baseline.encode("utf-8")).hexdigest()
    part = {
        "part_id": part_id,
        "block_key": block_key,
        "old_text": old_text,
        "new_text": new_text,
        "sel_start": start,
        "sel_end": start + len(old_text),
        "baseline_hash": block_hash,
    }
    payload = {
        "version": 2,
        "parts": [part],
        "editor": {"source": "demo-seed"},
    }

    persisted = indiff.PersistedCommentInput(
        cid=0,
        parts=[indiff.PersistedCommentPartInput(**part)],
    )
    validation = indiff.validate_exact_selection_patch_payload(
        baseline_old_minimd_by_block=baseline_by_block,
        comment=persisted,
        require_baseline_hash=True,
        require_selection=True,
    )
    if not validation.ok:
        raise SeedError(f"Generated Patch v2 failed validation: {validation.errors}")

    anchor = {
        "block_key": block_key,
        "selected_text": old_text,
        "sel_start": start,
        "sel_end": start + len(old_text),
        "baseline_hash": block_hash,
    }
    return payload, anchor


def validate_profile(
    project: dict[str, Any],
    law: dict[str, Any],
    participation: dict[str, Any],
) -> None:
    articles = law.get("articles")
    if not isinstance(articles, list) or not articles:
        raise SeedError("law.json must contain a non-empty articles array")

    article_slugs = [str(item.get("slug") or "") for item in articles]
    public_codes = [str(item.get("public_code") or "") for item in articles]
    sort_orders = [item.get("sort_order") for item in articles]
    if any(not slug for slug in article_slugs):
        raise SeedError("Every article needs a slug")
    if any(not code for code in public_codes):
        raise SeedError("Every article needs a public_code")
    require_unique(article_slugs, "article slug")
    require_unique(public_codes, "article public_code")
    require_unique(sort_orders, "article sort_order")
    article_set = set(article_slugs)

    users = participation.get("users") or []
    if not users:
        raise SeedError("participation.json must contain demo users")
    user_keys = [str(item.get("key") or "") for item in users]
    emails = [str(item.get("email") or "") for item in users]
    require_unique(user_keys, "user key")
    require_unique(emails, "user email")
    if not any(bool(item.get("is_admin")) for item in users):
        raise SeedError("At least one demo user must be an admin")
    for email in emails:
        if not email.endswith(".invalid"):
            raise SeedError(f"Demo email must use the .invalid domain: {email}")
    user_set = set(user_keys)
    for item in users:
        normalize_trust_level(item.get("trust_level", 0))

    def require_article_ref(item: dict[str, Any]) -> None:
        ref = str(item.get("article") or "")
        if ref not in article_set:
            raise SeedError(f"Unknown article reference: {ref!r}")

    def require_user_ref(item: dict[str, Any], field: str) -> None:
        ref = str(item.get(field) or "")
        if ref not in user_set:
            raise SeedError(f"Unknown user reference in {field}: {ref!r}")

    for section in ("article_votes", "article_reactions"):
        for item in participation.get(section, []):
            require_article_ref(item)
            require_user_ref(item, "user")

    comment_keys: set[str] = set()
    for section in ("proposals", "historical_proposals", "comments"):
        for item in participation.get(section, []):
            key = str(item.get("key") or "")
            if not key or key in comment_keys:
                raise SeedError(f"Duplicate or empty comment/proposal key: {key!r}")
            comment_keys.add(key)
            require_article_ref(item)
            # Historical version deltas may intentionally have no attributed author.
            # They are assigned to the synthetic demo author during seeding.
            if section != "historical_proposals" or item.get("author"):
                require_user_ref(item, "author")
            normalize_impact_scores(item.get("impact_scores"))

    for section in ("comment_votes", "comment_reactions"):
        for item in participation.get(section, []):
            require_user_ref(item, "user")
            ref = str(item.get("proposal") or item.get("comment") or "")
            if ref not in comment_keys:
                raise SeedError(f"Unknown comment/proposal reference: {ref!r}")

    for section in ("reviews", "historical_reviews"):
        for item in participation.get(section, []):
            require_user_ref(item, "reviewer")
            ref = str(item.get("proposal") or item.get("comment") or "")
            if ref not in comment_keys:
                raise SeedError(f"Unknown review target: {ref!r}")


def build_article_payloads(indiff: Any, law: dict[str, Any], participation: dict[str, Any]) -> dict[str, dict[str, Any]]:
    payloads: dict[str, dict[str, Any]] = {}
    for item in law["articles"]:
        content_blocks = render_content_blocks(indiff, item)
        payloads[item["slug"]] = {
            "article": item,
            "current_content_blocks": content_blocks,
            "current_minimd": canonical_minimd_by_block(indiff, content_blocks),
            "historical": {},
        }

    current_version = str(law["law"]["version"])
    historical = participation.get("historical_proposals") or []
    source_versions = sorted({str(item.get("source_version") or "") for item in historical if item.get("source_version")})

    for source_version in source_versions:
        relevant = [
            item for item in historical
            if str(item.get("source_version") or "") == source_version
        ]
        bad_targets = {
            str(item.get("target_version") or "")
            for item in relevant
            if str(item.get("target_version") or "") != current_version
        }
        if bad_targets:
            raise SeedError(
                "Historical demo reconstruction currently supports direct predecessors of "
                f"the current law version only; got targets {sorted(bad_targets)}"
            )

        by_article: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for item in relevant:
            by_article[str(item["article"])].append(item)

        for slug, payload in payloads.items():
            source_minimd = dict(payload["current_minimd"])
            for proposal in reversed(by_article.get(slug, [])):
                change = proposal["change"]
                block_key = str(change["block"])
                baseline = str(source_minimd.get(block_key) or "")
                pos, new_text, stripped_newline = locate_patch_fragment(
                    indiff,
                    block_key,
                    baseline,
                    str(change["replace"]),
                )
                old_text = canonicalize_patch_fragment(indiff, block_key, str(change["find"]))
                if stripped_newline:
                    old_text = old_text.rstrip("\n")
                source_minimd[block_key] = baseline[:pos] + old_text + baseline[pos + len(new_text):]

            payload["historical"][source_version] = {
                "minimd": source_minimd,
                "content_blocks": render_minimd_blocks(indiff, source_minimd),
            }

    return payloads


def seed_database(
    runtime: dict[str, Any],
    law: dict[str, Any],
    participation: dict[str, Any],
    article_payloads: dict[str, dict[str, Any]],
    *,
    profile: str,
) -> dict[str, int]:
    Base = runtime["Base"]
    SessionLocal = runtime["SessionLocal"]
    engine = runtime["engine"]
    User = runtime["User"]
    Article = runtime["Article"]
    ArticleVersion = runtime["ArticleVersion"]
    ArticleVote = runtime["ArticleVote"]
    ArticleReaction = runtime["ArticleReaction"]
    Comment = runtime["Comment"]
    CommentVote = runtime["CommentVote"]
    CommentReaction = runtime["CommentReaction"]
    Review = runtime["Review"]
    indiff = runtime["indiff"]
    review_code = runtime["review_code"]
    settings = runtime["settings"]

    Base.metadata.create_all(bind=engine)
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    with SessionLocal() as db:
        if db.query(Article).first() or db.query(User).first():
            raise SeedError("Target database is not empty. Use --reset or another --database file.")

        users: dict[str, Any] = {}
        for item in participation["users"]:
            user = User(
                email=item["email"],
                pseudonym=item["pseudonym"],
                trust_level=normalize_trust_level(
                    item.get("trust_level", 0),
                    minimum=int(getattr(settings, "TRUST_MIN", 0)),
                    maximum=int(getattr(settings, "TRUST_MAX", 25)),
                ),
                is_admin=bool(item.get("is_admin", False)),
            )
            db.add(user)
            db.flush()
            users[item["key"]] = user

        admin_user = next(user for key, user in users.items() if user.is_admin)
        default_author_key = next(
            (key for key in users if "author" in key.lower()),
            next(key for key, user in users.items() if user.is_admin),
        )
        current_version_label = str(law["law"]["version"])
        historical_versions = sorted({
            str(item.get("source_version"))
            for item in participation.get("historical_proposals", [])
            if item.get("source_version")
        })

        articles: dict[str, Any] = {}
        versions: dict[tuple[str, str], Any] = {}

        for item in law["articles"]:
            article = Article(
                slug=item["slug"],
                public_code=item["public_code"],
                sort_order=int(item["sort_order"]),
                title=item["title"],
                type=item.get("type", "article"),
                show_in_toc=bool(item.get("show_in_toc", True)),
                toc_title=item.get("toc_title"),
            )
            db.add(article)
            db.flush()
            articles[item["slug"]] = article

            payload = article_payloads[item["slug"]]
            for version_label in historical_versions:
                hist = payload["historical"].get(version_label)
                if not hist:
                    continue
                version = ArticleVersion(
                    article_id=article.id,
                    version_label=version_label,
                    content_blocks=hist["content_blocks"],
                    status="archived",
                    published_at=now,
                    created_by_user_id=admin_user.id,
                )
                db.add(version)
                db.flush()
                versions[(item["slug"], version_label)] = version

            current = ArticleVersion(
                article_id=article.id,
                version_label=current_version_label,
                content_blocks=payload["current_content_blocks"],
                status="published",
                published_at=now,
                created_by_user_id=admin_user.id,
            )
            db.add(current)
            db.flush()
            versions[(item["slug"], current_version_label)] = current
            article.current_version_id = current.id

        db.flush()

        for item in participation.get("article_votes", []):
            article = articles[item["article"]]
            version = versions[(item["article"], current_version_label)]
            db.add(ArticleVote(
                user_id=users[item["user"]].id,
                article_id=article.id,
                version_id=version.id,
                main_vote=normalize_vote(item["vote"]),
                flags=None,
                status="confirmed",
                confirmed_at=now,
            ))

        for item in participation.get("article_reactions", []):
            article = articles[item["article"]]
            version = versions[(item["article"], current_version_label)]
            db.add(ArticleReaction(
                user_id=users[item["user"]].id,
                article_id=article.id,
                version_id=version.id,
                reaction_emoji=str(item["emoji"]),
            ))

        comments: dict[str, Any] = {}

        def add_comment(item: dict[str, Any], *, historical: bool) -> None:
            slug = str(item["article"])
            article = articles[slug]
            version_label = str(item.get("source_version") or current_version_label) if historical else current_version_label
            version = versions.get((slug, version_label))
            if version is None:
                raise SeedError(f"Missing article version {version_label!r} for {slug!r}")

            baseline = canonical_minimd_by_block(indiff, version.content_blocks)
            patch_payload = None
            anchor_payload = None
            proposal_text = str(item.get("text") or "").strip()

            if item.get("change"):
                patch_payload, anchor_payload = build_patch_v2(
                    indiff,
                    baseline,
                    item["change"],
                    part_id=f"p_{item['change']['block']}",
                )
                proposal_text = patch_payload["parts"][0]["new_text"]

            raw_mode = str(item.get("mode") or "change")
            comment_mode = raw_mode if raw_mode in ALLOWED_COMMENT_MODES else "change"
            if historical:
                status = "archiviert"
                lifecycle_status = "archived"
            else:
                state = item.get("state") if isinstance(item.get("state"), dict) else {}
                requested = str(state.get("status") or "published").strip().lower()
                status_map = {
                    "draft": ("entwurf", "draft"),
                    "review": ("review", "review"),
                    "published": ("veröffentlicht", "published"),
                }
                if requested not in status_map:
                    raise SeedError(f"Unsupported demo comment state: {requested!r}")
                status, lifecycle_status = status_map[requested]

            author_key = str(item.get("author") or default_author_key)
            if author_key not in users:
                raise SeedError(f"Unknown comment author: {author_key!r}")
            author_user = users[author_key]

            comment = Comment(
                article_id=article.id,
                version_id=version.id,
                user_id=author_user.id,
                type="standard",
                anchor=anchor_payload,
                comment_mode=comment_mode,
                structure_payload=None,
                comment_category=str(item.get("category") or "general"),
                anchor_payload=anchor_payload,
                patch_payload=patch_payload,
                patch_stats=None,
                llm_assisted=False,
                lifecycle_status=lifecycle_status,
                policy_status=str(item.get("policy_status") or "ok"),
                candidate_status=(str(item.get("candidate_status")).strip() or None) if item.get("candidate_status") is not None else None,
                candidate_reasons=item.get("candidate_reasons") if isinstance(item.get("candidate_reasons"), dict) else None,
                proposal_text=proposal_text or str(item.get("explanation") or "Kommentar"),
                explanation=item.get("explanation"),
                impact_scores=normalize_impact_scores(item.get("impact_scores")),
                sources=normalize_sources(item.get("sources")),
                status=status,
                published_at=(now if status == "veröffentlicht" else None),
                public_author_label=author_user.pseudonym,
                export_anonymized_id=f"demo-{profile}-{item['key']}",
            )
            db.add(comment)
            db.flush()

            if patch_payload:
                parts = [indiff.PersistedCommentPartInput(**part) for part in patch_payload["parts"]]
                validation = indiff.validate_exact_selection_patch_payload(
                    baseline_old_minimd_by_block=baseline,
                    comment=indiff.PersistedCommentInput(cid=comment.id, parts=parts),
                    require_baseline_hash=True,
                    require_selection=True,
                )
                if not validation.ok:
                    raise SeedError(
                        f"Persisted patch validation failed for {item['key']!r}: {validation.errors}"
                    )

            comments[item["key"]] = comment

        for item in participation.get("proposals", []):
            add_comment(item, historical=False)
        for item in participation.get("historical_proposals", []):
            add_comment(item, historical=True)
        for item in participation.get("comments", []):
            add_comment(item, historical=False)

        for item in participation.get("comment_votes", []):
            key = str(item.get("proposal") or item.get("comment"))
            comment = comments[key]
            db.add(CommentVote(
                user_id=users[item["user"]].id,
                comment_id=comment.id,
                article_id=comment.article_id,
                version_id=comment.version_id,
                main_vote=normalize_vote(item["vote"]),
                flags=None,
                status="confirmed",
                confirmed_at=now,
            ))

        for item in participation.get("comment_reactions", []):
            key = str(item.get("proposal") or item.get("comment"))
            comment = comments[key]
            db.add(CommentReaction(
                user_id=users[item["user"]].id,
                comment_id=comment.id,
                reaction_emoji=str(item["emoji"]),
            ))

        for section in ("reviews", "historical_reviews"):
            for item in participation.get(section, []):
                key = str(item.get("proposal") or item.get("comment"))
                reviewer_key = str(item["reviewer"])
                reviewer = users[reviewer_key]
                recommendation = normalize_recommendation(item["recommendation"])
                normalized = review_code.normalize_review_payload(
                    {
                        "decision": recommendation,
                        "sliders": item.get("sliders") or {},
                        "compliance": item.get("compliance") or {
                            "no_personal_data": True,
                            "copyright_ok": True,
                            "no_illegal_content": True,
                        },
                        "feedback_tags": item.get("feedback_tags") or [],
                        "report_triggered": bool(item.get("report_triggered", False)),
                    },
                    context=str(item.get("context") or review_code.CONTEXT_GATE),
                )
                db.add(Review(
                    comment_id=comments[key].id,
                    reviewer_id=reviewer.id,
                    checks=None,
                    recommendation=recommendation,
                    structured_checks=review_code.normalized_review_to_structured_checks(normalized),
                    reviewer_trust_at_time=reviewer.trust_level,
                    review_note=item.get("note"),
                    visible_to_public=bool(item.get("visible_to_public", False)),
                ))

        db.commit()

        counts = {
            "users": db.query(User).count(),
            "articles": db.query(Article).count(),
            "versions": db.query(ArticleVersion).count(),
            "article_votes": db.query(ArticleVote).count(),
            "article_reactions": db.query(ArticleReaction).count(),
            "comments": db.query(Comment).count(),
            "comment_votes": db.query(CommentVote).count(),
            "comment_reactions": db.query(CommentReaction).count(),
            "reviews": db.query(Review).count(),
        }

    return counts


def write_active_profile(
    profile: str,
    demo_dir: Path,
    database: Path,
    project: dict[str, Any],
    law: dict[str, Any],
) -> Path:
    runtime_dir = ROOT / ".demo"
    runtime_dir.mkdir(exist_ok=True)
    path = runtime_dir / "active_profile.json"
    relative_demo = os.path.relpath((demo_dir / profile).resolve(), ROOT)
    relative_db = os.path.relpath(database.resolve(), ROOT)
    payload = {
        "profile": profile,
        "profile_dir": relative_demo,
        "database": relative_db,
        "project_name": str((project.get("project") or {}).get("name") or ""),
        "document_title": str((law.get("law") or {}).get("title") or ""),
        "document_version": str((law.get("law") or {}).get("version") or ""),
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def main() -> int:
    args = parse_args()
    demo_dir = args.demo_dir.expanduser().resolve()

    try:
        project, law, participation = load_profile(demo_dir, args.profile)
        validate_profile(project, law, participation)
        database = configure_database(args.database)
        runtime = import_runtime()
        article_payloads = build_article_payloads(runtime["indiff"], law, participation)
        if args.reset:
            reset_database_file(database, runtime["engine"])
        counts = seed_database(
            runtime,
            law,
            participation,
            article_payloads,
            profile=args.profile,
        )
        active_profile = write_active_profile(args.profile, demo_dir, database, project, law)
    except SeedError as exc:
        print(f"seed.py: {exc}", file=sys.stderr)
        return 2

    print(f"Seeded profile: {args.profile}")
    print(f"Database: {database}")
    print(f"Active profile: {active_profile}")
    for key, value in counts.items():
        print(f"{key}: {value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
