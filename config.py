"""KlimaGG-Web — central application configuration.
Version: v2.0.1

`Settings` is loaded from environment variables and an optional project-local `.env` file via pydantic-settings. Technical configuration identifiers and documentation use English. German strings remain where they are user-facing labels or established MiniMD/domain contracts.
"""

from typing import Any, Dict, List, Optional
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

__version__ = "2.0.1"


class Settings(BaseSettings):
    # pydantic-settings reads environment variables and additionally supports
    # a project-local `.env` file for local development.
    model_config = SettingsConfigDict(
        env_file=str(Path(__file__).resolve().parent / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",  # Unknown `.env` keys must not break startup
    )

    PROJECT_NAME: str = "Open Draft"

    # Fallback for the public build label when the local Git checkout
    # cannot provide a tag/commit. Normally `app.py` derives the local Git ref.
    APP_VERSION: str = "unknown"

    # Default: local SQLite file; override with `DATABASE_URL` in the environment.
    DATABASE_URL: str = "sqlite:///./klimagg.db"

    # ------------------------------------------------------------------
    # SQLite: stable single-file production mode
    # ------------------------------------------------------------------
    # WAL allows concurrent readers while one writer is active. This fits the
    # minimal SQLite deployment, but does not eliminate unnecessary hot-path writes;
    # those are handled separately.
    SQLITE_JOURNAL_MODE: str = "WAL"
    SQLITE_SYNCHRONOUS: str = "NORMAL"
    SQLITE_BUSY_TIMEOUT_MS: int = 10000
    SQLITE_TEMP_STORE: str = "MEMORY"
    SQLITE_CACHE_SIZE_KB: int = 64000
    SQLITE_ENABLE_FOREIGN_KEYS: bool = True
    SQLITE_SERIALIZE_WRITES: bool = True

    JWT_SECRET_KEY: str = "change-me-in-prod"
    JWT_ALGORITHM: str = "HS256"

    # ------------------------------------------------------------------
    # Authentication / security defaults
    # ------------------------------------------------------------------
    # Magic-link token: short-lived and single-use.
    # Default lifetime: 30 minutes; configurable through the environment.
    MAGIC_LINK_TOKEN_LIFETIME_MINUTES: int = 30

    # Bearer JWT lifetime for authenticated API requests.
    # Configure through `ACCESS_TOKEN_EXPIRE_MINUTES`.
    # Recommended upper bound: 24 hours.
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 48

    # ------------------------------------------------------------------
    # Account deletion
    # ------------------------------------------------------------------
    # Grace period between scheduling account deletion and anonymization.
    #
    #
    ACCOUNT_DELETE_GRACE_HOURS: int = 24

    # In-process housekeeping loop; no external worker is required.
    # It processes due deletions at a fixed interval.
    # With multiple Uvicorn workers, one loop runs per process.
    ACCOUNT_DELETE_HOUSEKEEPING_INTERVAL_SECONDS: int = 300
    ACCOUNT_DELETE_HOUSEKEEPING_ENABLED: bool = True
    
    # ------------------------------------------------------------------
    # Admin: web upgrade from GitHub tags/releases
    # ------------------------------------------------------------------
    # Optionally configure owner/repository explicitly. When empty, the backend
    # attempts to parse `remote.origin.url` from the local Git repository.
    GITHUB_REPO_OWNER: str = ""
    GITHUB_REPO_NAME: str = ""

    # Optional read-only GitHub token for higher rate limits or private repositories.
    # Store it server-side only; never expose it to the frontend.
    GITHUB_API_TOKEN: str = ""

    # Cache TTL for GitHub tag/release discovery in the admin panel.
    ADMIN_GITHUB_CACHE_SECONDS: int = 90

    # ------------------------------------------------------------------
    # MiniMD public/private boundary
    # ------------------------------------------------------------------
    # Unauthenticated MiniMD access is restricted to published versions.
    # This publication boundary is always enforced.

    # Compatibility heuristic for published version labels.
    # Example: `v2025-12-01` matches; `draft-xyz` does not.
    PUBLISHED_VERSION_LABEL_REGEX: str = r"^v\d{4}-\d{2}-\d{2}$"

    # ------------------------------------------------------------------
    # Comment display
    # ------------------------------------------------------------------
    # The frontend exposes one comment-visibility switch.
    # It affects currently public comments only;
    # historical/integrated/archived comments are not a separate public layer.
    # First-time default is off; subsequent state is stored in browser localStorage.
    #
    COMMENTS_DEFAULT_SHOW: bool = False
    DIFF_CARD_CONTEXT_MAX_CHARS: int = 25
    DIFF_CARD_CONTEXT_ELLIPSIS: str = "..."

    # Public comment layer
    # - Badges and manual inline-diff selection apply only to public comments.
    # - Next-Draft is a secondary public layer and uses the same publication boundary.
    COMMENT_PUBLIC_STATUSES: List[str] = ["veröffentlicht"]
    COMMENT_NEXT_DRAFT_STATUSES: List[str] = ["veröffentlicht"]

    # ------------------------------------------------------------------
    # Article inline diff: coarse mode for large changes
    # ------------------------------------------------------------------
    # Large proposals may be rendered as a coarse replacement instead of many
    # local insert/delete fragments.
    # This keeps large rewrites readable and structurally stable.
    ARTICLE_INLINE_COARSE_DIFF: Dict[str, Any] = {
        "min_old_len": 700,
        "min_new_len": 700,
        "hard_selected_ratio": 0.60,
        "soft_selected_ratio": 0.35,
        "min_total_len": 1800,
        "low_similarity": 0.78,
    }

    # ------------------------------------------------------------------
    # Review-v2 / trust policy (no schema migration)
    # ------------------------------------------------------------------
    TRUST_ENABLED: bool = True
    TRUST_MIN: int = 0
    TRUST_MAX: int = 25
    TRUST_START: int = 0
    TRUST_ADMIN_VALUE: int = 25

    TRUST_USE_FOR_REVIEWS: bool = True
    TRUST_USE_FOR_RELEASE: bool = True
    TRUST_USE_FOR_LIMITS: bool = True
    TRUST_USE_FOR_VOTE_PROTECTION: bool = True

    TRUST_RELEASE_WEIGHT_MAX: float = 3.0
    TRUST_REVIEW_RISK_WEIGHT_MAX: float = 2.0
    TRUST_REVIEW_WEIGHT_ONLY_IF_RISK: bool = True
    TRUST_RELEASE_WEIGHT_ONLY_FOR_DERIVED_SIGNALS: bool = True
    TRUST_VOTE_PROTECTION_TOUCHES_PUBLIC_COUNTS: bool = False

    TRUST_SHORT_NOTE_MIN: int = 6
    TRUST_LIMIT_TIER_NORMAL_MIN: int = 3
    TRUST_LIMIT_TIER_HIGH_MIN: int = 8
    TRUST_COMMENT_LIMIT_LOW: int = 1
    TRUST_COMMENT_LIMIT_NORMAL: int = 3
    TRUST_COMMENT_LIMIT_HIGH: int = 6
    TRUST_REVIEW_LIMIT_LOW: int = 3
    TRUST_REVIEW_LIMIT_NORMAL: int = 10
    TRUST_REVIEW_LIMIT_HIGH: int = 25

    TRUST_EVENT_VOTES_PER_DAY_THRESHOLD: int = 10
    TRUST_EVENT_VOTES_PER_DAY_DELTA: int = 1
    TRUST_EVENT_COMMENT_PUBLISHED_DELTA: int = 1
    TRUST_EVENT_COMMENT_INTEGRATED_DELTA: int = 1
    TRUST_EVENT_OWN_COMMENT_APPROVAL_THRESHOLD: int = 10
    TRUST_EVENT_OWN_COMMENT_APPROVAL_DELTA: int = 1
    TRUST_EVENT_GOLD_REVIEW_MATCH_DELTA: int = 1
    TRUST_EVENT_INACTIVITY_DAYS: int = 7
    TRUST_EVENT_INACTIVITY_DELTA: int = -1
    TRUST_EVENT_CONFIRMED_REPORT_DELTA: int = -25
    TRUST_CONFIRMED_REPORT_RESET_TO: Optional[int] = 0

    # Review gate v3:
    # - The decision count itself is not trust-weighted.
    # - Trust-weighted review scores are admin diagnostics only.
    # - A single review must never publish a comment.
    REVIEW_BASE_REQUIRED_COUNT_SMALL: int = 3
    REVIEW_BASE_REQUIRED_COUNT_MEDIUM: int = 6
    REVIEW_BASE_REQUIRED_COUNT_LARGE: int = 9

    REVIEW_REQUIRED_POSITIVE_SMALL: int = 3
    REVIEW_REQUIRED_POSITIVE_MEDIUM: int = 6
    REVIEW_REQUIRED_POSITIVE_LARGE: int = 9

    REVIEW_MAX_COUNT_SMALL: int = 9
    REVIEW_MAX_COUNT_MEDIUM: int = 12
    REVIEW_MAX_COUNT_LARGE: int = 15

    REVIEW_AUTO_BLOCK_NEGATIVE_MIN: int = 3
    REVIEW_NEGATIVE_HARD_REJECT_COUNT: int = 3
    REVIEW_EXTRA_POSITIVE_PER_NEGATIVE: int = 2
    REVIEW_HARD_BLOCK_NEGATIVE_PERCENT: float = 0.67
    REVIEW_ESCALATE_NEGATIVE_PERCENT: float = 0.50
    REVIEW_SUSPEND_ON_NEGATIVE_THRESHOLD: bool = True
    REVIEW_NOTE_ENABLED: bool = False
    REVIEW_REVISE_COUNT_AUTHOR_REVISION_MIN: int = 2

    REVIEW_SLIDER_RANGE_EXTRA_THRESHOLD: int = 4
    REVIEW_SLIDER_AVG_RANGE_EXTRA_THRESHOLD: float = 3.0
    REVIEW_EXTRA_REVIEWS_FOR_HIGH_SLIDER_SPREAD: int = 2
    REVIEW_SLIDER_EXTREME_RANGE_THRESHOLD: int = 5
    REVIEW_SLIDER_EXTREME_COUNT_FOR_MODERATION: int = 2

    REVIEW_TRUST_SCORE_WEIGHT_MIN: float = 0.5
    REVIEW_TRUST_SCORE_WEIGHT_MAX: float = 2.0
    REVIEW_TRUST_SCORE_STRONG_POSITIVE_MIN: float = 6.0
    REVIEW_TRUST_SCORE_STRONG_NEGATIVE_MAX: float = -3.0

    # Published comments are reviewable as test/calibration cases only when
    # admin/release logic explicitly places them in the candidate pool.
    # Phase 1 uses `candidate_status` without a schema migration.
    REVIEW_TEST_POOL_CANDIDATE_STATUSES: List[str] = ["review_test", "review_pool"]

    SUBMIT_REVIEW_GATE_ENABLED: bool = True
    SUBMIT_REQUIRED_REVIEWS_SMALL: int = 3
    SUBMIT_REQUIRED_REVIEWS_MEDIUM: int = 6
    SUBMIT_REQUIRED_REVIEWS_LARGE: int = 9
    SUBMIT_MAX_COLLECTIBLE_REVIEWS: int = 15
    # Cooldown between the last edit and review submission.
    # The intent is to encourage a final review of the draft before submission.
    SUBMIT_REVIEW_COOLDOWN_SECONDS: int = 300

    COMMENT_CHANGE_SCOPE_SMALL_MAX_WORDS: int = 10
    COMMENT_CHANGE_SCOPE_MEDIUM_MAX_WORDS: int = 50

    # ------------------------------------------------------------------
    # Admin v2: gold review / release / delay / cockpit
    # ------------------------------------------------------------------
    GOLD_REVIEW_ENABLED: bool = True
    GOLD_REVIEW_MARK_MIN_TRUST: int = 25
    GOLD_REVIEW_MIN_VOTES: int = 5
    GOLD_REVIEW_MIN_APPROVAL_RATIO: float = 0.75
    GOLD_REVIEW_MAX_SLIDER_VARIATION: float = 1.0
    GOLD_REVIEW_REQUIRE_PUBLISHED: bool = True

    REVIEW_DELAY_ENABLED: bool = True
    REVIEW_DELAY_MAX_CYCLES_PER_COMMENT: int = 1
    REVIEW_DELAY_REQUIRE_ARTICLE_WITHOUT_OTHER_CHANGES: bool = True
    ADMIN_WARN_IF_NO_REVIEW_QUEUE_ITEMS: bool = True

    RELEASE_FREEZE_ENABLED: bool = True
    RELEASE_FREEZE_BLOCK_NEW_COMMENTS_FOR_CURRENT_CYCLE: bool = True
    RELEASE_FREEZE_KEEP_EXISTING_REVIEW_VISIBLE: bool = True
    RELEASE_MARK_STALE_BASE_COMMENTS: bool = True
    RELEASE_AUTO_ARCHIVE_STALE_COMMENTS: bool = False
    RELEASE_SNAPSHOT_EXPORT_ENABLED: bool = True
    RELEASE_KEEP_LAST_VERSION_ARCHIVE: bool = True

    ADMIN_ESCALATION_QUEUE_ENABLED: bool = True
    ADMIN_COMMENT_TABLE_SHOW_GOLD_REVIEW_FLAGS: bool = True
    ADMIN_USE_NAMED_WORKTAB: bool = True
    ADMIN_WORKTAB_NAME: str = "main"

    ADMIN_ALLOW_SELF_REVIEW: bool = True
    ADMIN_ALLOW_SELF_VOTE: bool = True

    # ------------------------------------------------------------------
    # Exports / LLM support
    # ------------------------------------------------------------------
    # Filename tags used for public snapshot artifacts.
    EXPORT_RELEASE_FILE_TAG: str = "document"
    EXPORT_MONTHLY_FILE_TAG: str = "web"

    LLM_API_DIRNAME: str = "llm-api"
    LLM_FILENAME_PREFIX: str = "Project"
    LLM_LAW_CONTEXT_LABEL: str = "Dokumentkontext"
    LLM_ARTICLE_CONTEXT_LABEL: str = "Artikelkontext"
    LLM_LAW_CONTEXT_MAX_AGE_HOURS: int = 24
    LLM_ARTICLE_CONTEXT_MAX_AGE_HOURS: int = 1
    LLM_ARTICLE_COMMENT_STATUSES: List[str] = ["veröffentlicht"]

    # Mode-specific LLM instruction files. A comment mode is enabled in the frontend
    # only when its `source_filename` exists in the `llm-api` directory.
    LLM_INSTRUCTIONS_BY_MODE: Dict[str, Dict[str, str]] = {
        "change": {
            "source_filename": "LLM-Instructions-Change.md",
            "download_label": "LLM-Anweisungen_Aenderung",
            "ui_label": "Normale Kommentarentwürfe",
        },
        "new_article": {
            "source_filename": "LLM-Instructions-New-Article.md",
            "download_label": "LLM-Anweisungen_Neuer-Artikel",
            "ui_label": "Neue Artikel-Entwürfe",
        },
    }

    # ------------------------------------------------------------------
    # MiniMD / HTML text contract
    # ------------------------------------------------------------------
    # This contract is authoritative for MiniMD/HTML text-node conversion.
    # `indiff.py` must not hide additional implicit rules behind configuration flags.
    #
    #
    # MiniMD text may contain literal characters directly.
    # HTML text nodes are escaped according to the explicit export rules.
    # Import only decodes the entity forms allowed below.
    #
    # Design intent:
    # - Keep text semantics separate from structural/tag normalization.
    # - Keep NBSP handling explicit.
    # - Whitespace-sensitive scopes such as `<pre>` may define targeted exceptions.
    #
    MINIMD_TEXT_CONTRACT: Dict[str, Any] = {
        "html_export_escape": {
            "amp": True,
            "lt": True,
            "gt": True,
        },
        "html_import_unescape_entities": {
            "named": True,
            "decimal_numeric": True,
            "hex_numeric": True,
        },
        "nbsp_policy": {
            "normalize_unicode_nbsp_to_space": True,
            "normalize_nbsp_entity_to_space": True,
            "skip_in_scopes": ["pre"],
        },
        "whitespace_policy": {
            "default_space": " ",
            "replace_tabs_with_spaces": True,
            "tab_width": 4,
            "trim_trailing_spaces_on_import": True,
        },
    }

    # ------------------------------------------------------------------
    # Public URLs / magic-link authentication flow
    # ------------------------------------------------------------------
    # Public site base URL used for links in e-mails.
    # Example:
    #   - https://example.org
    # When empty, the backend derives it from `Request.base_url`.
    # Configure it explicitly in production behind a reverse proxy.
    PUBLIC_BASE_URL: str = ""

    # Enable magic-link e-mail delivery.
    # When enabled, SMTP and at least one login/info sender address must be
    # configured. Local development may instead use
    # `AUTH_EXPOSE_LOGIN_URL=True`.
    AUTH_MAGIC_LINK_EMAIL_ENABLED: bool = True

    # Browser page linked from the e-mail; it completes the API callback
    # and stores the JWT in the browser.
    AUTH_MAGIC_LINK_COMPLETE_PATH: str = "/auth/complete"

    # Development/tests: expose the direct callback `login_url` in the JSON response.
    # Keep this disabled in production.
    AUTH_EXPOSE_LOGIN_URL: bool = False

    # ------------------------------------------------------------------
    # Privacy-friendly analytics (no cookies or persistent client storage)
    # ------------------------------------------------------------------
    # Privacy-preserving reach measurement using page views and a daily visitor estimate.
    # Visitors are deduplicated per day through a salted hash of truncated IP,
    # user agent, date, and salt.
    ENABLE_ANALYTICS: bool = True

    # Privacy truncation: /24 for IPv4 and /56 for IPv6.
    ANALYTICS_IP_TRUNC_V4: int = 24
    ANALYTICS_IP_TRUNC_V6: int = 56

    # Salt for the daily visitor key; set it through the environment in production.
    ANALYTICS_SALT: str = "change-me-in-prod"

    # Reverse proxy: optionally trust `X-Forwarded-For` for the client address.
    ANALYTICS_TRUST_X_FORWARDED_FOR: bool = False

    # Visitor keys are needed only for short-term daily deduplication.
    # `metrics_daily` may be retained long-term; visitor keys are periodically removed.
    ANALYTICS_VISITOR_KEYS_RETENTION_DAYS: int = 90

    # Avoid synchronous SQLite writes on normal page-view hot paths.
    # Events are buffered in-process and flushed in batches.
    ANALYTICS_BUFFER_ENABLED: bool = True
    ANALYTICS_BUFFER_FLUSH_INTERVAL_SECONDS: int = 30
    ANALYTICS_BUFFER_MAX_EVENTS: int = 250

    # Request prefixes/extensions excluded from page-view counting.
    ANALYTICS_EXCLUDE_PREFIXES: List[str] = ["/api/", "/static/", "/versions/"]
    ANALYTICS_EXCLUDE_EXTS: List[str] = [
        ".css", ".js", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".ico", ".webp", ".map", ".txt"
    ]

    # ------------------------------------------------------------------
    # Homepage project statistics
    # ------------------------------------------------------------------
    # `/api/public/project-stats` provides totals, period deltas, sentiment,
    # and daily series consumed by the homepage. Configuration below controls
    # which existing metrics are shown; German label values are user-facing UI
    # content and intentionally remain German.
    #
    # Series tokens use: `metric:<key>`, `main:<emoji>`, or `flag:<emoji>`.
    PUBLIC_STATS_DAYS_DEFAULT: int = 30

    # Cache the public project-statistics hot path to avoid repeated live aggregation.
    PUBLIC_STATS_CACHE_SECONDS: int = 60

    # Short cache for the public article hot path.
    PUBLIC_ARTICLES_CACHE_SECONDS: int = 30

    # Admin and merge-preview runtime caches/limits.
    ADMIN_RUNTIME_CACHE_SECONDS: int = 30
    NEXT_DRAFT_LAYER_CACHE_SECONDS: int = 600
    MERGE_PREVIEW_MAX_CONCURRENT: int = 1
    MERGE_PREVIEW_MAX_COMMENTS: int = 60
    MERGE_PREVIEW_MAX_TOTAL_CHARS: int = 750000
    MERGE_PREVIEW_TIME_BUDGET_SECONDS: float = 15.0

    # Show the sentiment donut.
    PUBLIC_STATS_SHOW_MOOD: bool = False

    # KPI order and user-facing labels. Keys must exist in `totals`/`delta`.
    PUBLIC_STATS_KPI_KEYS: List[str] = ["votes", "comments", "reviews"]
    PUBLIC_STATS_KPI_LABELS: Dict[str, str] = {
        "votes": "Stimmen",
        "users": "Nutzer",
        "comments": "Vorschläge",
        "reviews": "Reviews",
    }

    # Toggle total and period-delta values.
    PUBLIC_STATS_KPI_SHOW_TOTAL: bool = True
    PUBLIC_STATS_KPI_SHOW_DELTA: bool = True

    # Additional graph metric series. Keys must exist in `series.metrics`.
    PUBLIC_STATS_GRAPH_METRIC_KEYS: List[str] = ["votes_total", "users", "comments", "reviews"]
    PUBLIC_STATS_GRAPH_METRIC_LABELS: Dict[str, str] = {
        # Labels omit "per day" because the chart is already a daily series.
        "votes_total": "Stimmen",
        "users": "Nutzer",
        "comments": "Kommentare",
        "reviews": "Reviews",
        "pageviews": "Pageviews",
        "visitors_est": "Visitors",
    }

    # Expose main-vote emoji series as selectable graph series.
    PUBLIC_STATS_GRAPH_INCLUDE_MAIN_VOTES: bool = True
    # Expose reaction/flag series as selectable graph series.
    PUBLIC_STATS_GRAPH_INCLUDE_FLAGS: bool = True

    # Default graph selection; token format: `metric:<key>`, `main:<emoji>`, `flag:<emoji>`.
    PUBLIC_STATS_GRAPH_DEFAULT_SELECTED: List[str] = ["metric:votes_total"]
 
    # ------------------------------------------------------------------
    # Security headers and rate limits
    # ------------------------------------------------------------------
    # Enable standard security headers on all responses.
    ENABLE_SECURITY_HEADERS: bool = True

    # Content Security Policy:
    # - own resources only (`self`),
    # - images and fonts may additionally use `data:` URIs,
    # - no external CDNs.
    CSP_DEFAULT: str = (
        "default-src 'self'; "
        "img-src 'self' data:; "
        "script-src 'self'; "
        "style-src 'self' 'unsafe-inline'; "
        "font-src 'self' data:; "
        "connect-src 'self'; "
        "frame-ancestors 'none'; "
        "base-uri 'self'"
    )

    X_FRAME_OPTIONS: str = "DENY"
    REFERRER_POLICY: str = "no-referrer"
    X_CONTENT_TYPE_OPTIONS: str = "nosniff"

    # Hourly rate limits for magic-link requests.
    MAGIC_LINK_MAX_PER_EMAIL_PER_HOUR: int = 3
    MAGIC_LINK_MAX_PER_IP_PER_HOUR: int = 10

    # Disposable-mail detection:
    # - `DISPOSABLE_EMAIL_DOMAINS`: simple provider deny-list.
    # - `DISPOSABLE_EMAIL_POLICY`:
    #     "reject" -> reject requests from listed domains.
    #     "flag_low_trust" -> reserved for a future lower-trust policy.
    DISPOSABLE_EMAIL_DOMAINS: List[str] = [
        "mailinator.com",
        "10minutemail.com",
        "10minutemail.net",
        "yopmail.com",
        "trashmail.com",
        "guerrillamail.com",
    ]
    DISPOSABLE_EMAIL_POLICY: str = "reject"

    # ------------------------------------------------------------------
    # Voting: delayed confirmation and spike detection
    # ------------------------------------------------------------------
    # New votes start as `pending` and are promoted to `confirmed` after the configured
    # delay when the article is not frozen.
    #
    # A value <= 0 confirms immediately.
    VOTE_CONFIRM_DELAY_MINUTES: int = 10

    # Optional auto-confirm support for public homepage statistics.
    # Due, non-suspicious pending votes may be confirmed so public statistics do not
    # remain permanently behind. Frozen articles remain excluded.
    #
    VOTE_AUTO_CONFIRM_PUBLIC_STATS_ENABLED: bool = True

    # Write-load protection: check at most once per configured interval per process.
    # A value <= 0 checks on every statistics request.
    VOTE_AUTO_CONFIRM_PUBLIC_STATS_INTERVAL_SECONDS: int = 60

    # Limit the number of writes per confirmation batch.
    VOTE_AUTO_CONFIRM_PUBLIC_STATS_BATCH_LIMIT: int = 500

    # Prefer background confirmation instead of writing during the public statistics GET.
    #
    VOTE_AUTO_CONFIRM_PUBLIC_STATS_BACKGROUND_ENABLED: bool = True

 
    # ------------------------------------------------------------------
    # Comment voting
    # ------------------------------------------------------------------
    MAX_COMMENT_VOTES_PER_USER_PER_24H: int = 40000

    # Next-Draft eligibility thresholds by change-size bucket.
    # - Word count uses `proposal_text + explanation`.
    # - Neutral votes count in the denominator and therefore reduce approval percentage.
    COMMENT_NEXTDRAFT_WORDS_SMALL_MAX: int = 40
    COMMENT_NEXTDRAFT_WORDS_MEDIUM_MAX: int = 120
    COMMENT_NEXTDRAFT_MIN_VOTES_SMALL: int = 10
    COMMENT_NEXTDRAFT_MIN_VOTES_MEDIUM: int = 10
    COMMENT_NEXTDRAFT_MIN_VOTES_LARGE: int = 10
    COMMENT_NEXTDRAFT_MIN_APPROVAL_PERCENT_SMALL: float = 90.0
    COMMENT_NEXTDRAFT_MIN_APPROVAL_PERCENT_MEDIUM: float = 90.0
    COMMENT_NEXTDRAFT_MIN_APPROVAL_PERCENT_LARGE: float = 90.0

    # ------------------------------------------------------------------
    # Next-Draft: Release cadence / freeze window (public parameters)
    # ------------------------------------------------------------------
    # Release cadence in days (used to translate "age in cycles" from timestamps).
    COMMENT_RELEASE_CYCLE_DAYS: int = 14
    # Auto-archive/reject comments after N release cycles if they remain low-signal.
    COMMENT_NEXTDRAFT_MAX_RELEASE_CYCLE_AGE: int = 6
    # Freeze window before release cut: comments may target next-draft baseline version.
    COMMENT_RELEASE_FREEZE_DAYS: int = 3

    # ------------------------------------------------------------------
    # Next-Draft: Negative votes (public parameters)
    # ------------------------------------------------------------------
    # If True, negative votes reduce eligibility and can block Next-Draft inclusion.
    COMMENT_NEXTDRAFT_INCLUDE_NEGATIVE_VOTES: bool = True
    # Disapproval cap: if negative share exceeds this, comment is not eligible
    # for automatic Next-Draft integration (even if approval is high).
    # Percent is computed over total votes (pos + neutral + neg).
    COMMENT_NEXTDRAFT_MAX_DISAPPROVAL_PERCENT_SMALL: float = 10.0
    COMMENT_NEXTDRAFT_MAX_DISAPPROVAL_PERCENT_MEDIUM: float = 10.0
    COMMENT_NEXTDRAFT_MAX_DISAPPROVAL_PERCENT_LARGE: float = 10.0

    # ------------------------------------------------------------------
    # Policy-Inputs (config-driven foundation for weighted eligibility/order)
    # ------------------------------------------------------------------
    # Weighted minimum support / vote thresholds (bucketed).
    # Tests may lower these values through `.env`.
    COMMENT_POLICY_WEIGHTED_MIN_SUPPORT_SMALL: int = 10
    COMMENT_POLICY_WEIGHTED_MIN_SUPPORT_MEDIUM: int = 10
    COMMENT_POLICY_WEIGHTED_MIN_SUPPORT_LARGE: int = 10
    # Weighted approval threshold in percent.
    COMMENT_POLICY_WEIGHTED_APPROVAL_THRESHOLD_SMALL: float = 90.0
    COMMENT_POLICY_WEIGHTED_APPROVAL_THRESHOLD_MEDIUM: float = 90.0
    COMMENT_POLICY_WEIGHTED_APPROVAL_THRESHOLD_LARGE: float = 90.0
    # Weighted disapproval cap in percent.
    COMMENT_POLICY_WEIGHTED_DISAPPROVAL_CAP_SMALL: float = 10.0
    COMMENT_POLICY_WEIGHTED_DISAPPROVAL_CAP_MEDIUM: float = 10.0
    COMMENT_POLICY_WEIGHTED_DISAPPROVAL_CAP_LARGE: float = 10.0

    # Precedence policy toggles for future merge ordering behavior.
    # These settings are read by current code; behavior remains unchanged here.
    MERGE_POLICY_PRECEDENCE_ENABLED: bool = True
    MERGE_POLICY_PRECEDENCE_MODE: str = "global_comment_precedence"

    # Per-user rate limits for write actions in a 24-hour window.
    MAX_VOTES_PER_USER_PER_24H: int = 200
    MAX_COMMENTS_PER_USER_PER_24H: int = 50
    MAX_REVIEWS_PER_USER_PER_24H: int = 100
    # Local development may optionally disable the comment quota.
    # The bypass is coupled to `AUTH_EXPOSE_LOGIN_URL` so it cannot silently
    # become active in normal production.
    KGG_DEV_DISABLE_COMMENT_QUOTA: bool = False

    # Vote spike detection and automatic freeze:
    # - `VOTE_SPIKE_WINDOW_MINUTES`: observation window.
    # - `VOTE_SPIKE_THRESHOLD_PER_ARTICLE`: number of votes within the window
    #   that triggers `vote_freeze` for an article.
    VOTE_SPIKE_WINDOW_MINUTES: int = 10
    VOTE_SPIKE_THRESHOLD_PER_ARTICLE: int = 100
    VOTE_SPIKE_LOG_USER_EVENTS: bool = True

    # ------------------------------------------------------------------
    # MiniMD vNext — canonical syntax contract
    # ------------------------------------------------------------------
    # Goals:
    # - explicit, reduced, safe authoring syntax;
    # - explicit canonical HTML target subset;
    # - no new implicit legacy comment-markup profiles;
    # - stable basis for HTML/MiniMD transformation and anchor mapping.

    # Canonical block order for a complete article MiniMD document.
    MINIMD_BLOCKS_ORDER: List[str] = [
        "meta",
        "kurzinfo",
        "story",
        "einleitung",
        "juristisch",
        "juristisch2",
        "anmerkung",
    ]

    # Comment-v2: `meta` is a regular MiniMD block.
    # Per-line syntax: `$ Label: $ value`.
    # The label is structural MiniMD syntax; the value is proposal text.
    # This list is the single authoritative whitelist.
    # `indiff.py` must not hard-code project-specific metadata labels.
    MINIMD_META_FIELDS: List[str] = [
        "Artikel-Kennung",
        "Artikel-Titel",
        "Artikel-Kurztitel",
        "Artikel im Inhaltsverzeichnis",
        "Einfügen nach folgender Artikel-Kennung",
    ]
    MINIMD_META_LINE_RE: str = r"^\$ (?P<label>[^$:\n]+): \$ ?(?P<value>.*)$"

    # UX only: show `meta` in the comment editor from the configured trust level.
    # This is not a security boundary; direct metadata edits remain proposals.
    COMMENT_META_SHOW_IN_EDITOR_MIN_TRUST: int = 2

    # Structural comment-v2 proposals require a high trust level.
    #
    COMMENT_NEW_ARTICLE_MIN_TRUST: int = 20
    COMMENT_DELETE_ARTICLE_MIN_TRUST: int = 20

    # Title/block sentinels
    MINIMD_SENTINEL_PREFIX: str = "###"
    MINIMD_TITLE_TEMPLATE: str = "### Artikel-Titel: {title} ###"
    MINIMD_TITLE_TOC: str = "### Artikel-Kurz: {toc} ###"
    MINIMD_BLOCK_START_TEMPLATE: str = "### start: {name} ###"
    MINIMD_BLOCK_END_TEMPLATE: str = "### end: {name} ###"

    # Additional structural scopes inside block content.
    MINIMD_STRUCTURAL_SCOPES: List[str] = [
        "details",
        "storybox",
        "pre",
        "table",
    ]

    # Inline rules
    MINIMD_INLINE_RULES: Dict[str, Dict[str, str]] = {
        "bold": {
            "syntax": "**Text**",
            "html_open": "<b>",
            "html_close": "</b>",
        },
        "italic": {
            "syntax": "*Text*",
            "html_open": "<i>",
            "html_close": "</i>",
        },
        "code": {
            "syntax": "`code`",
            "html_open": "<code>",
            "html_close": "</code>",
        },
        "link": {
            "syntax": "[Label](https://example.org)",
            "html_open": "<a href=\"...\">",
            "html_close": "</a>",
        },
        "sub": {
            "syntax": "~X~",
            "html_open": "<sub>",
            "html_close": "</sub>",
        },
    }

    # Line/paragraph rules
    MINIMD_LINE_RULES: Dict[str, str] = {
        "newline": "\\n innerhalb eines Blockinhalts bedeutet genau einen sichtbaren Zeilenumbruch (<br>)",
        "blank_line": "eine Leerzeile trennt Absätze / Blockabschnitte",
    }

    # List rules (authoritative)
    MINIMD_LIST_CONTRACT: Dict[str, Any] = {
        "max_depth": 2,
        "level_rules": {
            "level1": {
                "ul_prefix": "- ",
                "ol_prefix_pattern": r"\d+\.\s+",
                "leading_spaces_exact": 0,
            },
            "level2": {
                "ul_prefix": "  - ",
                "ol_prefix_pattern": r"  \d+\.\s+",
                "leading_spaces_exact": 2,
            },
        },
        "continuation_rules": {
            "level1_prefix": ":: ",
            "level2_prefix": "  :: ",
            "same_list_item_only": True,
            "purpose": "Fortsetzung desselben Listeneintrags nach Unterliste/Leerzeile",
        },
        "ordered_number_is_semantic": False,
        "require_space_after_token": True,
        "canonical_examples": {
            "ul_level_1": "- Punkt",
            "ol_level_1": "1. Punkt",
            "ul_level_2": "  - Punkt",
            "ol_level_2": "  1. Punkt",
            "continuation_level_1": ":: Fortsetzung desselben Level-1-Listeneintrags",
            "continuation_level_2": "  :: Fortsetzung desselben Level-2-Listeneintrags",
        },
    }

    # Structural scope rules inside block content.
    MINIMD_SCOPE_RULES: Dict[str, Dict[str, Any]] = {
        "details": {
            "start": "### start: details ###",
            "end": "### end: details ###",
            "required_first_line": "summary: ...",
            "allowed_blocks": ["einleitung", "juristisch", "juristisch2", "anmerkung"],
            "html_open": "<details>",
            "html_summary_open": "<summary>",
            "html_summary_close": "</summary>",
            "html_close": "</details>",
        },
        "storybox": {
            "start": "### start: storybox ###",
            "end": "### end: storybox ###",
            "allowed_blocks": ["einleitung", "story", "juristisch", "juristisch2", "anmerkung"],
            "html_open": '<blockquote class="story">',
            "html_close": "</blockquote>",
        },
        "pre": {
            "start": "### start: pre ###",
            "end": "### end: pre ###",
            "allowed_blocks": ["einleitung", "juristisch", "juristisch2", "anmerkung"],
            "html_open": "<pre>",
            "html_close": "</pre>",
            "preserve_whitespace": True,
        },
        "table": {
            "start": "### start: table ###",
            "end": "### end: table ###",
            "allowed_blocks": ["juristisch", "juristisch2", "anmerkung"],
            "row_syntax": "| Spalte A | Spalte B |",
            "header_separator": "| --- | --- |",
            "require_header_separator": True,
            "trim_cell_text": True,
            "emit_header_separator": True,
            "html_open": "<table>",
            "html_close": "</table>",
        },
    }

    # Canonical HTML target subset after normalization.
    # Only these tags may appear in canonical block HTML.
    MINIMD_ALLOWED_HTML_TAGS: List[str] = [
        "b",
        "i",
        "code",
        "a",
        "br",
        "ul",
        "ol",
        "li",
        "details",
        "summary",
        "blockquote",
        "pre",
        "table",
        "thead",
        "tbody",
        "tr",
        "th",
        "td",
        "sub",
    ]

    # HTML normalization before canonical MiniMD conversion.
    # These rules are explicit and semantic. `indiff.py` applies them directly
    # instead of deriving hidden behavior from generic booleans.
    #
    MINIMD_HTML_NORMALIZATION_RULES: Dict[str, Any] = {
        "tag_aliases": {
            "strong": "b",
            "em": "i",
        },
        "unwrap_keep_text_tags": [
            "span",
            "u",
        ],
        "drop_empty_tags": [],
        "structural_rewrites": {
            "p": {
                "open": "",
                "close": "<br><br>",
            },
        },
        "whitespace": {
            "replace_tabs_with_spaces": True,
            "tab_width": 4,
            "remove_trailing_whitespace": True,
            "reduce_between_tag_newlines": True,
            "max_consecutive_br": 2,
        },
        "entity_handling": {
            "apply_text_contract": True,
        },
    }

    # Tag-specific split/insert rules at an HTML anchor.
    # These rules define structural behavior at the insertion point, not visual styling.
    #
    #
    # Core concepts:
    # - `structural_parent_behavior`: whether a structural container may/must be
    #   exited locally.
    # - `inline_split_behavior`: how active inline scopes are split.
    # - `badge_behavior`: badge stays in flow without inheriting text formatting.
    # - `minimd_anchor_classes`: MiniMD anchor classes relevant to the tag.
    #
    #
    # IMPORTANT ANCHOR / SPLIT / INSERT RULES
    #
    # These comments are part of the MiniMD-to-HTML implementation contract.
    # They prevent later refactors from accidentally simplifying behavior that is
    # required for links and lists.
    #
    # 1) Links (`<a>`)
    # ----------------
    # Two distinct change types must be handled:
    #
    # a) Change inside visible link text
    #    - An operational anchor may be placed inside visible link text.
    #    - The link text is split locally.
    #    - Left and right text fragments remain valid links with the same relevant
    #      attributes.
    #    - A badge remains in text flow but does not inherit an active inline/link
    #      text scope.
    #
    # b) Change to `href` or other attribute syntax
    #    - Operational anchors must never be placed inside HTML attribute syntax,
    #      especially inside `href="..."`.
    #    - Attribute changes are not modeled as an anchor inside a link.
    #    - Render them as:
    #         delete old link + insert new link.
    #    - This avoids ambiguous or syntactically fragile operations inside attributes.
    #
    #
    # 2) Lists (`<ul>`/`<ol>`/`<li>`)
    # --------------------------
    # MiniMD list anchors encode more than `<li>` text: list type and depth are also
    # structural information, so a purely local HTML strategy is not always enough.
    #
    #
    # At least two cases must be distinguished:
    #
    # a) Anchor inside an existing list-item text segment
    #    - Neither `<li>` nor the surrounding list container is split.
    #    - Only the local text/inline context inside the existing list item is split.
    #
    #
    # b) Anchor at a new MiniMD list line / list scope
    #    - The relevant list scope may be closed and rebuilt from MiniMD structure.
    #
    #    - This is required when MiniMD introduces not only new list-item content
    #      but also a new list type and/or depth.
    #
    #
    # Robustness rule:
    #    - prefer explicit split/rebuild over silently merging the wrong structure;
    #    - future healing logic may improve direct list resolution, but is not assumed.
    #
    #
    # Summary:
    #    - never anchor inside `href`/attribute syntax;
    #    - `href` change => delete old link, insert new link;
    #    - anchor in list-item text => keep list structure;
    #    - anchor at a new list line => list scope may be rebuilt.
    #
    # Rationale:
    # These rules live here because `config.py` defines the intended MiniMD/HTML
    # contracts consumed by `indiff.py`.
    MINIMD_HTML_TAG_RULES: Dict[str, Dict[str, Any]] = {
        "b": {
            "category": "inline_format",
            "inline_split_behavior": "split_local_reopen_same_tag",
            "badge_behavior": "outside_inline_format_keep_parent_flow",
            "minimd_anchor_classes": ["text_point", "range_point"],
        },
        "i": {
            "category": "inline_format",
            "inline_split_behavior": "split_local_reopen_same_tag",
            "badge_behavior": "outside_inline_format_keep_parent_flow",
            "minimd_anchor_classes": ["text_point", "range_point"],
        },
        "code": {
            "category": "inline_code",
            "inline_split_behavior": "split_local_reopen_same_tag",
            "badge_behavior": "outside_inline_format_keep_parent_flow",
            "minimd_anchor_classes": ["text_point", "range_point"],
            "preserve_text_exact": True,
        },
        "sub": {
            "category": "inline_format",
            "inline_split_behavior": "split_local_reopen_same_tag",
            "badge_behavior": "outside_inline_format_keep_parent_flow",
            "minimd_anchor_classes": ["text_point", "range_point"],
        },
        "a": {
            "category": "inline_link",
            "inline_split_behavior": "split_link_text_reopen_same_attrs",
            "badge_behavior": "outside_link_keep_parent_flow",
            "minimd_anchor_classes": ["text_point", "range_point", "link_text_point"],
            "preserve_attrs": ["href"],
            "allow_anchor_inside_attribute_syntax": False,
        },
        "br": {
            "category": "void_inline_break",
            "inline_split_behavior": "boundary_before_or_after_only",
            "badge_behavior": "allowed_at_adjacent_text_boundary",
            "minimd_anchor_classes": ["line_break_boundary"],
        },
        "ul": {
            "category": "list_container",
            "structural_parent_behavior": "stay_in_list_container",
            "inline_split_behavior": "not_applicable",
            "badge_behavior": "depends_on_minimd_anchor_class",
            "minimd_anchor_classes": ["list_item_boundary", "list_container_boundary"],
            "list_insert_rules": {
                "inside_li_text": "do_not_split_ul_or_li",
                "new_list_item_boundary": "insert_new_li_at_container_level",
            },
        },
        "ol": {
            "category": "list_container",
            "structural_parent_behavior": "stay_in_list_container",
            "inline_split_behavior": "not_applicable",
            "badge_behavior": "depends_on_minimd_anchor_class",
            "minimd_anchor_classes": ["list_item_boundary", "list_container_boundary"],
            "list_insert_rules": {
                "inside_li_text": "do_not_split_ol_or_li",
                "new_list_item_boundary": "insert_new_li_at_container_level",
            },
        },
        "li": {
            "category": "list_item",
            "structural_parent_behavior": "keep_same_li_for_text_anchors",
            "inline_split_behavior": "split_descendant_inline_only",
            "badge_behavior": "inside_li_but_outside_inline_format",
            "minimd_anchor_classes": ["list_item_text_point", "range_point"],
        },
        "details": {
            "category": "structural_scope",
            "structural_parent_behavior": "stay_in_same_scope",
            "inline_split_behavior": "not_applicable",
            "badge_behavior": "depends_on_child_context",
            "minimd_anchor_classes": ["scope_boundary", "range_point"],
        },
        "summary": {
            "category": "details_summary",
            "structural_parent_behavior": "stay_in_summary",
            "inline_split_behavior": "split_descendant_inline_only",
            "badge_behavior": "inside_summary_but_outside_inline_format",
            "minimd_anchor_classes": ["text_point", "range_point"],
        },
        "blockquote": {
            "category": "block_container",
            "structural_parent_behavior": "stay_in_same_block_container",
            "inline_split_behavior": "split_descendant_inline_only",
            "badge_behavior": "inside_blockquote_but_outside_inline_format",
            "minimd_anchor_classes": ["text_point", "range_point"],
        },
        "pre": {
            "category": "preformatted_block",
            "structural_parent_behavior": "stay_in_pre",
            "inline_split_behavior": "exact_text_boundary_only",
            "badge_behavior": "inside_pre_without_text_normalization",
            "minimd_anchor_classes": ["text_point", "range_point"],
            "preserve_whitespace_exact": True,
        },
        "table": {
            "category": "table_container",
            "structural_parent_behavior": "operate_via_cells_only",
            "inline_split_behavior": "not_applicable",
            "badge_behavior": "not_direct_anchor_target",
            "minimd_anchor_classes": ["table_cell_boundary"],
        },
        "thead": {
            "category": "table_section",
            "structural_parent_behavior": "operate_via_cells_only",
            "inline_split_behavior": "not_applicable",
            "badge_behavior": "not_direct_anchor_target",
            "minimd_anchor_classes": ["table_cell_boundary"],
        },
        "tbody": {
            "category": "table_section",
            "structural_parent_behavior": "operate_via_cells_only",
            "inline_split_behavior": "not_applicable",
            "badge_behavior": "not_direct_anchor_target",
            "minimd_anchor_classes": ["table_cell_boundary"],
        },
        "tr": {
            "category": "table_row",
            "structural_parent_behavior": "stay_in_same_row",
            "inline_split_behavior": "not_applicable",
            "badge_behavior": "operate_via_cells_only",
            "minimd_anchor_classes": ["table_cell_boundary"],
        },
        "th": {
            "category": "table_cell",
            "structural_parent_behavior": "stay_in_same_cell",
            "inline_split_behavior": "split_descendant_inline_only",
            "badge_behavior": "inside_cell_but_outside_inline_format",
            "minimd_anchor_classes": ["text_point", "range_point", "table_cell_text_point"],
        },
        "td": {
            "category": "table_cell",
            "structural_parent_behavior": "stay_in_same_cell",
            "inline_split_behavior": "split_descendant_inline_only",
            "badge_behavior": "inside_cell_but_outside_inline_format",
            "minimd_anchor_classes": ["text_point", "range_point", "table_cell_text_point"],
        },
    }

    # MiniMD-side anchor classes.
    # This classification is the semantic basis for deterministic
    # MiniMD-anchor-to-HTML-boundary mapping.
    MINIMD_ANCHOR_CLASSES: Dict[str, Dict[str, Any]] = {
        "text_point": {
            "description": "Anker zwischen zwei sichtbaren Zeichen innerhalb eines Textsegments",
            "allows_badge": True,
            "allows_insert": True,
            "allows_range_boundary": True,
        },
        "range_point": {
            "description": "Start- oder Endpunkt einer späteren Bereichsmarkierung",
            "allows_badge": True,
            "allows_insert": False,
            "allows_range_boundary": True,
        },
        "link_text_point": {
            "description": "Anker innerhalb des sichtbaren Linktexts",
            "allows_badge": True,
            "allows_insert": True,
            "requires_tag_rule": "a",
        },
        "line_break_boundary": {
            "description": "Anker direkt vor oder nach einem sichtbaren Zeilenumbruch",
            "allows_badge": True,
            "allows_insert": True,
            "allows_range_boundary": False,
        },
        "list_item_text_point": {
            "description": "Anker innerhalb des Textinhalts eines bestehenden Listeneintrags",
            "allows_badge": True,
            "allows_insert": True,
            "requires_tag_rule": "li",
        },
        "list_item_boundary": {
            "description": "Anker an der Grenze eines Listeneintrags; geeignet für neues <li>",
            "allows_badge": True,
            "allows_insert": True,
            "requires_tag_rule": "ul_or_ol",
        },
        "list_container_boundary": {
            "description": "Anker auf Listencontainer-Ebene, nicht innerhalb eines bestehenden li-Textsegments",
            "allows_badge": True,
            "allows_insert": True,
            "requires_tag_rule": "ul_or_ol",
        },
        "scope_boundary": {
            "description": "Anker an einer strukturellen Scope-Grenze",
            "allows_badge": True,
            "allows_insert": False,
            "allows_range_boundary": True,
        },
        "table_cell_boundary": {
            "description": "Anker an oder innerhalb einer Tabellenzelle, aber nicht zellenübergreifend",
            "allows_badge": True,
            "allows_insert": True,
            "allows_range_boundary": True,
        },
        "table_cell_text_point": {
            "description": "Anker innerhalb des Textinhalts einer Tabellenzelle",
            "allows_badge": True,
            "allows_insert": True,
            "allows_range_boundary": True,
        },
    }

    # Story rule:
    # - `story` is the dedicated article block.
    # - `storybox` is an embedded quote/callout scope inside other blocks.
    MINIMD_STORY_POLICY: Dict[str, str] = {
        "story_block_name": "story",
        "storybox_scope_name": "storybox",
        "storybox_html_tag": "blockquote",
        "storybox_html_class": "story",
    }

    # ------------------------------------------------------------------
    # Legal/operator information
    # ------------------------------------------------------------------
    # Instance-specific legal notice values belong in `.env`.
    LEGAL_PROVIDER_NAME: str = ""
    LEGAL_PROVIDER_ADDRESS: str = ""
    LEGAL_CONTENT_RESPONSIBLE: str = ""
    LEGAL_PRIVACY_AUTHORITY_NAME: str = ""
    LEGAL_PRIVACY_AUTHORITY_ADDRESS: str = ""
    LEGAL_NOTICE_DATE: str = ""

    # ------------------------------------------------------------------
    # Mail: SMTP transport and role addresses
    # ------------------------------------------------------------------
    # Instance-specific hosts, credentials, and mail addresses belong in ENV/`.env`.
    # The application keeps only neutral defaults and runtime fields here.
    SMTP_HOST: str = ""
    SMTP_PORT: int = 587
    SMTP_USE_TLS: bool = True  # STARTTLS
    SMTP_USERNAME: str = ""
    SMTP_PASSWORD: str = ""
    SMTP_TIMEOUT_SECONDS: int = 20

    # Role addresses. `mail.py` applies fallbacks only where the intended role
    # fallback is unambiguous.
    MAIL_INFO_ADDRESS: str = ""
    MAIL_LOGIN_ADDRESS: str = ""
    MAIL_KONTAKT_ADDRESS: str = ""
    MAIL_DATENSCHUTZ_ADDRESS: str = ""
    MAIL_ADMIN_ADDRESS: str = ""
    MAIL_NOREPLY_ADDRESS: str = ""

    # Internal recipients for forms and system notifications.
    MAIL_CONTACT_FORM_TO: str = ""
    MAIL_INTEREST_FORM_TO: str = ""
    MAIL_DATENSCHUTZ_FORM_TO: str = ""
    MAIL_ADMIN_ALERT_TO: str = ""

    # Local development: FastAPI backend and frontend on the same host.
    BACKEND_CORS_ORIGINS: List[str] = ["http://localhost:8000"]
    
settings = Settings()
