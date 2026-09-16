"""
indiff.py
Status: product inline-diff / patch / validator library

Purpose
-------
This module is the canonical backend library for:
  - MiniMD -> HTML rendering with offset mapping
  - persisted comment patch parts -> block-local change requests
  - article inline-diff composition
  - comment card table / card rendering from compose truth
  - comment patch / compatibility validation

Architecture rule
-----------------
MiniMD remains the input / persistence truth.
HTML remains the operative execution / anchor / split truth.
Structural parsing is the bridge between both.

Public product APIs
-------------------
- compose_article_merge_preview(...):
    Compose one full multi-comment article preview from persisted comment
    patch parts. This is the main app.py/API integration entrypoint.

- compose_block_change_set(...):
    Compose one block from already localized ChangeRequest objects. This is
    the product block engine used by compose_article_merge_preview and tests.

- compose_article_version_diff(...):
    Compose a previous -> current article-version diff from MiniMD block maps
    using the same semantic-change and block-composition pipeline.

- build_change_requests_from_patch_parts(...):
    Convert persisted comment patch_payload parts into block-local
    ChangeRequest objects plus applied/unapplied diagnostics.

- normalize_comment_patch_payload(...):
    Normalize persisted patch parts without resolving them against a baseline.

- validate_comment_minimd_syntax(...):
    Validate MiniMD syntax shape: scope balance, list syntax and basic
    structural completeness. It does not judge content quality.

- validate_comment_patch_payload(...):
    Validate one normalized patch payload against available block baselines
    and locate all old_text selections.

- validate_exact_selection_patch_payload(...):
    Strict exact-selection validation with optional baseline-hash binding.

- extract_canonical_events_from_comments(...):
    Return stable, sorted insert/delete events for comments against a baseline.

- validate_comment_submission(...):
    Runtime validation bundle for one comment submission against one article
    version and one current baseline.

- prepare_comment_submission(...):
    Convenience bundle for normalization, syntax validation, patch validation,
    submission validation and canonical events.

- check_comment_compatibility(...):
    Lightweight compatibility check for a persisted comment against a current
    baseline.

- materialize_article_with_comments(...):
    Apply selected persisted comment parts directly to block-local MiniMD,
    validate the resulting full MiniMD document, and render clean HTML/content
    blocks without diff markup.

- check_comment_base_rebase(...):
    Strictly relocate one persisted comment from an old MiniMD baseline to a
    new MiniMD baseline. Safe means the same old_text -> new_text parts can be
    applied unchanged; no semantic/fuzzy merge is attempted.

- minimd_to_html_text(...):
    Render MiniMD to HTML using the same minimal renderer and mapping rules
    as the compose pipeline.

- canonicalize_html_for_compare(...):
    Normalize HTML for stable comparisons in tests/tools.

- build_product_html_anchor_table(...):
    Build a simple product-facing HTML source-offset anchor table.

Identifier contract
-------------------
- cid:
    Comment id. Useful for coarse UI grouping, but too coarse for exact
    per-part toggling.

- part_id:
    Persisted comment part id. External ids normally look like p_juristisch.
    Compose-internal split ids are prefixed with the cid, e.g.
    c004_p_juristisch:m1.

- owner_id:
    Stable per-comment-part ownership id, usually "{cid}:{part_id}".
    This is the preferred toggle/style scope for all HTML pieces that belong
    to the same logical comment part, including structural list/table nodes.

- span_id:
    Materialized insert/delete span id. One owner can produce multiple spans,
    especially when a delete range is split by HTML tags or insert boundaries.

- data-kgg-owner-ids:
    Space-separated owner ids for shared structural/overlap pieces. For
    overlapping deletes, the shared text is rendered once and carries all
    owning owner ids.

- data-kgg-span-ids:
    Space-separated span ids for shared rendered text where more than one
    materialized span owns the same HTML segment.

State handling
--------------
This library does not decide UI policy such as warn/none.
It only provides stable span/owner ids plus tables describing what can be
styled externally.
"""

# Version: v0.4.51

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Optional, Protocol
import difflib
import html
import hashlib
import re
from html.parser import HTMLParser

try:
    from config import settings
except Exception:
    settings = None


class IndiffConfigLike(Protocol):
    ...


@dataclass
class ChangeRequest:
    change_id: str
    cid: int
    block_key: str
    old_text: str
    new_text: str
    old_start: int
    old_end: int
    context_before: str = ""
    context_after: str = ""
    list_parent_hint: str = ""
    change_class: str = "auto"
    list_host_mode: str = ""
    list_prev_block_kind: str = ""
    list_prev_block_raw_start: int = 0
    list_prev_block_raw_end: int = 0
    list_next_block_kind: str = ""
    list_next_block_raw_start: int = 0
    list_next_block_raw_end: int = 0


@dataclass
class StructuredInsertFragment:
    change_class: str
    text: str
    list_kind: str = ""
    list_level: int = 0


@dataclass
class HtmlAnchor:
    anchor_id: str
    source_offset: int


@dataclass
class ResolvedDeleteSegment:
    segment_id: str
    start: int
    end: int
    deleted_text: str
    owners: list[dict[str, Any]] = field(default_factory=list)
    status: str = "normal"
    html_source_start: int = 0
    html_source_end: int = 0
    resolved: bool = False


@dataclass
class ResolvedAtomicChange:
    atomic_id: str
    original_part_id: str
    cid: int
    block_key: str
    kind: str               # "insert" | "delete"
    subkind: str            # "inline_insert" | "list_item_insert" | "delete_range"
    raw_start: int
    raw_end: int
    source_start: int
    source_end: int
    inserted_text: str = ""
    deleted_text: str = ""
    list_kind: str = ""
    list_level: int = 0
    span_id: str = ""
    owner_id: str = ""
    delete_segment_id: str = ""
    host_kind: str = ""
    host_source_start: int = 0
    host_source_end: int = 0
    list_host_mode: str = ""
    list_prev_block_kind: str = ""
    list_prev_block_raw_start: int = 0
    list_prev_block_raw_end: int = 0
    list_next_block_kind: str = ""
    list_next_block_raw_start: int = 0
    list_next_block_raw_end: int = 0
    list_parent_hint: str = ""


@dataclass
class ChangeGroup:
    group_id: str
    block_key: str
    group_raw_start: int
    group_raw_end: int
    group_source_start: int
    group_source_end: int
    members: list[ResolvedAtomicChange] = field(default_factory=list)
    has_delete: bool = False
    has_inline_insert: bool = False
    has_list_insert: bool = False
    host_kind: str = ""
    host_source_start: int = 0
    host_source_end: int = 0


@dataclass
class SpanRegistryRow:
    span_id: str
    owner_id: str
    cid: int
    part_id: str
    block_key: str
    kind: str
    source_start: int = 0
    source_end: int = 0
    default_state: str = "normal"
    available_states: list[str] = field(default_factory=lambda: ["normal", "warn", "none"])
    css_class_normal: str = ""
    css_class_warn: str = ""
    css_class_none: str = ""


@dataclass
class HtmlOperation:
    op_id: str
    block_key: str
    op_type: str
    anchor_source_start: int
    anchor_source_end: int
    payload_before: str = ""
    payload_inside_start: str = ""
    payload_inside_end: str = ""
    payload_after: str = ""
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class ComposeBlockChangeSetResult:
    block_key: str
    baseline_html: str
    composed_html: str
    normalized_changes: list[dict[str, Any]]
    delete_segments: list[dict[str, Any]]
    insert_points: list[dict[str, Any]]
    operations: list[HtmlOperation]
    anchor_audit_rows: list[dict[str, Any]]
    span_registry: list[dict[str, Any]] = field(default_factory=list)
    style_table: list[dict[str, Any]] = field(default_factory=list)
    interaction_table: list[dict[str, Any]] = field(default_factory=list)
    comment_card_rows: list[dict[str, Any]] = field(default_factory=list)
    diagnostics: dict[str, Any] = field(default_factory=dict)


@dataclass
class PersistedCommentPartInput:
    """
    Stable external input contract for one persisted patch_payload part.

    This is the public bridge from app.py / DB-layer into indiff.py.
    """
    part_id: str
    block_key: str = ""
    old_text: str = ""
    new_text: str = ""
    sel_start: int | None = None
    sel_end: int | None = None
    baseline_hash: str = ""


@dataclass
class PersistedCommentInput:
    """
    Stable external input contract for one persisted comment.
    """
    cid: int
    parts: list[PersistedCommentPartInput] = field(default_factory=list)


@dataclass
class ArticleMergePreviewResult:
    """
    Stable product output for one composed multi-comment article preview.
    """
    merged_marked_html_full: str
    comment_cards_html: str
    comment_cards_table: list[dict[str, Any]] = field(default_factory=list)
    block_results: dict[str, ComposeBlockChangeSetResult] = field(default_factory=dict)
    applied_parts: list[dict[str, Any]] = field(default_factory=list)
    locate_failures: list[dict[str, Any]] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)


@dataclass
class CommentSubmissionValidationResult:
    """
    Runtime validation result for one comment submission against one article version.
    """
    ok: bool
    review_blocking: bool
    errors: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[dict[str, Any]] = field(default_factory=list)
    document_meta: dict[str, Any] = field(default_factory=dict)
    part_results: list[dict[str, Any]] = field(default_factory=list)
    normalized_comment: Optional[PersistedCommentInput] = None
    syntax_validation: dict[str, Any] = field(default_factory=dict)
    patch_validation: dict[str, Any] = field(default_factory=dict)


@dataclass
class CommentPatchPartStats:
    part_id: str
    block_key: str
    changed_words: int
    old_text: str = ""
    new_text: str = ""
    pre_text: str = ""
    post_text: str = ""
    diff_html: str = ""
    diff_html_compact: str = ""


@dataclass
class CommentPatchStats:
    cid: int
    total_changed_words: int
    size_bucket: str
    parts: list[CommentPatchPartStats] = field(default_factory=list)


@dataclass
class CommentCardPartData:
    part_id: str
    block_key: str
    old_text: str
    new_text: str
    changed_words: int
    pre_text: str = ""
    post_text: str = ""
    diff_html: str = ""
    diff_html_compact: str = ""


@dataclass
class CommentDiffBoxData:
    cid: int
    total_changed_words: int
    size_bucket: str
    validator_summary: dict[str, Any] = field(default_factory=dict)
    parts: list[CommentCardPartData] = field(default_factory=list)


@dataclass
class CommentSubmissionPreparationResult:
    normalized_comment: PersistedCommentInput
    syntax_validation: dict[str, Any]
    patch_validation: dict[str, Any]
    submission_validation: CommentSubmissionValidationResult
    canonical_events: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class CommentCompatibilityResult:
    compatible: bool
    normalized_comment: PersistedCommentInput
    applied_parts: list[dict[str, Any]] = field(default_factory=list)
    locate_failures: list[dict[str, Any]] = field(default_factory=list)
    errors: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class ArticleMaterializationResult:
    """Clean release-oriented materialization result.

    ``minimd_by_block`` is the canonical resulting text. ``html_by_block`` is
    rendered from that MiniMD. ``content_blocks`` follows the current DB
    contract: ``meta`` remains MiniMD text, all other blocks are clean HTML.
    """
    ok: bool
    block_order: list[str] = field(default_factory=list)
    minimd_by_block: dict[str, str] = field(default_factory=dict)
    html_by_block: dict[str, str] = field(default_factory=dict)
    content_blocks: dict[str, str] = field(default_factory=dict)
    article_minimd: str = ""
    applied_parts: list[dict[str, Any]] = field(default_factory=list)
    conflicts: list[dict[str, Any]] = field(default_factory=list)
    errors: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[dict[str, Any]] = field(default_factory=list)
    syntax_validation: dict[str, Any] = field(default_factory=dict)
    baseline_hash_by_block: dict[str, str] = field(default_factory=dict)
    result_hash_by_block: dict[str, str] = field(default_factory=dict)


@dataclass
class CommentBaseRebaseResult:
    """Strict base check for one persisted comment.

    ``safe`` only means that the *same persisted parts* can be relocated and
    applied to the new baseline without changing old_text/new_text. It does not
    perform a semantic/fuzzy merge.
    """
    safe: bool
    status: str
    normalized_comment: PersistedCommentInput
    rebased_comment: Optional[PersistedCommentInput] = None
    relocated_parts: list[dict[str, Any]] = field(default_factory=list)
    errors: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[dict[str, Any]] = field(default_factory=list)
    materialized: Optional[ArticleMaterializationResult] = None


# ---------------------------------------------------------------------------
# Minimal MiniMD renderer with offset mapping
# ---------------------------------------------------------------------------

@dataclass
class _RenderState:
    src: str
    out: list[str] = field(default_factory=list)
    offset_map: dict[int, int] = field(default_factory=dict)
    ol_open: bool = False
    ul1_open: bool = False
    ul2_open: bool = False
    li1_open: bool = False
    li2_open: bool = False

    def mark(self, raw_offset: int) -> None:
        self.offset_map[int(raw_offset)] = len("".join(self.out))

    def emit_literal(self, s: str) -> None:
        if s:
            self.out.append(s)

    def emit_text(self, s: str, raw_start: int) -> None:
        text = str(s)
        for i, ch in enumerate(text):
            self.offset_map[raw_start + i] = len("".join(self.out))
            self.out.append(html.escape(ch, quote=False))
            self.offset_map[raw_start + i + 1] = len("".join(self.out))


def _close_all_lists(st: _RenderState) -> None:
    if st.li2_open:
        st.emit_literal("</li>")
        st.li2_open = False
    if st.ul2_open:
        st.emit_literal("</ul>")
        st.ul2_open = False
    if st.li1_open:
        st.emit_literal("</li>")
        st.li1_open = False
    if st.ul1_open:
        st.emit_literal("</ul>")
        st.ul1_open = False
    if st.ol_open:
        st.emit_literal("</ol>")
        st.ol_open = False


def _parse_inline_bold(text: str, raw_start: int, st: _RenderState) -> None:
    i = 0
    while i < len(text):
        if text.startswith("**", i):
            j = text.find("**", i + 2)
            if j >= 0:
                st.mark(raw_start + i)
                st.mark(raw_start + i + 1)
                st.emit_literal("<b>")
                _emit_plain_text_with_map(text[i + 2:j], raw_start + i + 2, st)
                st.emit_literal("</b>")
                st.mark(raw_start + j)
                st.mark(raw_start + j + 1)
                i = j + 2
                continue
        if text.startswith("`", i):
            j = text.find("`", i + 1)
            if j >= 0:
                st.mark(raw_start + i)
                st.emit_literal("<code>")
                st.emit_text(text[i + 1:j], raw_start + i + 1)
                st.emit_literal("</code>")
                st.mark(raw_start + j)
                i = j + 1
                continue
        if text.startswith("[", i):
            mid = text.find("](", i + 1)
            if mid >= 0:
                end = text.find(")", mid + 2)
                if end >= 0:
                    label = text[i + 1:mid]
                    href = text[mid + 2:end]
                    st.mark(raw_start + i)
                    st.emit_literal(f'<a href="{html.escape(href, quote=True)}">')
                    _emit_plain_text_with_map(label, raw_start + i + 1, st)
                    st.emit_literal("</a>")
                    for off in range(raw_start + mid, raw_start + end + 1):
                        st.mark(off)
                    i = end + 1
                    continue
        if text.startswith("*", i):
            j = text.find("*", i + 1)
            if j >= 0:
                st.mark(raw_start + i)
                st.emit_literal("<i>")
                _emit_plain_text_with_map(text[i + 1:j], raw_start + i + 1, st)
                st.emit_literal("</i>")
                st.mark(raw_start + j)
                i = j + 1
                continue
        if text.startswith("~", i):
            j = text.find("~", i + 1)
            if j >= 0:
                st.mark(raw_start + i)
                st.emit_literal("<sub>")
                _emit_plain_text_with_map(text[i + 1:j], raw_start + i + 1, st)
                st.emit_literal("</sub>")
                st.mark(raw_start + j)
                i = j + 1
                continue
        st.offset_map[raw_start + i] = len("".join(st.out))
        st.out.append(html.escape(text[i], quote=False))
        st.offset_map[raw_start + i + 1] = len("".join(st.out))
        i += 1


def _emit_plain_text_with_map(text: str, raw_start: int, st: _RenderState) -> None:
    _parse_inline_bold(str(text or ""), int(raw_start), st)


def _render_inline_html(text: str) -> str:
    dummy = _RenderState(src=str(text or ""))
    _emit_plain_text_with_map(str(text or ""), 0, dummy)
    return "".join(dummy.out)


def _render_inline_with_mapping(text: str) -> tuple[str, dict[int, int]]:
    dummy = _RenderState(src=str(text or ""))
    _emit_plain_text_with_map(str(text or ""), 0, dummy)
    return "".join(dummy.out), dict(dummy.offset_map)


def _merge_submapping(
    st: _RenderState,
    raw_base: int,
    html_base: int,
    sub_map: dict[int, int],
) -> None:
    for k, v in dict(sub_map or {}).items():
        st.offset_map[int(raw_base) + int(k)] = int(html_base) + int(v)


def _split_table_row_with_cell_offsets(body: str) -> list[tuple[str, int, int]]:
    """
    Split one MiniMD table row into cells and preserve per-cell raw offsets
    relative to the row body.

    Returns:
      (cell_text_stripped, raw_start_in_body, raw_end_in_body)
    """
    s = str(body or "")
    out: list[tuple[str, int, int]] = []
    n = len(s)
    i = 0

    if i < n and s[i] == "|":
        i += 1

    cell_start = i
    while i <= n:
        at_end = i == n
        at_sep = (i < n and s[i] == "|")
        if at_end or at_sep:
            raw_cell = s[cell_start:i]
            if at_end and cell_start == n:
                break
            left_trim = len(raw_cell) - len(raw_cell.lstrip())
            right_trim = len(raw_cell.rstrip())
            trimmed = raw_cell.strip()
            start_off = cell_start + left_trim
            end_off = cell_start + right_trim
            if not (at_end and trimmed == "" and cell_start > 0 and s.endswith("|")):
                out.append((trimmed, int(start_off), int(end_off)))
            i += 1
            cell_start = i
            continue
        i += 1
    return out


def _render_table_scope_with_mapping(block_key: str, src: str, st: _RenderState, inner_start: int) -> None:
    lines = _iter_minimd_lines_with_offsets(src)
    row_lines = [(a, b, body) for a, b, body in lines if str(body).strip()]
    if not row_lines:
        st.emit_literal("<table></table>")
        return

    def _cell_texts(body: str) -> list[str]:
        return [c[0] for c in _split_table_row_with_cell_offsets(body)]

    header_cells = _cell_texts(row_lines[0][2])
    body_rows = row_lines[1:]
    if len(row_lines) >= 2:
        sep_cells = _cell_texts(row_lines[1][2])
        if sep_cells and all(re.match(r"^:?-{3,}:?$", c) for c in sep_cells):
            body_rows = row_lines[2:]

    st.emit_literal("<table>")
    st.emit_literal("<thead><tr>")
    row0_start, row0_end, row0_body = row_lines[0]
    for off in range(int(inner_start + row0_start), int(inner_start + row0_end) + 1):
        st.mark(off)
    for cell_text, cell_start, cell_end in _split_table_row_with_cell_offsets(row0_body):
        st.emit_literal("<th>")
        html_base = len("".join(st.out))
        cell_html, cell_map = _render_inline_with_mapping(cell_text)
        st.emit_literal(cell_html)
        _merge_submapping(
            st,
            int(inner_start + row0_start + cell_start),
            int(html_base),
            cell_map,
        )
        st.mark(int(inner_start + row0_start + cell_end))
        st.emit_literal("</th>")
    st.emit_literal("</tr></thead>")
    st.emit_literal("<tbody>")
    for row_start, row_end, body in body_rows:
        for off in range(int(inner_start + row_start), int(inner_start + row_end) + 1):
            st.mark(off)
        st.emit_literal("<tr>")
        for cell_text, cell_start, cell_end in _split_table_row_with_cell_offsets(body):
            st.emit_literal("<td>")
            html_base = len("".join(st.out))
            cell_html, cell_map = _render_inline_with_mapping(cell_text)
            st.emit_literal(cell_html)
            _merge_submapping(
                st,
                int(inner_start + row_start + cell_start),
                int(html_base),
                cell_map,
            )
            st.mark(int(inner_start + row_start + cell_end))
            st.emit_literal("</td>")
        st.emit_literal("</tr>")
    st.emit_literal("</tbody></table>")
    for off in range(int(inner_start), int(inner_start) + len(src) + 1):
        st.offset_map.setdefault(int(off), len("".join(st.out)))


def _split_source_lines_with_offsets(src: str) -> list[tuple[int, int, int, str, str]]:
    """
    Return rows as:
      (line_start, line_end_without_newline, line_end_with_newline, body, full_line)
    """
    out: list[tuple[int, int, int, str, str]] = []
    pos = 0
    for line in str(src or "").splitlines(keepends=True):
        body = line[:-1] if line.endswith("\n") else line
        line_start = pos
        line_end = pos + len(body)
        newline_end = pos + len(line)
        out.append((int(line_start), int(line_end), int(newline_end), str(body), str(line)))
        pos += len(line)
    return out


@dataclass
class _MiniMdListToken:
    kind: str
    raw_start: int
    raw_end: int
    content_raw_start: int
    text: str
    marker_text: str = ""


@dataclass
class _MiniMdListItem:
    list_kind: str
    list_level: int
    line_raw_start: int
    line_raw_end: int
    body_raw_start: int
    body_raw_end: int
    body_text: str
    tail: list[tuple[str, Any]] = field(default_factory=list)


@dataclass
class _MiniMdListBlock:
    list_kind: str
    list_level: int
    items: list[_MiniMdListItem] = field(default_factory=list)


@dataclass
class _MiniMdTopLevelListBlock:
    list_kind: str
    raw_start: int
    raw_end: int
    item_starts: list[int] = field(default_factory=list)
    item_ends: list[int] = field(default_factory=list)


@dataclass
class _HtmlTopLevelListBlock:
    list_kind: str
    open_start: int
    open_end: int
    close_start: int = 0
    close_end: int = 0
    li_open_starts: list[int] = field(default_factory=list)
    li_open_ends: list[int] = field(default_factory=list)
    li_close_starts: list[int] = field(default_factory=list)
    li_close_ends: list[int] = field(default_factory=list)


def _classify_minimd_list_token(
    body: str,
    line_start: int,
    line_end: int,
) -> Optional[_MiniMdListToken]:
    s = str(body or "")

    m_ul2 = re.match(r"^(  -\s+)(.*)$", s)
    if m_ul2:
        marker = str(m_ul2.group(1) or "")
        return _MiniMdListToken(
            kind="ul2",
            raw_start=int(line_start),
            raw_end=int(line_end),
            content_raw_start=int(line_start + len(marker)),
            text=str(m_ul2.group(2) or ""),
            marker_text=marker,
        )

    m_cont2 = re.match(r"^(  ::\s+)(.*)$", s)
    if m_cont2:
        marker = str(m_cont2.group(1) or "")
        return _MiniMdListToken(
            kind="cont2",
            raw_start=int(line_start),
            raw_end=int(line_end),
            content_raw_start=int(line_start + len(marker)),
            text=str(m_cont2.group(2) or ""),
            marker_text=marker,
        )

    m_ol2 = re.match(r"^(  \d+\.\s+)(.*)$", s)
    if m_ol2:
        marker = str(m_ol2.group(1) or "")
        return _MiniMdListToken(
            kind="ol2",
            raw_start=int(line_start),
            raw_end=int(line_end),
            content_raw_start=int(line_start + len(marker)),
            text=str(m_ol2.group(2) or ""),
            marker_text=marker,
        )

    m_ul1 = re.match(r"^(-\s+)(.*)$", s)
    if m_ul1:
        marker = str(m_ul1.group(1) or "")
        return _MiniMdListToken(
            kind="ul1",
            raw_start=int(line_start),
            raw_end=int(line_end),
            content_raw_start=int(line_start + len(marker)),
            text=str(m_ul1.group(2) or ""),
            marker_text=marker,
        )

    m_cont1 = re.match(r"^(::\s+)(.*)$", s)
    if m_cont1:
        marker = str(m_cont1.group(1) or "")
        return _MiniMdListToken(
            kind="cont1",
            raw_start=int(line_start),
            raw_end=int(line_end),
            content_raw_start=int(line_start + len(marker)),
            text=str(m_cont1.group(2) or ""),
            marker_text=marker,
        )

    # ordered level 1:
    # no leading indentation; "  1. ..." is ol2, not ol1.
    m_ol1 = re.match(r"^(\d+\.\s+)(.*)$", s)
    if m_ol1:
        marker = str(m_ol1.group(1) or "")
        return _MiniMdListToken(
            kind="ol1",
            raw_start=int(line_start),
            raw_end=int(line_end),
            content_raw_start=int(line_start + len(marker)),
            text=str(m_ol1.group(2) or ""),
            marker_text=marker,
        )

    return None


def _parse_list_region_from_lines(
    line_rows: list[tuple[int, int, int, str, str]],
    start_i: int,
) -> tuple[list[_MiniMdListBlock], int]:
    blocks: list[_MiniMdListBlock] = []
    i = int(start_i)
    current_block: Optional[_MiniMdListBlock] = None
    current_l1: Optional[_MiniMdListItem] = None
    current_l2: Optional[_MiniMdListItem] = None

    while i < len(line_rows):
        line_start, line_end, _newline_end, body, _line = line_rows[i]
        tok = _classify_minimd_list_token(body, line_start, line_end)
        if tok is None:
            break

        if tok.kind in {"ul1", "ol1"}:
            want_kind = "ul" if tok.kind == "ul1" else "ol"
            if current_block is None or str(current_block.list_kind) != want_kind or int(current_block.list_level) != 1:
                current_block = _MiniMdListBlock(list_kind=want_kind, list_level=1)
                blocks.append(current_block)
            item = _MiniMdListItem(
                list_kind=want_kind,
                list_level=1,
                line_raw_start=int(tok.raw_start),
                line_raw_end=int(tok.raw_end),
                body_raw_start=int(tok.content_raw_start),
                body_raw_end=int(tok.raw_end),
                body_text=str(tok.text),
            )
            current_block.items.append(item)
            current_l1 = item
            current_l2 = None
            i += 1
            continue

        if tok.kind in {"ul2", "ol2"}:
            if current_l1 is None:
                # invalid orphan level-2 list item; stop structural list parsing
                break
            item = _MiniMdListItem(
                list_kind="ol" if str(tok.kind) == "ol2" else "ul",
                list_level=2,
                line_raw_start=int(tok.raw_start),
                line_raw_end=int(tok.raw_end),
                body_raw_start=int(tok.content_raw_start),
                body_raw_end=int(tok.raw_end),
                body_text=str(tok.text),
            )
            current_l1.tail.append(("child", item))
            current_l2 = item
            i += 1
            continue

        if tok.kind == "cont2":
            if current_l2 is None:
                break
            current_l2.tail.append(("cont", tok))
            i += 1
            continue

        if tok.kind == "cont1":
            if current_l1 is None:
                break
            current_l1.tail.append(("cont", tok))
            current_l2 = None
            i += 1
            continue

        break

    return blocks, i


def _emit_list_token_text(tok: _MiniMdListToken, st: _RenderState) -> None:
    for off in range(int(tok.raw_start), int(tok.content_raw_start)):
        st.mark(off)
    _emit_plain_text_with_map(str(tok.text or ""), int(tok.content_raw_start), st)
    st.mark(int(tok.raw_end))


def _emit_list_item_body(item: _MiniMdListItem, st: _RenderState) -> None:
    for off in range(int(item.line_raw_start), int(item.body_raw_start)):
        st.mark(off)
    _emit_plain_text_with_map(str(item.body_text or ""), int(item.body_raw_start), st)
    st.mark(int(item.line_raw_end))


def _render_list_item_with_mapping(item: _MiniMdListItem, st: _RenderState) -> None:
    st.emit_literal("<li>")
    _emit_list_item_body(item, st)

    child_batch: list[_MiniMdListItem] = []

    def flush_child_batch() -> None:
        nonlocal child_batch
        if not child_batch:
            return
        i = 0
        while i < len(child_batch):
            tag = "ol" if str(child_batch[i].list_kind) == "ol" else "ul"
            st.emit_literal(f"<{tag}>")
            while i < len(child_batch) and ("ol" if str(child_batch[i].list_kind) == "ol" else "ul") == tag:
                _render_list_item_with_mapping(child_batch[i], st)
                i += 1
            st.emit_literal(f"</{tag}>")
        child_batch = []

    for entry_kind, payload in list(item.tail or []):
        if entry_kind == "child":
            child_batch.append(payload)
            continue
        flush_child_batch()
        if entry_kind == "cont":
            st.emit_literal("<br>")
            _emit_list_token_text(payload, st)

    flush_child_batch()
    st.emit_literal("</li>")


def _render_list_blocks_with_mapping(blocks: list[_MiniMdListBlock], st: _RenderState) -> None:
    for block in list(blocks or []):
        tag = "ol" if str(block.list_kind) == "ol" else "ul"
        st.emit_literal(f"<{tag}>")
        for item in list(block.items or []):
            _render_list_item_with_mapping(item, st)
        st.emit_literal(f"</{tag}>")


def _render_list_region_with_mapping(
    line_rows: list[tuple[int, int, int, str, str]],
    start_i: int,
    st: _RenderState,
) -> int:
    blocks, next_i = _parse_list_region_from_lines(line_rows, start_i)
    _render_list_blocks_with_mapping(blocks, st)
    return int(next_i)


def _collect_top_level_list_blocks(minimd_text: str) -> list[_MiniMdTopLevelListBlock]:
    """
    Collect contiguous top-level list blocks from MiniMD.

    A block is one contiguous run of level-1 list items of the same kind:
      - ul1
      - ol1

    Level-2 items and continuations belong to the currently open top-level block.
    """
    src = str(minimd_text or "").replace("\r\n", "\n").replace("\r", "\n")
    line_rows = _split_source_lines_with_offsets(src)
    out: list[_MiniMdTopLevelListBlock] = []
    i = 0
    cur_kind: str | None = None
    cur_start: int | None = None
    cur_end: int | None = None
    cur_item_starts: list[int] = []
    cur_item_ends: list[int] = []

    def flush() -> None:
        nonlocal cur_kind, cur_start, cur_end, cur_item_starts, cur_item_ends
        if cur_kind is None or cur_start is None or cur_end is None:
            return
        out.append(_MiniMdTopLevelListBlock(
            list_kind=str(cur_kind),
            raw_start=int(cur_start),
            raw_end=int(cur_end),
            item_starts=list(cur_item_starts),
            item_ends=list(cur_item_ends),
        ))
        cur_kind = None
        cur_start = None
        cur_end = None
        cur_item_starts = []
        cur_item_ends = []

    while i < len(line_rows):
        line_start, line_end, newline_end, body, _line = line_rows[i]
        tok = _classify_minimd_list_token(body, line_start, line_end)
        if tok is None:
            flush()
            i += 1
            continue

        if tok.kind == "ul1":
            want_kind = "ul"
        elif tok.kind == "ol1":
            want_kind = "ol"
        elif tok.kind in {"ul2", "ol2", "cont1", "cont2"}:
            want_kind = cur_kind
        else:
            want_kind = cur_kind

        if want_kind is None:
            flush()
            i += 1
            continue

        if cur_kind is None:
            cur_kind = str(want_kind)
            cur_start = int(line_start)
            cur_end = int(newline_end)
            if tok.kind in {"ul1", "ol1"}:
                cur_item_starts.append(int(line_start))
                cur_item_ends.append(int(newline_end))
        elif str(cur_kind) != str(want_kind) and tok.kind in {"ul1", "ol1"}:
            flush()
            cur_kind = str(want_kind)
            cur_start = int(line_start)
            cur_end = int(newline_end)
            cur_item_starts.append(int(line_start))
            cur_item_ends.append(int(newline_end))
        else:
            cur_end = int(newline_end)
            if tok.kind in {"ul1", "ol1"}:
                cur_item_starts.append(int(line_start))
                cur_item_ends.append(int(newline_end))

        i += 1

    flush()
    return out


def _find_neighbor_top_level_blocks(
    blocks: list[_MiniMdTopLevelListBlock],
    raw_pos: int,
) -> tuple[Optional[_MiniMdTopLevelListBlock], Optional[_MiniMdTopLevelListBlock]]:
    prev_block: Optional[_MiniMdTopLevelListBlock] = None
    next_block: Optional[_MiniMdTopLevelListBlock] = None
    pos = int(raw_pos)
    for blk in blocks:
        if int(blk.raw_end) <= pos:
            prev_block = blk
            continue
        if int(blk.raw_start) >= pos:
            next_block = blk
            break
        # raw_pos lies inside this block
        prev_block = blk
        next_block = blk
        break
    return prev_block, next_block


def _find_enclosing_top_level_block_and_next_item(
    blocks: list[_MiniMdTopLevelListBlock],
    raw_pos: int,
) -> tuple[Optional[_MiniMdTopLevelListBlock], int]:
    pos = int(raw_pos)
    for blk in list(blocks or []):
        if int(blk.raw_start) <= pos <= int(blk.raw_end):
            for item_start in list(blk.item_starts or []):
                if int(item_start) >= pos:
                    return blk, int(item_start)
            return blk, 0
    return None, 0


def _collect_html_top_level_list_blocks(html_text: str) -> list[_HtmlTopLevelListBlock]:
    """
    Collect top-level HTML list blocks and their direct <li> children.

    This is intentionally HTML-side truth for list seam placement:
    - only depth-1 <ol>/<ul> become top-level blocks
    - only direct child <li> of that block are recorded
    """
    src = str(html_text or "")
    tags = list(re.finditer(r"</?\s*(?:ol|ul|li)\b[^>]*>", src, flags=re.I))
    out: list[_HtmlTopLevelListBlock] = []
    list_stack: list[dict[str, Any]] = []
    li_stack: list[dict[str, Any]] = []

    for m in tags:
        raw_tok = str(m.group(0) or "")
        tag_match = re.match(r"<\s*(/?)\s*(ol|ul|li)\b", raw_tok, flags=re.I)
        if not tag_match:
            continue
        is_close = bool(str(tag_match.group(1) or ""))
        tag_name = str(tag_match.group(2) or "").lower()

        if not is_close and tag_name in {"ol", "ul"}:
            kind = str(tag_name)
            depth = len(list_stack) + 1
            if depth == 1:
                out.append(
                    _HtmlTopLevelListBlock(
                        list_kind=str(kind),
                        open_start=int(m.start()),
                        open_end=int(m.end()),
                    )
                )
                block_idx = len(out) - 1
            else:
                block_idx = next(
                    (int(x.get("block_idx")) for x in reversed(list_stack) if x.get("depth") == 1 and x.get("block_idx") is not None),
                    -1,
                )
            list_stack.append(
                {
                    "kind": str(kind),
                    "depth": int(depth),
                    "block_idx": int(block_idx),
                }
            )
            continue

        if is_close and tag_name in {"ol", "ul"}:
            if not list_stack:
                continue
            rec = list_stack.pop()
            if int(rec.get("depth") or 0) == 1:
                block_idx = int(rec.get("block_idx") or -1)
                if 0 <= block_idx < len(out):
                    out[block_idx].close_start = int(m.start())
                    out[block_idx].close_end = int(m.end())
            continue

        if not is_close and tag_name == "li":
            current_depth = len(list_stack)
            top_block_idx = next(
                (int(x.get("block_idx")) for x in reversed(list_stack) if x.get("depth") == 1 and x.get("block_idx") is not None),
                -1,
            )
            is_direct = current_depth == 1 and top_block_idx >= 0
            if is_direct:
                out[top_block_idx].li_open_starts.append(int(m.start()))
                out[top_block_idx].li_open_ends.append(int(m.end()))
            li_stack.append(
                {
                    "direct": bool(is_direct),
                    "block_idx": int(top_block_idx),
                }
            )
            continue

        if is_close and tag_name == "li":
            if not li_stack:
                continue
            rec = li_stack.pop()
            block_idx = int(rec.get("block_idx") or -1)
            if bool(rec.get("direct")) and 0 <= block_idx < len(out):
                out[block_idx].li_close_starts.append(int(m.start()))
                out[block_idx].li_close_ends.append(int(m.end()))
            continue

    return out


def _pick_html_top_level_list_block(
    html_text: str,
    list_kind: str,
    approx_pos: int,
) -> Optional[_HtmlTopLevelListBlock]:
    blocks = [
        blk
        for blk in _collect_html_top_level_list_blocks(html_text)
        if str(blk.list_kind or "") == str(list_kind or "")
    ]
    if not blocks:
        return None

    pos = int(approx_pos or 0)
    for blk in blocks:
        if int(blk.open_start) <= pos <= int(blk.close_end or blk.open_end):
            return blk

    return min(
        blocks,
        key=lambda blk: min(
            abs(int(blk.open_start) - pos),
            abs(int((blk.close_start or blk.open_end)) - pos),
        ),
    )


def _find_direct_li_open_in_block_at_or_after(
    block: _HtmlTopLevelListBlock,
    approx_pos: int,
) -> int:
    pos = int(approx_pos or 0)
    for li_pos in list(block.li_open_starts or []):
        if int(li_pos) >= pos:
            return int(li_pos)
    return int(block.close_start or block.close_end or block.open_end)


def _find_top_level_block_and_item_index_for_item_start(
    blocks: list[_MiniMdTopLevelListBlock],
    item_raw_start: int,
) -> tuple[Optional[_MiniMdTopLevelListBlock], int]:
    want = int(item_raw_start or 0)
    for blk in list(blocks or []):
        for idx, raw_start in enumerate(list(blk.item_starts or [])):
            if int(raw_start) == want:
                return blk, int(idx)
    return None, -1


def _pick_html_top_level_list_block_for_minimd_block(
    block_key: str,
    baseline_old_minimd: str,
    baseline_html: str,
    minimd_block: _MiniMdTopLevelListBlock,
) -> Optional[_HtmlTopLevelListBlock]:
    approx_pos = _resolve_source_anchor_from_raw_offset(
        block_key,
        baseline_old_minimd,
        baseline_html,
        int(minimd_block.raw_start),
    )
    return _pick_html_top_level_list_block(
        baseline_html,
        str(minimd_block.list_kind or ""),
        int(approx_pos),
    )


def _find_direct_li_open_for_top_level_item_start(
    block_key: str,
    baseline_old_minimd: str,
    baseline_html: str,
    list_kind: str,
    item_raw_start: int,
) -> int:
    minimd_blocks = [
        blk
        for blk in _collect_top_level_list_blocks(baseline_old_minimd)
        if str(blk.list_kind or "") == str(list_kind or "")
    ]
    minimd_block, item_idx = _find_top_level_block_and_item_index_for_item_start(
        minimd_blocks,
        int(item_raw_start),
    )
    if minimd_block is None or int(item_idx) < 0:
        approx_pos = _resolve_source_anchor_from_raw_offset(
            block_key,
            baseline_old_minimd,
            baseline_html,
            int(item_raw_start),
        )
        html_block = _pick_html_top_level_list_block(
            baseline_html,
            str(list_kind or ""),
            int(approx_pos),
        )
        if html_block is None:
            return int(approx_pos)
        return _find_direct_li_open_in_block_at_or_after(
            html_block,
            int(approx_pos),
        )

    html_block = _pick_html_top_level_list_block_for_minimd_block(
        block_key,
        baseline_old_minimd,
        baseline_html,
        minimd_block,
    )
    if html_block is None:
        approx_pos = _resolve_source_anchor_from_raw_offset(
            block_key,
            baseline_old_minimd,
            baseline_html,
            int(item_raw_start),
        )
        return int(approx_pos)

    li_opens = list(html_block.li_open_starts or [])
    if 0 <= int(item_idx) < len(li_opens):
        return int(li_opens[int(item_idx)])

    approx_pos = _resolve_source_anchor_from_raw_offset(
        block_key,
        baseline_old_minimd,
        baseline_html,
        int(item_raw_start),
    )
    return _find_direct_li_open_in_block_at_or_after(
        html_block,
        int(approx_pos),
    )


def _decide_level1_list_host_context(
    baseline_old_minimd: str,
    insert_raw_pos: int,
    insert_list_kind: str,
) -> dict[str, Any]:
    """
    Decide structural hosting context for one level-1 list insert from the
    MiniMD top-level block sequence.

    Modes:
      - append_same_top_level_before_close
      - prepend_same_top_level_at_open
      - start_new_top_level_block
    """
    blocks = _collect_top_level_list_blocks(baseline_old_minimd)
    enclosing_block, next_item_start = _find_enclosing_top_level_block_and_next_item(blocks, int(insert_raw_pos))
    prev_block, next_block = _find_neighbor_top_level_blocks(blocks, int(insert_raw_pos))
    new_kind = str(insert_list_kind or "").lower()

    if enclosing_block is not None and int(next_item_start or 0) > 0:
        current_kind = str(enclosing_block.list_kind or "")
        if current_kind == new_kind:
            return {
                "mode": "insert_into_existing_top_level_before_next_item",
                "prev_kind": str(current_kind),
                "prev_raw_start": int(enclosing_block.raw_start),
                "prev_raw_end": int(enclosing_block.raw_end),
                "next_kind": str(current_kind),
                "next_raw_start": int(next_item_start),
                "next_raw_end": int(enclosing_block.raw_end),
            }
        return {
            "mode": "split_existing_top_level_before_next_item",
            "prev_kind": str(current_kind),
            "prev_raw_start": int(enclosing_block.raw_start),
            "prev_raw_end": int(enclosing_block.raw_end),
            "next_kind": str(current_kind),
            "next_raw_start": int(next_item_start),
            "next_raw_end": int(enclosing_block.raw_end),
        }

    if prev_block is not None and str(prev_block.list_kind) == new_kind and int(prev_block.raw_end) <= int(insert_raw_pos):
        return {
            "mode": "append_same_top_level_before_close",
            "prev_kind": str(prev_block.list_kind),
            "prev_raw_start": int(prev_block.raw_start),
            "prev_raw_end": int(prev_block.raw_end),
            "next_kind": "",
            "next_raw_start": 0,
            "next_raw_end": 0,
        }

    if next_block is not None and str(next_block.list_kind) == new_kind and int(insert_raw_pos) <= int(next_block.raw_start):
        return {
            "mode": "prepend_same_top_level_at_open",
            "prev_kind": "",
            "prev_raw_start": 0,
            "prev_raw_end": 0,
            "next_kind": str(next_block.list_kind),
            "next_raw_start": int(next_block.raw_start),
            "next_raw_end": int(next_block.raw_end),
        }

    return {
        "mode": "start_new_top_level_block",
        "prev_kind": str(prev_block.list_kind) if prev_block is not None else "",
        "prev_raw_start": int(prev_block.raw_start) if prev_block is not None else 0,
        "prev_raw_end": int(prev_block.raw_end) if prev_block is not None else 0,
        "next_kind": str(next_block.list_kind) if next_block is not None else "",
        "next_raw_start": int(next_block.raw_start) if next_block is not None else 0,
        "next_raw_end": int(next_block.raw_end) if next_block is not None else 0,
    }


def _parse_meta_display_line(line: str) -> tuple[str, str, int] | None:
    match = _minimd_meta_line_re().match(str(line or ""))
    if not match:
        return None
    label = str(match.group("label") or "").strip()
    value = str(match.group("value") or "")
    value_start = str(line or "").find("$ ", str(line or "").find(": $"))
    if value_start >= 0:
        value_start += 2
    else:
        value_start = len(str(line or ""))
    return label, value, int(value_start)


def _render_meta_minimd_with_mapping(minimd_text: str) -> tuple[str, dict[int, int]]:
    src = _normalize_fragment_text(str(minimd_text or ""))
    st = _RenderState(src=src)
    st.mark(0)
    line_rows = _split_source_lines_with_offsets(src)
    first = True

    for line_start, line_end, newline_end, body, _line in line_rows:
        st.mark(line_start)
        if not first:
            st.emit_literal("<br>")
        first = False

        parsed = _parse_meta_display_line(body)
        if parsed is None:
            st.emit_text(body, line_start)
        else:
            label, value, rel_value_start = parsed
            field_attr = html.escape(str(label or ""), quote=True)
            st.emit_literal(f'<span class="kgg-meta-line" data-kgg-meta-field="{field_attr}">')
            st.emit_literal('<b class="kgg-meta-label">')
            st.emit_text(f"{label}:", line_start + 2)
            st.emit_literal("</b> ")
            st.emit_literal('<span class="kgg-meta-value">')
            st.emit_text(value, line_start + int(rel_value_start))
            st.emit_literal("</span></span>")

        st.mark(line_end)
        st.mark(newline_end)

    st.mark(len(src))
    return "".join(st.out), st.offset_map


def _render_minimd_with_mapping(block_key: str, minimd_text: str) -> tuple[str, dict[int, int]]:
    if str(block_key or "").strip().lower() == "meta":
        return _render_meta_minimd_with_mapping(str(minimd_text or ""))

    src = str(minimd_text or "").replace("\r\n", "\n").replace("\r", "\n")
    st = _RenderState(src=src)
    st.mark(0)
    line_rows = _split_source_lines_with_offsets(src)
    para_parts: list[tuple[str, int, bool]] = []

    def flush_para() -> None:
        nonlocal para_parts
        if not para_parts:
            return
        for part, part_start, has_newline in para_parts:
            _emit_plain_text_with_map(part, part_start, st)
            if has_newline:
                st.emit_literal("<br>")
        para_parts = []

    i = 0
    while i < len(line_rows):
        line_start, line_end, newline_end, body, line = line_rows[i]
        st.mark(line_start)

        stripped = body.strip()

        if stripped in {
            "### start: details ###",
            "### start: storybox ###",
            "### start: pre ###",
            "### start: table ###",
        }:
            flush_para()
            _close_all_lists(st)
            scope_name = stripped[len("### start: "):-len(" ###")]
            end_marker = f"### end: {scope_name} ###"
            scope_start = line_start
            inner_start = newline_end

            end_i: int | None = None
            for j in range(i + 1, len(line_rows)):
                _ls, _le, _ne, body_j, _line_j = line_rows[j]
                if body_j == end_marker:
                    end_i = j
                    break

            if end_i is None:
                para_parts.append((body, line_start, bool(newline_end > line_end)))
                st.mark(line_end)
                st.mark(newline_end)
                i += 1
                continue

            end_abs = line_rows[end_i][0]
            end_line_end = line_rows[end_i][2]
            inner_text = src[inner_start:end_abs]

            for off in range(scope_start, inner_start):
                st.mark(off)

            if scope_name == "details":
                inner_lines2 = inner_text.replace("\r\n", "\n").replace("\r", "\n").splitlines(keepends=True)
                summary_line = inner_lines2[0] if inner_lines2 else ""
                summary_body = summary_line[:-1] if summary_line.endswith("\n") else summary_line
                summary_text = summary_body[len("summary: "):] if summary_body.startswith("summary: ") else summary_body
                summary_raw_start = inner_start + (len("summary: ") if summary_body.startswith("summary: ") else 0)
                rest_inner_text = "".join(inner_lines2[1:])
                rest_inner_start = inner_start + len(summary_line)
                st.emit_literal("<details><summary>")
                _emit_plain_text_with_map(summary_text, summary_raw_start, st)
                st.emit_literal("</summary>")
                sub_html, sub_map = _render_minimd_with_mapping(block_key, rest_inner_text)
                html_base = len("".join(st.out))
                _merge_submapping(st, rest_inner_start, html_base, sub_map)
                st.emit_literal(sub_html)
                st.emit_literal("</details>")
            elif scope_name == "storybox":
                st.emit_literal('<blockquote class="story">')
                sub_html, sub_map = _render_minimd_with_mapping(block_key, inner_text)
                html_base = len("".join(st.out))
                _merge_submapping(st, inner_start, html_base, sub_map)
                st.emit_literal(sub_html)
                st.emit_literal("</blockquote>")
            elif scope_name == "pre":
                st.emit_literal("<pre>")
                st.emit_text(inner_text, inner_start)
                st.emit_literal("</pre>")
            elif scope_name == "table":
                _render_table_scope_with_mapping(block_key, inner_text, st, inner_start)

            for off in range(end_abs, end_line_end + 1):
                st.mark(off)
            i = end_i + 1
            continue

        if body.strip() == "":
            flush_para()
            _close_all_lists(st)
            st.emit_literal("<br>")
            st.mark(line_end)
            st.mark(newline_end)
            i += 1
            continue

        list_tok = _classify_minimd_list_token(body, line_start, line_end)
        if list_tok is not None and str(list_tok.kind) in {"ul1", "ol1"}:
            flush_para()
            _close_all_lists(st)
            next_i = _render_list_region_with_mapping(line_rows, i, st)
            if int(next_i) <= int(i):
                # Defensive progress guarantee: malformed/orphan list syntax must
                # never trap the renderer in an endless loop. Render the source
                # line as ordinary text and let syntax validation report the
                # structural problem separately.
                para_parts.append((body, line_start, bool(newline_end > line_end)))
                st.mark(line_end)
                st.mark(newline_end)
                i += 1
            else:
                i = int(next_i)
            continue

        para_parts.append((body, line_start, bool(newline_end > line_end)))
        st.mark(line_end)
        st.mark(newline_end)
        i += 1

    flush_para()
    _close_all_lists(st)
    st.mark(len(src))
    html_out = "".join(st.out)
    return html_out, st.offset_map


def minimd_to_html_text(block_key: str, minimd_text: str, *, config: Optional[IndiffConfigLike] = None) -> str:
    return _render_minimd_with_mapping(block_key, minimd_text)[0]


def canonicalize_html_for_compare(html_text: str) -> str:
    s = str(html_text or "").replace("\r\n", "\n").replace("\r", "\n")
    s = re.sub(r"(?i)<br\s*/?>", "<br>", s)
    s = re.sub(r">\s+<", "><", s)
    s = re.sub(r"\s+", " ", s)
    s = s.replace(" ><", "><")
    return s.strip()


# ---------------------------------------------------------------------------
# Canonical HTML -> MiniMD bridge
# ---------------------------------------------------------------------------


def _minimd_marker_start(name: str) -> str:
    tpl = "### start: {name} ###"
    if settings is not None:
        tpl = str(getattr(settings, "MINIMD_BLOCK_START_TEMPLATE", tpl) or tpl)
    return tpl.format(name=str(name))


def _minimd_marker_end(name: str) -> str:
    tpl = "### end: {name} ###"
    if settings is not None:
        tpl = str(getattr(settings, "MINIMD_BLOCK_END_TEMPLATE", tpl) or tpl)
    return tpl.format(name=str(name))


def minimd_block_order(*, config: Optional[IndiffConfigLike] = None) -> list[str]:
    """Return the canonical article block order without ORM/app.py dependencies."""
    cfg = config if config is not None else settings
    try:
        out = [str(x) for x in list(getattr(cfg, "MINIMD_BLOCKS_ORDER", []) or []) if str(x).strip()]
    except Exception:
        out = []
    return out or ["meta", "kurzinfo", "story", "einleitung", "juristisch", "juristisch2", "anmerkung"]


class _HtmlToMiniMdTokenParser(HTMLParser):
    """Tokenize the canonical HTML subset for deterministic block-local MiniMD import.

    This is the former app.py block converter moved into the canonical indiff
    library. It intentionally preserves its current behavior; syntax changes
    belong in a separate parser task.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tokens: list[dict[str, Any]] = []
        self._blockquote_story_stack: list[bool] = []
        self._token_index = 0

    def _push(self, typ: str, **payload: Any) -> None:
        row = {"token_index": int(self._token_index), "type": str(typ)}
        row.update(payload)
        self.tokens.append(row)
        self._token_index += 1

    def handle_starttag(self, tag: str, attrs: list[tuple[str, Optional[str]]]) -> None:
        t = str(tag or "").strip().lower()
        a = {str(k or "").lower(): ("" if v is None else str(v)) for k, v in list(attrs or [])}
        cls = set(x for x in str(a.get("class") or "").split() if x)
        simple = {"br", "b", "i", "code", "sub", "ul", "ol", "li", "details", "summary", "pre", "table", "thead", "tbody", "tr", "th", "td"}
        if t in simple:
            self._push(f"{t}_open" if t != "br" else "br")
            return
        if t == "a":
            self._push("a_open", href=str(a.get("href") or ""))
            return
        if t == "blockquote":
            is_story = "story" in cls
            self._blockquote_story_stack.append(bool(is_story))
            if is_story:
                self._push("blockquote_story_open")
            return
        if t == "p":
            self._push("br")
            self._push("br")

    def handle_endtag(self, tag: str) -> None:
        t = str(tag or "").strip().lower()
        simple = {"b", "i", "code", "sub", "a", "ul", "ol", "li", "details", "summary", "pre", "table", "thead", "tbody", "tr", "th", "td"}
        if t in simple:
            self._push(f"{t}_close")
            return
        if t == "blockquote":
            was_story = self._blockquote_story_stack.pop() if self._blockquote_story_stack else False
            if was_story:
                self._push("blockquote_story_close")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, Optional[str]]]) -> None:
        if str(tag or "").strip().lower() == "br":
            self._push("br")

    def handle_data(self, data: str) -> None:
        self._push("text", text=str(data or ""))


def html_to_minimd_text(block_key: str, html_text: str, *, config: Optional[IndiffConfigLike] = None) -> str:
    """Convert one canonical stored HTML block to MiniMD.

    ``meta`` is not HTML and must be passed through by callers instead of sent
    here. The function is ORM-free and safe for app.py, exporter and tools.
    """
    src = str(html_text or "")
    parser = _HtmlToMiniMdTokenParser()
    parser.feed(src)
    parser.close()
    tokens = list(parser.tokens)
    out: list[str] = []
    list_stack: list[str] = []
    href_stack: list[str] = []
    in_summary = False
    summary_buf: list[str] = []
    in_pre = False
    in_table = False
    table_rows: list[list[str]] = []
    table_row: list[str] | None = None
    table_cell: list[str] | None = None

    def emit(value: str) -> None:
        if value:
            out.append(value)

    def emit_summary(value: str) -> None:
        if value:
            summary_buf.append(value)

    def emit_text(value: str) -> None:
        if not value:
            return
        if in_summary:
            emit_summary(value)
        else:
            emit(value)

    for token in tokens:
        typ = str(token.get("type") or "")
        if typ == "text":
            txt = str(token.get("text") or "")
            if in_table:
                if table_cell is not None:
                    table_cell.append(txt)
            elif in_pre:
                emit_text(txt)
            else:
                txt = txt.replace("\r\n", "\n").replace("\r", "\n").replace("\n", " ")
                txt = re.sub(r"[ \t\f\v]+", " ", txt)
                emit_text(txt)
            continue
        if typ == "br":
            emit_summary(" ") if in_summary else emit("\n")
            continue
        if typ in {"b_open", "b_close"}:
            emit_text("**")
            continue
        if typ in {"i_open", "i_close"}:
            emit_text("*")
            continue
        if typ in {"code_open", "code_close"}:
            emit_text("`")
            continue
        if typ in {"sub_open", "sub_close"}:
            emit_text("~")
            continue
        if typ == "a_open":
            href_stack.append(str(token.get("href") or ""))
            emit_text("[")
            continue
        if typ == "a_close":
            href = href_stack.pop() if href_stack else ""
            emit_text(f"]({href})" if href else "]")
            continue
        if typ == "ul_open":
            list_stack.append("ul")
            continue
        if typ == "ol_open":
            list_stack.append("ol")
            continue
        if typ in {"ul_close", "ol_close"}:
            if list_stack:
                list_stack.pop()
            continue
        if typ == "li_open":
            if out and not out[-1].endswith("\n"):
                emit("\n")
            level = 1 if len(list_stack) <= 1 else 2
            kind = list_stack[-1] if list_stack else "ul"
            emit(("  " if level == 2 else "") + ("1. " if kind == "ol" else "- "))
            continue
        if typ == "li_close":
            if out and not out[-1].endswith("\n"):
                emit("\n")
            continue
        if typ == "details_open":
            if out and not out[-1].endswith("\n"):
                emit("\n")
            emit(_minimd_marker_start("details") + "\n")
            continue
        if typ == "details_close":
            if out and not out[-1].endswith("\n"):
                emit("\n")
            emit(_minimd_marker_end("details") + "\n")
            continue
        if typ == "summary_open":
            in_summary = True
            summary_buf = []
            continue
        if typ == "summary_close":
            in_summary = False
            summary_txt = "".join(summary_buf).replace("\r\n", "\n").replace("\r", "\n")
            summary_txt = re.sub(r"[ \t\f\v]+", " ", summary_txt)
            summary_txt = re.sub(r"\n+", " ", summary_txt).strip()
            emit(f"summary: {summary_txt}\n")
            summary_buf = []
            continue
        if typ == "blockquote_story_open":
            if out and not out[-1].endswith("\n"):
                emit("\n")
            emit(_minimd_marker_start("storybox") + "\n")
            continue
        if typ == "blockquote_story_close":
            if out and not out[-1].endswith("\n"):
                emit("\n")
            emit(_minimd_marker_end("storybox") + "\n")
            continue
        if typ == "pre_open":
            in_pre = True
            if out and not out[-1].endswith("\n"):
                emit("\n")
            emit(_minimd_marker_start("pre") + "\n")
            continue
        if typ == "pre_close":
            in_pre = False
            if out and not out[-1].endswith("\n"):
                emit("\n")
            emit(_minimd_marker_end("pre") + "\n")
            continue
        if typ == "table_open":
            in_table = True
            table_rows = []
            table_row = None
            table_cell = None
            if out and not out[-1].endswith("\n"):
                emit("\n")
            emit(_minimd_marker_start("table") + "\n")
            continue
        if typ == "tr_open" and in_table:
            table_row = []
            continue
        if typ in {"th_open", "td_open"} and in_table:
            table_cell = []
            continue
        if typ in {"th_close", "td_close"} and in_table:
            cell_txt = "".join(table_cell or []).replace("\r\n", "\n").replace("\r", "\n").replace("\n", " ")
            cell_txt = re.sub(r"[ \t\f\v]+", " ", cell_txt).strip()
            if table_row is not None:
                table_row.append(cell_txt)
            table_cell = None
            continue
        if typ == "tr_close" and in_table:
            if table_row is not None:
                table_rows.append(list(table_row))
            table_row = None
            continue
        if typ == "table_close":
            for row in table_rows:
                emit("| " + " | ".join(row) + " |\n")
            emit(_minimd_marker_end("table") + "\n")
            in_table = False
            table_rows = []
            table_row = None
            table_cell = None
            continue

    return "".join(out)


def content_blocks_to_minimd(
    content_blocks: Any,
    *,
    block_order: Optional[list[str]] = None,
    config: Optional[IndiffConfigLike] = None,
) -> tuple[str, list[str]]:
    """Build the canonical full-article MiniMD document from persisted blocks.

    DB contract:
    - ``meta`` is already MiniMD body text.
    - all other dict values are canonical stored HTML and are converted through
      :func:`html_to_minimd_text`.
    - legacy non-dict content is treated as ``juristisch`` HTML.
    """
    order = list(block_order or minimd_block_order(config=config))
    by_block: dict[str, str] = {str(k): "" for k in order}

    if isinstance(content_blocks, dict):
        for key in order:
            raw = content_blocks.get(key) or ""
            if str(key) == "meta":
                by_block[str(key)] = str(raw or "").replace("\r\n", "\n").replace("\r", "\n")
            else:
                by_block[str(key)] = html_to_minimd_text(str(key), str(raw or ""), config=config)
    else:
        raw = "" if content_blocks is None else str(content_blocks)
        if "juristisch" not in by_block:
            order.append("juristisch")
            by_block["juristisch"] = ""
        by_block["juristisch"] = html_to_minimd_text("juristisch", raw, config=config)

    return _minimd_document_from_blocks(order, by_block), order


# ---------------------------------------------------------------------------
# Change derivation
# ---------------------------------------------------------------------------

def _compact_change_window(old_text: str, new_text: str) -> dict[str, Any] | None:
    """Return the minimal middle window between old_text and new_text.

    This prevents difflib micro-matches inside one local patch part from
    fragmenting a product replace into many small spans.

    Contract:
    - common prefix is unchanged
    - common suffix is unchanged
    - old middle becomes at most one delete event
    - new middle becomes at most one insert event
    """
    a = str(old_text or "")
    b = str(new_text or "")
    if a == b:
        return None

    prefix = 0
    max_prefix = min(len(a), len(b))
    while prefix < max_prefix and a[prefix] == b[prefix]:
        prefix += 1

    suffix = 0
    max_suffix = min(len(a) - prefix, len(b) - prefix)
    while suffix < max_suffix and a[len(a) - 1 - suffix] == b[len(b) - 1 - suffix]:
        suffix += 1

    old_end = len(a) - suffix
    new_end = len(b) - suffix

    return {
        "old_start": int(prefix),
        "old_end": int(old_end),
        "new_start": int(prefix),
        "new_end": int(new_end),
        "old_text": a[prefix:old_end],
        "new_text": b[prefix:new_end],
    }


def _ordered_list_marker_agnostic_changes(old_text: str, new_text: str) -> list[dict[str, Any]] | None:
    """Return local changes while treating ordered-list numbers as generic.

    MiniMD ordered lists are semantic ordered lists. The concrete marker number
    ("1.", "2.", "3.", ...) must not by itself turn unchanged list items into
    delete+insert operations. This helper is intentionally used only in the
    article-compose expansion; persisted patch old_text/new_text stays exact.

    Returns:
      - None: not an ordered-list-only comparable fragment; use normal logic
      - []: applicable, but only marker numbers changed
      - list: real content changes inside ordered-list item bodies
    """
    old_src = _normalize_fragment_text(str(old_text or ""))
    new_src = _normalize_fragment_text(str(new_text or ""))
    old_rows = _split_source_lines_with_offsets(old_src)
    new_rows = _split_source_lines_with_offsets(new_src)
    if not old_rows or len(old_rows) != len(new_rows):
        return None

    out: list[dict[str, Any]] = []
    saw_ordered_item = False

    for old_row, new_row in zip(old_rows, new_rows):
        old_line_start, old_line_end, _old_newline_end, old_body, _old_full = old_row
        new_line_start, new_line_end, _new_newline_end, new_body, _new_full = new_row

        old_blank = not str(old_body or "").strip()
        new_blank = not str(new_body or "").strip()
        if old_blank or new_blank:
            if old_blank and new_blank:
                continue
            return None

        old_tok = _classify_minimd_list_token(str(old_body or ""), int(old_line_start), int(old_line_end))
        new_tok = _classify_minimd_list_token(str(new_body or ""), int(new_line_start), int(new_line_end))
        if old_tok is None or new_tok is None:
            return None

        if str(old_tok.kind) not in {"ol1", "ol2"} or str(new_tok.kind) not in {"ol1", "ol2"}:
            return None
        if str(old_tok.kind) != str(new_tok.kind):
            return None

        saw_ordered_item = True
        win = _compact_change_window(str(old_tok.text or ""), str(new_tok.text or ""))
        if win is None:
            continue

        old_mid = str(win.get("old_text") or "")
        new_mid = str(win.get("new_text") or "")
        if not old_mid and not new_mid:
            continue

        old_start = int(old_tok.content_raw_start) + int(win.get("old_start") or 0)
        old_end = int(old_tok.content_raw_start) + int(win.get("old_end") or 0)
        new_start = int(new_tok.content_raw_start) + int(win.get("new_start") or 0)
        new_end = int(new_tok.content_raw_start) + int(win.get("new_end") or 0)

        out.append({
            "change_kind": "replace" if old_mid and new_mid else ("delete" if old_mid else "insert"),
            "old_start": int(old_start),
            "old_end": int(old_end),
            "new_start": int(new_start),
            "new_end": int(new_end),
            "old_text": old_mid,
            "new_text": new_mid,
            "change_class": "inline_replace" if old_mid and new_mid else ("inline_delete" if old_mid else "inline_insert"),
        })

    if not saw_ordered_item:
        return None
    return out


def derive_block_semantic_changes(old_text: str, new_text: str, block_key: str, *, config: Optional[IndiffConfigLike] = None) -> list[dict[str, Any]]:
    win = _compact_change_window(str(old_text or ""), str(new_text or ""))
    if win is None:
        return []

    out: list[dict[str, Any]] = []
    old_mid = str(win.get("old_text") or "")
    new_mid = str(win.get("new_text") or "")

    if old_mid:
        out.append({
            "change_kind": "delete",
            "old_start": int(win.get("old_start") or 0),
            "old_end": int(win.get("old_end") or 0),
            "new_start": int(win.get("new_start") or 0),
            "new_end": int(win.get("new_start") or 0),
            "old_text": old_mid,
            "new_text": "",
        })

    if new_mid:
        out.append({
            "change_kind": "insert",
            "old_start": int(win.get("old_start") or 0),
            "old_end": int(win.get("old_start") or 0),
            "new_start": int(win.get("new_start") or 0),
            "new_end": int(win.get("new_end") or 0),
            "old_text": "",
            "new_text": new_mid,
        })

    return out


def _protected_minimd_marker_re() -> re.Pattern[str]:
    return re.compile(r"\$[^$\n]+\$")


def _protected_minimd_marker_spans(text: str) -> list[tuple[int, int, str]]:
    src = _normalize_fragment_text(str(text or ""))
    return [
        (int(m.start()), int(m.end()), str(m.group(0) or ""))
        for m in _protected_minimd_marker_re().finditer(src)
    ]


def _ranges_overlap(a_start: int, a_end: int, b_start: int, b_end: int) -> bool:
    return int(a_start) < int(b_end) and int(b_start) < int(a_end)


def _validate_protected_minimd_markers_against_change_events(
    *,
    old_text: str,
    new_text: str,
    block_key: str = "",
    part_id: str = "",
) -> list[dict[str, Any]]:
    """Reject patch changes that modify protected $...$ MiniMD markers.

    This is intentionally a patch-submission validator, not render logic:
    - derive_block_semantic_changes(...) remains the only Old/New event source
    - this helper only inspects those events
    - meta is not special-cased
    """
    old_src = _normalize_fragment_text(str(old_text or ""))
    new_src = _normalize_fragment_text(str(new_text or ""))
    old_markers = _protected_minimd_marker_spans(old_src)
    marker_re = _protected_minimd_marker_re()
    errors: list[dict[str, Any]] = []

    if not old_markers and not marker_re.search(new_src):
        return []

    for ev in derive_block_semantic_changes(old_src, new_src, str(block_key or "")):
        kind = str(ev.get("change_kind") or "")
        old_start = int(ev.get("old_start") or 0)
        old_end = int(ev.get("old_end") or old_start)
        new_text_ev = str(ev.get("new_text") or "")

        if kind == "delete":
            touched = [
                {"start": int(s), "end": int(e), "text": str(t)}
                for s, e, t in old_markers
                if _ranges_overlap(old_start, old_end, int(s), int(e))
            ]
            if touched:
                errors.append(
                    {
                        "code": "protected_minimd_marker_changed",
                        "part_id": str(part_id or ""),
                        "block_key": str(block_key or ""),
                        "reason": "change-overlaps-protected-$...$-marker",
                        "markers": touched,
                    }
                )
                continue

        if kind == "insert" and marker_re.search(new_text_ev):
            errors.append(
                {
                    "code": "protected_minimd_marker_changed",
                    "part_id": str(part_id or ""),
                    "block_key": str(block_key or ""),
                    "reason": "change-introduces-protected-$...$-marker",
                    "inserted_text": new_text_ev,
                }
            )

    return errors


def _derive_fragment_list_parent_hint(
    inherited_parent_hint: str,
    frag: dict[str, Any],
    current_inserted_level1_hint: str,
) -> tuple[str, str]:
    """
    Derive per-fragment parent hints after article-compose insert splitting.

    Why this exists:
    - one coarse insert payload may split into:
        ul2, ol1, ol2, ul1
    - later level-2 fragments must be able to target a newly inserted
      level-1 parent from the SAME split stream
    - the old coarse ChangeRequest parent_hint is not enough for that
    """
    inherited = str(inherited_parent_hint or "")
    current_parent = str(current_inserted_level1_hint or "")
    change_class = str(frag.get("change_class") or "")
    new_text = str(frag.get("new_text") or "")
    list_kind = str(frag.get("list_kind") or "").lower()
    list_level = int(frag.get("list_level") or 0)

    if not list_kind or list_level <= 0:
        is_list, detected_kind, detected_level, _body = _detect_list_insert(new_text)
        if is_list:
            list_kind = str(detected_kind or "").lower()
            list_level = int(detected_level or 0)

    if change_class == "list_item_insert" and int(list_level) == 1:
        next_parent = " ".join(_strip_list_marker_prefix(new_text).split()).strip()
        return inherited, str(next_parent or current_parent)

    if change_class in {"list_item_insert", "list_continuation_insert"} and int(list_level) == 2:
        if current_parent:
            return current_parent, current_parent
        return inherited, current_parent

    return inherited, current_parent


def _coarse_article_inline_thresholds() -> dict[str, float]:
    """Thresholds for switching article inline rendering to coarse replace mode.

    The values are deliberately conservative-low for the start phase: large
    LLM/article rewrites should be readable as one delete + one insert instead
    of being exploded into many overlapping local operations.
    """
    raw = None
    if settings is not None:
        raw = getattr(settings, "ARTICLE_INLINE_COARSE_DIFF", None)
    if not isinstance(raw, dict):
        raw = {}

    def num(key: str, default: float) -> float:
        try:
            return float(raw.get(key, default))
        except Exception:
            return float(default)

    return {
        "min_old_len": num("min_old_len", 700),
        "min_new_len": num("min_new_len", 700),
        "hard_selected_ratio": num("hard_selected_ratio", 0.60),
        "soft_selected_ratio": num("soft_selected_ratio", 0.35),
        "min_total_len": num("min_total_len", 1800),
        "low_similarity": num("low_similarity", 0.78),
    }


def _article_inline_coarse_reason(ch: ChangeRequest, baseline_old_minimd: str) -> str:
    """Return a reason when article inline compose should use coarse replace.

    This is article-view policy only. Persisted patches and validation stay exact;
    comment cards may still show the detailed part data.
    """
    baseline_len = len(str(baseline_old_minimd or ""))
    old_len = len(str(ch.old_text or ""))
    new_len = len(str(ch.new_text or ""))
    if baseline_len <= 0 or old_len <= 0 or new_len <= 0:
        return ""

    t = _coarse_article_inline_thresholds()
    selected_ratio = old_len / float(max(1, baseline_len))
    total_len = old_len + new_len

    if (
        old_len >= int(t["min_old_len"])
        and new_len >= int(t["min_new_len"])
        and selected_ratio >= float(t["hard_selected_ratio"])
    ):
        return "large_block_replace"

    if total_len >= int(t["min_total_len"]) and selected_ratio >= float(t["soft_selected_ratio"]):
        try:
            similarity = difflib.SequenceMatcher(
                a=str(ch.old_text or ""),
                b=str(ch.new_text or ""),
                autojunk=False,
            ).ratio()
        except Exception:
            similarity = 1.0
        if float(similarity) < float(t["low_similarity"]):
            return "large_low_similarity_replace"

    return ""


def _coarse_article_inline_changes(ch: ChangeRequest, reason: str) -> list[ChangeRequest]:
    reason_suffix = str(reason or "coarse")
    base_id = str(ch.change_id)
    return [
        ChangeRequest(
            change_id=f"{base_id}:coarse_del",
            cid=int(ch.cid),
            block_key=str(ch.block_key),
            old_text=str(ch.old_text or ""),
            new_text="",
            old_start=int(ch.old_start),
            old_end=int(ch.old_end),
            context_before=str(ch.context_before or ""),
            context_after=str(ch.context_after or ""),
            list_parent_hint=str(ch.list_parent_hint or ""),
            change_class=f"coarse_inline_delete:{reason_suffix}",
        ),
        ChangeRequest(
            change_id=f"{base_id}:coarse_ins",
            cid=int(ch.cid),
            block_key=str(ch.block_key),
            old_text="",
            new_text=str(ch.new_text or ""),
            old_start=int(ch.old_start),
            old_end=int(ch.old_start),
            context_before=str(ch.context_before or ""),
            context_after=str(ch.context_after or ""),
            list_parent_hint=str(ch.list_parent_hint or ""),
            change_class=f"coarse_inline_insert:{reason_suffix}",
        ),
    ]


def _expand_changes_for_article_compose(
    changes: list[ChangeRequest],
    baseline_old_minimd: str,
) -> list[ChangeRequest]:
    """
    Expand coarse replace-style persisted parts into local article-compose changes
    while leaving comment-card source truth unchanged.

    Important:
    - only the article compose path uses this expansion
    - comment cards still receive the original coarse persisted parts
    - structured change classes must pass through unchanged
    """
    out: list[ChangeRequest] = []
    structured_classes = {
        "list_item_insert",
        "list_item_delete",
        "list_continuation_insert",
        "list_continuation_delete",
        "table_row_insert",
        "table_row_delete",
        "pre_inline_insert",
        "pre_inline_delete",
        "table_cell_inline_insert",
    }

    for ch in list(changes or []):
        change_class = str(ch.change_class or "")
        old_text = str(ch.old_text or "")
        new_text = str(ch.new_text or "")

        coarse_reason = _article_inline_coarse_reason(ch, str(baseline_old_minimd or ""))
        if coarse_reason:
            out.extend(_coarse_article_inline_changes(ch, coarse_reason))
            continue

        if change_class in structured_classes:
            out.append(ch)
            continue

        if old_text and not new_text:
            delete_frags = _split_article_delete_fragments(str(old_text))
            if not delete_frags:
                out.append(ch)
                continue
            local_seq = 0
            for frag in list(delete_frags or []):
                local_seq += 1
                rel_start = int(frag.get("rel_start") or 0)
                rel_end = int(frag.get("rel_end") or 0)
                out.append(
                    ChangeRequest(
                        change_id=f"{str(ch.change_id)}:m{int(local_seq)}",
                        cid=int(ch.cid),
                        block_key=str(ch.block_key),
                        old_text=str(frag.get("old_text") or ""),
                        new_text="",
                        old_start=int(ch.old_start) + int(rel_start),
                        old_end=int(ch.old_start) + int(rel_end),
                        context_before=str(ch.context_before or ""),
                        context_after=str(ch.context_after or ""),
                        list_parent_hint=str(ch.list_parent_hint or ""),
                        change_class=str(frag.get("change_class") or "inline_delete"),
                        list_host_mode=str(ch.list_host_mode or ""),
                        list_prev_block_kind=str(ch.list_prev_block_kind or ""),
                        list_prev_block_raw_start=int(ch.list_prev_block_raw_start or 0),
                        list_prev_block_raw_end=int(ch.list_prev_block_raw_end or 0),
                        list_next_block_kind=str(ch.list_next_block_kind or ""),
                        list_next_block_raw_start=int(ch.list_next_block_raw_start or 0),
                        list_next_block_raw_end=int(ch.list_next_block_raw_end or 0),
                    )
                )
            continue

        if new_text and not old_text:
            insert_frags = _split_article_insert_fragments(str(new_text))
            if not insert_frags:
                out.append(ch)
                continue
            local_seq = 0
            current_inserted_level1_hint = ""
            for frag in list(insert_frags or []):
                local_seq += 1
                frag_parent_hint, current_inserted_level1_hint = _derive_fragment_list_parent_hint(
                    str(ch.list_parent_hint or ""),
                    frag,
                    current_inserted_level1_hint,
                )
                host_ctx = _derive_level1_list_host_ctx_for_insert_fragment(
                    str(baseline_old_minimd or ""),
                    frag,
                    int(ch.old_start),
                )
                out.append(
                    ChangeRequest(
                        change_id=f"{str(ch.change_id)}:m{int(local_seq)}",
                        cid=int(ch.cid),
                        block_key=str(ch.block_key),
                        old_text="",
                        new_text=str(frag.get("new_text") or ""),
                        old_start=int(ch.old_start),
                        old_end=int(ch.old_start),
                        context_before=str(ch.context_before or ""),
                        context_after=str(ch.context_after or ""),
                        list_parent_hint=str(frag_parent_hint or ""),
                        change_class=str(frag.get("change_class") or "inline_insert"),
                        list_host_mode=str(host_ctx.get("mode") or ch.list_host_mode or ""),
                        list_prev_block_kind=str(host_ctx.get("prev_kind") or ch.list_prev_block_kind or ""),
                        list_prev_block_raw_start=int(host_ctx.get("prev_raw_start") or ch.list_prev_block_raw_start or 0),
                        list_prev_block_raw_end=int(host_ctx.get("prev_raw_end") or ch.list_prev_block_raw_end or 0),
                        list_next_block_kind=str(host_ctx.get("next_kind") or ch.list_next_block_kind or ""),
                        list_next_block_raw_start=int(host_ctx.get("next_raw_start") or ch.list_next_block_raw_start or 0),
                        list_next_block_raw_end=int(host_ctx.get("next_raw_end") or ch.list_next_block_raw_end or 0),
                    )
                )
            continue

        if not old_text or not new_text:
            out.append(ch)
            continue

        marker_agnostic_local = _ordered_list_marker_agnostic_changes(old_text, new_text)
        if marker_agnostic_local is None:
            local = derive_block_semantic_changes(old_text, new_text, str(ch.block_key))
            if not local:
                out.append(ch)
                continue
        else:
            local = marker_agnostic_local
            if not local:
                # Only ordered-list marker numbers changed; article-inline diff is empty.
                continue

        local_seq = 0
        for ev in list(local or []):
            ev_old = str(ev.get("old_text") or "")
            ev_new = str(ev.get("new_text") or "")
            ev_old_start = int(ev.get("old_start") or 0)
            ev_old_end = int(ev.get("old_end") or 0)
            if not ev_old and not ev_new:
                continue
            if ev_old and not ev_new:
                delete_frags = _split_article_delete_fragments(str(ev_old))
                for frag in list(delete_frags or []):
                    local_seq += 1
                    rel_start = int(frag.get("rel_start") or 0)
                    rel_end = int(frag.get("rel_end") or 0)
                    out.append(
                        ChangeRequest(
                            change_id=f"{str(ch.change_id)}:m{int(local_seq)}",
                            cid=int(ch.cid),
                            block_key=str(ch.block_key),
                            old_text=str(frag.get("old_text") or ""),
                            new_text="",
                            old_start=int(ch.old_start) + int(ev_old_start) + int(rel_start),
                            old_end=int(ch.old_start) + int(ev_old_start) + int(rel_end),
                            context_before=str(ch.context_before or ""),
                            context_after=str(ch.context_after or ""),
                            list_parent_hint=str(ch.list_parent_hint or ""),
                            change_class=str(frag.get("change_class") or "inline_delete"),
                            list_host_mode=str(ch.list_host_mode or ""),
                            list_prev_block_kind=str(ch.list_prev_block_kind or ""),
                            list_prev_block_raw_start=int(ch.list_prev_block_raw_start or 0),
                            list_prev_block_raw_end=int(ch.list_prev_block_raw_end or 0),
                            list_next_block_kind=str(ch.list_next_block_kind or ""),
                            list_next_block_raw_start=int(ch.list_next_block_raw_start or 0),
                            list_next_block_raw_end=int(ch.list_next_block_raw_end or 0),
                        )
                    )
                continue
            if ev_new and not ev_old:
                insert_frags = _split_article_insert_fragments(str(ev_new))
                current_inserted_level1_hint = ""
                for frag in list(insert_frags or []):
                    local_seq += 1
                    frag_parent_hint, current_inserted_level1_hint = _derive_fragment_list_parent_hint(
                        str(ch.list_parent_hint or ""),
                        frag,
                        current_inserted_level1_hint,
                    )
                    host_ctx = _derive_level1_list_host_ctx_for_insert_fragment(
                        str(baseline_old_minimd or ""),
                        frag,
                        int(ch.old_start) + int(ev_old_start),
                    )
                    out.append(
                        ChangeRequest(
                            change_id=f"{str(ch.change_id)}:m{int(local_seq)}",
                            cid=int(ch.cid),
                            block_key=str(ch.block_key),
                            old_text="",
                            new_text=str(frag.get("new_text") or ""),
                            old_start=int(ch.old_start) + int(ev_old_start),
                            old_end=int(ch.old_start) + int(ev_old_start),
                            context_before=str(ch.context_before or ""),
                            context_after=str(ch.context_after or ""),
                            list_parent_hint=str(frag_parent_hint or ""),
                            change_class=str(frag.get("change_class") or "inline_insert"),
                            list_host_mode=str(host_ctx.get("mode") or ch.list_host_mode or ""),
                            list_prev_block_kind=str(host_ctx.get("prev_kind") or ch.list_prev_block_kind or ""),
                            list_prev_block_raw_start=int(host_ctx.get("prev_raw_start") or ch.list_prev_block_raw_start or 0),
                            list_prev_block_raw_end=int(host_ctx.get("prev_raw_end") or ch.list_prev_block_raw_end or 0),
                            list_next_block_kind=str(host_ctx.get("next_kind") or ch.list_next_block_kind or ""),
                            list_next_block_raw_start=int(host_ctx.get("next_raw_start") or ch.list_next_block_raw_start or 0),
                            list_next_block_raw_end=int(host_ctx.get("next_raw_end") or ch.list_next_block_raw_end or 0),
                        )
                    )
                continue
            local_seq += 1
            out.append(
                ChangeRequest(
                    change_id=f"{str(ch.change_id)}:m{int(local_seq)}",
                    cid=int(ch.cid),
                    block_key=str(ch.block_key),
                    old_text=str(ev_old),
                    new_text=str(ev_new),
                    old_start=int(ch.old_start) + int(ev_old_start),
                    old_end=int(ch.old_start) + int(ev_old_end),
                    context_before=str(ch.context_before or ""),
                    context_after=str(ch.context_after or ""),
                    list_parent_hint=str(ch.list_parent_hint or ""),
                    change_class=str(ev.get("change_class") or ("inline_delete" if ev_old and not ev_new else "inline_insert")),
                    list_host_mode=str(ch.list_host_mode or ""),
                    list_prev_block_kind=str(ch.list_prev_block_kind or ""),
                    list_prev_block_raw_start=int(ch.list_prev_block_raw_start or 0),
                    list_prev_block_raw_end=int(ch.list_prev_block_raw_end or 0),
                    list_next_block_kind=str(ch.list_next_block_kind or ""),
                    list_next_block_raw_start=int(ch.list_next_block_raw_start or 0),
                    list_next_block_raw_end=int(ch.list_next_block_raw_end or 0),
                )
            )
    return out


def _split_article_delete_fragments(old_text: str) -> list[dict[str, Any]]:
    """
    Split one local delete fragment for article-compose into safer structured
    subfragments.

    Main purpose:
    - avoid inline delete_ranges that accidentally span across list-item seams
    - preserve current comment-card truth (this is article-compose only)
    """
    src = str(old_text or "")
    if not src:
        return []

    rows = _iter_minimd_lines_with_offsets(src)
    if len(rows) <= 1:
        return [{
            "change_class": "inline_delete",
            "old_text": str(src),
            "rel_start": 0,
            "rel_end": len(src),
            "list_kind": "",
            "list_level": 0,
        }]

    out: list[dict[str, Any]] = []
    saw_list_delete = False

    def _is_semantically_empty_list_token_text(text: str) -> bool:
        s = re.sub(r"[*_`~\s]+", "", str(text or ""))
        return s == ""

    for row_start, row_end, body in rows:
        tok = _classify_minimd_list_token(body, row_start, row_end)
        if tok is not None:
            token_text = str(tok.text or "")
            if _is_semantically_empty_list_token_text(token_text):
                continue
            if str(tok.kind) in {"cont1", "cont2"}:
                frag_class = "list_continuation_delete"
                frag_kind = "ul"
                frag_level = 2 if str(tok.kind) == "cont2" else 1
            else:
                frag_class = "list_item_delete"
                frag_kind = "ol" if str(tok.kind) in {"ol1", "ol2"} else "ul"
                frag_level = 2 if str(tok.kind) in {"ul2", "ol2"} else 1
            out.append({
                "change_class": str(frag_class),
                "old_text": str(token_text),
                "rel_start": int(tok.content_raw_start),
                "rel_end": int(tok.raw_end),
                "list_kind": str(frag_kind),
                "list_level": int(frag_level),
            })
            saw_list_delete = True
            continue

        body_text = str(body or "")
        if not body_text:
            continue
        out.append({
            "change_class": "inline_delete",
            "old_text": body_text,
            "rel_start": int(row_start),
            "rel_end": int(row_end),
            "list_kind": "",
            "list_level": 0,
        })

    if saw_list_delete:
        trimmed: list[dict[str, Any]] = []
        for frag in out:
            if (
                str(frag.get("change_class") or "") == "inline_delete"
                and str(frag.get("old_text") or "").strip() in {".", ":", ";", "!", "?", "**", "*", "`", "~", "_", "__"}
            ):
                continue
            trimmed.append(frag)
        if trimmed:
            out = trimmed

    return out


def _split_article_insert_fragments(new_text: str) -> list[dict[str, Any]]:
    """
    Split one coarse insert payload into article-compose fragments that preserve
    structural list/table inserts as separate change requests.

    Main purpose:
    - every inserted list entry becomes its own ChangeRequest
    - every inserted continuation becomes its own ChangeRequest
    - inline leftovers remain explicit inline_insert fragments
    """
    src = str(new_text or "")
    if not src:
        return []

    fragments = list(_split_structured_insert_fragments(src) or [])
    if not fragments:
        return [{
            "change_class": "inline_insert",
            "new_text": str(src),
        }]

    out: list[dict[str, Any]] = []
    for frag in fragments:
        text = str(frag.text or "")
        if not text:
            continue
        if str(frag.change_class or "inline_insert") == "inline_insert" and not str(text).strip():
            continue
        out.append({
            "change_class": str(frag.change_class or "inline_insert"),
            "new_text": str(text),
            "list_kind": str(frag.list_kind or ""),
            "list_level": int(frag.list_level or 0),
        })
    return out


def _derive_level1_list_host_ctx_for_insert_fragment(
    baseline_old_minimd: str,
    frag: dict[str, Any],
    old_start: int,
) -> dict[str, Any]:
    """
    Recompute level-1 structural host context per already-split insert fragment.

    Important:
    - only level-1 list_item_insert fragments need a top-level list host mode
    - level-2 items keep their later parent-li resolution path
    """
    change_class = str(frag.get("change_class") or "")
    list_kind = str(frag.get("list_kind") or "").lower()
    list_level = int(frag.get("list_level") or 0)

    if change_class != "list_item_insert":
        return {
            "mode": "",
            "prev_kind": "",
            "prev_raw_start": 0,
            "prev_raw_end": 0,
            "next_kind": "",
            "next_raw_start": 0,
            "next_raw_end": 0,
        }

    if not list_kind or list_level <= 0:
        _is_list, detected_kind, detected_level, _body = _detect_list_insert(str(frag.get("new_text") or ""))
        list_kind = str(detected_kind or "").lower()
        list_level = int(detected_level or 0)

    if list_level != 1 or list_kind not in {"ul", "ol"}:
        return {
            "mode": "",
            "prev_kind": "",
            "prev_raw_start": 0,
            "prev_raw_end": 0,
            "next_kind": "",
            "next_raw_start": 0,
            "next_raw_end": 0,
        }

    return _decide_level1_list_host_context(
        str(baseline_old_minimd or ""),
        int(old_start),
        str(list_kind),
    )


def _normalize_external_block_key(raw_block_key: str, available_block_keys: set[str], fallback_block_key: str) -> str:
    raw = str(raw_block_key or "").strip()
    if raw in available_block_keys:
        return raw
    return str(fallback_block_key or "juristisch")


def _find_exact_old_text_slice(base_text: str, old_text: str, start_hint: int = 0) -> tuple[int, int] | None:
    """
    Exact substring locate used for persisted patch parts.

    This intentionally stays strict:
    - no fuzzy matching
    - no normalization
    - old_text must be found literally
    """
    hay = str(base_text or "")
    needle = str(old_text or "")
    if not needle:
        return None
    s0 = max(0, int(start_hint or 0))
    pos = hay.find(needle, s0)
    if pos < 0 and s0 > 0:
        pos = hay.find(needle, 0)
    if pos < 0:
        return None
    return int(pos), int(len(needle))


def _change_request_from_persisted_part(
    *,
    cid: int,
    part: PersistedCommentPartInput,
    block_key: str,
    baseline_old_minimd: str,
) -> tuple[ChangeRequest | None, dict[str, Any] | None]:
    """
    Convert one persisted patch part into one product ChangeRequest.

    Priority:
    1. exact sel_start/sel_end if present
    2. exact old_text locate in baseline_old_minimd
    3. pure insert fallback at 0 when old_text is empty
    """
    part_id = str(part.part_id or "p?")
    old_text = str(part.old_text or "")
    new_text = str(part.new_text or "")

    if part.sel_start is not None and part.sel_end is not None:
        try:
            s = max(0, min(len(baseline_old_minimd), int(part.sel_start)))
            e = max(s, min(len(baseline_old_minimd), int(part.sel_end)))
            return (
                ChangeRequest(
                    change_id=f"c{int(cid):03d}_{part_id}",
                    cid=int(cid),
                    block_key=str(block_key),
                    old_text=str(old_text),
                    new_text=str(new_text),
                    old_start=int(s),
                    old_end=int(e),
                ),
                None,
            )
        except Exception:
            pass

    if old_text:
        loc = _find_exact_old_text_slice(str(baseline_old_minimd or ""), str(old_text), 0)
        if loc is None:
            return None, {
                "cid": int(cid),
                "part_id": str(part_id),
                "block_key": str(block_key),
                "reason": "EXACT_OLD_TEXT_NOT_FOUND",
                "old_preview": str(old_text[:160]),
                "new_preview": str(new_text[:160]),
            }
        p0, ln = loc
        s = int(p0)
        e = int(p0 + max(0, ln))
        return (
            ChangeRequest(
                change_id=f"c{int(cid):03d}_{part_id}",
                cid=int(cid),
                block_key=str(block_key),
                old_text=str(old_text),
                new_text=str(new_text),
                old_start=int(s),
                old_end=int(e),
            ),
            None,
        )

    return (
        ChangeRequest(
            change_id=f"c{int(cid):03d}_{part_id}",
            cid=int(cid),
            block_key=str(block_key),
            old_text="",
            new_text=str(new_text),
            old_start=0,
            old_end=0,
        ),
        None,
    )


def build_change_requests_from_patch_parts(
    *,
    baseline_old_minimd_by_block: dict[str, str],
    comments: list[PersistedCommentInput],
    fallback_block_key: str = "juristisch",
) -> tuple[dict[str, list[ChangeRequest]], list[dict[str, Any]], list[dict[str, Any]]]:
    """
    Public product API:
    convert persisted patch_payload.parts into block-local ChangeRequests.

    Returns:
      - block_change_requests
      - applied_parts
      - locate_failures
    """
    available_block_keys = {str(k) for k in dict(baseline_old_minimd_by_block or {}).keys()}
    out: dict[str, list[ChangeRequest]] = {str(k): [] for k in available_block_keys}
    applied_parts: list[dict[str, Any]] = []
    locate_failures: list[dict[str, Any]] = []

    for comment in list(comments or []):
        cid = int(comment.cid)
        for idx, part in enumerate(list(comment.parts or [])):
            part_id = str(part.part_id or f"p{idx+1}")
            bk = _normalize_external_block_key(str(part.block_key or ""), available_block_keys, str(fallback_block_key))
            baseline_old_minimd = str((baseline_old_minimd_by_block or {}).get(str(bk)) or "")
            ch, fail = _change_request_from_persisted_part(
                cid=int(cid),
                part=part,
                block_key=str(bk),
                baseline_old_minimd=str(baseline_old_minimd),
            )
            if fail is not None:
                locate_failures.append(dict(fail))
                applied_parts.append({
                    "cid": int(cid),
                    "part_id": str(part_id),
                    "status": "unapplied",
                    "reason": str(fail.get("reason") or "UNRESOLVED"),
                })
                continue
            assert ch is not None
            out.setdefault(str(bk), []).append(ch)
            applied_parts.append({
                "cid": int(cid),
                "part_id": str(part_id),
                "status": "applied",
            })

    return out, applied_parts, locate_failures


def _normalize_fragment_text(text: str) -> str:
    return str(text or "").replace("\r\n", "\n").replace("\r", "\n")


def _sha256_hex_text(text: str) -> str:
    return hashlib.sha256(str(text or "").encode("utf-8")).hexdigest()


def _is_obviously_invalid_list_line(line: str, config: Optional[IndiffConfigLike] = None) -> bool:
    s = str(line or "")
    if not s.strip():
        return False
    t = s.lstrip(" ")
    if re.match(r"^-\S", t):
        return True
    if re.match(r"^\d+\.\S", t):
        return True
    return False


def _minimd_meta_fields(config: Optional[IndiffConfigLike] = None) -> list[str]:
    """Meta-Feld-Whitelist ausschließlich aus config/settings lesen.

    Keine projektspezifischen Label-Fallbacks in indiff.py:
    Die erlaubten Labels gehören in config.py.
    Wenn keine Liste konfiguriert ist, wird fail-closed validiert.
    """
    raw = None
    if config is not None:
        raw = getattr(config, "MINIMD_META_FIELDS", None)
    if raw is None and settings is not None:
        raw = getattr(settings, "MINIMD_META_FIELDS", None)
    if isinstance(raw, (list, tuple)):
        return [str(x).strip() for x in raw if str(x or "").strip()]
    return []


def _minimd_meta_line_re(config: Optional[IndiffConfigLike] = None) -> re.Pattern[str]:
    raw = None
    if config is not None:
        raw = getattr(config, "MINIMD_META_LINE_RE", None)
    if raw is None and settings is not None:
        raw = getattr(settings, "MINIMD_META_LINE_RE", "")
    pattern = str(raw or r"^\$ (?P<label>[^$:\n]+): \$ ?(?P<value>.*)$")
    try:
        return re.compile(pattern)
    except Exception:
        return re.compile(r"^\$ (?P<label>[^$:\n]+): \$ ?(?P<value>.*)$")


def _validate_minimd_meta_block_lines(
    lines: list[tuple[int, str]],
    *,
    config: Optional[IndiffConfigLike] = None,
) -> list[dict[str, Any]]:
    errors: list[dict[str, Any]] = []
    allowed = _minimd_meta_fields(config=config)
    allowed_set = set(allowed)
    seen: dict[str, int] = {}
    line_re = _minimd_meta_line_re(config=config)

    for line_no, raw in list(lines or []):
        line = str(raw or "")
        if not line.strip():
            continue
        match = line_re.match(line)
        if not match:
            errors.append(
                {
                    "code": "invalid_meta_line_syntax",
                    "line_no": int(line_no),
                    "line": line,
                }
            )
            continue
        label = str(match.group("label") or "").strip()
        if label not in allowed_set:
            errors.append(
                {
                    "code": "unknown_meta_label",
                    "line_no": int(line_no),
                    "label": label,
                }
            )
            continue
        if label in seen:
            errors.append(
                {
                    "code": "duplicate_meta_label",
                    "line_no": int(line_no),
                    "label": label,
                    "first_line_no": int(seen[label]),
                }
            )
            continue
        seen[label] = int(line_no)

    return errors


def normalize_comment_patch_payload(
    *,
    comment: PersistedCommentInput,
    fallback_block_key: str = "juristisch",
) -> PersistedCommentInput:
    out_parts: list[PersistedCommentPartInput] = []
    for idx, p in enumerate(list(comment.parts or [])):
        part_id = str(p.part_id or f"p{idx+1}")
        block_key = str(p.block_key or fallback_block_key or "juristisch")
        old_text = _normalize_fragment_text(str(p.old_text or ""))
        new_text = _normalize_fragment_text(str(p.new_text or ""))
        if not old_text and not new_text:
            continue
        out_parts.append(
            PersistedCommentPartInput(
                part_id=part_id,
                block_key=block_key,
                old_text=old_text,
                new_text=new_text,
                sel_start=(int(p.sel_start) if p.sel_start is not None else None),
                sel_end=(int(p.sel_end) if p.sel_end is not None else None),
                baseline_hash=str(getattr(p, "baseline_hash", "") or ""),
            )
        )
    return PersistedCommentInput(
        cid=int(comment.cid),
        parts=list(out_parts),
    )


def validate_exact_selection_patch_payload(
    *,
    baseline_old_minimd_by_block: dict[str, str],
    comment: PersistedCommentInput,
    fallback_block_key: str = "juristisch",
    require_baseline_hash: bool = True,
    require_selection: bool = True,
) -> CommentExactSelectionValidationResult:
    """
    Strict exact-selection validator for persisted patch parts.

    Purpose:
    - keep exact-selection / stale-baseline protection in the library
    - app.py remains only an HTTP/DB orchestrator
    """
    normalized_comment = normalize_comment_patch_payload(
        comment=comment,
        fallback_block_key=str(fallback_block_key or "juristisch"),
    )

    errors: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    block_hash_by_key: dict[str, str] = {}
    available_block_keys = {str(k) for k in dict(baseline_old_minimd_by_block or {}).keys()}

    if not list(normalized_comment.parts or []):
        errors.append({"code": "empty_patch_payload", "reason": "no-nonempty-parts"})
        return CommentExactSelectionValidationResult(
            ok=False,
            normalized_comment=normalized_comment,
            errors=list(errors),
            warnings=list(warnings),
            block_hash_by_key={},
        )

    for part in list(normalized_comment.parts or []):
        part_id = str(part.part_id or "")
        block_key = str(part.block_key or "")
        if block_key not in available_block_keys:
            errors.append(
                {
                    "code": "unknown_block_key",
                    "part_id": part_id,
                    "block_key": block_key,
                }
            )
            continue

        block_text = str((baseline_old_minimd_by_block or {}).get(block_key) or "")
        server_hash = _sha256_hex_text(block_text)
        block_hash_by_key[str(block_key)] = str(server_hash)

        if require_selection:
            if part.sel_start is None or part.sel_end is None:
                errors.append(
                    {
                        "code": "missing_selection",
                        "part_id": part_id,
                        "reason": "sel_start-and-sel_end-required",
                    }
                )
                continue
            sel_start = int(part.sel_start)
            sel_end = int(part.sel_end)
            if sel_start < 0 or sel_end < 0 or sel_start > sel_end or sel_end > len(block_text):
                errors.append(
                    {
                        "code": "selection_out_of_bounds",
                        "part_id": part_id,
                        "sel_start": int(sel_start),
                        "sel_end": int(sel_end),
                        "block_len": int(len(block_text)),
                    }
                )
                continue
            exact_slice = block_text[sel_start:sel_end]
            if str(exact_slice) != str(part.old_text or ""):
                errors.append(
                    {
                        "code": "selection_text_mismatch",
                        "part_id": part_id,
                    }
                )
                continue
            marker_errors = _validate_protected_minimd_markers_against_change_events(
                old_text=str(part.old_text or ""),
                new_text=str(part.new_text or ""),
                block_key=str(block_key),
                part_id=str(part_id),
            )
            if marker_errors:
                errors.extend(marker_errors)
                continue

        if require_baseline_hash:
            provided_hash = str(getattr(part, "baseline_hash", "") or "")
            if not provided_hash:
                errors.append(
                    {
                        "code": "missing_baseline_hash",
                        "part_id": part_id,
                    }
                )
                continue
            if str(provided_hash) != str(server_hash):
                errors.append(
                    {
                        "code": "baseline_hash_mismatch",
                        "part_id": part_id,
                        "block_key": block_key,
                    }
                )
                continue

    return CommentExactSelectionValidationResult(
        ok=bool(len(errors) == 0),
        normalized_comment=normalized_comment,
        errors=list(errors),
        warnings=list(warnings),
        block_hash_by_key=dict(block_hash_by_key or {}),
    )


def validate_comment_minimd_syntax(
    *,
    minimd_text: str,
    config: Optional[IndiffConfigLike] = None,
) -> dict[str, Any]:
    """
    Minimal runtime syntax validator for comment MiniMD.

    Scope:
    - block/scope marker balance
    - structurally parseable MiniMD
    - no unclosed named scopes
    - no obviously invalid list lines

    Intentionally NOT included:
    - content quality
    - legal/formal review
    - patch/baseline applicability
    """
    text = _normalize_fragment_text(str(minimd_text or ""))
    errors: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []

    lines = text.split("\n")
    open_scopes: list[tuple[str, int]] = []
    has_any_block_marker = False
    current_meta_lines: list[tuple[int, str]] | None = None

    for line_no, raw in enumerate(lines, start=1):
        line = str(raw or "")
        m_start = re.match(r"^### start: ([a-zA-Z0-9_-]+) ###$", line)
        m_end = re.match(r"^### end: ([a-zA-Z0-9_-]+) ###$", line)

        if m_start:
            has_any_block_marker = True
            scope_name = str(m_start.group(1) or "")
            open_scopes.append((scope_name, int(line_no)))
            if scope_name == "meta":
                current_meta_lines = []
            continue

        if m_end:
            has_any_block_marker = True
            scope_name = str(m_end.group(1) or "")
            if not open_scopes:
                errors.append(
                    {
                        "code": "unexpected_scope_end",
                        "line_no": int(line_no),
                        "scope_name": scope_name,
                    }
                )
                continue
            top_name, top_line = open_scopes[-1]
            if str(top_name) != str(scope_name):
                errors.append(
                    {
                        "code": "mismatched_scope_end",
                        "line_no": int(line_no),
                        "scope_name": scope_name,
                        "expected_scope_name": str(top_name),
                        "opened_at_line": int(top_line),
                    }
                )
                continue
            open_scopes.pop()
            if scope_name == "meta" and current_meta_lines is not None:
                errors.extend(_validate_minimd_meta_block_lines(current_meta_lines, config=config))
                current_meta_lines = None
            continue

        if open_scopes and str(open_scopes[-1][0]) == "meta" and current_meta_lines is not None:
            current_meta_lines.append((int(line_no), line))

        if _is_obviously_invalid_list_line(line, config=config):
            errors.append(
                {
                    "code": "invalid_list_syntax",
                    "line_no": int(line_no),
                    "line": line,
                }
            )

    for scope_name, opened_at_line in list(open_scopes):
        errors.append(
            {
                "code": "unclosed_scope",
                "scope_name": str(scope_name),
                "opened_at_line": int(opened_at_line),
            }
        )

    if not has_any_block_marker:
        warnings.append({"code": "no_scope_markers_found"})

    return {
        "ok": bool(len(errors) == 0),
        "errors": list(errors),
        "warnings": list(warnings),
        "document_meta": {
            "line_count": len(lines),
            "has_any_block_marker": bool(has_any_block_marker),
            "open_scopes_remaining": len(open_scopes),
        },
    }


@dataclass
class CommentPatchPayloadValidationResult:
    ok: bool
    normalized_comment: PersistedCommentInput
    change_requests_by_block: dict[str, list[ChangeRequest]] = field(default_factory=dict)
    applied_parts: list[dict[str, Any]] = field(default_factory=list)
    locate_failures: list[dict[str, Any]] = field(default_factory=list)
    errors: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class CommentExactSelectionValidationResult:
    ok: bool
    normalized_comment: PersistedCommentInput
    errors: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[dict[str, Any]] = field(default_factory=list)
    block_hash_by_key: dict[str, str] = field(default_factory=dict)


def validate_comment_patch_payload(
    *,
    baseline_old_minimd_by_block: dict[str, str],
    comment: PersistedCommentInput,
    fallback_block_key: str = "juristisch",
) -> CommentPatchPayloadValidationResult:
    normalized_comment = normalize_comment_patch_payload(
        comment=comment,
        fallback_block_key=str(fallback_block_key or "juristisch"),
    )
    errors: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []

    available_block_keys = {str(k) for k in dict(baseline_old_minimd_by_block or {}).keys()}
    seen_part_ids: set[str] = set()

    if not list(normalized_comment.parts or []):
        errors.append({"code": "empty_patch_payload", "reason": "no-nonempty-parts"})

    for part in list(normalized_comment.parts or []):
        part_id = str(part.part_id or "")
        block_key = str(part.block_key or "")
        if part_id in seen_part_ids:
            errors.append({"code": "duplicate_part_id", "part_id": part_id})
        seen_part_ids.add(part_id)

        if block_key not in available_block_keys:
            errors.append(
                {
                    "code": "unknown_block_key",
                    "part_id": part_id,
                    "block_key": block_key,
                }
            )

        if (part.sel_start is None) ^ (part.sel_end is None):
            errors.append(
                {
                    "code": "invalid_selection_pair",
                    "part_id": part_id,
                    "reason": "sel_start-and-sel_end-must-appear-together",
                }
            )
        if part.sel_start is not None and part.sel_end is not None and int(part.sel_end) < int(part.sel_start):
            errors.append(
                {
                    "code": "invalid_selection_range",
                    "part_id": part_id,
                    "sel_start": int(part.sel_start),
                    "sel_end": int(part.sel_end),
                }
            )

    if errors:
        return CommentPatchPayloadValidationResult(
            ok=False,
            normalized_comment=normalized_comment,
            change_requests_by_block={},
            applied_parts=[],
            locate_failures=[],
            errors=list(errors),
            warnings=list(warnings),
        )

    change_requests_by_block, applied_parts, locate_failures = build_change_requests_from_patch_parts(
        baseline_old_minimd_by_block=dict(baseline_old_minimd_by_block or {}),
        comments=[normalized_comment],
        fallback_block_key=str(fallback_block_key or "juristisch"),
    )
    errors.extend(dict(x) for x in list(locate_failures or []))
    return CommentPatchPayloadValidationResult(
        ok=bool(len(errors) == 0),
        normalized_comment=normalized_comment,
        change_requests_by_block=dict(change_requests_by_block or {}),
        applied_parts=list(applied_parts or []),
        locate_failures=list(locate_failures or []),
        errors=list(errors),
        warnings=list(warnings),
    )


@dataclass
class CanonicalCommentEvent:
    cid: int
    part_id: str
    block_key: str
    abs_pos: int
    kind: str
    text: str


def extract_canonical_events_from_comments(
    *,
    baseline_old_minimd_by_block: dict[str, str],
    comments: list[PersistedCommentInput],
    fallback_block_key: str = "juristisch",
) -> list[CanonicalCommentEvent]:
    change_requests_by_block, _applied_parts, _locate_failures = build_change_requests_from_patch_parts(
        baseline_old_minimd_by_block=dict(baseline_old_minimd_by_block or {}),
        comments=list(comments or []),
        fallback_block_key=str(fallback_block_key or "juristisch"),
    )
    events: list[CanonicalCommentEvent] = []
    for bk, changes in dict(change_requests_by_block or {}).items():
        for ch in list(changes or []):
            part_id = str(ch.change_id).split("_", 1)[1] if "_" in str(ch.change_id) else str(ch.change_id)
            local_changes = derive_block_semantic_changes(
                str(ch.old_text or ""),
                str(ch.new_text or ""),
                str(bk),
            )
            for ev in list(local_changes or []):
                change_kind = str(ev.get("change_kind") or "")
                if change_kind == "delete":
                    events.append(
                        CanonicalCommentEvent(
                            cid=int(ch.cid),
                            part_id=str(part_id),
                            block_key=str(bk),
                            abs_pos=int(ch.old_start) + int(ev.get("old_start") or 0),
                            kind="del",
                            text=str(ev.get("old_text") or ""),
                        )
                    )
                elif change_kind == "insert":
                    events.append(
                        CanonicalCommentEvent(
                            cid=int(ch.cid),
                            part_id=str(part_id),
                            block_key=str(bk),
                            abs_pos=int(ch.old_start) + int(ev.get("old_start") or 0),
                            kind="ins",
                            text=str(ev.get("new_text") or ""),
                        )
                    )
    events.sort(
        key=lambda x: (
            str(x.block_key),
            int(x.abs_pos),
            int(x.cid),
            str(x.part_id),
            str(x.kind),
            str(x.text),
        )
    )
    return events



def validate_comment_submission(
    *,
    article_version_id: int,
    current_version_id: int,
    baseline_article_minimd: str,
    baseline_old_minimd_by_block: dict[str, str],
    comment: PersistedCommentInput,
    fallback_block_key: str = "juristisch",
    comment_minimd_text: str | None = None,
) -> CommentSubmissionValidationResult:
    """
    Public runtime validator for one comment submission.

    Current scope:
    - validate version binding
    - baseline presence
    - MiniMD syntax completeness
    - strict patch-part validation / locate checks
    """
    errors: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    part_results: list[dict[str, Any]] = []
    document_meta: dict[str, Any] = {}

    if int(article_version_id or 0) <= 0:
        errors.append({"code": "invalid_article_version", "reason": "missing-article-version-id"})
    if int(current_version_id or 0) <= 0:
        errors.append({"code": "invalid_current_version", "reason": "missing-current-version-id"})
    elif int(article_version_id or 0) != int(current_version_id or 0):
        warnings.append(
            {
                "code": "stale_article_version",
                "reason": "submission-version-differs-from-current-version",
                "article_version_id": int(article_version_id or 0),
                "current_version_id": int(current_version_id or 0),
            }
        )

    if not str(baseline_article_minimd or ""):
        errors.append({"code": "missing_baseline_article_minimd", "reason": "empty-article-baseline"})

    if comment_minimd_text is not None:
        syntax_validation = validate_comment_minimd_syntax(
            minimd_text=str(comment_minimd_text or ""),
        )
        for err in list(syntax_validation.get("errors") or []):
            errors.append(dict(err))
        for warn in list(syntax_validation.get("warnings") or []):
            warnings.append(dict(warn))
    else:
        syntax_validation = {
            "ok": True,
            "errors": [],
            "warnings": [],
            "document_meta": {"skipped": True, "reason": "no_comment_minimd_text_provided"},
        }

    patch_validation = validate_comment_patch_payload(
        baseline_old_minimd_by_block=dict(baseline_old_minimd_by_block or {}),
        comment=comment,
        fallback_block_key=str(fallback_block_key or "juristisch"),
    )

    for err in list(patch_validation.errors or []):
        errors.append(dict(err))
    for warn in list(patch_validation.warnings or []):
        warnings.append(dict(warn))

    for ap in list(patch_validation.applied_parts or []):
        part_results.append(dict(ap))
    for fail in list(patch_validation.locate_failures or []):
        part_results.append(
            {
                "cid": int(fail.get("cid") or comment.cid),
                "part_id": str(fail.get("part_id") or ""),
                "status": "unapplied",
                "reason": str(fail.get("reason") or "UNRESOLVED"),
                "block_key": str(fail.get("block_key") or ""),
            }
        )

    review_blocking = bool(len(errors) > 0)
    ok = bool(not review_blocking)

    document_meta = {
        "article_minimd_len": len(str(baseline_article_minimd or "")),
        "block_count": len(dict(baseline_old_minimd_by_block or {})),
        "parts_total": len(list((patch_validation.normalized_comment.parts if patch_validation.normalized_comment else []) or [])),
    }

    return CommentSubmissionValidationResult(
        ok=bool(ok),
        review_blocking=bool(review_blocking),
        errors=list(errors),
        warnings=list(warnings),
        document_meta=document_meta,
        part_results=list(part_results),
        normalized_comment=patch_validation.normalized_comment,
        syntax_validation=dict(syntax_validation or {}),
        patch_validation={
            "ok": bool(patch_validation.ok),
            "errors": list(patch_validation.errors or []),
            "warnings": list(patch_validation.warnings or []),
            "applied_parts": list(patch_validation.applied_parts or []),
            "locate_failures": list(patch_validation.locate_failures or []),
        },
    )


def prepare_comment_submission(
    *,
    article_version_id: int,
    current_version_id: int,
    baseline_article_minimd: str,
    baseline_old_minimd_by_block: dict[str, str],
    comment: PersistedCommentInput,
    fallback_block_key: str = "juristisch",
    comment_minimd_text: str | None = None,
) -> CommentSubmissionPreparationResult:
    """
    One-stop preparation API for draft/review submission.

    This function intentionally bundles only what is needed for comment
    background processes:
    - normalized persisted comment patch payload
    - syntax validation
    - patch applicability validation
    - canonical events
    """
    normalized_comment = normalize_comment_patch_payload(
        comment=comment,
        fallback_block_key=str(fallback_block_key or "juristisch"),
    )
    if comment_minimd_text is not None:
        syntax_validation = validate_comment_minimd_syntax(
            minimd_text=str(comment_minimd_text or ""),
        )
    else:
        syntax_validation = {
            "ok": True,
            "errors": [],
            "warnings": [],
            "document_meta": {"skipped": True, "reason": "no_comment_minimd_text_provided"},
        }
    patch_validation = validate_comment_patch_payload(
        baseline_old_minimd_by_block=dict(baseline_old_minimd_by_block or {}),
        comment=normalized_comment,
        fallback_block_key=str(fallback_block_key or "juristisch"),
    )
    submission_validation = validate_comment_submission(
        article_version_id=int(article_version_id),
        current_version_id=int(current_version_id),
        baseline_article_minimd=str(baseline_article_minimd or ""),
        baseline_old_minimd_by_block=dict(baseline_old_minimd_by_block or {}),
        comment=normalized_comment,
        fallback_block_key=str(fallback_block_key or "juristisch"),
        comment_minimd_text=comment_minimd_text,
    )
    canonical_events = [
        asdict(ev)
        for ev in extract_canonical_events_from_comments(
            baseline_old_minimd_by_block=dict(baseline_old_minimd_by_block or {}),
            comments=[normalized_comment],
            fallback_block_key=str(fallback_block_key or "juristisch"),
        )
    ]
    return CommentSubmissionPreparationResult(
        normalized_comment=normalized_comment,
        syntax_validation=dict(syntax_validation or {}),
        patch_validation={
            "ok": bool(patch_validation.ok),
            "errors": list(patch_validation.errors or []),
            "warnings": list(patch_validation.warnings or []),
            "applied_parts": list(patch_validation.applied_parts or []),
            "locate_failures": list(patch_validation.locate_failures or []),
        },
        submission_validation=submission_validation,
        canonical_events=list(canonical_events),
    )


def check_comment_compatibility(
    *,
    baseline_old_minimd_by_block: dict[str, str],
    comment: PersistedCommentInput,
    fallback_block_key: str = "juristisch",
) -> CommentCompatibilityResult:
    """
    First-priority compatibility check:
    block baseline still unchanged enough that old_text can still be located.
    """
    patch_validation = validate_comment_patch_payload(
        baseline_old_minimd_by_block=dict(baseline_old_minimd_by_block or {}),
        comment=comment,
        fallback_block_key=str(fallback_block_key or "juristisch"),
    )
    return CommentCompatibilityResult(
        compatible=bool(patch_validation.ok),
        normalized_comment=patch_validation.normalized_comment,
        applied_parts=list(patch_validation.applied_parts or []),
        locate_failures=list(patch_validation.locate_failures or []),
        errors=list(patch_validation.errors or []),
        warnings=list(patch_validation.warnings or []),
    )



def _release_block_order(
    block_order: list[str],
    baseline_old_minimd_by_block: dict[str, str],
) -> list[str]:
    """Return a lossless deterministic block order for materialization."""
    out: list[str] = []
    seen: set[str] = set()
    for raw in list(block_order or []):
        key = str(raw or "").strip()
        if key and key not in seen:
            seen.add(key)
            out.append(key)
    for raw in dict(baseline_old_minimd_by_block or {}).keys():
        key = str(raw or "").strip()
        if key and key not in seen:
            seen.add(key)
            out.append(key)
    return out


def _minimd_document_from_blocks(block_order: list[str], minimd_by_block: dict[str, str]) -> str:
    """Build the canonical top-level MiniMD document used by validation/tools."""
    start_tpl = "### start: {name} ###"
    end_tpl = "### end: {name} ###"
    if settings is not None:
        start_tpl = str(getattr(settings, "MINIMD_BLOCK_START_TEMPLATE", start_tpl) or start_tpl)
        end_tpl = str(getattr(settings, "MINIMD_BLOCK_END_TEMPLATE", end_tpl) or end_tpl)
    parts: list[str] = []
    for block_key in list(block_order or []):
        body = _normalize_fragment_text(str((minimd_by_block or {}).get(str(block_key)) or ""))
        parts.append(start_tpl.format(name=str(block_key)) + "\n")
        if body:
            parts.append(body)
            if not body.endswith("\n"):
                parts.append("\n")
        parts.append(end_tpl.format(name=str(block_key)) + "\n\n")
    return "".join(parts)


def _materialization_conflicts(operations_by_block: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    """Detect ambiguous source-range overlap before mutating MiniMD.

    Adjacent non-empty ranges are allowed. Inserts at the same position or on
    either boundary of a replacement are intentionally rejected for release v1:
    both are order-dependent and therefore not suitable for silent release
    materialization.
    """
    conflicts: list[dict[str, Any]] = []
    for block_key, raw_ops in dict(operations_by_block or {}).items():
        ops = sorted(
            list(raw_ops or []),
            key=lambda row: (
                int(row.get("start") or 0),
                int(row.get("end") or 0),
                int(row.get("cid") or 0),
                str(row.get("part_id") or ""),
            ),
        )
        for i, left in enumerate(ops):
            ls = int(left.get("start") or 0)
            le = int(left.get("end") or 0)
            for right in ops[i + 1:]:
                rs = int(right.get("start") or 0)
                re_ = int(right.get("end") or 0)
                if rs > le and le > ls:
                    break
                left_insert = ls == le
                right_insert = rs == re_
                if left_insert and right_insert:
                    conflict = ls == rs
                elif left_insert:
                    conflict = rs <= ls <= re_
                elif right_insert:
                    conflict = ls <= rs <= le
                else:
                    conflict = max(ls, rs) < min(le, re_)
                if not conflict:
                    continue
                conflicts.append({
                    "code": "overlapping_release_parts",
                    "block_key": str(block_key),
                    "left": {
                        "cid": int(left.get("cid") or 0),
                        "part_id": str(left.get("part_id") or ""),
                        "start": ls,
                        "end": le,
                    },
                    "right": {
                        "cid": int(right.get("cid") or 0),
                        "part_id": str(right.get("part_id") or ""),
                        "start": rs,
                        "end": re_,
                    },
                })
    return conflicts


def materialize_article_with_comments(
    *,
    block_order: list[str],
    baseline_old_minimd_by_block: dict[str, str],
    comments: list[PersistedCommentInput],
    fallback_block_key: str = "juristisch",
    require_baseline_hash: bool = True,
) -> ArticleMaterializationResult:
    """Materialize selected comments directly on block-local MiniMD.

    Release-v1 uses persisted exact-selection patch parts, not marked preview
    HTML and not a second semantic merge engine:

      baseline MiniMD block
        -> validate every selected old_text/new_text part
        -> reject ambiguous overlapping source ranges
        -> apply non-overlapping replacements from right to left
        -> validate the complete resulting MiniMD document
        -> render clean HTML from the resulting MiniMD
    """
    baseline = {
        str(k): _normalize_fragment_text(str(v or ""))
        for k, v in dict(baseline_old_minimd_by_block or {}).items()
    }
    order = _release_block_order(list(block_order or []), baseline)
    errors: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    applied_parts: list[dict[str, Any]] = []
    operations_by_block: dict[str, list[dict[str, Any]]] = {str(k): [] for k in order}
    seen_owner_ids: set[str] = set()

    if not order:
        errors.append({"code": "empty_article_baseline"})

    for comment in list(comments or []):
        exact = validate_exact_selection_patch_payload(
            baseline_old_minimd_by_block=dict(baseline),
            comment=comment,
            fallback_block_key=str(fallback_block_key or "juristisch"),
            require_baseline_hash=bool(require_baseline_hash),
            require_selection=True,
        )
        if not exact.ok:
            for err in list(exact.errors or []):
                row = dict(err)
                row.setdefault("cid", int(comment.cid))
                errors.append(row)
            warnings.extend(dict(x) for x in list(exact.warnings or []))
            continue

        for part in list(exact.normalized_comment.parts or []):
            owner_id = f"{int(exact.normalized_comment.cid)}:{str(part.part_id)}"
            if owner_id in seen_owner_ids:
                errors.append({
                    "code": "duplicate_comment_part",
                    "cid": int(exact.normalized_comment.cid),
                    "part_id": str(part.part_id),
                })
                continue
            seen_owner_ids.add(owner_id)
            assert part.sel_start is not None and part.sel_end is not None
            block_key = str(part.block_key or fallback_block_key or "juristisch")
            operations_by_block.setdefault(block_key, []).append({
                "cid": int(exact.normalized_comment.cid),
                "part_id": str(part.part_id),
                "block_key": block_key,
                "start": int(part.sel_start),
                "end": int(part.sel_end),
                "old_text": str(part.old_text or ""),
                "new_text": str(part.new_text or ""),
            })

    conflicts = _materialization_conflicts(operations_by_block)
    if conflicts:
        errors.extend(dict(x) for x in conflicts)

    baseline_hash_by_block = {str(k): _sha256_hex_text(v) for k, v in baseline.items()}
    if errors:
        return ArticleMaterializationResult(
            ok=False,
            block_order=list(order),
            minimd_by_block=dict(baseline),
            article_minimd=_minimd_document_from_blocks(order, baseline),
            applied_parts=list(applied_parts),
            conflicts=list(conflicts),
            errors=list(errors),
            warnings=list(warnings),
            baseline_hash_by_block=dict(baseline_hash_by_block),
            result_hash_by_block=dict(baseline_hash_by_block),
        )

    result_blocks = dict(baseline)
    for block_key in order:
        text = str(result_blocks.get(str(block_key)) or "")
        ops = sorted(
            list(operations_by_block.get(str(block_key)) or []),
            key=lambda row: (
                int(row.get("start") or 0),
                int(row.get("end") or 0),
                int(row.get("cid") or 0),
                str(row.get("part_id") or ""),
            ),
            reverse=True,
        )
        for op in ops:
            start = int(op.get("start") or 0)
            end = int(op.get("end") or 0)
            old_text = str(op.get("old_text") or "")
            if text[start:end] != old_text:
                errors.append({
                    "code": "materialization_selection_changed",
                    "cid": int(op.get("cid") or 0),
                    "part_id": str(op.get("part_id") or ""),
                    "block_key": str(block_key),
                })
                continue
            text = text[:start] + str(op.get("new_text") or "") + text[end:]
            applied_parts.append({
                "cid": int(op.get("cid") or 0),
                "part_id": str(op.get("part_id") or ""),
                "block_key": str(block_key),
                "start": start,
                "end": end,
                "status": "applied",
            })
        result_blocks[str(block_key)] = text

    article_minimd = _minimd_document_from_blocks(order, result_blocks)
    syntax_validation = validate_comment_minimd_syntax(minimd_text=article_minimd)
    if not bool(syntax_validation.get("ok")):
        errors.extend(dict(x) for x in list(syntax_validation.get("errors") or []))
    warnings.extend(dict(x) for x in list(syntax_validation.get("warnings") or []))

    html_by_block: dict[str, str] = {}
    content_blocks: dict[str, str] = {}
    if not errors:
        for block_key in order:
            body = str(result_blocks.get(str(block_key)) or "")
            rendered = minimd_to_html_text(str(block_key), body)
            html_by_block[str(block_key)] = str(rendered)
            # Existing ArticleVersion contract: meta remains MiniMD text.
            content_blocks[str(block_key)] = body if str(block_key) == "meta" else str(rendered)

    result_hash_by_block = {str(k): _sha256_hex_text(v) for k, v in result_blocks.items()}
    return ArticleMaterializationResult(
        ok=bool(len(errors) == 0),
        block_order=list(order),
        minimd_by_block=dict(result_blocks),
        html_by_block=dict(html_by_block),
        content_blocks=dict(content_blocks),
        article_minimd=str(article_minimd),
        applied_parts=list(applied_parts),
        conflicts=list(conflicts),
        errors=list(errors),
        warnings=list(warnings),
        syntax_validation=dict(syntax_validation or {}),
        baseline_hash_by_block=dict(baseline_hash_by_block),
        result_hash_by_block=dict(result_hash_by_block),
    )


def _all_exact_occurrences(haystack: str, needle: str) -> list[int]:
    if not needle:
        return []
    out: list[int] = []
    start = 0
    while True:
        pos = str(haystack).find(str(needle), int(start))
        if pos < 0:
            break
        out.append(int(pos))
        start = int(pos) + 1
    return out


def _context_matches_relocated_part(
    *,
    old_base: str,
    new_base: str,
    old_start: int,
    old_end: int,
    new_start: int,
    new_end: int,
    context_chars: int,
) -> bool:
    n = max(0, int(context_chars))
    if n == 0:
        return True
    before = old_base[max(0, int(old_start) - n):int(old_start)]
    after = old_base[int(old_end):min(len(old_base), int(old_end) + n)]
    if before and new_base[max(0, int(new_start) - len(before)):int(new_start)] != before:
        return False
    if after and new_base[int(new_end):int(new_end) + len(after)] != after:
        return False
    return True


def _relocate_exact_part_to_new_base(
    *,
    old_base: str,
    new_base: str,
    part: PersistedCommentPartInput,
    context_chars: int = 64,
) -> tuple[PersistedCommentPartInput | None, dict[str, Any]]:
    """Relocate one unchanged patch part from old_base to new_base.

    No fuzzy/semantic matching is used. Replacements require an exact old_text
    occurrence, disambiguated by unchanged local context when necessary. Pure
    inserts require an exact unchanged boundary context around the old insertion
    point.
    """
    assert part.sel_start is not None and part.sel_end is not None
    old_start = int(part.sel_start)
    old_end = int(part.sel_end)
    old_text = str(part.old_text or "")
    new_text = str(part.new_text or "")
    part_id = str(part.part_id or "")
    block_key = str(part.block_key or "")

    if old_text:
        candidates = _all_exact_occurrences(new_base, old_text)
        if len(candidates) > 1:
            candidates = [
                pos for pos in candidates
                if _context_matches_relocated_part(
                    old_base=old_base,
                    new_base=new_base,
                    old_start=old_start,
                    old_end=old_end,
                    new_start=int(pos),
                    new_end=int(pos + len(old_text)),
                    context_chars=int(context_chars),
                )
            ]
        if len(candidates) != 1:
            return None, {
                "code": "rebase_old_text_not_unique" if candidates else "rebase_old_text_missing",
                "part_id": part_id,
                "block_key": block_key,
                "candidate_count": len(candidates),
            }
        new_start = int(candidates[0])
        new_end = int(new_start + len(old_text))
    else:
        # Pure insert: locate the old source boundary using exact context on both
        # sides. This permits unrelated edits elsewhere in the block while
        # rejecting an altered/ambiguous insertion neighbourhood.
        n = max(1, int(context_chars))
        before = old_base[max(0, old_start - n):old_start]
        after = old_base[old_start:min(len(old_base), old_start + n)]
        positions: list[int] = []
        for pos in range(0, len(new_base) + 1):
            if before and new_base[max(0, pos - len(before)):pos] != before:
                continue
            if after and new_base[pos:pos + len(after)] != after:
                continue
            positions.append(int(pos))
        if len(positions) != 1:
            return None, {
                "code": "rebase_insert_anchor_ambiguous" if positions else "rebase_insert_anchor_missing",
                "part_id": part_id,
                "block_key": block_key,
                "candidate_count": len(positions),
            }
        new_start = new_end = int(positions[0])

    rebased = PersistedCommentPartInput(
        part_id=part_id,
        block_key=block_key,
        old_text=old_text,
        new_text=new_text,
        sel_start=int(new_start),
        sel_end=int(new_end),
        baseline_hash=_sha256_hex_text(new_base),
    )
    return rebased, {
        "code": "rebased_exact",
        "part_id": part_id,
        "block_key": block_key,
        "old_start": old_start,
        "old_end": old_end,
        "new_start": int(new_start),
        "new_end": int(new_end),
    }


def check_comment_base_rebase(
    *,
    block_order: list[str],
    old_baseline_minimd_by_block: dict[str, str],
    new_baseline_minimd_by_block: dict[str, str],
    comment: PersistedCommentInput,
    fallback_block_key: str = "juristisch",
    context_chars: int = 64,
) -> CommentBaseRebaseResult:
    """Check whether one comment can move to a new article base unchanged.

    The persisted patch parts are the intended-change contract. They are first
    validated against the old baseline, then the exact same old_text->new_text
    operations are relocated to the new baseline. No fuzzy merge is attempted.
    """
    old_baseline = {
        str(k): _normalize_fragment_text(str(v or ""))
        for k, v in dict(old_baseline_minimd_by_block or {}).items()
    }
    new_baseline = {
        str(k): _normalize_fragment_text(str(v or ""))
        for k, v in dict(new_baseline_minimd_by_block or {}).items()
    }
    exact = validate_exact_selection_patch_payload(
        baseline_old_minimd_by_block=dict(old_baseline),
        comment=comment,
        fallback_block_key=str(fallback_block_key or "juristisch"),
        require_baseline_hash=True,
        require_selection=True,
    )
    if not exact.ok:
        return CommentBaseRebaseResult(
            safe=False,
            status="invalid_old_base",
            normalized_comment=exact.normalized_comment,
            errors=[dict(x) for x in list(exact.errors or [])],
            warnings=[dict(x) for x in list(exact.warnings or [])],
        )

    rebased_parts: list[PersistedCommentPartInput] = []
    relocated: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for part in list(exact.normalized_comment.parts or []):
        block_key = str(part.block_key or fallback_block_key or "juristisch")
        if block_key not in new_baseline:
            errors.append({
                "code": "rebase_block_missing",
                "part_id": str(part.part_id or ""),
                "block_key": block_key,
            })
            continue
        rebased_part, diagnostic = _relocate_exact_part_to_new_base(
            old_base=str(old_baseline.get(block_key) or ""),
            new_base=str(new_baseline.get(block_key) or ""),
            part=part,
            context_chars=int(context_chars),
        )
        relocated.append(dict(diagnostic))
        if rebased_part is None:
            errors.append(dict(diagnostic))
            continue
        rebased_parts.append(rebased_part)

    rebased_comment = PersistedCommentInput(
        cid=int(exact.normalized_comment.cid),
        parts=list(rebased_parts),
    )
    if errors:
        return CommentBaseRebaseResult(
            safe=False,
            status="changed",
            normalized_comment=exact.normalized_comment,
            rebased_comment=None,
            relocated_parts=list(relocated),
            errors=list(errors),
            warnings=[dict(x) for x in list(exact.warnings or [])],
        )

    materialized = materialize_article_with_comments(
        block_order=list(block_order or []),
        baseline_old_minimd_by_block=dict(new_baseline),
        comments=[rebased_comment],
        fallback_block_key=str(fallback_block_key or "juristisch"),
        require_baseline_hash=True,
    )
    if not materialized.ok:
        return CommentBaseRebaseResult(
            safe=False,
            status="changed",
            normalized_comment=exact.normalized_comment,
            rebased_comment=rebased_comment,
            relocated_parts=list(relocated),
            errors=[dict(x) for x in list(materialized.errors or [])],
            warnings=[dict(x) for x in list(materialized.warnings or [])],
            materialized=materialized,
        )

    return CommentBaseRebaseResult(
        safe=True,
        status="unchanged",
        normalized_comment=exact.normalized_comment,
        rebased_comment=rebased_comment,
        relocated_parts=list(relocated),
        errors=[],
        warnings=[dict(x) for x in list(materialized.warnings or [])],
        materialized=materialized,
    )


def compose_article_merge_preview(
    *,
    block_order: list[str],
    baseline_old_minimd_by_block: dict[str, str],
    baseline_html_by_block: dict[str, str],
    comments: list[PersistedCommentInput],
    fallback_block_key: str = "juristisch",
) -> ArticleMergePreviewResult:
    """
    Public product API for app.py:
    compose one full multi-comment article preview from persisted patch parts.
    """
    block_change_requests, applied_parts, locate_failures = build_change_requests_from_patch_parts(
        baseline_old_minimd_by_block=dict(baseline_old_minimd_by_block or {}),
        comments=list(comments or []),
        fallback_block_key=str(fallback_block_key or "juristisch"),
    )

    block_results: dict[str, ComposeBlockChangeSetResult] = {}
    merged_blocks: dict[str, str] = {}

    for bk in list(block_order or []):
        baseline_old_minimd = str((baseline_old_minimd_by_block or {}).get(str(bk)) or "")
        baseline_html = str((baseline_html_by_block or {}).get(str(bk)) or "")
        changes = list((block_change_requests or {}).get(str(bk)) or [])
        if not changes:
            merged_blocks[str(bk)] = str(baseline_html)
            continue
        result = compose_block_change_set(
            str(bk),
            str(baseline_old_minimd),
            str(baseline_html),
            list(changes),
        )
        block_results[str(bk)] = result
        merged_blocks[str(bk)] = str(result.composed_html or baseline_html)

    merged_marked_html_full = "".join(str(merged_blocks.get(str(bk)) or "") for bk in list(block_order or []))
    comment_cards_table: list[dict[str, Any]] = []
    for bk in list(block_order or []):
        res = block_results.get(str(bk))
        if res is None:
            continue
        comment_cards_table.extend(list(res.comment_card_rows or []))
    comment_cards_table.sort(
        key=lambda r: (
            int((r or {}).get("cid") or 0),
            str((r or {}).get("block_key") or ""),
            str((r or {}).get("row_sort_key") or ""),
            int((r or {}).get("source_start") or 0),
            str((r or {}).get("span_id") or ""),
        )
    )
    comment_cards_html = _render_comment_cards_html_from_table(comment_cards_table)

    counts = {
        "applied": int(sum(1 for x in list(applied_parts or []) if str(x.get("status")) == "applied")),
        "unapplied": int(sum(1 for x in list(applied_parts or []) if str(x.get("status")) != "applied")),
    }
    counts["article_interaction_rows"] = int(len(_build_article_interaction_rows_from_block_results(block_results)))

    return ArticleMergePreviewResult(
        merged_marked_html_full=str(merged_marked_html_full or ""),
        comment_cards_html=str(comment_cards_html or ""),
        comment_cards_table=list(comment_cards_table or []),
        block_results=dict(block_results or {}),
        applied_parts=list(applied_parts or []),
        locate_failures=list(locate_failures or []),
        counts=dict(counts or {}),
    )


# ---------------------------------------------------------------------------
# Product anchor table and resolution
# ---------------------------------------------------------------------------

def build_product_html_anchor_table(block_key: str, baseline_html: str) -> list[HtmlAnchor]:
    src = str(baseline_html or "")
    anchors: list[HtmlAnchor] = []
    in_tag = False
    for i in range(0, len(src) + 1):
        if i < len(src):
            ch = src[i]
            if ch == "<":
                in_tag = True
            elif ch == ">":
                pass
            else:
                pass
        else:
            pass
        anchors.append(HtmlAnchor(anchor_id=f"ha{i:05d}", source_offset=i))
        if i < len(src) and src[i] == ">":
            in_tag = False
    # dedupe by source offset naturally unique
    return anchors


def _resolve_source_anchor_from_raw_offset(block_key: str, baseline_old_minimd: str, baseline_html: str, raw_offset: int) -> int:
    _, mapping = _render_minimd_with_mapping(block_key, baseline_old_minimd)
    paragraph_shift = 0
    m_para = re.match(r"^(<p\b[^>]*>)([\s\S]*)(</p>)$", str(baseline_html or ""), re.I | re.S)
    if m_para:
        paragraph_shift = len(str(m_para.group(1) or ""))
    off = max(0, min(int(raw_offset), len(str(baseline_old_minimd or ""))))
    if off in mapping:
        return int(mapping[off]) + int(paragraph_shift)
    # nearest predecessor
    keys = sorted(mapping.keys())
    prev = 0
    for k in keys:
        if k > off:
            break
        prev = k
    return int(mapping.get(prev, 0)) + int(paragraph_shift)


# ---------------------------------------------------------------------------
# Delete splitting and cluster build
# ---------------------------------------------------------------------------

def _split_overlapping_delete_segments_from_changes(baseline_old_minimd: str, changes: list[ChangeRequest]) -> list[ResolvedDeleteSegment]:
    deletes = [ch for ch in changes if ch.old_text and not ch.new_text and int(ch.old_end) > int(ch.old_start)]
    cuts = sorted(set([int(ch.old_start) for ch in deletes] + [int(ch.old_end) for ch in deletes]))
    out: list[ResolvedDeleteSegment] = []
    for a, b in zip(cuts, cuts[1:]):
        owners = []
        for ch in deletes:
            if int(ch.old_start) <= a and int(ch.old_end) >= b:
                owners.append({"part_id": str(ch.change_id), "cid": int(ch.cid)})
        if not owners:
            continue
        out.append(ResolvedDeleteSegment(
            segment_id=f"delseg_{a}_{b}",
            start=int(a),
            end=int(b),
            deleted_text=str(baseline_old_minimd[a:b]),
            owners=owners,
            status="overlap" if len(owners) > 1 else "normal",
        ))
    return out


def _detect_list_insert(new_text: str) -> tuple[bool, str, int, str]:
    s = str(new_text or "").lstrip("\r\n")
    if re.match(r"^  -\s+", s):
        return True, "ul", 2, re.sub(r"^  -\s+", "", s).rstrip("\n")
    m2 = re.match(r"^  (\d+)\.\s+(.*)$", s, flags=re.S)
    if m2:
        return True, "ol", 2, str(m2.group(2) or "").rstrip("\n")
    m = re.match(r"^(\d+)\.\s+(.*)$", s, flags=re.S)
    if m:
        return True, "ol", 1, str(m.group(2) or "").rstrip("\n")
    if re.match(r"^-\s+", s):
        return True, "ul", 1, re.sub(r"^-\s+", "", s).rstrip("\n")
    return False, "", 0, s


def _split_mixed_insert_text(new_text: str) -> list[str]:
    """
    Split one insert payload into product-ready subparts when it mixes
    inline text and a following list block.

    Example:
        ".\\n  - **Haftung:** ..."
    becomes:
        [".", "  - **Haftung:** ..."]
    """
    src = str(new_text or "")
    if not src:
        return []
    m = re.search(r"\n(?=(?:  -|-|\d+\.\s))", src)
    if not m:
        return [src]
    left = src[: int(m.start())]
    right = src[int(m.start()) + 1 :]
    out: list[str] = []
    if left:
        out.append(left)
    if right:
        out.append(right)
    return out or [src]


def _is_table_separator_line(body: str) -> bool:
    cells = [c[0] for c in _split_table_row_with_cell_offsets(str(body or ""))]
    return bool(cells) and all(re.match(r"^:?-{3,}:?$", c) for c in cells)


def _normalize_table_row_insert_text(text: str) -> str:
    """
    Normalize the common diff artifact:
        " |\n| Evaluation | geplant | Nach drei Jahren"
    ->  "| Evaluation | geplant | Nach drei Jahren"
    """
    src = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    src = re.sub(r"^\s*\|\s*\n(?=\|)", "", src, count=1)
    return src


def _split_structured_insert_fragments(new_text: str) -> list[StructuredInsertFragment]:
    """
    Split one insert payload by MiniMD structure before later HTML materialization.

    Result classes:
      - inline_insert
      - list_item_insert
      - list_continuation_insert
      - table_row_insert
    """
    src = _normalize_table_row_insert_text(new_text)
    if not src:
        return []

    lines = src.splitlines(keepends=True)
    out: list[StructuredInsertFragment] = []
    inline_buf: list[str] = []

    def flush_inline() -> None:
        nonlocal inline_buf
        if not inline_buf:
            return
        txt = "".join(inline_buf)
        if txt:
            out.append(StructuredInsertFragment(
                change_class="inline_insert",
                text=txt,
            ))
        inline_buf = []

    for idx, line in enumerate(lines):
        body = line[:-1] if line.endswith("\n") else line

        m_cont2 = re.match(r"^  ::\s+(.*)$", body)
        if m_cont2:
            flush_inline()
            out.append(StructuredInsertFragment(
                change_class="list_continuation_insert",
                text=str(m_cont2.group(1) or "").strip(),
                list_kind="ul",
                list_level=2,
            ))
            continue

        m_cont1 = re.match(r"^::\s+(.*)$", body)
        if m_cont1:
            flush_inline()
            out.append(StructuredInsertFragment(
                change_class="list_continuation_insert",
                text=str(m_cont1.group(1) or "").strip(),
                list_kind="ul",
                list_level=1,
            ))
            continue

        is_list, list_kind, list_level, _body = _detect_list_insert(body)
        if is_list:
            flush_inline()
            out.append(StructuredInsertFragment(
                change_class="list_item_insert",
                text=str(body),
                list_kind=str(list_kind),
                list_level=int(list_level),
            ))
            continue

        if body.strip() == "|" and idx + 1 < len(lines):
            next_body = lines[idx + 1][:-1] if lines[idx + 1].endswith("\n") else lines[idx + 1]
            if next_body.strip().startswith("|"):
                continue

        if body.strip().startswith("|") and not _is_table_separator_line(body):
            flush_inline()
            out.append(StructuredInsertFragment(
                change_class="table_row_insert",
                text=str(body).strip(),
            ))
            continue

        inline_buf.append(line)

    flush_inline()
    return out


def _normalize_mixed_insert_subparts(subparts: list[str]) -> list[str]:
    """
    Normalize the narrow mixed case:
      punctuation-only inline fragment + following level-2 UL insert

    Example:
      [".", "  - **Haftung:** ..."]
    becomes:
      ["  - **Haftung:** ...."]

    Rationale:
    In the current diff model, trailing punctuation of the new nested list item
    may be matched against the old baseline sentence period. For product output,
    that punctuation belongs to the inserted level-2 item, not as a separate
    inline insert in front of the nested UL.
    """
    parts = list(subparts or [])
    if len(parts) != 2:
        return parts
    left = str(parts[0] or "")
    right = str(parts[1] or "")
    if left not in {".", "!", "?", ";", ":"}:
        return parts
    is_list, list_kind, list_level, _body = _detect_list_insert(right)
    if not (is_list and str(list_kind) in {"ul", "ol"} and int(list_level) == 2):
        return parts
    if right.rstrip().endswith(left):
        return [right]
    return [right.rstrip() + left]


def _iter_minimd_lines_with_offsets(src: str) -> list[tuple[int, int, str]]:
    out: list[tuple[int, int, str]] = []
    pos = 0
    for line in str(src or "").replace("\r\n", "\n").replace("\r", "\n").splitlines(keepends=True):
        body = line[:-1] if line.endswith("\n") else line
        start = pos
        end = pos + len(body)
        out.append((int(start), int(end), str(body)))
        pos += len(line)
    return out


def _list_body_text_from_line(body: str) -> str:
    s = str(body or "")
    m_ul2 = re.match(r"^  -\s+(.*)$", s)
    if m_ul2:
        return str(m_ul2.group(1) or "").strip()
    m_ul1 = re.match(r"^-\s+(.*)$", s)
    if m_ul1:
        return str(m_ul1.group(1) or "").strip()
    m_ol1 = re.match(r"^\d+\.\s+(.*)$", s)
    if m_ol1:
        return str(m_ol1.group(1) or "").strip()
    return s.strip()


def _find_parent_level1_hint_from_minimd(src: str, offset: int) -> str:
    """
    From one MiniMD text, find the immediately preceding level-1 list item body
    before the given offset.

    This is used on the *comment MiniMD* side to remember which parent item a
    later level-2 insert belongs to.
    """
    lines = _iter_minimd_lines_with_offsets(src)
    parent_hint = ""
    for start, end, body in lines:
        if start > int(offset):
            break
        if re.match(r"^(?:-\s+|\d+\.\s+)", str(body or "")):
            parent_hint = _list_body_text_from_line(body)
    return str(parent_hint or "")


def _resolve_parent_level1_source_anchor_for_level2(
    block_key: str,
    baseline_old_minimd: str,
    baseline_html: str,
    raw_offset: int,
    parent_hint: str = "",
) -> int:
    """
    Resolve a level-2 UL insert against the parent level-1 list item in the
    baseline MiniMD structure, not merely against the raw insert offset.

    We search the last level-1 list item line whose start is <= raw_offset and
    resolve the HTML source anchor from that line end. This gives the HTML-side
    search for the closing </li> a stable parent context.
    """
    lines = _iter_minimd_lines_with_offsets(baseline_old_minimd)
    parent_end: int | None = None

    hint = str(parent_hint or "").strip()
    if hint:
        hint_norm = " ".join(hint.split())
        for start, end, body in lines:
            if re.match(r"^(?:-\s+|\d+\.\s+)", str(body or "")):
                body_norm = " ".join(_list_body_text_from_line(body).split())
                if body_norm == hint_norm:
                    parent_end = int(end)
                    break

    if parent_end is None:
        for start, end, body in lines:
            if start > int(raw_offset):
                break
            if re.match(r"^(?:-\s+|\d+\.\s+)", str(body or "")):
                parent_end = int(end)
    if parent_end is None:
        return _resolve_source_anchor_from_raw_offset(block_key, baseline_old_minimd, baseline_html, raw_offset)
    return _resolve_source_anchor_from_raw_offset(block_key, baseline_old_minimd, baseline_html, int(parent_end))


def _make_owner_id(comment_id: int, part_id: str) -> str:
    return f"{int(comment_id)}:{str(part_id)}"


def _make_span_id(kind: str, comment_id: int, part_id: str, local_id: str = "") -> str:
    suffix = f":{local_id}" if local_id else ""
    return f"{kind}:{int(comment_id)}:{part_id}{suffix}"


def _normalize_owner_ids(
    owner_ids: list[str] | tuple[str, ...] | set[str] | None,
    *,
    primary: str = "",
) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    if str(primary or ""):
        out.append(str(primary))
        seen.add(str(primary))
    for owner_id in list(owner_ids or []):
        s = str(owner_id or "").strip()
        if not s or s in seen:
            continue
        out.append(s)
        seen.add(s)
    return out


def _build_owner_scope_attrs(
    *,
    owner_id: str,
    owner_ids: list[str] | tuple[str, ...] | set[str] | None = None,
    op_role: str = "",
) -> str:
    primary = str(owner_id or "")
    all_ids = _normalize_owner_ids(owner_ids, primary=primary)
    attrs = [
        f'data-kgg-owner-id="{html.escape(primary, quote=True)}"',
        f'data-kgg-owner-ids="{html.escape(" ".join(all_ids), quote=True)}"',
    ]
    if op_role:
        attrs.append(f'data-kgg-op-role="{html.escape(str(op_role), quote=True)}"')
    return " ".join(attrs)


def _build_span_ids_attr(
    span_ids: list[str] | tuple[str, ...] | set[str] | None,
) -> str:
    out: list[str] = []
    seen: set[str] = set()
    for span_id in list(span_ids or []):
        s = str(span_id or "").strip()
        if not s or s in seen:
            continue
        out.append(s)
        seen.add(s)
    if not out:
        return ""
    return f'data-kgg-span-ids="{html.escape(" ".join(out), quote=True)}"'


def _owner_ids_for_atomics(atoms: list[ResolvedAtomicChange]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for atomic in list(atoms or []):
        owner_id = str(getattr(atomic, "owner_id", "") or "").strip()
        if not owner_id or owner_id in seen:
            continue
        out.append(owner_id)
        seen.add(owner_id)
    return out


def _render_badge_html(
    cid: int,
    op_id: str,
    *,
    owner_id: str = "",
    owner_ids: list[str] | tuple[str, ...] | set[str] | None = None,
) -> str:
    attrs = [
        'class="kgg-inline-diff-badge"',
        f'data-kgg-op-id="{html.escape(op_id, quote=True)}"',
    ]
    if owner_id:
        attrs.append(
            _build_owner_scope_attrs(
                owner_id=str(owner_id),
                owner_ids=owner_ids,
                op_role="badge",
            )
        )
    return f'<span {" ".join(attrs)}>#{int(cid)}</span>'


def _build_structural_op_attrs(
    *,
    span_id: str,
    owner_id: str,
    cid: int,
    part_id: str,
    owner_ids: list[str] | tuple[str, ...] | set[str] | None = None,
    op_role: str = "structural",
) -> str:
    attrs = [
        'class="kgg-inline-op"',
        f'data-kgg-cid="{int(cid)}"',
        f'data-kgg-part-id="{html.escape(str(part_id or ""), quote=True)}"',
        f'data-kgg-span-id="{html.escape(str(span_id or ""), quote=True)}"',
        _build_owner_scope_attrs(
            owner_id=str(owner_id or ""),
            owner_ids=owner_ids,
            op_role=str(op_role or "structural"),
        ),
        'data-kgg-structural-op="true"',
    ]
    return " " + " ".join(attrs)


def _build_structural_op_attrs_for_atomic(
    atomic: ResolvedAtomicChange,
    *,
    owner_ids: list[str] | tuple[str, ...] | set[str] | None = None,
    op_role: str = "insert-structural",
) -> str:
    return _build_structural_op_attrs(
        span_id=str(getattr(atomic, "span_id", "") or ""),
        owner_id=str(getattr(atomic, "owner_id", "") or ""),
        cid=int(getattr(atomic, "cid", 0) or 0),
        part_id=str(getattr(atomic, "original_part_id", "") or ""),
        owner_ids=owner_ids,
        op_role=str(op_role or "insert-structural"),
    )


def _render_insert_span(
    text: str,
    span_id: str,
    op_id: str,
    cid: int,
    *,
    owner_id: str = "",
    owner_ids: list[str] | tuple[str, ...] | set[str] | None = None,
) -> str:
    attrs = [
        'class="kgg-inline-diff-ins"',
        f'data-kgg-op-id="{html.escape(op_id, quote=True)}"',
        f'data-kgg-cid="{int(cid)}"',
        f'data-kgg-span-id="{html.escape(span_id, quote=True)}"',
    ]
    if owner_id:
        attrs.append(
            _build_owner_scope_attrs(
                owner_id=str(owner_id),
                owner_ids=owner_ids,
                op_role="insert-text",
            )
        )
    return f'<span {" ".join(attrs)}>{minimd_to_html_text("_inline", text)}</span>'


def _render_insert_span_from_body_html(
    body_html: str,
    span_id: str,
    op_id: str,
    cid: int,
    *,
    owner_id: str = "",
    owner_ids: list[str] | tuple[str, ...] | set[str] | None = None,
) -> str:
    attrs = [
        'class="kgg-inline-diff-ins"',
        f'data-kgg-op-id="{html.escape(op_id, quote=True)}"',
        f'data-kgg-cid="{int(cid)}"',
        f'data-kgg-span-id="{html.escape(span_id, quote=True)}"',
    ]
    if owner_id:
        attrs.append(
            _build_owner_scope_attrs(
                owner_id=str(owner_id),
                owner_ids=owner_ids,
                op_role="insert-text",
            )
        )
    return f'<span {" ".join(attrs)}>{str(body_html or "")}</span>'


def _render_coarse_insert_block(
    text: str,
    span_id: str,
    op_id: str,
    cid: int,
    *,
    owner_id: str = "",
    owner_ids: list[str] | tuple[str, ...] | set[str] | None = None,
) -> str:
    """Render a large/coarse insert as block-capable HTML.

    A normal inline <span> cannot validly contain MiniMD-rendered block
    structures such as <ol>, <ul> or <blockquote>. Browsers then split the
    span automatically, which makes large coarse inserts look interrupted.
    """
    attrs = [
        'class="kgg-inline-diff-ins kgg-inline-diff-ins-block kgg-inline-op"',
        f'data-kgg-op-id="{html.escape(op_id, quote=True)}"',
        f'data-kgg-cid="{int(cid)}"',
        f'data-kgg-span-id="{html.escape(span_id, quote=True)}"',
        'data-kgg-structural-op="true"',
        'data-kgg-coarse-insert="true"',
    ]
    if owner_id:
        attrs.append(
            _build_owner_scope_attrs(
                owner_id=str(owner_id),
                owner_ids=owner_ids,
                op_role="insert-block",
            )
        )
    body_html = minimd_to_html_text("_inline", str(text or ""))
    return f'<div {" ".join(attrs)}>{body_html}</div>'


def _render_coarse_delete_block(
    atomic: ResolvedAtomicChange,
    *,
    owner_ids: list[str] | tuple[str, ...] | set[str] | None = None,
    span_ids: list[str] | tuple[str, ...] | set[str] | None = None,
) -> str:
    """Render a large/coarse delete as block-capable HTML.

    Normal delete spans intentionally preserve tag balance for small inline
    changes. For coarse article rewrites, however, the deleted MiniMD may
    contain lists, storybox/details scopes or other block structures; wrapping
    those with an inline <span> produces interrupted visual ranges.
    """
    attrs = [
        'class="kgg-inline-diff-del kgg-inline-diff-del-block kgg-inline-op"',
        f'data-kgg-op-id="{html.escape(str(atomic.original_part_id) + "_del", quote=True)}"',
        f'data-kgg-cid="{int(atomic.cid)}"',
        f'data-kgg-span-id="{html.escape(str(atomic.span_id or ""), quote=True)}"',
        'data-kgg-structural-op="true"',
        'data-kgg-coarse-delete="true"',
        _build_owner_scope_attrs(
            owner_id=str(atomic.owner_id or ""),
            owner_ids=owner_ids,
            op_role="delete-block",
        ),
    ]
    span_ids_attr = _build_span_ids_attr(span_ids)
    if span_ids_attr:
        attrs.append(span_ids_attr)
    body_html = minimd_to_html_text("_inline", str(atomic.deleted_text or ""))
    return f'<div {" ".join(attrs)}>{body_html}</div>'


def _render_insert_payload_html(atomic: ResolvedAtomicChange) -> str:
    if str(atomic.subkind) == "coarse_inline_insert":
        return (
            _render_badge_html(int(atomic.cid), f"{atomic.original_part_id}_badge", owner_id=str(atomic.owner_id))
            + _render_coarse_insert_block(atomic.inserted_text, atomic.span_id, f"{atomic.original_part_id}_ins", atomic.cid, owner_id=str(atomic.owner_id))
        )
    if str(atomic.subkind) == "pre_inline_insert":
        body_html = html.escape(str(atomic.inserted_text or ""), quote=False)
        return (
            _render_badge_html(int(atomic.cid), f"{atomic.original_part_id}_badge", owner_id=str(atomic.owner_id))
            + _render_insert_span_from_body_html(body_html, atomic.span_id, f"{atomic.original_part_id}_ins", atomic.cid, owner_id=str(atomic.owner_id))
        )
    if str(atomic.subkind) == "table_cell_inline_insert":
        body_html = _render_inline_html(str(atomic.inserted_text or ""))
        return (
            _render_badge_html(int(atomic.cid), f"{atomic.original_part_id}_badge", owner_id=str(atomic.owner_id))
            + _render_insert_span_from_body_html(body_html, atomic.span_id, f"{atomic.original_part_id}_ins", atomic.cid, owner_id=str(atomic.owner_id))
        )
    if str(atomic.subkind) == "list_continuation_insert":
        return (
            "<br>"
            + _render_badge_html(int(atomic.cid), f"{atomic.original_part_id}_badge", owner_id=str(atomic.owner_id))
            + _render_insert_span(str(atomic.inserted_text or ""), atomic.span_id, f"{atomic.original_part_id}_ins", atomic.cid, owner_id=str(atomic.owner_id))
        )
    return (
        _render_badge_html(int(atomic.cid), f"{atomic.original_part_id}_badge", owner_id=str(atomic.owner_id))
        + _render_insert_span(atomic.inserted_text, atomic.span_id, f"{atomic.original_part_id}_ins", atomic.cid, owner_id=str(atomic.owner_id))
    )


def _delete_span_open_html(
    d: ResolvedAtomicChange,
    span_id: str,
    *,
    owner_ids: list[str] | tuple[str, ...] | set[str] | None = None,
    span_ids: list[str] | tuple[str, ...] | set[str] | None = None,
) -> str:
    attrs = [
        'class="kgg-inline-diff-del"',
        f'data-kgg-op-id="{html.escape(d.original_part_id + "_del", quote=True)}"',
        f'data-kgg-span-id="{html.escape(span_id, quote=True)}"',
        _build_owner_scope_attrs(
            owner_id=str(d.owner_id or ""),
            owner_ids=owner_ids,
            op_role="delete-text",
        ),
    ]
    span_ids_attr = _build_span_ids_attr(span_ids)
    if span_ids_attr:
        attrs.append(span_ids_attr)
    return f'<span {" ".join(attrs)}>'


_INLINE_BALANCE_TAGS = {"b", "i", "code", "sub", "a"}


def _parse_inline_balance_tag(tag_html: str) -> tuple[str, str] | None:
    m = re.match(r"^<\s*(/)?\s*([a-zA-Z0-9]+)\b", str(tag_html or ""))
    if not m:
        return None
    is_close = bool(m.group(1))
    name = str(m.group(2) or "").lower()
    if name not in _INLINE_BALANCE_TAGS:
        return None
    return ("close" if is_close else "open", name)


def _render_delete_html_preserving_tag_balance(
    d: ResolvedAtomicChange,
    span_id: str,
    inner_html: str,
    *,
    owner_ids: list[str] | tuple[str, ...] | set[str] | None = None,
    span_ids: list[str] | tuple[str, ...] | set[str] | None = None,
) -> str:
    """
    Wrap deleted content without ever spanning across raw HTML tags.

    Important:
    - tags stay outside the delete span wrappers
    - only text islands between tags are wrapped
    - this avoids invalid nesting like:
        <b><span del>... </b> text </span>
    """
    src = str(inner_html or "")
    if not src:
        return ""

    parts = re.split(r"(<[^>]+>)", src)
    out: list[str] = []
    span_open = False
    local_inline_stack: list[str] = []

    for part in list(parts or []):
        if not part:
            continue
        is_tag = part.startswith("<") and part.endswith(">")
        if is_tag:
            parsed = _parse_inline_balance_tag(part)
            if parsed is None:
                if span_open:
                    out.append("</span>")
                    span_open = False
                out.append(part)
                continue

            tag_mode, tag_name = parsed

            if tag_mode == "open":
                if not span_open:
                    out.append(
                        _delete_span_open_html(
                            d,
                            span_id,
                            owner_ids=owner_ids,
                            span_ids=span_ids,
                        )
                    )
                    span_open = True
                out.append(part)
                local_inline_stack.append(str(tag_name))
                continue

            # closing inline tag:
            # - if it closes a tag opened inside THIS segment, keep it inside the span
            # - otherwise it belongs to outer context, so close delete span before it
            if local_inline_stack and str(local_inline_stack[-1]) == str(tag_name):
                if not span_open:
                    out.append(
                        _delete_span_open_html(
                            d,
                            span_id,
                            owner_ids=owner_ids,
                            span_ids=span_ids,
                        )
                    )
                    span_open = True
                out.append(part)
                local_inline_stack.pop()
                continue

            if span_open:
                out.append("</span>")
                span_open = False
            out.append(part)
            continue

        if not span_open:
            out.append(
                _delete_span_open_html(
                    d,
                    span_id,
                    owner_ids=owner_ids,
                    span_ids=span_ids,
                )
            )
            span_open = True
        out.append(part)

    if span_open:
        out.append("</span>")

    return "".join(out)


def _render_overlapping_delete_payload_html(
    matches: list[tuple[int, int, ResolvedAtomicChange, str]],
    deleted_html: str,
    emitted_badge_owner_ids: set[str],
) -> str:
    """
    Render one delete segment, possibly owned by multiple comments/parts.

    Important:
    - matches passed here all represent the same already split [a,b) segment
    - the deleted text must be emitted once, not once per owner
    - overlapping owners are represented via data-kgg-owner-ids and
      data-kgg-span-ids on the same rendered delete wrapper

    This avoids invalid/misleading nested markup such as:
      <span owner=#4><span owner=#5>same text</span></span>
    """
    ordered = sorted(
        list(matches or []),
        key=lambda t: (int(t[2].cid), str(t[2].original_part_id), str(t[3])),
    )
    if not ordered:
        return str(deleted_html or "")

    badges_parts: list[str] = []
    for _ra, _rb, d, _span_id in ordered:
        owner_id = str(d.owner_id or "")
        if owner_id and owner_id not in emitted_badge_owner_ids:
            emitted_badge_owner_ids.add(owner_id)
            badges_parts.append(_render_badge_html(int(d.cid), f"{d.original_part_id}_badge", owner_id=str(d.owner_id)))

    primary = ordered[0][2]
    primary_span_id = str(ordered[0][3] or primary.span_id or "")
    owner_ids = _owner_ids_for_atomics([t[2] for t in ordered])
    span_ids: list[str] = []
    seen_span_ids: set[str] = set()
    for _ra, _rb, _d, span_id in ordered:
        sid = str(span_id or "").strip()
        if sid and sid not in seen_span_ids:
            span_ids.append(sid)
            seen_span_ids.add(sid)

    wrapped = _render_delete_html_preserving_tag_balance(
        primary,
        primary_span_id,
        str(deleted_html or ""),
        owner_ids=owner_ids,
        span_ids=span_ids,
    )
    return "".join(badges_parts) + wrapped


def _collect_parallel_meta_rows_from_members(
    members: list[ResolvedAtomicChange],
) -> list[tuple[str, str, int, str]]:
    rows: list[tuple[str, str, int, str]] = []
    seen: set[tuple[str, str, int, str]] = set()
    for m in list(members or []):
        rec = (
            str(m.span_id or ""),
            str(m.owner_id or ""),
            int(m.cid or 0),
            str(m.original_part_id or ""),
        )
        if not rec[0] or rec in seen:
            continue
        seen.add(rec)
        rows.append(rec)
    return rows


def _materialize_inline_replace_payload_from_members(
    original: str,
    gstart: int,
    deletes: list[ResolvedAtomicChange],
    inserts: list[ResolvedAtomicChange],
) -> str:
    """
    Shared boundary-based materializer for local inline replace clusters.

    Rule:
    - split delete ranges at inline insert anchors
    - build one common boundary grid
    - emit inserts at the boundary position
    - then emit either the delete fragment for [a,b) or the unchanged original text

    This is the general "deletes first, split into common subpieces, inserts at
    the separating boundaries" rule in one place.
    """
    src = str(original or "")
    g0 = int(gstart)

    ins_list = sorted(
        list(inserts or []),
        key=lambda a: (int(a.source_start), int(a.cid), str(a.original_part_id), str(a.atomic_id)),
    )
    del_list = sorted(
        list(deletes or []),
        key=lambda a: (int(a.source_start), int(a.source_end), int(a.cid), str(a.original_part_id), str(a.atomic_id)),
    )

    insert_by_rel: dict[int, list[ResolvedAtomicChange]] = {}
    for ins in ins_list:
        rel = int(ins.source_start) - g0
        insert_by_rel.setdefault(rel, []).append(ins)

    split_delete_segments: list[tuple[int, int, ResolvedAtomicChange, str]] = []
    for d in del_list:
        cut_points = {int(d.source_start), int(d.source_end)}
        for ins in ins_list:
            if int(ins.source_start) > int(d.source_start) and int(ins.source_start) < int(d.source_end):
                cut_points.add(int(ins.source_start))
        pts = sorted(cut_points)
        seg_no = 0
        for a, b in zip(pts, pts[1:]):
            if int(a) >= int(b):
                continue
            seg_no += 1
            rel_a = int(a) - g0
            rel_b = int(b) - g0
            span_id = f"{d.span_id}:s{seg_no}"
            split_delete_segments.append((rel_a, rel_b, d, span_id))

    split_delete_segments.sort(key=lambda t: (int(t[0]), int(t[1]), int(t[2].cid), str(t[2].original_part_id)))

    boundaries = {0, len(src)}
    for rel_a, rel_b, _d, _sid in split_delete_segments:
        boundaries.add(int(rel_a))
        boundaries.add(int(rel_b))
    for rel in insert_by_rel.keys():
        boundaries.add(int(rel))
    ordered = sorted(boundaries)

    out: list[str] = []
    emitted_delete_badge_owner_ids: set[str] = set()

    def emit_inserts_at(rel_pos: int) -> None:
        for ins in insert_by_rel.get(int(rel_pos), []):
            out.append(_render_insert_payload_html(ins))

    emit_inserts_at(0)

    for a, b in zip(ordered, ordered[1:]):
        if int(b) <= int(a):
            continue

        segs = [
            cand for cand in split_delete_segments
            if int(cand[0]) == int(a) and int(cand[1]) == int(b)
        ]

        if segs:
            deleted_html = src[int(a):int(b)]
            out.append(
                _render_overlapping_delete_payload_html(
                    segs,
                    deleted_html,
                    emitted_delete_badge_owner_ids,
                )
            )
        else:
            out.append(src[int(a):int(b)])

        emit_inserts_at(int(b))

    return "".join(out)


def _render_insert_body_inline(text: str) -> str:
    body_html = minimd_to_html_text("_inline", text)
    body_html = re.sub(r'^(?:<ol>|<ul>|<li>|</li>|</ol>|</ul>)+', '', body_html)
    body_html = re.sub(r'(?:</li>|</ol>|</ul>)+$', '', body_html)
    return body_html


def _build_atomic_changes(block_key: str, baseline_old_minimd: str, baseline_html: str, changes: list[ChangeRequest]) -> tuple[list[ResolvedAtomicChange], list[ResolvedDeleteSegment], list[dict[str, Any]]]:
    generic_delete_changes = [
        ch for ch in changes
        if str(ch.change_class or "") not in {"table_row_delete", "list_item_delete"}
        and not str(ch.change_class or "").startswith("coarse_inline_delete")
    ]
    delete_segments = _split_overlapping_delete_segments_from_changes(baseline_old_minimd, generic_delete_changes)
    atomics: list[ResolvedAtomicChange] = []
    audit: list[dict[str, Any]] = []

    for ch in [c for c in list(changes or []) if str(c.change_class or "") == "list_item_delete" and str(c.old_text or "")]:
        src_start = _resolve_source_anchor_from_raw_offset(block_key, baseline_old_minimd, baseline_html, int(ch.old_start))
        src_end = _resolve_source_anchor_from_raw_offset(block_key, baseline_old_minimd, baseline_html, int(ch.old_end))
        list_kind = "ol" if re.match(r"^\s*\d+\.\s+", str(ch.old_text or "")) else "ul"
        list_level = 2 if re.match(r"^\s{2}-\s+", str(ch.old_text or "")) else 1
        atomics.append(ResolvedAtomicChange(
            atomic_id=f"atomic:{str(ch.change_id)}:list_delete",
            original_part_id=str(ch.change_id),
            cid=int(ch.cid),
            block_key=str(block_key),
            kind="delete",
            subkind="list_item_delete",
            raw_start=int(ch.old_start),
            raw_end=int(ch.old_end),
            source_start=int(src_start),
            source_end=int(src_end),
            deleted_text=str(ch.old_text or ""),
            list_kind=str(list_kind),
            list_level=int(list_level),
            span_id=_make_span_id("del", int(ch.cid), str(ch.change_id), "list_item"),
            owner_id=_make_owner_id(int(ch.cid), str(ch.change_id)),
        ))
        audit.append({
            "part_id": str(ch.change_id),
            "kind": "list_delete",
            "start": int(ch.old_start),
            "end": int(ch.old_end),
            "html_source_start": int(src_start),
            "html_source_end": int(src_end),
            "resolved_status": "resolved",
        })

    for seg in delete_segments:
        seg.html_source_start = _resolve_source_anchor_from_raw_offset(block_key, baseline_old_minimd, baseline_html, seg.start)
        seg.html_source_end = _resolve_source_anchor_from_raw_offset(block_key, baseline_old_minimd, baseline_html, seg.end)
        seg.resolved = True
        for owner in list(seg.owners or []):
            cid = int(owner["cid"])
            part_id = str(owner["part_id"])
            atomics.append(ResolvedAtomicChange(
                atomic_id=f"atomic:{seg.segment_id}:{cid}:{part_id}",
                original_part_id=part_id,
                cid=cid,
                block_key=str(block_key),
                kind="delete",
                subkind="delete_range",
                raw_start=int(seg.start),
                raw_end=int(seg.end),
                source_start=int(seg.html_source_start),
                source_end=int(seg.html_source_end),
                deleted_text=str(seg.deleted_text),
                span_id=_make_span_id("del", cid, part_id, seg.segment_id),
                owner_id=_make_owner_id(cid, part_id),
                delete_segment_id=str(seg.segment_id),
            ))
        audit.append({
            "segment_id": seg.segment_id,
            "kind": "delete",
            "start": seg.start,
            "end": seg.end,
            "html_source_start": seg.html_source_start,
            "html_source_end": seg.html_source_end,
            "status": "resolved",
            "owners": seg.owners,
        })

    for ch in [c for c in list(changes or []) if str(c.change_class or "").startswith("coarse_inline_delete") and str(c.old_text or "")]:
        src_start = _resolve_source_anchor_from_raw_offset(block_key, baseline_old_minimd, baseline_html, int(ch.old_start))
        src_end = _resolve_source_anchor_from_raw_offset(block_key, baseline_old_minimd, baseline_html, int(ch.old_end))
        atomics.append(ResolvedAtomicChange(
            atomic_id=f"atomic:{str(ch.change_id)}:coarse_delete",
            original_part_id=str(ch.change_id),
            cid=int(ch.cid),
            block_key=str(block_key),
            kind="delete",
            subkind="coarse_inline_delete",
            raw_start=int(ch.old_start),
            raw_end=int(ch.old_end),
            source_start=int(src_start),
            source_end=int(src_end),
            deleted_text=str(ch.old_text or ""),
            span_id=_make_span_id("del", int(ch.cid), str(ch.change_id), "coarse"),
            owner_id=_make_owner_id(int(ch.cid), str(ch.change_id)),
        ))
        audit.append({
            "part_id": str(ch.change_id),
            "kind": "coarse_delete",
            "start": int(ch.old_start),
            "end": int(ch.old_end),
            "html_source_start": int(src_start),
            "html_source_end": int(src_end),
            "resolved_status": "resolved",
        })

    for ch in changes:
        if str(ch.change_class or "") == "table_row_delete" and ch.old_text and not ch.new_text:
            start = _resolve_source_anchor_from_raw_offset(block_key, baseline_old_minimd, baseline_html, int(ch.old_start))
            end = _resolve_source_anchor_from_raw_offset(block_key, baseline_old_minimd, baseline_html, int(ch.old_end))
            atomics.append(ResolvedAtomicChange(
                atomic_id=f"atomic:{ch.change_id}",
                original_part_id=str(ch.change_id),
                cid=int(ch.cid),
                block_key=str(block_key),
                kind="delete",
                subkind="table_row_delete",
                raw_start=int(ch.old_start),
                raw_end=int(ch.old_end),
                source_start=int(start),
                source_end=int(end),
                deleted_text=str(ch.old_text or ""),
                span_id=_make_span_id("del", int(ch.cid), str(ch.change_id), "table_row"),
                owner_id=_make_owner_id(int(ch.cid), str(ch.change_id)),
            ))
            audit.append({
                "part_id": ch.change_id,
                "kind": "table_row_delete",
                "abs_pos": int(ch.old_start),
                "resolved_status": "resolved",
                "html_source_start": int(start),
                "html_source_end": int(end),
            })
            continue
        if not ch.new_text:
            continue
        if (
            str(ch.change_class or "") in {
                "list_item_insert",
                "list_continuation_insert",
                "table_row_insert",
                "pre_inline_insert",
                "table_cell_inline_insert",
            }
            or str(ch.change_class or "").startswith("coarse_inline_insert")
        ):
            subparts: list[tuple[str, str]] = [
                (str(ch.new_text or ""), str(ch.change_class or "auto"))
            ]
        else:
            structured = list(_split_structured_insert_fragments(str(ch.new_text or "")) or [])
            if len(structured) > 1 or any(str(f.change_class or "inline_insert") != "inline_insert" for f in structured):
                subparts = [
                    (str(f.text or ""), str(f.change_class or "inline_insert"))
                    for f in structured
                    if str(f.text or "")
                ]
            else:
                subparts = [
                    (str(p or ""), str(ch.change_class or "auto"))
                    for p in _normalize_mixed_insert_subparts(_split_mixed_insert_text(ch.new_text))
                    if str(p or "")
                ]
        multi = len(subparts) > 1
        for idx, sub in enumerate(subparts, start=1):
            part_text, part_class = sub
            is_list, list_kind, list_level, body = _detect_list_insert(part_text)
            subkind = "inline_insert"

            if str(part_class or "").startswith("coarse_inline_insert"):
                start = _resolve_source_anchor_from_raw_offset(block_key, baseline_old_minimd, baseline_html, ch.old_start)
                subkind = "coarse_inline_insert"
                is_list = False
                list_kind = ""
                list_level = 0
            elif part_class == "table_row_insert":
                start = _resolve_source_anchor_from_raw_offset(block_key, baseline_old_minimd, baseline_html, ch.old_start)
                subkind = "table_row_insert"
                is_list = False
                list_kind = ""
                list_level = 0
            elif part_class == "pre_inline_insert":
                start = _resolve_source_anchor_from_raw_offset(block_key, baseline_old_minimd, baseline_html, ch.old_start)
                subkind = "pre_inline_insert"
                is_list = False
                list_kind = ""
                list_level = 0
            elif part_class == "table_cell_inline_insert":
                start = _resolve_source_anchor_from_raw_offset(block_key, baseline_old_minimd, baseline_html, ch.old_start)
                subkind = "table_cell_inline_insert"
                is_list = False
                list_kind = ""
                list_level = 0
            elif part_class == "list_continuation_insert":
                if str(ch.list_parent_hint or "").strip():
                    start = _resolve_parent_level1_source_anchor_for_level2(
                        block_key,
                        baseline_old_minimd,
                        baseline_html,
                        int(ch.old_start),
                        str(ch.list_parent_hint or ""),
                    )
                else:
                    start = _resolve_source_anchor_from_raw_offset(block_key, baseline_old_minimd, baseline_html, ch.old_start)
                subkind = "list_continuation_insert"
                part_text = str(part_text or "").strip()
                is_list = False
                list_kind = ""
                list_level = 0
            elif part_class == "list_item_insert" or is_list:
                if int(list_level) == 2:
                    parent_hint = str(ch.list_parent_hint or "").strip()
                    if not parent_hint:
                        parent_hint = _find_parent_level1_hint_from_minimd(
                            str(baseline_old_minimd or ""),
                            int(ch.old_start),
                        )
                    start = _resolve_parent_level1_source_anchor_for_level2(
                        block_key,
                        baseline_old_minimd,
                        baseline_html,
                        int(ch.old_start),
                        str(parent_hint or ""),
                    )
                    subkind = "list_item_insert"
                else:
                    start = _resolve_source_anchor_from_raw_offset(block_key, baseline_old_minimd, baseline_html, ch.old_start)
                    subkind = "list_item_insert"
            else:
                start = _resolve_source_anchor_from_raw_offset(block_key, baseline_old_minimd, baseline_html, ch.old_start)
                subkind = "inline_insert"
            suffix = f":m{idx}" if multi else ""
            span_suffix = f"m{idx}" if multi else ""
            atomics.append(ResolvedAtomicChange(
                atomic_id=f"atomic:{ch.change_id}{suffix}",
                original_part_id=str(ch.change_id),
                cid=int(ch.cid),
                block_key=str(block_key),
                kind="insert",
                subkind=str(subkind),
                raw_start=int(ch.old_start),
                raw_end=int(ch.old_end),
                source_start=int(start),
                source_end=int(start),
                inserted_text=str(part_text),
                list_kind=str(list_kind),
                list_level=int(list_level),
                span_id=_make_span_id("ins", int(ch.cid), str(ch.change_id), span_suffix),
                owner_id=_make_owner_id(int(ch.cid), str(ch.change_id)),
                list_host_mode=str(ch.list_host_mode or ""),
                list_prev_block_kind=str(ch.list_prev_block_kind or ""),
                list_prev_block_raw_start=int(ch.list_prev_block_raw_start or 0),
                list_prev_block_raw_end=int(ch.list_prev_block_raw_end or 0),
                list_next_block_kind=str(ch.list_next_block_kind or ""),
                list_next_block_raw_start=int(ch.list_next_block_raw_start or 0),
                list_next_block_raw_end=int(ch.list_next_block_raw_end or 0),
                list_parent_hint=str(ch.list_parent_hint or ""),
            ))
            audit.append({
                "part_id": ch.change_id,
                "kind": "ins",
                "abs_pos": int(ch.old_start),
                "resolved_status": "resolved",
                "html_source_start": int(start),
                "at_list_item_boundary": bool(is_list),
                "sub_index": int(idx),
                "mixed_split_total": int(len(subparts)),
            })

    atomics.sort(key=lambda a: (a.source_start, 0 if a.kind == "delete" else 1, a.cid, a.original_part_id, a.atomic_id))
    return atomics, delete_segments, audit


def _normalized_list_parent_hint(text: str) -> str:
    return " ".join(str(text or "").split()).strip()


def _normalized_inserted_list_body_text(inserted_text: str) -> str:
    _is_list, _list_kind, _list_level, body = _detect_list_insert(str(inserted_text or ""))
    return " ".join(str(body or "").split()).strip()


def _level2_targets_inserted_level1(child: ResolvedAtomicChange, parent: ResolvedAtomicChange) -> bool:
    if str(child.subkind) != "list_item_insert" or str(parent.subkind) != "list_item_insert":
        return False
    if int(child.list_level or 0) != 2 or int(parent.list_level or 0) != 1:
        return False
    child_hint = _normalized_list_parent_hint(str(child.list_parent_hint or ""))
    parent_body = _normalized_inserted_list_body_text(str(parent.inserted_text or ""))
    if not child_hint or not parent_body:
        return False
    return str(child_hint) == str(parent_body)


def _touch_or_overlap(a_start: int, a_end: int, b_start: int, b_end: int) -> bool:
    return not (a_end < b_start or b_end < a_start)


def _atomic_effective_raw_end(a: ResolvedAtomicChange) -> int:
    return int(a.raw_start if a.kind == "insert" else a.raw_end)


def _atomic_effective_source_end(a: ResolvedAtomicChange) -> int:
    return int(a.source_start if a.kind == "insert" else a.source_end)


def _same_interaction_zone(a: ResolvedAtomicChange, b: ResolvedAtomicChange) -> bool:
    if int(a.raw_start) == int(b.raw_start):
        return True
    if _touch_or_overlap(int(a.raw_start), _atomic_effective_raw_end(a), int(b.raw_start), _atomic_effective_raw_end(b)):
        return True
    if int(a.source_start) == int(b.source_start):
        return True
    if str(a.original_part_id) == str(b.original_part_id):
        return True
    return False


def _build_change_groups(block_key: str, baseline_html: str, atomics: list[ResolvedAtomicChange]) -> list[ChangeGroup]:
    groups: list[ChangeGroup] = []
    if not atomics:
        return groups

    host_by_atomic_id: dict[str, tuple[str, int, int]] = {}
    for a in atomics:
        host_by_atomic_id[str(a.atomic_id)] = _resolve_list_host_for_atomic(baseline_html, a)

    cur_members: list[ResolvedAtomicChange] = [atomics[0]]
    gseq = 0

    def _flush(members: list[ResolvedAtomicChange]) -> None:
        nonlocal gseq
        if not members:
            return
        gseq += 1
        raw_start = min(int(m.raw_start) for m in members)
        raw_end = max(_atomic_effective_raw_end(m) for m in members)
        src_start = min(int(m.source_start) for m in members)
        src_end = max(_atomic_effective_source_end(m) for m in members)
        list_members = [m for m in members if str(m.subkind) == "list_item_insert"]
        if list_members:
            host_kind, host_start, host_end = host_by_atomic_id[str(list_members[0].atomic_id)]
        else:
            host_kind, host_start, host_end = ("text", int(src_start), int(src_end))
        groups.append(ChangeGroup(
            group_id=f"grp_{gseq:04d}",
            block_key=str(block_key),
            group_raw_start=int(raw_start),
            group_raw_end=int(raw_end),
            group_source_start=int(src_start),
            group_source_end=int(src_end),
            members=list(members),
            has_delete=any(m.kind == "delete" for m in members),
            has_inline_insert=any(m.subkind in {"inline_insert", "coarse_inline_insert"} for m in members),
            has_list_insert=any(m.subkind == "list_item_insert" for m in members),
            host_kind=str(host_kind),
            host_source_start=int(host_start),
            host_source_end=int(host_end),
        ))

    def _same_list_host(a: ResolvedAtomicChange, b: ResolvedAtomicChange) -> bool:
        if str(a.subkind) != "list_item_insert" or str(b.subkind) != "list_item_insert":
            return False
        ah = host_by_atomic_id[str(a.atomic_id)]
        bh = host_by_atomic_id[str(b.atomic_id)]
        return str(ah[0]) == str(bh[0]) and int(ah[1]) == int(bh[1]) and int(ah[2]) == int(bh[2])

    def _same_inserted_parent_chain(a: ResolvedAtomicChange, b: ResolvedAtomicChange) -> bool:
        return _level2_targets_inserted_level1(a, b) or _level2_targets_inserted_level1(b, a)

    def _list_host_conflict(a: ResolvedAtomicChange, b: ResolvedAtomicChange) -> bool:
        if str(a.subkind) != "list_item_insert" or str(b.subkind) != "list_item_insert":
            return False
        if _same_inserted_parent_chain(a, b):
            return False
        return not _same_list_host(a, b)

    for nxt in atomics[1:]:
        same_zone = any(_same_interaction_zone(m, nxt) for m in cur_members)
        same_host = any(_same_list_host(m, nxt) for m in cur_members)
        host_conflict = any(_list_host_conflict(m, nxt) for m in cur_members)
        if (same_zone or same_host or any(_same_inserted_parent_chain(m, nxt) for m in cur_members)) and not host_conflict:
            cur_members.append(nxt)
        else:
            _flush(cur_members)
            cur_members = [nxt]
    _flush(cur_members)
    return groups


# ---------------------------------------------------------------------------
# Operations and compose
# ---------------------------------------------------------------------------

def _resolve_level1_list_host_from_context(
    block_key: str,
    baseline_old_minimd: str,
    baseline_html: str,
    atomic: ResolvedAtomicChange,
) -> tuple[str, int, int]:
    mode = str(atomic.list_host_mode or "")
    if not mode:
        return ("text", int(atomic.source_start), int(atomic.source_start))

    if mode == "insert_into_existing_top_level_before_next_item":
        next_kind = str(atomic.list_next_block_kind or "")
        next_start_raw = int(atomic.list_next_block_raw_start or 0)
        if next_kind and next_start_raw > 0:
            pos = _find_direct_li_open_for_top_level_item_start(
                block_key,
                baseline_old_minimd,
                baseline_html,
                str(next_kind),
                int(next_start_raw),
            )
            return (f"before_next_{next_kind}_item", int(pos), int(pos))

    if mode == "split_existing_top_level_before_next_item":
        current_kind = str(atomic.list_prev_block_kind or atomic.list_next_block_kind or "")
        next_start_raw = int(atomic.list_next_block_raw_start or 0)
        if current_kind and next_start_raw > 0:
            pos = _find_direct_li_open_for_top_level_item_start(
                block_key,
                baseline_old_minimd,
                baseline_html,
                str(current_kind),
                int(next_start_raw),
            )
            return (f"split_before_next_{current_kind}_item", int(pos), int(pos))

    if mode == "append_same_top_level_before_close":
        prev_kind = str(atomic.list_prev_block_kind or "")
        prev_end_raw = int(atomic.list_prev_block_raw_end or 0)
        if prev_kind:
            blk_anchor = _resolve_source_anchor_from_raw_offset(
                block_key,
                baseline_old_minimd,
                baseline_html,
                int(prev_end_raw),
            )
            html_block = _pick_html_top_level_list_block(
                baseline_html,
                str(prev_kind),
                int(blk_anchor),
            )
            if html_block is not None:
                before_close = int(html_block.close_start or html_block.close_end or blk_anchor)
                return (f"before_{prev_kind}_close", int(before_close), int(before_close))

    if mode == "prepend_same_top_level_at_open":
        next_kind = str(atomic.list_next_block_kind or "")
        next_start_raw = int(atomic.list_next_block_raw_start or 0)
        if next_kind:
            blk_anchor = _resolve_source_anchor_from_raw_offset(
                block_key,
                baseline_old_minimd,
                baseline_html,
                int(next_start_raw),
            )
            html_block = _pick_html_top_level_list_block(
                baseline_html,
                str(next_kind),
                int(blk_anchor),
            )
            if html_block is not None:
                pos = int(html_block.open_end or blk_anchor)
                return (f"after_{next_kind}_open", int(pos), int(pos))

    if mode == "start_new_top_level_block":
        prev_kind = str(atomic.list_prev_block_kind or "")
        prev_end_raw = int(atomic.list_prev_block_raw_end or 0)
        if prev_kind:
            blk_anchor = _resolve_source_anchor_from_raw_offset(
                block_key,
                baseline_old_minimd,
                baseline_html,
                int(prev_end_raw),
            )
            html_block = _pick_html_top_level_list_block(
                baseline_html,
                str(prev_kind),
                int(blk_anchor),
            )
            if html_block is not None:
                after_close = int(html_block.close_end or blk_anchor)
                return ("after_top_level_block", int(after_close), int(after_close))
        return ("after_top_level_block", int(atomic.source_start), int(atomic.source_start))

    return ("text", int(atomic.source_start), int(atomic.source_start))


def _find_table_tbody_end_for_insert(html_text: str, anchor_source_start: int) -> int:
    src = str(html_text)
    pos = src.find("</tbody>", int(anchor_source_start))
    if pos >= 0:
        return int(pos)
    pos = src.find("</table>", int(anchor_source_start))
    if pos >= 0:
        return int(pos)
    pos = src.rfind("</tbody>")
    if pos >= 0:
        return int(pos)
    pos = src.rfind("</table>")
    return len(src) if pos < 0 else int(pos)


def _find_parent_li_close_for_level2(html_text: str, anchor_source_start: int) -> int:
    """
    Return the closing </li> of the current level-1 parent item.

    This is the correct host seam for a new level-2 UL insert.
    It must not use the previous </li><li seam, because that would attach the
    nested UL to the wrong sibling item in cases like:
      - append nested UL under the second UL/OL item near EOF
    """
    src = str(html_text)
    start = max(0, int(anchor_source_start))
    pos = src.find("</li>", start)
    if pos >= 0:
        return int(pos)
    pos = src.rfind("</li>", 0, start + 1)
    return len(src) if pos < 0 else int(pos)


def _find_existing_child_list_bounds_for_parent_li(html_text: str, anchor_source_start: int, tag_name: str) -> tuple[int, int] | None:
    """
    For one parent-li anchor context, detect whether that parent li already
    contains a direct child <ul> ... </ul>.

    Returns:
      (child_ul_open_end, child_ul_close_start)
    where:
      - child_ul_open_end points right after "<ul>"
      - child_ul_close_start points at the "<" of "</ul>"
    """
    src = str(html_text)
    start = max(0, int(anchor_source_start))
    li_close = src.find("</li>", start)
    if li_close < 0:
        li_close = src.rfind("</li>", 0, start + 1)
        if li_close < 0:
            return None

    open_tag = f"<{str(tag_name)}>"
    close_tag = f"</{str(tag_name)}>"
    child_open = src.find(open_tag, start, li_close)
    if child_open < 0:
        return None

    child_close = src.find(close_tag, child_open, li_close)
    if child_close < 0:
        return None

    return (int(child_open + len(open_tag)), int(child_close))


def _resolve_level2_list_host_for_atomic(
    baseline_html: str,
    atomic: ResolvedAtomicChange,
) -> tuple[str, int, int]:
    """
    Resolve the host for one ul level-2 insert.

    Rules:
    - If the parent li already has a child <ul>, insert as sibling li into that
      existing child list: before_child_ul_close
    - Otherwise create a new child <ul> before the parent </li>: before_li_close
    """
    tag_name = "ol" if str(atomic.list_kind or "").lower() == "ol" else "ul"
    bounds = _find_existing_child_list_bounds_for_parent_li(
        baseline_html,
        int(atomic.source_start),
        str(tag_name),
    )
    if bounds is not None:
        _open_end, close_start = bounds
        return (f"before_child_{tag_name}_close", int(close_start), int(close_start))

    pos = _find_parent_li_close_for_level2(baseline_html, int(atomic.source_start))
    return ("before_li_close", int(pos), int(pos))


def _find_list_continuation_insert_offset(html_text: str, anchor_source_start: int) -> int:
    """
    Return the insertion seam for a continuation that belongs to the current li.

    Rule:
      - insert before a nested child <ul> if one starts before the closing </li>
      - otherwise insert before the closing </li>
    """
    src = str(html_text)
    start = max(0, int(anchor_source_start))
    li_close = src.find("</li>", start)
    if li_close < 0:
        li_close = src.rfind("</li>", 0, start + 1)
        if li_close < 0:
            return len(src)

    child_ul = src.find("<ul>", start)
    if child_ul >= 0 and child_ul < li_close:
        return int(child_ul)

    return int(li_close)


def _resolve_list_host_for_atomic(baseline_html: str, atomic: ResolvedAtomicChange) -> tuple[str, int, int]:
    """
    Resolve the explicit list host for one list insert atomic.

    Returns:
      (host_kind, host_source_start, host_source_end)

    host_kind values:
      - before_li_close
      - before_ul_close
      - before_ol_close
      - text
    """
    if str(atomic.host_kind or ""):
        return (
            str(atomic.host_kind),
            int(atomic.host_source_start),
            int(atomic.host_source_end),
        )

    if str(atomic.subkind) != "list_item_insert":
        if str(atomic.subkind) == "table_row_insert":
            pos = _find_table_tbody_end_for_insert(baseline_html, int(atomic.source_start))
            return ("before_tbody_close", int(pos), int(pos))
        if str(atomic.subkind) == "table_row_delete":
            return ("table_row_delete", int(atomic.source_start), int(atomic.source_end))
        if str(atomic.subkind) == "list_continuation_insert":
            pos = _find_list_continuation_insert_offset(baseline_html, int(atomic.source_start))
            return ("before_li_continuation_break", int(pos), int(pos))
        pos = int(atomic.source_start)
        return ("text", pos, pos)

    list_kind = str(atomic.list_kind or "").lower()
    list_level = int(atomic.list_level or 0)

    if list_kind in {"ul", "ol"} and list_level == 2:
        return _resolve_level2_list_host_for_atomic(baseline_html, atomic)

    if list_level == 1:
        if str(atomic.host_kind or ""):
            return (
                str(atomic.host_kind),
                int(atomic.host_source_start),
                int(atomic.host_source_end),
            )
        pos = int(atomic.source_start)
        return ("text", int(pos), int(pos))

    pos = int(atomic.source_start)
    return ("text", pos, pos)


def _should_drop_inline_atomic(atomic: ResolvedAtomicChange, baseline_html: str, group: ChangeGroup) -> bool:
    if atomic.kind != "insert" or atomic.subkind != "inline_insert":
        return False
    txt = str(atomic.inserted_text or "")
    if txt not in {".", ",", ";", ":"}:
        return False
    pos = int(atomic.source_start)
    if 0 <= pos < len(baseline_html) and baseline_html[pos:pos + 1] == txt:
        return True
    if group.has_delete and 0 <= pos - 1 < len(baseline_html) and baseline_html[pos - 1:pos] == txt:
        return True
    return False


def _infer_reopened_ol_start_for_split(baseline_html: str, host_source_start: int) -> int:
    """
    For split_before_next_ol_item payloads, infer the start number for the
    reopened baseline <ol>.

    Example:
      <ol><li>A</li><li>B</li><li>C</li></ol>
      insert a different list kind before C

    Payload must become:
      </ol><ul>...</ul><ol start="3">

    This is intentionally HTML-side: host_source_start is the resolved HTML
    source offset of the next baseline <li> inside the top-level ordered list.
    """
    pos = int(host_source_start or 0)
    ol_blocks = [
        blk
        for blk in _collect_html_top_level_list_blocks(str(baseline_html or ""))
        if str(blk.list_kind or "") == "ol"
    ]

    for blk in ol_blocks:
        if str(blk.list_kind or "") != "ol":
            continue
        close_start = int(blk.close_start or blk.close_end or blk.open_end)
        if not (int(blk.open_end or blk.open_start) <= pos <= close_start):
            continue
        previous_items = sum(1 for li_pos in list(blk.li_open_starts or []) if int(li_pos) < pos)
        start = int(previous_items) + 1
        return int(start) if int(start) > 1 else 0

    # Fallback:
    # If close_start was not detected or the anchor sits just before / inside
    # the next direct <li>, infer the continuation number from direct li opens.
    # This is the important split_before_next_ol_item case:
    #   </ol><ul>...</ul><ol start="N">
    for blk in ol_blocks:
        li_starts = [int(x) for x in list(blk.li_open_starts or [])]
        if not li_starts:
            continue
        for idx, li_pos in enumerate(li_starts):
            next_li_pos = int(li_starts[idx + 1]) if idx + 1 < len(li_starts) else int(blk.close_start or blk.close_end or len(str(baseline_html or "")))
            if int(pos) <= int(li_pos):
                start = int(idx) + 1
                return int(start) if int(start) > 1 else 0
            if int(li_pos) <= int(pos) < int(next_li_pos):
                start = int(idx) + 1
                return int(start) if int(start) > 1 else 0
    return 0


def _build_reopened_ol_start_attr(reopen_ol_start: int) -> str:
    """
    Mark a reopened right-hand baseline <ol> after a structural split.

    The static start value is only the baseline fallback. The actual rendered
    start may depend on which structural insert owners are currently visible.
    Consumers can recompute the visible start from the DOM for elements carrying
    data-kgg-reopen-ol="true".
    """
    start = int(reopen_ol_start or 0)
    attrs = [
        ' data-kgg-reopen-ol="true"',
        f' data-kgg-reopen-ol-start-baseline="{int(start if start > 1 else 1)}"',
    ]
    if start > 1:
        attrs.insert(0, f' start="{int(start)}"')
    return "".join(attrs)


def _build_list_item_insert_payload(
    *,
    host_kind: str,
    list_kind: str,
    list_level: int,
    badge_html: str,
    span_html: str,
    op_attrs: str = "",
    reopen_ol_start: int = 0,
) -> str:
    item_html = f"<li{str(op_attrs or '')}>{badge_html}{span_html}</li>"
    lk = str(list_kind or "").lower()
    hk = str(host_kind or "")
    level = int(list_level or 0)

    if level == 1:
        if hk in {
            "before_next_ol_item",
            "before_next_ul_item",
            "before_ol_close",
            "before_ul_close",
            "after_ol_open",
            "after_ul_open",
        }:
            return item_html

        if hk == "split_before_next_ol_item":
            reopen_attrs = _build_reopened_ol_start_attr(int(reopen_ol_start or 0))
            return f"</ol><{lk}{str(op_attrs or '')}><li{str(op_attrs or '')}>{badge_html}{span_html}</li></{lk}><ol{reopen_attrs}>"

        if hk == "split_before_next_ul_item":
            return f"</ul><{lk}{str(op_attrs or '')}><li{str(op_attrs or '')}>{badge_html}{span_html}</li></{lk}><ul>"

        if hk == "after_top_level_block":
            return f"<{lk}{str(op_attrs or '')}><li{str(op_attrs or '')}>{badge_html}{span_html}</li></{lk}>"

    if level == 2:
        if hk == "before_li_close":
            return f"<{lk}{str(op_attrs or '')}><li{str(op_attrs or '')}>{badge_html}{span_html}</li></{lk}>"

        if hk in {"before_child_ul_close", "before_child_ol_close"}:
            return item_html

    return badge_html + span_html


def _build_one_list_item_html(
    atomic: ResolvedAtomicChange,
    *,
    owner_ids: list[str] | tuple[str, ...] | set[str] | None = None,
) -> str:
    badge = _render_badge_html(
        int(atomic.cid),
        f"{atomic.original_part_id}_badge",
        owner_id=str(atomic.owner_id),
        owner_ids=owner_ids,
    )
    _is_list, _list_kind, _list_level, body = _detect_list_insert(atomic.inserted_text)
    body_html = _render_insert_body_inline(body)
    span = _render_insert_span_from_body_html(
        body_html,
        atomic.span_id,
        f"{atomic.original_part_id}_ins",
        atomic.cid,
        owner_id=str(atomic.owner_id),
        owner_ids=owner_ids,
    )
    structural_attrs = _build_structural_op_attrs_for_atomic(
        atomic,
        owner_ids=owner_ids,
        op_role="insert-list-item",
    )
    return f"<li{structural_attrs}>{badge}{span}</li>"


def _build_nested_list_children_html(children: list[ResolvedAtomicChange]) -> str:
    if not children:
        return ""
    parts: list[str] = []
    i = 0
    ordered = list(children or [])
    while i < len(ordered):
        first = ordered[i]
        tag = "ol" if str(first.list_kind or "").lower() == "ol" else "ul"
        group: list[ResolvedAtomicChange] = []
        while i < len(ordered):
            cur = ordered[i]
            cur_tag = "ol" if str(cur.list_kind or "").lower() == "ol" else "ul"
            if cur_tag != tag:
                break
            group.append(cur)
            i += 1
        owner_ids = _owner_ids_for_atomics(group)
        list_attrs = _build_structural_op_attrs_for_atomic(
            first,
            owner_ids=owner_ids,
            op_role="insert-child-list",
        )
        inner = [_build_one_list_item_html(cur, owner_ids=owner_ids) for cur in group]
        parts.append(f"<{tag}{list_attrs}>{''.join(inner)}</{tag}>")
    return "".join(parts)


def _build_inserted_parent_with_children_operation(
    block_key: str,
    baseline_html: str,
    parent: ResolvedAtomicChange,
    children: list[ResolvedAtomicChange],
) -> HtmlOperation:
    owner_ids = _owner_ids_for_atomics([parent] + list(children or []))
    badge = _render_badge_html(
        int(parent.cid),
        f"{parent.original_part_id}_badge",
        owner_id=str(parent.owner_id),
        owner_ids=owner_ids,
    )
    _is_list, list_kind, _list_level, body = _detect_list_insert(parent.inserted_text)
    body_html = _render_insert_body_inline(body)
    span = _render_insert_span_from_body_html(
        body_html,
        parent.span_id,
        f"{parent.original_part_id}_ins",
        parent.cid,
        owner_id=str(parent.owner_id),
        owner_ids=owner_ids,
    )
    structural_attrs = _build_structural_op_attrs_for_atomic(
        parent,
        owner_ids=owner_ids,
        op_role="insert-list-root",
    )
    reopen_ol_start = 0
    if str(parent.host_kind or "") == "split_before_next_ol_item":
        reopen_ol_start = _infer_reopened_ol_start_for_split(
            str(baseline_html or ""),
            int(parent.host_source_start if parent.host_kind else parent.source_start),
        )
    payload = _build_list_item_insert_payload(
        host_kind=str(parent.host_kind),
        list_kind=str(list_kind),
        list_level=1,
        badge_html=str(badge),
        span_html=str(span) + _build_nested_list_children_html(children),
        op_attrs=str(structural_attrs),
        reopen_ol_start=int(reopen_ol_start),
    )
    start = int(parent.host_source_start if parent.host_kind else parent.source_start)
    return HtmlOperation(
        op_id=f"{parent.atomic_id}:list_insert_with_children",
        block_key=block_key,
        op_type="insert_fragment",
        anchor_source_start=int(start),
        anchor_source_end=int(start),
        payload_before=str(payload),
        meta={
            "span_ids": [str(parent.span_id)] + [str(c.span_id) for c in list(children or [])],
            "owner_ids": [str(parent.owner_id)] + [str(c.owner_id) for c in list(children or [])],
            "comment_ids": [int(parent.cid)] + [int(c.cid) for c in list(children or [])],
            "part_ids": [str(parent.original_part_id)] + [str(c.original_part_id) for c in list(children or [])],
            "reopen_ol_start": int(reopen_ol_start),
            "reopen_ol_dynamic": bool(str(parent.host_kind or "") == "split_before_next_ol_item"),
        },
    )


def _materialize_inserted_parent_child_bundles(
    block_key: str,
    baseline_html: str,
    members: list[ResolvedAtomicChange],
) -> tuple[list[HtmlOperation], list[ResolvedAtomicChange]]:
    """
    Materialize all inserted level-1 parent + inserted level-2 child bundles
    that can be detected inside one already grouped member list.

    Important:
    - multiple inserted level-1 parents may coexist in the same group
    - each parent must be allowed to consume only its own level-2 children
    - remaining members are returned for the normal downstream path
    """
    ops: list[HtmlOperation] = []
    consumed_ids: set[str] = set()

    list_inserts = [m for m in list(members or []) if str(m.subkind) == "list_item_insert"]
    parent_candidates = [
        m for m in list_inserts
        if int(m.list_level or 0) == 1
    ]
    parent_candidates.sort(
        key=lambda m: (
            int(m.host_source_start or m.source_start),
            int(m.cid),
            str(m.original_part_id),
            str(m.atomic_id),
        )
    )

    for parent in parent_candidates:
        if str(parent.atomic_id) in consumed_ids:
            continue
        child_members = [
            m for m in list_inserts
            if str(m.atomic_id) not in consumed_ids
            and int(m.cid) == int(parent.cid)
            and _level2_targets_inserted_level1(m, parent)
        ]
        if not child_members:
            continue
        child_members.sort(
            key=lambda m: (
                int(m.source_start),
                int(m.cid),
                str(m.original_part_id),
                str(m.atomic_id),
            )
        )
        ops.append(
            _build_inserted_parent_with_children_operation(
                block_key,
                baseline_html,
                parent,
                child_members,
            )
        )
        consumed_ids.add(str(parent.atomic_id))
        for child in child_members:
            consumed_ids.add(str(child.atomic_id))

    remaining = [
        m for m in list(members or [])
        if str(m.atomic_id) not in consumed_ids
    ]
    return ops, remaining


def _make_insert_operation(block_key: str, baseline_html: str, group: ChangeGroup, atomic: ResolvedAtomicChange) -> HtmlOperation:
    host_kind = str(atomic.host_kind or group.host_kind or "")
    host_source_start = int(atomic.host_source_start if int(atomic.host_source_start or 0) else int(group.host_source_start or atomic.source_start))
    badge = _render_badge_html(int(atomic.cid), f"{atomic.original_part_id}_badge", owner_id=str(atomic.owner_id))
    if atomic.subkind == "table_row_insert":
        row_cells = [c[0] for c in _split_table_row_with_cell_offsets(str(atomic.inserted_text or ""))]
        cell_parts: list[str] = []
        for idx, cell in enumerate(row_cells):
            inner = _render_insert_span_from_body_html(
                _render_inline_html(cell),
                atomic.span_id,
                f"{atomic.original_part_id}_ins",
                atomic.cid,
                owner_id=str(atomic.owner_id),
            )
            if idx == 0:
                inner = badge + inner
            cell_parts.append(f"<td>{inner}</td>")
        start = int(host_source_start) if host_kind == "before_tbody_close" else _find_table_tbody_end_for_insert(baseline_html, int(atomic.source_start))
        row_attrs = _build_structural_op_attrs_for_atomic(atomic, op_role="insert-table-row")
        return HtmlOperation(
            op_id=f"{atomic.atomic_id}:table_row_insert",
            block_key=block_key,
            op_type="insert_fragment",
            anchor_source_start=int(start),
            anchor_source_end=int(start),
            payload_before=f"<tr{row_attrs}>{''.join(cell_parts)}</tr>",
            meta={"span_id": atomic.span_id, "owner_id": atomic.owner_id, "comment_id": atomic.cid, "part_id": atomic.original_part_id},
        )
    if atomic.subkind == "pre_inline_insert":
        span = _render_insert_span_from_body_html(
            html.escape(str(atomic.inserted_text or ""), quote=False),
            atomic.span_id,
            f"{atomic.original_part_id}_ins",
            atomic.cid,
            owner_id=str(atomic.owner_id),
        )
        return HtmlOperation(
            op_id=f"{atomic.atomic_id}:pre_insert",
            block_key=block_key,
            op_type="insert_fragment",
            anchor_source_start=int(atomic.source_start),
            anchor_source_end=int(atomic.source_start),
            payload_before=badge + span,
            meta={"span_id": atomic.span_id, "owner_id": atomic.owner_id, "comment_id": atomic.cid, "part_id": atomic.original_part_id},
        )
    if atomic.subkind == "table_cell_inline_insert":
        span = _render_insert_span_from_body_html(
            _render_inline_html(str(atomic.inserted_text or "")),
            atomic.span_id,
            f"{atomic.original_part_id}_ins",
            atomic.cid,
            owner_id=str(atomic.owner_id),
        )
        return HtmlOperation(
            op_id=f"{atomic.atomic_id}:table_cell_insert",
            block_key=block_key,
            op_type="insert_fragment",
            anchor_source_start=int(atomic.source_start),
            anchor_source_end=int(atomic.source_start),
            payload_before=badge + span,
            meta={"span_id": atomic.span_id, "owner_id": atomic.owner_id, "comment_id": atomic.cid, "part_id": atomic.original_part_id},
        )
    if atomic.subkind == "list_continuation_insert":
        span = _render_insert_span(atomic.inserted_text, atomic.span_id, f"{atomic.original_part_id}_ins", atomic.cid, owner_id=str(atomic.owner_id))
        start = int(host_source_start) if host_kind == "before_li_continuation_break" else _find_list_continuation_insert_offset(baseline_html, int(atomic.source_start))
        return HtmlOperation(
            op_id=f"{atomic.atomic_id}:list_continuation_insert",
            block_key=block_key,
            op_type="insert_fragment",
            anchor_source_start=int(start),
            anchor_source_end=int(start),
            payload_before=f"<br>{badge}{span}",
            meta={"span_id": atomic.span_id, "owner_id": atomic.owner_id, "comment_id": atomic.cid, "part_id": atomic.original_part_id},
        )
    if atomic.subkind == "coarse_inline_insert":
        block = _render_coarse_insert_block(atomic.inserted_text, atomic.span_id, f"{atomic.original_part_id}_ins", atomic.cid, owner_id=str(atomic.owner_id))
        return HtmlOperation(
            op_id=f"{atomic.atomic_id}:coarse_insert",
            block_key=block_key,
            op_type="insert_fragment",
            anchor_source_start=int(atomic.source_start),
            anchor_source_end=int(atomic.source_start),
            payload_before=badge + block,
            meta={"span_id": atomic.span_id, "owner_id": atomic.owner_id, "comment_id": atomic.cid, "part_id": atomic.original_part_id, "coarse_insert": True},
        )
    if atomic.subkind == "inline_insert":
        span = _render_insert_span(atomic.inserted_text, atomic.span_id, f"{atomic.original_part_id}_ins", atomic.cid, owner_id=str(atomic.owner_id))
        return HtmlOperation(
            op_id=f"{atomic.atomic_id}:insert",
            block_key=block_key,
            op_type="insert_fragment",
            anchor_source_start=int(atomic.source_start),
            anchor_source_end=int(atomic.source_start),
            payload_before=badge + span,
            meta={"span_id": atomic.span_id, "owner_id": atomic.owner_id, "comment_id": atomic.cid, "part_id": atomic.original_part_id},
        )

    _is_list, list_kind, list_level, body = _detect_list_insert(atomic.inserted_text)
    body_html = _render_insert_body_inline(body)
    span = _render_insert_span_from_body_html(
        body_html,
        atomic.span_id,
        f"{atomic.original_part_id}_ins",
        atomic.cid,
        owner_id=str(atomic.owner_id),
    )
    structural_attrs = _build_structural_op_attrs_for_atomic(atomic, op_role="insert-list-root")
    start = int(host_source_start if host_kind else atomic.source_start)
    reopen_ol_start = 0
    if str(host_kind or "") == "split_before_next_ol_item":
        reopen_ol_start = _infer_reopened_ol_start_for_split(
            str(baseline_html or ""),
            int(host_source_start),
        )
    payload = _build_list_item_insert_payload(
        host_kind=str(host_kind),
        list_kind=str(list_kind),
        list_level=int(list_level),
        badge_html=str(badge),
        span_html=str(span),
        op_attrs=str(structural_attrs),
        reopen_ol_start=int(reopen_ol_start),
    )
    return HtmlOperation(
        op_id=f"{atomic.atomic_id}:list_insert",
        block_key=block_key,
        op_type="insert_fragment",
        anchor_source_start=int(start),
        anchor_source_end=int(start),
        payload_before=payload,
        meta={
            "span_id": atomic.span_id,
            "owner_id": atomic.owner_id,
            "comment_id": atomic.cid,
            "part_id": atomic.original_part_id,
            "reopen_ol_start": int(reopen_ol_start),
            "reopen_ol_dynamic": bool(str(host_kind or "") == "split_before_next_ol_item"),
        },
    )


def _build_group_replace_operation(block_key: str, baseline_html: str, group: ChangeGroup) -> HtmlOperation:
    gstart = int(group.group_source_start)
    gend = int(group.group_source_end)
    original = str(baseline_html or "")[gstart:gend]

    members = sorted(group.members, key=lambda a: (int(a.source_start), 0 if a.kind == "delete" else 1, int(a.cid), str(a.original_part_id), str(a.atomic_id)))
    deletes = [m for m in members if m.kind == "delete"]
    inserts = [
        m for m in members
        if m.kind == "insert"
        and m.subkind in {"inline_insert", "coarse_inline_insert", "pre_inline_insert", "table_cell_inline_insert", "list_continuation_insert"}
    ]

    inserts = [m for m in inserts if not _should_drop_inline_atomic(m, baseline_html, group)]
    payload_html = _materialize_inline_replace_payload_from_members(
        original=original,
        gstart=int(gstart),
        deletes=deletes,
        inserts=inserts,
    )

    if any(str(d.subkind) == "coarse_inline_delete" for d in deletes):
        delete_parts: list[str] = []
        for d in sorted(deletes, key=lambda a: (int(a.source_start), int(a.cid), str(a.original_part_id), str(a.atomic_id))):
            if str(d.subkind) == "coarse_inline_delete":
                delete_parts.append(
                    _render_badge_html(int(d.cid), f"{d.original_part_id}_badge", owner_id=str(d.owner_id))
                    + _render_coarse_delete_block(d)
                )
                continue
            delete_parts.append(
                _render_badge_html(int(d.cid), f"{d.original_part_id}_badge", owner_id=str(d.owner_id))
                + _render_delete_html_preserving_tag_balance(d, str(d.span_id), str(baseline_html or "")[int(d.source_start):int(d.source_end)])
            )
        insert_parts = [
            _render_insert_payload_html(ins)
            for ins in sorted(inserts, key=lambda a: (int(a.source_start), int(a.cid), str(a.original_part_id), str(a.atomic_id)))
        ]
        payload_html = "".join(delete_parts + insert_parts)

    meta_rows = _collect_parallel_meta_rows_from_members(members)
    span_ids = [str(span_id) for span_id, _owner_id, _cid, _part_id in meta_rows]
    owner_ids = [str(owner_id) for _span_id, owner_id, _cid, _part_id in meta_rows]
    comment_ids = [int(cid) for _span_id, _owner_id, cid, _part_id in meta_rows]
    part_ids = [str(part_id) for _span_id, _owner_id, _cid, part_id in meta_rows]

    return HtmlOperation(
        op_id=f"{group.group_id}:replace",
        block_key=block_key,
        op_type="replace_range",
        anchor_source_start=gstart,
        anchor_source_end=gend,
        payload_before=str(payload_html),
        meta={
            "group_id": str(group.group_id),
            "owner_ids": owner_ids,
            "span_ids": span_ids,
            "comment_ids": comment_ids,
            "part_ids": part_ids,
        },
    )


def _find_table_row_html_bounds(baseline_html: str, source_start: int, source_end: int) -> tuple[int, int]:
    src = str(baseline_html or "")
    start = max(0, int(source_start))
    end = max(start, int(source_end))
    tr_open = src.rfind("<tr>", 0, start + 1)
    if tr_open < 0:
        tr_open = src.rfind("<tr>", 0, end + 1)
    tr_close = src.find("</tr>", end)
    if tr_close < 0:
        tr_close = src.find("</tr>", start)
    if tr_open < 0 or tr_close < 0:
        return (int(start), int(end))
    return (int(tr_open), int(tr_close + len("</tr>")))


def _find_li_html_bounds(baseline_html: str, source_start: int, source_end: int) -> tuple[int, int]:
    src = str(baseline_html or "")
    start = max(0, int(source_start))
    end = max(start, int(source_end))
    li_open = src.rfind("<li>", 0, start + 1)
    if li_open < 0:
        li_open = src.rfind("<li>", 0, end + 1)
    li_close = src.find("</li>", end)
    if li_close < 0:
        li_close = src.find("</li>", start)
    if li_open < 0 or li_close < 0:
        return (int(start), int(end))
    return (int(li_open), int(li_close + len("</li>")))


def _render_deleted_table_row_html(atomic: ResolvedAtomicChange) -> str:
    cells = [c[0] for c in _split_table_row_with_cell_offsets(str(atomic.deleted_text or ""))]
    parts: list[str] = []
    for idx, cell in enumerate(cells):
        badge = _render_badge_html(int(atomic.cid), f"{atomic.original_part_id}_badge", owner_id=str(atomic.owner_id)) if idx == 0 else ""
        body = _render_inline_html(cell)
        span_id = f"{atomic.span_id}:c{idx+1}"
        parts.append(
            "<td>"
            + badge
            + _delete_span_open_html(atomic, span_id)
            + body
            + "</span>"
            + "</td>"
        )
    row_attrs = _build_structural_op_attrs_for_atomic(atomic, op_role="delete-table-row")
    return f"<tr{row_attrs}>{''.join(parts)}</tr>"


def _render_deleted_list_item_html(atomic: ResolvedAtomicChange) -> str:
    badge = _render_badge_html(int(atomic.cid), f"{atomic.original_part_id}_badge", owner_id=str(atomic.owner_id))
    body = _render_inline_html(str(atomic.deleted_text or ""))
    structural_attrs = _build_structural_op_attrs_for_atomic(atomic, op_role="delete-list-item")
    return (
        f"<li{structural_attrs}>"
        + badge
        + _delete_span_open_html(atomic, str(atomic.span_id))
        + body
        + "</span>"
        + "</li>"
    )


def _render_shared_deleted_list_item_html(atoms: list[ResolvedAtomicChange]) -> str:
    ordered = sorted(
        list(atoms or []),
        key=lambda d: (int(d.cid), str(d.original_part_id), str(d.atomic_id)),
    )
    if not ordered:
        return ""

    primary = ordered[0]
    owner_ids = _owner_ids_for_atomics(ordered)
    span_ids: list[str] = []
    seen_span_ids: set[str] = set()
    for d in ordered:
        sid = str(getattr(d, "span_id", "") or "").strip()
        if sid and sid not in seen_span_ids:
            span_ids.append(sid)
            seen_span_ids.add(sid)

    badges = "".join(
        _render_badge_html(
            int(d.cid),
            f"{d.original_part_id}_badge",
            owner_id=str(d.owner_id),
            owner_ids=owner_ids,
        )
        for d in ordered
    )
    body = _render_inline_html(str(primary.deleted_text or ""))
    structural_attrs = _build_structural_op_attrs_for_atomic(
        primary,
        owner_ids=owner_ids,
        op_role="delete-list-item",
    )
    return (
        f"<li{structural_attrs}>"
        + badges
        + _delete_span_open_html(
            primary,
            str(primary.span_id),
            owner_ids=owner_ids,
            span_ids=span_ids,
        )
        + body
        + "</span>"
        + "</li>"
    )


def _materialize_group_to_operations(block_key: str, baseline_html: str, group: ChangeGroup) -> list[HtmlOperation]:
    ops: list[HtmlOperation] = []
    members = sorted(group.members, key=lambda a: (int(a.source_start), 0 if a.kind == "delete" else 1, int(a.cid), str(a.original_part_id), str(a.atomic_id)))
    deletes = [m for m in members if m.kind == "delete"]
    inserts = [m for m in members if m.kind == "insert"]

    list_inserts = [m for m in inserts if str(m.subkind) == "list_item_insert"]
    if list_inserts:
        bundled_ops, remaining = _materialize_inserted_parent_child_bundles(
            block_key,
            baseline_html,
            members,
        )
        if bundled_ops:
            ops.extend(list(bundled_ops))
            if not remaining:
                return ops
            tail_group = ChangeGroup(
                group_id=str(group.group_id) + ":tail",
                block_key=str(group.block_key),
                group_raw_start=int(group.group_raw_start),
                group_raw_end=int(group.group_raw_end),
                group_source_start=int(group.group_source_start),
                group_source_end=int(group.group_source_end),
                members=list(remaining),
                has_delete=any(m.kind == "delete" for m in remaining),
                has_inline_insert=any(m.subkind in {"inline_insert", "coarse_inline_insert"} for m in remaining),
                has_list_insert=any(m.subkind == "list_item_insert" for m in remaining),
                host_kind=str(group.host_kind),
                host_source_start=int(group.host_source_start),
                host_source_end=int(group.host_source_end),
            )
            ops.extend(_materialize_group_to_operations(block_key, baseline_html, tail_group))
            return ops

    for d in [m for m in members if m.kind == "delete" and m.subkind == "table_row_delete"]:
        row_start, row_end = _find_table_row_html_bounds(baseline_html, int(d.source_start), int(d.source_end))
        ops.append(HtmlOperation(
            op_id=f"{d.atomic_id}:table_row_delete",
            block_key=block_key,
            op_type="replace_range",
            anchor_source_start=int(row_start),
            anchor_source_end=int(row_end),
            payload_before=_render_deleted_table_row_html(d),
            meta={
                "span_id": d.span_id,
                "owner_id": d.owner_id,
                "comment_id": d.cid,
                "part_id": d.original_part_id,
            },
        ))

    list_delete_groups: dict[tuple[int, int], list[ResolvedAtomicChange]] = {}
    for d in [m for m in members if m.kind == "delete" and m.subkind == "list_item_delete"]:
        li_start, li_end = _find_li_html_bounds(
            baseline_html,
            int(d.source_start),
            int(d.source_end),
        )
        list_delete_groups.setdefault((int(li_start), int(li_end)), []).append(d)

    for (li_start, li_end), group_deletes in sorted(list_delete_groups.items(), key=lambda item: (int(item[0][0]), int(item[0][1]))):
        ordered_group_deletes = sorted(
            list(group_deletes or []),
            key=lambda d: (int(d.cid), str(d.original_part_id), str(d.atomic_id)),
        )
        primary = ordered_group_deletes[0]
        ops.append(HtmlOperation(
            op_id=f"{primary.atomic_id}:list_item_delete",
            block_key=block_key,
            op_type="replace_range",
            anchor_source_start=int(li_start),
            anchor_source_end=int(li_end),
            payload_before=(
                _render_shared_deleted_list_item_html(ordered_group_deletes)
                if len(ordered_group_deletes) > 1
                else _render_deleted_list_item_html(primary)
            ),
            meta={
                "span_ids": [str(d.span_id) for d in ordered_group_deletes],
                "owner_ids": [str(d.owner_id) for d in ordered_group_deletes],
                "comment_ids": [int(d.cid) for d in ordered_group_deletes],
                "part_ids": [str(d.original_part_id) for d in ordered_group_deletes],
            },
        ))

    boundary_inserts = [
        m for m in inserts
        if str(m.subkind) in {"inline_insert", "coarse_inline_insert", "pre_inline_insert", "table_cell_inline_insert", "list_continuation_insert"}
    ]
    if (
        not group.has_list_insert
        and not any(m.subkind in {"table_row_delete", "list_item_delete"} for m in members)
        and int(group.group_source_end) > int(group.group_source_start)
        and (len(boundary_inserts) > 0 or len(deletes) > 0)
        and (len(members) > 1 or len(deletes) > 0)
    ):
        return [_build_group_replace_operation(block_key, baseline_html, group)]

    # normalize punctuation-only inline inserts in mixed groups
    inserts = [m for m in inserts if not _should_drop_inline_atomic(m, baseline_html, group)]

    # delete ranges are always split at insert anchors inside the delete span
    for d in [m for m in deletes if m.subkind not in {"table_row_delete", "list_item_delete"}]:
        cut_points = {int(d.source_start), int(d.source_end)}
        for ins in inserts:
            if int(ins.source_start) > int(d.source_start) and int(ins.source_start) < int(d.source_end):
                cut_points.add(int(ins.source_start))
        pts = sorted(cut_points)
        seg_no = 0
        for a, b in zip(pts, pts[1:]):
            if int(a) >= int(b):
                continue
            seg_no += 1
            span_id = f"{d.span_id}:s{seg_no}"
            badge = _render_badge_html(int(d.cid), f"{d.original_part_id}_badge", owner_id=str(d.owner_id))
            ops.append(HtmlOperation(
                op_id=f"{d.atomic_id}:delete:s{seg_no}",
                block_key=block_key,
                op_type="delete_range",
                anchor_source_start=int(a),
                anchor_source_end=int(b),
                payload_before=badge if seg_no == 1 else "",
                payload_inside_start=_delete_span_open_html(d, span_id),
                payload_inside_end="</span>",
                meta={
                    "span_id": span_id,
                    "owner_id": d.owner_id,
                    "comment_id": d.cid,
                    "part_id": d.original_part_id,
                    "base_span_id": d.span_id,
                },
            ))

    # inserts afterwards, stable order
    for ins in sorted(inserts, key=lambda a: (int(a.source_start), int(a.cid), str(a.original_part_id), str(a.atomic_id))):
        ops.append(_make_insert_operation(block_key, baseline_html, group, ins))

    return ops


def _build_article_interaction_rows_from_block_results(
    block_results: dict[str, ComposeBlockChangeSetResult],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    grouped: dict[str, dict[str, Any]] = {}

    for block_key, res in dict(block_results or {}).items():
        for style_row in list(getattr(res, "style_table", []) or []):
            owner_id = str(style_row.get("owner_id") or "")
            if not owner_id:
                continue
            cid = int(style_row.get("cid") or 0)
            rec = grouped.setdefault(owner_id, {
                "owner_id": owner_id,
                "cid": cid,
                "block_keys": set(),
                "part_ids": set(),
                "span_ids": set(),
                "recommended_states": set(),
                "default_state": str(style_row.get("default_state") or "normal"),
            })
            rec["block_keys"].add(str(block_key))
            for pid in list(style_row.get("part_ids") or []):
                rec["part_ids"].add(str(pid))
            for sid in list(style_row.get("span_ids") or []):
                rec["span_ids"].add(str(sid))

        for grp in list(getattr(res, "interaction_table", []) or []):
            state_map = dict(grp.get("recommended_state_map") or {})
            owner_ids = [str(x) for x in list(grp.get("owner_ids") or []) if str(x or "")]
            for owner_id in owner_ids:
                rec = grouped.setdefault(owner_id, {
                    "owner_id": owner_id,
                    "cid": 0,
                    "block_keys": set(),
                    "part_ids": set(),
                    "span_ids": set(),
                    "recommended_states": set(),
                    "default_state": "normal",
                })
                rec["block_keys"].add(str(block_key))
                for sid, state in state_map.items():
                    rec["span_ids"].add(str(sid))
                    rec["recommended_states"].add(str(state or "normal"))

    for owner_id, rec in grouped.items():
        states = set(rec.get("recommended_states") or set())
        if "none" in states:
            recommended = "none"
        elif "warn" in states:
            recommended = "warn"
        else:
            recommended = str(rec.get("default_state") or "normal")
        rows.append({
            "owner_id": str(owner_id),
            "cid": int(rec.get("cid") or 0),
            "block_keys": sorted(str(x) for x in set(rec.get("block_keys") or set())),
            "part_ids": sorted(str(x) for x in set(rec.get("part_ids") or set())),
            "span_ids": sorted(str(x) for x in set(rec.get("span_ids") or set())),
            "recommended_state": str(recommended),
            "default_state": str(rec.get("default_state") or "normal"),
        })

    rows.sort(key=lambda r: (int(r.get("cid") or 0), str(r.get("owner_id") or "")))
    return rows


def _build_product_operations(block_key: str, baseline_html: str, groups: list[ChangeGroup]) -> list[HtmlOperation]:
    ops: list[HtmlOperation] = []
    for group in groups:
        ops.extend(_materialize_group_to_operations(block_key, baseline_html, group))
    ops.sort(key=lambda o: (o.anchor_source_start, 0 if o.op_type == 'delete_range' else 1, o.op_id))
    return ops


def _apply_html_operations(baseline_html: str, operations: list[HtmlOperation]) -> str:
    src = str(baseline_html)
    shift = 0
    for op in operations:
        if op.op_type == "insert_fragment":
            pos = int(op.anchor_source_start) + shift
            src = src[:pos] + str(op.payload_before or "") + src[pos:]
            shift += len(str(op.payload_before or ""))
        elif op.op_type == "replace_range":
            s = int(op.anchor_source_start) + shift
            e = int(op.anchor_source_end) + shift
            payload = str(op.payload_before or "")
            src = src[:s] + payload + src[e:]
            shift += len(payload) - (e - s)
        elif op.op_type == "delete_range":
            s = int(op.anchor_source_start) + shift
            e = int(op.anchor_source_end) + shift
            middle = src[s:e]
            payload = str(op.payload_before or "") + str(op.payload_inside_start or "") + middle + str(op.payload_inside_end or "")
            src = src[:s] + payload + src[e:]
            shift += len(str(op.payload_before or "")) + len(str(op.payload_inside_start or "")) + len(str(op.payload_inside_end or ""))
    return src


def _build_span_registry_and_style_table(block_key: str, operations: list[HtmlOperation]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    registry = []
    owner_to_spans: dict[str, list[str]] = {}
    def _append_row(op: HtmlOperation, span_id: str, owner_id: str, cid: int, part_id: str) -> None:
        kind = "delete" if str(span_id).startswith("del:") else "insert" if str(span_id).startswith("ins:") else ("delete" if str(op.op_type) == "delete_range" else "insert")
        row = SpanRegistryRow(
            span_id=str(span_id),
            owner_id=str(owner_id or ""),
            cid=int(cid or 0),
            part_id=str(part_id or ""),
            block_key=str(block_key),
            kind=str(kind),
            source_start=int(op.anchor_source_start),
            source_end=int(op.anchor_source_end),
            css_class_normal="kgg-inline-diff-del" if kind == "delete" else "kgg-inline-diff-ins",
            css_class_warn="kgg-inline-diff-warn-del" if kind == "delete" else "kgg-inline-diff-warn-ins",
            css_class_none="kgg-inline-diff-none-del" if kind == "delete" else "kgg-inline-diff-none-ins",
        )
        registry.append(asdict(row))
        owner_to_spans.setdefault(str(row.owner_id), []).append(str(row.span_id))
    for op in operations:
        meta = dict(op.meta or {})
        span_id = str(meta.get("span_id") or "")
        if span_id:
            _append_row(op, span_id, str(meta.get("owner_id") or ""), int(meta.get("comment_id") or 0), str(meta.get("part_id") or ""))
            continue
        span_ids = [str(x or "") for x in list(meta.get("span_ids") or []) if str(x or "")]
        owner_ids = [str(x or "") for x in list(meta.get("owner_ids") or [])]
        comment_ids = [int(x or 0) for x in list(meta.get("comment_ids") or [])]
        part_ids = [str(x or "") for x in list(meta.get("part_ids") or [])]
        for idx, sid in enumerate(span_ids):
            _append_row(
                op,
                str(sid),
                str(owner_ids[idx] if idx < len(owner_ids) else ""),
                int(comment_ids[idx] if idx < len(comment_ids) else 0),
                str(part_ids[idx] if idx < len(part_ids) else ""),
            )
    style_table = [
        {
            "owner_id": owner,
            "span_ids": span_ids,
            "cid": int(next((r["cid"] for r in registry if str(r.get("owner_id")) == owner), 0)),
            "part_ids": sorted({str(r.get("part_id") or "") for r in registry if str(r.get("owner_id")) == owner}),
            "default_state": "normal",
            "available_states": ["normal", "warn", "none"],
        }
        for owner, span_ids in sorted(owner_to_spans.items())
    ]
    return registry, style_table


def _build_interaction_table(
    block_key: str,
    groups: list[ChangeGroup],
    operations: list[HtmlOperation],
) -> list[dict[str, Any]]:
    """
    Build a product-facing interaction table.

    Current scope:
    - delete-delete groups by identical delete segment range
    - delete-insert groups by insert anchor inside delete span
    - insert-insert groups by identical insert anchor

    recommended_state_map is conservative for now:
    - first CID in sorted order wins normal
    - other delete-delete members become warn
    - delete-insert and insert-insert default to normal for all members
    """
    out: list[dict[str, Any]] = []
    op_rows = []
    def _append_op_row(op: HtmlOperation, span_id: str, owner_id: str, cid: int, part_id: str) -> None:
        op_rows.append({
            "span_id": str(span_id),
            "owner_id": str(owner_id or ""),
            "cid": int(cid or 0),
            "part_id": str(part_id or ""),
            "op_type": str(op.op_type),
            "start": int(op.anchor_source_start),
            "end": int(op.anchor_source_end),
        })
    for op in operations:
        meta = dict(op.meta or {})
        span_id = str(meta.get("span_id") or "")
        if span_id:
            _append_op_row(op, span_id, str(meta.get("owner_id") or ""), int(meta.get("comment_id") or 0), str(meta.get("part_id") or ""))
            continue
        span_ids = [str(x or "") for x in list(meta.get("span_ids") or []) if str(x or "")]
        owner_ids = [str(x or "") for x in list(meta.get("owner_ids") or [])]
        comment_ids = [int(x or 0) for x in list(meta.get("comment_ids") or [])]
        part_ids = [str(x or "") for x in list(meta.get("part_ids") or [])]
        for idx, sid in enumerate(span_ids):
            _append_op_row(
                op,
                str(sid),
                str(owner_ids[idx] if idx < len(owner_ids) else ""),
                int(comment_ids[idx] if idx < len(comment_ids) else 0),
                str(part_ids[idx] if idx < len(part_ids) else ""),
            )

    seen_group_keys: set[tuple[str, tuple[str, ...], tuple[str, ...]]] = set()
    for group in groups:
        members = [r for r in op_rows if group.group_source_start <= int(r["start"]) <= group.group_source_end or group.group_source_start <= int(r["end"]) <= group.group_source_end]
        if not members:
            continue
        interaction_type = "mixed" if group.has_delete and (group.has_inline_insert or group.has_list_insert) else ("delete-delete" if group.has_delete else "insert-insert")
        members_sorted = sorted(members, key=lambda r: (int(r["start"]), int(r["cid"]), str(r["part_id"]), str(r["span_id"])))
        dedupe_key = (
            str(interaction_type),
            tuple(str(m["span_id"]) for m in members_sorted),
            tuple(str(m["owner_id"]) for m in members_sorted),
        )
        if dedupe_key in seen_group_keys:
            continue
        seen_group_keys.add(dedupe_key)
        winner_span_ids: list[str] = []
        recommended_state_map: dict[str, str] = {}
        if interaction_type == "delete-delete":
            owner_set = {str(m["owner_id"] or "") for m in members_sorted}
            cid_set = {int(m["cid"] or 0) for m in members_sorted}
            if len(owner_set) <= 1 or len(cid_set) <= 1:
                recommended_state_map = {str(m["span_id"]): "normal" for m in members_sorted}
            else:
                delete_members = [m for m in members_sorted if m["op_type"] == "delete_range"]
                if delete_members:
                    winner_span_ids = [str(delete_members[0]["span_id"])]
                for m in members_sorted:
                    if m["op_type"] == "delete_range":
                        recommended_state_map[str(m["span_id"])] = "normal" if str(m["span_id"]) in winner_span_ids else "warn"
                    else:
                        recommended_state_map[str(m["span_id"])] = "normal"
        else:
            recommended_state_map = {str(m["span_id"]): "normal" for m in members_sorted}
        out.append({
            "group_id": str(group.group_id),
            "block_key": str(block_key),
            "group_type": interaction_type,
            "group_raw_start": int(group.group_raw_start),
            "group_raw_end": int(group.group_raw_end),
            "group_source_start": int(group.group_source_start),
            "group_source_end": int(group.group_source_end),
            "span_ids": [str(m["span_id"]) for m in members_sorted],
            "owner_ids": [str(m["owner_id"]) for m in members_sorted],
            "cid_order": [int(m["cid"]) for m in members_sorted],
            "winner_span_ids": winner_span_ids,
            "recommended_state_map": recommended_state_map,
        })
    return out


def _interaction_rows_by_span_id(interaction_table: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for row in list(interaction_table or []):
        rec = dict(row or {})
        recommended_state_map = dict(rec.get("recommended_state_map") or {})
        winner_span_ids = {str(x) for x in list(rec.get("winner_span_ids") or [])}
        for span_id in list(rec.get("span_ids") or []):
            sid = str(span_id or "")
            if not sid:
                continue
            out[sid] = {
                "group_id": str(rec.get("group_id") or ""),
                "group_type": str(rec.get("group_type") or ""),
                "recommended_state": str(recommended_state_map.get(sid) or "normal"),
                "is_winner": bool(sid in winner_span_ids),
            }
    return out


def _style_rows_by_owner_id(style_table: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for row in list(style_table or []):
        rec = dict(row or {})
        owner_id = str(rec.get("owner_id") or "")
        if owner_id:
            out[owner_id] = rec
    return out


def _block_label_from_key(block_key: str) -> str:
    s = str(block_key or "").strip().replace("_", " ")
    return s[:1].upper() + s[1:] if s else ""


def _normalize_inline_text(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "").replace("\r\n", "\n").replace("\r", "\n")).strip()


def _line_bounds_at_offset(src: str, offset: int) -> tuple[int, int]:
    s = str(src or "")
    pos = max(0, min(int(offset or 0), len(s)))
    line_start = s.rfind("\n", 0, pos)
    if line_start < 0:
        line_start = 0
    else:
        line_start += 1
    line_end = s.find("\n", pos)
    if line_end < 0:
        line_end = len(s)
    return int(line_start), int(line_end)


def _list_marker_from_line(line: str) -> str:
    s = str(line or "")
    m = re.match(r"^(  -\s+)", s)
    if m:
        return str(m.group(1))
    m = re.match(r"^(-\s+)", s)
    if m:
        return str(m.group(1))
    m = re.match(r"^(\s{0,2}\d+\.\s+)", s)
    if m:
        return str(m.group(1))
    return ""


def _strip_list_marker_prefix(text: str) -> str:
    s = str(text or "")
    s = re.sub(r"^(  -\s+)", "", s, count=1)
    s = re.sub(r"^(-\s+)", "", s, count=1)
    s = re.sub(r"^(\s{0,2}\d+\.\s+)", "", s, count=1)
    return s


def _truncate_context_words(before_text: str, after_text: str, *, max_words: int = 6) -> tuple[str, str, bool, bool]:
    before_words = [w for w in re.split(r"\s+", str(before_text or "").strip()) if w]
    after_words = [w for w in re.split(r"\s+", str(after_text or "").strip()) if w]
    pre_truncated = len(before_words) > int(max_words)
    post_truncated = len(after_words) > int(max_words)
    pre_words = before_words[-int(max_words):] if before_words else []
    post_words = after_words[:int(max_words)] if after_words else []
    pre_text = ("… " if pre_truncated and pre_words else "") + " ".join(pre_words)
    post_text = " ".join(post_words) + (" …" if post_truncated and post_words else "")
    return str(pre_text).strip(), str(post_text).strip(), bool(pre_truncated), bool(post_truncated)


def _extract_one_word_before(text: str, pos: int) -> str:
    s = str(text or "")
    p = max(0, min(int(pos or 0), len(s)))
    left = s[:p]
    words = re.findall(r"\S+", left, flags=re.UNICODE)
    return str(words[-1] if words else "")


def _extract_one_word_after(text: str, pos: int) -> str:
    s = str(text or "")
    p = max(0, min(int(pos or 0), len(s)))
    right = s[p:]
    m = re.search(r"\S+", right, flags=re.UNICODE)
    return str(m.group(0) if m else "")


def _build_comment_change_html_from_texts(
    *,
    cid: int,
    part_id: str,
    local_id: str,
    old_text: str,
    new_text: str,
) -> str:
    parts: list[str] = []
    if str(old_text or ""):
        parts.append(
            '<del class="kgg-inline-diff-del"'
            + f' data-kgg-cid="{int(cid)}"'
            + f' data-kgg-part-id="{html.escape(str(part_id or ""), quote=True)}"'
            + '>'
            + _render_inline_html(str(old_text or ""))
            + '</del>'
        )
    if str(new_text or ""):
        parts.append(
            _render_insert_span_from_body_html(
                _render_inline_html(str(new_text or "")),
                f"{part_id}_card_{local_id}_ins",
                f"{part_id}_card_ins_{local_id}",
                int(cid),
            )
        )
    return "".join(parts)


def _build_comment_card_rows_from_block(
    *,
    block_key: str,
    changes: list[ChangeRequest],
    style_table: list[dict[str, Any]],
    interaction_table: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    style_by_owner = _style_rows_by_owner_id(style_table)
    interaction_by_span = _interaction_rows_by_span_id(interaction_table)

    for ch in list(changes or []):
        part_id_full = str(ch.change_id or "")
        part_id = str(part_id_full.split("_", 1)[1] if "_" in part_id_full else part_id_full)
        canonical_owner_id = _make_owner_id(int(ch.cid), str(part_id_full))
        style_row = dict(style_by_owner.get(str(canonical_owner_id)) or {})
        default_state = str(style_row.get("default_state") or "normal")
        style_span_ids = [str(x or "") for x in list(style_row.get("span_ids") or []) if str(x or "")]
        recommended_states = [
            str(dict(interaction_by_span.get(span_id) or {}).get("recommended_state") or default_state)
            for span_id in style_span_ids
        ]
        recommended_state = str(next((s for s in recommended_states if s and s != "normal"), "normal"))
        old_full = str(ch.old_text or "")
        new_full = str(ch.new_text or "")
        sm = difflib.SequenceMatcher(a=old_full, b=new_full, autojunk=False)
        local_seq = 0

        for tag, i1, i2, j1, j2 in sm.get_opcodes():
            if str(tag) == "equal":
                continue

            old_seg = old_full[i1:i2] if tag in {"replace", "delete"} else ""
            new_seg = new_full[j1:j2] if tag in {"replace", "insert"} else ""
            if not old_seg and not new_seg:
                continue

            local_seq += 1
            local_id = f"m{int(local_seq)}"

            pre_source = old_full if int(i1) > 0 else new_full
            pre_pos = int(i1) if int(i1) > 0 else int(j1)
            post_source = old_full if int(i2) < len(old_full) else new_full
            post_pos = int(i2) if int(i2) < len(old_full) else int(j2)

            pre_text = _extract_one_word_before(pre_source, pre_pos)
            post_text = _extract_one_word_after(post_source, post_pos)
            pre_html = f'<span class="kgg-diff-pre">{_render_inline_html(pre_text)}</span>' if pre_text else ""
            post_html = f'<span class="kgg-diff-post">{_render_inline_html(post_text)}</span>' if post_text else ""
            change_html = _build_comment_change_html_from_texts(
                cid=int(ch.cid),
                part_id=str(part_id),
                local_id=str(local_id),
                old_text=str(old_seg or ""),
                new_text=str(new_seg or ""),
            )

            if str(tag) == "replace":
                kind = "replace"
            elif str(tag) == "delete":
                kind = "delete"
            else:
                kind = "insert"

            rows.append(
                {
                    "cid": int(ch.cid),
                    "part_id": str(part_id),
                    "part_label": "",
                    "owner_id": str(canonical_owner_id),
                    "block_key": str(block_key or ""),
                    "block_label": _block_label_from_key(str(block_key or "")),
                    "span_id": f"comment_part:{int(ch.cid)}:{part_id}:{local_id}",
                    "kind": str(kind),
                    "subkind": str(ch.change_class or "patch_part"),
                    "source_start": int(ch.old_start or 0) + int(i1),
                    "source_end": int(ch.old_start or 0) + int(i2),
                    "default_state": str(default_state),
                    "recommended_state": str(recommended_state),
                    "available_states": ["normal", "warn", "none"],
                    "group_id": str(canonical_owner_id),
                    "group_type": "comment_part",
                    "is_winner": True,
                    "context_mode": "minimal_word_context",
                    "list_kind": "",
                    "list_level": 0,
                    "list_marker_pre": "",
                    "list_marker_change": "",
                    "list_marker_post": "",
                    "pre_text": str(pre_text or ""),
                    "change_text": str(old_seg or "") + str(new_seg or ""),
                    "post_text": str(post_text or ""),
                    "pre_html": str(pre_html or ""),
                    "change_html": str(change_html or ""),
                    "post_html": str(post_html or ""),
                    "context_pre_truncated": False,
                    "context_post_truncated": False,
                    "html_fragment": str(pre_html or "") + str(change_html or "") + str(post_html or ""),
                    "row_sort_key": f'{int(ch.old_start or 0) + int(i1):08d}:{str(part_id)}:{int(local_seq):04d}:{str(kind)}',
                }
            )

    rows.sort(
        key=lambda r: (
            int(r.get("cid") or 0),
            str(r.get("block_key") or ""),
            str(r.get("row_sort_key") or ""),
            int(r.get("source_start") or 0),
            str(r.get("span_id") or ""),
        )
    )
    for idx, row in enumerate(rows, start=1):
        row["part_label"] = f"Teil {int(idx)}"
    return rows


def _render_comment_cards_html_from_table(comment_cards_table: list[dict[str, Any]]) -> str:
    by_cid: dict[int, list[dict[str, Any]]] = {}
    cid_order: list[int] = []
    for row in list(comment_cards_table or []):
        rec = dict(row or {})
        cid = int(rec.get("cid") or 0)
        if cid not in by_cid:
            by_cid[cid] = []
            cid_order.append(cid)
        by_cid[cid].append(rec)

    cards: list[str] = []
    for cid in list(cid_order):
        rows = sorted(
            list(by_cid.get(int(cid)) or []),
            key=lambda r: (
                str(r.get("block_key") or ""),
                str(r.get("row_sort_key") or ""),
                int(r.get("source_start") or 0),
                str(r.get("span_id") or ""),
            ),
        )
        for seq_idx, rec in enumerate(rows, start=1):
            rec["part_label"] = f"Teil {int(seq_idx)}"
        by_block: dict[str, list[dict[str, Any]]] = {}
        block_order: list[str] = []
        for rec in rows:
            bk = str(rec.get("block_key") or "")
            if bk not in by_block:
                by_block[bk] = []
                block_order.append(bk)
            by_block[bk].append(rec)

        block_html: list[str] = []
        for bk in list(block_order):
            group = list(by_block.get(bk) or [])
            block_label = str(group[0].get("block_label") or bk or "block")
            rows_html_parts: list[str] = []
            for rec_i, rec in enumerate(group):
                block_badge_html = (
                    f'<span class="kgg-comment-card-block-badge">{html.escape(block_label, quote=False)}</span>'
                    if rec_i == 0 else ""
                )
                rows_html_parts.append(
                    '<div class="kgg-diff-row"'
                    + f' data-kgg-cid="{int(rec.get("cid") or 0)}"'
                    + f' data-kgg-block-key="{html.escape(str(rec.get("block_key") or ""), quote=True)}"'
                    + f' data-kgg-part-id="{html.escape(str(rec.get("part_id") or ""), quote=True)}"'
                    + f' data-kgg-span-id="{html.escape(str(rec.get("span_id") or ""), quote=True)}"'
                    + f' data-kgg-kind="{html.escape(str(rec.get("kind") or ""), quote=True)}"'
                    + f' data-kgg-subkind="{html.escape(str(rec.get("subkind") or ""), quote=True)}"'
                    + '>'
                    + block_badge_html
                    + f'<span class="kgg-diff-part-badge">{html.escape(str(rec.get("part_label") or rec.get("part_id") or ""), quote=False)}</span>'
                    + '<span class="kgg-diff-row-body">'
                    + str(rec.get("pre_html") or "")
                    + str(rec.get("change_html") or "")
                    + str(rec.get("post_html") or "")
                    + '</span>'
                    + '</div>'
                )
            rows_html = "".join(rows_html_parts)
            block_html.append(
                '<div class="kgg-comment-card-block"'
                + f' data-kgg-block-key="{html.escape(str(bk or ""), quote=True)}">'
                + (rows_html if rows_html else '<div class="kgg-diff-row"><span class="kgg-diff-row-body">(no changes)</span></div>')
                + '</div>'
            )
        cards.append(
            '<div class="kgg-comment-card"'
            + f' data-kgg-cid="{int(cid)}">'
            + '<div class="kgg-comment-card-body">'
            + "".join(block_html)
            + '</div></div>'
        )
    return "".join(cards)


# ---------------------------------------------------------------------------
# Public product API
# ---------------------------------------------------------------------------

def _split_wrapped_content_block_html(block_key: str, baseline_html: str) -> tuple[str, str, str]:
    src = str(baseline_html or "")
    bk = str(block_key or "").strip()
    if not src or not bk:
        return ("", src, "")
    esc = re.escape(bk)
    m = re.match(
        rf'^(?P<prefix><section\b[^>]*\bdata-block\s*=\s*"{esc}"[^>]*>)(?P<body>[\s\S]*)(?P<suffix></section>)$',
        src,
        re.I | re.S,
    )
    if not m:
        return ("", src, "")
    return (
        str(m.group("prefix") or ""),
        str(m.group("body") or ""),
        str(m.group("suffix") or ""),
    )


def compose_block_change_set(block_key: str, baseline_old_minimd: str, baseline_html: str, changes: list[ChangeRequest], *, with_badges: bool = True, style_state: dict[str, str] | None = None, config: Optional[IndiffConfigLike] = None) -> ComposeBlockChangeSetResult:
    wrapper_prefix, body_baseline_html, wrapper_suffix = _split_wrapped_content_block_html(block_key, baseline_html)
    wrapper_shift = len(wrapper_prefix)
    compose_html = str(body_baseline_html or "")
    compose_changes = _expand_changes_for_article_compose(
        list(changes or []),
        str(baseline_old_minimd or ""),
    )

    atomics, delete_segments, audit = _build_atomic_changes(block_key, baseline_old_minimd, compose_html, list(compose_changes or []))

    # Resolve explicit list hosts per atomic before grouping/materialization.
    #
    # Important:
    # - host_* is the HTML insertion target
    # - source_start/source_end must stay as the original atomic order anchor
    #   from the MiniMD-derived insert stream
    # - otherwise later top-level host resolution can reorder fragments from one
    #   MiniMD split stream (for example m5 before m4), which is exactly the
    #   wrong behavior for list inserts
    for a in atomics:
        if str(a.subkind) == "list_item_insert":
            if int(a.list_level or 0) == 1:
                host_kind, host_start, host_end = _resolve_level1_list_host_from_context(
                    block_key,
                    baseline_old_minimd,
                    compose_html,
                    a,
                )
            else:
                host_kind, host_start, host_end = _resolve_list_host_for_atomic(
                    compose_html,
                    a,
                )
            a.host_kind = str(host_kind)
            a.host_source_start = int(host_start)
            a.host_source_end = int(host_end)

    groups = _build_change_groups(block_key, compose_html, atomics)
    operations = _build_product_operations(block_key, compose_html, groups)
    if not with_badges:
        badge_re = re.compile(r'<span\s+class="kgg-inline-diff-badge"[^>]*>#[^<]*</span>')
        for op in operations:
            op.payload_before = badge_re.sub("", str(op.payload_before or ""))
            op.payload_inside_start = badge_re.sub("", str(op.payload_inside_start or ""))
            op.payload_inside_end = badge_re.sub("", str(op.payload_inside_end or ""))
            op.payload_after = badge_re.sub("", str(op.payload_after or ""))
    span_registry, style_table = _build_span_registry_and_style_table(block_key, operations)
    interaction_table = _build_interaction_table(block_key, groups, operations)
    composed_body_html = _apply_html_operations(compose_html, operations)
    comment_card_rows = _build_comment_card_rows_from_block(
        block_key=str(block_key),
        changes=list(compose_changes or []),
        style_table=list(style_table or []),
        interaction_table=list(interaction_table or []),
    )
    insert_op_by_span_id: dict[str, HtmlOperation] = {}
    for op in operations:
        span_id = str(dict(op.meta or {}).get("span_id") or "")
        if span_id:
            insert_op_by_span_id[span_id] = op

    insert_points = []
    for a in atomics:
        if a.kind == "delete":
            continue
        op = insert_op_by_span_id.get(str(a.span_id))
        final_anchor = (int(op.anchor_source_start) if op is not None else int(a.source_start)) + int(wrapper_shift)
        insert_points.append(
            {
                "part_id": a.original_part_id,
                "cid": a.cid,
                "html_source_start": final_anchor,
                "at_list_item_boundary": a.subkind == "list_item_insert",
                "fragment_html": a.inserted_text,
                "span_id": a.span_id,
                "owner_id": a.owner_id,
            }
        )

    composed_html = (
        str(wrapper_prefix) + str(composed_body_html or "") + str(wrapper_suffix)
        if wrapper_prefix or wrapper_suffix
        else str(composed_body_html or "")
    )

    audit_out: list[dict[str, Any]] = []
    for row in list(audit or []):
        rec = dict(row or {})
        if wrapper_shift:
            if rec.get("html_source_start") is not None:
                rec["html_source_start"] = int(rec.get("html_source_start") or 0) + int(wrapper_shift)
            if rec.get("html_source_end") is not None:
                rec["html_source_end"] = int(rec.get("html_source_end") or 0) + int(wrapper_shift)
        audit_out.append(rec)

    operations_out: list[HtmlOperation] = []
    for op in list(operations or []):
        if wrapper_shift:
            operations_out.append(
                HtmlOperation(
                    op_id=str(op.op_id),
                    block_key=str(op.block_key),
                    op_type=str(op.op_type),
                    anchor_source_start=int(op.anchor_source_start) + int(wrapper_shift),
                    anchor_source_end=int(op.anchor_source_end) + int(wrapper_shift),
                    payload_before=str(op.payload_before or ""),
                    payload_inside_start=str(op.payload_inside_start or ""),
                    payload_inside_end=str(op.payload_inside_end or ""),
                    payload_after=str(op.payload_after or ""),
                    meta=dict(op.meta or {}),
                )
            )
        else:
            operations_out.append(op)

    return ComposeBlockChangeSetResult(
        block_key=block_key,
        baseline_html=str(baseline_html or ""),
        composed_html=str(composed_html or ""),
        normalized_changes=[asdict(ch) for ch in list(compose_changes or [])],
        delete_segments=[asdict(seg) for seg in delete_segments],
        insert_points=insert_points,
        operations=operations_out,
        anchor_audit_rows=audit_out,
        span_registry=span_registry,
        style_table=style_table,
        interaction_table=interaction_table,
        comment_card_rows=comment_card_rows,
        diagnostics={
            "change_requests_total": len(list(compose_changes or [])),
            "original_change_requests_total": len(list(changes or [])),
            "coarse_article_inline_changes_total": int(sum(1 for ch in list(compose_changes or []) if str(getattr(ch, "change_class", "") or "").startswith("coarse_inline_"))),
            "delete_segments_total": len(delete_segments),
            "clusters_total": len(atomics),
            "groups_total": len(groups),
            "operations_total": len(operations),
            "composed_html_len": len(composed_html),
        },
    )

def compose_article_version_diff(
    *,
    block_order: list[str],
    old_minimd_by_block: dict[str, str],
    new_minimd_by_block: dict[str, str],
    config: Optional[IndiffConfigLike] = None,
) -> dict[str, ComposeBlockChangeSetResult]:
    """Compose one article-version diff through the canonical indiff pipeline.

    The previous published version is the baseline. Each block is diffed into
    semantic old/new events and then rendered by compose_block_change_set().
    No comment badges are emitted; frontend policy may style the resulting
    insert/delete markers as a version layer.
    """
    out: dict[str, ComposeBlockChangeSetResult] = {}
    old_map = {str(k): str(v or "") for k, v in dict(old_minimd_by_block or {}).items()}
    new_map = {str(k): str(v or "") for k, v in dict(new_minimd_by_block or {}).items()}

    for raw_key in list(block_order or []):
        block_key = str(raw_key or "").strip()
        if not block_key:
            continue
        old_text = str(old_map.get(block_key) or "")
        new_text = str(new_map.get(block_key) or "")
        events = derive_block_semantic_changes(old_text, new_text, block_key, config=config)
        changes: list[ChangeRequest] = []
        for idx, event in enumerate(list(events or []), start=1):
            row = dict(event or {})
            old_start = int(row.get("old_start") or 0)
            old_end = int(row.get("old_end") or old_start)
            changes.append(
                ChangeRequest(
                    change_id=f"version:{block_key}:{idx}",
                    cid=0,
                    block_key=block_key,
                    old_text=str(row.get("old_text") or ""),
                    new_text=str(row.get("new_text") or ""),
                    old_start=old_start,
                    old_end=old_end,
                    change_class=str(row.get("change_class") or "version_diff"),
                )
            )

        baseline_html = minimd_to_html_text(block_key, old_text, config=config) if old_text else ""
        result = compose_block_change_set(
            block_key,
            old_text,
            baseline_html,
            changes,
            with_badges=False,
            config=config,
        )

        # Lists, tables and other block structures cannot always be safely
        # represented by an arbitrary minimal inline edit at their HTML edge.
        # Keep the semantic compose result as diagnostics/source truth, but use
        # a block-safe old/new rendering for the visible version layer.
        if old_text != new_text:
            new_html = minimd_to_html_text(block_key, new_text, config=config) if new_text else ""
            structural_probe = (str(baseline_html or "") + "\n" + str(new_html or "")).lower()
            structural_tags = ("<ul", "<ol", "<table", "<pre", "<blockquote", "<details")
            if any(tag in structural_probe for tag in structural_tags):
                parts: list[str] = []
                if baseline_html:
                    parts.append(
                        '<div class="kgg-inline-diff-del kgg-inline-diff-del-block kgg-version-structural-old" '
                        'data-kgg-version-structural="old">' + str(baseline_html) + "</div>"
                    )
                if new_html:
                    parts.append(
                        '<div class="kgg-inline-diff-ins kgg-inline-diff-ins-block kgg-version-structural-new" '
                        'data-kgg-version-structural="new">' + str(new_html) + "</div>"
                    )
                result.composed_html = "".join(parts)
                result.diagnostics = dict(result.diagnostics or {})
                result.diagnostics["version_diff_render_mode"] = "structural_block"
            else:
                result.diagnostics = dict(result.diagnostics or {})
                result.diagnostics["version_diff_render_mode"] = "semantic_inline"
        else:
            result.diagnostics = dict(result.diagnostics or {})
            result.diagnostics["version_diff_render_mode"] = "unchanged"

        out[block_key] = result

    return out
