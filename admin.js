// KlimaGG-Web — admin.js
// Version: v2.0.1
// Zweck:
//   - Wird nur auf /admin geladen (CSP: script-src 'self').
//   - Nutzt ausschließlich bestehendes JWT + /api/me + is_admin
//   - Bündelt Beteiligung, Artikel, Release, Nutzer, Betrieb und ausgewählte Startseiten-Inhalte.

(function () {
  "use strict";

  const AUTH_STORAGE_KEY = "klimagg_auth_token_v1";
  const AUTH_STATE_CHANGED_KEY = "klimagg_auth_state_changed_v1";

  const $ = (id) => document.getElementById(id);
  const elAuth = $("admin-auth-status");
  const elCockpitGrid = $("admin-cockpit-grid");
  const elHealth = $("admin-health");
  const elStats = $("admin-stats");
  const elExports = $("admin-exports-list");
  const elExportStatus = $("admin-export-status");
  const btnExportRelease = $("btn-export-release");
  const btnExportMonthly = $("btn-export-monthly");
  const btnExportReleaseDetail = $("btn-export-release-detail");
  const btnExportMonthlyDetail = $("btn-export-monthly-detail");

  // Web-Release-Upgrade (GitHub Tags/Releases)
  const elWebCurrent = $("admin-web-current");
  const elWebCurrentDetail = $("admin-web-current-detail");
  const elWebBadge = $("admin-web-badge");
  const cbWebStable = $("admin-web-stable");
  const selWebTarget = $("admin-web-target");
  const btnWebUpgrade = $("btn-web-upgrade");
  const btnWebRefresh = $("btn-web-refresh");
  const btnWebRefreshDetail = $("btn-web-refresh-detail");
  const elWebAction = $("admin-web-action");


  // Gesetzes-Release Workflow
  const elReleaseStatusDetail = $("admin-release-status-detail");
  const elReleaseArticles = $("admin-release-articles");
  const elReleaseSelection = $("admin-release-selection");
  const elReleaseConflicts = $("admin-release-conflicts");
  const elReleaseRebase = $("admin-release-rebase");
  const elReleaseAction = $("admin-release-action");
  const btnReleasePrepare = $("btn-release-prepare");
  const btnReleaseRefresh = $("btn-release-refresh");
  const btnReleaseCancel = $("btn-release-cancel");
  const btnReleaseExecute = $("btn-release-execute");
  const tblReleaseCandidates = $("admin-release-candidates-table");

  const elTopicsList = $("admin-topics-list");
  const elTopicsStatus = $("admin-topics-status");
  const btnTopicsAdd = $("btn-topics-add");
  const btnTopicsSave = $("btn-topics-save");
  let currentTopicsVersion = 0;

  // Übersicht (Listen)
  const btnOverviewRefresh = $("btn-admin-overview-refresh");
  const cbOverviewPersonal = $("admin-overview-personal");
  const elOverviewMeta = $("admin-overview-meta");

  const elUsersMeta = $("admin-users-meta");
  const tblUsers = $("admin-users-table");
  const elArticlesMeta = $("admin-articles-meta");
  const tblArticles = $("admin-articles-table");

  const elCommentsMeta = $("admin-comments-meta");
  const tblComments = $("admin-comments-table");
  const selCommentsSort = $("admin-comments-sort");
  const cbCommentsHideDone = $("admin-comments-hide-done");

  const elCommentChangesMeta = $("admin-comment-changes-meta");
  const tblCommentChanges = $("admin-comment-changes-table");

  const elReviewsMeta = $("admin-reviews-meta");
  const tblReviews = $("admin-reviews-table");

  const elVotesDistMeta = $("admin-votesdist-meta");
  const tblVotesDist = $("admin-votesdist-table");

  const ADMIN_TAB_STORAGE_KEY = "klimagg_admin_active_tab_v2";
  const ADMIN_COMMENTS_SORT_STORAGE_KEY = "klimagg_admin_comments_sort_v2";
  const ADMIN_COMMENTS_HIDE_DONE_STORAGE_KEY = "klimagg_admin_comments_hide_done_v2";
  const ADMIN_OVERVIEW_PERSONAL_STORAGE_KEY = "klimagg_admin_overview_personal_v2";
  const adminTabButtons = Array.from(document.querySelectorAll("[data-admin-tab]"));
  const adminTabPanels = Array.from(document.querySelectorAll("[data-admin-panel]"));

  const sidebarExportLast = $("admin-sidebar-export-last");
  const sidebarTopicsCount = $("admin-sidebar-topics-count");
  const sidebarActivityComments = $("admin-activity-comments");
  const sidebarActivityVotes = $("admin-activity-votes");
  const sidebarActivityReviews = $("admin-activity-reviews");
  const sidebarCriticalReports = $("admin-critical-reports");
  const sidebarReleaseStatus = $("admin-release-status");
  const sidebarReleaseCandidates = $("admin-release-candidates");

  const stripUsers = $("admin-strip-users");
  const stripArticles = $("admin-strip-articles");
  const stripComments = $("admin-strip-comments");
  const stripReviews = $("admin-strip-reviews");

  const tabBadgeUsers = $("admin-tab-badge-users");
  const tabBadgeArticles = $("admin-tab-badge-articles");
  const tabBadgeRelease = $("admin-tab-badge-release");
  const tabBadgeComments = $("admin-tab-badge-comments");
  const tabBadgeOps = $("admin-tab-badge-ops");

  const ADMIN_COMMENT_VIEW_STORAGE_KEY = "klimagg_admin_comment_view_v2";
  const adminCommentViewButtons = Array.from(document.querySelectorAll(".admin-view-chip"));
  let activeAdminCommentView = "alle";
  let adminCommentItemsCache = [];
  let releaseCommentHistoryById = Object.create(null);
  let adminReviewQueueCommentIds = new Set();
  let adminCommentChangeIds = new Set();

  function setText(el, txt) {
    if (el) el.textContent = String(txt ?? "");
  }

  function setHtml(el, html) {
    if (el) el.innerHTML = String(html ?? "");
  }

  function esc(v) {
    const s = String(v ?? "");
    return s
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#39;");
  }

  function stripTagsToText(v) {
    // AUDIT(v1.0.27): Backend-HTML wird hier nur zu Text reduziert; bei sehr großen
    // Fragmenten später Server-Kurztext/Längenbegrenzung prüfen.
    const box = document.createElement("div");
    box.innerHTML = String(v ?? "");
    return String(box.textContent || box.innerText || "").replace(/\s+/g, " ").trim();
  }

  function trunc(v, maxLen) {
    const s = String(v ?? "");
    const n = Number(maxLen || 0);
    if (!n || s.length <= n) return s;
    return s.slice(0, Math.max(0, n - 1)) + "…";
  }

  function fmtInt(n) {
    const v = Number(n || 0);
    return new Intl.NumberFormat("de-DE").format(v);
  }

  function setAuthStatus(text) {
    if (!elAuth) return;
    elAuth.textContent = "";
    const label = document.createElement("b");
    label.textContent = "Status:";
    elAuth.appendChild(label);
    elAuth.appendChild(document.createTextNode(" " + String(text ?? "")));
  }

  function setAdminCockpitVisible(visible) {
    const on = !!visible;
    if (elCockpitGrid) elCockpitGrid.hidden = !on;
    try { document.body.classList.toggle("admin-auth-ok", on); } catch { /* ignore */ }
  }

  function getStoredBool(key, fallback) {
    try {
      const v = localStorage.getItem(key);
      if (v === "1") return true;
      if (v === "0") return false;
    } catch { /* ignore */ }
    return !!fallback;
  }

  function setStoredBool(key, value) {
    try { localStorage.setItem(key, value ? "1" : "0"); } catch { /* ignore */ }
  }

  function getStoredString(key, fallback, allowed) {
    let v = String(fallback || "");
    try { v = localStorage.getItem(key) || v; } catch { /* ignore */ }
    if (Array.isArray(allowed) && allowed.length && !allowed.includes(v)) return String(fallback || "");
    return v;
  }

  function setStoredString(key, value) {
    try { localStorage.setItem(key, String(value || "")); } catch { /* ignore */ }
  }

  function restoreAdminControlPrefs() {
    if (selCommentsSort) {
      const allowed = Array.from(selCommentsSort.options || []).map((opt) => String(opt.value || ""));
      selCommentsSort.value = getStoredString(ADMIN_COMMENTS_SORT_STORAGE_KEY, selCommentsSort.value || "newest", allowed);
    }
    if (cbCommentsHideDone) {
      cbCommentsHideDone.checked = getStoredBool(ADMIN_COMMENTS_HIDE_DONE_STORAGE_KEY, cbCommentsHideDone.checked);
    }
    if (cbOverviewPersonal) {
      cbOverviewPersonal.checked = getStoredBool(ADMIN_OVERVIEW_PERSONAL_STORAGE_KEY, cbOverviewPersonal.checked);
    }
  }

  function normalizeErrorMessage(err) {
    const data = err && err.data;
    const status = err && err.status ? Number(err.status) : 0;
    if ((status === 502 || status === 503 || status === 504) && typeof data === "string" && /<html[\s>]/i.test(data)) {
      return "Server während Neustart kurz nicht erreichbar.";
    }
    if (data && typeof data.detail === "string") return data.detail;
    if (Array.isArray(data && data.detail)) {
      return data.detail.map((x) => (x && x.msg) ? x.msg : JSON.stringify(x)).join("; ");
    }
    if (data && typeof data === "object") {
      try { return JSON.stringify(data); } catch { /* ignore */ }
    }
    if (typeof data === "string" && /<html[\s>]/i.test(data)) return resStatusText(status) || "Serverfehler";
    return (err && err.message) ? String(err.message) : "unknown";
  }

  function resStatusText(status) {
    if (status === 502) return "502 Bad Gateway";
    if (status === 503) return "503 Service Unavailable";
    if (status === 504) return "504 Gateway Timeout";
    return status ? String(status) : "";
  }

  function isRestartHttpError(err) {
    const status = err && err.status ? Number(err.status) : 0;
    return status === 502 || status === 503 || status === 504;
  }

  function sleep(ms) {
    return new Promise((resolve) => window.setTimeout(resolve, ms));
  }

  async function waitForWebRestartAndReload(ref) {
    const started = Date.now();
    const maxMs = 90000;
    setText(elWebAction, "Upgrade auf " + ref + " angestoßen. Server startet neu …");
    while ((Date.now() - started) < maxMs) {
      await sleep(1500);
      try {
        const res = await fetch("/api/health?restart_check=" + encodeURIComponent(String(Date.now())), {
          method: "GET",
          cache: "no-store",
        });
        if (res && res.ok) {
          setText(elWebAction, "Server wieder erreichbar. Admin wird neu geladen …");
          window.setTimeout(() => window.location.reload(), 700);
          return true;
        }
      } catch {
        // Erwartbar während systemd/uvicorn neu startet.
      }
    }
    setText(elWebAction, "Update wurde angestoßen, aber /api/health ist noch nicht wieder erreichbar. Bitte Seite manuell neu laden und Dienststatus prüfen.");
    return false;
  }

  async function withActionLock(button, fn) {
    const btn = button || null;
    const oldDisabled = btn ? !!btn.disabled : false;
    if (btn) btn.disabled = true;
    try {
      return await fn();
    } finally {
      if (btn) btn.disabled = oldDisabled;
    }
  }

  function confirmAdminAction(message) {
    // AUDIT(v1.0.27): Minimaler Fehlklick-Schutz für ops-nahe Admin-Aktionen.
    // Später ggf. durch modalen Zwei-Schritt-Flow mit Detailanzeige ersetzen.
    return window.confirm(String(message || "Diese Admin-Aktion wirklich ausführen?"));
  }

  function setActiveAdminTab(tabName, persist) {
    const name = String(tabName || "beteiligung");
    const known = adminTabButtons.some((btn) => btn && btn.dataset && btn.dataset.adminTab === name);
    const active = known ? name : "beteiligung";
    adminTabButtons.forEach((btn) => {
      const on = btn && btn.dataset && btn.dataset.adminTab === active;
      btn.classList.toggle("is-active", !!on);
      btn.setAttribute("aria-selected", on ? "true" : "false");
    });
    adminTabPanels.forEach((panel) => {
      const on = panel && panel.dataset && panel.dataset.adminPanel === active;
      panel.classList.toggle("is-active", !!on);
      panel.hidden = !on;
    });
    if (persist !== false) {
      try { localStorage.setItem(ADMIN_TAB_STORAGE_KEY, active); } catch { /* ignore */ }
    }
  }

  function initAdminTabs() {
    adminTabButtons.forEach((btn) => {
      btn.addEventListener("click", () => setActiveAdminTab(btn.dataset.adminTab || "beteiligung", true));
    });
    document.querySelectorAll("[data-admin-tab-jump]").forEach((btn) => {
      btn.addEventListener("click", () => setActiveAdminTab(btn.dataset.adminTabJump || "betrieb", true));
    });
    let saved = "beteiligung";
    try { saved = localStorage.getItem(ADMIN_TAB_STORAGE_KEY) || "beteiligung"; } catch { /* ignore */ }
    setActiveAdminTab(saved, false);
  }

  function setBadge(el, txt) {
    if (el) el.textContent = String(txt || "—");
  }

  function adminWorktabTarget() {
    const root = document.querySelector(".admin-page-main");
    const configured = root && root.dataset ? String(root.dataset.adminWorktab || "").trim() : "";
    return configured || "main";
  }

  function adminArticleUrl(articleId) {
    const id = Number.parseInt(String(articleId || ""), 10);
    if (!id || id < 1) return "";
    return "/#artikel-" + encodeURIComponent(String(id));
  }

  function adminCommentUrl(articleId, commentId) {
    const aid = Number.parseInt(String(articleId || ""), 10);
    const cid = Number.parseInt(String(commentId || ""), 10);
    if (!aid || aid < 1) return "";
    if (!cid || cid < 1) return adminArticleUrl(aid);
    const qs = new URLSearchParams({
      kgg_jump: "comment",
      article_id: String(aid),
      comment_id: String(cid),
    });
    return "/?" + qs.toString() + "#artikel-" + encodeURIComponent(String(aid));
  }

  function renderAdminOpenArticleLink(articleId, label) {
    const url = adminArticleUrl(articleId);
    if (!url) return "<span class='admin-muted'>—</span>";
    return "<a class='admin-open-link' href='" + esc(url) + "' target='" + esc(adminWorktabTarget()) + "'>" + esc(label || "Öffnen") + "</a>";
  }

  function renderAdminOpenCommentLink(articleId, commentId, label) {
    const url = adminCommentUrl(articleId, commentId);
    if (!url) return "<span class='admin-muted'>—</span>";
    return "<a class='admin-open-link' href='" + esc(url) + "' target='" + esc(adminWorktabTarget()) + "'>" + esc(label || "Öffnen") + "</a>";
  }

  function normalizeAdminCommentView(value) {
    const raw = String(value || "").trim().toLowerCase();
    const map = {
      "alle": "alle",
      "review": "review",
      "reif": "reif",
      "entscheidungsreif": "reif",
      "konflikte": "konflikte",
      "konflikt": "konflikte",
      "gold": "gold",
      "release": "release",
      "reports": "reports",
      "report": "reports",
    };
    return map[raw] || "alle";
  }

  function setActiveAdminCommentView(view, options) {
    activeAdminCommentView = normalizeAdminCommentView(view);
    adminCommentViewButtons.forEach((btn) => {
      const key = normalizeAdminCommentView(btn.dataset.adminCommentView || btn.textContent || "");
      const on = key === activeAdminCommentView;
      btn.classList.toggle("is-active", on);
      btn.setAttribute("aria-pressed", on ? "true" : "false");
      btn.dataset.adminCommentView = key;
    });
    if (!options || options.persist !== false) {
      try { localStorage.setItem(ADMIN_COMMENT_VIEW_STORAGE_KEY, activeAdminCommentView); } catch { /* ignore */ }
    }
    renderAdminCommentsTableFromCache();
  }

  function initAdminCommentViews() {
    adminCommentViewButtons.forEach((btn) => {
      const key = normalizeAdminCommentView(btn.dataset.adminCommentView || btn.textContent || "");
      btn.dataset.adminCommentView = key;
      btn.addEventListener("click", () => setActiveAdminCommentView(key, { persist: true }));
    });
    let saved = "alle";
    try { saved = localStorage.getItem(ADMIN_COMMENT_VIEW_STORAGE_KEY) || "alle"; } catch { /* ignore */ }
    setActiveAdminCommentView(saved, { persist: false });
  }

  function lowerText(value) {
    return String(value || "").toLowerCase();
  }

  function statusBundle(c) {
    const status = lowerText(c && c.status);
    const lifecycle = lowerText(c && c.lifecycle_status);
    const policy = lowerText(c && c.policy_status);
    const candidate = lowerText(c && c.candidate_status);
    return [status, lifecycle, policy, candidate].join(" ");
  }

  function objectText(obj) {
    try { return JSON.stringify(obj || {}).toLowerCase(); } catch { return ""; }
  }

  function getReviewRollup(c) {
    return (c && c.review_rollup && typeof c.review_rollup === "object") ? c.review_rollup : {};
  }

  function getRequiredReviews(c) {
    if (typeof (c && c.required_reviews) === "number") return Number(c.required_reviews || 0);
    return null;
  }

  function isReviewComment(c) {
    const id = Number(c && c.id || 0);
    const bundle = statusBundle(c);
    const roll = getReviewRollup(c);
    return bundle.includes("review") || adminReviewQueueCommentIds.has(id) || Number(roll.total || 0) > 0;
  }

  function isReviewReadyComment(c) {
    const roll = getReviewRollup(c);
    const req = getRequiredReviews(c);
    const total = Number(roll.total || 0);
    const approve = Number(roll.approve || 0);
    const reject = Number(roll.reject || 0);
    if (c && c.review_state && (c.review_state.publish_ready || c.review_state.reject_ready)) return true;
    if (req != null && req > 0 && total >= req) return true;
    if (approve >= 1 && reject >= 1) return true;
    return false;
  }

  function isConflictComment(c) {
    const id = Number(c && c.id || 0);
    if (Array.isArray(c && c.conflict_flags) && c.conflict_flags.length) return true;
    const text = statusBundle(c) + " " + objectText(c && c.candidate_reasons) + " " + objectText(c && c.patch_stats);
    return adminCommentChangeIds.has(id) || /conflict|konflikt|locate|failure|invalid|error|policy|block/.test(text);
  }

  function isGoldComment(c) {
    if (c && c.gold_review_candidate) return true;
    const text = statusBundle(c) + " " + objectText(c && c.candidate_reasons);
    return /gold/.test(text);
  }

  function isReleaseComment(c) {
    if (currentReleasePreviewStateForComment(c)) return true;
    return !!(c && (c.release_candidate || c.release_delay_candidate || c.qualified_for_next_release));
  }

  function isReportComment(c) {
    if (Array.isArray(c && c.report_flags) && c.report_flags.length) return true;
    const text = statusBundle(c) + " " + objectText(c && c.candidate_reasons) + " " + objectText(c && c.report_flags);
    return /report|meldung|abuse|datenschutz|recht|illegal|copyright|personenbezogen/.test(text);
  }

  function adminCommentPriority(c) {
    if (c && typeof c.admin_priority_rank === "number" && c.admin_priority_label) {
      return {
        rank: Number(c.admin_priority_rank),
        label: String(c.admin_priority_label || "—"),
        title: String(c.admin_priority_title || "priorisiert"),
      };
    }
    if (isConflictComment(c) || isReportComment(c)) return { rank: 1, label: "🔴", title: "kritisch" };
    if (isReviewReadyComment(c)) return { rank: 2, label: "🟠", title: "entscheidungsreif" };
    if (isReviewComment(c)) return { rank: 3, label: "🟡", title: "Review" };
    if (isReleaseComment(c) || isGoldComment(c)) return { rank: 4, label: "🟢", title: "Kandidat" };
    return { rank: 9, label: "—", title: "normal" };
  }

  function commentMatchesActiveView(c) {
    if (activeAdminCommentView === "review") return isReviewComment(c);
    if (activeAdminCommentView === "reif") return isReviewReadyComment(c);
    if (activeAdminCommentView === "konflikte") return isConflictComment(c);
    if (activeAdminCommentView === "gold") return isGoldComment(c);
    if (activeAdminCommentView === "release") return isReleaseComment(c);
    if (activeAdminCommentView === "reports") return isReportComment(c);
    return true;
  }

  function sortAdminCommentsForView(items) {
    const out = Array.isArray(items) ? Array.from(items) : [];
    out.sort((a, b) => {
      const pa = adminCommentPriority(a).rank;
      const pb = adminCommentPriority(b).rank;
      if (pa !== pb) return pa - pb;
      const da = Date.parse(a && a.created_at || "") || 0;
      const db = Date.parse(b && b.created_at || "") || 0;
      return db - da;
    });
    return out;
  }

  function commentModePill(c) {
    const mode = c && c.comment_mode ? String(c.comment_mode) : "change";
    return pillHtml(mode);
  }

  function releaseHistoryForComment(c) {
    const cid = String(c && c.id != null ? c.id : "").trim();
    return cid && releaseCommentHistoryById && releaseCommentHistoryById[cid]
      ? releaseCommentHistoryById[cid]
      : null;
  }

  function currentReleasePreviewStateForComment(c) {
    const cid = Number(c && c.id || 0);
    const articles = _releasePreview && Array.isArray(_releasePreview.articles) ? _releasePreview.articles : [];
    if (!cid || !articles.length) return null;
    for (const row of articles) {
      const qualified = Array.isArray(row && row.qualified_comment_ids) ? row.qualified_comment_ids.map(Number) : [];
      const selected = Array.isArray(row && row.selected_comment_ids) ? row.selected_comment_ids.map(Number) : [];
      const excluded = Array.isArray(row && row.excluded_comment_ids) ? row.excluded_comment_ids.map(Number) : [];
      const rebaseRows = Array.isArray(row && row.rebase) ? row.rebase : [];
      const rebase = rebaseRows.find((x) => Number(x && x.comment_id || 0) === cid) || null;
      if (qualified.includes(cid) || selected.includes(cid) || excluded.includes(cid) || rebase) {
        return {
          qualified: qualified.includes(cid),
          selected: selected.includes(cid),
          excluded: excluded.includes(cid),
          rebaseSafe: !!(rebase && rebase.safe === true),
          rebaseArchive: !!(rebase && rebase.safe === false),
        };
      }
    }
    return null;
  }

  function commentReleaseIndicator(c) {
    const status = String(c && c.status || "").trim().toLowerCase();
    if (status === "integriert" || status === "integrated") return "integriert";

    const hist = releaseHistoryForComment(c) || {};
    const current = currentReleasePreviewStateForComment(c);
    const skipped = Number(hist.skipped_count || 0);
    const priorQualified = Number(hist.qualified_count || 0) > 0;

    if (current && current.selected) {
      if (skipped > 0) return skipped + "× übergangen · jetzt ausgewählt";
      return priorQualified ? "erneut qualifiziert · ausgewählt" : "NEU · ausgewählt";
    }
    if (current && current.excluded) {
      if (skipped > 0) return skipped + "× übergangen · erneut Konflikt";
      return priorQualified ? "erneut qualifiziert · Konflikt" : "NEU · Konflikt";
    }
    if (current && current.qualified) {
      return priorQualified ? "erneut qualifiziert" : "NEU · qualifiziert";
    }
    if (current && current.rebaseSafe) return "nicht ausgewählt · wird weitergeführt";
    if (current && current.rebaseArchive) return "nicht ausgewählt · würde archiviert";

    if (skipped > 0) return skipped + "× übergangen · aktuell nicht dabei";
    if (String(hist.latest_action || "") === "rebased") return "weitergeführt";
    if (String(hist.latest_action || "") === "archived") return "im Release archiviert";
    if (Number(hist.selected_count || 0) > 0) return "früher ausgewählt";
    if (priorQualified) return "früher qualifiziert";
    return "—";
  }

  function commentCandidateText(c) {
    const parts = [];
    if (c && c.gold_review_candidate) parts.push("Gold");
    else if (isGoldComment(c)) parts.push("Gold?");
    if (c && c.release_candidate) parts.push("Release");
    if (c && c.release_delay_candidate) parts.push("Delay");
    else if (isReleaseComment(c)) parts.push("Release?");
    if (isConflictComment(c)) parts.push("Konflikt");
    if (isReportComment(c)) parts.push("Report");
    return parts.length ? parts.join(" · ") : "—";
  }

  function renderAdminCommentAction(c) {
    const aid = Number(c && c.article_id || 0);
    const cid = Number(c && c.id || 0);
    const openUrl = adminCommentUrl(aid, cid);
    const st = String((c && c.status) || "").toLowerCase();
    const opts = [
      ["", "Aktion…"],
      [openUrl, "Im Artikel öffnen"],
    ];
    if (cid && st === "review") {
      opts.push(["admin-publish:" + String(cid), "Veröffentlichen"]);
    }
    return "<select class='admin-action-select' data-admin-comment-action='" + esc(String(cid || "")) + "'>" +
      opts.map(([value, label]) => "<option value='" + esc(value) + "'>" + esc(label) + "</option>").join("") +
      "</select>";
  }

  async function adminPublishCommentStartphase(commentId) {
    const cid = commentId != null ? String(commentId) : "";
    if (!cid) return;
    try {
      await apiFetch("/api/admin/comments/" + encodeURIComponent(cid) + "/publish", { method: "POST" });
      await loadOverviewAll();
      setText(elCommentsMeta, "Kommentar #" + cid + " veröffentlicht.");
    } catch (e) {
      setText(elCommentsMeta, "Veröffentlichen fehlgeschlagen: " + (e && e.message ? e.message : "unknown"));
    }
  }

  function renderAdminCommentsTableFromCache() {
    if (!tblComments) return;
    const headers = ["CID","Prio","Artikel","Modus","Status","Review","Votes","Kandidat","Release","Alter","Autor","Öffnen","Aktion"];
    const filtered = sortAdminCommentsForView(adminCommentItemsCache.filter(commentMatchesActiveView));
    const rows = filtered.map((c) => {
      const roll = getReviewRollup(c);
      const req = getRequiredReviews(c);
      const rollTxt = "A:" + fmtInt(roll.approve || 0) + " / R:" + fmtInt(roll.revise || 0) + " / X:" + fmtInt(roll.reject || 0) + " (" + fmtInt(roll.total || 0) + (req != null ? ("/" + fmtInt(req)) : "") + ")";
      const art = (c.article_title ? String(c.article_title) : (c.article_slug || ""));
      const artShort = art.length > 34 ? (art.slice(0, 31) + "…") : art;
      const priority = adminCommentPriority(c);
      const voteCounts = c.vote_counts || {};
      const approval = (typeof c.vote_approval_percent === "number") ? Math.round(c.vote_approval_percent) + "%" : "—";
      const votesTxt = (kvInline(voteCounts) || "—") + "<div class='klein admin-muted'>Zustimmung: " + esc(approval) + "</div>";
      return (
        "<tr>" +
          "<td class='cc-mini'>#" + esc(c.id) + "</td>" +
          "<td title='" + esc(priority.title) + "'>" + esc(priority.label) + "</td>" +
          "<td title='" + esc(art) + "'>" + esc(artShort) + "</td>" +
          "<td>" + commentModePill(c) + "</td>" +
          "<td>" + pillHtml(c.status || "—") + "</td>" +
          "<td>" + esc(rollTxt) + "</td>" +
          "<td>" + votesTxt + "</td>" +
          "<td>" + esc(commentCandidateText(c)) + "</td>" +
          "<td>" + esc(commentReleaseIndicator(c)) + "</td>" +
          "<td>" + esc(fmtDT(c.created_at)) + "</td>" +
          "<td>" + esc(c.user_pseudonym || "") + "</td>" +
          "<td>" + renderAdminOpenCommentLink(c.article_id, c.id, "Öffnen") + "</td>" +
          "<td>" + renderAdminCommentAction(c) + "</td>" +
        "</tr>"
      );
    }).join("");
    setHtml(tblComments, tableHtml(headers, rows));
    const suffix = activeAdminCommentView === "alle" ? "" : " · Ansicht: " + activeAdminCommentView;
    setText(elCommentsMeta, fmtInt(filtered.length) + " / " + fmtInt(adminCommentItemsCache.length) + " Einträge" + suffix);
  }

  function fmtDT(v) {
    if (!v) return "—";
    try {
      const d = new Date(v);
      return d.toLocaleString("de-DE");
    } catch {
      return String(v);
    }
  }

  function getToken() {
    try {
      return localStorage.getItem(AUTH_STORAGE_KEY) || "";
    } catch {
      return "";
    }
  }

  function signalAuthStateChanged(reason) {
    try {
      localStorage.setItem(AUTH_STATE_CHANGED_KEY, String(Date.now()) + ":" + String(reason || "admin"));
    } catch { /* ignore */ }
  }

  function clearLocalAuth(reason) {
    try { localStorage.removeItem(AUTH_STORAGE_KEY); } catch { /* ignore */ }
    signalAuthStateChanged(reason || "admin_auth_clear");
  }

  function setupAuthStateSync() {
    window.addEventListener("storage", (ev) => {
      if (!ev || ![AUTH_STORAGE_KEY, AUTH_STATE_CHANGED_KEY].includes(String(ev.key || ""))) return;
      try { window.location.reload(); } catch { /* ignore */ }
    });
  }

  async function apiFetch(path, opts) {
    const token = getToken();
    const headers = Object.assign(
      { "Content-Type": "application/json" },
      (opts && opts.headers) || {}
    );
    if (token) headers.Authorization = "Bearer " + token;
    const res = await fetch(path, Object.assign({}, opts || {}, { headers }));
    let data = null;
    const ct = (res.headers.get("content-type") || "").toLowerCase();
    if (ct.includes("application/json")) {
      try {
        data = await res.json();
      } catch {
        data = null;
      }
    } else {
      try {
        data = await res.text();
      } catch {
        data = null;
      }
    }
    if (!res.ok) {
      const err = new Error(
        (typeof data === "string" && data) ||
        res.statusText ||
        "Request failed"
      );
      err.status = res.status;
      err.data = data;
      err.message = normalizeErrorMessage(err);
      throw err;
    }
    return data;
  }

  function disableUI(disabled) {
    const ids = [
      "btn-export-release",
      "btn-export-monthly",
      "btn-export-release-detail",
      "btn-export-monthly-detail",
      "btn-web-upgrade",
      "btn-web-refresh",
      "btn-web-refresh-detail",
      "admin-web-stable",
      "admin-web-target",
      "btn-topics-add",
      "btn-topics-save",
      "btn-admin-overview-refresh",
      "admin-overview-personal",
      "admin-comments-sort",
      "admin-comments-hide-done",
      "btn-release-prepare",
      "btn-release-refresh",
      "btn-release-cancel",
      "btn-release-execute",
    ];
    ids.forEach((id) => {
      const el = $(id);
      if (!el) return;
      el.disabled = !!disabled;
    });
  }

  function setStatusLine(el, text) {
    if (!el) return;
    el.textContent = String(text || "");
  }

  let _releaseWorkflowState = null;
  let _releasePreview = null;
  let _releaseActionBusy = false;

  function releaseStateLabel(state) {
    const value = String(state || "normal");
    if (value === "release_focus") return "Vorbereitung";
    if (value === "finalizing") return "Release läuft";
    return "normal";
  }

  function releasePreviewTotals() {
    return (_releasePreview && _releasePreview.totals) ? _releasePreview.totals : {};
  }

  function renderReleaseCandidatesTable() {
    if (!tblReleaseCandidates) return;
    const articles = _releasePreview && Array.isArray(_releasePreview.articles) ? _releasePreview.articles : [];
    const rows = [];

    for (const article of articles) {
      const qualified = Array.isArray(article && article.qualified_comment_ids) ? article.qualified_comment_ids.map(Number) : [];
      const selected = new Set(Array.isArray(article && article.selected_comment_ids) ? article.selected_comment_ids.map(Number) : []);
      const excluded = new Set(Array.isArray(article && article.excluded_comment_ids) ? article.excluded_comment_ids.map(Number) : []);
      for (const cid of qualified) {
        if (!cid) continue;
        let state = "qualifiziert";
        if (selected.has(cid)) state = "ausgewählt";
        else if (excluded.has(cid)) state = "Konflikt";
        const articleLabel = String((article && (article.public_code || article.title || article.article_id)) || "—");
        rows.push(
          "<tr>" +
            "<td>" + esc(cid) + "</td>" +
            "<td>" + esc(articleLabel) + "</td>" +
            "<td>" + esc(state) + "</td>" +
            "<td>" + renderAdminOpenCommentLink(article.article_id, cid, "Öffnen") + "</td>" +
          "</tr>"
        );
      }
    }

    const body = rows.length
      ? rows.join("")
      : "<tr><td colspan='4' class='admin-muted'>Keine qualifizierten Release-Kandidaten.</td></tr>";
    setHtml(tblReleaseCandidates, tableHtml(["CID", "Artikel", "Auswahl", "Öffnen"], body));
  }

  function renderReleaseWorkflow() {
    const workflow = _releaseWorkflowState || {};
    const state = String(workflow.state || "normal");
    const totals = releasePreviewTotals();
    const ready = !!(_releasePreview && _releasePreview.ready);
    const selected = Number(totals.selected_comments || 0);
    const qualified = Number(totals.qualified_comments || 0);
    const affected = Number(totals.articles_would_change || 0);
    const conflicts = Number(totals.selection_conflicts || 0);
    const rebaseSafe = Number(totals.rebase_safe || 0);
    const rebaseArchive = Number(totals.rebase_would_archive || 0);

    const label = releaseStateLabel(state);
    setText(sidebarReleaseStatus, label);
    setText(elReleaseStatusDetail, label);
    setText(sidebarReleaseCandidates, _releasePreview ? (fmtInt(selected) + " / " + fmtInt(qualified)) : "—");
    setText(elReleaseArticles, _releasePreview ? fmtInt(affected) : "—");
    setText(elReleaseSelection, _releasePreview ? (fmtInt(qualified) + " / " + fmtInt(selected)) : "—");
    setText(elReleaseConflicts, _releasePreview ? fmtInt(conflicts) : "—");
    setText(elReleaseRebase, _releasePreview ? (fmtInt(rebaseSafe) + " / " + fmtInt(rebaseArchive)) : "—");
    setBadge(tabBadgeRelease, _releasePreview ? fmtInt(selected) : "—");
    renderReleaseCandidatesTable();

    const busy = _releaseActionBusy;
    if (btnReleasePrepare) btnReleasePrepare.disabled = busy || state !== "normal" || !ready || selected <= 0;
    if (btnReleaseRefresh) btnReleaseRefresh.disabled = busy || state === "finalizing";
    if (btnReleaseCancel) btnReleaseCancel.disabled = busy || (state !== "release_focus" && state !== "finalizing");
    if (btnReleaseExecute) btnReleaseExecute.disabled = busy || state !== "release_focus" || !ready || selected <= 0;

    if (!elReleaseAction) return;
    if (busy && state === "finalizing") {
      setStatusLine(elReleaseAction, "Release wird durchgeführt …");
      return;
    }
    if (!busy && state === "finalizing") {
      setStatusLine(elReleaseAction, "Finalizing-Status ohne laufende Aktion. Falls der Server während eines Releases neu gestartet wurde, kann der Zustand mit ‚Vorbereitung abbrechen‘ zurückgesetzt werden.");
      return;
    }
    if (!_releasePreview) {
      setStatusLine(elReleaseAction, "Release-Preview noch nicht geladen.");
      return;
    }
    if (!ready) {
      setStatusLine(elReleaseAction, "Nicht releasefähig: " + fmtInt(Number(totals.blocking_articles || 0)) + " blockierende Artikel.");
      return;
    }
    if (selected <= 0) {
      setStatusLine(elReleaseAction, "Keine releasefähigen Änderungen vorhanden.");
      return;
    }
    if (state === "release_focus") {
      const started = workflow.release_focus_started_at ? (" · seit " + fmtDT(workflow.release_focus_started_at)) : "";
      setStatusLine(elReleaseAction, "Vorbereitung aktiv" + started + ". Vor Durchführung erneut prüfen.");
      return;
    }
    const last = workflow.last_release && workflow.last_release.version_label ? (" · letzter Release: " + workflow.last_release.version_label) : "";
    setStatusLine(elReleaseAction, "Bereit: " + fmtInt(selected) + " Änderungen in " + fmtInt(affected) + " Artikeln" + last + ".");
  }

  async function loadReleaseWorkflow(includePreview) {
    try {
      const workflow = await apiFetch("/api/admin/release/state", { method: "GET" });
      _releaseWorkflowState = workflow || {};
      if (includePreview !== false) {
        _releasePreview = await apiFetch("/api/admin/release/preview", { method: "GET" });
        if (_releasePreview && _releasePreview.workflow) {
          _releaseWorkflowState = _releasePreview.workflow;
        }
      }
      renderReleaseWorkflow();
      if (adminCommentItemsCache.length) renderAdminCommentsTableFromCache();
    } catch (e) {
      setStatusLine(elReleaseAction, "Fehler: " + normalizeErrorMessage(e));
      setText(sidebarReleaseStatus, "Fehler");
    }
  }

  async function prepareRelease() {
    if (!confirmAdminAction("Release-Vorbereitung starten? Neue Entwürfe können danach bis Abschluss oder Abbruch nicht zur Review eingereicht werden. Votes, Bearbeitung und bestehende Reviews laufen weiter.")) return;
    _releaseActionBusy = true;
    renderReleaseWorkflow();
    setStatusLine(elReleaseAction, "Starte Release-Vorbereitung …");
    try {
      const out = await apiFetch("/api/admin/release/prepare", { method: "POST", body: JSON.stringify({}) });
      _releaseWorkflowState = out && out.workflow ? out.workflow : _releaseWorkflowState;
      _releasePreview = out && out.preview ? out.preview : _releasePreview;
    } catch (e) {
      setStatusLine(elReleaseAction, "Fehler: " + normalizeErrorMessage(e));
    } finally {
      _releaseActionBusy = false;
      await loadReleaseWorkflow(true);
    }
  }

  async function cancelReleasePreparation() {
    if (!confirmAdminAction("Release-Vorbereitung abbrechen? Neue Einreichungen zur Review werden danach wieder freigegeben.")) return;
    _releaseActionBusy = true;
    renderReleaseWorkflow();
    try {
      const out = await apiFetch("/api/admin/release/cancel", { method: "POST", body: JSON.stringify({}) });
      _releaseWorkflowState = out && out.workflow ? out.workflow : _releaseWorkflowState;
      setStatusLine(elReleaseAction, "Release-Vorbereitung abgebrochen.");
    } catch (e) {
      setStatusLine(elReleaseAction, "Fehler: " + normalizeErrorMessage(e));
    } finally {
      _releaseActionBusy = false;
      await loadReleaseWorkflow(true);
    }
  }

  async function executeRelease() {
    const totals = releasePreviewTotals();
    const selected = Number(totals.selected_comments || 0);
    const affected = Number(totals.articles_would_change || 0);
    const archives = Number(totals.rebase_would_archive || 0);
    const question = "Release jetzt durchführen?\n\n" +
      fmtInt(selected) + " Änderungen werden in " + fmtInt(affected) + " Artikeln integriert.\n" +
      fmtInt(archives) + " nicht sicher rebasierbare Kommentare werden archiviert.\n" +
      "Vor der DB-Änderung wird automatisch der Pre-Release-Snapshot erzeugt.";
    if (!confirmAdminAction(question)) return;

    _releaseActionBusy = true;
    if (_releaseWorkflowState) _releaseWorkflowState.state = "finalizing";
    renderReleaseWorkflow();
    try {
      const out = await apiFetch("/api/admin/release/execute", { method: "POST", body: JSON.stringify({}) });
      const rel = out && out.release ? out.release : {};
      _releaseWorkflowState = out && out.workflow ? out.workflow : { state: "normal" };
      _releasePreview = null;
      _releaseActionBusy = false;
      renderReleaseWorkflow();
      setStatusLine(elReleaseAction, "Release abgeschlossen: " + String(rel.version_label || "") + ".");
      await loadOverviewAll();
      await loadSystem();
      await loadExports();
    } catch (e) {
      setStatusLine(elReleaseAction, "Release fehlgeschlagen: " + normalizeErrorMessage(e));
    } finally {
      _releaseActionBusy = false;
      await loadReleaseWorkflow(true);
    }
  }

  function renderExports(reports) {
    if (!elExports) return;
    elExports.innerHTML = "";
    if (!reports || !Array.isArray(reports) || reports.length === 0) {
      elExports.appendChild(document.createTextNode("Keine Exporte gefunden."));
      return;
    }

    const wrap = document.createElement("div");
    wrap.style.display = "grid";
    wrap.style.gap = "0.75rem";

    reports.forEach((r) => {
      const box = document.createElement("div");
      box.className = "anmerkung";

      const title = document.createElement("div");
      title.className = "klein";
      title.style.opacity = "0.9";
      const kind = document.createElement("b");
      kind.textContent = r.report_type === "release" ? "Release-Snapshot" : "Monatsexport";
      title.appendChild(kind);
      title.appendChild(document.createTextNode(
        " · " + fmtDT(r.generated_at) +
        " · Zeitraum: " + fmtDT(r.period_start) +
        " → " + fmtDT(r.period_end)
      ));
      box.appendChild(title);

      const files = document.createElement("div");
      files.className = "klein";
      files.style.marginTop = "0.35rem";

      const urls = (r && r.download_urls) || {};
      const names = (r && r.file_names) || {};
      const keys = Object.keys(urls || {});

      if (keys.length === 0) {
        files.appendChild(document.createTextNode("Keine Dateien verlinkt."));
      } else {
        keys.sort().forEach((k, idx) => {
          const a = document.createElement("a");
          a.href = String(urls[k] || "#");
          a.target = "_blank";
          a.rel = "noopener";
          a.textContent = (names[k] ? names[k] : k);
          if (idx > 0) files.appendChild(document.createTextNode(" · "));
          files.appendChild(a);
        });
      }
      box.appendChild(files);
      wrap.appendChild(box);
    });

    elExports.appendChild(wrap);
  }

  function updateAdminSidebarFromStats(s) {
    const users = Number(s && s.users_total || 0);
    const articles = Number(s && s.articles_total || 0);
    const comments = Number(s && s.comments_total || 0);
    const votes = Number(s && s.votes_total || 0);
    const reviews = Number(s && s.reviews_total || 0);

    setText(sidebarActivityComments, fmtInt(comments));
    setText(sidebarActivityVotes, fmtInt(votes));
    setText(sidebarActivityReviews, fmtInt(reviews));

    setText(tabBadgeUsers, users ? fmtInt(users) : "—");
    setText(tabBadgeArticles, articles ? fmtInt(articles) : "—");
    setText(tabBadgeComments, comments ? fmtInt(comments) : "—");
    setText(tabBadgeOps, "ok");

    setText(stripUsers, users ? fmtInt(users) : "—");
    setText(stripArticles, articles ? fmtInt(articles) : "—");
    setText(stripComments, comments ? fmtInt(comments) : "—");
    setText(stripReviews, reviews ? fmtInt(reviews) : "—");
  }

  async function loadSystem() {
    try {
      const h = await apiFetch("/api/health", { method: "GET" });
      setText(elHealth, (h && h.status) ? String(h.status) : "ok");
    } catch (e) {
      setText(elHealth, "Fehler: " + (e && e.message ? e.message : "unknown"));
    }

    try {
      const s = await apiFetch("/api/admin/stats", { method: "GET" });
      // kompakt, ohne Tabelle
      const parts = [];
      parts.push("Users: " + s.users_total);
      parts.push("Artikel: " + s.articles_total);
      parts.push("Versionen: " + s.article_versions_total);
      parts.push("Votes: " + s.votes_total + " (✓ " + s.votes_confirmed_total + " / … " + s.votes_pending_total + ")");
      parts.push("Kommentare: " + s.comments_total);
      parts.push("Reviews: " + s.reviews_total);
      setText(elStats, parts.join(" · "));
      updateAdminSidebarFromStats(s);
    } catch (e) {
      setText(elStats, "Fehler: " + (e && e.message ? e.message : "unknown"));
    }
  }

  async function loadExports() {
    if (!elExports) return;
    setText(elExports, "Lade Exporte…");
    try {
      const list = await apiFetch("/api/admin/exports?limit=10", { method: "GET" });
      renderExports(list);
      const first = Array.isArray(list) && list.length ? list[0] : null;
      setText(sidebarExportLast, first && first.generated_at ? fmtDT(first.generated_at) : "—");
    } catch (e) {
      setText(elExports, "Fehler: " + (e && e.message ? e.message : "unknown"));
    }
  }

  async function runExport(mode, sourceButton) {
    if (!confirmAdminAction("Export starten: " + String(mode) + "?")) return;
    setText(elExportStatus, "Starte " + mode + "…");
    try {
      const lockButton = sourceButton || (mode === "release" ? btnExportRelease : btnExportMonthly);
      const out = await withActionLock(lockButton, () => apiFetch("/api/admin/exports/run?mode=" + encodeURIComponent(mode), {
        method: "POST",
        body: JSON.stringify({}),
      }));
      const links = [];
      const paths = (out && out.paths) || {};
      Object.keys(paths).sort().forEach((k) => {
        const rel = String(paths[k] || "");
        // Export-Script schreibt unter versions/...; dafür existiert der sichere Download-Endpunkt:
        const a = document.createElement("a");
        a.href = "/api/admin/files?path=" + encodeURIComponent(rel);
        a.target = "_blank";
        a.rel = "noopener";
        a.textContent = rel;
        links.push(a);
      });
      if (links.length === 0) {
        setText(elExportStatus, "Fertig.");
      } else {
        elExportStatus.innerHTML = "";
        elExportStatus.appendChild(document.createTextNode("Fertig: "));
        links.forEach((a, idx) => {
          if (idx > 0) elExportStatus.appendChild(document.createTextNode(" · "));
          elExportStatus.appendChild(a);
        });
      }
      await loadExports();
      await loadSystem();
    } catch (e) {
      setText(elExportStatus, "Fehler: " + normalizeErrorMessage(e));
    }
  }

  function createTopicEditor(item) {
    const row = document.createElement("div");
    row.className = "admin-topic-editor";

    const head = document.createElement("div");
    head.className = "admin-topic-editor-head";
    const label = document.createElement("b");
    label.className = "klein";
    label.textContent = "Thema";
    head.appendChild(label);

    const actions = document.createElement("div");
    actions.className = "admin-topic-editor-actions";
    [["↑", "up"], ["↓", "down"], ["Entfernen", "remove"]].forEach(([txt, action]) => {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "btn btn-ghost btn-sm";
      btn.dataset.topicAction = action;
      btn.textContent = txt;
      actions.appendChild(btn);
    });
    head.appendChild(actions);
    row.appendChild(head);

    const grid = document.createElement("div");
    grid.className = "admin-topic-editor-grid";

    function addField(labelText, selectorClass, value, opts) {
      const options = opts || {};
      const field = document.createElement("label");
      field.className = "field" + (options.full ? " field-full" : "");
      const lab = document.createElement("span");
      lab.className = "klein";
      lab.textContent = labelText;
      field.appendChild(lab);
      const input = options.textarea ? document.createElement("textarea") : document.createElement("input");
      input.className = (options.textarea ? "textarea " : "input ") + selectorClass;
      if (options.textarea) input.rows = options.rows || 3;
      if (options.maxLength) input.maxLength = options.maxLength;
      input.value = String(value || "");
      field.appendChild(input);
      grid.appendChild(field);
    }

    const data = item && typeof item === "object" ? item : {};
    addField("Titel *", "topic-title", data.title, { maxLength: 120 });
    addField("Link-Text", "topic-link-label", data.link_label, { maxLength: 80 });
    addField("Text *", "topic-text", data.text, { full: true, textarea: true, rows: 3, maxLength: 700 });
    addField("Link (optional)", "topic-link-url", data.link_url, { full: true, maxLength: 300 });
    row.appendChild(grid);
    return row;
  }

  function renumberTopicEditors() {
    if (!elTopicsList) return;
    Array.from(elTopicsList.querySelectorAll(".admin-topic-editor")).forEach((row, idx) => {
      const label = row.querySelector(".admin-topic-editor-head b");
      if (label) label.textContent = "Thema " + String(idx + 1);
    });
  }

  function renderCurrentTopics(data) {
    if (!elTopicsList) return;
    elTopicsList.innerHTML = "";
    const items = data && Array.isArray(data.items) ? data.items : [];
    currentTopicsVersion = Number(data && data.version || 0);
    items.forEach((item) => elTopicsList.appendChild(createTopicEditor(item)));
    if (!items.length) {
      const empty = document.createElement("div");
      empty.className = "klein admin-muted";
      empty.dataset.topicsEmpty = "1";
      empty.textContent = "Noch keine aktuellen Themen. Mit „Thema hinzufügen“ beginnt die Liste.";
      elTopicsList.appendChild(empty);
    }
    renumberTopicEditors();
    setText(sidebarTopicsCount, fmtInt(items.length));
    const updated = data && data.updated_at_label ? " · zuletzt " + String(data.updated_at_label) : "";
    setText(elTopicsStatus, "Gespeicherter Stand: v" + String(currentTopicsVersion) + updated);
  }

  function addTopicEditor(item) {
    if (!elTopicsList) return;
    const empty = elTopicsList.querySelector("[data-topics-empty]");
    if (empty) empty.remove();
    const count = elTopicsList.querySelectorAll(".admin-topic-editor").length;
    if (count >= 6) {
      setText(elTopicsStatus, "Maximal 6 Themen.");
      return;
    }
    elTopicsList.appendChild(createTopicEditor(item || {}));
    renumberTopicEditors();
  }

  function readCurrentTopicsForm() {
    if (!elTopicsList) return [];
    const rows = Array.from(elTopicsList.querySelectorAll(".admin-topic-editor"));
    return rows.map((row) => ({
      title: String((row.querySelector(".topic-title") || {}).value || "").trim(),
      text: String((row.querySelector(".topic-text") || {}).value || "").trim(),
      link_url: String((row.querySelector(".topic-link-url") || {}).value || "").trim(),
      link_label: String((row.querySelector(".topic-link-label") || {}).value || "").trim(),
    })).filter((item) => item.title || item.text || item.link_url || item.link_label);
  }

  async function loadCurrentTopics() {
    if (!elTopicsList) return;
    setText(elTopicsStatus, "Lade…");
    try {
      const data = await apiFetch("/api/admin/current-topics", { method: "GET" });
      renderCurrentTopics(data || {});
    } catch (e) {
      setText(elTopicsStatus, "Fehler: " + normalizeErrorMessage(e));
      setText(sidebarTopicsCount, "—");
    }
  }

  async function saveCurrentTopics() {
    const items = readCurrentTopicsForm();
    for (const item of items) {
      if (!item.title || !item.text) {
        setText(elTopicsStatus, "Titel und Text sind für jedes Thema Pflicht.");
        return;
      }
    }
    setText(elTopicsStatus, "Speichere…");
    try {
      const data = await withActionLock(btnTopicsSave, () => apiFetch("/api/admin/current-topics", {
        method: "PUT",
        body: JSON.stringify({ version: currentTopicsVersion, items }),
      }));
      renderCurrentTopics(data || {});
      setText(elTopicsStatus, "Gespeichert" + (data && data.updated_at_label ? " · " + data.updated_at_label : "") + ".");
    } catch (e) {
      setText(elTopicsStatus, "Fehler: " + normalizeErrorMessage(e));
    }
  }


  // ------------------------------------------------------------------
  // Übersicht (Admin): Nutzer / Artikel / Kommentare / Reviews / Votes
  // ------------------------------------------------------------------

  function tableHtml(headers, rowsHtml) {
    const th = headers.map((h) => "<th>" + esc(h) + "</th>").join("");
    return "<thead><tr>" + th + "</tr></thead><tbody>" + (rowsHtml || "") + "</tbody>";
  }

  function pillHtml(text) {
    return "<span class='admin-pill'>" + esc(text) + "</span>";
  }

  function voteBarHtml(byMain) {
    const bm = byMain || {};
    const total = Object.values(bm).reduce((a, b) => a + Number(b || 0), 0);
    if (!total) return "<span class='admin-muted'>—</span>";
    const map = { "✅": "vote-ok", "🟢": "vote-yes", "🟡": "vote-mid", "🟠": "vote-warn", "🔴": "vote-bad" };
    const order = ["✅", "🟢", "🟡", "🟠", "🔴"];
    const spans = order.map((emo) => {
      const n = Number(bm[emo] || 0);
      if (!n) return "";
      const pct = Math.max(0, Math.min(100, (n / total) * 100));
      const cls = map[emo] || "";
      return "<span class='" + cls + "' style='width:" + pct.toFixed(2) + "%'></span>";
    }).join("");
    return "<div class='vote-bar' title='" + esc(JSON.stringify(bm)) + "'>" + spans + "</div>";
  }

  function kvInline(obj) {
    if (!obj || typeof obj !== "object") return "";
    const parts = [];
    for (const [k, v] of Object.entries(obj)) {
      if (v == null) continue;
      const n = Number(v || 0);
      if (!n) continue;
      parts.push(String(k) + ": " + fmtInt(n));
    }
    return parts.join(" · ");
    }

  async function loadOverviewUsers() {
    if (!tblUsers) return;
    // AUDIT(v1.0.27): include_personal zeigt E-Mail/PLZ im Admin-DOM. Default bleibt aus;
    // Backend-Audit-Logging/Auto-Reset später separat prüfen.
    setText(elUsersMeta, "Lade…");
    const userHeaders = ["ID","Pseudonym","E-Mail","PLZ","Trust","Admin","Erstellt","Stimmen","Kommentare","Reviews","Review-Profil","Negativ","Treffer","Slider-Abw.","Hinweis"];
    setHtml(tblUsers, tableHtml(userHeaders, ""));
    try {
      const includePersonal = cbOverviewPersonal && cbOverviewPersonal.checked ? "1" : "0";
      const out = await apiFetch("/api/admin/overview/users?include_personal=" + includePersonal + "&limit=200", { method: "GET" });
      const items = Array.isArray(out && out.items) ? out.items : [];
      const rows = items.map((u) => {
        const profile = "A:" + fmtInt(u.review_accept_total || 0) +
          " / K:" + fmtInt(u.review_revise_total || 0) +
          " / X:" + fmtInt(u.review_reject_total || 0) +
          " / R:" + fmtInt(u.review_report_total || 0);
        const negRatio = (typeof u.review_negative_ratio === "number") ? Math.round(u.review_negative_ratio * 100) + "%" : "—";
        const hitRate = (typeof u.review_alignment_rate === "number")
          ? Math.round(u.review_alignment_rate * 100) + "% (" + fmtInt(u.review_alignment_hits || 0) + "/" + fmtInt(u.review_alignment_total || 0) + ")"
          : "—";
        const dev = (typeof u.review_slider_deviation_avg === "number") ? u.review_slider_deviation_avg.toFixed(2) : "—";
        const flag = String(u.review_quality_flag || "ok");
        const reasons = Array.isArray(u.review_quality_reasons) && u.review_quality_reasons.length
          ? u.review_quality_reasons.join(" · ")
          : "—";
        const flagCell = "<span class='admin-quality-pill admin-quality-" + esc(flag) + "' title='" + esc(reasons) + "'>" + esc(flag) + "</span>";
        return (
          "<tr>" +
            "<td>" + esc(u.id) + "</td>" +
            "<td>" + esc(u.pseudonym || "") + "</td>" +
            "<td>" + esc(u.email || "") + "</td>" +
            "<td>" + esc(u.plz || "") + "</td>" +
            "<td>" + esc(u.trust_level ?? 0) + "</td>" +
            "<td>" + (u.is_admin ? pillHtml("admin") : "<span class='admin-muted'>—</span>") + "</td>" +
            "<td>" + esc(fmtDT(u.created_at)) + "</td>" +
            "<td>" + esc(fmtInt(u.votes_total)) + "</td>" +
            "<td>" + esc(fmtInt(u.comments_total)) + "</td>" +
            "<td>" + esc(fmtInt(u.reviews_total)) + "</td>" +
            "<td class='admin-nowrap' title='A=annehmen, K=korrigieren, X=ablehnen, R=Report'>" + esc(profile) + "</td>" +
            "<td>" + esc(negRatio) + "</td>" +
            "<td>" + esc(hitRate) + "</td>" +
            "<td>" + esc(dev) + "</td>" +
            "<td>" + flagCell + "</td>" +
          "</tr>"
        );
      }).join("");
      setHtml(tblUsers, tableHtml(userHeaders, rows));
      const note = out && out.meta && out.meta.note ? String(out.meta.note) : "";
      const gen = out && out.meta && out.meta.generated_at ? fmtDT(out.meta.generated_at) : "";
      setText(elUsersMeta, items.length + " Nutzer" + (gen ? " · Stand: " + gen : "") + (note ? " · " + note : ""));
      setText(stripUsers, fmtInt(items.length));
      setBadge(tabBadgeUsers, fmtInt(items.length));
    } catch (e) {
      setText(elUsersMeta, "Fehler: " + (e && e.message ? e.message : "unknown"));
      setHtml(tblUsers, tableHtml(userHeaders, ""));
    }
  }

  async function loadOverviewArticles() {
    if (!tblArticles) return;
    setText(elArticlesMeta, "Lade…");
    setHtml(tblArticles, tableHtml(["ID","Slug","Titel","Typ","Current","Versionen","Votes","% Zustimmung","Kommentare","Pending","Öffnen"], ""));
    try {
      const out = await apiFetch("/api/admin/overview/articles", { method: "GET" });
      const items = Array.isArray(out && out.items) ? out.items : [];
      const rows = items.map((a) => {
        const byStatus = a.comments_by_status || {};
        const cInfo = kvInline(byStatus) || "—";
        const appr = (typeof a.approval_percent === "number") ? (a.approval_percent.toFixed(2) + "%") : "—";
        const cv = a.current_version_label || a.current_version_id || "—";
        return (
          "<tr>" +
            "<td>" + esc(a.id) + "</td>" +
            "<td><span class='admin-muted'>" + esc(a.slug) + "</span></td>" +
            "<td>" + esc(a.title) + "</td>" +
            "<td>" + esc(a.type) + "</td>" +
            "<td>" + esc(cv) + "</td>" +
            "<td>" + esc(fmtInt(a.versions_total)) + "</td>" +
            "<td>" + voteBarHtml(a.votes_by_main) + "<div class='klein admin-muted' style='margin-top:0.2rem;'>" + esc(fmtInt(a.votes_total)) + "</div></td>" +
            "<td>" + esc(appr) + "</td>" +
            "<td>" + esc(fmtInt(a.comments_total)) + "<div class='klein admin-muted' style='margin-top:0.2rem;'>" + esc(cInfo) + "</div></td>" +
            "<td>" + esc(fmtInt(a.pending_changes)) + "</td>" +
            "<td>" + renderAdminOpenArticleLink(a.id, "Öffnen") + "</td>" +
          "</tr>"
        );
      }).join("");
      setHtml(tblArticles, tableHtml(["ID","Slug","Titel","Typ","Current","Versionen","Votes","% Zustimmung","Kommentare","Pending","Öffnen"], rows));
      const gen = out && out.meta && out.meta.generated_at ? fmtDT(out.meta.generated_at) : "";
      setText(elArticlesMeta, items.length + " Artikel" + (gen ? " · Stand: " + gen : ""));
      setText(stripArticles, fmtInt(items.length));
      setBadge(tabBadgeArticles, fmtInt(items.length));
    } catch (e) {
      setText(elArticlesMeta, "Fehler: " + (e && e.message ? e.message : "unknown"));
      setHtml(tblArticles, tableHtml(["ID","Slug","Titel","Typ","Current","Versionen","Votes","% Zustimmung","Kommentare","Pending","Öffnen"], ""));
    }
  }

  async function loadOverviewComments() {
    if (!tblComments) return;
    setText(elCommentsMeta, "Lade…");
    setHtml(tblComments, tableHtml(["CID","Prio","Artikel","Modus","Status","Review","Votes","Kandidat","Release","Alter","Autor","Öffnen","Aktion"], ""));
    try {
      const hideDone = cbCommentsHideDone && cbCommentsHideDone.checked ? 1 : 0;
      const sort = selCommentsSort && selCommentsSort.value ? String(selCommentsSort.value) : "newest";
      const [overviewResult, historyResult] = await Promise.allSettled([
        apiFetch("/api/admin/overview/comments?hide_done=" + hideDone + "&sort=" + encodeURIComponent(sort) + "&limit=250&offset=0", { method: "GET" }),
        apiFetch("/api/admin/release/comment-history", { method: "GET" }),
      ]);
      if (overviewResult.status !== "fulfilled") throw overviewResult.reason;
      const out = overviewResult.value;
      if (historyResult.status === "fulfilled") {
        const hist = historyResult.value;
        releaseCommentHistoryById = hist && hist.comments && typeof hist.comments === "object"
          ? hist.comments
          : Object.create(null);
      } else {
        releaseCommentHistoryById = Object.create(null);
      }
      adminCommentItemsCache = Array.isArray(out && out.items) ? out.items : [];
      renderAdminCommentsTableFromCache();
      renderReleaseCandidatesTable();
      setText(sidebarCriticalReports, fmtInt(adminCommentItemsCache.filter(isReportComment).length));
      const gen = out && out.meta && out.meta.generated_at ? fmtDT(out.meta.generated_at) : "";
      const shown = tblComments ? tblComments.querySelectorAll("tbody tr").length : 0;
      setText(elCommentsMeta, fmtInt(shown) + " / " + fmtInt(adminCommentItemsCache.length) + " Einträge" + (activeAdminCommentView !== "alle" ? (" · Ansicht: " + activeAdminCommentView) : "") + (gen ? " · Stand: " + gen : ""));
      setText(stripComments, fmtInt(adminCommentItemsCache.length));
      if (!_releasePreview) setText(sidebarReleaseCandidates, fmtInt(adminCommentItemsCache.filter(isReleaseComment).length));
      setBadge(tabBadgeComments, fmtInt(adminCommentItemsCache.length));
    } catch (e) {
      adminCommentItemsCache = [];
      renderReleaseCandidatesTable();
      setText(sidebarCriticalReports, "—");
      setText(elCommentsMeta, "Fehler: " + (e && e.message ? e.message : "unknown"));
      setHtml(tblComments, tableHtml(["CID","Prio","Artikel","Modus","Status","Review","Votes","Kandidat","Release","Alter","Autor","Öffnen","Aktion"], ""));
    }
  }

  async function loadCommentChanges() {
    if (!tblCommentChanges) return;
    setText(elCommentChangesMeta, "Lade…");
    setHtml(
      tblCommentChanges,
      tableHtml(["CID","Part","Span","Typ","Stil","Text"], "")
    );
    try {
      const out = await apiFetch("/api/admin/comment-changes?limit_comments=250", { method: "GET" });
      const items = Array.isArray(out && out.items) ? out.items : [];
      adminCommentChangeIds = new Set(items.map((it) => Number(it && it.cid || 0)).filter(Boolean));
      renderAdminCommentsTableFromCache();
      const rows = items.map((it) => {
        const cid = String(it.cid ?? "");
        const partId = String(it.part_id || "—");
        const spanId = String(it.span_id || "—");
        const kind = String(it.kind || "");
        const subkind = String(it.subkind || "");
        const state = String(it.recommended_state || it.default_state || "normal");
        const rawText = stripTagsToText(it.html_fragment || "");
        const shortText = trunc(rawText, 96);
        const typeText = subkind ? (kind + " · " + subkind) : kind;
        return (
          "<tr>" +
            "<td class='cc-mini' title='" + esc(cid) + "'>" + esc(cid) + "</td>" +
            "<td class='cc-mini' title='" + esc(partId) + "'>" + esc(partId) + "</td>" +
            "<td class='cc-mini' title='" + esc(spanId) + "'>" + esc(spanId) + "</td>" +
            "<td class='cc-mini' title='" + esc(typeText) + "'>" + esc(typeText) + "</td>" +
            "<td class='cc-mini' title='" + esc(state) + "'>" + esc(state) + "</td>" +
            "<td class='cc-ellipsis' title='" + esc(rawText) + "'>" + esc(shortText) + "</td>" +
          "</tr>"
        );
      }).join("");
      setHtml(
        tblCommentChanges,
        tableHtml(["CID","Part","Span","Typ","Stil","Text"], rows)
      );
      const meta = out && out.meta ? out.meta : {};
      setText(
        elCommentChangesMeta,
        fmtInt(meta.rows_total || items.length) + " Rows · " +
        fmtInt(meta.comments_total || 0) + " Kommentare" +
        ((meta.locate_failures_total || 0) ? (" · locate_failures: " + fmtInt(meta.locate_failures_total || 0)) : "")
      );
    } catch (e) {
      adminCommentChangeIds = new Set();
      renderAdminCommentsTableFromCache();
      setText(elCommentChangesMeta, "Fehler: " + (e && e.message ? e.message : "unknown"));
      setHtml(
        tblCommentChanges,
        tableHtml(["CID","Part","Span","Typ","Stil","Text"], "")
      );
    }
  }

  async function loadOverviewReviewsQueue() {
    if (!tblReviews) return;
    setText(elReviewsMeta, "Lade…");
    setHtml(tblReviews, tableHtml(["Kommentar","Artikel","Autor","Erstellt","Required","Offen","Rollup","Öffnen"], ""));
    try {
      const out = await apiFetch("/api/admin/overview/reviews/queue?limit=200", { method: "GET" });
      const items = Array.isArray(out && out.items) ? out.items : [];
      adminReviewQueueCommentIds = new Set(items.map((it) => Number(it && it.comment && it.comment.id || 0)).filter(Boolean));
      renderAdminCommentsTableFromCache();
      const rows = items.map((it) => {
        const c = it.comment || {};
        const roll = c.review_rollup || {};
        const req = (typeof c.required_reviews === "number") ? c.required_reviews : 0;
        const open = (typeof it.open_reviews_needed === "number") ? it.open_reviews_needed : 0;
        const rollTxt = "A:" + fmtInt(roll.approve || 0) + " / R:" + fmtInt(roll.revise || 0) + " / X:" + fmtInt(roll.reject || 0) + " (" + fmtInt(roll.total || 0) + ")";
        const art = (c.article_title ? String(c.article_title) : (c.article_slug || ""));
        const artShort = art.length > 36 ? (art.slice(0, 33) + "…") : art;
        return (
          "<tr>" +
            "<td>" + esc(c.id) + "</td>" +
            "<td title='" + esc(art) + "'>" + esc(artShort) + "</td>" +
            "<td>" + esc(c.user_pseudonym || "") + "</td>" +
            "<td>" + esc(fmtDT(c.created_at)) + "</td>" +
            "<td>" + esc(fmtInt(req)) + "</td>" +
            "<td>" + esc(fmtInt(open)) + "</td>" +
            "<td>" + esc(rollTxt) + "</td>" +
            "<td>" + renderAdminOpenCommentLink(c.article_id, c.id, "Öffnen") + "</td>" +
          "</tr>"
        );
      }).join("");
      setHtml(tblReviews, tableHtml(["Kommentar","Artikel","Autor","Erstellt","Required","Offen","Rollup","Öffnen"], rows));
      const gen = out && out.meta && out.meta.generated_at ? fmtDT(out.meta.generated_at) : "";
      const total = (typeof out.total_in_review === "number") ? out.total_in_review : items.length;
      setText(elReviewsMeta, fmtInt(total) + " in review" + (gen ? " · Stand: " + gen : ""));
      setText(stripReviews, fmtInt(total));
    } catch (e) {
      adminReviewQueueCommentIds = new Set();
      renderAdminCommentsTableFromCache();
      setText(elReviewsMeta, "Fehler: " + (e && e.message ? e.message : "unknown"));
      setHtml(tblReviews, tableHtml(["Kommentar","Artikel","Autor","Erstellt","Required","Offen","Rollup","Öffnen"], ""));
    }
  }

  async function loadOverviewVotesDistribution() {
    if (!tblVotesDist) return;
    setText(elVotesDistMeta, "Lade…");
    setHtml(tblVotesDist, tableHtml(["Tag","Votes","Nutzer","% Zustimmung","Verteilung"], ""));
    try {
      const out = await apiFetch("/api/admin/overview/votes/distribution?days=30", { method: "GET" });
      const series = Array.isArray(out && out.series) ? out.series : [];
      const rows = series.map((d) => {
        const appr = (typeof d.approval_percent === "number") ? (d.approval_percent.toFixed(2) + "%") : "—";
        return (
          "<tr>" +
            "<td>" + esc(d.day) + "</td>" +
            "<td>" + esc(fmtInt(d.total)) + "</td>" +
            "<td>" + esc(fmtInt(d.distinct_users)) + "</td>" +
            "<td>" + esc(appr) + "</td>" +
            "<td>" + voteBarHtml(d.by_main) + "</td>" +
          "</tr>"
        );
      }).join("");
      setHtml(tblVotesDist, tableHtml(["Tag","Votes","Nutzer","% Zustimmung","Verteilung"], rows));
      const gen = out && out.meta && out.meta.generated_at ? fmtDT(out.meta.generated_at) : "";
      setText(elVotesDistMeta, fmtInt(series.length) + " Tage" + (gen ? " · Stand: " + gen : ""));
    } catch (e) {
      setText(elVotesDistMeta, "Fehler: " + (e && e.message ? e.message : "unknown"));
      setHtml(tblVotesDist, tableHtml(["Tag","Votes","Nutzer","% Zustimmung","Verteilung"], ""));
    }
  }

  async function loadOverviewAll() {
    // AUDIT(v1.0.27): bewusst sequentiell für einfache Fehlerbilder. Bei großen Tabellen
    // später parallelisieren und Teilausfälle pro Karte isoliert anzeigen.
    const t0 = Date.now();
    try {
      await loadOverviewUsers();
      await loadOverviewArticles();
      await loadOverviewComments();
      await loadCommentChanges();
      await loadOverviewReviewsQueue();
      await loadOverviewVotesDistribution();
    } finally {
      if (elOverviewMeta) {
        const dt = Math.max(0, Date.now() - t0);
        const extra = (elOverviewMeta.textContent && elOverviewMeta.textContent !== "—") ? "" : ("Stand: " + new Date().toLocaleString("de-DE"));
        if (extra) setText(elOverviewMeta, extra + " · geladen in " + dt + "ms");
      }
    }
  }

  function renderWebBadge(state) {
    if (!elWebBadge) return;
    const kind = (state && state.kind) ? String(state.kind) : "muted";
    const label = (state && state.label) ? String(state.label) : "—";
    const styles = {
      ok: "background: rgba(22,163,74,0.10); border-color: rgba(22,163,74,0.22); color: #14532d;",
      warn: "background: rgba(234,179,8,0.12); border-color: rgba(234,179,8,0.30); color: #78350f;",
      danger: "background: rgba(220,38,38,0.10); border-color: rgba(220,38,38,0.25); color: #7f1d1d;",
      muted: "background: rgba(15,23,42,0.06); border-color: rgba(148,163,184,0.55); color: #0f172a;",
    };
    elWebBadge.className = "variant-pill";
    elWebBadge.style.cssText = "padding:0.16rem 0.5rem; " + (styles[kind] || styles.muted);
    elWebBadge.textContent = label;
  }

  function renderWebTargetOptions(items, currentRef) {
    if (!selWebTarget) return;
    selWebTarget.innerHTML = "";
    const list = Array.isArray(items) ? items : [];
    if (list.length === 0) {
      const opt = document.createElement("option");
      opt.value = "";
      opt.textContent = "(keine Tags gefunden)";
      selWebTarget.appendChild(opt);
      selWebTarget.disabled = true;
      return;
    }
    selWebTarget.disabled = false;

    list.forEach((it) => {
      const opt = document.createElement("option");
      opt.value = String(it.ref || "");
      const stableMark = it.is_stable ? " · stable" : "";
      opt.textContent = String(it.ref || "") + stableMark;
      if (String(it.ref || "") === String(currentRef || "")) opt.selected = true;
      selWebTarget.appendChild(opt);
    });
  }

  async function loadWebVersions(force) {
    if (!elWebCurrent && !elWebBadge) return;
    try {
      const qs = force ? "?force=1" : "";
      const info = await apiFetch("/api/admin/web/versions" + qs, { method: "GET" });

      const current = (info && info.current_version) ? String(info.current_version) : "—";
      setText(elWebCurrent, current);
      setText(elWebCurrentDetail, current);

      if (cbWebStable) cbWebStable.checked = !!(info && info.current_is_stable);

      const isUpdate = !!(info && info.update_available);
      renderWebBadge(isUpdate ? { label: "Update steht an", kind: "danger" } : { label: "aktuell", kind: "ok" });
      setBadge(tabBadgeOps, isUpdate ? "Update" : "ok");

      renderWebTargetOptions((info && info.available_versions) || [], (info && info.current_ref) || current);

      if (elWebAction) {
        const cacheAge = (info && typeof info.cache_age_sec === "number") ? Math.round(info.cache_age_sec) : null;
        const checkedAt = info && info.last_checked_at ? fmtDT(info.last_checked_at) : null;
        const small = [];
        if (checkedAt) small.push("GitHub zuletzt geprüft: " + checkedAt);
        if (cacheAge != null) small.push("Cache: " + cacheAge + "s");
        setText(elWebAction, small.join(" · "));
      }
    } catch (e) {
      setText(elWebCurrent, "—");
      setText(elWebCurrentDetail, "—");
      renderWebBadge({ label: "Fehler", kind: "warn" });
      if (elWebAction) setText(elWebAction, "Fehler: " + (e && e.message ? e.message : "unknown"));
    }
  }

  async function setCurrentStableFlag(isStable) {
    if (!cbWebStable) return;
    if (!confirmAdminAction("Aktuelle Web-Version als " + (isStable ? "stable" : "nicht stable") + " markieren?")) {
      await loadWebVersions(false);
      return;
    }
    setText(elWebAction, "Speichere…");
    try {
      await withActionLock(cbWebStable, () => apiFetch("/api/admin/web/stable", {
        method: "POST",
        body: JSON.stringify({ stable: !!isStable }),
      }));
      await loadWebVersions(true);
      setText(elWebAction, "Gespeichert.");
    } catch (e) {
      setText(elWebAction, "Fehler: " + (e && e.message ? e.message : "unknown"));
    }
  }

  async function runWebUpgrade() {
    if (!selWebTarget) return;
    const ref = (selWebTarget.value || "").trim();
    if (!ref) {
      setText(elWebAction, "Bitte Zielversion wählen.");
      return;
    }
    if (!confirmAdminAction("Web-Update auf " + ref + " starten? Der Server wird neu gestartet.")) return;
    setText(elWebAction, "Update auf " + ref + " wird vorbereitet…");
    try {
      await withActionLock(btnWebUpgrade, () => apiFetch("/api/admin/web/upgrade", {
        method: "POST",
        body: JSON.stringify({ ref }),
      }));
      await waitForWebRestartAndReload(ref);
    } catch (e) {
      if (isRestartHttpError(e)) {
        await waitForWebRestartAndReload(ref);
        return;
      }
      setText(elWebAction, "Fehler: " + (e && e.message ? e.message : "unknown"));
    }
  }

  async function init() {
    setupAuthStateSync();
    initAdminTabs();
    initAdminCommentViews();
    restoreAdminControlPrefs();
    setAdminCockpitVisible(false);
    disableUI(true);
    setAuthStatus("Initialisiere…");

    const token = getToken();
    if (!token) {
      setAdminCockpitVisible(false);
      setAuthStatus("Nicht eingeloggt. Bitte zuerst auf der Startseite anmelden (JWT fehlt).");
      return;
    }

    let me = null;
    try {
      me = await apiFetch("/api/me", { method: "GET" });
    } catch (e) {
      setAdminCockpitVisible(false);
      disableUI(true);
      if (e && Number(e.status) === 401) {
        clearLocalAuth("admin_me_401");
      }
      setAuthStatus("Login ungültig/abgelaufen. Bitte neu einloggen. (" + (e && e.message ? e.message : "Fehler") + ")");
      return;
    }

    const isAdmin = !!(me && me.is_admin);
    if (!isAdmin) {
      setAdminCockpitVisible(false);
      disableUI(true);
      setAuthStatus("Eingeloggt als " + (me.pseudonym || "User") + " – kein Admin.");
      return;
    }

    setAdminCockpitVisible(true);
    setAuthStatus("Eingeloggt als " + (me.pseudonym || "Admin") + " – Admin OK.");
    disableUI(false);

    if (btnExportRelease) btnExportRelease.addEventListener("click", () => runExport("release", btnExportRelease));
    if (btnExportMonthly) btnExportMonthly.addEventListener("click", () => runExport("monthly", btnExportMonthly));
    if (btnExportReleaseDetail) btnExportReleaseDetail.addEventListener("click", () => runExport("release", btnExportReleaseDetail));
    if (btnExportMonthlyDetail) btnExportMonthlyDetail.addEventListener("click", () => runExport("monthly", btnExportMonthlyDetail));
    if (btnWebRefresh) btnWebRefresh.addEventListener("click", () => loadWebVersions(true));
    if (btnWebRefreshDetail) btnWebRefreshDetail.addEventListener("click", () => loadWebVersions(true));
    if (btnWebUpgrade) btnWebUpgrade.addEventListener("click", () => runWebUpgrade());
    if (cbWebStable) cbWebStable.addEventListener("change", () => setCurrentStableFlag(cbWebStable.checked));
    if (btnTopicsAdd) btnTopicsAdd.addEventListener("click", () => addTopicEditor({}));
    if (btnTopicsSave) btnTopicsSave.addEventListener("click", () => saveCurrentTopics());
    if (elTopicsList) elTopicsList.addEventListener("click", (ev) => {
      const btn = ev.target && ev.target.closest ? ev.target.closest("button[data-topic-action]") : null;
      if (!btn) return;
      const row = btn.closest(".admin-topic-editor");
      if (!row) return;
      const action = String(btn.dataset.topicAction || "");
      if (action === "remove") row.remove();
      if (action === "up" && row.previousElementSibling && row.previousElementSibling.classList.contains("admin-topic-editor")) {
        row.parentNode.insertBefore(row, row.previousElementSibling);
      }
      if (action === "down" && row.nextElementSibling && row.nextElementSibling.classList.contains("admin-topic-editor")) {
        row.parentNode.insertBefore(row.nextElementSibling, row);
      }
      renumberTopicEditors();
      if (!elTopicsList.querySelector(".admin-topic-editor")) {
        const empty = document.createElement("div");
        empty.className = "klein admin-muted";
        empty.dataset.topicsEmpty = "1";
        empty.textContent = "Keine Themen. Speichern entfernt alle Einträge aus der Startseite.";
        elTopicsList.appendChild(empty);
      }
    });

    // Übersicht Controls
    if (btnOverviewRefresh) btnOverviewRefresh.addEventListener("click", () => loadOverviewAll());
    if (cbOverviewPersonal) cbOverviewPersonal.addEventListener("change", () => {
      setStoredBool(ADMIN_OVERVIEW_PERSONAL_STORAGE_KEY, cbOverviewPersonal.checked);
      loadOverviewUsers();
    });
    if (selCommentsSort) selCommentsSort.addEventListener("change", () => {
      setStoredString(ADMIN_COMMENTS_SORT_STORAGE_KEY, selCommentsSort.value || "newest");
      loadOverviewComments();
    });
    if (cbCommentsHideDone) cbCommentsHideDone.addEventListener("change", () => {
      setStoredBool(ADMIN_COMMENTS_HIDE_DONE_STORAGE_KEY, cbCommentsHideDone.checked);
      loadOverviewComments();
    });
    if (tblComments) tblComments.addEventListener("change", (ev) => {
      const sel = ev.target && ev.target.closest ? ev.target.closest("[data-admin-comment-action]") : null;
      if (!sel || !sel.value) return;
      const action = String(sel.value || "");
      sel.selectedIndex = 0;
      if (action.indexOf("admin-publish:") === 0) {
        adminPublishCommentStartphase(action.slice("admin-publish:".length));
        return;
      }
      window.open(action, adminWorktabTarget());
    });


    if (btnReleasePrepare) btnReleasePrepare.addEventListener("click", () => prepareRelease());
    if (btnReleaseRefresh) btnReleaseRefresh.addEventListener("click", () => loadReleaseWorkflow(true));
    if (btnReleaseCancel) btnReleaseCancel.addEventListener("click", () => cancelReleasePreparation());
    if (btnReleaseExecute) btnReleaseExecute.addEventListener("click", () => executeRelease());

    await loadOverviewAll();
    await loadSystem();
    await loadWebVersions(false);
    await loadReleaseWorkflow(true);
    await loadExports();
    await loadCurrentTopics();
  }

  document.addEventListener("DOMContentLoaded", init);
})();
