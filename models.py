"""KlimaGG-Web — ORM models.
Version: v2.0.0

Role:
- Defines the SQLAlchemy data model for authentication, articles, votes, comments, reviews, reactions, analytics, and release audit data.
- Defines the minimal database schema used by the public distribution.

Compatibility policy:
- Database table/column names and persisted enum-like values are contracts.
- Cosmetic language cleanup must not require a schema migration.
- The public repository starts from the current schema and does not carry unused historical tables.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Date,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
    Index,
    CheckConstraint,
)
from sqlalchemy.orm import relationship

from db import Base

__version__ = "2.0.0"


# ---------------------------------------------------------------------------
# 1. Users and authentication
# ---------------------------------------------------------------------------


class User(Base):
    """User account for authentication and participation.
    
    Key fields:
    - `email`: unique identifier used by magic-link authentication.
    - `pseudonym`: public display name used in the UI and comments.
    - `plz`: optional German postal code for regional analysis.
    - `trust_level`: internal trust score.
    """

    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, unique=True, index=True, nullable=False)
    pseudonym = Column(String, nullable=False)
    plz = Column(String, nullable=True)

    # Legacy v0.9 note: onboarding may raise the initial trust level.
    # A pre-onboarding value of 0 remains valid.
    trust_level = Column(Integer, nullable=False, default=0)
    is_admin = Column(Boolean, nullable=False, default=False)

    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(
        DateTime,
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )

    # Account deletion scheduling and later anonymization.
    deletion_requested_at = Column(DateTime, nullable=True)
    deletion_scheduled_for = Column(DateTime, nullable=True, index=True)
    deleted_at = Column(DateTime, nullable=True)
    is_deleted = Column(Boolean, nullable=False, default=False, index=True)

    # Relationships
    login_tokens = relationship(
        "LoginToken",
        back_populates="user",
        cascade="all, delete-orphan",
    )
    votes = relationship(
        "ArticleVote",
        back_populates="user",
        cascade="all, delete-orphan",
    )

    comment_votes = relationship(
        "CommentVote",
        back_populates="user",
        cascade="all, delete-orphan",
    )
    comments = relationship(
        "Comment",
        back_populates="user",
        cascade="all, delete-orphan",
    )
    reviews = relationship(
        "Review",
        back_populates="reviewer",
        cascade="all, delete-orphan",
        foreign_keys="Review.reviewer_id",
    )
    comment_reactions = relationship(
        "CommentReaction",
        back_populates="user",
        cascade="all, delete-orphan",
    )
    article_reactions = relationship(
        "ArticleReaction",
        back_populates="user",
        cascade="all, delete-orphan",
    )
    user_events = relationship(
        "UserEvent",
        back_populates="user",
        cascade="all, delete-orphan",
    )


class LoginToken(Base):
    """Single-use token for passwordless magic-link authentication.
    
    The token may reference an existing user or carry signup data until the authentication callback completes.
    """

    __tablename__ = "login_tokens"

    id = Column(Integer, primary_key=True, index=True)

    user_id = Column(
        Integer,
        ForeignKey("users.id"),
        nullable=True,
        index=True,
    )

    email = Column(String, nullable=False, index=True)
    token = Column(String, unique=True, nullable=False, index=True)

    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    expires_at = Column(DateTime, nullable=False)
    used_at = Column(DateTime, nullable=True)

    ip_address = Column(String, nullable=True)
    user_agent = Column(String, nullable=True)

    user = relationship(
        "User",
        back_populates="login_tokens",
        foreign_keys=[user_id],
    )


# ---------------------------------------------------------------------------
# 2. Articles and versions
# ---------------------------------------------------------------------------


class Article(Base):
    """Logical article in the managed document.
    
    `slug` is the machine-readable identifier, while `public_code` and `title` define the public article identity. `current_version` points to the currently active article version.
    """

    __tablename__ = "articles"

    id = Column(Integer, primary_key=True, index=True)
    slug = Column(String, unique=True, index=True, nullable=False)

    # Comment-v2 / public article identity:
    # - `id` remains the internal article id.
    # - `public_code` is the human-readable identifier, e.g. "Artikel 12", "Artikel 12a", "VO 3".
    # - `sort_order` is the canonical order for UI, TOC, SSR, exports, and insertion logic.
    public_code = Column(String, unique=True, index=True, nullable=False)
    sort_order = Column(Integer, nullable=False, index=True)

    title = Column(String, nullable=False)
    type = Column(String, nullable=False)

    # TOC control: only articles with `show_in_toc=True` are listed.
    show_in_toc = Column(Boolean, nullable=False, default=True)
    # TOC short titles belong to parent articles; child articles reference them via `toc_parent_id`
    # and inherit the parent `toc_title` as the group label.
    toc_title = Column(String, nullable=True)
    toc_parent_id = Column(
        Integer,
        ForeignKey("articles.id"),
        nullable=True,
        index=True,
    )

    current_version_id = Column(
        Integer,
        ForeignKey("article_versions.id"),
        nullable=True,
    )

    # Automatic voting freeze used by spike detection.
    vote_freeze = Column(Boolean, nullable=False, default=False)

    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(
        DateTime,
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )

    versions = relationship(
        "ArticleVersion",
        back_populates="article",
        cascade="all, delete-orphan",
        order_by="ArticleVersion.id",
        foreign_keys="ArticleVersion.article_id",
    )

    current_version = relationship(
        "ArticleVersion",
        foreign_keys=[current_version_id],
        uselist=False,
        post_update=True,
    )

    toc_parent = relationship(
        "Article",
        remote_side=[id],
        back_populates="toc_children",
        foreign_keys=[toc_parent_id],
    )
    toc_children = relationship(
        "Article",
        back_populates="toc_parent",
        foreign_keys=[toc_parent_id],
    )

    article_reactions = relationship(
        "ArticleReaction",
        back_populates="article",
        cascade="all, delete-orphan",
    )

    votes = relationship(
        "ArticleVote",
        back_populates="article",
        cascade="all, delete-orphan",
    )
    comments = relationship(
        "Comment",
        back_populates="article",
        cascade="all, delete-orphan",
    )


class ArticleVersion(Base):
    """Concrete version of an article.
    
    `content_blocks` stores the rendered block structure. German block keys such as `einleitung`, `juristisch`, and `anmerkung` are persisted MiniMD/domain contracts and must not be translated casually.
    """

    __tablename__ = "article_versions"

    id = Column(Integer, primary_key=True, index=True)

    article_id = Column(
        Integer,
        ForeignKey("articles.id"),
        nullable=False,
        index=True,
    )

    version_label = Column(String, nullable=False)
    content_blocks = Column(JSON, nullable=False)

    # Comment v2 – public/private boundary
    # - `status`: draft/published/archived baseline.
    # - `published_at`: set when the version is public.
    status = Column(String, nullable=False, default="draft", index=True)
    published_at = Column(DateTime, nullable=True, index=True)

    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    created_by_user_id = Column(
        Integer,
        ForeignKey("users.id"),
        nullable=True,
    )

    article = relationship(
        "Article",
        back_populates="versions",
        foreign_keys=[article_id],
    )
    created_by = relationship(
        "User",
        foreign_keys=[created_by_user_id],
    )

    votes = relationship(
        "ArticleVote",
        back_populates="version",
        cascade="all, delete-orphan",
    )
    article_reactions = relationship(
        "ArticleReaction",
        back_populates="version",
        cascade="all, delete-orphan",
    )
    comments = relationship(
        "Comment",
        back_populates="version",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        UniqueConstraint(
            "article_id",
            "version_label",
            name="uq_article_version_label_per_article",
        ),
    )


# ---------------------------------------------------------------------------
# 3. Voting and sentiment
# ---------------------------------------------------------------------------


class ArticleVote(Base):
    """User vote for a specific article version.
    
    The legacy database table name is intentionally `votes`; the ORM model remains `ArticleVote`.
    """

    # Legacy compatibility:
    # The table intentionally remains named `votes` to avoid a database migration
    # for a cosmetic model-name refactor.
    # The API/ORM model is `ArticleVote`; only the database table name is legacy.
    __tablename__ = "votes"

    id = Column(Integer, primary_key=True, index=True)

    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    article_id = Column(Integer, ForeignKey("articles.id"), nullable=False, index=True)
    version_id = Column(Integer, ForeignKey("article_versions.id"), nullable=False, index=True)

    main_vote = Column(String, nullable=False)
    flags = Column(JSON, nullable=True)

    status = Column(String, nullable=False, default="confirmed")  # pending/confirmed/rejected

    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    confirmed_at = Column(DateTime, nullable=True)

    user = relationship("User", back_populates="votes", foreign_keys=[user_id])
    article = relationship("Article", back_populates="votes", foreign_keys=[article_id])
    version = relationship("ArticleVersion", back_populates="votes", foreign_keys=[version_id])

    __table_args__ = (
        CheckConstraint("main_vote IN ('✅','🟢','🟡','🟠','🔴')", name="ck_article_votes_main_vote"),
        UniqueConstraint(
            "user_id",
            "article_id",
            "version_id",
            name="uq_article_vote_user_article_version",
        ),
        Index(
            "ix_article_votes_article_version",
            "article_id",
            "version_id",
        ),
    )


# ---------------------------------------------------------------------------
# Comment voting (parallel to article voting)
# ---------------------------------------------------------------------------


class CommentVote(Base):
    """User vote for a specific comment.
    
    `article_id` and `version_id` are intentionally denormalized to support efficient aggregation.
    """

    __tablename__ = "comment_votes"

    id = Column(Integer, primary_key=True, index=True)

    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    comment_id = Column(Integer, ForeignKey("comments.id"), nullable=False, index=True)
    article_id = Column(Integer, ForeignKey("articles.id"), nullable=False, index=True)
    version_id = Column(Integer, ForeignKey("article_versions.id"), nullable=False, index=True)

    main_vote = Column(String, nullable=False)
    flags = Column(JSON, nullable=True)

    status = Column(String, nullable=False, default="confirmed")  # pending/confirmed/rejected

    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    confirmed_at = Column(DateTime, nullable=True)

    user = relationship("User", back_populates="comment_votes", foreign_keys=[user_id])
    comment = relationship("Comment", back_populates="comment_votes", foreign_keys=[comment_id])

    __table_args__ = (
        CheckConstraint("main_vote IN ('✅','🟢','🟡','🟠','🔴')", name="ck_comment_votes_main_vote"),
        UniqueConstraint("user_id", "comment_id", name="uq_comment_vote_user_comment"),
        Index("ix_comment_votes_article_version", "article_id", "version_id"),
    )


# ---------------------------------------------------------------------------
# 4. Comments and structural proposals
# ---------------------------------------------------------------------------


class Comment(Base):
    """Comment or structured change proposal.
    
    `status` contains the persisted German primary status contract (for example `entwurf`, `review`, `veröffentlicht`, `abgelehnt`, `archiviert`, `integriert`, `gelöscht`). `lifecycle_status`, `policy_status`, and `candidate_status` are separate workflow fields and must not be conflated with it.
    """

    __tablename__ = "comments"

    id = Column(Integer, primary_key=True, index=True)

    article_id = Column(Integer, ForeignKey("articles.id"), nullable=False, index=True)
    version_id = Column(Integer, ForeignKey("article_versions.id"), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)

    type = Column(String, nullable=False, default="standard")
    anchor = Column(JSON, nullable=True)
    # Comment-v2 modes:
    # - `change`: change to an existing article.
    # - `new_article`: structured proposal for a new article after `article_id`.
    # - `delete_article`: proposal to delete `article_id`.
    comment_mode = Column(String, nullable=False, default="change", index=True)
    structure_payload = Column(JSON, nullable=True)

    # -------------------------------------------------------------------
    # Comment v2: patch/lifecycle metadata.
    # -------------------------------------------------------------------
    comment_category = Column(String, nullable=False, default="general")
    anchor_payload = Column(JSON, nullable=True)
    patch_payload = Column(JSON, nullable=True)
    patch_stats = Column(JSON, nullable=True)
    llm_assisted = Column(Boolean, nullable=False, default=False)
    llm_context_hash = Column(String, nullable=True)

    lifecycle_status = Column(String, nullable=False, default="draft")
    policy_status = Column(String, nullable=False, default="ok")
    candidate_status = Column(String, nullable=True)
    candidate_reasons = Column(JSON, nullable=True)
    candidate_score = Column(Integer, nullable=True)
 
    proposal_text = Column(Text, nullable=False)
    explanation = Column(Text, nullable=True)
    impact_scores = Column(JSON, nullable=True)
    sources = Column(Text, nullable=True)

    status = Column(String, nullable=False, default="entwurf")

    parent_comment_id = Column(Integer, ForeignKey("comments.id"), nullable=True, index=True)

    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
    published_at = Column(DateTime, nullable=True)

    public_author_label = Column(String, nullable=True)
    export_anonymized_id = Column(String, nullable=True, index=True)

    article = relationship("Article", back_populates="comments", foreign_keys=[article_id])
    version = relationship("ArticleVersion", back_populates="comments", foreign_keys=[version_id])
    user = relationship("User", back_populates="comments", foreign_keys=[user_id])

    parent_comment = relationship("Comment", remote_side=[id], backref="child_comments")

    reviews = relationship("Review", back_populates="comment", cascade="all, delete-orphan")
    reactions = relationship("CommentReaction", back_populates="comment", cascade="all, delete-orphan")

    comment_votes = relationship(
        "CommentVote",
        back_populates="comment",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        Index("ix_comments_article_version", "article_id", "version_id"),
        Index("ix_comments_article_version_status", "article_id", "version_id", "status"),
        Index("ix_comments_user_status", "user_id", "status"),
        Index("ix_comments_mode_status", "comment_mode", "status"),
    )


# ---------------------------------------------------------------------------
# 5. Review system
# ---------------------------------------------------------------------------


class Review(Base):
    """Review of a comment.
    
    `recommendation` stores the established German review-decision contract. `structured_checks` carries the normalized Review-v2 payload when available.
    """

    __tablename__ = "reviews"

    id = Column(Integer, primary_key=True, index=True)

    comment_id = Column(Integer, ForeignKey("comments.id"), nullable=False, index=True)
    reviewer_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)

    checks = Column(JSON, nullable=True)
    recommendation = Column(String, nullable=False)

    # Review v2: structured checks and trust snapshot.
    structured_checks = Column(JSON, nullable=True)
    reviewer_trust_at_time = Column(Integer, nullable=True)
    review_note = Column(Text, nullable=True)
    visible_to_public = Column(Boolean, nullable=False, default=False)

    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    comment = relationship("Comment", back_populates="reviews", foreign_keys=[comment_id])
    reviewer = relationship("User", back_populates="reviews", foreign_keys=[reviewer_id])




# ---------------------------------------------------------------------------
# 6. Reactions
# ---------------------------------------------------------------------------

class ArticleReaction(Base):
    """Emoji reaction to an article version.
    
    The bookmark marker `🚩` has special product semantics. `article_id` is denormalized for aggregation.
    """

    __tablename__ = "article_reactions"

    id = Column(Integer, primary_key=True, index=True)

    article_id = Column(Integer, ForeignKey("articles.id"), nullable=False, index=True)
    version_id = Column(Integer, ForeignKey("article_versions.id"), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)

    reaction_emoji = Column(String, nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    article = relationship("Article", back_populates="article_reactions", foreign_keys=[article_id])
    version = relationship("ArticleVersion", back_populates="article_reactions", foreign_keys=[version_id])
    user = relationship("User", back_populates="article_reactions", foreign_keys=[user_id])

    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "version_id",
            "reaction_emoji",
            name="uq_article_reaction_once",
        ),
        Index(
            "ix_article_reactions_article_version",
            "article_id",
            "version_id",
        ),
    )


class CommentReaction(Base):
    """Emoji reaction to a comment.
    
    A user can store each reaction emoji at most once per comment.
    """

    __tablename__ = "comment_reactions"

    id = Column(Integer, primary_key=True, index=True)

    comment_id = Column(Integer, ForeignKey("comments.id"), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)

    reaction_emoji = Column(String, nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    comment = relationship("Comment", back_populates="reactions", foreign_keys=[comment_id])
    user = relationship("User", back_populates="comment_reactions", foreign_keys=[user_id])

    __table_args__ = (
        UniqueConstraint(
            "comment_id",
            "user_id",
            "reaction_emoji",
            name="uq_comment_reaction_once",
        ),
    )






# ---------------------------------------------------------------------------
# 7. Runtime events and analytics
# ---------------------------------------------------------------------------






# ---------------------------------------------------------------------------
# 7. Runtime events and analytics
# ---------------------------------------------------------------------------




class UserEvent(Base):
    """User-related event log used for operational and account lifecycle events."""

    __tablename__ = "user_events"

    id = Column(Integer, primary_key=True, index=True)

    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)

    event_type = Column(String, nullable=False)
    payload = Column(JSON, nullable=True)

    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    user = relationship("User", back_populates="user_events", foreign_keys=[user_id])


# ---------------------------------------------------------------------------
# 8b. Privacy-friendly analytics (daily aggregation, no cookies)
# ---------------------------------------------------------------------------


class MetricsDaily(Base):
    """Daily privacy-preserving traffic aggregate."""

    __tablename__ = "metrics_daily"

    day = Column(Date, primary_key=True)

    pageviews = Column(Integer, nullable=False, default=0)
    pageviews_logged_in = Column(Integer, nullable=False, default=0)

    visitors_est = Column(Integer, nullable=False, default=0)

    onsite_seconds = Column(Integer, nullable=False, default=0)
    onsite_seconds_logged_in = Column(Integer, nullable=False, default=0)

    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)


class MetricsVisitorDaily(Base):
    """Daily deduplication table for visitor estimation without cookies.
    
    Only a daily visitor hash is stored; raw IP addresses and user agents are not persisted here.
    """

    __tablename__ = "metrics_visitors_daily"

    id = Column(Integer, primary_key=True, autoincrement=True)
    day = Column(Date, nullable=False, index=True)
    visitor_key = Column(String(64), nullable=False)  # sha256 hex
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("day", "visitor_key", name="uq_metrics_visitors_day_key"),
    )


# ---------------------------------------------------------------------------
# 8. Runtime configuration and release audit
# ---------------------------------------------------------------------------

class SystemConfig(Base):
    """Versioned system configuration snapshot stored in the database."""

    __tablename__ = "system_config"

    id = Column(Integer, primary_key=True, index=True)
    key = Column(String, nullable=False, unique=True, index=True)
    value_json = Column(JSON, nullable=False, default=dict)
    version = Column(Integer, nullable=False, default=1)

    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)




class ReleaseRun(Base):
    """Audit record for release cuts, including policy/configuration snapshots and release details."""

    __tablename__ = "release_runs"

    id = Column(Integer, primary_key=True, index=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    performed_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)

    from_draft_version_label = Column(String, nullable=True)
    to_current_version_label = Column(String, nullable=True)

    config_hash = Column(String, nullable=True)
    policy_snapshot = Column(JSON, nullable=True)
    details = Column(JSON, nullable=True)
