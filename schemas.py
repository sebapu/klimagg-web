"""KlimaGG-Web — Pydantic request/response schemas.
Version: v2.0.1

These models define REST API contracts for authentication, articles, voting, comments, reviews, administration, exports, and public statistics. Existing field names and enum-like string values may be compatibility contracts; language cleanup must not rename them without an explicit migration plan.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional, Literal

import re

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator, model_validator

__version__ = "2.0.1"


# ---------------------------------------------------------------------------
# Core
# ---------------------------------------------------------------------------


class HealthOut(BaseModel):
    status: str


class APIErrorOut(BaseModel):
    """Optional standardized API error response."""

    detail: str


class ContactInterestRequest(BaseModel):
    """Public contact/interest request. The `website` field is a honeypot and is ignored when populated by bots."""

    name: str = Field(..., min_length=2, max_length=160)
    email: EmailStr
    organization: Optional[str] = Field(default=None, max_length=200)
    role: Optional[str] = Field(default=None, max_length=160)
    interest_type: str = Field(..., min_length=2, max_length=80)
    message: str = Field(..., min_length=10, max_length=4000)
    datenschutz_ok: bool = False
    website: Optional[str] = Field(default=None, max_length=200)

    model_config = ConfigDict(extra="ignore")

    @field_validator("name", "organization", "role", "interest_type", mode="before")
    @classmethod
    def _normalize_short_text(cls, v):
        if v is None:
            return None
        s = re.sub(r"\s+", " ", str(v or "").strip())
        return s or None

    @field_validator("message", mode="before")
    @classmethod
    def _normalize_message(cls, v):
        s = str(v or "").replace("\r\n", "\n").replace("\r", "\n").strip()
        return s

    @field_validator("datenschutz_ok", mode="before")
    @classmethod
    def _normalize_datenschutz_ok(cls, v):
        if isinstance(v, bool):
            return v
        return str(v or "").strip().lower() in {"1", "true", "yes", "ja", "on"}

    @model_validator(mode="after")
    def _require_datenschutz_ok(self):
        if not self.datenschutz_ok:
            raise ValueError("datenschutz_ok required")
        return self


class ContactInterestOut(BaseModel):
    ok: bool
    message: str


# ---------------------------------------------------------------------------
# Metrics / analytics (privacy-friendly, no cookies)
# ---------------------------------------------------------------------------



# ---------------------------------------------------------------------------
# Articles and versions
# ---------------------------------------------------------------------------


class ArticleVersionOut(BaseModel):
    id: int
    version_label: str
    content_blocks: Dict[str, str]

    created_at: Optional[datetime] = None
    created_by_user_id: Optional[int] = None

    status: Optional[str] = None
    published_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)




class ArticleVersionMetaOut(BaseModel):
    """Compact metadata for an article version."""

    id: int
    version_label: str
    created_at: Optional[datetime] = None

    status: Optional[str] = None
    published_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


class VoteSummaryPerVersion(BaseModel):
    """Aggregated vote and reaction summary for one article version. Public counts remain raw; trust-weighted values are exposed separately where present."""

    version_id: int
    version_label: str

    # Legacy v1.0.x compatibility: raw total.
    total_votes: int
    votes_by_main: Dict[str, int]

    # v2: explicit raw and weighted fields for consistent UI use.
    raw_positive: int = 0
    raw_neutral: int = 0
    raw_negative: int = 0
    raw_total: int = 0
    raw_approval_percent: Optional[float] = None

    weighted_positive: int = 0
    weighted_neutral: int = 0
    weighted_negative: int = 0
    weighted_total: int = 0
    weighted_approval_percent: Optional[float] = None
    weighted_votes_by_main: Dict[str, int] = Field(default_factory=dict)
    reactions_count: Dict[str, int] = Field(default_factory=dict)
    effective_total_votes: Optional[int] = None
    votes_by_main_weighted: Dict[str, int] = Field(default_factory=dict)
    majority_emoji: Optional[str] = None


class ArticleVoteSummaryOut(BaseModel):
    """Aggregated vote summary for an article across versions."""

    article_id: int

    # Legacy v1.0.x compatibility: raw total.
    total_votes: int
    effective_total_votes: Optional[int] = None

    # v2: explicit raw and weighted fields for consistent UI use.
    raw_positive: int = 0
    raw_neutral: int = 0
    raw_negative: int = 0
    raw_total: int = 0
    raw_approval_percent: Optional[float] = None

    weighted_positive: int = 0
    weighted_neutral: int = 0
    weighted_negative: int = 0
    weighted_total: int = 0
    weighted_approval_percent: Optional[float] = None
    weighted_votes_by_main: Dict[str, int] = Field(default_factory=dict)

    versions: List[VoteSummaryPerVersion] = Field(default_factory=list)


class ArticleOut(BaseModel):
    id: int
    slug: str
    public_code: str
    sort_order: int
    toc_parent_id: Optional[int] = None
    title: str
    type: str

    # Display fields populated by the server.
    display_label: Optional[str] = None
    toc_group_label: Optional[str] = None

    # TOC: only articles with this flag are listed.
    show_in_toc: bool = True
    # Parent-article TOC short title. Child articles inherit the group title
    # through `toc_parent_id`.
    toc_title: Optional[str] = None

    # Convenience field for the current version; may be absent for incomplete/imported data.
    current_version: Optional[ArticleVersionOut] = None

    # Aggregated by the backend vote-summary logic.
    vote_summary: Optional[ArticleVoteSummaryOut] = None

    # Dominant main-vote emoji for the current version (TOC display).
    majority_emoji: Optional[str] = None

    # Automatic vote freeze used by spike detection.
    vote_freeze: bool = False

    # Public visibility: published articles or draft shells exposed by
    # published `new_article` comments.
    visibility_status: Optional[str] = None
    visibility_reason: Optional[str] = None
    public_comment_ids: List[int] = Field(default_factory=list)

    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


class ArticleVersionsOut(BaseModel):
    """Response model for `GET /api/articles/{id}/versions`."""

    article_id: int
    current_version_id: Optional[int] = None
    versions: List[ArticleVersionMetaOut] = Field(default_factory=list)


class ArticleDiffOut(BaseModel):
    """Response model for article-version diffs. `diff_blocks` is keyed by established German MiniMD/domain block names."""

    article_id: int
    from_version_id: int
    to_version_id: int
    diff_blocks: Dict[str, str]


class ArticleMiniMdOut(BaseModel):
    """Canonical MiniMD representation of an article version for patch-first comment flows."""
    article_id: int
    version_id: int
    minimd: str
    # Block order is optional display/debug metadata.
    blocks: List[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Users / authentication / profile
# ---------------------------------------------------------------------------


class UserOut(BaseModel):
    id: int
    email: EmailStr
    pseudonym: str
    plz: Optional[str] = None
    trust_level: int
    is_admin: bool

    # Account deletion scheduling/status.
    deletion_requested_at: Optional[datetime] = None
    deletion_scheduled_for: Optional[datetime] = None
    deleted_at: Optional[datetime] = None
    is_deleted: bool = False

    model_config = ConfigDict(from_attributes=True)


class AccountDeleteRequest(BaseModel):
    """Request for `POST /api/me/delete-request`. The action controls scheduling, cancellation, or immediate anonymizing deletion."""

    action: Literal["schedule", "cancel", "delete_now"]


class AccountDeleteStatusOut(BaseModel):
    """Response for the account-deletion request endpoint."""

    status: Literal["none", "scheduled", "deleted"]
    scheduled_for: Optional[datetime] = None


class ActivityItemOut(BaseModel):
    # One merged activity-log row.
    # kind: "comment" | "review" | "vote" | "note" | "system"
    kind: str
    created_at: datetime
    title: str
    detail: Optional[str] = None
    article_id: Optional[int] = None
    comment_id: Optional[int] = None
    version_id: Optional[int] = None


class MagicLinkRequest(BaseModel):
    """Request for a passwordless login link. Profile fields are collected after link verification."""

    email: EmailStr
    model_config = ConfigDict(extra="ignore")


class UpdatePseudonymRequest(BaseModel):
    """Request for `POST /api/me/pseudonym`."""

    pseudonym: str

    @field_validator("pseudonym", mode="before")
    @classmethod
    def _normalize_pseudonym(cls, v):
        s = str(v or "").strip()
        if not s:
            raise ValueError("pseudonym required")
        return s


class CompleteSignupRequest(BaseModel):
    """Request for `POST /api/auth/complete-signup`."""

    signup_token: str
    pseudonym: str
    plz: Optional[str] = None

    model_config = ConfigDict(extra="ignore")

    @field_validator("pseudonym", mode="before")
    @classmethod
    def _normalize_pseudonym(cls, v):
        s = str(v or "").strip()
        if not s:
            raise ValueError("pseudonym required")
        return s

    @field_validator("plz", mode="before")
    @classmethod
    def _normalize_plz_de(cls, v):
        """Validate/normalize a German five-digit postal code."""
        if v is None:
            return None
        s = str(v).strip()
        if not s:
            return None
        s = re.sub(r"\s+", "", s)
        if re.fullmatch(r"\d{5}", s):
            return s
        return None


class AuthCallbackResponse(BaseModel):
    """Authentication callback response. Existing users receive an access token; new users receive a signup token instead of an immediately created account."""

    access_token: Optional[str] = None
    token_type: str = "bearer"
    user: Optional[UserOut] = None
    signup_required: bool = False
    signup_token: Optional[str] = None


class UpdatePlzRequest(BaseModel):
    """Request for `POST /api/me/plz`."""

    plz: str

    @field_validator("plz", mode="before")
    @classmethod
    def _normalize_plz_de(cls, v):
        """Validate/normalize a German five-digit postal code."""
        s = str(v or "").strip()
        s = re.sub(r"\s+", "", s)
        if not re.fullmatch(r"\d{5}", s):
            raise ValueError("invalid plz")
        return s


# ---------------------------------------------------------------------------
# Meetings (legacy v0.9)
# ---------------------------------------------------------------------------








# ---------------------------------------------------------------------------
# Votes
# ---------------------------------------------------------------------------

AllowedMainVote = Literal["✅", "🟢", "🟡", "🟠", "🔴"]
# Keep the supported reaction set small and centrally typed;
# additions must be coordinated with UI rendering paths.
AllowedFlagEmoji = Literal["🧭", "✍️", "🧩", "⚖️", "🚩"]

class ArticleVoteRequest(BaseModel):
    """Request body for an article-version vote. Emoji reactions/bookmarks are managed by separate reaction endpoints."""

    main_vote: AllowedMainVote


class PersonalArticleVoteOut(BaseModel):
    """Current user vote for an article version."""

    article_id: int
    version_id: int
    main_vote: AllowedMainVote
    flags: Optional[List[AllowedFlagEmoji]] = None


class CommentVoteRequest(BaseModel):
    """Request body for a comment vote. Emoji reactions/bookmarks are managed separately."""

    main_vote: AllowedMainVote


class PersonalCommentVoteOut(BaseModel):
    """Current user vote for a comment."""

    comment_id: int
    article_id: int
    version_id: int
    main_vote: AllowedMainVote


class CommentVoteSummaryOut(BaseModel):
    """Aggregated comment vote/reaction summary. Public raw counts and internal weighted fields are deliberately separated."""

    comment_id: int

    # Legacy v1.0.x compatibility: raw total.
    total_votes: int
    effective_total_votes: Optional[int] = None
    votes_by_main_weighted: Dict[str, int] = Field(default_factory=dict)
    approval_percent_weighted: Optional[float] = None
    votes_by_main: Dict[str, int] = Field(default_factory=dict)

    # v2: explicit raw and weighted fields.
    raw_positive: int = 0
    raw_neutral: int = 0
    raw_negative: int = 0
    raw_total: int = 0
    raw_approval_percent: Optional[float] = None

    weighted_positive: int = 0
    weighted_neutral: int = 0
    weighted_negative: int = 0
    weighted_total: int = 0
    weighted_approval_percent: Optional[float] = None
    weighted_votes_by_main: Dict[str, int] = Field(default_factory=dict)

    # Backward compatibility: `approval_percent` remains the raw value.
    approval_percent: Optional[float] = None
    reactions_count: Dict[str, int] = Field(default_factory=dict)

    # Review metadata for display; votes remain a separate signal.
    reviews_total: int = 0
    reviews_positive: int = 0

    qualified_for_next_release: Optional[bool] = None
    bucket: Optional[str] = None

    # v2: deterministic server-side conflict check.
    conflict_count: int = 0
    conflict_parts: List[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Reactions (emoji/bookmark) are separate from votes (v2)
# ---------------------------------------------------------------------------


class ReactionToggleRequest(BaseModel):
    """Toggle request for one reaction emoji."""

    emoji: AllowedFlagEmoji


class ReactionToggleOut(BaseModel):
    """Reaction-toggle response containing the resulting state."""

    active: bool
    emojis: List[str] = Field(default_factory=list)


class MyArticleReactionsOut(BaseModel):
    """Current user reactions for an article version."""

    article_id: int
    version_id: int
    emojis: List[str] = Field(default_factory=list)


class MyCommentReactionsOut(BaseModel):
    """Current user reactions for a comment, including article/version identifiers for UI use."""

    comment_id: int
    article_id: int
    version_id: int
    emojis: List[str] = Field(default_factory=list)



# ---------------------------------------------------------------------------
# Patch payload v2 (parts[])
# ---------------------------------------------------------------------------

class PatchPartV2(BaseModel):
    """One part of a v2 patch payload. Extra fields remain allowed for forward-compatible editor/anchor metadata."""

    part_id: str = Field(..., min_length=1)
    old_text: str
    new_text: str

    # API/test contract: every patch part has a `block_key`.
    # `_all` means whole document / no specific block mapping.
    block_key: Optional[str] = Field(default="_all")

    @model_validator(mode="after")
    def _normalize_block_key(self):
        bk = (self.block_key or "").strip().lower()
        if not bk:
            bk = (self.block_id or "").strip().lower()
        if bk == "anmerkungen":
            bk = "anmerkung"
        if not bk:
            bk = "_all"
        self.block_key = bk
        return self

    # Optional metadata for UI/layer processing.
    block_id: Optional[str] = None
    anchor: Optional[Dict[str, Any]] = None
    meta: Optional[Dict[str, Any]] = None

    model_config = ConfigDict(extra="allow")


class PatchPayloadV2(BaseModel):
    """Multipart v2 patch payload stored and exchanged by the comment workflow."""

    version: Literal[2] = 2
    parts: List[PatchPartV2] = Field(default_factory=list)

    # Optional editor metadata.
    editor: Optional[Dict[str, Any]] = None

    model_config = ConfigDict(extra="allow")


class LegacyPatchPayloadV1(BaseModel):
    """Legacy single-part patch payload accepted for compatibility and normalized server-side."""
    old_text: str
    new_text: str
    diff_html: Optional[str] = None

    model_config = ConfigDict(extra="allow")


class PatchPartStatsV2(BaseModel):
    part_id: str
    block_key: Optional[str] = None
    old_chars: int
    new_chars: int
    changed_chars: int
    old_words: int
    new_words: int
    word_delta: int
    changed_words: int
    change_size_bucket: str
    diff_html: Optional[str] = None

    model_config = ConfigDict(extra="allow")


class PatchStatsV2(BaseModel):
    version: Literal[2] = 2
    parts: List[PatchPartStatsV2] = Field(default_factory=list)
    total_changed_words: int = 0
    bucket: str = "small"

    model_config = ConfigDict(extra="allow")


# ---------------------------------------------------------------------------
# Comments
# ---------------------------------------------------------------------------


class NewArticleDraftCreate(BaseModel):
    """Request for creating or previewing a structured new-article proposal. Article metadata is carried in the MiniMD meta block."""

    minimd: str = Field(..., min_length=1)
    structure_payload: Optional[Dict[str, Any]] = None
    explanation: Optional[str] = None
    impact_scores: Optional[Dict[str, Any]] = None
    sources: Optional[str] = None
    llm_assisted: bool = False
    llm_context_hash: Optional[str] = None


class CommentCreate(BaseModel):
    """Create request for a comment/change proposal. Legacy anchor fields and newer multipart patch fields are both accepted for compatibility."""

    article_id: int
    version_id: int
    
    type: str = "standard"

    # Legacy UI v1.0.x anchor.
    anchor: Optional[Dict[str, Any]] = None

    # Comment-v2 mode and structured metadata.
    comment_mode: Literal["change", "new_article", "delete_article"] = "change"
    structure_payload: Optional[Dict[str, Any]] = None

    # v2 anchor/patch data.
    comment_category: str = "general"
    anchor_payload: Optional[Dict[str, Any]] = None
    patch_payload: Optional[PatchPayloadV2 | LegacyPatchPayloadV1 | Dict[str, Any]] = None

    llm_assisted: bool = False
    llm_context_hash: Optional[str] = None

    # Content
    proposal_text: str
    explanation: Optional[str] = None
    impact_scores: Optional[Dict[str, Any]] = None
    sources: Optional[str] = None
    
    parent_comment_id: Optional[int] = None


class CommentUpdate(BaseModel):
    """Partial update request for an existing comment draft."""

    type: Optional[str] = None

    # Legacy compatibility.
    anchor: Optional[Dict[str, Any]] = None

    # Comment-v2.
    comment_mode: Optional[Literal["change", "new_article", "delete_article"]] = None
    structure_payload: Optional[Dict[str, Any]] = None

    # v2 fields.
    comment_category: Optional[str] = None
    anchor_payload: Optional[Dict[str, Any]] = None
    patch_payload: Optional[PatchPayloadV2 | LegacyPatchPayloadV1 | Dict[str, Any]] = None

    llm_assisted: Optional[bool] = None
    llm_context_hash: Optional[str] = None
 
    proposal_text: Optional[str] = None
    explanation: Optional[str] = None
    impact_scores: Optional[Dict[str, Any]] = None
    sources: Optional[str] = None


class CommentOut(BaseModel):
    """Public/API representation of a comment or patch proposal. Compatibility fields remain optional to support historical data and clients."""

    id: int
    article_id: int
    version_id: int
    user_id: int

    type: str
    status: str
    anchor: Optional[Dict[str, Any]] = None

    # Comment-v2.
    comment_mode: str = "change"
    structure_payload: Optional[Dict[str, Any]] = None

    # v2 patch/lifecycle metadata.
    comment_category: Optional[str] = None
    anchor_payload: Optional[Dict[str, Any]] = None
    patch_payload: Optional[PatchPayloadV2 | LegacyPatchPayloadV1 | Dict[str, Any]] = None
    patch_stats: Optional[PatchStatsV2 | Dict[str, Any]] = None
    llm_assisted: Optional[bool] = None
    llm_context_hash: Optional[str] = None

    lifecycle_status: Optional[str] = None
    policy_status: Optional[str] = None
    candidate_status: Optional[str] = None
    candidate_reasons: Optional[Dict[str, Any]] = None
    candidate_score: Optional[int] = None

    # Optional aggregates populated by article/Next-Draft views.
    approval_percent: Optional[float] = None
    qualified_for_next_release: Optional[bool] = None

    # Comment voting mirrors article voting.
    votes_total: Optional[int] = None
    effective_total_votes: Optional[int] = None
    votes_by_main: Optional[Dict[str, int]] = None
    flags_count: Optional[Dict[str, int]] = None
    my_main_vote: Optional[str] = None
    my_flags: Optional[List[str]] = None
    next_draft_bucket: Optional[str] = None
 
    proposal_text: str
    explanation: Optional[str] = None
    impact_scores: Optional[Dict[str, Any]] = None
    sources: Optional[str] = None

    parent_comment_id: Optional[int] = None

    created_at: datetime
    updated_at: datetime
    published_at: Optional[datetime] = None

    public_author_label: Optional[str] = None
    export_anonymized_id: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)


class CommentOverviewItemOut(BaseModel):
    """Compact row for the current user comment overview. Optional fields allow older stored data and partially populated workflows."""

    # Identity
    id: int
    article_id: int
    version_id: int

    # Status / timestamps
    status: str
    created_at: datetime
    updated_at: datetime
    published_at: Optional[datetime] = None
    lifecycle_status: Optional[str] = None
    policy_status: Optional[str] = None
    candidate_status: Optional[str] = None

    # Optional display metadata.
    article_slug: Optional[str] = None
    article_title: Optional[str] = None
    version_label: Optional[str] = None

    # Comment-v2 retrieval/editing data.
    comment_mode: str = "change"
    structure_payload: Optional[Dict[str, Any]] = None
    proposal_text: Optional[str] = None
    explanation: Optional[str] = None
    sources: Optional[str] = None
    anchor: Optional[Dict[str, Any]] = None
    anchor_payload: Optional[Dict[str, Any]] = None
    impact_scores: Optional[Dict[str, Any]] = None
    patch_stats: Optional[Dict[str, Any]] = None
    llm_assisted: Optional[bool] = None
    llm_context_hash: Optional[str] = None

    # Optional engagement aggregates.
    reviews_count: Optional[int] = None
    reviews_required: Optional[int] = None
    reviews_total: Optional[int] = None
    reviews_approve: Optional[int] = None
    reviews_revise: Optional[int] = None
    reviews_reject: Optional[int] = None
    reactions_summary: Optional[Dict[str, int]] = None
    reactions: Optional[Dict[str, int]] = None

    # Optional integration/workflow metadata.
    integrated_in_version_id: Optional[int] = None
    integrated_in_version_label: Optional[str] = None
    integrated_anchor: Optional[Dict[str, Any]] = None

    model_config = ConfigDict(from_attributes=True)


class CommentForkResponse(BaseModel):
    """Response for `POST /api/comments/{id}/fork`."""

    id: int
    parent_comment_id: int
    status: str


class CommentDiscardResponse(BaseModel):
    """Response for `POST /api/comments/{id}/discard`."""

    id: int
    status: str


class CommentSubmitResponse(BaseModel):
    """Response for `POST /api/comments/{id}/submit`."""

    id: int
    status: str


# ---------------------------------------------------------------------------
# Reviews (v2 with v0.9 compatibility)
# ---------------------------------------------------------------------------


class ReviewCreate(BaseModel):
    """Review-v2 request. Canonical German decision values remain part of the persisted/API contract; legacy v0.9 fields are still accepted."""

    comment_id: int

    # Review-v2 fields.
    decision: Optional[Literal["annehmen", "korrigieren", "ablehnen"] | str] = None
    slider_goal: Optional[int] = Field(None, ge=0, le=5)
    slider_style: Optional[int] = Field(None, ge=0, le=5)
    slider_practical: Optional[int] = Field(None, ge=0, le=5)
    slider_legal: Optional[int] = Field(None, ge=0, le=5)
    sliders: Optional[Dict[str, Any]] = None

    compliance_no_personal_data: Optional[bool] = None
    compliance_copyright_ok: Optional[bool] = None
    compliance_no_illegal_content: Optional[bool] = None
    compliance: Optional[Dict[str, Any]] = None

    feedback_tags: List[str] = Field(default_factory=list)
    report_triggered: bool = False

    # Legacy compatibility.
    checks: Optional[Dict[str, Any]] = None
    recommendation: Optional[str] = None
    review_note: Optional[str] = None
    visible_to_public: bool = False


class ReviewOut(BaseModel):
    id: int
    comment_id: int
    reviewer_id: int
    checks: Optional[Dict[str, Any]] = None
    structured_checks: Optional[Dict[str, Any]] = None
    reviewer_trust_at_time: Optional[int] = None
    recommendation: str
    review_note: Optional[str] = None
    visible_to_public: bool
    created_at: datetime

    # Optional convenience fields for v2 clients.
    review_state: Optional[Dict[str, Any]] = None
    comment_status: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)


class ReviewNextOut(BaseModel):
    """Response for `GET /api/reviews/next`."""

    comment: CommentOut
    context: Optional[Literal["gate", "test"] | str] = None
    review_state: Optional[Dict[str, Any]] = None
    ui_hint: Optional[str] = None

    # Optional legacy-compatible gold/training case metadata; does not control the UI.
    is_gold_check: Optional[bool] = None
    gold_reference: Optional[Dict[str, Any]] = None


class CommentReviewsOut(BaseModel):
    """Response for `GET /api/comments/{id}/reviews`."""

    comment_id: int
    reviews: List[ReviewOut] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Historical news / logs / statistics
# ---------------------------------------------------------------------------



 






class AdminStatsOut(BaseModel):
    """Compact admin dashboard statistics."""

    users_total: int
    articles_total: int
    article_versions_total: int

    votes_total: int
    votes_confirmed_total: int
    votes_pending_total: int

    comments_total: int
    comments_by_status: Dict[str, int]

    reviews_total: int



# ---------------------------------------------------------------------------
# Admin overviews
# ---------------------------------------------------------------------------

class AdminOverviewMeta(BaseModel):
    """Metadata shared by admin overview responses."""
    generated_at: datetime
    note: Optional[str] = None


class AdminUserRowOut(BaseModel):
    id: int
    pseudonym: str
    email: Optional[str] = None
    plz: Optional[str] = None
    trust_level: int = 0
    is_admin: bool = False
    created_at: datetime
    is_deleted: bool = False
    votes_total: int = 0
    reviews_total: int = 0
    comments_total: int = 0
    review_accept_total: int = 0
    review_revise_total: int = 0
    review_reject_total: int = 0
    review_report_total: int = 0
    review_negative_total: int = 0
    review_negative_ratio: Optional[float] = None
    review_alignment_total: int = 0
    review_alignment_hits: int = 0
    review_alignment_rate: Optional[float] = None
    review_slider_deviation_avg: Optional[float] = None
    review_quality_flag: str = "ok"
    review_quality_reasons: List[str] = Field(default_factory=list)


class AdminUsersOverviewOut(BaseModel):
    meta: AdminOverviewMeta
    items: List[AdminUserRowOut]


class AdminArticleRowOut(BaseModel):
    id: int
    slug: str
    public_code: str
    sort_order: int
    toc_parent_id: Optional[int] = None
    title: str
    type: str
    display_label: Optional[str] = None
    toc_group_label: Optional[str] = None
    current_version_id: Optional[int] = None
    current_version_label: Optional[str] = None
    versions_total: int = 0
    votes_total: int = 0
    votes_by_main: Dict[str, int] = {}
    approval_percent: Optional[float] = None
    comments_total: int = 0
    comments_by_status: Dict[str, int] = {}
    pending_changes: int = 0
    updated_at: Optional[datetime] = None


class AdminArticlesOverviewOut(BaseModel):
    meta: AdminOverviewMeta
    items: List[AdminArticleRowOut]


class AdminCommentRowOut(BaseModel):
    id: int
    article_id: int
    article_slug: str
    article_title: str
    version_id: int
    version_label: Optional[str] = None
    status: str
    lifecycle_status: Optional[str] = None
    policy_status: Optional[str] = None
    candidate_status: Optional[str] = None
    candidate_reasons: Dict[str, Any] = Field(default_factory=dict)
    candidate_score: Optional[int] = None
    comment_mode: str = "change"
    patch_stats: Dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    updated_at: Optional[datetime] = None
    user_id: int
    user_pseudonym: str
    vote_counts: Dict[str, int] = Field(default_factory=dict)
    vote_approval_percent: Optional[float] = None
    reaction_counts: Dict[str, int] = Field(default_factory=dict)
    required_reviews: Optional[int] = None
    review_rollup: Dict[str, int] = Field(default_factory=dict)
    review_state: Dict[str, Any] = Field(default_factory=dict)
    review_trust_signal: Optional[str] = None
    review_trust_net_score: Optional[float] = None
    admin_priority_rank: int = 9
    admin_priority_label: str = "—"
    admin_priority_title: str = "normal"
    gold_review_candidate: bool = False
    release_candidate: bool = False
    release_delay_candidate: bool = False
    conflict_flags: List[str] = Field(default_factory=list)
    report_flags: List[str] = Field(default_factory=list)
    open_url: Optional[str] = None


class AdminCommentsOverviewOut(BaseModel):
    meta: AdminOverviewMeta
    items: List[AdminCommentRowOut]


class AdminReviewQueueItemOut(BaseModel):
    comment: AdminCommentRowOut
    open_reviews_needed: int = 0


class AdminReviewQueueOut(BaseModel):
    meta: AdminOverviewMeta
    total_in_review: int = 0
    items: List[AdminReviewQueueItemOut]


class AdminVoteDistributionDayOut(BaseModel):
    day: str  # YYYY-MM-DD
    total: int = 0
    by_main: Dict[str, int] = {}
    distinct_users: int = 0
    low_trust_votes: int = 0
    new_user_votes: int = 0
    approval_percent: Optional[float] = None
    anomaly: bool = False


class AdminVoteDistributionOut(BaseModel):
    meta: AdminOverviewMeta
    days: int = 30
    series: List[AdminVoteDistributionDayOut]


class PublicProjectStatsMood(BaseModel):
    """Compact public vote-sentiment summary used on the homepage."""
    total: int
    by_main: Dict[str, int]
    approval_percent: float


class PublicProjectStatsSeries(BaseModel):
    """Daily public project-statistics series."""
    dates: List[str]
    main: Dict[str, List[int]]
    flags: Dict[str, List[int]]
    metrics: Dict[str, List[int]] = Field(default_factory=dict)


class PublicProjectStatsOut(BaseModel):
    """Public project-statistics response used by the homepage."""
    days: int
    totals: Dict[str, int]
    delta: Dict[str, int]
    mood: PublicProjectStatsMood
    mood_total: PublicProjectStatsMood
    series: PublicProjectStatsSeries


class PublicTrafficSeries(BaseModel):
    """Daily privacy-friendly traffic series."""

    dates: List[str]
    pageviews: List[int]
    visitors_est: List[int]

    # Optional compatibility field.
    pageviews_logged_in: List[int] = Field(default_factory=list)


class PublicTrafficStatsOut(BaseModel):
    """Public privacy-friendly traffic statistics."""

    days: int
    totals: Dict[str, int]
    series: PublicTrafficSeries

class AdminExportMetaOut(BaseModel):
    """Metadata and download links for one export run."""

    id: int
    report_type: str
    period_start: datetime
    period_end: datetime
    generated_at: datetime

    file_names: Dict[str, str]
    download_urls: Dict[str, str]

    model_config = ConfigDict(from_attributes=True)


class AdminExportRunOut(BaseModel):
    """Response returned after starting an export."""

    mode: str
    paths: Dict[str, str]


class WebVersionItemOut(BaseModel):
    """One Git tag/release candidate shown by the web-upgrade UI."""
    ref: str
    sha: Optional[str] = None
    is_stable: bool = False

class AdminWebVersionsOut(BaseModel):
    """Current web version plus versions discovered from GitHub."""
    current_version: str
    current_ref: str
    current_is_stable: bool
    latest_ref: Optional[str] = None
    update_available: bool
    available_versions: List[WebVersionItemOut]
    last_checked_at: Optional[datetime] = None
    cache_age_sec: Optional[float] = None
    source: Optional[str] = None

class AdminWebUpgradeRequest(BaseModel):
    ref: str

class AdminWebUpgradeOut(BaseModel):
    ok: bool
    ref: str
    detail: Optional[str] = None

class AdminWebStableRequest(BaseModel):
    stable: bool




class UserReviewStatsOut(BaseModel):
    """Compact review/comment statistics for the user panel."""

    # User comments.
    total_comments: int
    comments_by_status: Dict[str, int]

    # Review activity.
    total_reviews: int
    reviews_since_last_comment: Optional[int] = None

    # Optional gold-check metadata.
    gold_hits: Optional[int] = None
    gold_misses: Optional[int] = None
    accuracy_ratio: Optional[float] = None

    # Time metadata.
    last_review_at: Optional[datetime] = None
    last_comment_created_at: Optional[datetime] = None


# ---------------------------------------------------------------------------
# Transparency
# ---------------------------------------------------------------------------


