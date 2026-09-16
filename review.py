"""KlimaGG-Web — review policy and trust/gate helpers.
Version: v2.0.0

Implementation identifiers are English. Canonical persisted review decisions intentionally remain German (`annehmen`, `korrigieren`, `ablehnen`), while legacy aliases are accepted for compatibility.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Mapping

__version__ = "2.0.0"

DECISION_ACCEPT = "annehmen"
DECISION_CORRECT = "korrigieren"
DECISION_REJECT = "ablehnen"
DECISIONS = {DECISION_ACCEPT, DECISION_CORRECT, DECISION_REJECT}

LEGACY_DECISION_ALIASES = {
    "accept": DECISION_ACCEPT,
    "approve": DECISION_ACCEPT,
    "ok": DECISION_ACCEPT,
    "accepted": DECISION_ACCEPT,
    "angenommen": DECISION_ACCEPT,
    "annehmen": DECISION_ACCEPT,
    "revise": DECISION_CORRECT,
    "changes": DECISION_CORRECT,
    "überarbeiten": DECISION_CORRECT,
    "ueberarbeiten": DECISION_CORRECT,
    "korrigieren": DECISION_CORRECT,
    "correct": DECISION_CORRECT,
    "reject": DECISION_REJECT,
    "discard": DECISION_REJECT,
    "abgelehnt": DECISION_REJECT,
    "ablehnen": DECISION_REJECT,
}

SLIDER_KEYS = ("slider_goal", "slider_style", "slider_practical", "slider_legal")
SLIDER_MARKERS = {"slider_goal": "🧭", "slider_style": "✍️", "slider_practical": "🧩", "slider_legal": "⚖️"}
COMPLIANCE_KEYS = ("compliance_no_personal_data", "compliance_copyright_ok", "compliance_no_illegal_content")
FEEDBACK_TAGS = {
    "sources_missing",
    "legal_unclear",
    "too_imprecise",
    "wrong_article_location",
    "practicality_unclear",
    "tone_or_style",
    "formal_issue",
}
STATUS_REVIEW = "review"
STATUS_PUBLISHED = "veröffentlicht"
STATUS_REJECTED = "abgelehnt"
CONTEXT_GATE = "gate"
CONTEXT_TEST = "test"
CONTEXT_NONE = "none"
DEFAULT_REVIEW_TEST_POOL_CANDIDATE_STATUSES = ("review_test", "review_pool")


@dataclass(frozen=True)
class NormalizedReview:
    decision: str
    sliders: dict[str, int]
    compliance: dict[str, bool]
    feedback_tags: list[str] = field(default_factory=list)
    report_triggered: bool = False
    context: str = CONTEXT_GATE


@dataclass(frozen=True)
class ReviewEffect:
    positive_gate: bool
    negative_gate: bool
    reported: bool
    critical: bool
    quality_markers: list[str]
    feedback_tags: list[str]
    sliders: dict[str, int]


@dataclass(frozen=True)
class ReviewState:
    context: str
    positive_count: int
    negative_count: int
    required_positive: int
    required_review_count: int
    negative_threshold: int
    publish_ready: bool
    reject_ready: bool
    admin_warning: bool
    review_count: int
    slider_averages: dict[str, float | None]
    quality_marker_counts: dict[str, int]
    feedback_tag_counts: dict[str, int]
    author_review_gap: dict[str, float | None]
    base_required_reviews: int = 0
    max_reviews: int = 0
    required_positive_base: int = 0
    required_positive_total: int = 0
    extra_positive_from_negative: int = 0
    extra_reviews_from_slider_spread: int = 0
    revise_count: int = 0
    reject_count: int = 0
    moderation_needed: bool = False
    revision_requested: bool = False
    max_reviews_reached: bool = False
    slider_spread_max: float = 0.0
    slider_spread_avg: float = 0.0
    slider_spread_high: bool = False
    slider_spread_extreme: bool = False
    trust_positive_score: float = 0.0
    trust_negative_score: float = 0.0
    trust_net_score: float = 0.0
    trust_review_signal: str = "neutral"


@dataclass(frozen=True)
class ReviewTransition:
    status: str | None = None
    lifecycle_status: str | None = None
    policy_status: str | None = None
    candidate_status: str | None = None
    candidate_reasons: dict[str, Any] = field(default_factory=dict)


def to_dict(value: Any) -> dict[str, Any]:
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if hasattr(value, "dict"):
        return value.dict()
    if isinstance(value, Mapping):
        return dict(value)
    return {}


def cfg(settings: Any, name: str, default: Any) -> Any:
    return getattr(settings, name, default)


def cfg_int(settings: Any, name: str, default: int) -> int:
    try:
        return int(cfg(settings, name, default))
    except Exception:
        return int(default)


def cfg_float(settings: Any, name: str, default: float) -> float:
    try:
        return float(cfg(settings, name, default))
    except Exception:
        return float(default)


def cfg_str_list(settings: Any, name: str, default: tuple[str, ...] | list[str]) -> list[str]:
    value = cfg(settings, name, default)
    if value is None:
        return list(default)
    if isinstance(value, str):
        return [part.strip() for part in value.split(",") if part.strip()]
    if isinstance(value, (list, tuple, set)):
        return [str(part).strip() for part in value if str(part).strip()]
    return list(default)


def status_key(value: Any) -> str:
    return str(value or "").strip().lower().replace("oe", "ö")


def normalize_decision(value: Any) -> str:
    key = str(value or "").strip().lower().replace("oe", "ö")
    out = LEGACY_DECISION_ALIASES.get(key)
    if out not in DECISIONS:
        raise ValueError("Ungültige Review-Entscheidung")
    return str(out)


def normalize_sliders(raw: Any) -> dict[str, int]:
    data = to_dict(raw)
    out: dict[str, int] = {}
    for key in SLIDER_KEYS:
        value = data.get(key, 0)
        if value is None:
            value = 0
        try:
            n = int(value)
        except Exception:
            raise ValueError(f"Ungültiger Slider-Wert für {key}")
        if n < 0 or n > 5:
            raise ValueError(f"Slider-Wert für {key} muss zwischen 0 und 5 liegen")
        out[key] = n
    return out


AUTHOR_IMPACT_TO_REVIEW_SLIDER = {
    "goal": "slider_goal",
    "clarity": "slider_style",
    "practical": "slider_practical",
    "legal": "slider_legal",
}


def normalize_author_impact_scores(raw: Any) -> dict[str, float]:
    """Normalize the author's 0..4 self-assessment onto the review slider key space.

    Review sliders use 0..5. Author scores are scaled to the same range so the
    diagnostic author/reviewer gap compares like with like.
    """
    data = to_dict(raw)
    out: dict[str, float] = {}
    for author_key, slider_key in AUTHOR_IMPACT_TO_REVIEW_SLIDER.items():
        value = data.get(author_key)
        if value is None:
            continue
        if isinstance(value, bool):
            raise ValueError(f"Ungültiger Impact-Wert für {author_key}")
        try:
            n = int(value)
        except Exception:
            raise ValueError(f"Ungültiger Impact-Wert für {author_key}")
        if n < 0 or n > 4:
            raise ValueError(f"Impact-Wert für {author_key} muss zwischen 0 und 4 liegen")
        out[slider_key] = float(n) * 1.25
    return out


def normalize_compliance(raw: Any) -> dict[str, bool]:
    data = to_dict(raw)
    return {key: bool(data.get(key, False)) for key in COMPLIANCE_KEYS}


def normalize_feedback_tags(raw: Any) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, str):
        values = [raw]
    elif isinstance(raw, (list, tuple, set)):
        values = list(raw)
    else:
        raise ValueError("feedback_tags muss eine Liste sein")
    out: list[str] = []
    for item in values:
        key = str(item or "").strip()
        if not key:
            continue
        if key not in FEEDBACK_TAGS:
            raise ValueError(f"Unbekanntes Feedback-Tag: {key}")
        if key not in out:
            out.append(key)
    return out


def quality_markers_from_sliders(sliders: Mapping[str, Any]) -> list[str]:
    out: list[str] = []
    for key in SLIDER_KEYS:
        try:
            value = int(sliders.get(key, 0) or 0)
        except Exception:
            value = 0
        if value >= 4 and SLIDER_MARKERS.get(key):
            out.append(SLIDER_MARKERS[key])
    return out


def normalize_review_payload(payload: Any, *, context: str = CONTEXT_GATE) -> NormalizedReview:
    data = to_dict(payload)
    decision = normalize_decision(data.get("decision") or data.get("recommendation"))
    sliders = normalize_sliders(data.get("sliders") or {key: data.get(key, 0) for key in SLIDER_KEYS})

    has_v2_compliance = (
        data.get("compliance") is not None
        or any(data.get(key) is not None for key in COMPLIANCE_KEYS)
    )
    if not has_v2_compliance and data.get("decision") is None and data.get("recommendation") is not None:
        # Compatibility path for clients that still send only `recommendation`/`checks`
        # instead of the Review-v2 fields. Missing compliance data must not automatically
        # become a critical violation/admin warning on that legacy path. Once v2 fields
        # are present, explicit compliance values are enforced strictly.
        compliance = {key: True for key in COMPLIANCE_KEYS}
    else:
        compliance = normalize_compliance(data.get("compliance") or {key: data.get(key, False) for key in COMPLIANCE_KEYS})

    feedback_tags = normalize_feedback_tags(data.get("feedback_tags"))
    report_triggered = bool(data.get("report_triggered", False))
    ctx = str(context or CONTEXT_GATE).strip() or CONTEXT_GATE
    if ctx not in {CONTEXT_GATE, CONTEXT_TEST}:
        ctx = CONTEXT_GATE
    return NormalizedReview(decision, sliders, compliance, feedback_tags, report_triggered, ctx)


def normalized_review_to_structured_checks(review: NormalizedReview) -> dict[str, Any]:
    effect = classify_review_effect(review)
    return {
        "schema": "review-v2",
        "context": review.context,
        "decision": review.decision,
        "sliders": dict(review.sliders),
        "compliance": dict(review.compliance),
        "feedback_tags": list(review.feedback_tags),
        "report_triggered": bool(review.report_triggered),
        "effective_markers": list(effect.quality_markers),
    }


def normalize_review_from_model(review: Any, *, fallback_context: str = CONTEXT_GATE) -> NormalizedReview:
    structured = getattr(review, "structured_checks", None)
    if isinstance(structured, Mapping) and structured.get("schema") == "review-v2":
        context = str(structured.get("context") or fallback_context or CONTEXT_GATE)
        return normalize_review_payload(
            {
                "decision": structured.get("decision") or getattr(review, "recommendation", None),
                "sliders": structured.get("sliders") or {},
                "compliance": structured.get("compliance") or {},
                "feedback_tags": structured.get("feedback_tags") or [],
                "report_triggered": structured.get("report_triggered", False),
            },
            context=context,
        )
    checks = getattr(review, "checks", None)
    data = checks if isinstance(checks, Mapping) else {}
    return normalize_review_payload(
        {
            "decision": getattr(review, "recommendation", None),
            "sliders": data.get("sliders") or {},
            "compliance": data.get("compliance") or {},
            "feedback_tags": data.get("feedback_tags") or [],
            "report_triggered": data.get("report_triggered", False),
        },
        context=fallback_context,
    )


def classify_review_effect(review: NormalizedReview) -> ReviewEffect:
    compliance_ok = all(bool(v) for v in review.compliance.values())
    reported = bool(review.report_triggered)
    positive = review.decision == DECISION_ACCEPT and compliance_ok and not reported
    negative = review.decision in {DECISION_CORRECT, DECISION_REJECT} or (not compliance_ok) or reported
    critical = reported or not bool(review.compliance.get("compliance_no_illegal_content", False))
    return ReviewEffect(bool(positive), bool(negative), reported, bool(critical), quality_markers_from_sliders(review.sliders), list(review.feedback_tags), dict(review.sliders))


def review_context_for_comment(comment: Any, settings: Any = None) -> str:
    st = status_key(getattr(comment, "status", ""))
    if st == STATUS_REVIEW:
        return CONTEXT_GATE
    if st in {STATUS_PUBLISHED, "veroeffentlicht", "published"}:
        test_statuses = set(cfg_str_list(settings, "REVIEW_TEST_POOL_CANDIDATE_STATUSES", DEFAULT_REVIEW_TEST_POOL_CANDIDATE_STATUSES))
        if str(getattr(comment, "candidate_status", "") or "").strip() in test_statuses:
            return CONTEXT_TEST
    return CONTEXT_NONE


def is_reviewable_for_user(comment: Any, user: Any, *, has_reviewed: bool = False, settings: Any = None, allow_admin_self_review: bool | None = None) -> bool:
    if comment is None or user is None or has_reviewed:
        return False
    if review_context_for_comment(comment, settings) == CONTEXT_NONE:
        return False
    if str(getattr(comment, "candidate_status", "") or "") in {"review_stop", "admin_warning", "moderation_needed", "revision_requested"} and not bool(getattr(user, "is_admin", False)):
        return False
    if allow_admin_self_review is None:
        allow_admin_self_review = bool(cfg(settings, "ADMIN_ALLOW_SELF_REVIEW", True))
    if int(getattr(comment, "user_id", 0) or 0) == int(getattr(user, "id", 0) or 0):
        return bool(getattr(user, "is_admin", False)) and bool(allow_admin_self_review)
    return True


def change_size_bucket_for_comment(comment: Any) -> str:
    stats = getattr(comment, "patch_stats", None)
    bucket = None
    if isinstance(stats, Mapping):
        bucket = stats.get("change_size_bucket") or stats.get("size_bucket") or stats.get("bucket")
    bucket_s = str(bucket or "small").strip().lower()
    return bucket_s if bucket_s in {"small", "medium", "large"} else "small"

def base_required_reviews_for_comment(comment: Any, settings: Any = None) -> int:
    bucket = change_size_bucket_for_comment(comment)
    if bucket == "large":
        return cfg_int(settings, "REVIEW_BASE_REQUIRED_COUNT_LARGE", 9)
    if bucket == "medium":
        return cfg_int(settings, "REVIEW_BASE_REQUIRED_COUNT_MEDIUM", 6)
    return cfg_int(settings, "REVIEW_BASE_REQUIRED_COUNT_SMALL", 3)


def required_positive_reviews_for_comment(comment: Any, settings: Any = None) -> int:
    bucket = change_size_bucket_for_comment(comment)
    if bucket == "large":
        return cfg_int(settings, "REVIEW_REQUIRED_POSITIVE_LARGE", 9)
    if bucket == "medium":
        return cfg_int(settings, "REVIEW_REQUIRED_POSITIVE_MEDIUM", 6)
    return cfg_int(settings, "REVIEW_REQUIRED_POSITIVE_SMALL", 3)


def max_reviews_for_comment(comment: Any, settings: Any = None) -> int:
    bucket = change_size_bucket_for_comment(comment)
    if bucket == "large":
        return cfg_int(settings, "REVIEW_MAX_COUNT_LARGE", 15)
    if bucket == "medium":
        return cfg_int(settings, "REVIEW_MAX_COUNT_MEDIUM", 12)
    return cfg_int(settings, "REVIEW_MAX_COUNT_SMALL", 9)


def negative_threshold(settings: Any = None) -> int:
    return max(1, cfg_int(settings, "REVIEW_NEGATIVE_HARD_REJECT_COUNT", cfg_int(settings, "REVIEW_AUTO_BLOCK_NEGATIVE_MIN", 3)))


def trust_weight_for_review(review: Any, settings: Any = None) -> float:
    trust_max = max(1, cfg_int(settings, "TRUST_MAX", 25))
    weight_min = cfg_float(settings, "REVIEW_TRUST_SCORE_WEIGHT_MIN", 0.5)
    weight_max = cfg_float(settings, "REVIEW_TRUST_SCORE_WEIGHT_MAX", 2.0)
    try:
        trust = int(getattr(review, "reviewer_trust_at_time", 0) or 0)
    except Exception:
        trust = 0
    trust = max(0, min(trust_max, trust))
    return round(float(weight_min) + (float(weight_max) - float(weight_min)) * (float(trust) / float(trust_max)), 3)


def _slider_spread(slider_values: dict[str, list[int]], settings: Any = None) -> dict[str, Any]:
    ranges: list[float] = []
    for values in slider_values.values():
        if not values:
            continue
        ranges.append(float(max(values) - min(values)))
    spread_max = max(ranges) if ranges else 0.0
    spread_avg = (sum(ranges) / float(len(ranges))) if ranges else 0.0
    high = spread_max >= float(cfg_int(settings, "REVIEW_SLIDER_RANGE_EXTRA_THRESHOLD", 4)) or spread_avg >= cfg_float(settings, "REVIEW_SLIDER_AVG_RANGE_EXTRA_THRESHOLD", 3.0)
    extreme_count = sum(1 for r in ranges if r >= float(cfg_int(settings, "REVIEW_SLIDER_EXTREME_RANGE_THRESHOLD", 5)))
    extreme = extreme_count >= cfg_int(settings, "REVIEW_SLIDER_EXTREME_COUNT_FOR_MODERATION", 2)
    return {
        "max": round(spread_max, 2),
        "avg": round(spread_avg, 2),
        "high": bool(high),
        "extreme": bool(extreme),
    }


def _trust_review_signal(net_score: float, settings: Any = None) -> str:
    pos_min = cfg_float(settings, "REVIEW_TRUST_SCORE_STRONG_POSITIVE_MIN", 6.0)
    neg_max = cfg_float(settings, "REVIEW_TRUST_SCORE_STRONG_NEGATIVE_MAX", -3.0)
    if float(net_score) >= pos_min:
        return "strong_positive"
    if float(net_score) <= neg_max:
        return "strong_negative"
    return "mixed_or_weak"


def compute_comment_review_state(comment: Any, reviews: list[Any], settings: Any = None) -> ReviewState:
    context = review_context_for_comment(comment, settings)
    if context == CONTEXT_NONE:
        context = CONTEXT_GATE
    base_required_reviews = base_required_reviews_for_comment(comment, settings)
    required_positive_base = required_positive_reviews_for_comment(comment, settings)
    max_reviews = max_reviews_for_comment(comment, settings)
    neg_threshold = negative_threshold(settings)
    extra_positive_per_negative = max(0, cfg_int(settings, "REVIEW_EXTRA_POSITIVE_PER_NEGATIVE", 2))
    positive = 0
    negative = 0
    revise = 0
    reject = 0
    admin_warning = False
    review_count = 0
    slider_sums = {key: 0 for key in SLIDER_KEYS}
    slider_counts = {key: 0 for key in SLIDER_KEYS}
    slider_values = {key: [] for key in SLIDER_KEYS}
    marker_counts: dict[str, int] = {}
    tag_counts: dict[str, int] = {}
    trust_positive_score = 0.0
    trust_negative_score = 0.0
    for row in list(reviews or []):
        try:
            normalized = normalize_review_from_model(row, fallback_context=context)
        except Exception:
            continue
        effect = classify_review_effect(normalized)
        review_count += 1
        for key, value in effect.sliders.items():
            slider_sums[key] += int(value)
            slider_values[key].append(int(value))
            slider_counts[key] += 1
        for marker in effect.quality_markers:
            marker_counts[marker] = int(marker_counts.get(marker, 0)) + 1
        for tag in effect.feedback_tags:
            tag_counts[tag] = int(tag_counts.get(tag, 0)) + 1
        if effect.critical:
            admin_warning = True
        if normalized.context == CONTEXT_GATE:
            if effect.positive_gate:
                positive += 1
                trust_positive_score += trust_weight_for_review(row, settings)
            if effect.negative_gate:
                negative += 1
                trust_negative_score += trust_weight_for_review(row, settings)
            if normalized.decision == DECISION_CORRECT or (effect.negative_gate and normalized.decision not in {DECISION_REJECT}):
                revise += 1
            if normalized.decision == DECISION_REJECT:
                reject += 1
    slider_averages: dict[str, float | None] = {}
    for key in SLIDER_KEYS:
        cnt = int(slider_counts.get(key, 0))
        slider_averages[key] = round(float(slider_sums[key]) / float(cnt), 2) if cnt > 0 else None
    spread = _slider_spread(slider_values, settings)
    extra_reviews_from_slider = cfg_int(settings, "REVIEW_EXTRA_REVIEWS_FOR_HIGH_SLIDER_SPREAD", 2) if bool(spread["high"]) else 0
    required_review_count = int(base_required_reviews) + int(extra_reviews_from_slider)
    extra_positive_from_negative = int(negative) * int(extra_positive_per_negative)
    required_positive_total = int(required_positive_base) + int(extra_positive_from_negative)
    author_gap: dict[str, float | None] = {}
    try:
        author_scores = normalize_author_impact_scores(getattr(comment, "impact_scores", None) or {})
    except Exception:
        author_scores = {}
    for key in SLIDER_KEYS:
        avg = slider_averages.get(key)
        author_gap[key] = None if avg is None or key not in author_scores else round(float(avg) - float(author_scores[key]), 2)
    max_reached = int(review_count) >= int(max_reviews)
    slider_extreme = bool(spread["extreme"])
    revision_requested = bool(
        context == CONTEXT_GATE
        and not admin_warning
        and int(revise) >= cfg_int(settings, "REVIEW_REVISE_COUNT_AUTHOR_REVISION_MIN", 2)
        and int(reject) == 0
        and not (positive >= required_positive_total)
    )
    reject_ready = bool(context == CONTEXT_GATE and int(negative) >= int(neg_threshold) and not admin_warning)
    publish_ready = bool(
        context == CONTEXT_GATE
        and not admin_warning
        and not slider_extreme
        and not reject_ready
        and int(review_count) >= int(required_review_count)
        and int(positive) >= int(required_positive_total)
        and int(negative) < int(neg_threshold)
    )
    moderation_needed = bool(
        context == CONTEXT_GATE
        and not admin_warning
        and not publish_ready
        and not reject_ready
        and (
            slider_extreme
            or (max_reached and int(review_count) > 0)
            or (int(negative) > 0 and int(review_count) >= int(required_review_count) and int(positive) < int(required_positive_total))
        )
    )
    trust_net_score = round(float(trust_positive_score) - float(trust_negative_score), 2)
    return ReviewState(
        context=context,
        positive_count=int(positive),
        negative_count=int(negative),
        required_positive=int(required_positive_total),
        required_review_count=int(required_review_count),
        negative_threshold=int(neg_threshold),
        publish_ready=bool(publish_ready),
        reject_ready=bool(reject_ready),
        admin_warning=bool(admin_warning),
        review_count=int(review_count),
        slider_averages=slider_averages,
        quality_marker_counts=marker_counts,
        feedback_tag_counts=tag_counts,
        author_review_gap=author_gap,
        base_required_reviews=int(base_required_reviews),
        max_reviews=int(max_reviews),
        required_positive_base=int(required_positive_base),
        required_positive_total=int(required_positive_total),
        extra_positive_from_negative=int(extra_positive_from_negative),
        extra_reviews_from_slider_spread=int(extra_reviews_from_slider),
        revise_count=int(revise),
        reject_count=int(reject),
        moderation_needed=bool(moderation_needed),
        revision_requested=bool(revision_requested),
        max_reviews_reached=bool(max_reached),
        slider_spread_max=float(spread["max"]),
        slider_spread_avg=float(spread["avg"]),
        slider_spread_high=bool(spread["high"]),
        slider_spread_extreme=bool(slider_extreme),
        trust_positive_score=round(float(trust_positive_score), 2),
        trust_negative_score=round(float(trust_negative_score), 2),
        trust_net_score=trust_net_score,
        trust_review_signal=_trust_review_signal(trust_net_score, settings),
    )


def review_state_to_dict(state: ReviewState) -> dict[str, Any]:
    return asdict(state)


def decide_comment_transition(comment: Any, state: ReviewState) -> ReviewTransition:
    reasons = review_state_to_dict(state)
    if state.context == CONTEXT_GATE:
        if state.admin_warning:
            return ReviewTransition(status=STATUS_REVIEW, lifecycle_status="submitted", policy_status="admin_warning", candidate_status="admin_warning", candidate_reasons=reasons)
        if state.publish_ready:
            return ReviewTransition(status=STATUS_PUBLISHED, lifecycle_status="published", policy_status="ok", candidate_status="accepted", candidate_reasons=reasons)
        if state.reject_ready:
            return ReviewTransition(status=STATUS_REJECTED, lifecycle_status="rejected", policy_status="ok", candidate_status="rejected", candidate_reasons=reasons)
        if state.revision_requested:
            return ReviewTransition(status=STATUS_REVIEW, lifecycle_status="submitted", policy_status="ok", candidate_status="revision_requested", candidate_reasons=reasons)
        if state.moderation_needed:
            return ReviewTransition(status=STATUS_REVIEW, lifecycle_status="submitted", policy_status="admin_warning", candidate_status="moderation_needed", candidate_reasons=reasons)
        return ReviewTransition(status=STATUS_REVIEW, lifecycle_status="submitted", policy_status="ok", candidate_status="needs_more_reviews", candidate_reasons=reasons)
    if state.admin_warning:
        return ReviewTransition(status=getattr(comment, "status", None), policy_status="admin_warning", candidate_status="admin_warning", candidate_reasons=reasons)
    return ReviewTransition(status=getattr(comment, "status", None), candidate_reasons=reasons)


def get_effective_trust(user: Any, settings: Any = None) -> int:
    value = cfg_int(settings, "TRUST_ADMIN_VALUE", 24) if bool(getattr(user, "is_admin", False)) else int(getattr(user, "trust_level", 0) or 0)
    return max(cfg_int(settings, "TRUST_MIN", 0), min(cfg_int(settings, "TRUST_MAX", 24), int(value)))


def get_trust_tier(trust: int, settings: Any = None) -> str:
    if int(trust) >= cfg_int(settings, "TRUST_LIMIT_TIER_HIGH_MIN", 8):
        return "high"
    if int(trust) >= cfg_int(settings, "TRUST_LIMIT_TIER_NORMAL_MIN", 3):
        return "normal"
    return "low"


def get_review_limit_for_trust(trust: int, settings: Any = None) -> int:
    tier = get_trust_tier(trust, settings)
    if tier == "high":
        return cfg_int(settings, "TRUST_REVIEW_LIMIT_HIGH", 25)
    if tier == "normal":
        return cfg_int(settings, "TRUST_REVIEW_LIMIT_NORMAL", 10)
    return cfg_int(settings, "TRUST_REVIEW_LIMIT_LOW", 3)


def get_comment_limit_for_trust(trust: int, settings: Any = None) -> int:
    tier = get_trust_tier(trust, settings)
    if tier == "high":
        return cfg_int(settings, "TRUST_COMMENT_LIMIT_HIGH", 6)
    if tier == "normal":
        return cfg_int(settings, "TRUST_COMMENT_LIMIT_NORMAL", 3)
    return cfg_int(settings, "TRUST_COMMENT_LIMIT_LOW", 1)


def get_submit_gate_required(bucket: str, settings: Any = None) -> int:
    bucket_s = str(bucket or "small").strip().lower()
    if bucket_s == "large":
        return cfg_int(settings, "SUBMIT_REQUIRED_REVIEWS_LARGE", 3)
    if bucket_s == "medium":
        return cfg_int(settings, "SUBMIT_REQUIRED_REVIEWS_MEDIUM", 2)
    return cfg_int(settings, "SUBMIT_REQUIRED_REVIEWS_SMALL", 1)
