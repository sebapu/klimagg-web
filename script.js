// KlimaGG-Web — frontend application logic
// Version: v2.0.2
//
// Vanilla JavaScript with no build step (CSP: script-src 'self').
// The file remains monolithic for deployment simplicity, but responsibilities are
// exposed through window.KlimaGG.* namespaces so they can be split into ES modules later.
//
// Shared authentication state with auth_complete.js uses
// localStorage["klimagg_auth_token_v1"] for the JWT access token.
 
(function () {
  const KLIMAGG_WEB_JS_VERSION = "2.0.2";
  const isAppIndex = (document.body.dataset.klimaggApp === "true") && !!document.getElementById("articles-container");
  const AUTH_STORAGE_KEY = "klimagg_auth_token_v1";
  const AUTH_STATE_CHANGED_KEY = "klimagg_auth_state_changed_v1";

  // Robust: Token kann schon in localStorage liegen, bevor restoreAuthFromStorage()
  // (async) klimaggAuth.accessToken gesetzt hat.
  function getStoredAccessToken() {
    try {
      if (typeof klimaggAuth !== "undefined" && klimaggAuth && klimaggAuth.accessToken) {
        return klimaggAuth.accessToken;
      }
    } catch (_) {}
    try {
      if (window.localStorage) return window.localStorage.getItem(AUTH_STORAGE_KEY) || null;
    } catch (_) {}
    return null;
  }
  const SIGNUP_STORAGE_KEY = "klimagg_signup_token_v1";
  const PERSONAL_MOOD_TOC_STORAGE_KEY = "klimagg_personal_mood_toc_v1";
  const BOOKMARKS_TOC_STORAGE_KEY = "klimagg_bookmarks_toc_v1";
  const GUEST_ARTICLE_VOTES_STORAGE_KEY = "klimagg_guest_article_votes_v1";
  const GUEST_ARTICLE_REACTIONS_STORAGE_KEY = "klimagg_guest_article_reactions_v1";
  const GUEST_COMMENT_VOTES_STORAGE_KEY = "klimagg_guest_comment_votes_v1";
  const GUEST_COMMENT_REACTIONS_STORAGE_KEY = "klimagg_guest_comment_reactions_v1";
	
  // UI preferences (Navigation)
  const MOOD_OVERVIEW_STORAGE_KEY = "klimagg.ui.mood_overview";

  
  let globalMoodCounts = null;
  let globalMoodTotal = 0;
  
  let personalArticleVotes = {};
  
  // Key: "<article_id>:<version_id>" → { article_id, version_id, main_vote }
  let personalArticleVotesByKey = {};
  
  let personalArticleReactions = {};
  
  // Key: "<article_id>:<version_id>" → { article_id, version_id, emojis: [...] }
  let personalArticleReactionsByKey = {};

  // ---------------------------------------------------------------------
  
  // - Votes: ✅ 🟢 🟡 🟠 🔴 (main_vote)
  // - Reactions/Emojis: 🧭 ✍️ 🧩 ⚖️ (toggle)
  
  let personalCommentVotes = {};        // key: "<comment_id>" → {comment_id, main_vote}
  let personalCommentReactions = {};    // key: "<comment_id>" → {comment_id, emojis:[...]}
  let commentVoteSummaryCache = {};     // key: "<comment_id>" → summary
  let guestInteractionImportInFlight = false;

  // Expose for console testers / diagnostics (authoritative source stays the local vars)
  // This prevents "empty reactions" in console tools and makes drift analysis reliable.
  try {
    window.personalCommentVotes = personalCommentVotes;
    window.personalCommentReactions = personalCommentReactions;
  } catch (_) {}

  
  let publicProjectStatsCache = null;
  let publicStatsUiConfig = null;
  const projectSeriesSelection = {
    main: new Set(["✅"]),
    flags: new Set(),
    metrics: new Set(),
  };

  // -------------------------------------------------------------------------
  // Soft-module namespaces. The runtime stays in one file, while public module
  // boundaries live under window.KlimaGG.* for controlled future extraction.
  const KlimaGG = window.KlimaGG || (window.KlimaGG = {});
  KlimaGG.core = KlimaGG.core || {};
  KlimaGG.util = KlimaGG.util || {};
  KlimaGG.api = KlimaGG.api || {};
  KlimaGG.auth = KlimaGG.auth || {};
  KlimaGG.ui = KlimaGG.ui || {};
  KlimaGG.features = KlimaGG.features || {};
  KlimaGG.admin = KlimaGG.admin || {};

  // -------------------------------------------------------------------------
  // Central storage keys must remain inside the IIFE because it executes immediately.
  // -------------------------------------------------------------------------
  const UI_RIGHT_PANEL_STORAGE_KEY = "klimagg.ui.right_panel";
  const UI_LEFT_PANEL_STORAGE_KEY = "klimagg.ui.left_panel";
  const UI_FONT_SIZE_STORAGE_KEY = "klimagg.ui.font_size"; // normal|compact|dense
  
  const UI_ARTICLE_SCOPE_STORAGE_KEY = "klimagg.ui.article_scope"; // toc|core|allall
  // Navigation → Versionen: Diff-Ansicht im Artikeltext
  const UI_VERSION_DIFF_MODE_STORAGE_KEY = "klimagg.ui.version_diff_mode"; // off|next_draft|last_version
  
  const UI_COMMENT_OVERLAY_STORAGE_KEY = "klimagg.ui.comment_overlay"; // on|off
  
  const UI_COMMENTS_STORAGE_KEY = "klimagg.ui.comments"; // on|off
  const UI_COMMENT_BADGES_STORAGE_KEY = "klimagg.ui.comment_badges"; // on|off
  const UI_COMMENTS_SORT_STORAGE_KEY = "klimagg.ui.comments_sort_v1"; // JSON map: "aid:vid" -> mode
  const UI_COMMENT_FOCUS_STORAGE_KEY = "klimagg.ui.comment_focus_v1"; // JSON map: "aid:vid" -> cid
  
  const UI_HOME_FIRST_VISIT_KEY = "klimagg.ui.home_first_visit_done";
  const UI_HOME_SIDEBARS_VISIBLE_KEY = "klimagg.ui.home_sidebars_visible"; // on|off
  const UI_USERBOX_STORAGE_PREFIX = "klimagg.ui.userbox.";
  
  const UI_POST_LOGIN_OPEN_RIGHT_PANEL_KEY = "klimagg.ui.post_login_open_right_panel";

  function safeGetLS(key) {
    try {
      return window.localStorage ? window.localStorage.getItem(key) : null;
    } catch (_) {
      return null;
    }
  }

  function safeSetLS(key, val) {
    try {
      if (window.localStorage) window.localStorage.setItem(key, val);
    } catch (_) {}
  }

  function _safeParseGuestMap(raw) {
    if (!raw) return {};
    try {
      const parsed = JSON.parse(String(raw));
      return (parsed && typeof parsed === "object" && !Array.isArray(parsed)) ? parsed : {};
    } catch (_) {
      return {};
    }
  }

  function _getGuestMap(storageKey) {
    return _safeParseGuestMap(safeGetLS(storageKey));
  }

  function _setGuestMap(storageKey, obj) {
    try {
      const safeObj = (obj && typeof obj === "object" && !Array.isArray(obj)) ? obj : {};
      safeSetLS(storageKey, JSON.stringify(safeObj));
    } catch (_) {
      try { safeSetLS(storageKey, "{}"); } catch (_) {}
    }
  }

  function getGuestArticleVotes() {
    return _getGuestMap(GUEST_ARTICLE_VOTES_STORAGE_KEY);
  }

  function setGuestArticleVoteLocal(articleId, versionId, mainVote) {
    const aid = Number(articleId);
    if (!Number.isFinite(aid)) return null;
    const vote = String(mainVote || "").trim();
    if (!vote) return null;
    const map = getGuestArticleVotes();
    Object.keys(map).forEach((k) => {
      const rec = map[k];
      if (rec && Number(rec.article_id) === aid) delete map[k];
    });
    const v = (versionId == null || versionId === "") ? null : Number(versionId);
    const entry = { article_id: aid, version_id: Number.isFinite(v) ? v : null, main_vote: vote };
    map[String(aid) + ":" + String(entry.version_id == null ? "" : entry.version_id)] = entry;
    _setGuestMap(GUEST_ARTICLE_VOTES_STORAGE_KEY, map);
    return entry;
  }

  function getGuestArticleReactions() {
    return _getGuestMap(GUEST_ARTICLE_REACTIONS_STORAGE_KEY);
  }

  function toggleGuestArticleReactionLocal(articleId, versionId, emoji) {
    const aid = Number(articleId);
    const em = String(emoji || "").trim();
    if (!Number.isFinite(aid) || !em) return null;
    const map = getGuestArticleReactions();
    let prev = [];
    Object.keys(map).forEach((k) => {
      const rec = map[k];
      if (rec && Number(rec.article_id) === aid) {
        if (Array.isArray(rec.emojis)) prev = rec.emojis.map(String);
        delete map[k];
      }
    });
    const set = new Set(prev.map(String).filter(Boolean));
    if (set.has(em)) set.delete(em);
    else set.add(em);
    const v = (versionId == null || versionId === "") ? null : Number(versionId);
    const entry = { article_id: aid, version_id: Number.isFinite(v) ? v : null, emojis: Array.from(set) };
    map[String(aid) + ":" + String(entry.version_id == null ? "" : entry.version_id)] = entry;
    _setGuestMap(GUEST_ARTICLE_REACTIONS_STORAGE_KEY, map);
    return entry;
  }

  function getGuestCommentVotes() {
    return _getGuestMap(GUEST_COMMENT_VOTES_STORAGE_KEY);
  }

  function setGuestCommentVoteLocal(commentId, articleId, versionId, mainVote) {
    const cid = Number(commentId);
    const aid = (articleId == null || articleId === "") ? null : Number(articleId);
    const vid = (versionId == null || versionId === "") ? null : Number(versionId);
    const vote = String(mainVote || "").trim();
    if (!Number.isFinite(cid) || !vote) return null;
    const map = getGuestCommentVotes();
    map[String(cid)] = {
      comment_id: cid,
      article_id: Number.isFinite(aid) ? aid : null,
      version_id: Number.isFinite(vid) ? vid : null,
      main_vote: vote,
    };
    _setGuestMap(GUEST_COMMENT_VOTES_STORAGE_KEY, map);
    return map[String(cid)];
  }

  function getGuestCommentReactions() {
    return _getGuestMap(GUEST_COMMENT_REACTIONS_STORAGE_KEY);
  }

  function toggleGuestCommentReactionLocal(commentId, articleId, versionId, emoji) {
    const cid = Number(commentId);
    const aid = (articleId == null || articleId === "") ? null : Number(articleId);
    const vid = (versionId == null || versionId === "") ? null : Number(versionId);
    const em = String(emoji || "").trim();
    if (!Number.isFinite(cid) || !em) return null;
    const map = getGuestCommentReactions();
    const prev = map[String(cid)] && Array.isArray(map[String(cid)].emojis)
      ? map[String(cid)].emojis.map(String)
      : [];
    const set = new Set(prev.filter(Boolean));
    if (set.has(em)) set.delete(em);
    else set.add(em);
    map[String(cid)] = {
      comment_id: cid,
      article_id: Number.isFinite(aid) ? aid : null,
      version_id: Number.isFinite(vid) ? vid : null,
      emojis: Array.from(set),
    };
    _setGuestMap(GUEST_COMMENT_REACTIONS_STORAGE_KEY, map);
    return map[String(cid)];
  }

  function clearGuestInteractionState() {
    _setGuestMap(GUEST_ARTICLE_VOTES_STORAGE_KEY, {});
    _setGuestMap(GUEST_ARTICLE_REACTIONS_STORAGE_KEY, {});
    _setGuestMap(GUEST_COMMENT_VOTES_STORAGE_KEY, {});
    _setGuestMap(GUEST_COMMENT_REACTIONS_STORAGE_KEY, {});
  }

  async function importGuestInteractionsAfterAuth() {
    if (guestInteractionImportInFlight) return;
    if (!klimaggAuth.user || !klimaggAuth.accessToken) return;

    const gArticleVotes = getGuestArticleVotes();
    const gArticleReactions = getGuestArticleReactions();
    const gCommentVotes = getGuestCommentVotes();
    const gCommentReactions = getGuestCommentReactions();
    const hasGuestData =
      Object.keys(gArticleVotes || {}).length > 0 ||
      Object.keys(gArticleReactions || {}).length > 0 ||
      Object.keys(gCommentVotes || {}).length > 0 ||
      Object.keys(gCommentReactions || {}).length > 0;
    if (!hasGuestData) return;

    guestInteractionImportInFlight = true;
    try {
      const validArticleIds = new Set();
      try {
        const arts = Array.isArray(appState.articles_all)
          ? appState.articles_all
          : (Array.isArray(appState.articles) ? appState.articles : []);
        arts.forEach((a) => {
          const aid = Number(a && a.id);
          if (Number.isFinite(aid)) validArticleIds.add(aid);
        });
      } catch (_) {}
      try {
        document.querySelectorAll('.article-vote-bar[data-article-id]').forEach((el) => {
          const aid = Number(el.getAttribute('data-article-id'));
          if (Number.isFinite(aid)) validArticleIds.add(aid);
        });
      } catch (_) {}

      const validCommentIds = new Set();
      try {
        document.querySelectorAll('.comment-vote-bar[data-comment-id]').forEach((el) => {
          const cid = Number(el.getAttribute('data-comment-id'));
          if (Number.isFinite(cid)) validCommentIds.add(cid);
        });
      } catch (_) {}
      try {
        const byId = window.__klimaggCommentCacheById || {};
        Object.keys(byId).forEach((k) => {
          const cid = Number(k);
          if (Number.isFinite(cid)) validCommentIds.add(cid);
        });
      } catch (_) {}
      try {
        const byAid = window.__klimaggCommentsCacheByArticleId || {};
        Object.keys(byAid).forEach((aid) => {
          const arr = byAid[aid];
          if (!Array.isArray(arr)) return;
          arr.forEach((c) => {
            const cid = Number(c && c.id);
            if (Number.isFinite(cid)) validCommentIds.add(cid);
          });
        });
      } catch (_) {}

      const [aVotesRes, aReactsRes, cVotesRes, cReactsRes] = await Promise.allSettled([
        apiGetJson('/api/me/article-votes'),
        apiGetJson('/api/me/article-reactions'),
        apiGetJson('/api/me/comment-votes'),
        apiGetJson('/api/me/comment-reactions'),
      ]);

      const serverArticleVoteByAid = {};
      const serverArticleReactionsByAid = {};
      const serverCommentVoteByCid = {};
      const serverCommentReactionsByCid = {};

      try {
        const arr = (aVotesRes.status === 'fulfilled' && Array.isArray(aVotesRes.value)) ? aVotesRes.value : [];
        arr.forEach((v) => {
          const aid = Number(v && v.article_id);
          if (!Number.isFinite(aid)) return;
          serverArticleVoteByAid[aid] = String(v.main_vote || '');
        });
      } catch (_) {}
      try {
        const arr = (aReactsRes.status === 'fulfilled' && Array.isArray(aReactsRes.value)) ? aReactsRes.value : [];
        arr.forEach((r) => {
          const aid = Number(r && r.article_id);
          if (!Number.isFinite(aid)) return;
          serverArticleReactionsByAid[aid] = new Set(Array.isArray(r.emojis) ? r.emojis.map(String) : []);
        });
      } catch (_) {}
      try {
        const arr = (cVotesRes.status === 'fulfilled' && Array.isArray(cVotesRes.value)) ? cVotesRes.value : [];
        arr.forEach((v) => {
          const cid = Number(v && v.comment_id);
          if (!Number.isFinite(cid)) return;
          serverCommentVoteByCid[cid] = String(v.main_vote || '');
        });
      } catch (_) {}
      try {
        const arr = (cReactsRes.status === 'fulfilled' && Array.isArray(cReactsRes.value)) ? cReactsRes.value : [];
        arr.forEach((r) => {
          const cid = Number(r && r.comment_id);
          if (!Number.isFinite(cid)) return;
          serverCommentReactionsByCid[cid] = new Set(Array.isArray(r.emojis) ? r.emojis.map(String) : []);
        });
      } catch (_) {}

      const localArticleVoteByAid = {};
      Object.values(gArticleVotes || {}).forEach((rec) => {
        const aid = Number(rec && rec.article_id);
        const vote = String((rec && rec.main_vote) || '').trim();
        if (!Number.isFinite(aid) || !vote) return;
        localArticleVoteByAid[aid] = vote;
      });
      for (const aidRaw of Object.keys(localArticleVoteByAid)) {
        const aid = Number(aidRaw);
        if (!Number.isFinite(aid)) continue;
        if (validArticleIds.size && !validArticleIds.has(aid)) continue;
        const localVote = String(localArticleVoteByAid[aid] || '');
        const serverVote = String(serverArticleVoteByAid[aid] || '');
        if (!localVote || localVote === serverVote) continue;
        try {
          await apiPostJson(`/api/articles/${encodeURIComponent(String(aid))}/vote`, { main_vote: localVote }, { authRequired: true });
        } catch (_) {}
      }

      const localArticleReactsByAid = {};
      Object.values(gArticleReactions || {}).forEach((rec) => {
        const aid = Number(rec && rec.article_id);
        if (!Number.isFinite(aid)) return;
        localArticleReactsByAid[aid] = new Set(Array.isArray(rec.emojis) ? rec.emojis.map(String) : []);
      });
      for (const aidRaw of Object.keys(localArticleReactsByAid)) {
        const aid = Number(aidRaw);
        if (!Number.isFinite(aid)) continue;
        if (validArticleIds.size && !validArticleIds.has(aid)) continue;
        const localSet = localArticleReactsByAid[aid] || new Set();
        const serverSet = serverArticleReactionsByAid[aid] || new Set();
        const all = new Set([...Array.from(localSet), ...Array.from(serverSet)]);
        for (const emoji of Array.from(all)) {
          if (localSet.has(emoji) === serverSet.has(emoji)) continue;
          try {
            await apiPostJson(`/api/articles/${encodeURIComponent(String(aid))}/reactions/toggle`, { emoji: String(emoji) }, { authRequired: true });
          } catch (_) {}
        }
      }

      const localCommentVoteByCid = {};
      Object.values(gCommentVotes || {}).forEach((rec) => {
        const cid = Number(rec && rec.comment_id);
        const vote = String((rec && rec.main_vote) || '').trim();
        if (!Number.isFinite(cid) || !vote) return;
        localCommentVoteByCid[cid] = vote;
      });
      for (const cidRaw of Object.keys(localCommentVoteByCid)) {
        const cid = Number(cidRaw);
        if (!Number.isFinite(cid)) continue;
        if (validCommentIds.size && !validCommentIds.has(cid)) continue;
        const localVote = String(localCommentVoteByCid[cid] || '');
        const serverVote = String(serverCommentVoteByCid[cid] || '');
        if (!localVote || localVote === serverVote) continue;
        try {
          await apiPostJson(`/api/comments/${encodeURIComponent(String(cid))}/vote`, { main_vote: localVote }, { authRequired: true });
        } catch (_) {}
      }

      const localCommentReactsByCid = {};
      Object.values(gCommentReactions || {}).forEach((rec) => {
        const cid = Number(rec && rec.comment_id);
        if (!Number.isFinite(cid)) return;
        localCommentReactsByCid[cid] = new Set(Array.isArray(rec.emojis) ? rec.emojis.map(String) : []);
      });
      for (const cidRaw of Object.keys(localCommentReactsByCid)) {
        const cid = Number(cidRaw);
        if (!Number.isFinite(cid)) continue;
        if (validCommentIds.size && !validCommentIds.has(cid)) continue;
        const localSet = localCommentReactsByCid[cid] || new Set();
        const serverSet = serverCommentReactionsByCid[cid] || new Set();
        const all = new Set([...Array.from(localSet), ...Array.from(serverSet)]);
        for (const emoji of Array.from(all)) {
          if (localSet.has(emoji) === serverSet.has(emoji)) continue;
          try {
            await apiPostJson(`/api/comments/${encodeURIComponent(String(cid))}/reactions/toggle`, { emoji: String(emoji) }, { authRequired: true });
          } catch (_) {}
        }
      }

      clearGuestInteractionState();
      await Promise.allSettled([
        (typeof fetchPersonalArticleVotes === 'function') ? fetchPersonalArticleVotes() : Promise.resolve(),
        (typeof fetchPersonalCommentVotes === 'function') ? fetchPersonalCommentVotes() : Promise.resolve(),
      ]);
      const [aReactsReload, cReactsReload] = await Promise.allSettled([
        apiGetJson('/api/me/article-reactions'),
        apiGetJson('/api/me/comment-reactions'),
      ]);
      if (aReactsReload.status === 'fulfilled') {
        const byKey = {};
        const byArticle = {};
        (Array.isArray(aReactsReload.value) ? aReactsReload.value : []).forEach((r) => {
          const aid = Number(r && r.article_id);
          const vid = Number(r && r.version_id);
          if (!Number.isFinite(aid) || !Number.isFinite(vid)) return;
          const emojis = Array.isArray(r.emojis) ? r.emojis.map(String) : [];
          const key = String(aid) + ':' + String(vid);
          const entry = { article_id: aid, version_id: vid, emojis };
          byKey[key] = entry;
          if (!byArticle[String(aid)]) byArticle[String(aid)] = [];
          byArticle[String(aid)].push(entry);
        });
        const mapped = {};
        const arts = Array.isArray(appState.articles_all)
          ? appState.articles_all
          : (Array.isArray(appState.articles) ? appState.articles : []);
        const seen = new Set();
        if (arts.length) {
          arts.forEach((a) => {
            if (!a || a.id == null) return;
            const aidStr = String(a.id);
            const entries = byArticle[aidStr];
            if (!entries || !entries.length) return;
            seen.add(aidStr);
            const currentVid = a.current_version && a.current_version.id != null ? Number(a.current_version.id) : null;
            let chosen = null;
            if (currentVid) chosen = entries.find((e) => e && Number(e.version_id) === currentVid) || null;
            if (!chosen) {
              chosen = entries.reduce((best, e) => {
                if (!e || e.version_id == null) return best;
                if (!best || best.version_id == null) return e;
                return Number(e.version_id) > Number(best.version_id) ? e : best;
              }, null);
            }
            if (chosen) mapped[aidStr] = chosen;
          });
        }
        Object.keys(byArticle).forEach((aidStr) => {
          if (seen.has(aidStr)) return;
          const entries = byArticle[aidStr];
          if (!entries || !entries.length) return;
          const chosen = entries.reduce((best, e) => {
            if (!e || e.version_id == null) return best;
            if (!best || best.version_id == null) return e;
            return Number(e.version_id) > Number(best.version_id) ? e : best;
          }, null);
          if (chosen) mapped[aidStr] = chosen;
        });
        personalArticleReactionsByKey = byKey;
        personalArticleReactions = mapped;
      }
      if (cReactsReload.status === 'fulfilled') {
        const mapped = {};
        (Array.isArray(cReactsReload.value) ? cReactsReload.value : []).forEach((r) => {
          const cid = Number(r && r.comment_id);
          if (!Number.isFinite(cid)) return;
          mapped[String(cid)] = {
            comment_id: cid,
            emojis: Array.isArray(r.emojis) ? r.emojis.map(String) : [],
          };
        });
        personalCommentReactions = mapped;
        try { window.personalCommentReactions = personalCommentReactions; } catch (_) {}
      }
      try { applyPersonalVotesToRenderedArticles(); } catch (_) {}
      try { applyPersonalVotesToRenderedComments(); } catch (_) {}
      try { await reapplyCommentFlagOverlaysForLoadedArticles({ silent: true }); } catch (_) {}
    } finally {
      guestInteractionImportInFlight = false;
    }
  }


  
  
  
  function cssEscape(value) {
    const s = String(value ?? "");
    try {
      if (window.CSS && typeof window.CSS.escape === "function") return window.CSS.escape(s);
    } catch (_) {}
    // Minimal fallback for backslashes, quotes, and line breaks.
    return s
      .replace(/\\/g, "\\\\")
      .replace(/\"/g, "\\\"")
      .replace(/\n/g, "\\A ")
      .replace(/\r/g, "\\D ")
      .replace(/\f/g, "\\C ");
  }

  
  
  
  
  function ensureHomeSidebarsVisible() {
    try {
      const body = document.body;
      if (!body) return;
      if (body.getAttribute("data-page") !== "home") return;

      
      
      
      body.classList.add("sidebars-manual-on");
      safeSetLS(UI_HOME_SIDEBARS_VISIBLE_KEY, "on");

      const tocSidebar =
        document.querySelector("nav.toc.app-sidebar") || document.querySelector("nav.toc");
      const userSidebar = document.getElementById("user-panel");
      if (tocSidebar) tocSidebar.hidden = false;
      if (userSidebar) userSidebar.hidden = false;
    } catch (_) {}
  }

  // -------------------------------------------------------------------------
  
  
  
  
  // -------------------------------------------------------------------------

  function getStickyHeaderHeightPx() {
    const header = document.querySelector(".site-header");
    if (!header) return 0;
    const rect = header.getBoundingClientRect();
    const h = rect && rect.height ? Math.ceil(rect.height) : 0;
    return h;
  }

  function syncStickyHeaderOffsetVar() {
    
    const h = getStickyHeaderHeightPx();
    if (!h) return;
    const clamped = Math.max(56, Math.min(160, h));
    document.documentElement.style.setProperty("--sticky-header-offset", clamped + "px");
  }

  function scrollToElementWithHeaderOffset(el, { behavior = "smooth", extraGapPx = 14 } = {}) {
    if (!el) return false;
    const headerH = getStickyHeaderHeightPx() || 0;
    const offset = Math.max(0, headerH + (extraGapPx || 0));
    const y = el.getBoundingClientRect().top + window.pageYOffset - offset;
    window.scrollTo({ top: Math.max(0, y), behavior });
    return true;
  }

  function scrollToHashWithOffset(hash, { behavior = "smooth", extraGapPx = 14 } = {}) {
    if (!hash) return false;
    const raw = String(hash);
    if (!raw.startsWith("#") || raw.length < 2) return false;
    let id = raw.slice(1);
    try { id = decodeURIComponent(id); } catch (_) {}
    const target = document.getElementById(id);
    if (!target) return false;
    return scrollToElementWithHeaderOffset(target, { behavior, extraGapPx });
  }

  
  async function _kggSha256HexText(s) {
    try {
      const txt = String(s || "");
      if (!window.crypto || !window.crypto.subtle || typeof TextEncoder === "undefined") return "";
      const enc = new TextEncoder();
      const buf = enc.encode(txt);
      const dig = await window.crypto.subtle.digest("SHA-256", buf);
      const arr = Array.from(new Uint8Array(dig));
      return arr.map((b) => b.toString(16).padStart(2, "0")).join("");
    } catch (_) {
      return "";
    }
  }

  function _kggComputeAnchorFromOldNew(oldText, newText) {
    
    
    const o = String(oldText || "");
    const n = String(newText || "");
    let i = 0;
    const minLen = Math.min(o.length, n.length);
    while (i < minLen && o[i] === n[i]) i++;
    let j = 0;
    while (
      j < (minLen - i) &&
      o[o.length - 1 - j] === n[n.length - 1 - j]
    ) j++;

    const oMid = o.slice(i, o.length - j);
    const nMid = n.slice(i, n.length - j);

    const ctxWin = 80; 
    const before = o.slice(Math.max(0, i - ctxWin), i);
    const after = o.slice(o.length - j, Math.min(o.length, o.length - j + ctxWin));

    
    
    const selected = oMid && oMid.trim() ? oMid : "";

    return {
      mode: "auto_prefix_suffix",
      selected_text: selected,
      context_before: before,
      context_after: after,
      old_mid_len: oMid.length,
      new_mid_len: nMid.length,
    };
  }

  function _kggBuildExactSelectionPart(partId, blockKey, oldText, newText, baselineHash) {
    const o = String(oldText || "");
    const n = String(newText || "");
    let i = 0;
    const minLen = Math.min(o.length, n.length);
    while (i < minLen && o[i] === n[i]) i++;
    let j = 0;
    while (
      j < (minLen - i) &&
      o[o.length - 1 - j] === n[n.length - 1 - j]
    ) j++;

    const selStart = i;
    const selEnd = o.length - j;
    const oldSlice = o.slice(selStart, selEnd);
    const newSlice = n.slice(selStart, n.length - j);
    const anchor = _kggComputeAnchorFromOldNew(o, n);

    return {
      part_id: String(partId || ""),
      block_key: String(blockKey || ""),
      old_text: oldSlice,
      new_text: newSlice,
      sel_start: selStart,
      sel_end: selEnd,
      baseline_hash: String(baselineHash || ""),
      anchor: anchor,
    };
  }

  
  
  let __kggPendingHashTimer = 0;
  function scheduleScrollToHashWithOffset(hash, { behavior = "smooth", extraGapPx = 14 } = {}) {
    try {
      if (__kggPendingHashTimer) window.clearTimeout(__kggPendingHashTimer);
    } catch (_) {}

    let tries = 0;
    const tick = () => {
      tries += 1;
      try { syncStickyHeaderOffsetVar(); } catch (_) {}
      const ok = scrollToHashWithOffset(hash, { behavior, extraGapPx });
      if (ok) return;
      if (tries >= 25) return; // ~3s
      __kggPendingHashTimer = window.setTimeout(tick, 120);
    };

    __kggPendingHashTimer = window.setTimeout(tick, 60);
  }


  function isLoginHash(h) {
    const s = String(h || "").trim().toLowerCase();
    return s === "#login" || s === "#anmelden";
  }

  function triggerLoginCTA({ behavior = "smooth" } = {}) {
    
    if (!isAppIndex) {
      try { window.location.href = "/#login"; } catch (_) {}
      return true;
    }

    
    
    openRightPanelForLogin({ focus: true });
    startLoginFlow();
    return true;
  }

  function hookAnchorOffsetNavigation() {
    // 1) CSS-Var synchronisieren (initial + resize)
    syncStickyHeaderOffsetVar();
    let resizeT = 0;
    window.addEventListener("resize", () => {
      if (resizeT) window.clearTimeout(resizeT);
      resizeT = window.setTimeout(() => syncStickyHeaderOffsetVar(), 120);
    });

    
    document.addEventListener(
      "click",
      (ev) => {
        if (ev.defaultPrevented) return;
        if (ev.button !== 0) return;
        if (ev.metaKey || ev.ctrlKey || ev.shiftKey || ev.altKey) return;
        const a = ev.target && ev.target.closest ? ev.target.closest('a[href^="#"]') : null;
        if (!a) return;
        const href = a.getAttribute("href") || "";
        if (!href || href === "#") return;
		
        
        if (isLoginHash(href)) {
          ev.preventDefault();
          try { history.pushState(null, "", href); } catch (_) {}
          triggerLoginCTA({ behavior: "smooth" });
          return;
        }

        const did = scrollToHashWithOffset(href, { behavior: "smooth", extraGapPx: 14 });
        ev.preventDefault();
        try { history.pushState(null, "", href); } catch (_) {}
        if (!did) {
          
          scheduleScrollToHashWithOffset(href, { behavior: "smooth", extraGapPx: 14 });
        }
      },
      true
    );

    
    if (window.location && window.location.hash) {
      window.requestAnimationFrame(() => {
        syncStickyHeaderOffsetVar();
        if (isLoginHash(window.location.hash)) {
          triggerLoginCTA({ behavior: "auto" });
          return;
        }
        if (!scrollToHashWithOffset(window.location.hash, { behavior: "auto", extraGapPx: 14 })) {
          scheduleScrollToHashWithOffset(window.location.hash, { behavior: "auto", extraGapPx: 14 });
        }
      });
    }

    
    function onHashNav() {
      syncStickyHeaderOffsetVar();
      if (isLoginHash(window.location.hash)) {
        triggerLoginCTA({ behavior: "auto" });
        return;
      }
      if (!scrollToHashWithOffset(window.location.hash, { behavior: "auto", extraGapPx: 14 })) {
        scheduleScrollToHashWithOffset(window.location.hash, { behavior: "auto", extraGapPx: 14 });
      }
    }
    window.addEventListener("hashchange", onHashNav);
    window.addEventListener("popstate", onHashNav);
  }

  
  const LEFT_SIDEBAR_MIN = 160;
  const LEFT_SIDEBAR_MAX = 480;
  const RIGHT_SIDEBAR_MIN = 200;
  const RIGHT_SIDEBAR_MAX = 1040;

  // Persistente Sidebar-Breiten (localStorage)
  const UI_LEFT_SIDEBAR_WIDTH_KEY = "klimagg.ui.sidebar_width_left";
  const UI_RIGHT_SIDEBAR_WIDTH_KEY = "klimagg.ui.sidebar_width_right";

  
  const votingRefreshIds = new Set();

  const klimaggAuth = {
      accessToken: null,
      user: null,
    };  

  
  const appState = {
    
    articles_all: null,
    articles: null,
  };

  // -------------------------------------------------------------------------
  // Central refresh cascade; server state remains authoritative.
  // -------------------------------------------------------------------------
  const __refreshCascade = {
    inFlight: null,
    pending: false,
    pendingOpts: {
      reasons: new Set(),
      articleIds: new Set(),
      wantAuthed: false,
      wantPublic: false,
    },
  };

  function requestRefreshCascade(
    reason,
    { articleId = null, wantPublic = false, wantAuthed = false } = {}
  ) {
    const r = String(reason || "unknown");

    
    if (r === "login" || r === "logout" || r === "auth") {
      wantAuthed = true;
    }
    if (r === "vote") {
      wantAuthed = true;
    }
    if (r === "reaction") {
      wantAuthed = true;
    }
    if (r === "comment" || r === "review") {
      wantAuthed = true;
    }

    
    
    if (r.startsWith("comment")) {
      wantAuthed = true;
    }

    __refreshCascade.pendingOpts.reasons.add(r);
    if (wantPublic) __refreshCascade.pendingOpts.wantPublic = true;
    if (wantAuthed) __refreshCascade.pendingOpts.wantAuthed = true;
    if (articleId != null && !Number.isNaN(Number(articleId))) {
      __refreshCascade.pendingOpts.articleIds.add(Number(articleId));
    }

    if (__refreshCascade.inFlight) {
      __refreshCascade.pending = true;
      return __refreshCascade.inFlight;
    }

    __refreshCascade.inFlight = (async () => {
      // Coalesce same-tick triggers
      await Promise.resolve();

      while (true) {
        __refreshCascade.pending = false;

        const snapshot = __refreshCascade.pendingOpts;
        const reasons = Array.from(snapshot.reasons);
        const articleIds = Array.from(snapshot.articleIds);
        const wantAuthedNow = !!snapshot.wantAuthed;
        const wantPublicNow = !!snapshot.wantPublic;

        // reset for next loop
        __refreshCascade.pendingOpts = {
          reasons: new Set(),
          articleIds: new Set(),
          wantAuthed: false,
          wantPublic: false,
        };

        const wantsVotes = reasons.includes("vote") || reasons.includes("login");
        const wantsComments = reasons.some((x) => (x === "review") || String(x || "").startsWith("comment"));

        try {
          // Authed user data (Server = Truth)
          if (wantAuthedNow && klimaggAuth.user && klimaggAuth.accessToken) {
            try {
              await fetchPersonalArticleVotes();
            } catch (_) {}
            try { await fetchPersonalCommentVotes(); } catch (_) {}
            try {
              await loadUserReviewStats();
            } catch (_) {}
            try {
              if (typeof window.klimaggRefreshMyCommentsOverview === "function") {
                await window.klimaggRefreshMyCommentsOverview({ force: false, silent: true });
              }
            } catch (_) {}
          } else {
            // Ensure UI resets cleanly in anon state
            try {
              await fetchPersonalArticleVotes();
            } catch (_) {}
            try {
              await loadUserReviewStats();
            } catch (_) {}
            try {
              if (typeof window.klimaggRefreshMyCommentsOverview === "function") {
                await window.klimaggRefreshMyCommentsOverview({ force: true, silent: true });
              }
            } catch (_) {}
          }

          
          try {
            if (typeof window.klimaggUpdateCommentPanelButtons === "function") {
              window.klimaggUpdateCommentPanelButtons();
            }
          } catch (_) {}

          
          if (articleIds.length) {
            if (wantsVotes) {
              for (const aid of articleIds) {
                try {
                  await refreshVoteSummary(aid);
                } catch (_) {}
              }
            }
            if (wantsComments) {
              for (const aid of articleIds) {
                try {
                  refreshCommentsForArticle(aid);
                } catch (_) {}
              }
            }
          }
        } catch (err) {
          console.warn("[KlimaGG] Refresh-Kaskade fehlgeschlagen:", err, {
            reasons,
            articleIds,
          });
        }

        if (!__refreshCascade.pending) break;
      }
    })().finally(() => {
      __refreshCascade.inFlight = null;
    });

    return __refreshCascade.inFlight;
  }


  // -------------------------------------------------------------------------
  // Inline-Notices / Toasts (statt alert())
  // -------------------------------------------------------------------------

  function ensureToastRoot() {
    let root = document.getElementById("klimagg-toast-root");
    if (root) return root;
    root = document.createElement("div");
    root.id = "klimagg-toast-root";
    root.className = "klimagg-toast-root";
    root.setAttribute("aria-live", "polite");
    root.setAttribute("aria-atomic", "true");
    document.body.appendChild(root);
    return root;
  }

  function notify(msg, { type = "info", timeoutMs = 4500 } = {}) {
    if (!msg) return;
    const root = ensureToastRoot();
    const el = document.createElement("div");
    el.className = "klimagg-toast klimagg-toast-" + String(type || "info");
    el.setAttribute("role", type === "error" ? "alert" : "status");
    el.textContent = String(msg);
    root.appendChild(el);
    window.setTimeout(() => {
      try {
        el.classList.add("is-hiding");
        window.setTimeout(() => el.remove(), 220);
      } catch (_) {}
    }, Math.max(1200, timeoutMs || 4500));
  }

  function setStatusText(elOrId, msg) {
    const el =
      typeof elOrId === "string" ? document.getElementById(elOrId) : elOrId;
    if (!el) return;
    el.textContent = msg ? String(msg) : "";
  }

  // -------------------------------------------------------------------------
  // Unified async states (F22): Loading / Empty / Error / Retry
  // -------------------------------------------------------------------------

  function renderAsyncState(
    targetEl,
    {
      kind = "loading",
      message = "",
      onRetry = null,
      retryLabel = "Erneut versuchen",
    } = {}
  ) {
    const el =
      typeof targetEl === "string" ? document.getElementById(targetEl) : targetEl;
    if (!el) return null;

    
    el.innerHTML = "";

    const wrap = document.createElement("div");
    wrap.className = "klimagg-state klimagg-state-" + String(kind || "loading");
    wrap.setAttribute("role", kind === "error" ? "alert" : "status");
    wrap.setAttribute("aria-live", "polite");

    const icon = document.createElement("div");
    icon.className = "klimagg-state-icon";
    icon.textContent =
      kind === "loading" ? "⏳" : kind === "error" ? "⚠️" : "—";

    const body = document.createElement("div");
    body.className = "klimagg-state-body";

    const msg = document.createElement("div");
    msg.className = "klimagg-state-msg klein";
    msg.textContent =
      message ||
      (kind === "loading"
        ? "Lade …"
        : kind === "error"
        ? "Fehler"
        : "Keine Daten");

    body.appendChild(msg);

    if (typeof onRetry === "function") {
      const actions = document.createElement("div");
      actions.className = "klimagg-state-actions";

      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "btn btn-ghost btn-sm";
      btn.textContent = String(retryLabel || "Erneut versuchen");
      btn.addEventListener("click", (ev) => {
        ev.preventDefault();
        try {
          onRetry();
        } catch (e) {
          console.error("[KlimaGG] Retry handler error:", e);
        }
      });

      actions.appendChild(btn);
      body.appendChild(actions);
    }

    wrap.appendChild(icon);
    wrap.appendChild(body);
    el.appendChild(wrap);
    return wrap;
  }

  function signalAuthStateChanged(reason) {
    try {
      if (window.localStorage) {
        window.localStorage.setItem(
          AUTH_STATE_CHANGED_KEY,
          String(Date.now()) + ":" + String(reason || "auth_state")
        );
      }
    } catch (_) {}
  }

  function logoutLocal({ reason = "", broadcast = true } = {}) {
    klimaggAuth.accessToken = null;
    klimaggAuth.user = null;
    persistAuth({ broadcast });
    updateUserUI();
    if (reason) notify(reason, { type: "warn" });
  }

  // -------------------------------------------------------------------------
  // Shared JSON API wrapper: timeout handling, structured errors, and one 401 refresh/retry.
  // -------------------------------------------------------------------------

  class ApiError extends Error {
    constructor(message, { status = 0, url = "", payload = null } = {}) {
      super(message);
      this.name = "ApiError";
      this.status = status;
      this.url = url;
      this.payload = payload;
    }
  }

    


    async function apiFetchJson(
      url,
      options = {},
      {
        authRequired = false,
        retryAuth = true,
        timeoutMs = 20000,
        authToken = null,
      } = {}
    ) {
      const headers = options.headers ? { ...options.headers } : {};

      const token = authToken || klimaggAuth.accessToken;
      if (authRequired && token) {
        headers["Authorization"] = "Bearer " + token;
      }
  

      const ctrl = typeof AbortController !== "undefined" ? new AbortController() : null;
      const t = ctrl
        ? window.setTimeout(() => {
            try { ctrl.abort(); } catch (_) {}
          }, timeoutMs)
        : 0;

      let resp;
      try {
        resp = await fetch(url, { ...options, headers, signal: ctrl ? ctrl.signal : undefined });
      } catch (err) {
        if (t) window.clearTimeout(t);
        const msg =
          err && String(err.name) === "AbortError"
            ? "Zeitüberschreitung – bitte versuche es erneut."
            : "Netzwerkfehler – bitte prüfe deine Verbindung.";
        throw new ApiError(msg, { status: 0, url });
      }
      if (t) window.clearTimeout(t);

      let data = null;
      const ct = (resp.headers && resp.headers.get && resp.headers.get("content-type")) || "";
      const wantsJson = ct.includes("application/json") || ct.includes("+json");
      if (resp.status !== 204) {
        if (wantsJson) {
          try {
            data = await resp.json();
          } catch (_) {
            data = null;
          }
        } else {
          try {
            data = await resp.text();
          } catch (_) {
            data = null;
          }
        }
      }

      
      if (
        resp.status === 401 &&
        authRequired &&
        retryAuth &&
        klimaggAuth.accessToken &&
        String(url).indexOf("/api/auth/refresh") === -1
      ) {
        const refreshed = await refreshAccessToken({ silent: true });
        if (refreshed && refreshed.access_token) {
          return apiFetchJson(url, options, {
            authRequired,
            retryAuth: false,
            timeoutMs,
            authToken: refreshed.access_token,
          });
        }
      }

      if (!resp.ok) {
        const detail =
          data && typeof data === "object" && data.detail
            ? String(data.detail)
            : typeof data === "string" && data.trim()
            ? data.trim().slice(0, 200)
            : "HTTP " + resp.status;
        throw new ApiError(detail, { status: resp.status, url, payload: data });
      }

      return data;
    }


    // -----------------------------------------------------------------------
    // Convenience Wrapper (D1.2)
    // - Einige UI-Module erwarten apiGetJson/apiPostJson.
    
    
    
    // -----------------------------------------------------------------------

    async function apiGetJson(url, { authRequired = null, timeoutMs = 20000 } = {}) {
      const needAuth = (authRequired == null) ? !!klimaggAuth.accessToken : !!authRequired;
      return apiFetchJson(
        url,
        { method: "GET" },
        { authRequired: needAuth, retryAuth: true, timeoutMs: Number(timeoutMs) || 20000 }
      );
    }

    async function apiPostJson(
      url,
      body,
      { authRequired = null, timeoutMs = 20000, method = "POST" } = {}
    ) {
      const needAuth = (authRequired == null) ? !!klimaggAuth.accessToken : !!authRequired;
      let payload = null;
      if (body != null) {
        payload = (typeof body === "string") ? body : JSON.stringify(body);
      }
      return apiFetchJson(
        url,
        {
          method: String(method || "POST").toUpperCase(),
          headers: { "Content-Type": "application/json" },
          body: payload,
        },
        { authRequired: needAuth, retryAuth: true, timeoutMs: Number(timeoutMs) || 20000 }
      );
    }

    


    async function refreshAccessToken({ silent = false } = {}) {
      if (!klimaggAuth.accessToken) return null;
  
      try {
        const data = await apiFetchJson(
          "/api/auth/refresh",
          { method: "POST" },
          { authRequired: true, retryAuth: false, timeoutMs: 15000 }
        );
  
        if (data && data.access_token && data.user) {
          klimaggAuth.accessToken = data.access_token;
          klimaggAuth.user = data.user;
          persistAuth({ reason: "refresh" });
          updateUserUI();
          return data;
        }
  
        console.warn(
          "[KlimaGG] /api/auth/refresh lieferte unerwartete Daten:",
          data
        );
        return null;
      } catch (err) {
        console.warn("[KlimaGG] /api/auth/refresh fehlgeschlagen:", err);
        logoutLocal({
          reason: silent
            ? ""
            : "Deine Sitzung ist abgelaufen – bitte melde dich erneut an.",
        });
        return null;
      }
    }
  
  const commentState = {
    currentArticleId: null,
    currentVersionId: null,
    currentCommentId: null,

    
    currentCommentStatus: "entwurf",

    // UI-Mode im Nutzerbereich (Kommentarbox): "overview" | "editor"
    uiMode: "overview",
    
    myCommentsCache: null,
    myCommentsFetchedAt: 0,
    
    baseMiniMd: null,
    baseMiniMdKey: null,
  };



  // -------------------------------------------------------------------------
  // mini-md Writer UX (Phase 2)
  
  
  // - Marker-Highlight = reservierte Marker hervorheben
  
  // -------------------------------------------------------------------------
  KlimaGG.minimd = KlimaGG.minimd || {};

  (function (ns) {
    const UI = {
      mounted: false,
      baseText: null,
      baseKey: null,
      textarea: null,
      toolbar: null,
      diffBox: null,
      previewBox: null,
      timer: null,
      resizeObserver: null,
      
      handlers: {
        onInput: null,
        onScrollEditor: null,
        onScrollDiff: null,
        onChangeDiff: null,
        onChangeHighlight: null,
        onChangeSync: null,
      },
      _observedTextarea: null,
    };

    const LS_DIFF_KEY = "klimagg.minimd.diff";
    const LS_SYNC_KEY = "klimagg_minimd_sync_scroll";
    const MINIMD_DEFAULT_EDITOR_MIN_HEIGHT = "20rem";

    function esc(s) {
      return String(s ?? "").replace(/[&<>\"']/g, (c) => ({
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        '"': "&quot;",
        "'": "&#39;",
      }[c]));
    }

    function tokenizeWords(line) {
      const s = String(line ?? "");
      const m = s.match(/(\s+|[^\s]+)/g);
      return m && m.length ? m : [""];
    }

    function isMarkerLine(line) {
      const s = String(line ?? "");
      return /^\s*###\s*(start|end)\s*:\s*[^#\n]+###\s*$/.test(s)
        || /^\s*###\s*(Artikel-Titel|Artikel-ID)\s*:\s*[^#\n]+###\s*$/.test(s);
    }

    function normalize(text) {
      const t = String(text ?? "").replace(/\r\n/g, "\n").replace(/\r/g, "\n");
      // trailing newline stabilisiert Diff-Ergebnis
      return t.endsWith("\n") ? t : (t + "\n");
    }

    function highlightReserved(text) {
      // Reservierte Marker-Zeilen:
      // ### start/end: ... ###
      
      return esc(text).replace(
        /(^|\n)(\s*###\s*(?:start|end|Artikel-Titel|Artikel-ID)\s*:\s*[^#\n]+###)(?=\n|$)/g,
        (m, pre, mark) => pre + '<span class="minimd-marker">' + mark + "</span>"
      );
    }

    
    function myersTrace(aLines, bLines) {
      const N = aLines.length;
      const M = bLines.length;
      const max = N + M;
      const v = new Map();
      v.set(1, 0);
      const trace = [];

      for (let d = 0; d <= max; d++) {
        const v2 = new Map();
        for (let k = -d; k <= d; k += 2) {
          let x;
          if (k === -d || (k !== d && (v.get(k - 1) ?? -1) < (v.get(k + 1) ?? -1))) {
            x = v.get(k + 1) ?? 0;
          } else {
            x = (v.get(k - 1) ?? 0) + 1;
          }
          let y = x - k;
          while (x < N && y < M && aLines[x] === bLines[y]) {
            x++; y++;
          }
          v2.set(k, x);
          if (x >= N && y >= M) {
            trace.push(v2);
            return trace;
          }
        }
        trace.push(v2);
        v.clear();
        for (const [kk, vv] of v2.entries()) v.set(kk, vv);
      }
      return trace;
    }

    function myersBacktrack(trace, aLines, bLines) {
      // Myers backtracking must derive prevK/prevX from trace[d - 1], not trace[d].
      let x = aLines.length;
      let y = bLines.length;
      const out = [];

      for (let d = trace.length - 1; d > 0; d--) {
        const vPrev = trace[d - 1];
        const k = x - y;

        let prevK;
        if (
          k === -d ||
          (k !== d && (vPrev.get(k - 1) ?? -1) < (vPrev.get(k + 1) ?? -1))
        ) {
          prevK = k + 1;
        } else {
          prevK = k - 1;
        }

        const prevX = vPrev.get(prevK) ?? 0;
        const prevY = prevX - prevK;

        // diagonal: eq
        while (x > prevX && y > prevY) {
          out.push({ t: "eq", v: aLines[x - 1] });
          x--; y--;
        }

        // move: ins/del
        if (x === prevX) {
          out.push({ t: "ins", v: bLines[y - 1] });
          y--;
        } else {
          out.push({ t: "del", v: aLines[x - 1] });
          x--;
        }
      }

      
      while (x > 0 && y > 0) {
        out.push({ t: "eq", v: aLines[x - 1] });
        x--; y--;
      }
      while (x > 0) {
        out.push({ t: "del", v: aLines[x - 1] });
        x--;
      }
      while (y > 0) {
        out.push({ t: "ins", v: bLines[y - 1] });
        y--;
      }

      return out.reverse();
    }

    // "Rich"-Inlineformatierung (Reader-UX):
    // - minimal: **bold**, `code`, Links [t](https://..)
    
    function _renderInlineRich(escapedHtml) {
      let s = String(escapedHtml || "");
      // **bold**
      s = s.replace(/\*\*([^*][\s\S]*?)\*\*/g, "<strong>$1</strong>");
      // `code`
      s = s.replace(/`([^`]+)`/g, "<code>$1</code>");
      // [text](https://...)
      s = s.replace(/\[([^\]]+)\]\((https?:\/\/[^)]+)\)/g, '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>');
      return s;
    }

    function renderLine(line, { highlight = true, stripMarkers = false, rich = false } = {}) {
      const raw = String(line ?? "");
      if (stripMarkers && isMarkerLine(raw)) return "";

      // Liste erkennen (Indent + Marker entfernen, Bullet separat rendern)
      if (rich) {
        const m = raw.match(/^(\s*)([-*]|\d+\.)\s+(.*)$/);
        if (m) {
          const indentWs = (m[1] || "").replace(/\t/g, "    ");
          const indent = Math.max(0, Math.min(3, Math.floor(indentWs.length / 2)));
          const bullet = m[2] && /^\d+\./.test(m[2]) ? esc(m[2]) : "•";
          const restRaw = m[3] || "";
          let rest = highlight ? highlightReserved(restRaw) : esc(restRaw);
          rest = _renderInlineRich(rest);
          return '<span class="minimd-li" data-indent="' + String(indent) + '">' +
            '<span class="minimd-li-bullet">' + bullet + '</span>' +
            '<span class="minimd-li-text">' + rest + '</span>' +
            "</span>";
        }
      }

      let out = highlight ? highlightReserved(raw) : esc(raw);
      if (rich) out = _renderInlineRich(out);
      return out;
    }

    function diffReplaceLineToHtml(oldLine, newLine, { highlight = true } = {}) {
      
      const stripMarkers = !!(arguments[2] && arguments[2].stripMarkers);
      const rich = !!(arguments[2] && arguments[2].rich);
      if (stripMarkers && (isMarkerLine(oldLine) || isMarkerLine(newLine))) {
        let html = "";
        if (!isMarkerLine(oldLine)) html += '<span class="minimd-del">' + renderLine(oldLine, { highlight, stripMarkers, rich }) + "</span>\n";
        if (!isMarkerLine(newLine)) html += '<span class="minimd-ins">' + renderLine(newLine, { highlight, stripMarkers, rich }) + "</span>\n";
        return html;
      }
      if (isMarkerLine(oldLine) || isMarkerLine(newLine)) {
        return (
          '<span class="minimd-del">' + renderLine(oldLine, { highlight, stripMarkers, rich }) + "</span>\n" +
          '<span class="minimd-ins">' + renderLine(newLine, { highlight, stripMarkers, rich }) + "</span>\n"
        );
      }

      const aT = tokenizeWords(oldLine);
      const bT = tokenizeWords(newLine);
      const trace = myersTrace(aT, bT);
      const ops = myersBacktrack(trace, aT, bT);

      let html = "";
      for (const op of ops) {
        const tok = op.v ?? "";
        const rendered = esc(tok);
        if (op.t === "eq") {
          html += rendered;
        } else if (op.t === "ins") {
          html += '<span class="minimd-ins">' + rendered + "</span>";
        } else if (op.t === "del") {
          html += '<span class="minimd-del">' + rendered + "</span>";
        }
      }
      return html + "\n";
    }



    function splitLines(text) {
      const t = normalize(text);
      const parts = t.split("\n");
      
      if (parts.length && parts[parts.length - 1] === "") parts.pop();
      return parts;
    }

    function diffToHtml(oldText, newText, { highlight = true, stripMarkers = false, rich = false } = {}) {
      const aL = splitLines(oldText);
      const bL = splitLines(newText);

      const trace = myersTrace(aL, bL);
      const ops = myersBacktrack(trace, aL, bL);

      let html = "";
      for (let i = 0; i < ops.length; i++) {
        const op = ops[i];

        if (op.t === "eq") {
          const r = renderLine(op.v, { highlight, stripMarkers, rich });
          if (r) html += r + "\n";
          continue;
        }

        // Replace-Erkennung in BEIDEN Richtungen:
        // - del* -> ins* (klassisch)
        
        if (op.t === "del" || op.t === "ins") {
          const firstType = op.t;
          const secondType = firstType === "del" ? "ins" : "del";

          const first = [];
          let j = i;
          while (j < ops.length && ops[j].t === firstType) {
            first.push(ops[j].v ?? "");
            j++;
          }

          const second = [];
          let k = j;
          while (k < ops.length && ops[k].t === secondType) {
            second.push(ops[k].v ?? "");
            k++;
          }

          if (second.length) {
            const dels = firstType === "del" ? first : second;
            const ins = firstType === "del" ? second : first;

            const pair = Math.min(dels.length, ins.length);
            for (let p = 0; p < pair; p++) {
              html += diffReplaceLineToHtml(dels[p], ins[p], { highlight, stripMarkers, rich });
            }
            for (let p = pair; p < dels.length; p++) {
              const r = renderLine(dels[p], { highlight, stripMarkers, rich }); if (r) html += '<span class="minimd-del">' + r + "</span>\n";
            }
            for (let p = pair; p < ins.length; p++) {
              const r = renderLine(ins[p], { highlight, stripMarkers, rich }); if (r) html += '<span class="minimd-ins">' + r + "</span>\n";
            }

            i = k - 1;
            continue;
          }

          
          const cls = firstType === "del" ? "minimd-del" : "minimd-ins";
          for (const line of first) {
            const r = renderLine(line, { highlight, stripMarkers, rich }); if (r) html += '<span class="' + cls + '">' + r + "</span>\n";
          }
          i = j - 1;
          continue;
        }
      }

      return html;
    }

    function mountUI(textarea) {
      if (!textarea) return;

      
      if (!UI.mounted || !UI.toolbar || !UI.diffBox || !UI.previewBox) {
        UI.mounted = true;

        const toolbar = document.createElement("div");
        toolbar.className = "minimd-toolbar";
        toolbar.innerHTML = `
          <label class="klein" title="Zeigt Unterschiede zum Bestand (grün = neu, rot = entfernt)." style="display:flex; gap:0.4rem; align-items:center;">
            <input type="checkbox" id="minimd-diff-toggle" checked />
            <span>Diff-View</span>
          </label>
          <label class="klein" title="Synchronisiert Scroll-Position und Höhe zwischen Editor und Diff-View." style="display:flex; gap:0.4rem; align-items:center;">
            <input type="checkbox" id="minimd-sync-toggle" checked />
            <span>Diff-View synchronisieren</span>
          </label>
          <label class="klein" title="Hebt reservierte Marker (### start/end ###) im Text hervor." style="display:flex; gap:0.4rem; align-items:center;">
            <input type="checkbox" id="minimd-highlight-toggle" checked />
            <span>Marker hervorheben</span>
          </label>
          <span class="klein" id="minimd-status" style="opacity:0.85;"></span>
        `;

        const diffBox = document.createElement("div");
        diffBox.id = "minimd-diff-box";
        diffBox.className = "minimd-diff";
        diffBox.style.display = "none";
        diffBox.textContent = "Diff …";

        const previewBox = document.createElement("div");
        previewBox.id = "minimd-preview";
        previewBox.className = "minimd-preview";
        previewBox.style.display = "none";

        UI.toolbar = toolbar;
        UI.diffBox = diffBox;
        UI.previewBox = previewBox;
      }

      // 2) UI-Elemente unter dem AKTUELLEN Textarea platzieren
      try {
        textarea.parentNode.insertBefore(UI.toolbar, textarea.nextSibling);
        textarea.parentNode.insertBefore(UI.diffBox, UI.toolbar.nextSibling);
        textarea.parentNode.insertBefore(UI.previewBox, UI.diffBox.nextSibling);
      } catch (_) {}

      
      const toolbar = UI.toolbar;
      const diffBox = UI.diffBox;
      const previewBox = UI.previewBox;
      const cbDiff = toolbar.querySelector("#minimd-diff-toggle");
      const cbSync = toolbar.querySelector("#minimd-sync-toggle");
      const cbHi = toolbar.querySelector("#minimd-highlight-toggle");
      const status = toolbar.querySelector("#minimd-status");

      function setStatus(t) { if (status) status.textContent = t || ""; }

      try {
        if (textarea) {
          if (!textarea.style.minHeight || String(textarea.style.minHeight).trim() === "") {
            textarea.style.minHeight = MINIMD_DEFAULT_EDITOR_MIN_HEIGHT;
          }
          if (!textarea.rows || Number(textarea.rows) < 16) {
            textarea.rows = 16;
          }
        }
      } catch (_) {}

      try { if (cbDiff) cbDiff.checked = (safeGetLS(LS_DIFF_KEY) !== "0"); } catch (_) {}
      try { if (cbSync) cbSync.checked = (safeGetLS(LS_SYNC_KEY) === "1"); } catch (_) {}
      try { if (cbSync && safeGetLS(LS_SYNC_KEY) == null) cbSync.checked = true; } catch (_) {}

      const oldTa = UI.textarea;
      if (oldTa && oldTa !== textarea) {
        try {
          if (UI.handlers.onInput) oldTa.removeEventListener("input", UI.handlers.onInput);
          if (UI.handlers.onScrollEditor) oldTa.removeEventListener("scroll", UI.handlers.onScrollEditor);
        } catch (_) {}
      }
      try {
        if (UI.handlers.onScrollDiff) diffBox.removeEventListener("scroll", UI.handlers.onScrollDiff);
        if (UI.handlers.onChangeDiff) cbDiff.removeEventListener("change", UI.handlers.onChangeDiff);
        if (UI.handlers.onChangeHighlight) cbHi.removeEventListener("change", UI.handlers.onChangeHighlight);
        if (UI.handlers.onChangeSync) cbSync.removeEventListener("change", UI.handlers.onChangeSync);
      } catch (_) {}

      UI.textarea = textarea;

      let _syncing = false;
      function _ratio(el){ const d=(el.scrollHeight-el.clientHeight); return d>0? (el.scrollTop/d):0; }
      function _setByRatio(el,r){ const d=(el.scrollHeight-el.clientHeight); el.scrollTop = d>0? (r*d):0; }
      function syncScroll(fromEl,toEl){ if(_syncing) return; _syncing=true; try{ _setByRatio(toEl,_ratio(fromEl)); }catch(_){ } _syncing=false; }
      function syncHeights(){
        const h = Math.round(UI.textarea.getBoundingClientRect().height);
        if (diffBox) diffBox.style.height = h + "px";
        if (previewBox) previewBox.style.height = h + "px";
      }
      try {
        if (!UI._minimdResizeObserver && "ResizeObserver" in window) {
          UI._minimdResizeObserver = new ResizeObserver(() => syncHeights());
        }
        if (UI._minimdResizeObserver && UI._observedTextarea && UI._observedTextarea !== textarea) {
          try { UI._minimdResizeObserver.unobserve(UI._observedTextarea); } catch (_) {}
        }
        if (UI._minimdResizeObserver) {
          UI._minimdResizeObserver.observe(textarea);
          UI._observedTextarea = textarea;
        }
      } catch (_) {}

      function isSyncEnabled(){ return !!(cbDiff && cbDiff.checked && cbSync && cbSync.checked); }

      function render(){
        
        const liveTa = document.getElementById("comment-proposal") || UI.textarea;
        if (liveTa && liveTa !== UI.textarea) UI.textarea = liveTa;
        const edited = normalize((UI.textarea && UI.textarea.value) ? UI.textarea.value : "");
        const doDiff = !!(cbDiff && cbDiff.checked);
        const doHi = !!(cbHi && cbHi.checked);

        
        if (cbSync) cbSync.disabled = !doDiff;

        // Do not show the diff box while diff view is disabled; marker highlighting is independent.
        if (!doDiff) {
          diffBox.style.display = "none"; diffBox.textContent = "";
          previewBox.style.display = "none"; previewBox.textContent = "";
          setStatus("");
          return;
        }

        
        previewBox.style.display = "none"; previewBox.textContent = "";

        if (!UI.baseText) {
          diffBox.style.display = "block";
          diffBox.innerHTML = doHi ? highlightReserved(edited) : esc(edited);
          syncHeights();
          setStatus("Bestand lädt …");
          return;
        }

        diffBox.style.display = "block";
        diffBox.innerHTML = diffToHtml(UI.baseText, edited, { highlight: doHi });
        syncHeights();
        if (isSyncEnabled()) { try { syncScroll(UI.textarea, diffBox); } catch (_) {} }
        setStatus("");
      }

      function scheduleRender(){ if (UI.timer) clearTimeout(UI.timer); UI.timer = setTimeout(render, 120); }

      UI.handlers.onInput = () => scheduleRender();
      UI.handlers.onScrollEditor = () => { if (!isSyncEnabled()) return; if (diffBox.style.display !== "none") syncScroll(UI.textarea, diffBox); };
      UI.handlers.onScrollDiff = () => { if (!isSyncEnabled()) return; syncScroll(diffBox, UI.textarea); };
      UI.handlers.onChangeDiff = () => render();
      UI.handlers.onChangeHighlight = () => render();
      UI.handlers.onChangeSync = () => { try { safeSetLS(LS_SYNC_KEY, cbSync.checked ? "1" : "0"); } catch (_) {} render(); };

      
      try {
        if (UI._bound) {
          const b = UI._bound;
          if (b.cbDiff && UI.handlers.onChangeDiff) b.cbDiff.removeEventListener("change", UI.handlers.onChangeDiff);
          if (b.cbHi && UI.handlers.onChangeHighlight) b.cbHi.removeEventListener("change", UI.handlers.onChangeHighlight);
          if (b.cbSync && UI.handlers.onChangeSync) b.cbSync.removeEventListener("change", UI.handlers.onChangeSync);
          if (b.textarea && UI.handlers.onInput) b.textarea.removeEventListener("input", UI.handlers.onInput);
          if (b.textarea && UI.handlers.onScrollEditor) b.textarea.removeEventListener("scroll", UI.handlers.onScrollEditor);
          if (b.diffBox && UI.handlers.onScrollDiff) b.diffBox.removeEventListener("scroll", UI.handlers.onScrollDiff);
        }
      } catch (_) {}

      
      if (cbDiff) cbDiff.addEventListener("change", UI.handlers.onChangeDiff);
      if (cbHi) cbHi.addEventListener("change", UI.handlers.onChangeHighlight);
      if (cbSync) cbSync.addEventListener("change", UI.handlers.onChangeSync);
      textarea.addEventListener("input", UI.handlers.onInput);
      textarea.addEventListener("keyup", UI.handlers.onInput); 
      textarea.addEventListener("scroll", UI.handlers.onScrollEditor);
      diffBox.addEventListener("scroll", UI.handlers.onScrollDiff);
 
      render();
    }

    async function loadBaseMiniMd(articleId, versionId) {
      if (!articleId || !versionId) return null;
      const data = await apiFetchJson(
        `/api/articles/${encodeURIComponent(articleId)}/versions/${encodeURIComponent(versionId)}/minimd`,
        { method: "GET" },
        { authRequired: true }
      );
      return data && typeof data.minimd === "string" ? data.minimd : null;
    }



    ns.getBaseMiniMd = function () {
      return UI.baseText;
    };

    ns.setBaseMiniMdForComment = function (baseText, opts) {
      const o = (opts && typeof opts === "object") ? opts : {};
      const ta = document.getElementById(o.textareaId || "comment-proposal") || UI.textarea;
      if (ta) mountUI(ta);
      const key = String(o.key || "manual_base");
      UI.baseText = normalize(String(baseText || ""));
      UI.baseKey = key;
      commentState.baseMiniMd = UI.baseText;
      commentState.baseMiniMdKey = key;
      if (ta) {
        ta.dataset.minimdKey = key;
        if (!o.preserveTextarea && (!ta.value || !String(ta.value).trim())) {
          ta.value = UI.baseText;
        }
        try { ta.dispatchEvent(new Event("input")); } catch (_) {}
      }
    };


    ns.resetForComment = function () {
      UI.baseText = null;
      UI.baseKey = null;
      if (UI.toolbar) {
        const status = UI.toolbar.querySelector("#minimd-status");
        if (status) status.textContent = "";
        const cbDiff = UI.toolbar.querySelector("#minimd-diff-toggle");
        if (cbDiff) cbDiff.checked = false;
      }
      if (UI.diffBox) {
        UI.diffBox.style.display = "none";
        UI.diffBox.textContent = "";
      }
      if (UI.previewBox) {
        UI.previewBox.innerHTML = "";
      }
    };

    ns.initForComment = async function ({
      articleId,
      versionId,
      textareaId = "comment-proposal",
      preserveTextarea = false,
      forceBaseIntoTextarea = false,
    } = {}) {
      const ta = document.getElementById(textareaId);
      if (!ta) return;
      mountUI(ta);

      
      const prevKey = ta.dataset.minimdKey || "";

      const key = String(articleId) + ":" + String(versionId);
      if (UI.baseKey === key && UI.baseText) {
        ta.dataset.minimdKey = key;
        if (forceBaseIntoTextarea) {
          ta.value = UI.baseText;
        } else if (!preserveTextarea && (!ta.value || !ta.value.trim())) {
          ta.value = UI.baseText;
        }
        ta.dispatchEvent(new Event("input"));
        return;
      }

      UI.baseText = null;
      UI.baseKey = key;
      ta.dataset.minimdKey = key;

      try {
        const base = await loadBaseMiniMd(articleId, versionId);
        if (typeof base === "string") {
          UI.baseText = normalize(base);
          commentState.baseMiniMd = UI.baseText;
          commentState.baseMiniMdKey = key;

          // Drei Modi:
          
          
          
          if (forceBaseIntoTextarea) {
            ta.value = UI.baseText;
          } else if (preserveTextarea) {
            // bestehenden Draft-Inhalt bewusst erhalten
          } else if (prevKey && prevKey !== key) {
            ta.value = UI.baseText;
          } else if (!ta.value || !ta.value.trim()) {
            
            ta.value = UI.baseText;
          }

          ta.dispatchEvent(new Event("input"));
        }
      } catch (err) {
        console.warn("[KlimaGG] minimd base konnte nicht geladen werden:", err);
        UI.baseText = null;
      }
    };

  })(KlimaGG.minimd);

  document.addEventListener("DOMContentLoaded", () => {
    try {
    
    hookAnchorOffsetNavigation();
    setupNavDropdownAutoClose();
    setupPanelToggles();
    setupNavPanelCollapsibles();
    setupUserPanelCollapsibles();
    setupDraftSidebarsActivation();
    setupResizeHandles();
    setupTextLayerToggles();
    setupVersionDiffToggles();
    setupCommentsVisibilityControls();
    setupArticleScopeControls();
    setupHomeLandingControls();
    setupPersonalMoodToggle();
    setupBookmarksToggle();
    setupFontSizeControls();
    setupMoodOverviewControls();
    setupNavToggleBoxes();
    setupRoadmapPhases();
    setupLayoutControls();
    initAuth();
    setupCommentPanel();
    setupReviewQueuePanel();
    hookLandingButtons();

    const hasArticlesContainer = !!document.getElementById("articles-container");

    if (isAppIndex) {
      fetchHealth();
    } else {
      
      fetchHealth();
    }

    
    if (hasArticlesContainer) {
      fetchArticles();
    }
    } catch (err) {
      console.error("[KlimaGG] FATAL init error:", err);
      const statusEl = document.getElementById("health-status-text");
      if (statusEl) statusEl.textContent = "JS Fehler – siehe Konsole";
    }
  });


  
  function setupNavDropdownAutoClose() {
    const dds = Array.from(document.querySelectorAll("details.nav-dd"));
    if (!dds.length) return;

    function closeAll(except) {
      dds.forEach((d) => {
        if (d !== except && d.open) d.open = false;
      });
    }

    dds.forEach((d) => {
      d.addEventListener("toggle", () => {
        if (d.open) closeAll(d);
      });
      d.querySelectorAll(".nav-dd-menu a[href]").forEach((a) => {
        a.addEventListener("click", () => {
          d.open = false;
        });
      });
    });

    document.addEventListener(
      "click",
      (ev) => {
        const target = ev.target;
        dds.forEach((d) => {
          if (!d.open) return;
          if (target && d.contains(target)) return;
          d.open = false;
        });
      },
      true
    );

    document.addEventListener("keydown", (ev) => {
      if (ev.key === "Escape") closeAll(null);
    });
  }


  // --- Roadmap UI (Split-Layout + Variante B horizontal) -------------------
  function setupRoadmapPhases() {
    const phaseLists = [
      document.getElementById("roadmap-phases"),
      document.getElementById("roadmap-phases-h"),
    ].filter(Boolean);

    const detailBoxes = [
      document.getElementById("roadmap-detail"),
      document.getElementById("roadmap-detail-h"),
    ].filter(Boolean);

    if (!phaseLists.length || !detailBoxes.length) return;

    const buttons = phaseLists.flatMap((list) =>
      Array.from(list.querySelectorAll("[data-phase]"))
    );

    if (!buttons.length) return;

    const LS_KEY = "klimagg.ui.roadmap_phase";

    function renderPhaseInto(box, phase) {
      const tpl = document.getElementById("roadmap-phase-" + phase);
      box.innerHTML = "";
      if (tpl && tpl.content) {
        box.appendChild(tpl.content.cloneNode(true));
      } else {
        box.textContent = "Details zu Phase " + phase + " sind noch nicht hinterlegt.";
      }
    }

    function renderPhase(phase) {
      detailBoxes.forEach((box) => renderPhaseInto(box, phase));
    }

    function setActivePhase(phase) {
      buttons.forEach((b) => {
        const isActive = b.dataset.phase === String(phase);
        b.classList.toggle("is-active", isActive);
        b.setAttribute("aria-selected", isActive ? "true" : "false");
      });
      renderPhase(phase);
      safeSetLS(LS_KEY, String(phase));
    }

    buttons.forEach((b) => {
      b.addEventListener("click", () => setActivePhase(b.dataset.phase));
    });

    
    const stored = safeGetLS(LS_KEY);
    const initial = buttons.some((b) => b.dataset.phase === stored) ? stored : "0";
    setActivePhase(initial);
  }

  // --- Healthcheck --------------------------------------------------------

  async function fetchHealth() {
    try {
      const data = await apiFetchJson("/api/health", {}, { timeoutMs: 8000 });
      console.log("[KlimaGG] /api/health:", data);

      const statusEl = document.getElementById("health-status-text");
      if (statusEl) {
        statusEl.textContent =
          data.status === "ok" ? "erreichbar" : data.status;
      }
    } catch (err) {
      console.warn("[KlimaGG] Healthcheck fehlgeschlagen:", err);
      const statusEl = document.getElementById("health-status-text");
      if (statusEl) {
        statusEl.textContent = "Fehler – Backend nicht erreichbar";
      }
    }
  }

  // --- Auth-Handling (Magic-Link + JWT) ----------------------------------

  function escapeHtml(str) {
    return String(str)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#039;");
  }


  
  let __lastAuthKey = "__init__";

  function maybeTriggerAuthRefreshCascade() {
    const key =
      klimaggAuth.user && klimaggAuth.user.id != null
        ? "u:" + String(klimaggAuth.user.id)
        : "anon";
    if (key === __lastAuthKey) return;
    __lastAuthKey = key;

    // Login/Logout → definierte Refresh-Kaskade
    requestRefreshCascade(key === "anon" ? "logout" : "login", {
      wantAuthed: true,
    });
  }

  
  function _setElHidden(el, hidden) {
    if (!el) return;
    el.classList.toggle("hidden", !!hidden);
    if (hidden) el.setAttribute("aria-hidden", "true");
    else el.removeAttribute("aria-hidden");
  }

  function _setDetailsAuthLocked(detailsEl, locked) {
    if (!detailsEl || !detailsEl.tagName) return;
    if (String(detailsEl.tagName).toLowerCase() !== "details") return;

    detailsEl.classList.toggle("auth-locked", !!locked);
    if (locked) {
      detailsEl.dataset.authLocked = "1";
      detailsEl.open = false;
      
      if (detailsEl.dataset.authLockWired !== "1") {
        detailsEl.dataset.authLockWired = "1";
        detailsEl.addEventListener("toggle", () => {
          if (detailsEl.dataset.authLocked === "1" && detailsEl.open) detailsEl.open = false;
        });
      }
    } else {
      delete detailsEl.dataset.authLocked;
      
    }
  }

  
  function _setDetailsForceOpen(detailsEl, forceOpen) {
    if (!detailsEl || !detailsEl.tagName) return;
    if (String(detailsEl.tagName).toLowerCase() !== "details") return;
  
    detailsEl.classList.toggle("force-open", !!forceOpen);
    if (forceOpen) {
      detailsEl.dataset.forceOpen = "1";
      detailsEl.open = true;
      
      if (detailsEl.dataset.forceOpenWired !== "1") {
        detailsEl.dataset.forceOpenWired = "1";
        detailsEl.addEventListener("toggle", () => {
          if (detailsEl.dataset.forceOpen === "1" && !detailsEl.open) detailsEl.open = true;
        });
      }
    } else {
      delete detailsEl.dataset.forceOpen;
      
    }
  }
  
  function applyUserStatusCardCollapseMode({ locked } = {}) {
    const d = document.getElementById("user-status-card");
    if (!d) return;
  
    const isLocked = !!locked;
    _setDetailsForceOpen(d, isLocked);
  
    const key = UI_USERBOX_STORAGE_PREFIX + "user-status";
    if (isLocked) {
      d.open = true;
      return;
    }
  
    const stored = safeGetLS(key);
    if (stored === "open") d.open = true;
    else if (stored === "closed") d.open = false;
    else d.open = true; // Default: Status sichtbar lassen
  }
  
  
  (function _wireForceOpenDetailsOnce() {
    try {
      if (window.__klimaggForceOpenWired) return;
      window.__klimaggForceOpenWired = true;
  
      document.addEventListener(
        "click",
        (ev) => {
          const sum =
            ev.target && ev.target.closest
              ? ev.target.closest("details.force-open > summary")
              : null;
          if (!sum) return;
          ev.preventDefault();
          ev.stopPropagation();
        },
        true
      );
  
      document.addEventListener(
        "keydown",
        (ev) => {
          const key = ev && ev.key ? String(ev.key) : "";
          if (key !== "Enter" && key !== " ") return;
          const sum =
            ev.target && ev.target.closest
              ? ev.target.closest("details.force-open > summary")
              : null;
          if (!sum) return;
          ev.preventDefault();
          ev.stopPropagation();
        },
        true
      );
    } catch (_) {}
  })();
  
  
  (function _wireAuthLockedDetailsOnce() {
    try {
      if (window.__klimaggAuthLockWired) return;
      window.__klimaggAuthLockWired = true;

      document.addEventListener("click", (ev) => {
        const sum = ev.target && ev.target.closest ? ev.target.closest("details.auth-locked > summary") : null;
        if (!sum) return;
        ev.preventDefault();
        ev.stopPropagation();
      }, true);

      document.addEventListener("keydown", (ev) => {
        const key = ev && ev.key ? ev.key : "";
        if (key !== "Enter" && key !== " ") return;
        const sum = ev.target && ev.target.closest ? ev.target.closest("details.auth-locked > summary") : null;
        if (!sum) return;
        ev.preventDefault();
        ev.stopPropagation();
      }, true);
    } catch (_) {}
  })();

  function applyUserPanelAuthGates(isAuthed) {
    const hideExtras = !isAuthed;

    
    _setElHidden(document.getElementById("user-display-card"), hideExtras);
    _setElHidden(document.getElementById("user-toggles-card"), hideExtras);
    _setElHidden(document.getElementById("user-feedback-card"), hideExtras);

    
    _setDetailsAuthLocked(document.getElementById("user-votes-overview-section"), hideExtras);
    _setDetailsAuthLocked(document.getElementById("review-overview-section"), hideExtras);
    _setDetailsAuthLocked(document.getElementById("comment-overview-section"), hideExtras);
  }


  
  function _parseReleaseLabelToYmd(label) {
    const s = (label == null) ? '' : String(label).trim();
    const m = /^v?(\d{4})-(\d{2})-(\d{2})/.exec(s);
    if (!m) return null;
    const y = parseInt(m[1], 10);
    const mo = parseInt(m[2], 10);
    const d = parseInt(m[3], 10);
    if (!y || !mo || !d) return null;
    return y * 10000 + mo * 100 + d;
  }

  function _sortReleaseLabels(labels) {
    const arr = Array.isArray(labels) ? labels.slice() : [];
    arr.sort((a, b) => {
      const da = _parseReleaseLabelToYmd(a);
      const db = _parseReleaseLabelToYmd(b);
      if (da != null && db != null && da !== db) return da - db;
      if (da != null && db == null) return -1;
      if (da == null && db != null) return 1;
      return String(a).localeCompare(String(b), 'de');
    });
    return arr;
  }

  function _shortReleaseLabel(label) {
    const s = (label == null) ? '' : String(label).trim();
    // Beispiele:
    // - "klimagg-20251228-...." -> v25.12
    // - "v2025-12-28"          -> v25.12
    // - "2025-12-28"           -> v25.12
    // - "20251228"             -> v25.12
    let y = null, mo = null;
    let m = /(\d{4})-(\d{2})-(\d{2})/.exec(s);
    if (m) {
      y = m[1]; mo = m[2];
    } else {
      m = /(\d{4})(\d{2})(\d{2})/.exec(s);
      if (m) { y = m[1]; mo = m[2]; }
    }
    if (y && mo) return `v${String(y).slice(2)}.${mo}`;
    
    return s.length > 12 ? (s.slice(0, 12) + '…') : s;
  }

  function _getArticleAnchorIdForClient(article) {
    if (!article || typeof article !== 'object') return null;
    if (article.slug) return String(article.slug);
    if (article.id != null) return `artikel-${article.id}`;
    return null;
  }

  function _articleDisplayLabelForClient(article) {
    if (!article || typeof article !== 'object') return '';
    const displayLabel = typeof article.display_label === 'string' ? article.display_label.trim() : '';
    if (displayLabel) return displayLabel;
    const publicCode = typeof article.public_code === 'string' ? article.public_code.trim() : '';
    const title = typeof article.title === 'string' ? article.title.trim() : '';
    if (publicCode && title && publicCode === title) return title;
    if (publicCode && title) return publicCode + ' – ' + title;
    if (publicCode) return publicCode;
    return title;
  }

  function _articleTocLabelForClient(article) {
    if (!article || typeof article !== 'object') return '';
    const publicCode = typeof article.public_code === 'string' ? article.public_code.trim() : '';
    const tocTitle = typeof article.toc_title === 'string' ? article.toc_title.trim() : '';
    const groupLabel = typeof article.toc_group_label === 'string' ? article.toc_group_label.trim() : '';
    const shortLabel = tocTitle || groupLabel;
    if (shortLabel) {
      const codeLooksLikeArticle = /^Artikel\s+\S+/i.test(publicCode);
      const shortAlreadyHasCode = publicCode && shortLabel.toLowerCase().startsWith(publicCode.toLowerCase());
      const shortStartsWithArticle = /^Artikel\s+\S+/i.test(shortLabel);
      if (codeLooksLikeArticle && !shortAlreadyHasCode && !shortStartsWithArticle) {
        return publicCode + ': ' + shortLabel;
      }
      return shortLabel;
    }
    return _articleDisplayLabelForClient(article);
  }

  function _articleSortKeyForClient(article) {
    if (!article || typeof article !== 'object') return Number.POSITIVE_INFINITY;
    const sortOrder = Number(article.sort_order);
    if (Number.isFinite(sortOrder)) return sortOrder;
    const aid = Number(article.id);
    return Number.isFinite(aid) ? aid : Number.POSITIVE_INFINITY;
  }

  function getCurrentVersionIdForArticle(articleId) {
    const aid = (articleId == null) ? null : Number(articleId);
    if (!aid || Number.isNaN(aid)) return null;

    
    try {
      const el = document.querySelector(`article[data-article-id="${aid}"],.article-wrap[data-article-id="${aid}"]`);
      if (el) {
        const v =
          el.getAttribute('data-current-version-id') ||
          el.getAttribute('data-current-version') ||
          el.getAttribute('data-version-id') ||
          '';
        const vid = v ? parseInt(v, 10) : null;
        if (vid) return vid;
      }
    } catch (_) {}

    // 2) In-Memory (API /api/articles)
    try {
      const arts = Array.isArray(appState.articles_all) ? appState.articles_all : (Array.isArray(appState.articles) ? appState.articles : []);
      const a = arts.find((x) => x && x.id === aid);
      if (a && a.current_version && a.current_version.id != null) return Number(a.current_version.id);
    } catch (_) {}

    return null;
  }

  function renderUserVotesOverview() {
    const section = document.getElementById('user-votes-overview-section');
    const headRow = document.getElementById('user-votes-table-head-row');
    const body = document.getElementById('user-votes-table-body');
    const hint = document.getElementById('user-votes-overview-hint');
    if (!section || !headRow || !body) return;

    const authed = !!(klimaggAuth.user && klimaggAuth.accessToken);
    const articles = Array.isArray(appState.articles_all)
      ? appState.articles_all
      : (Array.isArray(appState.articles) ? appState.articles : []);

    if (!articles.length) {
      headRow.innerHTML = '<th>Artikel</th>';
      body.innerHTML = '<tr><td class="klein" colspan="2">Lade Artikel …</td></tr>';
      return;
    }

    
    const labelsSet = new Set();
    for (const a of articles) {
      const vs = a && a.vote_summary && Array.isArray(a.vote_summary.versions) ? a.vote_summary.versions : [];
      vs.forEach((v) => {
        if (v && v.version_label) labelsSet.add(String(v.version_label));
      });
    }
    const releaseLabels = _sortReleaseLabels(Array.from(labelsSet));

    // Header bauen
    headRow.innerHTML = '';
    const th0 = document.createElement('th');
    th0.textContent = 'Artikel';
    headRow.appendChild(th0);

    for (const label of releaseLabels) {
      const th = document.createElement('th');
      const span = document.createElement('span');
      span.className = 'rel-short';
      span.textContent = _shortReleaseLabel(label);
      th.title = String(label);
      th.appendChild(span);
      headRow.appendChild(th);
    }

    // Body bauen
    body.innerHTML = '';

    if (!authed) {
      const tr = document.createElement('tr');
      const td = document.createElement('td');
      td.className = 'klein';
      td.colSpan = Math.max(1, 1 + releaseLabels.length);
      td.textContent = 'Zur Anzeige deiner eigenen Stimmen bitte anmelden.';
      tr.appendChild(td);
      body.appendChild(tr);
      if (hint) hint.textContent = 'Eigene Stimmen pro Artikel und Release (⚪ = nicht abgestimmt).';
      return;
    }

    
    const sortedArticles = articles.slice().sort((a, b) => {
      const ai = _articleSortKeyForClient(a);
      const bi = _articleSortKeyForClient(b);
      if (ai !== bi) return ai - bi;
      const aid = (a && a.id != null) ? Number(a.id) : 0;
      const bid = (b && b.id != null) ? Number(b.id) : 0;
      return aid - bid;
    });

    for (const a of sortedArticles) {
      if (!a || a.id == null) continue;
      const tr = document.createElement('tr');

      
      const tdTitle = document.createElement('td');
      const title = _articleTocLabelForClient(a);
      const anchorId = _getArticleAnchorIdForClient(a);
      if (anchorId) {
        const link = document.createElement('a');
        link.href = '#' + anchorId;
        link.title = title;
        const span = document.createElement('span');
        span.className = 'article-title-short';
        span.textContent = title;
        link.appendChild(span);
        link.title = 'Zum Artikel springen';
        tdTitle.appendChild(link);
      } else {
        const span = document.createElement('span');
        span.className = 'article-title-short';
        span.textContent = title;
        tdTitle.title = title;
        tdTitle.appendChild(span);
      }
      tr.appendChild(tdTitle);

      
      const versions = a && a.vote_summary && Array.isArray(a.vote_summary.versions) ? a.vote_summary.versions : [];
      const versionIdByLabel = {};
      versions.forEach((v) => {
        if (!v || v.version_id == null || !v.version_label) return;
        versionIdByLabel[String(v.version_label)] = Number(v.version_id);
      });

      for (const label of releaseLabels) {
        const td = document.createElement('td');
        td.className = 'vote-cell';

        const versionId = versionIdByLabel[String(label)];
        if (!versionId) {
          const span = document.createElement('span');
          span.className = 'vote-missing';
          span.textContent = '—';
          span.title = 'In dieser Version nicht vorhanden.';
          td.appendChild(span);
          tr.appendChild(td);
          continue;
        }

        const key = String(a.id) + ':' + String(versionId);
        const entry = personalArticleVotesByKey ? personalArticleVotesByKey[key] : null;

        const emoji = entry && entry.main_vote ? String(entry.main_vote) : '⚪';
        const span = document.createElement('span');
        if (!entry) span.className = 'vote-empty';
        span.textContent = emoji;

        let tip = entry ? ('Deine Stimme: ' + emoji) : 'Nicht abgestimmt.';
        const rxEntry = personalArticleReactionsByKey ? personalArticleReactionsByKey[key] : null;
        const emojis = rxEntry && Array.isArray(rxEntry.emojis) ? rxEntry.emojis.filter(Boolean) : [];
        if (emojis.length) tip += ' · Emojis: ' + emojis.join(' ');
        span.title = tip;

        td.appendChild(span);
        tr.appendChild(td);
      }

      body.appendChild(tr);
    }

    if (hint) {
      hint.textContent = 'Eigene Stimmen pro Artikel und Release (⚪ = nicht abgestimmt).';
    }
  }

  function updateUserUI() {
    const userSummary = document.querySelector(
      "#user-left-panel .user-summary"
    );
    const userPanel = document.getElementById("user-panel");
    const userStatusSummary = document.getElementById("user-status-summary");
    const userStatusCard = document.getElementById("user-status-card");
    const navAdmin = document.getElementById("nav-admin");

    function _setStatusCardTitle(suffix) {
      try {
        if (!userStatusCard) return;
        const suffixEl = userStatusCard.querySelector("#user-status-title-suffix");
        if (suffixEl) {
          suffixEl.textContent = "(" + String(suffix || "") + ")";
          return;
        }
        const sum = userStatusCard.querySelector("summary");
        if (!sum) return;
        sum.textContent = "Status (" + String(suffix || "") + ")";
      } catch (_) {}

    }

    
    if (!klimaggAuth.user) {
      _setStatusCardTitle("nicht angemeldet");
      if (userSummary) {
        userSummary.innerHTML =
          '<p><b>Status:</b> nicht angemeldet</p>' +
          '<p class="klein">Melde dich an, um Kommentare vorzubereiten, Votes zu setzen und Reviews abzugeben.</p>';
      }
      if (userStatusSummary) {
        userStatusSummary.innerHTML =
          '<p class="klein">Du kannst den Entwurf lesen, auch ohne Anmeldung. Für Votes, Kommentare und Reviews ist eine Anmeldung nötig.</p>' +
          '<p class="klein hint" style="margin:0.35rem 0 0;">Anmeldung erfolgt oben im Abschnitt <b>„Anmeldung per E-Mail-Link“</b> (Magic-Link per E-Mail).</p>';
      }

      if (navAdmin) navAdmin.classList.add("hidden");

      applyUserPanelAuthGates(false);
	  
      
      applyUserStatusCardCollapseMode({ locked: true });

      
      renderUserVotesOverview();

      refreshReviewQueueUI();

      
      maybeTriggerAuthRefreshCascade();

      updateOwnMoodToggleAvailability();
      updateBookmarksToggleAvailability();
      renderAuthFlowUI();
      syncAuthCTAButtons();
      try { if (typeof window.klimaggRefreshCommentArticleSelect === "function") window.klimaggRefreshCommentArticleSelect(); } catch (_) {}
      try { if (typeof window.klimaggUpdateCommentPanelButtons === "function") window.klimaggUpdateCommentPanelButtons(); } catch (_) {}
      return;
    }

    // Angemeldet
    const user = klimaggAuth.user;
    _setStatusCardTitle("angemeldet");
    if (navAdmin) {
      if (user && user.is_admin) navAdmin.classList.remove("hidden");
      else navAdmin.classList.add("hidden");
    }

    applyUserPanelAuthGates(true);

    
    renderUserVotesOverview();

    const pseudonym = user.pseudonym || user.email;
    const needsPseudo = looksLikeEmail(pseudonym);

    
    applyUserStatusCardCollapseMode({ locked: !!needsPseudo });
    const identityHtml = loggedInIdentityHtml(user);

    if (userSummary) {
      userSummary.innerHTML =
        '<p class="klein" style="margin:0;">' + identityHtml + "</p>";
    }

    if (userStatusSummary) {
      let statusHtml = '<p class="klein" style="margin:0;">' + identityHtml + "</p>";

      statusHtml +=
        '<details id="pseudo-sidebar" class="app-mutedbox" ' + (needsPseudo ? 'open' : '') + '>' +
        '  <summary><b>' + (needsPseudo ? 'Profil vervollständigen: Pseudonym wählen' : 'Pseudonym ändern') + '</b></summary>' +
        '  <div class="klein" style="margin-top:8px;">Kein Freitext – du wählst einen Vorschlag aus einem neutralen Pool.</div>' +
        '  <div style="display:flex; gap:10px; margin-top:10px; align-items:flex-end; flex-wrap:wrap;">' +
        '    <label class="field" style="flex: 1 1 200px; margin:0;">' +
        '      <span class="klein">Namenspool</span>' +
        '      <select id="pseudo-pool" class="input"></select>' +
        '    </label>' +
        '    <button id="pseudo-refresh" class="btn btn-ghost" type="button">Neue Vorschläge</button>' +
        '  </div>' +
        '  <div id="pseudo-pool-desc" class="klein hint" style="margin-top:6px;"></div>' +
        '  <div id="pseudo-suggestions" class="suggestion-grid" style="margin-top:10px;"></div>' +
        '  <div class="klein" style="margin-top:8px;">Ausgewählt: <b id="pseudo-chosen">—</b></div>' +
        '  <button id="pseudo-submit" class="btn btn-primary btn-full" type="button" style="margin-top:10px;" disabled>Pseudonym speichern</button>' +
        '  <div id="pseudo-status" class="klein" style="margin-top:8px; min-height:1.2em;"></div>' +
        '</details>';

      statusHtml +=
        '<details id="account-delete-box" class="app-mutedbox" style="margin-top:10px;">' +
        '  <summary><b>Account löschen</b></summary>' +
        '  <div class="klein" style="margin-top:8px;">Du kannst die Löschung 24h lang widerrufen. Danach wird dein Account anonymisiert (Kommentare bleiben erhalten, der Autorenname wird anonym).</div>' +
        '  <div style="display:flex; gap:10px; margin-top:10px; flex-wrap:wrap; align-items:center;">' +
        '    <button id="account-delete-schedule" class="btn btn-ghost btn-sm" type="button">Account löschen (24h Countdown)</button>' +
        '    <button id="account-delete-now" class="btn btn-ghost btn-sm" type="button" style="display:none;">Account sofort unwiderruflich löschen</button>' +
        '  </div>' +
        '  <div id="account-delete-status" class="klein hint" style="margin-top:8px;"></div>' +
        '</details>';

 
      userStatusSummary.innerHTML = statusHtml;
      wirePseudonymSidebarHandlers();
      try { wireAccountDeleteHandlers(user); } catch (_) {}
    }



    // Panels (sofort) anpassen
    refreshReviewQueueUI();

    
    maybeTriggerAuthRefreshCascade();

    updateOwnMoodToggleAvailability();
    updateBookmarksToggleAvailability();
    renderAuthFlowUI();
    syncAuthCTAButtons();
    try { if (typeof window.klimaggRefreshCommentArticleSelect === "function") window.klimaggRefreshCommentArticleSelect(); } catch (_) {}
    try { if (typeof window.klimaggUpdateCommentPanelButtons === "function") window.klimaggUpdateCommentPanelButtons(); } catch (_) {}
  }

  function persistAuth({ broadcast = true, reason = "persist" } = {}) {
    if (!window.localStorage) return;
    if (klimaggAuth.accessToken) {
      window.localStorage.setItem(AUTH_STORAGE_KEY, klimaggAuth.accessToken);
    } else {
      window.localStorage.removeItem(AUTH_STORAGE_KEY);
    }
    if (broadcast) signalAuthStateChanged(reason);
  }

  // AUDIT(v1.1.132): Auth-State liegt in klimaggAuth + localStorage.
  
  async function restoreAuthFromStorage() {
    if (!window.localStorage) return;

    const token = window.localStorage.getItem(AUTH_STORAGE_KEY);
    if (!token) {
      klimaggAuth.accessToken = null;
      klimaggAuth.user = null;
      updateUserUI();
      return;
    }

    try {
      const user = await apiFetchJson("/api/me", {}, { authRequired: true, authToken: token, retryAuth: false });

      klimaggAuth.accessToken = token;
      klimaggAuth.user = user;
      updateUserUI();

      
      try {
        const flag = safeGetLS(UI_POST_LOGIN_OPEN_RIGHT_PANEL_KEY);
        if (flag === '1') {
          if (window.localStorage) window.localStorage.removeItem(UI_POST_LOGIN_OPEN_RIGHT_PANEL_KEY);
          openRightPanelForLogin({ focus: false });
        }
      } catch (_) {}

      try { await importGuestInteractionsAfterAuth(); } catch (_) {}

      await loadUserReviewStats();
    } catch (err) {
      console.warn(
        "[KlimaGG] /api/me mit gespeichertem Token fehlgeschlagen:",
        err
      );
      klimaggAuth.accessToken = null;
      klimaggAuth.user = null;
      persistAuth({ reason: "restore_failed" });
      updateUserUI();
    }
  }

  function setupAuthStateSync() {
    window.addEventListener("storage", (ev) => {
      if (!ev) return;
      if (![AUTH_STORAGE_KEY, SIGNUP_STORAGE_KEY, AUTH_STATE_CHANGED_KEY].includes(String(ev.key || ""))) {
        return;
      }
      Promise.resolve(restoreAuthFromStorage()).catch((err) => {
        console.warn("[KlimaGG] Auth-State-Sync fehlgeschlagen:", err);
        logoutLocal({ reason: "", broadcast: false });
      });
      try { renderAuthFlowUI({ focus: false }); } catch (_) {}
    });
  }

  function initAuth() {
    setupAuthStateSync();

    
    restoreAuthFromStorage();
    updateUserUI();

    
    
    try {
      const flag = safeGetLS(UI_POST_LOGIN_OPEN_RIGHT_PANEL_KEY);
      if (flag === "1") {
        if (window.localStorage) window.localStorage.removeItem(UI_POST_LOGIN_OPEN_RIGHT_PANEL_KEY);
        openRightPanelForLogin({ focus: false });
        
        renderAuthFlowUI({ focus: false });
      }
    } catch (_) {}
  }

  async function startLoginFlow() {
    if (!isAppIndex) {
      notify("Login ist aktuell nur auf der Hauptseite verfügbar.", { type: "warn" });
      return;
    }

    
    renderAuthFlowUI({ focus: true });
  }


  // --- Auth-Flow ----------------------------------------------------------
  // 2-stufiger Flow im Nutzerpanel
  //   1) E-Mail eingeben → Magic-Link senden
  
  const authFlowState = {
    email: "",
    info: "",
    error: "",
    busy: false,
    // Signup-Stufe
    pool: "neutral",
    pools: null,
    poolDescById: {},
    suggestions: [],
    chosen: "",
    lastRefreshAt: 0,
    // Registrierung: Zustimmung (1x) zu Datenschutz & Nutzungsbedingungen
    agreePrivacy: false,
    agreeTerms: false,
    
    signupAutoloadStarted: false,
  };

  function getSignupToken() {
    try {
      return (window.localStorage && window.localStorage.getItem(SIGNUP_STORAGE_KEY)) || "";
    } catch (_) {
      return "";
    }
  }

  function clearSignupToken() {
    try {
      if (window.localStorage) window.localStorage.removeItem(SIGNUP_STORAGE_KEY);
    } catch (_) {}
    
    authFlowState.signupAutoloadStarted = false;
    authFlowState.pools = null;
    authFlowState.poolDescById = {};
    authFlowState.suggestions = [];
    authFlowState.chosen = "";
    authFlowState.info = "";
    authFlowState.error = "";
    authFlowState.agreePrivacy = false;
    authFlowState.agreeTerms = false;
  }

  function ensureAuthFlowRoot() {
    return document.getElementById("auth-flow-root");
  }

  function errDetail(err) {
    try {
      if (err && err.payload && err.payload.detail) return String(err.payload.detail);
    } catch (_) {}
    return err && err.message ? String(err.message) : String(err || "");
  }

  function loggedInIdentityHtml(user) {
    const pseudo = escapeHtml(String((user && (user.pseudonym || user.email)) || "–"));
    const plz = escapeHtml(String((user && user.plz) || "ohne PLZ"));
    const email = escapeHtml(String((user && user.email) || "–"));
    return (
      'Angemeldet als <b>' +
      pseudo +
      '</b> aus <b>' +
      plz +
      '</b> unter der Adresse <b>' +
      email +
      '</b>. Abmeldung: oben in der Leiste.'
    );
  }

  async function ensureSignupDataLoaded({ forceSuggestions = false } = {}) {
    if (!authFlowState.pools) {
      const data = await apiGetPseudonymPools();
      const pools = Array.isArray(data && data.pools) ? data.pools : [];
      authFlowState.pools = pools;
      authFlowState.poolDescById = {};
      for (const p of pools) {
        authFlowState.poolDescById[String(p.id)] = String(p.description || "");
      }
      if (!authFlowState.pool) authFlowState.pool = "neutral";
    }

    if (forceSuggestions || !Array.isArray(authFlowState.suggestions) || authFlowState.suggestions.length === 0) {
      const data2 = await apiGetPseudonymSuggestions(authFlowState.pool, 10);
      authFlowState.suggestions = Array.isArray(data2 && data2.suggestions) ? data2.suggestions : [];
      if (!authFlowState.chosen && authFlowState.suggestions.length) {
        authFlowState.chosen = String(authFlowState.suggestions[0] || "");
      }
    }
  }

  function renderAuthFlowUI({ focus = false } = {}) {
    const root = ensureAuthFlowRoot();
    if (!root) return;

    
    try {
      const titleEl = document.querySelector("#auth-flow-card > strong");
      if (titleEl) titleEl.textContent = "Anmeldung per E-Mail-Link";
    } catch (_) {}

    
    if (klimaggAuth.user) {
      try {
        const titleEl = document.querySelector("#auth-flow-card > strong");
        if (titleEl) titleEl.textContent = "Anmeldung";
      } catch (_) {}
      const pseudo = escapeHtml(String(klimaggAuth.user.pseudonym || klimaggAuth.user.email || "–"));
      const plz = escapeHtml(String(klimaggAuth.user.plz || "ohne PLZ"));
      root.innerHTML = `
        <div class="klein">
          <p style="margin:0;"><b>Angemeldet als:</b> ${pseudo} <span class="hint">(${plz})</span></p>
          <p class="hint" style="margin:0.35rem 0 0;">Abmelden: oben in der Leiste.</p>
          <p class="klein" style="margin:0.55rem 0 0;">
            Hier kannst du dein <a href="#" data-auth-inline="pseudo">[Pseudonym ändern]</a>.
            Hier deine <a href="#" data-auth-inline="plz">[PLZ aktualisieren]</a>.
          </p>

          <div id="auth-inline-profile" style="margin-top:0.55rem;">
            <div id="auth-inline-pseudo" class="app-mutedbox" style="display:none;">
              <div class="klein"><b>Pseudonym ändern</b></div>

              <div class="row-2" style="margin-top:0.45rem;">
                <label class="field">
                  <span class="field-label">Namenspool</span>
                  <select class="field-input" id="auth-inline-pseudo-pool"></select>
                </label>
                <div style="display:flex; align-items:end; justify-content:flex-end;">
                  <button class="btn btn-ghost btn-sm" type="button" data-auth-inline-action="pseudo-refresh">Pseudonym neu generieren</button>
                </div>
              </div>

              <div class="suggestion-grid" id="auth-inline-pseudo-suggestions" style="margin-top:0.5rem;"></div>
              <div class="klein" style="margin-top:8px;">Ausgewählt: <b id="auth-inline-pseudo-chosen">–</b></div>

              <div style="margin-top:0.55rem; display:flex; gap:0.5rem; align-items:center;">
                <button class="btn btn-primary btn-sm" type="button" data-auth-inline-action="pseudo-save">Speichern</button>
                <button class="btn btn-ghost btn-sm" type="button" data-auth-inline-action="pseudo-cancel">Schließen</button>
                <span class="klein hint" id="auth-inline-pseudo-status"></span>
              </div>
            </div>

            <div id="auth-inline-plz" class="app-mutedbox" style="display:none;">
              <div class="klein"><b>PLZ aktualisieren</b></div>

              <div class="row-2" style="margin-top:0.45rem;">
                <label class="field">
                  <span class="field-label">PLZ</span>
                  <input class="field-input" id="auth-inline-plz-input" inputmode="numeric" maxlength="5" placeholder="z.B. 45127" />
                </label>
                <div style="display:flex; gap:0.5rem; align-items:end; justify-content:flex-end;">
                  <button class="btn btn-primary btn-sm" type="button" data-auth-inline-action="plz-save">PLZ aktualisieren</button>
                  <button class="btn btn-ghost btn-sm" type="button" data-auth-inline-action="plz-cancel">Schließen</button>
                </div>
              </div>

              <div class="klein hint" style="margin-top:0.35rem;" id="auth-inline-plz-status"></div>
            </div>
          </div>
        </div>
      `;
      wireAuthInlineProfileEditors(root);
      return;
    }

    const signupToken = getSignupToken();

    
    if (signupToken) {
      try {
        const titleEl = document.querySelector("#auth-flow-card > strong");
        if (titleEl) titleEl.textContent = "Registrierung";
      } catch (_) {}
      const poolDesc = authFlowState.poolDescById[String(authFlowState.pool)] || "";
      root.innerHTML = `
        <div class="klein">
          <p><b>Registrierung abschließen</b></p>
          <p class="hint">Wähle ein Pseudonym aus den Vorschlägen. PLZ ist optional.</p>

          <div class="row-2" style="margin-top:0.6rem;">
            <label class="field">
              <span class="field-label">PLZ (optional)</span>
              <input class="field-input" name="plz" inputmode="numeric" maxlength="10" placeholder="z.B. 45127" />
            </label>

            <label class="field">
              <span class="field-label">Namenspool</span>
              <select class="field-input" name="pool"></select>
            </label>
          </div>

          <div class="pseudonym-box" style="margin-top:0.6rem;">
            <div class="pseudonym-head">
              <div>
                <div class="pseudonym-title">Pseudonym auswählen</div>
                <div class="pseudonym-hint klein" data-el="pool-desc">${escapeHtml(poolDesc)}</div>
              </div>
              <button class="btn btn-ghost btn-sm" type="button" id="btn-auth-refresh">Neue Vorschläge</button>
            </div>
            <div class="suggestion-grid" id="auth-suggestions"></div>
            <div class="klein" style="margin-top:8px;">Ausgewählt: <b id="auth-chosen">${escapeHtml(authFlowState.chosen || "")}</b></div>
          </div>

          <div class="auth-legal-checks" style="margin-top:0.65rem;">
            <div class="klein" style="margin-bottom:0.35rem;"><b>Bitte bestätigen</b></div>
            <div class="comment-source-checks" align="left">
              <table role="presentation" width="100%" cellpadding="0" cellspacing="0" align="left">
                <tr>
                  <td width="26" valign="top" align="left">
                    <input id="auth-agree-privacy" type="checkbox" ${authFlowState.agreePrivacy ? "checked" : ""} />
                  </td>
                  <td valign="top" align="left">
                    <label for="auth-agree-privacy" class="klein">
                      Ich habe die <a href="/rechtliches#datenschutz">Datenschutz-Informationen</a> gelesen.
                    </label>
                  </td>
                </tr>
                <tr>
                  <td width="26" valign="top" align="left">
                    <input id="auth-agree-terms" type="checkbox" ${authFlowState.agreeTerms ? "checked" : ""} />
                  </td>
                  <td valign="top" align="left">
                    <label for="auth-agree-terms" class="klein">
                      Ich halte mich an die <a href="/rechtliches#nutzungsbedingungen">Nutzungsbedingungen</a>.
                    </label>
                  </td>
                </tr>
              </table>
            </div>
          </div>

          <div style="margin-top:0.65rem;">
            <button class="btn btn-primary btn-full" type="button" id="btn-auth-complete">Account anlegen</button>
          </div>

          <div class="klein hint" style="margin-top:0.45rem;" id="auth-info">${escapeHtml(authFlowState.info || "")}</div>
          <div class="klein" style="margin-top:0.25rem; color: var(--danger, #b42318);" id="auth-error">${escapeHtml(authFlowState.error || "")}</div>
        </div>
      `;

      const poolSel = root.querySelector('select[name="pool"]');
      const plzEl = root.querySelector('input[name="plz"]');
      const grid = root.querySelector("#auth-suggestions");
      const chosenEl = root.querySelector("#auth-chosen");
      const cbPrivacy = root.querySelector("#auth-agree-privacy");
      const cbTerms = root.querySelector("#auth-agree-terms");

      function updateConsentState() {
        authFlowState.agreePrivacy = !!(cbPrivacy && cbPrivacy.checked);
        authFlowState.agreeTerms = !!(cbTerms && cbTerms.checked);
        try {
          const btn = root.querySelector("#btn-auth-complete");
          if (btn) btn.disabled = !(authFlowState.agreePrivacy && authFlowState.agreeTerms) || !!authFlowState.busy;
        } catch (_) {}
      }
      if (cbPrivacy) cbPrivacy.addEventListener("change", updateConsentState);
      if (cbTerms) cbTerms.addEventListener("change", updateConsentState);
      updateConsentState();

      if (poolSel) {
        poolSel.innerHTML = "";
        const pools = Array.isArray(authFlowState.pools) ? authFlowState.pools : [];
        const fallbackPools = pools.length ? pools : [{ id: "neutral", label: "Neutral · Form & Material", description: "" }];
        for (const p of fallbackPools) {
          const opt = document.createElement("option");
          opt.value = String(p.id);
          opt.textContent = String(p.label || p.id);
          if (String(p.id) === String(authFlowState.pool)) opt.selected = true;
          poolSel.appendChild(opt);
        }
        poolSel.addEventListener("change", async () => {
          authFlowState.pool = String(poolSel.value || "neutral");
          authFlowState.info = "";
          authFlowState.error = "";
          authFlowState.chosen = "";
          try {
            await ensureSignupDataLoaded({ forceSuggestions: true });
          } catch (_) {
            authFlowState.error = "Vorschläge konnten nicht geladen werden.";
          }
          renderAuthFlowUI();
        });
      }

      if (grid) {
        grid.innerHTML = "";
        for (const s of authFlowState.suggestions || []) {
          const btn = document.createElement("button");
          btn.type = "button";
          btn.className = "suggestion-btn";
          btn.textContent = String(s);
          if (String(s) === String(authFlowState.chosen)) btn.classList.add("is-selected");
          btn.addEventListener("click", () => {
            authFlowState.chosen = String(s);
            if (chosenEl) chosenEl.textContent = authFlowState.chosen;
            try {
              const all = grid.querySelectorAll(".suggestion-btn.is-selected");
              all.forEach((b) => b.classList.remove("is-selected"));
              btn.classList.add("is-selected");
            } catch (_) {}
          });
          grid.appendChild(btn);
        }
      }

      const btnRefresh = root.querySelector("#btn-auth-refresh");
      if (btnRefresh) {
        btnRefresh.addEventListener("click", async () => {
          const now = Date.now();
          if (authFlowState.lastRefreshAt && now - authFlowState.lastRefreshAt < 60_000) {
            authFlowState.info = "Bitte warte kurz – neue Vorschläge sind nur alle 60 Sekunden möglich.";
            authFlowState.error = "";
            renderAuthFlowUI();
            return;
          }
          authFlowState.lastRefreshAt = now;
          authFlowState.info = "Lade neue Vorschläge …";
          authFlowState.error = "";
          renderAuthFlowUI();
          try {
            await ensureSignupDataLoaded({ forceSuggestions: true });
            authFlowState.info = "";
          } catch (_) {
            authFlowState.error = "Vorschläge konnten nicht geladen werden.";
          }
          renderAuthFlowUI();
        });
      }

      const btnComplete = root.querySelector("#btn-auth-complete");
      if (btnComplete) {
        btnComplete.addEventListener("click", async () => {
          if (authFlowState.busy) return;
          if (!(authFlowState.agreePrivacy && authFlowState.agreeTerms)) {
            authFlowState.error = "Bitte Datenschutz & Nutzungsbedingungen bestätigen.";
            renderAuthFlowUI();
            return;
          }
          const pseudonym = String(authFlowState.chosen || "").trim();
          if (!pseudonym) {
            authFlowState.error = "Bitte ein Pseudonym auswählen.";
            renderAuthFlowUI();
            return;
          }
          const plzRaw = plzEl ? String(plzEl.value || "").trim() : "";
          const plzNorm = plzRaw.replace(/\s+/g, "");
          const plz = plzNorm && /^\d{5}$/.test(plzNorm) ? plzNorm : null;
          if (plzRaw && !plz) {
            authFlowState.info = "Hinweis: PLZ ignoriert (Format z.B. 45127).";
          }

          authFlowState.busy = true;
          authFlowState.info = "Registrierung wird abgeschlossen …";
          authFlowState.error = "";
          renderAuthFlowUI();

          try {
            const data = await apiFetchJson("/api/auth/complete-signup", {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ signup_token: signupToken, pseudonym, plz }),
            });

            if (data && data.access_token) {
              klimaggAuth.accessToken = String(data.access_token);
              klimaggAuth.user = data.user || null;
              persistAuth();
              clearSignupToken();
              authFlowState.info = "Erfolgreich registriert.";
              authFlowState.error = "";
              updateUserUI();
              try { await importGuestInteractionsAfterAuth(); } catch (_) {}
              notify("Willkommen! Du kannst jetzt abstimmen.", { type: "success" });
              return;
            }

            authFlowState.error = "Unerwartete Antwort vom Server.";
          } catch (err) {
            authFlowState.error = errDetail(err) || "Registrierung fehlgeschlagen.";
          } finally {
            authFlowState.busy = false;
            renderAuthFlowUI();
          }
        });
      }

      
      
      const needsBoot =
        !authFlowState.pools ||
        !Array.isArray(authFlowState.suggestions) ||
        authFlowState.suggestions.length === 0;

      if (needsBoot && !authFlowState.signupAutoloadStarted) {
        authFlowState.signupAutoloadStarted = true;
        (async () => {
          try {
            await ensureSignupDataLoaded({ forceSuggestions: false });
          } catch (_) {
            authFlowState.error = authFlowState.error || "Vorschläge konnten nicht geladen werden.";
          }
          renderAuthFlowUI();
        })();
      }

      return;
    }

    // 3) E-Mail-Stufe
    root.innerHTML = `
      <form id="auth-email-form" class="klein" autocomplete="off">
        <p class="hint">Wir schicken dir einen Anmeldelink per E-Mail. Danach wählst du (einmalig) ein Pseudonym aus.</p>
 
        <div class="auth-row-inline" style="margin-top:0.6rem;">
          <label class="field" style="flex: 1 1 200px; margin:0;">
            <input class="field-input" id="auth-email" name="email" type="email" placeholder="name@beispiel.de" aria-label="E-Mail-Adresse" required />
          </label>
          <button class="btn btn-primary auth-inline-btn" type="submit">Link senden</button>
        </div>

        <div class="klein hint" style="margin-top:0.45rem;" id="auth-info">${escapeHtml(authFlowState.info || "")}</div>
        <div class="klein" style="margin-top:0.25rem; color: var(--danger, #b42318);" id="auth-error">${escapeHtml(authFlowState.error || "")}</div>
      </form>
    `;

    const form = root.querySelector("#auth-email-form");
    const emailInput = form ? form.querySelector('input[name="email"]') : null;
    if (emailInput && authFlowState.email) emailInput.value = authFlowState.email;
    if (focus && emailInput) {
      try { emailInput.focus(); } catch (_) {}
    }

    if (form) {
      form.addEventListener("submit", async (ev) => {
        ev.preventDefault();
        if (authFlowState.busy) return;

        const email = emailInput ? String(emailInput.value || "").trim() : "";
        if (!email || !/@/.test(email)) {
          authFlowState.error = "Bitte eine gültige E-Mail eingeben.";
          authFlowState.info = "";
          renderAuthFlowUI();
          return;
        }

        authFlowState.busy = true;
        authFlowState.email = email;
        authFlowState.error = "";
        authFlowState.info = "Sende Magic-Link …";
        renderAuthFlowUI();

        try {
          const data = await apiFetchJson("/api/auth/magic-link", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ email }),
          });

          authFlowState.info =
            (data && data.email_sent)
              ? "Link gesendet. Bitte prüfe dein Postfach (und ggf. Spam)."
              : "Link erzeugt. (Mail-Versand ist evtl. deaktiviert.)";
        } catch (err) {
          authFlowState.error = errDetail(err) || "Link konnte nicht gesendet werden.";
          authFlowState.info = "";
        } finally {
          authFlowState.busy = false;
          renderAuthFlowUI();
        }
      });
    }
  }


  // --- Inline: Pseudonym/PLZ im "Anmeldung"-Bereich (eingeloggt) --------

  function _getAuthInlineState(root) {
    if (!root) return null;
    if (!root._authInlineProfileState) {
      root._authInlineProfileState = {
        pseudo: {
          pools: null,
          pool: "neutral",
          suggestions: [],
          chosen: "",
          lastRefreshAt: 0,
          busy: false,
        },
        plz: { busy: false },
      };
    }
    return root._authInlineProfileState;
  }

  function _getAuthInlineEls(root) {
    if (!root) return null;
    return {
      pseudoBox: root.querySelector("#auth-inline-pseudo"),
      pseudoPool: root.querySelector("#auth-inline-pseudo-pool"),
      pseudoGrid: root.querySelector("#auth-inline-pseudo-suggestions"),
      pseudoChosen: root.querySelector("#auth-inline-pseudo-chosen"),
      pseudoStatus: root.querySelector("#auth-inline-pseudo-status"),
      plzBox: root.querySelector("#auth-inline-plz"),
      plzInput: root.querySelector("#auth-inline-plz-input"),
      plzStatus: root.querySelector("#auth-inline-plz-status"),
    };
  }

  function _setAuthInlineVisible(els, which) {
    if (!els) return;
    if (els.pseudoBox) els.pseudoBox.style.display = which === "pseudo" ? "" : "none";
    if (els.plzBox) els.plzBox.style.display = which === "plz" ? "" : "none";
  }

  function _renderAuthInlinePseudo(root) {
    const st = _getAuthInlineState(root);
    const els = _getAuthInlineEls(root);
    if (!st || !els || !els.pseudoBox) return;

    // Pools
    if (els.pseudoPool) {
      const pools = Array.isArray(st.pseudo.pools) && st.pseudo.pools.length
        ? st.pseudo.pools
        : [{ id: "neutral", label: "Neutral · Form & Material", description: "" }];

      els.pseudoPool.innerHTML = "";
      for (const p of pools) {
        const opt = document.createElement("option");
        opt.value = String(p.id);
        opt.textContent = String(p.label || p.id);
        if (String(p.id) === String(st.pseudo.pool)) opt.selected = true;
        els.pseudoPool.appendChild(opt);
      }
    }

    // Grid
    if (els.pseudoGrid) {
      els.pseudoGrid.innerHTML = "";
      for (const s of st.pseudo.suggestions || []) {
        const b = document.createElement("button");
        b.type = "button";
        b.className = "suggestion-btn";
        b.textContent = String(s);
        b.setAttribute("data-auth-inline-suggestion", String(s));
        if (String(s) === String(st.pseudo.chosen)) b.classList.add("is-selected");
        els.pseudoGrid.appendChild(b);
      }
    }

    if (els.pseudoChosen) els.pseudoChosen.textContent = st.pseudo.chosen ? String(st.pseudo.chosen) : "–";
  }

  async function _ensureAuthInlinePseudoLoaded(root, { forceSuggestions = false } = {}) {
    const st = _getAuthInlineState(root);
    if (!st) return;

    if (!Array.isArray(st.pseudo.pools) || st.pseudo.pools.length === 0) {
      const poolsData = await apiGetPseudonymPools();
      st.pseudo.pools = (poolsData && Array.isArray(poolsData.pools)) ? poolsData.pools : [];
      if (!st.pseudo.pool) st.pseudo.pool = "neutral";
    }

    if (forceSuggestions || !Array.isArray(st.pseudo.suggestions) || st.pseudo.suggestions.length === 0) {
      const sugData = await apiGetPseudonymSuggestions(st.pseudo.pool, 10);
      st.pseudo.suggestions = (sugData && Array.isArray(sugData.suggestions)) ? sugData.suggestions : [];
      if (!st.pseudo.chosen && st.pseudo.suggestions.length) {
        st.pseudo.chosen = String(st.pseudo.suggestions[0] || "");
      }
    }
  }

  async function _submitAuthInlinePseudonym(root) {
    const st = _getAuthInlineState(root);
    const els = _getAuthInlineEls(root);
    if (!st || !els) return;
    if (!klimaggAuth.user || !klimaggAuth.accessToken) {
      startLoginFlow();
      return;
    }

    const pseudonym = String(st.pseudo.chosen || "").trim();
    if (!pseudonym) {
      if (els.pseudoStatus) els.pseudoStatus.textContent = "Bitte ein Pseudonym auswählen.";
      return;
    }

    if (els.pseudoStatus) els.pseudoStatus.textContent = "Speichere …";
    try {
      const data = await apiFetchJson(
        "/api/me/pseudonym",
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ pseudonym }),
        },
        { authRequired: true }
      );

      if (data && typeof data === "object") {
        klimaggAuth.user = data;
        persistAuth();
      }
      updateUserUI(); 
      notify("Pseudonym aktualisiert.", { type: "success" });
    } catch (err) {
      console.error("[KlimaGG] Inline-Pseudonym-Update fehlgeschlagen:", err);
      if (els.pseudoStatus) els.pseudoStatus.textContent = errDetail(err) || "Fehler beim Speichern.";
    }
  }

  async function _submitAuthInlinePlz(root) {
    const els = _getAuthInlineEls(root);
    if (!els) return;
    if (!klimaggAuth.user || !klimaggAuth.accessToken) {
      startLoginFlow();
      return;
    }

    const raw = els.plzInput ? String(els.plzInput.value || "").trim() : "";
    const norm = raw.replace(/\s+/g, "");
    if (!/^\d{5}$/.test(norm)) {
      if (els.plzStatus) els.plzStatus.textContent = "Bitte 5 Ziffern eingeben (z.B. 45127).";
      return;
    }

    if (els.plzStatus) els.plzStatus.textContent = "Speichere …";
    try {
      const data = await apiFetchJson(
        "/api/me/plz",
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ plz: norm }),
        },
        { authRequired: true }
      );

      if (data && typeof data === "object") {
        klimaggAuth.user = data;
        persistAuth();
      }
      updateUserUI(); 
      notify("PLZ aktualisiert.", { type: "success" });
    } catch (err) {
      console.error("[KlimaGG] Inline-PLZ-Update fehlgeschlagen:", err);
      if (els.plzStatus) els.plzStatus.textContent = errDetail(err) || "Fehler beim Speichern.";
    }
  }

  function wireAuthInlineProfileEditors(root) {
    if (!root) return;
    if (root.dataset && root.dataset.authInlineWired === "1") return;
    if (root.dataset) root.dataset.authInlineWired = "1";

    
    root.addEventListener("click", async (ev) => {
      const a = ev.target && ev.target.closest ? ev.target.closest('a[data-auth-inline]') : null;
      if (a) {
        ev.preventDefault();
        const which = String(a.getAttribute("data-auth-inline") || "");
        const els = _getAuthInlineEls(root);
        const st = _getAuthInlineState(root);
        if (!els || !st) return;

        
        const isOpen = (which === "pseudo" && els.pseudoBox && els.pseudoBox.style.display !== "none")
          || (which === "plz" && els.plzBox && els.plzBox.style.display !== "none");

        if (isOpen) {
          _setAuthInlineVisible(els, null);
          return;
        }

        _setAuthInlineVisible(els, which);

        // Status leeren
        if (els.pseudoStatus) els.pseudoStatus.textContent = "";
        if (els.plzStatus) els.plzStatus.textContent = "";

        if (which === "pseudo") {
          try {
            await _ensureAuthInlinePseudoLoaded(root, { forceSuggestions: false });
          } catch (err) {
            console.error("[KlimaGG] Inline-Pseudonym: Laden fehlgeschlagen:", err);
            if (els.pseudoStatus) els.pseudoStatus.textContent = "Vorschläge konnten nicht geladen werden.";
          }
          _renderAuthInlinePseudo(root);
        } else if (which === "plz") {
          
          try {
            if (els.plzInput && klimaggAuth.user && klimaggAuth.user.plz) {
              els.plzInput.value = String(klimaggAuth.user.plz || "");
            }
            if (els.plzInput && typeof els.plzInput.focus === "function") {
              els.plzInput.focus();
            }
          } catch (_) {}
        }
        return;
      }

      const sugBtn = ev.target && ev.target.closest ? ev.target.closest("button.suggestion-btn[data-auth-inline-suggestion]") : null;
      if (sugBtn) {
        ev.preventDefault();
        const st = _getAuthInlineState(root);
        const els = _getAuthInlineEls(root);
        if (!st || !els) return;
        st.pseudo.chosen = String(sugBtn.getAttribute("data-auth-inline-suggestion") || "");
        _renderAuthInlinePseudo(root);
        return;
      }

      const act = ev.target && ev.target.closest ? ev.target.closest("button[data-auth-inline-action]") : null;
      if (!act) return;
      ev.preventDefault();

      const action = String(act.getAttribute("data-auth-inline-action") || "");
      const els = _getAuthInlineEls(root);
      const st = _getAuthInlineState(root);
      if (!els || !st) return;

      if (action === "pseudo-cancel") {
        _setAuthInlineVisible(els, null);
        return;
      }
      if (action === "plz-cancel") {
        _setAuthInlineVisible(els, null);
        return;
      }

      if (action === "pseudo-refresh") {
        const now = Date.now();
        if (st.pseudo.lastRefreshAt && now - st.pseudo.lastRefreshAt < 60_000) {
          if (els.pseudoStatus) els.pseudoStatus.textContent = "Bitte warte kurz – neue Vorschläge nur alle 60 Sekunden.";
          return;
        }
        st.pseudo.lastRefreshAt = now;
        if (els.pseudoStatus) els.pseudoStatus.textContent = "Lade neue Vorschläge …";
        try {
          st.pseudo.chosen = "";
          st.pseudo.suggestions = [];
          await _ensureAuthInlinePseudoLoaded(root, { forceSuggestions: true });
          if (els.pseudoStatus) els.pseudoStatus.textContent = "";
        } catch (err) {
          console.error("[KlimaGG] Inline-Pseudonym: Refresh fehlgeschlagen:", err);
          if (els.pseudoStatus) els.pseudoStatus.textContent = "Vorschläge konnten nicht geladen werden.";
        }
        _renderAuthInlinePseudo(root);
        return;
      }

      if (action === "pseudo-save") {
        await _submitAuthInlinePseudonym(root);
        return;
      }

      if (action === "plz-save") {
        await _submitAuthInlinePlz(root);
        return;
      }
    });

    // Pool-Wechsel (Select) per Delegation
    root.addEventListener("change", async (ev) => {
      const sel = ev.target && ev.target.id === "auth-inline-pseudo-pool" ? ev.target : null;
      if (!sel) return;
      const st = _getAuthInlineState(root);
      const els = _getAuthInlineEls(root);
      if (!st || !els) return;

      st.pseudo.pool = String(sel.value || "neutral");
      st.pseudo.chosen = "";
      st.pseudo.suggestions = [];
      if (els.pseudoStatus) els.pseudoStatus.textContent = "Lade Vorschläge …";
      try {
        await _ensureAuthInlinePseudoLoaded(root, { forceSuggestions: true });
        if (els.pseudoStatus) els.pseudoStatus.textContent = "";
      } catch (err) {
        console.error("[KlimaGG] Inline-Pseudonym: Pool-Wechsel fehlgeschlagen:", err);
        if (els.pseudoStatus) els.pseudoStatus.textContent = "Vorschläge konnten nicht geladen werden.";
      }
      _renderAuthInlinePseudo(root);
    });
  }

  
  // Ablauf:
  
  // 2) Button "Anmeldelink schicken" -> /api/auth/magic-link
  
  

  function openRightPanelForLogin({ focus = false } = {}) {
    try {
      if (!document.body) return;
      // Home: Sidebars sichtbar machen (inkl. hidden=false)
      ensureHomeSidebarsVisible();
      document.body.classList.remove('right-collapsed');
      safeSetLS(UI_RIGHT_PANEL_STORAGE_KEY, 'open');
      window.dispatchEvent(new Event('resize'));

      if (focus) {
        window.setTimeout(() => {
          const el = document.getElementById('auth-email') || document.querySelector('#auth-flow-root input[name="email"]');
          if (el && typeof el.focus === 'function') el.focus();
        }, 60);
      }
    } catch (_) {}
  }

  function looksLikeEmail(s) {
    const t = String(s || '').trim();
    return t.includes('@') && t.includes('.') && t.length >= 6;
  }


  // --- Pseudonym-Setup in rechter Sidebar --------------------------------
  

  function getPseudonymSidebarEls() {
    const root = document.getElementById("pseudo-sidebar");
    if (!root) return null;
    return {
      root,
      pool: document.getElementById("pseudo-pool"),
      poolDesc: document.getElementById("pseudo-pool-desc"),
      suggestions: document.getElementById("pseudo-suggestions"),
      chosen: document.getElementById("pseudo-chosen"),
      refresh: document.getElementById("pseudo-refresh"),
      submit: document.getElementById("pseudo-submit"),
      status: document.getElementById("pseudo-status"),
      btn: document.getElementById("btn-change-pseudonym"),
    };
  }

  function buildPseudonymSidebarState() {
    const els = getPseudonymSidebarEls();
    if (!els || !els.pool || !els.poolDesc || !els.suggestions || !els.chosen || !els.refresh || !els.submit || !els.status) {
      return null;
    }
    return {
      root: els.root,
      pool: els.pool,
      poolDesc: els.poolDesc,
      suggestions: els.suggestions,
      chosen: els.chosen,
      refresh: els.refresh,
      submit: els.submit,
      status: els.status,
      selectedPseudonym: null,
      pools: null,
      _cooldownTimer: null,
    };
  }

  async function ensurePseudonymSidebarInitialized(st) {
    if (!st) return;
    if (st._initialized) return;
    st._initialized = true;

    st.status.textContent = "Lade Namenspools …";
    try {
      const poolsData = await apiGetPseudonymPools();
      const pools = poolsData && Array.isArray(poolsData.pools) ? poolsData.pools : [];
      st.pools = pools;
      st.pool.innerHTML = "";
      pools.forEach((p) => {
        const opt = document.createElement("option");
        opt.value = p.id;
        opt.textContent = p.label || p.id;
        st.pool.appendChild(opt);
      });
      if (!st.pool.value) st.pool.value = "neutral";

      updatePoolDesc(st);
      
      await loadSuggestionsIntoModal(st, true);
      st.status.textContent = "";

      // Cooldown-Ticker starten
      _setRefreshButtonState(st, "pseudo_sidebar");
      _startCooldownTicker(st, "pseudo_sidebar");
    } catch (err) {
      console.error("[KlimaGG] Pseudonym-Pools konnten nicht geladen werden:", err);
      st.status.textContent = "Fehler beim Laden der Namenspools.";
    }
  }

  async function submitPseudonymUpdateFromSidebar(st) {
    if (!st) return;
    if (!klimaggAuth.user || !klimaggAuth.accessToken) {
      startLoginFlow();
      return;
    }

    const pseudonym = st.selectedPseudonym;
    if (!pseudonym) {
      st.status.textContent = "Bitte einen Vorschlag auswählen.";
      return;
    }

    st.submit.disabled = true;
    st.status.textContent = "Speichere Pseudonym …";

    try {
      const resp = await fetch("/api/me/pseudonym", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: "Bearer " + klimaggAuth.accessToken,
        },
        body: JSON.stringify({ pseudonym }),
      });
      const data = await resp.json().catch(() => null);
      if (!resp.ok) {
        const detail = data && data.detail ? data.detail : "Unbekannter Fehler";
        throw new Error(detail);
      }

      klimaggAuth.user = data;
      updateUserUI();
      st.status.textContent = "Pseudonym gespeichert.";
    } catch (err) {
      console.error("[KlimaGG] Pseudonym-Update fehlgeschlagen:", err);
      const msg = err && err.message ? String(err.message) : String(err);
      st.status.textContent = "Fehler: " + msg;
    } finally {
      st.submit.disabled = !st.selectedPseudonym;
    }
  }

  function wirePseudonymSidebarHandlers() {
    const els = getPseudonymSidebarEls();
    if (!els) return;

    
    if (els.btn && !els.btn.dataset.wired) {
      els.btn.dataset.wired = "1";
      els.btn.addEventListener("click", (e) => {
        e.preventDefault();
        openRightPanelForLogin({ focus: false });
        const det = document.getElementById("pseudo-sidebar");
        if (det && det.tagName && det.tagName.toLowerCase() === "details") {
          det.open = true;
        }
        try {
          if (det && typeof det.scrollIntoView === "function") {
            det.scrollIntoView({ behavior: "smooth", block: "start" });
          }
        } catch (_) {}
      });
    }

    if (els.root && els.root.dataset.wired === "1") {
      
      const st = buildPseudonymSidebarState();
      if (st) ensurePseudonymSidebarInitialized(st);
      return;
    }
    if (els.root) els.root.dataset.wired = "1";

    const st = buildPseudonymSidebarState();
    if (!st) return;

    
    ensurePseudonymSidebarInitialized(st);

    // Pool-Wechsel
    st.pool.addEventListener("change", () => {
      loadSuggestionsIntoModal(st, true);
      _setRefreshButtonState(st, "pseudo_sidebar");
    });

    
    st.refresh.addEventListener("click", (e) => {
      e.preventDefault();
      const rem = _getCooldownRemainingMs("pseudo_sidebar");
      if (rem > 0) {
        _setRefreshButtonState(st, "pseudo_sidebar");
        return;
      }
      _setCooldownNow("pseudo_sidebar");
      _setRefreshButtonState(st, "pseudo_sidebar");
      loadSuggestionsIntoModal(st, true);
    });

    
    st.submit.addEventListener("click", (e) => {
      e.preventDefault();
      submitPseudonymUpdateFromSidebar(st);
    });
  }

  
  const PSEUDONYM_SUGGESTION_COUNT = 10;
  const PSEUDONYM_REFRESH_COOLDOWN_MS = 60 * 1000;

  function _cooldownKey(kind) {
    return "klimagg.pseudonym.refresh_ts_v1." + String(kind || "generic");
  }

  function _getCooldownRemainingMs(kind) {
    const ts = safeGetLS(_cooldownKey(kind));
    const last = ts ? parseInt(ts, 10) : 0;
    if (!last) return 0;
    const rem = PSEUDONYM_REFRESH_COOLDOWN_MS - (Date.now() - last);
    return rem > 0 ? rem : 0;
  }

  function _setCooldownNow(kind) {
    safeSetLS(_cooldownKey(kind), String(Date.now()));
  }

  function _setRefreshButtonState(st, kind) {
    if (!st || !st.refresh) return;
    const rem = _getCooldownRemainingMs(kind);
    if (rem <= 0) {
      st.refresh.disabled = false;
      st.refresh.textContent = "Neue Vorschläge";
      return;
    }
    st.refresh.disabled = true;
    const secs = Math.ceil(rem / 1000);
    st.refresh.textContent = "Neue Vorschläge (" + secs + "s)";
  }

  function _startCooldownTicker(st, kind) {
    if (!st) return;
    if (st._cooldownTimer) {
      clearInterval(st._cooldownTimer);
      st._cooldownTimer = null;
    }
    _setRefreshButtonState(st, kind);
    st._cooldownTimer = setInterval(() => {
      if (!st.root || !document.body.contains(st.root)) {
        clearInterval(st._cooldownTimer);
        st._cooldownTimer = null;
        return;
      }
      _setRefreshButtonState(st, kind);
      if (_getCooldownRemainingMs(kind) <= 0) {
        clearInterval(st._cooldownTimer);
        st._cooldownTimer = null;
      }
    }, 250);
  }

  async function apiGetPseudonymPools() {
    const resp = await fetch("/api/pseudonym/pools");
    if (!resp.ok) throw new Error("HTTP " + resp.status);
    return resp.json();
  }

  async function apiGetPseudonymSuggestions(poolId, n = PSEUDONYM_SUGGESTION_COUNT) {
    const url = `/api/pseudonym/suggestions?pool=${encodeURIComponent(poolId || "neutral")}&n=${encodeURIComponent(String(n))}`;
    const resp = await fetch(url);
    if (!resp.ok) throw new Error("HTTP " + resp.status);
    return resp.json();
  }

  
  function renderSuggestions(st, suggestions) {
    st.suggestions.innerHTML = "";
    st.selectedPseudonym = null;
    st.chosen.textContent = "—";
    st.submit.disabled = true;

    (suggestions || []).forEach((name) => {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "suggestion-btn";
      btn.textContent = name;
      btn.addEventListener("click", () => {
        st.selectedPseudonym = name;
        st.chosen.textContent = name;
        st.submit.disabled = false;
        Array.from(st.suggestions.querySelectorAll(".suggestion-btn.is-selected")).forEach((el) => el.classList.remove("is-selected"));
        btn.classList.add("is-selected");
      });
      st.suggestions.appendChild(btn);
    });
  }

  function updatePoolDesc(st) {
    const pid = st.pool.value || "neutral";
    const match = (st.pools || []).find((p) => p.id === pid);
    st.poolDesc.textContent = match && match.description ? match.description : "";
  }

  async function loadSuggestionsIntoModal(st, resetSelection) {
    if (!st) return;
    updatePoolDesc(st);
    if (resetSelection) {
      st.selectedPseudonym = null;
      st.chosen.textContent = "—";
      st.submit.disabled = true;
    }
    st.status.textContent = "Lade Vorschläge …";
    try {
      const data = await apiGetPseudonymSuggestions(st.pool.value || "neutral", 12);
      const suggestions = Array.isArray(data.suggestions) ? data.suggestions : [];
      renderSuggestions(st, suggestions);
      st.status.textContent = suggestions.length ? "" : "Keine Vorschläge verfügbar.";
    } catch (err) {
      console.error("[KlimaGG] Pseudonym-Vorschläge konnten nicht geladen werden:", err);
      st.status.textContent = "Fehler beim Laden der Vorschläge.";
      st.suggestions.innerHTML = "";
    }
  }


  function getPublishedNewArticleCommentIdsFromArticle(article) {
    const raw = article && (article.public_comment_ids || article.publicCommentIds || []);
    const arr = Array.isArray(raw)
      ? raw
      : String(raw || "").split(/[\s,;]+/);
    const out = [];
    const seen = new Set();
    arr.forEach((value) => {
      const cid = String(value || "").trim();
      if (!cid || seen.has(cid)) return;
      seen.add(cid);
      out.push(cid);
    });
    return out;
  }

  function isPublishedNewArticleProposalArticle(article) {
    return !!(
      article &&
      String(article.visibility_status || "") === "proposed_by_comment" &&
      getPublishedNewArticleCommentIdsFromArticle(article).length > 0
    );
  }

  function applyPublishedNewArticleDomMetadata(el, article) {
    if (!el || !article) return;
    try {
      const status0 = String(article.visibility_status || "");
      if (status0) el.setAttribute("data-visibility-status", status0);
      const ids = getPublishedNewArticleCommentIdsFromArticle(article);
      if (ids.length) el.setAttribute("data-public-comment-ids", ids.join(" "));
      if (isPublishedNewArticleProposalArticle(article)) {
        el.setAttribute("data-new-article-proposal", "true");
      }
    } catch (_) {}
  }

  function getRequiredPublishedNewArticleCommentIdsForArticleId(articleId) {
    const aid = String(articleId || "").trim();
    if (!aid) return [];
    const out = [];
    const seen = new Set();
    try {
      const selector = '[data-article-id="' + cssEscape(aid) + '"][data-visibility-status="proposed_by_comment"]';
      document.querySelectorAll(selector).forEach((el) => {
        const raw = String(el.getAttribute("data-public-comment-ids") || "");
        raw.split(/[\s,;]+/).forEach((part) => {
          const cid = String(part || "").trim();
          if (!cid || seen.has(cid)) return;
          seen.add(cid);
          out.push(cid);
        });
      });
    } catch (_) {}
    return out;
  }

  function mergeRequiredInlineDiffIds(articleId, ids) {
    const out = [];
    const seen = new Set();
    const push = (value) => {
      const cid = String(value || "").trim();
      if (!cid || seen.has(cid)) return;
      seen.add(cid);
      out.push(cid);
    };
    getRequiredPublishedNewArticleCommentIdsForArticleId(articleId).forEach(push);
    (Array.isArray(ids) ? ids : []).forEach(push);
    return out;
  }

  function enforcePublishedNewArticleInlineDiff(articleId, versionId) {
    const required = getRequiredPublishedNewArticleCommentIdsForArticleId(articleId);
    if (!required.length) return false;
    try {
      const st = getInlineDiffStateSafe();
      if (st && typeof st.setLayers === "function") {
        st.setLayers(articleId, required, versionId || undefined);
        return true;
      }
    } catch (_) {}
    return false;
  }

  async function activatePublishedNewArticleProposalArticles(articles) {
    const rows = (Array.isArray(articles) ? articles : []).filter((article) => {
      return (
        article &&
        String(article.visibility_status || "") === "proposed_by_comment" &&
        getPublishedNewArticleCommentIdsFromArticle(article).length > 0
      );
    });
    if (!rows.length) return;

    try { setCommentOverlayModeAndSync("on"); } catch (_) {}

    for (const article of rows) {
      const aid = article && article.id != null ? String(article.id) : "";
      if (!aid) continue;
      const ids = getPublishedNewArticleCommentIdsFromArticle(article);
      if (!ids.length) continue;
      const versionId =
        article &&
        article.current_version &&
        article.current_version.id != null
          ? String(article.current_version.id)
          : "";

      try {
        setManualInlineDiffIdsSafe(aid, ids, versionId);
      } catch (_) {}

      try {
        const box = document.querySelector('.article-comments[data-article-id="' + aid + '"]');
        if (box) {
          box.hidden = false;
          const listEl = box.querySelector(".article-comments-list");
          if (listEl && listEl.dataset.loaded !== "true" && listEl.dataset.loaded !== "loading") {
            await loadCommentsForArticle(aid, listEl);
          }
        }
      } catch (_) {}

      try {
        await refreshInlineDiffSafe(aid, versionId || "", { source: "newArticleProposal.auto" });
      } catch (err) {
        console.warn("[KlimaGG] new_article Inline-Diff konnte nicht automatisch aktiviert werden:", aid, err);
      }
    }

    try { updateCommentOverlayResetControlState(); } catch (_) {}
  }

  async function fetchArticles() {
    const container = document.getElementById("articles-container");
    if (!container) return;

    renderAsyncState(container, {
      kind: "loading",
      message: "Artikel werden geladen …",
    });

    try {
      const articles = await apiFetchJson("/api/articles");

      if (!Array.isArray(articles) || articles.length === 0) {
        renderAsyncState(container, {
          kind: "empty",
          message:
            "Noch keine Artikel in der Datenbank. (Seeder in app.py legt einen Beispielartikel an.)",
        });
        return;
      }

      
      appState.articles_all = articles;
      const scoped = filterArticlesByScope(appState.articles_all, getArticleScope());
      appState.articles = scoped;

      renderArticles(scoped);
      buildTOC(articles);
      initVotingUI(scoped);
      computeAndRenderGlobalMood(scoped);
      if (klimaggAuth.user && klimaggAuth.accessToken) {
        applyPersonalVotesToRenderedArticles();
      } else {
        applyGuestVotesToRenderedArticles();
        applyGuestReactionsToRenderedArticles();
      }
      applyTextLayerTogglesOnce(); 
      await applyVersionDiffModeToRenderedArticles({ silent: true });
      await applyCommentOverlayModeToRenderedArticles({ silent: true });
      await rehydrateCommentOverlaysForRenderedArticles({ silent: true });
      await reapplyGuestCommentFlagOverlaysForLoadedArticles({ silent: true });
      await activatePublishedNewArticleProposalArticles(scoped);
      if (typeof window.klimaggRefreshCommentArticleSelect === "function") {
        try { window.klimaggRefreshCommentArticleSelect(); } catch (_) {}
      }
      try { if (typeof window.klimaggUpdateCommentPanelButtons === "function") window.klimaggUpdateCommentPanelButtons(); } catch (_) {}
      updateHeroStatsFromCache();
      renderUserVotesOverview();
      scheduleAdminJumpFromLocation();
    } catch (err) {
      console.error("[KlimaGG] Fehler beim Laden der Artikel:", err);
      renderAsyncState(container, {
        kind: "error",
        message: "Artikel konnten nicht geladen werden.",
        onRetry: () => fetchArticles(),
      });
      notify("Artikel konnten nicht geladen werden.", { type: "error" });
    }
  }

  function renderArticles(articles) {
    const container = document.getElementById("articles-container");
    if (!container) return;

    container.innerHTML = "";

    articles.forEach((article) => {
      const artEl = document.createElement("article");
      const articleAnchorId = article.slug || `artikel-${article.id}`;
      artEl.id = articleAnchorId;
      artEl.classList.add("has-stimmungsbild"); 

      
      if (article.id != null) {
        artEl.setAttribute("data-article-id", String(article.id));
      }
      applyPublishedNewArticleDomMetadata(artEl, article);

      const h2 = document.createElement("h2");
      h2.textContent = _articleDisplayLabelForClient(article);
      artEl.appendChild(h2);

      const current = article.current_version || {};
      const blocks = current.content_blocks || {};

      if (current && current.id != null) {
        
        artEl.setAttribute("data-current-version-id", String(current.id));
        // Legacy/Kompat: einige Pfade lesen data-version-id
        artEl.setAttribute("data-version-id", String(current.id));
      }

      // Inline-diff invariant: always render block containers, including empty ones,
      // so backend-composed changes can be mounted into previously empty blocks.
      
      const mkBlock = (key, html) => {
        const div = document.createElement("section");
        div.className = `content-block ${key}`;
        div.setAttribute("data-block", String(key || ""));
        const safeHtml = (html && String(html).trim().length) ? String(html) : "";
        
        
        div.innerHTML = safeHtml;
        if (!safeHtml) {
          div.classList.add("is-empty");
          div.hidden = true;
        }

        // Freeze the exact article baseline. Canonical inline-diff markers must never be present here.
        const baselineHtml = String(div.innerHTML || "");
        if (/\bkgg-inline-op\b|\bkgg-inline-cid\b|\bkgg-conflict-inline-marker\b|\bminimd-(ins|del|warn)\b|\bminimd-diff-inline\b|\bkgg-merged-diff\b|data-kgg-cid\b|data-kgg-part\b/.test(baselineHtml)) {
          throw new Error("Inline-diff baseline contains diff artifacts: " + String(key || "unknown"));
        }
        div.dataset.kggBaseHtml = baselineHtml;
        div.dataset.kggBaseText = String(div.textContent || "").replace(/\s+/g, " ").trim();
        div.dataset.kggBaselineFrozen = "1";

        artEl.appendChild(div);
      };

      
      
      mkBlock("meta", blocks.meta);
      
      mkBlock("kurzinfo", blocks.kurzinfo);
      // Story / Kontext
      mkBlock("story", blocks.story);
      
      mkBlock("einleitung", blocks.einleitung);
      // Juristischer Haupttext (Bundesgesetz)
      mkBlock("juristisch", blocks.juristisch);
      // Juristischer Anhang / weitere juristische Ebene (optional)
      mkBlock("juristisch2", blocks.juristisch2);
      
      mkBlock("anmerkung", blocks.anmerkung || blocks.anmerkungen);

      
      const interactions = document.createElement("div");
      interactions.className = "article-interactions";
      interactions.innerHTML = `
        <div class="article-interactions-left">
          <div class="article-vote-bar" data-article-id="${article.id}">
            <div class="article-vote-buttons" role="group" aria-label="Stimme zu diesem Artikel abgeben">
              <button class="article-vote-btn" type="button" data-emoji="✅" title="Starke Zustimmung">✅</button>
              <button class="article-vote-btn" type="button" data-emoji="🟢" title="Zustimmung">🟢</button>
              <button class="article-vote-btn" type="button" data-emoji="🟡" title="Enthaltung / egal">🟡</button>
              <button class="article-vote-btn" type="button" data-emoji="🟠" title="Eher kritisch">🟠</button>
              <button class="article-vote-btn" type="button" data-emoji="🔴" title="Starke Ablehnung">🔴</button>
            </div>
            <div class="article-flag-buttons" role="group" aria-label="Zusätzliche Markierungen (unabhängig von der Stimme)">
              <button class="article-flag-btn" type="button" data-flag="🧭" title="Klimawirkung / strategisch stark">🧭</button>
              <button class="article-flag-btn" type="button" data-flag="✍️" title="Gut formuliert / gut lesbar">✍️</button>
              <button class="article-flag-btn" type="button" data-flag="🧩" title="Praktisch anschlussfähig / umsetzbar">🧩</button>
              <button class="article-flag-btn" type="button" data-flag="⚖️" title="Fair / ausgewogen / rechtlich sauber">⚖️</button>
              <button class="article-flag-btn" type="button" data-flag="🚩" title="Lesezeichen (privat)">🚩</button>
            </div>
            <div class="article-vote-summary" aria-live="polite"></div>
          </div>
        </div>
        <div class="article-interactions-spacer"></div>
        <button class="article-comment-btn" type="button" title="Kommentar zu diesem Artikel verfassen">
          Kommentar schreiben
        </button>
      `;      
      artEl.appendChild(interactions);

      const commentBtn = interactions.querySelector(".article-comment-btn");
      if (commentBtn && typeof window.klimaggOpenCommentForArticle === "function") {
        commentBtn.addEventListener("click", () => {
          window.klimaggOpenCommentForArticle({
            articleId: article.id,
            versionId: article.current_version ? article.current_version.id : null,
            title: _articleDisplayLabelForClient(article) || null,
            versionLabel:
              article.current_version && article.current_version.version_label
                ? article.current_version.version_label
                : null,
          });
        });
      }

      
      
      
      
      
      const commentsBox = document.createElement("div");
      commentsBox.className = "article-comments article-comments-outside";
      if (article.id != null) {
        commentsBox.setAttribute("data-article-id", String(article.id));
      }
      applyPublishedNewArticleDomMetadata(commentsBox, article);
      commentsBox.innerHTML = `
        <div class="article-comments-head klein">
          <div class="article-comments-title">Kommentare</div>
          <div class="article-comments-controls article-comments-controls-compact" aria-label="Kommentarsteuerung">
            <div class="article-comments-sort-chips" role="group" aria-label="Kommentare sortieren">
              <button class="article-comments-sort-chip" type="button" data-sort="newest" aria-pressed="true">Neueste</button>
              <button class="article-comments-sort-chip" type="button" data-sort="approval" aria-pressed="false">Zustimmung</button>
            </div>
            <div class="article-comments-bulk-actions" role="group" aria-label="Kommentar-Diffs steuern">
              <button class="article-comments-all-show" type="button">Alle einblenden</button>
              <button class="article-comments-all-hide" type="button">Alle ausblenden</button>
            </div>
          </div>
        </div>
        <div class="article-comments-hidden-layer-hint klein" hidden></div>
        <div class="article-comments-nextdraft-hint klein" hidden>
          Next-Draft ist aktiv. Qualifizierte Kommentare bleiben eingeblendet. Nochmals „Alle ausblenden“ klicken, um auch Next-Draft auszuschalten.
        </div>
        <div class="article-comments-list">
          <p class="article-comments-empty klein">Noch keine Kommentare sichtbar.</p>
        </div>
      `;

      
      
      try {
        const prefs = getCommentsVisibilityPrefs();
        commentsBox.hidden = commentsSuppressedByVersionLayer() || !(prefs && prefs.showComments);
      } catch (_) {
        commentsBox.hidden = true;
      }

      registerCommentsBoxForLazyLoad(commentsBox);
      try { setupCommentsSortControlForBox(commentsBox); } catch (_) {}  

      
      const wrap = document.createElement("div");
      wrap.className = "article-wrap";
      if (article.id != null) {
        wrap.setAttribute("data-article-id", String(article.id));
      }
      applyPublishedNewArticleDomMetadata(wrap, article);
      
      try {
        if (current && current.id != null) {
          wrap.setAttribute("data-current-version-id", String(current.id));
          wrap.setAttribute("data-version-id", String(current.id));
        }
      } catch (_) {}
      wrap.appendChild(artEl);
      wrap.appendChild(commentsBox);

      container.appendChild(wrap);
    });
  }

  

  function normalizeCommentSortMode(v) {
    const s = String(v || "").trim().toLowerCase();
    if (s === "approval" || s === "votes" || s === "published" || s === "newest") return s;
    return "newest";
  }

  function sortKeyForArticleVersion(articleId) {
    const aid = String(articleId || "");
    let vid = "";
    try {
      if (typeof getCurrentVersionIdForArticle === "function") {
        const v = getCurrentVersionIdForArticle(String(articleId));
        if (v != null) vid = String(v);
      }
    } catch (_) {}
    return aid + ":" + String(vid || "");
  }

  function readCommentsSortMap() {
    try {
      const raw = safeGetLS(UI_COMMENTS_SORT_STORAGE_KEY);
      const obj = raw ? JSON.parse(raw) : {};
      return (obj && typeof obj === "object") ? obj : {};
    } catch (_) {
      return {};
    }
  }

  function writeCommentsSortMap(map) {
    try { safeSetLS(UI_COMMENTS_SORT_STORAGE_KEY, JSON.stringify(map || {})); } catch (_) {}
  }

  function getCommentSortMode(articleId) {
    const k = sortKeyForArticleVersion(articleId);
    const map = readCommentsSortMap();
    return normalizeCommentSortMode(map[k] || "newest");
  }

  function setCommentSortMode(articleId, mode) {
    const k = sortKeyForArticleVersion(articleId);
    const map = readCommentsSortMap();
    map[k] = normalizeCommentSortMode(mode);
    writeCommentsSortMap(map);
  }

  function getCommentFocusId(articleId) {
    const k = sortKeyForArticleVersion(articleId);
    try {
      const raw = safeGetLS(UI_COMMENT_FOCUS_STORAGE_KEY);
      const obj = raw ? JSON.parse(raw) : {};
      const val = obj && typeof obj === "object" ? obj[k] : "";
      return val != null ? String(val) : "";
    } catch (_) {
      return "";
    }
  }

  function setCommentFocusId(articleId, commentId) {
    const k = sortKeyForArticleVersion(articleId);
    try {
      const raw = safeGetLS(UI_COMMENT_FOCUS_STORAGE_KEY);
      const obj = raw ? JSON.parse(raw) : {};
      const map = (obj && typeof obj === "object") ? obj : {};
      const cid = commentId != null ? String(commentId).trim() : "";
      if (cid) map[k] = cid;
      else delete map[k];
      safeSetLS(UI_COMMENT_FOCUS_STORAGE_KEY, JSON.stringify(map));
    } catch (_) {}
  }

  function setupCommentsSortControlForBox(box) {
    if (!box || box.dataset.commentsSortWired === "1") return;
    box.dataset.commentsSortWired = "1";

    const aid = box.getAttribute("data-article-id");
    if (!aid) return;

    function rerenderLoaded() {
      try {
        const listEl = box.querySelector(".article-comments-list");
        if (!listEl || listEl.dataset.loaded !== "true") return;
        const cached = (window.__klimaggCommentsCacheByArticleId) ? window.__klimaggCommentsCacheByArticleId[String(aid)] : null;
        if (cached && Array.isArray(cached)) {
          renderCommentsIntoList(Number(aid), cached, listEl);
        }
      } catch (_) {}
    }

    function syncSortControls(mode) {
      const m = normalizeCommentSortMode(mode);
      const sel = box.querySelector(".article-comments-sort");
      if (sel) {
        try { sel.value = m; } catch (_) {}
      }
      box.querySelectorAll(".article-comments-sort-chip[data-sort]").forEach((btn) => {
        const on = normalizeCommentSortMode(btn.getAttribute("data-sort")) === m;
        btn.classList.toggle("is-active", on);
        btn.setAttribute("aria-pressed", on ? "true" : "false");
      });
    }

    syncSortControls(getCommentSortMode(aid));

    const sel = box.querySelector(".article-comments-sort");
    if (sel) {
      sel.addEventListener("change", () => {
        const mode = normalizeCommentSortMode(sel.value);
        setCommentSortMode(aid, mode);
        syncSortControls(mode);
        rerenderLoaded();
      });
    }

    box.querySelectorAll(".article-comments-sort-chip[data-sort]").forEach((btn) => {
      btn.addEventListener("click", () => {
        const mode = normalizeCommentSortMode(btn.getAttribute("data-sort"));
        setCommentSortMode(aid, mode);
        syncSortControls(mode);
        rerenderLoaded();
      });
    });

  }

  // Safe bridge from the early comment renderer to the later inline-diff module.
  // Access state only through window.KlimaGG.*; the internal IIFE state is private.
  function getInlineDiffApiSafe() {
    try {
      const api = window.KlimaGG && window.KlimaGG.inlineDiff;
      return (api && typeof api === "object") ? api : null;
    } catch (_) {
      return null;
    }
  }

  function getInlineDiffStateSafe() {
    try {
      const st = window.KlimaGG && window.KlimaGG.inlineDiffState;
      return (st && typeof st === "object") ? st : null;
    } catch (_) {
      return null;
    }
  }

  function getManualInlineDiffIdsSafe(articleId, versionId) {
    try {
      const st = getInlineDiffStateSafe();
      if (st && typeof st.getLayers === "function") {
        const arr = st.getLayers(articleId, versionId || undefined);
        return Array.isArray(arr) ? arr.map((x) => String(x)).filter(Boolean) : [];
      }
    } catch (_) {}
    return [];
  }

  function getNextDraftInlineDiffIdsSafe(articleId) {
    try {
      const st = getInlineDiffStateSafe();
      if (st && typeof st.getNextDraft === "function") {
        const arr = st.getNextDraft(articleId);
        return Array.isArray(arr) ? arr.map((x) => String(x)).filter(Boolean) : [];
      }
    } catch (_) {}
    return [];
  }

  function setManualInlineDiffIdsSafe(articleId, ids, versionId) {
    try {
      const st = getInlineDiffStateSafe();
      if (st && typeof st.setLayers === "function") {
        st.setLayers(articleId, mergeRequiredInlineDiffIds(articleId, Array.isArray(ids) ? ids : []), versionId || undefined);
        return true;
      }
    } catch (_) {}
    return false;
  }

  function clearManualInlineDiffIdsSafe(articleId, versionId) {
    try {
      const st = getInlineDiffStateSafe();
      if (st && typeof st.clearLayers === "function") {
        const required = getRequiredPublishedNewArticleCommentIdsForArticleId(articleId);
        if (required.length) st.setLayers(articleId, required, versionId || undefined);
        else st.clearLayers(articleId, versionId || undefined);
        return true;
      }
    } catch (_) {}
    return false;
  }

  function setNextDraftInlineDiffIdsSafe(articleId, ids) {
    try {
      const st = getInlineDiffStateSafe();
      if (st && typeof st.setNextDraft === "function") {
        st.setNextDraft(articleId, Array.isArray(ids) ? ids : []);
        return true;
      }
    } catch (_) {}
    return false;
  }

  function clearNextDraftInlineDiffIdsSafe(articleId) {
    try {
      const st = getInlineDiffStateSafe();
      if (st && typeof st.clearNextDraft === "function") {
        st.clearNextDraft(articleId);
        return true;
      }
    } catch (_) {}
    return false;
  }

  function clearInlineDiffStateForArticleSafe(articleId, versionId) {
    try { clearManualInlineDiffIdsSafe(articleId, versionId); } catch (_) {}
    try {
      const st = getInlineDiffStateSafe();
      if (st && typeof st.clearHighlights === "function") st.clearHighlights(articleId, versionId || undefined);
    } catch (_) {}
    try { clearNextDraftInlineDiffIdsSafe(articleId); } catch (_) {}
    try { enforcePublishedNewArticleInlineDiff(articleId, versionId); } catch (_) {}
  }

  async function refreshInlineDiffSafe(articleId, versionId, options) {
    const api = getInlineDiffApiSafe();
    if (api && typeof api.refresh === "function") {
      return api.refresh(articleId, versionId || undefined, options || {});
    }
    return false;
  }

  function getCommentBoxForElement(el) {
    try {
      return el && el.closest ? el.closest(".article-comments[data-article-id]") : null;
    } catch (_) {
      return null;
    }
  }

  async function withStableCommentBoxTop(el, fn) {
    const box = getCommentBoxForElement(el) || el;
    const beforeTop = (() => {
      try { return box && box.getBoundingClientRect ? box.getBoundingClientRect().top : null; } catch (_) {}
      return null;
    })();
    try {
      return await fn();
    } finally {
      if (beforeTop == null) continueStableCommentBoxTopNoop();
      try {
        requestAnimationFrame(() => {
          try {
            const afterTop = box && box.getBoundingClientRect ? box.getBoundingClientRect().top : null;
            if (afterTop == null) return;
            const delta = afterTop - beforeTop;
            if (Math.abs(delta) > 1) window.scrollBy(0, delta);
          } catch (_) {}
        });
      } catch (_) {}
    }
  }

  function continueStableCommentBoxTopNoop() {
    return false;
  }

  function rerenderCompactCommentsForElement(el) {
    const box = getCommentBoxForElement(el);
    if (!box) return false;
    const aid = String(box.getAttribute("data-article-id") || "").trim();
    if (!aid) return false;
    const listEl = box.querySelector(".article-comments-list");
    if (!listEl || listEl.dataset.loaded !== "true") return false;
    const cached = (window.__klimaggCommentsCacheByArticleId) ? window.__klimaggCommentsCacheByArticleId[aid] : null;
    if (!Array.isArray(cached)) return false;
    renderCommentsIntoList(Number(aid), cached, listEl);
    return true;
  }

  function rerenderAllLoadedCompactComments() {
    document.querySelectorAll(".article-comments[data-article-id]").forEach((box) => {
      try { rerenderCompactCommentsForElement(box); } catch (_) {}
    });
  }

  const __kggCommentResortTimers = {};
  function scheduleResortCommentsForArticle(articleId) {
    const aid = String(articleId || "");
    if (!aid) return;
    try { if (__kggCommentResortTimers[aid]) window.clearTimeout(__kggCommentResortTimers[aid]); } catch (_) {}
    __kggCommentResortTimers[aid] = window.setTimeout(() => {
      try { resortRenderedCommentsForArticle(aid); } catch (_) {}
    }, 140);
  }

  function resortRenderedCommentsForArticle(articleId) {
    const aid = String(articleId || "");
    if (!aid) return;
    const box = document.querySelector(`.article-comments[data-article-id="${cssEscape(aid)}"]`);
    if (!box) return;
    const listEl = box.querySelector(".article-comments-list");
    if (!listEl || listEl.dataset.loaded !== "true") return;
    const mode = getCommentSortMode(aid);
    if (mode !== "approval" && mode !== "votes") return; 
    const cached = (window.__klimaggCommentsCacheByArticleId) ? window.__klimaggCommentsCacheByArticleId[String(aid)] : null;
    if (cached && Array.isArray(cached)) {
      renderCommentsIntoList(Number(aid), cached, listEl);
    }
  }  
  
  function ensureCommentsBoxLoaded(commentsBox) {
    if (!commentsBox) return;
    const articleId = Number(commentsBox.getAttribute("data-article-id"));
    if (!Number.isFinite(articleId)) return;
    const listEl = commentsBox.querySelector(".article-comments-list");
    if (!listEl) return;
    if (listEl.dataset.loaded === "true" || listEl.dataset.loaded === "loading") return;
    loadCommentsForArticle(articleId, listEl);
  }

  function getCurrentVersionIdSafe(articleId, root) {
    try {
      if (typeof getCurrentVersionIdForArticle === "function") {
        const v = getCurrentVersionIdForArticle(String(articleId || ""));
        if (v != null) {
          const n = Number(v);
          if (Number.isFinite(n) && n > 0) return n;
        }
      }
    } catch (_) {}
    try {
      let raw = root ? (
        root.getAttribute("data-version-id") ||
        root.getAttribute("data-current-version-id") ||
        (root.dataset ? (root.dataset.versionId || root.dataset.currentVersionId) : "")
      ) : "";
      if ((!raw || !String(raw).trim()) && root && typeof root.querySelector === "function") {
        const aid = String(articleId || "");
        const inner = root.querySelector('article[data-article-id="' + aid + '"]');
        if (inner) {
          raw = inner.getAttribute("data-version-id") ||
                inner.getAttribute("data-current-version-id") ||
                (inner.dataset ? (inner.dataset.versionId || inner.dataset.currentVersionId) : "") ||
                raw;
        }
      }
      const n = Number(raw);
      if (Number.isFinite(n) && n > 0) return n;
    } catch (_) {}
    return null;
  }

  function store() {
    if (!window.__kggMergePreviewStore) window.__kggMergePreviewStore = { byKey: {}, byAid: {} };
    if (!window.__kggMergePreviewStore.byKey) window.__kggMergePreviewStore.byKey = {};
    if (!window.__kggMergePreviewStore.byAid) window.__kggMergePreviewStore.byAid = {};
    return window.__kggMergePreviewStore;
  }

  function ensureRec(recKey) {
    const s = store();
    const k = String(recKey || "");
    if (!s.byKey[k]) {
      s.byKey[k] = {
        orig: {},
        conflictByCid: {},
        conflictByBlock: {},
        conflictReasonByCid: {},
        conflictPartsByCid: {},
      };
    }
    return s.byKey[k];
  }

  function inlineDiffRecordDataScore(rec) {
    if (!rec || typeof rec !== "object") return 0;
    let score = 0;
    try {
      const cards = rec.backendCommentCardsByCid && typeof rec.backendCommentCardsByCid === "object"
        ? Object.keys(rec.backendCommentCardsByCid).length
        : 0;
      const rows = rec.backendCommentCardRowsByCid && typeof rec.backendCommentCardRowsByCid === "object"
        ? Object.keys(rec.backendCommentCardRowsByCid).length
        : 0;
      const blocks = rec.backendMergeBlocksByKey && typeof rec.backendMergeBlocksByKey === "object"
        ? Object.keys(rec.backendMergeBlocksByKey).length
        : 0;
      const payload = rec.backendMergePayload && typeof rec.backendMergePayload === "object"
        ? rec.backendMergePayload
        : {};
      const payloadBlocks = payload.indiffBlockResults && typeof payload.indiffBlockResults === "object"
        ? Object.keys(payload.indiffBlockResults).length
        : 0;
      score += cards * 10;
      score += rows * 6;
      score += blocks * 20;
      score += payloadBlocks * 25;
      if (payload.inlineEngineIndiff === true) score += 5;
      if (rec.backendMergePromise) score += 1;
    } catch (_) {}
    return score;
  }

  function getInlineDiffRecForRead(articleId, versionIdOverride) {
    const scope = resolveInlineDiffScope(articleId, versionIdOverride);
    return ensureRec(scope.scopeKey);
  }

  function resolveInlineDiffScope(articleId, versionIdOverride) {
    const aid = String(articleId || "").trim();
    const rawVid = (versionIdOverride == null ? "" : String(versionIdOverride)).trim();
    try {
      const api = (window.KlimaGG && window.KlimaGG.inlineDiff) ? window.KlimaGG.inlineDiff : null;
      if (api && typeof api.getScope === "function") {
        const scope = api.getScope(aid, rawVid || undefined);
        if (scope && typeof scope === "object") return scope;
      }
    } catch (_) {}
    try {
      if (typeof getInlineDiffScope === "function") {
        const scope = getInlineDiffScope(aid, rawVid || undefined);
        if (scope && typeof scope === "object") return scope;
      }
    } catch (_) {}
    const versionId = rawVid || "";
    return {
      articleId: aid,
      versionId: versionId,
      scopeKey: String(aid) + ":" + String(versionId),
    };
  }

  function getInlineDiffApiOrThrow() {
    const api = (window.KlimaGG && window.KlimaGG.inlineDiff) ? window.KlimaGG.inlineDiff : null;
    if (!api || typeof api.toggleDraft !== "function" || typeof api.refresh !== "function" || typeof api.clear !== "function") {
      throw new ReferenceError("INLINE_DIFF_API unavailable via window.KlimaGG.inlineDiff");
    }
    return api;
  }

  async function syncInlineDiffFromRenderedFlagsForArticle(articleId, versionIdOverride, options) {
    const aid = String(articleId || "").trim();
    if (!aid) return false;
    const inlineApi = getInlineDiffApiOrThrow();
    const scope = resolveInlineDiffScope(aid, versionIdOverride);
    const root =
      document.querySelector('.article-comments[data-article-id="' + cssEscape(aid) + '"]') ||
      null;
    if (!root) return false;

    const activeButtons = Array.from(
      root.querySelectorAll('.comment-flag-btn.active[data-flag="🚩"], .comment-flag-btn.active[data-emoji="🚩"]')
    );
    const wanted = Array.from(new Set(activeButtons.map((btn) => {
      try {
        const bar = btn.closest('.comment-vote-bar');
        const cid =
          (bar && (bar.getAttribute('data-comment-id') || (bar.dataset ? bar.dataset.commentId : ''))) ||
          '';
        return String(cid || '').trim();
      } catch (_) {
        return '';
      }
    }).filter(Boolean)));

    try {
      if (wanted.length) {
        setCommentOverlayModeAndSync("on");
      }
    } catch (_) {}

    const current = (() => {
      try {
        const arr = inlineApi && typeof inlineApi.getActiveIds === "function"
          ? inlineApi.getActiveIds(aid, scope.versionId || undefined)
          : [];
        return Array.isArray(arr) ? arr.map((x) => String(x)) : [];
      } catch (_) {
        return [];
      }
    })();

    let changed = false;

    current.forEach((cid) => {
      if (!wanted.includes(String(cid))) {
        try {
          inlineApi.setActive(aid, cid, false, scope.versionId || undefined);
          changed = true;
        } catch (_) {}
      }
    });

    wanted.forEach((cid) => {
      if (!current.includes(String(cid))) {
        try {
          inlineApi.setActive(aid, cid, true, scope.versionId || undefined);
          changed = true;
        } catch (_) {}
      }
    });

    const force = !!(options && options.forceRefresh);
    if (changed || force) {
      await inlineApi.refresh(aid, scope.versionId || undefined, { source: "syncInlineDiffFromRenderedFlagsForArticle" });
      return true;
    }
    return false;
  }

  const commentPrivateOverlayState = {
    review: null,
    draft: null,
  };

  function _privateOverlayCommentId(commentObj) {
    return String(commentObj && commentObj.id != null ? commentObj.id : "").trim();
  }

  function _privateOverlayArticleId(commentObj) {
    return String(commentObj && commentObj.article_id != null ? commentObj.article_id : "").trim();
  }

  function _cachePrivateOverlayComment(commentObj) {
    const cid = _privateOverlayCommentId(commentObj);
    if (!cid || !commentObj || typeof commentObj !== "object") return;
    try {
      window.__klimaggCommentCacheById = window.__klimaggCommentCacheById || {};
      window.__klimaggCommentCacheById[cid] = commentObj;
    } catch (_) {}
  }

  function _privateOverlayCommentsForArticle(articleId) {
    const aid = String(articleId || "").trim();
    if (!aid) return [];
    const out = [];
    [commentPrivateOverlayState.draft, commentPrivateOverlayState.review].forEach((entry) => {
      const c = entry && entry.comment && typeof entry.comment === "object" ? entry.comment : null;
      if (!c) return;
      if (_privateOverlayArticleId(c) !== aid) return;
      const cid = _privateOverlayCommentId(c);
      if (!cid) return;
      out.push(Object.assign({}, c, {
        _klimagg_private_overlay: true,
        _klimagg_private_overlay_reason: String(entry.reason || "private"),
      }));
    });
    return out;
  }

  function mergePrivateOverlayCommentsForArticle(articleId, comments) {
    const base = Array.isArray(comments) ? comments.slice() : [];
    const overlays = _privateOverlayCommentsForArticle(articleId);
    if (!overlays.length) return base;
    const seen = new Set();
    const out = [];
    overlays.concat(base).forEach((c) => {
      const cid = _privateOverlayCommentId(c);
      if (cid) {
        if (seen.has(cid)) return;
        seen.add(cid);
      }
      out.push(c);
    });
    return out;
  }

  function refreshCommentListPrivateOverlay(articleId) {
    const aid = String(articleId || "").trim();
    if (!aid) return;
    try {
      const box = document.querySelector('.article-comments[data-article-id="' + cssEscape(aid) + '"]');
      const listEl = box ? box.querySelector(".article-comments-list") : null;
      if (!listEl || listEl.dataset.loaded !== "true") return;
      const cache = window.__klimaggCommentsCacheByArticleId || {};
      const rows = Array.isArray(cache[aid]) ? cache[aid] : [];
      const aidNum = Number(aid);
      renderCommentsIntoList(Number.isFinite(aidNum) ? aidNum : aid, rows, listEl);
    } catch (_) {}
  }

  function setPrivateCommentOverlay(reason, commentObj, options) {
    const r = String(reason || "private").trim() || "private";
    const c = commentObj && typeof commentObj === "object" ? commentObj : null;
    if (!c || c.id == null || c.article_id == null) return false;
    const entry = { reason: r, comment: c };
    if (r === "review") commentPrivateOverlayState.review = entry;
    else if (r === "draft") commentPrivateOverlayState.draft = entry;
    else commentPrivateOverlayState[r] = entry;
    _cachePrivateOverlayComment(c);
    try { setCommentFocusId(c.article_id, String(c.id)); } catch (_) {}
    if (!options || options.refresh !== false) refreshCommentListPrivateOverlay(c.article_id);
    return true;
  }

  function clearPrivateCommentOverlay(reason, options) {
    const r = String(reason || "").trim();
    const articleIds = new Set();
    const collect = (entry) => {
      const c = entry && entry.comment ? entry.comment : null;
      const aid = _privateOverlayArticleId(c);
      if (aid) articleIds.add(aid);
    };
    if (r) {
      collect(commentPrivateOverlayState[r]);
      if (Object.prototype.hasOwnProperty.call(commentPrivateOverlayState, r)) commentPrivateOverlayState[r] = null;
    } else {
      Object.keys(commentPrivateOverlayState).forEach((key) => {
        collect(commentPrivateOverlayState[key]);
        commentPrivateOverlayState[key] = null;
      });
    }
    if (!options || options.refresh !== false) {
      articleIds.forEach((aid) => refreshCommentListPrivateOverlay(aid));
    }
  }

  async function ensurePrivateOverlayCommentRendered(articleId, commentId, options) {
    const aid = String(articleId || "").trim();
    const cid = String(commentId || "").trim();
    const opts = (options && typeof options === "object") ? options : {};
    if (!aid || !cid) return false;

    const box = document.querySelector('.article-comments[data-article-id="' + cssEscape(aid) + '"]');
    if (!box) return false;

    try { box.hidden = false; } catch (_) {}

    
    
    
    try {
      await deactivateVersionLayerForCommentAction();
      const prefs = getCommentsVisibilityPrefs();
      if (!prefs.showComments) {
        setCommentsVisibilityPref(true);
        applyCommentsVisibilityToAllRenderedArticles();
      }
    } catch (_) {}

    const listEl = box.querySelector(".article-comments-list");
    if (!listEl) return false;

    if (listEl.dataset.loaded !== "true" && listEl.dataset.loaded !== "loading") {
      try { await loadCommentsForArticle(aid, listEl); } catch (_) {}
    } else if (listEl.dataset.loaded === "true") {
      try { refreshCommentListPrivateOverlay(aid); } catch (_) {}
    }

    await Promise.resolve();
    try { setCommentFocusId(aid, cid); } catch (_) {}

    if (opts.markReviewTarget) {
      try { markReviewTargetComment(cid); } catch (_) {}
    }

    if (opts.scroll !== false) {
      const target = document.getElementById("comment-" + cid);
      if (target) {
        try { target.scrollIntoView({ behavior: "smooth", block: "center" }); } catch (_) {}
      }
    }
    return !!document.getElementById("comment-" + cid);
  }

  async function loadCommentsForArticle(articleId, listEl) {
    if (!articleId || !listEl) return;

    listEl.dataset.loaded = "loading";
    renderAsyncState(listEl, {
      kind: "loading",
      message: "Lade Kommentare …",
    });

    try {
      const data = await apiFetchJson(
        `/api/articles/${articleId}/comments`,
        {},
        { authRequired: !!klimaggAuth.accessToken }
      );
      if (!Array.isArray(data)) {
        console.warn(
          "[KlimaGG] /api/articles/{id}/comments liefert kein Array:",
          data
        );
        renderAsyncState(listEl, {
          kind: "error",
          message: "Kommentare konnten nicht geladen werden.",
          onRetry: () => loadCommentsForArticle(articleId, listEl),
        });
        listEl.dataset.loaded = "";
        return;
      }

      try {
        window.__klimaggCommentsCacheByArticleId = window.__klimaggCommentsCacheByArticleId || {};
        window.__klimaggCommentsCacheByArticleId[String(articleId)] = data;
      } catch (_) {}

      try {
        const root =
          findArticleRoot(articleId) ||
          document.querySelector('article[data-article-id="' + cssEscape(String(articleId)) + '"]') ||
          document.querySelector('.article-wrap[data-article-id="' + cssEscape(String(articleId)) + '"]') ||
          null;
        const versionId = getCurrentVersionIdSafe(articleId, root) || "";
        const scope = resolveInlineDiffScope(articleId, versionId || "");
        const rec = ensureRec(scope.scopeKey);
        const allCommentIds = mergePrivateOverlayCommentsForArticle(articleId, data)
          .map((c) => Number(c && c.id))
          .filter((n) => Number.isFinite(n) && n > 0)
          .map((n) => String(n));
        if (allCommentIds.length) {
          const payload = await fetchInlineDiffPayload(articleId, allCommentIds);
          updateInlineDiffCommentCardCache(rec, payload, { replace: true });
        }
      } catch (_) {}

      renderCommentsIntoList(articleId, data, listEl);
      listEl.dataset.loaded = "true";
      
      
    } catch (err) {
      console.error(
        "[KlimaGG] Fehler beim Laden der Kommentare für Artikel",
        articleId,
        err
      );
      renderAsyncState(listEl, {
        kind: "error",
        message: "Kommentare konnten nicht geladen werden.",
        onRetry: () => loadCommentsForArticle(articleId, listEl),
      });
      listEl.dataset.loaded = "";
    }
  }

  function refreshCommentsForArticle(articleId) {
    if (!articleId) return;
    const box = document.querySelector(
      `.article-comments[data-article-id="${articleId}"]`
    );
    if (!box) return;
    const listEl = box.querySelector(".article-comments-list");
    if (!listEl) return;
    // Reload erzwingen
    listEl.dataset.loaded = "";
    loadCommentsForArticle(articleId, listEl);
  }

  const KGG_RELATED_COMMENTS_KEY = "related_comments";

  function getRelatedCommentsPayload(comment) {
    const sp = comment && comment.structure_payload && typeof comment.structure_payload === "object" ? comment.structure_payload : null;
    const rel = sp && sp[KGG_RELATED_COMMENTS_KEY] && typeof sp[KGG_RELATED_COMMENTS_KEY] === "object" ? sp[KGG_RELATED_COMMENTS_KEY] : null;
    return rel && rel.enabled ? rel : null;
  }

  function extractCommentIdsFromRelatedText(text) {
    const s = String(text || "");
    const out = [];
    const re = /(?:Kommentar\s*)?#(\d+)|comment[_-]?(?:id)?[=:_-]?(\d+)/gi;
    let m;
    while ((m = re.exec(s)) !== null) {
      const n = Number(m[1] || m[2]);
      if (Number.isFinite(n) && n > 0 && !out.includes(n)) out.push(n);
    }
    return out;
  }

  function linkedCommentRefHtml(commentId) {
    const cid = Number(commentId);
    if (!Number.isFinite(cid) || cid < 1) return "";
    return '<a href="#comment-' + escapeHtml(String(cid)) + '" data-related-comment-jump="' + escapeHtml(String(cid)) + '">#' + escapeHtml(String(cid)) + '</a>';
  }

  function linkifyRelatedCommentRefs(rawText) {
    const escaped = escapeHtml(String(rawText || ""));
    return escaped.replace(/(?:Kommentar\s*)?#(\d+)|comment[_-]?(?:id)?[=:_-]?(\d+)/gi, (full, a, b) => {
      const n = Number(a || b);
      if (!Number.isFinite(n) || n < 1) return full;
      const label = full.toLowerCase().includes("kommentar") ? ("Kommentar #" + String(n)) : ("#" + String(n));
      return '<a href="#comment-' + escapeHtml(String(n)) + '" data-related-comment-jump="' + escapeHtml(String(n)) + '">' + escapeHtml(label) + '</a>';
    }).replace(/\n/g, "<br>");
  }

  function renderRelatedCommentsSection(c) {
    const rel = getRelatedCommentsPayload(c);
    if (!rel) return "";
    const title = String(rel.title || "Zusammenhängender Vorschlag").trim();
    const note = String(rel.note || "").trim();
    const sources = String(rel.sources || "").trim();
    const ownIds = Array.isArray(rel.own_comment_ids) ? rel.own_comment_ids : [];
    const mentioned = extractCommentIdsFromRelatedText([note, sources].join("\n"));
    const ids = Array.from(new Set([].concat(ownIds, mentioned).map((x) => Number(x)).filter((x) => Number.isFinite(x) && x > 0)));
    const parts = [];
    parts.push('<div class="article-comment-section article-comment-related-section">');
    parts.push('<div class="section-title">🔗 Zusammenhang</div>');
    parts.push('<div class="section-body">');
    parts.push('<div class="article-comment-related-title">' + escapeHtml(title || "Zusammenhängender Vorschlag") + '</div>');
    if (note) parts.push('<div class="article-comment-related-note">' + linkifyRelatedCommentRefs(note) + '</div>');
    if (ids.length) {
      parts.push('<div class="article-comment-related-links klein">Verlinkt: ' + ids.map((id) => linkedCommentRefHtml(id)).filter(Boolean).join(" · ") + '</div>');
    }
    if (sources) {
      parts.push('<div class="article-comment-related-sources klein"><b>Gemeinsame Quellen/Hinweise:</b><br>' + sourcesToHtml(sources) + '</div>');
    }
    if (rel.integrate_as_bundle) {
      parts.push('<div class="article-comment-related-bundle klein">⚠ Hinweis des Autors: nur gemeinsam mit den verbundenen eigenen Kommentaren integrieren.</div>');
    }
    parts.push('</div></div>');
    return parts.join("");
  }

  function sourcesToHtml(sourcesText) {
    const s = String(sourcesText || "").trim();
    if (!s) return "";

    
    const lines = s.split(/\r?\n/).map((x) => x.trim()).filter(Boolean);
    const urlRe = /(https?:\/\/[^\s<>"]+)/g;

    const items = lines.map((line) => {
      const safe = escapeHtml(line);
      const html = safe.replace(urlRe, (m) => {
        const u = escapeHtml(m);
        return '<a href="' + u + '" target="_blank" rel="noopener noreferrer">' + u + '</a>';
      });
      return '<li>' + html + '</li>';
    });

    return '<ul class="article-comment-sources-list">' + items.join("") + '</ul>';
  }


// -------------------------------------------------------------------------

// WICHTIG:



// -------------------------------------------------------------------------

const KGG_TOP_LEVEL_MINIMD_BLOCK_KEYS = [
  "meta",
  "kurzinfo",
  "story",
  "einleitung",
  "juristisch",
  "juristisch2",
  "anmerkung",
];

function parseMiniMdBlocks(minimdText) {
  const text = String(minimdText || "").replace(/\r\n/g, "\n");
  const out = {};
  for (const rawKey of KGG_TOP_LEVEL_MINIMD_BLOCK_KEYS) {
    const key = String(rawKey || "").trim();
    if (!key) continue;
    const esc = key.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    const re = new RegExp(
      `^### start: ${esc} ###\\n([\\s\\S]*?)^### end: ${esc} ###$`,
      "m"
    );
    const m = text.match(re);
    out[key] = m ? (m[1] || "") : "";
  }
  return out;
}

function kggFilterTopLevelMiniMdBlocks(blocks) {
  const src = blocks && typeof blocks === "object" ? blocks : {};
  const out = {};
  KGG_TOP_LEVEL_MINIMD_BLOCK_KEYS.forEach((k) => { out[k] = String(src[k] ?? ""); });
  return out;
}

function kggNormalizeMiniMdBlockBodyForPatch(blockText) {
  let s = String(blockText ?? "").replace(/\r\n/g, "\n").replace(/\r/g, "\n");
  if (s.endsWith("\n")) s = s.slice(0, -1);
  return s;
}

function kggReplaceMiniMdBlockBody(minimdText, blockKey, newBodyText) {
  const src = String(minimdText || "").replace(/\r\n/g, "\n").replace(/\r/g, "\n");
  const key = String(blockKey || "").trim();
  const body = String(newBodyText ?? "").replace(/\r\n/g, "\n").replace(/\r/g, "\n").replace(/\n$/, "");
  const re = new RegExp(`(^### start: ${key.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")} ###\\n)([\\s\\S]*?)(^### end: ${key.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")} ###$)`, "m");
  return src.replace(re, (_m, p1, _oldBody, p3) => body ? (p1 + body + "\n" + p3) : (p1 + p3));
}

// Global strict-capable MiniMD splitter. SaveDraft must not depend on helpers local
// to the preview path.
function kggSplitMiniMdBlocks(minimdText) {
  const s = String(minimdText || '').replace(/\r\n/g, "\n").replace(/\r/g, "\n");
  const hasMarkers = /###\s*start\s*:\s*[^#\n]+###/i.test(s) && /###\s*end\s*:\s*[^#\n]+###/i.test(s);
  const blocksRaw = (typeof parseMiniMdBlocks === "function") ? (parseMiniMdBlocks(s) || {}) : {};
  const blocks = {};
  for (const k0 of Object.keys(blocksRaw)) {
    const keyRaw = String(k0 || "").trim().toLowerCase();
    const key = (keyRaw === "anmerkungen") ? "anmerkung" : keyRaw;
    blocks[key] = String(blocksRaw[k0] ?? "");
  }
  return { hasMarkers, blocks };
}

function kggContentBlockSelectorByKey(blockKey) {
  const k = normalizeBlockKey(blockKey);
  if (!k) return ".content-block.juristisch";
  return ".content-block." + k;
}

function normalizeBlockKey(rawKey) {
  const k = String(rawKey || '').trim().toLowerCase();
  if (!k) return null;
  // Backend compatibility: `_all` is not a renderable article block; map it to the legal block.
  if (k === '_all' || k === 'all') return 'juristisch';
  if (k.includes('kurz')) return 'kurzinfo';
  if (k.includes('story')) return 'story';
  if (k.includes('einleit')) return 'einleitung';
  if (k.includes('juristisch2')) return 'juristisch2';
  if (k.includes('juristisch')) return 'juristisch';
  if (k === 'anmerkungen') return 'anmerkung';
  if (k.includes('anmerk')) return 'anmerkung';
  return k;
}

  // Next-Draft and Last-Version layers are mounted exclusively from the canonical
  // backend renderer based on indiff.py.

  // Best-effort navigation to a comment: locate the article, ensure its comment list
  // is loaded and visible, then scroll to the requested comment element.
  window.klimaggJumpToCommentInArticle = async function ({ articleId = null, commentId = null } = {}) {
    const aid = Number(articleId);
    const cid = Number(commentId);
    if (!Number.isFinite(aid) || !Number.isFinite(cid)) return false;

    const articleSelector = 'article[data-article-id="' + cssEscape(String(aid)) + '"]';
    const wrapSelector = '.article-wrap[data-article-id="' + cssEscape(String(aid)) + '"]';
    const commentSelector = '#comment-' + cssEscape(String(cid)) + ', [data-comment-id="' + cssEscape(String(cid)) + '"]';

    
    
    const rootEl = document.querySelector(wrapSelector) || document.querySelector(articleSelector);
    if (rootEl) {
      try { rootEl.scrollIntoView({ behavior: "smooth", block: "start" }); } catch (_) {}
    }

    const box = document.querySelector('.article-comments[data-article-id="' + cssEscape(String(aid)) + '"]');
    if (!box) {
      notify("Kommentarbereich nicht gefunden.", { type: "warn" });
      return false;
    }
    const listEl = box.querySelector(".article-comments-list");
    if (!listEl) return false;

    
    try {
      await deactivateVersionLayerForCommentAction();
      const prefs = getCommentsVisibilityPrefs();
      if (!prefs.showComments) {
        setCommentsVisibilityPref(true);
        applyCommentsVisibilityToAllRenderedArticles();
      }
    } catch (_) {}

    // Kommentarbox sicher einblenden
    try { box.hidden = false; } catch (_) {}

    
    if (listEl.dataset.loaded !== "true" && listEl.dataset.loaded !== "loading") {
      try { await loadCommentsForArticle(aid, listEl); } catch (_) {}
    }
    if (listEl.dataset.loaded === "loading") {
      for (let i = 0; i < 10 && listEl.dataset.loaded === "loading"; i += 1) {
        await new Promise((resolve) => window.setTimeout(resolve, 120));
      }
    }

    
    let target = null;
    for (let i = 0; i < 6; i += 1) {
      await new Promise((resolve) => window.setTimeout(resolve, i === 0 ? 0 : 120));
      target = document.querySelector(commentSelector);
      if (target) break;
    }

    if (!target) {
      notify("Kommentar ist auf der Hauptseite nicht sichtbar (evtl. nicht veröffentlicht, archiviert oder gefiltert).", { type: "warn" });
      return false;
    }
    try {
      target.scrollIntoView({ behavior: "smooth", block: "center" });
      target.classList.add("comment-highlight");
      target.setAttribute("tabindex", "-1");
      target.focus({ preventScroll: true });
    } catch (_) {}
    return true;
  };
 

  function getAdminJumpFromLocation() {
    try {
      const params = new URLSearchParams(window.location.search || "");
      const mode = String(params.get("kgg_jump") || "").trim();
      if (mode !== "comment") return null;
      const articleId = Number.parseInt(String(params.get("article_id") || ""), 10);
      const commentId = Number.parseInt(String(params.get("comment_id") || ""), 10);
      if (!Number.isFinite(articleId) || articleId < 1 || !Number.isFinite(commentId) || commentId < 1) return null;
      return { articleId, commentId };
    } catch (_) {
      return null;
    }
  }

  function clearAdminJumpQueryFromLocation() {
    try {
      const url = new URL(window.location.href);
      url.searchParams.delete("kgg_jump");
      url.searchParams.delete("article_id");
      url.searchParams.delete("comment_id");
      window.history.replaceState(window.history.state, document.title, url.pathname + url.search + url.hash);
    } catch (_) {}
  }

  function scheduleAdminJumpFromLocation() {
    const jump = getAdminJumpFromLocation();
    if (!jump) return;
    const key = String(jump.articleId) + ":" + String(jump.commentId) + ":" + String(window.location.href || "");
    if (window.__klimaggAdminJumpScheduled === key) return;
    window.__klimaggAdminJumpScheduled = key;
    window.setTimeout(() => {
      Promise.resolve(window.klimaggJumpToCommentInArticle(jump))
        .catch((err) => console.warn("[KlimaGG] Admin-Kommentarsprung fehlgeschlagen:", err))
        .finally(() => { clearAdminJumpQueryFromLocation(); });
    }, 350);
  }

  /* ===========================
     Article Review Helpers
     - Force full blocks for one article
     - Show inline comment diff overlay (strong)
     =========================== */

  function findArticleRootById(articleId) {
    const id = String(articleId);
    const candidates = [];
    try { candidates.push(...Array.from(document.querySelectorAll(`.article-wrap[data-article-id="${id}"], article[data-article-id="${id}"]`))); } catch (_) {}
    try { const a = document.getElementById(`article-${id}`); if (a) candidates.push(a); } catch (_) {}
    try { const a = document.getElementById(`a-${id}`); if (a) candidates.push(a); } catch (_) {}
    try { const a = document.querySelector(`.article[data-id="${id}"]`); if (a) candidates.push(a); } catch (_) {}

    if (!candidates.length) return null;

    // Prefer: visible + contains one of the canonical content-block containers
    const score = (el) => {
      let s = 0;
      try {
        const r = el.getBoundingClientRect();
        if (r && r.width > 0 && r.height > 0) s += 10;
        // visible-ish
        if (r && r.top < (window.innerHeight || 0) && r.bottom > 0) s += 5;
      } catch (_) {}
      try {
        for (const k of KGG_TOP_LEVEL_MINIMD_BLOCK_KEYS) {
          const selector = kggContentBlockSelectorByKey(k);
          if (selector && el.querySelector(selector)) { s += 20; break; }
        }
      } catch (_) {}
      return s;
    };

    let best = candidates[0], bestS = -1;
    for (const el of candidates) {
      const sc = score(el);
      if (sc > bestS) { bestS = sc; best = el; }
    }
    return best || null;
  }

  function forceAllBlocksForReview(articleId) {
    const root = findArticleRootById(articleId);
    if (!root) return;
    root.classList.add('review-force-all-blocks');
  }

  function restoreBlocksAfterReview(articleId) {
    const root = findArticleRootById(articleId);
    if (!root) return;
    root.classList.remove('review-force-all-blocks');
  }

  function markReviewTargetComment(commentId) {
    document.querySelectorAll('.review-target').forEach(el => el.classList.remove('review-target'));
    const cid = String(commentId);
    const el =
      document.querySelector(`[data-comment-id="${cid}"]`) ||
      document.getElementById(`comment-${cid}`) ||
      null;
    if (el) {
      el.classList.add('review-target');
      try {
        el.scrollIntoView({ block: 'center', behavior: 'smooth' });
      } catch (_) {}
    }
  }

  
  window.klimaggBeginInlineReview = function({ articleId = null, commentId = null } = {}) {
    if (articleId != null) {
      forceAllBlocksForReview(articleId);
    }
    if (commentId != null) {
      markReviewTargetComment(commentId);
      
      if (articleId != null && typeof window.klimaggJumpToCommentInArticle === 'function') {
        window.klimaggJumpToCommentInArticle({ articleId, commentId });
      }
    }
  };

  window.klimaggEndInlineReview = function({ articleId = null } = {}) {
    if (articleId != null) {
      restoreBlocksAfterReview(articleId);
    }
    document.querySelectorAll('.review-target').forEach(el => el.classList.remove('review-target'));
  };

  // Debug/Tests
  window.klimaggForceAllBlocksForReview = forceAllBlocksForReview;
  window.klimaggRestoreBlocksAfterReview = restoreBlocksAfterReview;
  window.klimaggMarkReviewTargetComment = markReviewTargetComment;

  const KGG_COMMENT_LAYER_BLOCKS = {
    kurzinfo: { label: "Kurzinfo", checkboxId: "toggle-kurzinfo" },
    story: { label: "Story", checkboxId: "toggle-story" },
    einleitung: { label: "Einleitung", checkboxId: "toggle-einleitung" },
    juristisch: { label: "Juristisch", checkboxId: "toggle-juristisch" },
    juristisch2: { label: "Rechtsverordnungen", checkboxId: "toggle-juristisch2" },
    anmerkung: { label: "Anmerkung", checkboxId: "toggle-anmerkung" },
  };

  function normalizeCommentLayerBlockKey(v) {
    const k = String(v || "").trim().toLowerCase();
    if (!k) return "";
    if (k === "kurz" || k === "kurzinfo") return "kurzinfo";
    if (k === "story") return "story";
    if (k === "einleitung") return "einleitung";
    if (k === "juristisch2" || k === "rechtsverordnungen") return "juristisch2";
    if (k === "juristisch") return "juristisch";
    if (k === "anmerkung") return "anmerkung";
    return "";
  }

  function getCommentChangedLayerBlockKeys(comment) {
    const out = [];
    const seen = new Set();
    const add = (raw) => {
      const key = normalizeCommentLayerBlockKey(raw);
      if (!key || seen.has(key)) return;
      seen.add(key);
      out.push(key);
    };
    try {
      const parts = comment && comment.patch_payload && Array.isArray(comment.patch_payload.parts)
        ? comment.patch_payload.parts
        : [];
      parts.forEach((p) => add(p && (p.block_key || p.block_id)));
    } catch (_) {}
    try {
      const rows = comment && Array.isArray(comment.comment_cards_table) ? comment.comment_cards_table : [];
      rows.forEach((r) => add(r && r.block_key));
    } catch (_) {}
    return out;
  }

  function getHiddenLayerBlockKeysForComments(comments) {
    const out = [];
    const seen = new Set();
    (Array.isArray(comments) ? comments : []).forEach((comment) => {
      getCommentChangedLayerBlockKeys(comment).forEach((key) => {
        const meta = KGG_COMMENT_LAYER_BLOCKS[key];
        if (!meta || seen.has(key)) return;
        const cb = document.getElementById(meta.checkboxId);
        if (cb && !cb.checked) {
          seen.add(key);
          out.push(key);
        }
      });
    });
    return out;
  }

  function setTextLayerBlockKeysVisible(blockKeys) {
    let changed = false;
    (Array.isArray(blockKeys) ? blockKeys : []).forEach((raw) => {
      const key = normalizeCommentLayerBlockKey(raw);
      const meta = key ? KGG_COMMENT_LAYER_BLOCKS[key] : null;
      if (!meta) return;
      const cb = document.getElementById(meta.checkboxId);
      if (!cb || cb.checked) return;
      cb.checked = true;
      changed = true;
      try { cb.dispatchEvent(new Event("change", { bubbles: true })); } catch (_) {}
    });
    return changed;
  }

  function updateHiddenLayerHintForCommentsBox(box, comments, focusedComment, activeIds) {
    if (!box) return;
    const hint = box.querySelector(".article-comments-hidden-layer-hint");
    if (!hint) return;
    const ids = new Set((Array.isArray(activeIds) ? activeIds : []).map((x) => String(x)).filter(Boolean));
    const relevant = [];
    if (ids.size) {
      (Array.isArray(comments) ? comments : []).forEach((c) => {
        const cid = String(c && c.id != null ? c.id : "");
        if (cid && ids.has(cid)) relevant.push(c);
      });
    }
    if (!relevant.length && focusedComment) relevant.push(focusedComment);

    const hiddenKeys = getHiddenLayerBlockKeysForComments(relevant);
    if (!hiddenKeys.length) {
      hint.hidden = true;
      hint.innerHTML = "";
      delete hint.dataset.blockKeys;
      return;
    }
    const labels = hiddenKeys.map((key) => KGG_COMMENT_LAYER_BLOCKS[key] && KGG_COMMENT_LAYER_BLOCKS[key].label || key);
    const subject = ids.size ? "Aktive Kommentare betreffen" : "Dieser Kommentar betrifft";
    hint.dataset.blockKeys = hiddenKeys.join(",");
    hint.hidden = false;
    hint.innerHTML =
      '<span class="article-comments-hidden-layer-hint-text">' +
      escapeHtml(subject + " ausgeblendete Textebenen: " + labels.join(", ") + ".") +
      '</span> ' +
      '<button class="article-comments-hidden-layer-show" type="button">Einblenden</button>';
  }

function extractInlineDiffCommentCardHtmlFromComment(comment) {
  const c = comment && typeof comment === "object" ? comment : {};
  const cid = String(c.id || "").trim();
  try {
    if (typeof c.comment_card_html === "string" && c.comment_card_html.trim()) return c.comment_card_html;
  } catch (_) {}
  const payload = c.patch_payload && typeof c.patch_payload === "object" ? c.patch_payload : {};
  try {
    if (typeof payload.comment_card_html === "string" && payload.comment_card_html.trim()) return payload.comment_card_html;
  } catch (_) {}
  try {
    const byCid = payload.comment_cards_by_cid_html && typeof payload.comment_cards_by_cid_html === "object"
      ? payload.comment_cards_by_cid_html
      : {};
    if (cid && typeof byCid[cid] === "string" && byCid[cid].trim()) return byCid[cid];
  } catch (_) {}
  return "";
}

function seedInlineDiffCommentCardCacheFromComments(rec, comments) {
  if (!rec || typeof rec !== "object" || !Array.isArray(comments)) return 0;
  const nextCards = rec.backendCommentCardsByCid && typeof rec.backendCommentCardsByCid === "object"
    ? Object.assign({}, rec.backendCommentCardsByCid)
    : {};
  let changed = 0;
  comments.forEach((comment) => {
    try {
      const cid = String(comment && comment.id || "").trim();
      if (!cid) return;
      if (String(nextCards[cid] || "").trim()) return;
      const html = extractInlineDiffCommentCardHtmlFromComment(comment);
      if (!html) return;
      nextCards[cid] = String(html);
      changed += 1;
    } catch (_) {}
  });
  if (changed > 0) rec.backendCommentCardsByCid = nextCards;
  return changed;
}


function renderCommentsIntoList(articleId, comments, listEl) {
  const aidNum = Number(articleId);
  const aid = Number.isFinite(aidNum) && aidNum > 0 ? aidNum : articleId;
  if (!listEl) return;

  const prefs = getCommentsVisibilityPrefs();
  const showComments = !!(prefs && prefs.showComments) && !commentsSuppressedByVersionLayer();
  const publicStatuses = new Set(["veröffentlicht", "veroeffentlicht", "published"]);

  const normalizeStatus = (v) => String(v || "").trim().toLowerCase();
  const normalizeApproval = (v) => {
    const n = Number(v);
    return Number.isFinite(n) ? n : -1;
  };
  const normalizeVotes = (v) => {
    const n = Number(v);
    return Number.isFinite(n) ? n : 0;
  };
  const getCommentSummaryForSort = (c) => {
    const cid = c && c.id != null ? String(c.id) : "";
    return cid && commentVoteSummaryCache && commentVoteSummaryCache[cid] ? commentVoteSummaryCache[cid] : {};
  };
  const commentApprovalPercent = (c) => {
    const s = getCommentSummaryForSort(c);
    return normalizeApproval(
      s.raw_approval_percent != null ? s.raw_approval_percent :
      (s.approval_percent != null ? s.approval_percent :
      (c && c.raw_approval_percent != null ? c.raw_approval_percent : c && c.approval_percent))
    );
  };
  const commentVoteTotal = (c) => {
    const s = getCommentSummaryForSort(c);
    return normalizeVotes(
      s.raw_total != null ? s.raw_total :
      (s.total_votes != null ? s.total_votes :
      (c && c.effective_total_votes != null ? c.effective_total_votes :
      (c && c.votes_total != null ? c.votes_total :
      (c && c.total_votes != null ? c.total_votes : c && c.votes_count))))
    );
  };

  const currentSort = (() => {
    try { return getCommentSortMode(aid); } catch (_) {}
    return "newest";
  })();

  const rowsIn = mergePrivateOverlayCommentsForArticle(aid, comments);
  const requiredProposalIds = new Set(getRequiredPublishedNewArticleCommentIdsForArticleId(aid));
  const visible = rowsIn.filter((c) => {
    const cid = String(c && c.id != null ? c.id : "");
    if (cid && requiredProposalIds.has(cid)) return true;
    if (c && c._klimagg_private_overlay) return true;
    if (!showComments) return false;
    const st = normalizeStatus(c && c.status);
    return publicStatuses.has(st);
  });

  visible.sort((a, b) => {
    const aCreated = Date.parse((a && (a.created_at || a.published_at)) || "") || 0;
    const bCreated = Date.parse((b && (b.created_at || b.published_at)) || "") || 0;
    if (currentSort === "approval") {
      const ap = commentApprovalPercent(a);
      const bp = commentApprovalPercent(b);
      if (bp !== ap) return bp - ap;
      const av = commentVoteTotal(a);
      const bv = commentVoteTotal(b);
      if (bv !== av) return bv - av;
      return bCreated - aCreated;
    }
    if (currentSort === "votes") {
      const av = normalizeVotes(a && (a.effective_total_votes != null ? a.effective_total_votes : a.votes_total));
      const bv = normalizeVotes(b && (b.effective_total_votes != null ? b.effective_total_votes : b.votes_total));
      if (bv !== av) return bv - av;
      return bCreated - aCreated;
    }
    if (currentSort === "published") {
      const aPub = Date.parse((a && a.published_at) || "") || 0;
      const bPub = Date.parse((b && b.published_at) || "") || 0;
      if (bPub !== aPub) return bPub - aPub;
      return bCreated - aCreated;
    }
    return bCreated - aCreated;
  });

  const articleRoot = (() => {
    try {
      return (
        document.querySelector(`article[data-article-id="${cssEscape(String(aid || ""))}"]`) ||
        document.querySelector(`.article-wrap[data-article-id="${cssEscape(String(aid || ""))}"]`) ||
        null
      );
    } catch (_) {
      return null;
    }
  })();

  const versionId = getCurrentVersionIdSafe(aid, articleRoot) || "";
  const scope = resolveInlineDiffScope(aid, versionId || "");
  const rec = getInlineDiffRecForRead(aid, versionId || "");
  try { seedInlineDiffCommentCardCacheFromComments(rec, comments); } catch (_) {}
  const backendMap = rec && rec.backendCommentCardsByCid && typeof rec.backendCommentCardsByCid === "object"
    ? rec.backendCommentCardsByCid
    : {};
  const statusBadgeHtml = (c) => {
    const st = normalizeStatus(c && c.status);
    const mode = String((c && c.comment_mode) || "change").trim();
    const out = [];
    if (mode === "new_article") out.push('<span class="badge article-comment-mode-badge article-comment-mode-badge-new">Neuer Artikel</span>');
    if (mode === "delete_article") out.push('<span class="badge article-comment-mode-badge article-comment-mode-badge-delete">Antrag zur Artikel-Löschung</span>');
    if (st === "entwurf" || st === "draft") out.push('<span class="badge badge-draft">Entwurf</span>');
    if (st === "review") out.push('<span class="badge badge-review">Review</span>');
    if (st === "abgelehnt" || st === "rejected") out.push('<span class="badge badge-archived">Abgelehnt</span>');
    if (st === "veröffentlicht") out.push('<span class="badge badge-published">Veröffentlicht</span>');
    if (st === "integriert") out.push('<span class="badge">Integriert</span>');
    if (st === "archiviert") out.push('<span class="badge">Archiviert</span>');
    if (c && c.qualified_for_next_release) {
      out.push('<span class="badge badge-nextdraft article-comment-badge-qualified">zur Release qualifiziert</span>');
    }
    if (getRelatedCommentsPayload(c)) {
      out.push('<span class="badge badge-related">🔗 Zusammenhang</span>');
    }
    return out.join(" ");
  };

  const renderDate = (value) => {
    const raw = String(value || "").trim();
    if (!raw) return "";
    try {
      const d = new Date(raw);
      if (!Number.isNaN(d.getTime())) return d.toLocaleString("de-DE");
    } catch (_) {}
    return raw;
  };

  const renderExplanation = (c) => {
    const raw = c && c.explanation != null ? String(c.explanation) : "";
    if (!raw.trim()) return "";
    const html = (typeof renderCommentMarkup === "function")
      ? renderCommentMarkup(raw)
      : escapeHtml(raw).replace(/\n/g, "<br>");
    return '<div class="article-comment-section"><div class="section-title">Begründung</div><div class="section-body">' + html + '</div></div>';
  };

  const renderSources = (c) => {
    const raw = c && c.sources != null ? c.sources : null;
    if (Array.isArray(raw)) {
      const items = raw
        .map((x) => String(x || "").trim())
        .filter(Boolean)
        .map((x) => '<li>' + escapeHtml(x) + '</li>')
        .join("");
      if (!items) return "";
      return '<div class="article-comment-section"><div class="section-title">Quellen</div><div class="section-body klein"><ul>' + items + '</ul></div></div>';
    }
    if (typeof raw === "string" && raw.trim()) {
      return '<div class="article-comment-section"><div class="section-title">Quellen</div><div class="section-body klein">' + sourcesToHtml(raw) + '</div></div>';
    }
    return "";
  };

  const renderSummary = (c) => {
    return "";
  };

  const renderDiff = (c) => {
    const cid = c && c.id != null ? String(c.id) : "";
    const backendHtml = cid ? String(backendMap[cid] || extractInlineDiffCommentCardHtmlFromComment(c) || "") : "";
    if (!backendHtml) return "";
    const diffUsedAttr = ' data-kgg-diff-used="' + escapeHtml("backend_comment_cards_by_cid") + '"';
    
    
    return '<div class="article-comment-section"><div class="section-title">Diff</div><div class="section-body"' + diffUsedAttr + '>' + backendHtml + '</div></div>';
  };

  const renderVoteBar = (c) => {
    return (
      '<div class="comment-interactions">' +
        '<div class="comment-vote-bar" data-comment-id="' + escapeHtml(String(c.id)) + '"' +
          ' data-article-id="' + escapeHtml(String(c.article_id || aid || "")) + '"' +
          ' data-owner-user-id="' + escapeHtml(String(c.user_id || "")) + '">' +
          '<div class="comment-vote-buttons" role="group" aria-label="Stimme zu diesem Kommentar abgeben">' +
            '<button class="comment-vote-btn" type="button" data-emoji="✅" title="Starke Zustimmung">✅</button>' +
            '<button class="comment-vote-btn" type="button" data-emoji="🟢" title="Zustimmung">🟢</button>' +
            '<button class="comment-vote-btn" type="button" data-emoji="🟡" title="Enthaltung / egal">🟡</button>' +
            '<button class="comment-vote-btn" type="button" data-emoji="🟠" title="Eher kritisch">🟠</button>' +
            '<button class="comment-vote-btn" type="button" data-emoji="🔴" title="Starke Ablehnung">🔴</button>' +
          '</div>' +
          '<div class="comment-flag-buttons" role="group" aria-label="Zusätzliche Markierungen für deine Stimme">' +
            '<button class="comment-flag-btn" type="button" data-flag="🧭" title="Klimawirkung / strategisch stark">🧭</button>' +
            '<button class="comment-flag-btn" type="button" data-flag="✍️" title="Gut formuliert / gut lesbar">✍️</button>' +
            '<button class="comment-flag-btn" type="button" data-flag="🧩" title="Praktisch anschlussfähig / umsetzbar">🧩</button>' +
            '<button class="comment-flag-btn" type="button" data-flag="⚖️" title="Fair / ausgewogen / rechtlich sauber">⚖️</button>' +
            '<button class="comment-flag-btn" type="button" data-flag="🚩" title="Lesezeichen / Merker für diesen Kommentar">🚩</button>' +
          '</div>' +
          '<div class="comment-vote-summary" aria-live="polite">' +
            '<span class="comment-vote-summary-text"></span>' +
            '<span class="comment-top-flag"></span>' +
          '</div>' +
        '</div>' +
      '</div>'
    );
  };

  if (!visible.length) {
    listEl.innerHTML = '<p class="article-comments-empty klein">Noch keine Kommentare sichtbar.</p>';
    try { initCommentVotingUI(listEl); } catch (_) {}
    return;
  }

  const beforeHeight = (() => { try { return listEl.getBoundingClientRect().height; } catch (_) { return 0; } })();
  if (beforeHeight > 0) {
    try { listEl.style.minHeight = Math.ceil(beforeHeight) + "px"; } catch (_) {}
  }

  const manualActive = new Set(getManualInlineDiffIdsSafe(aid, versionId));
  const nextDraftActive = new Set(getNextDraftInlineDiffIdsSafe(aid));
  const nextDraftRows = visible.filter((c) => {
    const cid = String(c && c.id != null ? c.id : "");
    return !!cid && nextDraftActive.has(cid);
  });
  const normalRows = visible.filter((c) => {
    const cid = String(c && c.id != null ? c.id : "");
    return !cid || !nextDraftActive.has(cid);
  });
  const navRowsForFocus = nextDraftRows.concat(normalRows);
  const visibleIds = navRowsForFocus.map((c) => String(c && c.id != null ? c.id : "")).filter(Boolean);

  const storedFocus = getCommentFocusId(aid);
  const currentFocus = (storedFocus && visibleIds.includes(storedFocus)) ? storedFocus : visibleIds[0];
  if (currentFocus) setCommentFocusId(aid, currentFocus);
  const focusedComment = visible.find((c) => String(c && c.id) === String(currentFocus)) || visible[0];

  try {
    const box = listEl.closest(".article-comments[data-article-id]");
    const activeIdsForHint = Array.from(new Set([].concat(
      Array.from(manualActive || []),
      Array.from(nextDraftActive || [])
    )));
    updateHiddenLayerHintForCommentsBox(box, visible, focusedComment, activeIdsForHint);
  } catch (_) {}

  const renderCard = (c) => {
    const created = renderDate(c && (c.published_at || c.created_at));
    const author = escapeHtml(String((c && (c.public_author_label || c.author_label || c.author || "Anonym")) || "Anonym"));
    const statusBadges = statusBadgeHtml(c);
    const cid = String(c && c.id != null ? c.id : "");
    const overlayReason = String((c && c._klimagg_private_overlay_reason) || "").trim();
    const classes = ["article-comment"];
    if (overlayReason) classes.push("private-comment-overlay", "private-comment-overlay-" + overlayReason);
    if (overlayReason === "review") classes.push("review-target");
    const cidBadge = '<span class="kgg-comment-cid-badge">#' + escapeHtml(cid) + '</span>';
    return (
      '<div class="' + classes.join(" ") + '" id="comment-' + escapeHtml(cid) + '" data-comment-id="' + escapeHtml(cid) + '"' +
        (overlayReason ? ' data-private-overlay="' + escapeHtml(overlayReason) + '"' : '') +
      '>' +
        '<div class="article-comment-meta klein">' +
          '<span class="article-comment-author">' + author + '</span>' +
          (created ? '<span class="article-comment-release">' + escapeHtml(created) + '</span>' : '') +
          (statusBadges ? '<span class="article-comment-statuses">' + statusBadges + '</span>' : '') +
          cidBadge +
        '</div>' +
        renderDiff(c) +
        renderExplanation(c) +
        renderRelatedCommentsSection(c) +
        renderSources(c) +
        renderVoteBar(c) +
      '</div>'
    );
  };

  const badgeDate = (c) => {
    const raw = String((c && (c.published_at || c.created_at)) || "").trim();
    if (!raw) return "";
    try {
      const d = new Date(raw);
      if (!Number.isNaN(d.getTime())) {
        return d.toLocaleDateString("de-DE", { day: "numeric", month: "numeric" });
      }
    } catch (_) {}
    return raw.slice(0, 10);
  };

  const ownMarkersVisible = !!(
    klimaggAuth &&
    klimaggAuth.user &&
    klimaggAuth.accessToken &&
    document.body &&
    document.body.classList.contains("toc-personal-mood-on")
  );
  const ownBadgeMarkers = (cid) => {
    if (!ownMarkersVisible || !cid) return "";
    const out = [];
    try {
      const vote = personalCommentVotes && personalCommentVotes[String(cid)] ? String(personalCommentVotes[String(cid)].main_vote || "").trim() : "";
      if (vote) out.push(vote);
    } catch (_) {}
    try {
      const rec0 = personalCommentReactions && personalCommentReactions[String(cid)];
      const emojis = rec0 && Array.isArray(rec0.emojis) ? rec0.emojis.map(String).filter(Boolean) : [];
      emojis.forEach((emoji) => { if (!out.includes(emoji)) out.push(emoji); });
    } catch (_) {}
    if (!out.length) return "";
    return '<span class="comment-nav-own-symbols"> ' + out.map((x) => escapeHtml(x)).join(" ") + '</span>';
  };

  const renderNavBadge = (c) => {
    const cid = String(c && c.id != null ? c.id : "");
    const classes = ["comment-nav-badge"];
    if (cid && String(currentFocus) === cid) classes.push("is-focused");
    if (cid && manualActive.has(cid)) classes.push("is-manual-active");
    if (cid && nextDraftActive.has(cid)) classes.push("is-nextdraft-active");
    return '<button class="' + classes.join(" ") + '" type="button" data-comment-id="' + escapeHtml(cid) + '" aria-pressed="' + (manualActive.has(cid) ? "true" : "false") + '">#' + escapeHtml(cid) + ownBadgeMarkers(cid) + '</button>';
  };

  const normalMarkers = (() => {
    if (!normalRows.length) return { before: "", after: "" };
    if (currentSort === "approval") {
      return { before: "🟢", after: normalRows.length > 1 ? "🔴" : "" };
    }
    return {
      before: badgeDate(normalRows[0]),
      after: normalRows.length > 1 ? badgeDate(normalRows[normalRows.length - 1]) : "",
    };
  })();

  const navParts = [];
  nextDraftRows.forEach((c) => navParts.push(renderNavBadge(c)));
  if (nextDraftRows.length && normalRows.length) {
    navParts.push('<span class="comment-nav-separator" title="Links Next-Draft, rechts Kommentare">|</span>');
  }
  if (normalMarkers.before) {
    navParts.push('<span class="comment-nav-sort-marker">' + escapeHtml(normalMarkers.before) + '</span>');
  }
  normalRows.forEach((c) => navParts.push(renderNavBadge(c)));
  if (normalMarkers.after && normalMarkers.after !== normalMarkers.before) {
    navParts.push('<span class="comment-nav-sort-marker">' + escapeHtml(normalMarkers.after) + '</span>');
  }
  const navHtml = navParts.join(" ");

  listEl.innerHTML =
    '<div class="article-comments-badge-row" role="group" aria-label="Kommentare auswählen">' + navHtml + '</div>' +
    '<div class="article-comment-card-pane">' + renderCard(focusedComment) + '</div>';

  if (!listEl.dataset.compactCommentWired) {
    listEl.dataset.compactCommentWired = "1";
    listEl.addEventListener("click", async (ev) => {
      const badge = ev.target && ev.target.closest ? ev.target.closest(".comment-nav-badge[data-comment-id]") : null;
      if (!badge) return;
      ev.preventDefault();
      await withStableCommentBoxTop(listEl, async () => {
        const cid = String(badge.getAttribute("data-comment-id") || "").trim();
        if (!cid) return;
        setCommentFocusId(aid, cid);
        const cur = new Set(getManualInlineDiffIdsSafe(aid, versionId));
        if (cur.has(cid)) cur.delete(cid); else cur.add(cid);
        setManualInlineDiffIdsSafe(aid, Array.from(cur), versionId);
        try { setCommentOverlayModeAndSync("on"); } catch (_) {}
        await refreshInlineDiffSafe(aid, versionId, { source: "commentCompactBadge.toggle" });
        try { renderCommentsIntoList(aid, comments, listEl); } catch (_) {}
        try { updateCommentOverlayResetControlState(); } catch (_) {}
      });
    });
  }

  try {
    const box = listEl.closest(".article-comments[data-article-id]");
    if (box && box.dataset.compactBulkWired !== "1") {
      box.dataset.compactBulkWired = "1";
      const btnShow = box.querySelector(".article-comments-all-show");
      const btnHide = box.querySelector(".article-comments-all-hide");
      const hint = box.querySelector(".article-comments-nextdraft-hint");
      const hiddenLayerHint = box.querySelector(".article-comments-hidden-layer-hint");
      if (hiddenLayerHint && hiddenLayerHint.dataset.wired !== "1") {
        hiddenLayerHint.dataset.wired = "1";
        hiddenLayerHint.addEventListener("click", (ev) => {
          const btn = ev.target && ev.target.closest ? ev.target.closest(".article-comments-hidden-layer-show") : null;
          if (!btn) return;
          ev.preventDefault();
          const keys = String(hiddenLayerHint.dataset.blockKeys || "").split(",").map((x) => x.trim()).filter(Boolean);
          setTextLayerBlockKeysVisible(keys);
          try { rerenderCompactCommentsForElement(box); } catch (_) {}
        });
      }
      if (btnShow) {
        btnShow.addEventListener("click", async () => {
          await withStableCommentBoxTop(box, async () => {
            const cached = (window.__klimaggCommentsCacheByArticleId && Array.isArray(window.__klimaggCommentsCacheByArticleId[String(aid)]))
              ? window.__klimaggCommentsCacheByArticleId[String(aid)]
              : visible;
            const ids = cached
              .filter((c) => c && !c._klimagg_private_overlay && publicStatuses.has(normalizeStatus(c.status)))
              .map((c) => String(c && c.id != null ? c.id : ""))
              .filter(Boolean);
            setManualInlineDiffIdsSafe(aid, ids, versionId);
            try { setCommentOverlayModeAndSync("on"); } catch (_) {}
            if (hint) hint.hidden = true;
            await refreshInlineDiffSafe(aid, versionId, { source: "commentCompactAll.show" });
            try { renderCommentsIntoList(aid, comments, listEl); } catch (_) {}
            try { updateCommentOverlayResetControlState(); } catch (_) {}
          });
        });
      }
      if (btnHide) {
        btnHide.addEventListener("click", async () => {
          await withStableCommentBoxTop(box, async () => {
            const manual = getManualInlineDiffIdsSafe(aid, versionId);
            const next = getNextDraftInlineDiffIdsSafe(aid);
            if (manual.length) {
              clearManualInlineDiffIdsSafe(aid, versionId);
              if (hint) hint.hidden = !(next && next.length);
              await refreshInlineDiffSafe(aid, versionId, { source: "commentCompactAll.hideManual" });
              try { renderCommentsIntoList(aid, comments, listEl); } catch (_) {}
              try { updateCommentOverlayResetControlState(); } catch (_) {}
              return;
            }
            if (next.length) {
              clearNextDraftInlineDiffIdsSafe(aid);
              try { setVersionDiffMode("off"); } catch (_) {}
              try {
                const cb = document.getElementById("toggle-next-draft");
                if (cb) cb.checked = false;
              } catch (_) {}
              if (hint) hint.hidden = true;
              await refreshInlineDiffSafe(aid, versionId, { source: "commentCompactAll.hideNextDraftSecondClick" });
              try { renderCommentsIntoList(aid, comments, listEl); } catch (_) {}
              try { updateCommentOverlayResetControlState(); } catch (_) {}
            }
          });
        });
      }
    }
  } catch (_) {}

  try { initCommentVotingUI(listEl); } catch (_) {}
  if (!klimaggAuth.user || !klimaggAuth.accessToken) {
    try { applyGuestVotesToRenderedComments(); } catch (_) {}
    try { applyGuestReactionsToRenderedComments(); } catch (_) {}
  } else {
    try { applyPersonalVotesToRenderedComments(); } catch (_) {}
  }

  try {
    requestAnimationFrame(() => {
      try { listEl.style.minHeight = ""; } catch (_) {}
    });
  } catch (_) {
    try { listEl.style.minHeight = ""; } catch (_) {}
  }

}

  
  (function initEntryReadMore(){
    const btn = document.getElementById("btn-entry-readmore");
    if (!btn) return;
    btn.addEventListener("click", () => {
      const section = document.getElementById("articles-section");
      if (section) section.scrollIntoView({ behavior: "smooth", block: "start" });
    });
  })();

  // -------------------------------------------------------------------------
  
  // -------------------------------------------------------------------------
  function normalizeArticleScope(v) {
    const s = String(v || "").trim().toLowerCase();
    if (s === "toc" || s === "core" || s === "all") return s;
    return "toc"; 
  }

  function getArticleScope() {
    return normalizeArticleScope(safeGetLS(UI_ARTICLE_SCOPE_STORAGE_KEY) || "toc");
  }

  function setArticleScope(scope) {
    safeSetLS(UI_ARTICLE_SCOPE_STORAGE_KEY, normalizeArticleScope(scope));
  }

  function titleIsVO(title) {
    if (!title) return false;
    return /\bVO\b/i.test(String(title));
  }

  function parseArticleNoFromTitle(title) {
    
    if (!title) return null;
    const m = String(title).match(/^\s*Artikel\s+(\d+)/i);
    if (!m) return null;
    const n = parseInt(m[1], 10);
    return Number.isFinite(n) ? n : null;
  }

  function filterArticlesByScope(allArticles, scope) {
    const arr = Array.isArray(allArticles) ? allArticles : [];
    const s = normalizeArticleScope(scope);

    
    if (s === "toc") {
      return arr.filter((a) => a && a.show_in_toc !== false);
    }

    
    if (s === "core") {
      return arr.filter((a) => {
        if (!a) return false;
        if (titleIsVO(a.public_code) || titleIsVO(a.title)) return false;
        const n = parseArticleNoFromTitle(a.public_code || a.title);
        return n !== null && n >= 0 && n <= 18;
      });
    }

    // XL: alles (inkl. VO)
    return arr.filter((a) => !!a);
  }

  // -------------------------------------------------------------------------
  
  // - Anker = sichtbares <article> im Entwurfsbereich
  
  
  // -------------------------------------------------------------------------

  function getRenderedArticleElements() {
    const container = document.getElementById("articles-container");
    if (!container) return [];
    return Array.from(container.querySelectorAll("article[id]"));
  }

  function pickScrollAnchorElement() {
    const els = getRenderedArticleElements();
    if (!els.length) return null;

    
    const yRef = 120;
    let best = null;
    let bestTop = Infinity;

    for (const el of els) {
      const r = el.getBoundingClientRect();
      // 1) bevorzugt: Element schneidet Referenzlinie
      if (r.top <= yRef && r.bottom > yRef) return el;
      
      if (r.top >= 0 && r.top < bestTop) {
        bestTop = r.top;
        best = el;
      }
    }
    
    return best || els[els.length - 1];
  }

  function getArticleIndexInAllById(articleId) {
    if (!Array.isArray(appState.articles_all)) return null;
    const idNum = (articleId != null) ? Number(articleId) : NaN;
    if (!Number.isFinite(idNum)) return null;
    const idx = appState.articles_all.findIndex((a) => a && Number(a.id) === idNum);
    return idx >= 0 ? idx : null;
  }

  function getArticleIndexInAllByAnchorId(anchorId) {
    if (!Array.isArray(appState.articles_all)) return null;
    const key = String(anchorId || "");
    if (!key) return null;
    const idx = appState.articles_all.findIndex((a) => {
      if (!a) return false;
      const aid = a.slug || `artikel-${a.id}`;
      return aid === key;
    });
    return idx >= 0 ? idx : null;
  }

  function getAnchorInfoBeforeRerender() {
    const el = pickScrollAnchorElement();
    if (!el) return null;
    const r = el.getBoundingClientRect();
    const articleId = el.getAttribute("data-article-id");
    return {
      anchorId: el.id,
      anchorTop: r.top,
      articleId: articleId != null ? Number(articleId) : null,
    };
  }

  function findBestFallbackAnchorId(oldInfo, scopedArticles) {
    const arr = Array.isArray(scopedArticles) ? scopedArticles : [];
    if (!arr.length) return null;

    
    let oldIdx = null;
    if (oldInfo && Number.isFinite(oldInfo.articleId)) {
      oldIdx = getArticleIndexInAllById(oldInfo.articleId);
    }
    if (oldIdx == null && oldInfo && oldInfo.anchorId) {
      oldIdx = getArticleIndexInAllByAnchorId(oldInfo.anchorId);
    }

    if (oldIdx == null) {
      const a0 = arr[0];
      return a0 ? (a0.slug || `artikel-${a0.id}`) : null;
    }

    // Kandidat: kleinster Index >= oldIdx
    let best = null;
    let bestIdx = Infinity;
    for (const a of arr) {
      if (!a) continue;
      const idx = getArticleIndexInAllById(a.id);
      if (idx == null) continue;
      if (idx >= oldIdx && idx < bestIdx) {
        bestIdx = idx;
        best = a;
      }
    }
    if (!best) best = arr[arr.length - 1];
    return best ? (best.slug || `artikel-${best.id}`) : null;
  }

  function restoreScrollAfterRerender(oldInfo, scopedArticles) {
    if (!oldInfo) return;
    const desiredTop = oldInfo.anchorTop;

    const attempt = () => {
      let target = document.getElementById(oldInfo.anchorId);
      if (!target) {
        const fbId = findBestFallbackAnchorId(oldInfo, scopedArticles);
        if (fbId) target = document.getElementById(fbId);
      }
      if (!target) return;
      const newTop = target.getBoundingClientRect().top;
      const delta = newTop - desiredTop;
      if (Number.isFinite(delta) && Math.abs(delta) > 1) {
        window.scrollBy(0, delta);
      }
    };

    
    requestAnimationFrame(() => requestAnimationFrame(attempt));
  }

  function applyTextLayerTogglesOnce() {
    const ids = [
      { checkboxId: "toggle-kurzinfo", selector: ".content-block.kurzinfo" },
      { checkboxId: "toggle-story", selector: ".content-block.story" },
      { checkboxId: "toggle-einleitung", selector: ".content-block.einleitung" },
      { checkboxId: "toggle-juristisch", selector: ".content-block.juristisch" },
      { checkboxId: "toggle-juristisch2", selector: ".content-block.juristisch2" },
      { checkboxId: "toggle-anmerkung", selector: ".content-block.anmerkung" },
    ];
    ids.forEach(({ checkboxId, selector }) => {
      const cb = document.getElementById(checkboxId);
      if (!cb) return;
      document.querySelectorAll(selector).forEach((el) => {
        if (cb.checked) el.classList.remove("hidden");
        else el.classList.add("hidden");
      });
    });
  }

  // -------------------------------------------------------------------------
  // Versionen: Next-Draft / Last-Version (Diff im Artikeltext)
  // -------------------------------------------------------------------------

  function normalizeVersionDiffMode(v) {
    const s = String(v || "").trim().toLowerCase();
    if (s === "last_version") return s;
    if (s === "next_draft" || s === "next-draft" || s === "nextdraft") return "next_draft";
    return "off";
  }

  function getVersionDiffMode() {
    return normalizeVersionDiffMode(safeGetLS(UI_VERSION_DIFF_MODE_STORAGE_KEY) || "off");
  }

  function setVersionDiffMode(mode) {
    safeSetLS(UI_VERSION_DIFF_MODE_STORAGE_KEY, normalizeVersionDiffMode(mode));
  }


  // -------------------------------------------------------------------------
  
  // -------------------------------------------------------------------------

  function normalizeCommentOverlayMode(v) {
    const s = String(v || "").trim().toLowerCase();
    if (s === "off") return "off";
    return "on";
  }

  function getCommentOverlayMode() {
    return normalizeCommentOverlayMode(safeGetLS(UI_COMMENT_OVERLAY_STORAGE_KEY) || "on");
  }

  function setCommentOverlayMode(mode) {
    const normalized = normalizeCommentOverlayMode(mode);
    safeSetLS(UI_COMMENT_OVERLAY_STORAGE_KEY, normalized);
    if (normalized === "off") {
      try { localStorage.removeItem(layerKey()); } catch (_) {}
      try { localStorage.removeItem(hlKey()); } catch (_) {}
    }
  }

  function syncCommentOverlayMasterCheckboxFromMode() {
    
    
    
    try { updateCommentOverlayResetControlState(); } catch (_) {}
  }

  function setCommentOverlayModeAndSync(mode) {
    try { setCommentOverlayMode(mode); } catch (_) {}
    try { updateCommentOverlayResetControlState(); } catch (_) {}
  }

  // Apply v1.0.63 patch semantics: master OFF is hard reset (DOM+storage+UI)

  // Apply comment overlay master mode to rendered articles.
  // Semantics:
  // - on: no-op (overlays can be toggled per-comment)
  // - off: HARD RESET (DOM + storage + UI) and refresh visible articles
  async function applyCommentOverlayModeToRenderedArticles({ silent = false } = {}) {
    try {
      const mode = getCommentOverlayMode();
      if (mode !== "off") return;
      const ctl = window.KlimaGG && window.KlimaGG.commentFlagOverlay;
      if (ctl && typeof ctl.applyMasterMode === "function") {
        await ctl.applyMasterMode("off");
        return;
      }
      const api = window.KlimaGG && window.KlimaGG.inlineDiff;
      if (!api) return;
      for (const { articleId, versionId } of listRenderedInlineDiffTargets()) {
        try { if (typeof api.clear === "function") api.clear(articleId, versionId || undefined); } catch (_) {}
        try {
          if (typeof api.refresh === "function") {
            await api.refresh(
              articleId,
              versionId || undefined,
              { source: "applyCommentOverlayModeToRenderedArticles.off" }
            );
          }
        } catch (_) {}
      }
    } catch (err) {
      if (!silent) console.warn("[KlimaGG] applyCommentOverlayModeToRenderedArticles failed", err);
    }
  }

  async function rehydrateCommentOverlaysForRenderedArticles({ silent = false } = {}) {
    try {
      if (getCommentOverlayMode() === "off") return;
      const inlineApi = getInlineDiffApiSafe();
      if (!inlineApi || typeof inlineApi.refresh !== "function" || typeof inlineApi.getActiveIds !== "function") return;
      for (const { articleId, versionId } of listRenderedInlineDiffTargets()) {
        try {
          const activeIds = inlineApi.getActiveIds(articleId, versionId || undefined) || [];
          if (Array.isArray(activeIds) && activeIds.length) {
            await inlineApi.refresh(articleId, versionId || undefined);
          }
        } catch (_) {}
      }
    } catch (err) {
      if (!silent) console.warn("[KlimaGG] rehydrateCommentOverlaysForRenderedArticles failed", err);
    }
  }

  function listLoadedCommentOverlayTargets() {
    const out = [];
    const seen = new Set();
    try {
      document.querySelectorAll('.article-comments[data-article-id]').forEach((box) => {
        try {
          const listEl = box.querySelector(".article-comments-list");
          if (!listEl || listEl.dataset.loaded !== "true") return;
          const articleId = String(box.getAttribute("data-article-id") || "").trim();
          if (!articleId) return;
          const root =
            document.querySelector('article[data-article-id="' + cssEscape(articleId) + '"]') ||
            document.querySelector('.article-wrap[data-article-id="' + cssEscape(articleId) + '"]') ||
            box;
          const versionId = getCurrentVersionIdSafe(articleId, root) || "";
          const activeFlagCount = Array.from(
            box.querySelectorAll('.comment-flag-btn[data-flag="🚩"].active,.comment-flag-btn[data-flag="🚩"].is-active') || []
          ).length;
          let activeLayerCount = 0;
          try {
            const api = window.KlimaGG && window.KlimaGG.inlineDiff;
            const activeIds = api && typeof api.getActiveIds === "function"
              ? api.getActiveIds(articleId, versionId || undefined)
              : [];
            activeLayerCount = Array.isArray(activeIds) ? activeIds.length : 0;
          } catch (_) {}
          const key = String(articleId) + ":" + String(versionId || "");
          if (seen.has(key)) return;
          seen.add(key);
          out.push({ articleId, versionId, activeFlagCount, activeLayerCount });
        } catch (_) {}
      });
    } catch (_) {}
    return out;
  }

  async function reapplyCommentFlagOverlaysForLoadedArticles({ silent = true } = {}) {
    try {
      const ctl = window.KlimaGG && window.KlimaGG.commentFlagOverlay;
      if (ctl && typeof ctl.rehydrateLoaded === "function") {
        await ctl.rehydrateLoaded({ silent: true, source: "reapplyCommentFlagOverlaysForLoadedArticles" });
      } else if (!silent) {
        console.warn("[KlimaGG] commentFlagOverlay controller unavailable");
      }
    } catch (err) {
      if (!silent) console.warn("[KlimaGG] reapplyCommentFlagOverlaysForLoadedArticles failed", err);
    }
  }

  async function reapplyGuestCommentFlagOverlaysForLoadedArticles({ silent = true } = {}) {
    try {
      if (klimaggAuth.user && klimaggAuth.accessToken) return;
      const ctl = window.KlimaGG && window.KlimaGG.commentFlagOverlay;
      if (ctl && typeof ctl.rehydrateLoaded === "function") {
        await ctl.rehydrateLoaded({ silent: true, source: "reapplyGuestCommentFlagOverlaysForLoadedArticles" });
      } else if (!silent) {
        console.warn("[KlimaGG] commentFlagOverlay controller unavailable");
      }
    } catch (err) {
      if (!silent) console.warn("[KlimaGG] reapplyGuestCommentFlagOverlaysForLoadedArticles failed", err);
    }
  }

  async function applyVersionDiffModeToRenderedArticles({ silent = false } = {}) {
    try {
      const mode = getVersionDiffMode();
      // Beim Einschalten zuerst Kommentare ausblenden/Control sperren; beim
      
      if (mode !== "off") applyCommentsVisibilityToAllRenderedArticles();
      const tools = window.KlimaGG && window.KlimaGG.inlineDiffTools;
      if (tools && typeof tools.applyVersionLayerMode === "function") {
        await tools.applyVersionLayerMode(mode, { silent: !!silent });
      }
      applyCommentsVisibilityToAllRenderedArticles();
      return true;
    } catch (err) {
      console.warn("[KlimaGG] applyVersionDiffModeToRenderedArticles failed", err);
      applyCommentsVisibilityToAllRenderedArticles();
      return false;
    }
  }

  async function deactivateVersionLayerForCommentAction() {
    const mode = getVersionDiffMode();
    if (mode === "off") return false;
    setVersionDiffMode("off");
    const cbNext = document.getElementById("toggle-next-draft");
    const cbLast = document.getElementById("toggle-last-version");
    if (cbNext) cbNext.checked = false;
    if (cbLast) cbLast.checked = false;
    await applyVersionDiffModeToRenderedArticles({ silent: true });
    return true;
  }


(function () {
  if (window.__KGG_INLINE_DIFF_TOOLS__) return;
  window.__KGG_INLINE_DIFF_TOOLS__ = true;

  window.KlimaGG = window.KlimaGG || {};
  try { delete window.KlimaGG.mergePreview; } catch (_) { window.KlimaGG.mergePreview = undefined; }
  const ns = (window.KlimaGG.inlineDiffTools = window.KlimaGG.inlineDiffTools || {});

  function getUserKey() {
    try {
      // The storage key must stay stable before the authenticated user object is loaded.
      
      
      // Deshalb: bevorzugt Token-basiert.
      try {
        const tok = (typeof getStoredAccessToken === "function") ? getStoredAccessToken() : "";
        if (tok) return "t:" + String(tok).slice(0, 24);
      } catch (_) {}

      const u = window.klimaggAuth && klimaggAuth.user ? klimaggAuth.user : null;
      if (u && u.id != null) return "u:" + String(u.id);
      if (u && u.email) return "e:" + String(u.email);
    } catch (_) {}
    return "anon";
  }

  function hlKey() {
    return "__kgg_hl_" + getUserKey().replace(/[^a-zA-Z0-9:_\-]/g, "");
  }

  
  function layerKey() {
    return "__kgg_layers_" + getUserKey().replace(/[^a-zA-Z0-9:_\-]/g, "");
  }

  
  function _getVersionIdFromDom(articleId) {
    try {
      const id = String(articleId || "");
      const root =
        document.querySelector('article[data-article-id="' + id + '"]') ||
        document.querySelector('.article-wrap[data-article-id="' + id + '"]') ||
        null;
      if (!root) return "";
      const raw =
        root.getAttribute("data-current-version-id") ||
        root.getAttribute("data-version-id") ||
        (root.dataset ? (root.dataset.currentVersionId || root.dataset.versionId) : "");
      const n = Number(raw);
      return (Number.isFinite(n) && n > 0) ? String(n) : "";
    } catch (_) { return ""; }
  }

  function layerScopeKey(articleId, versionIdOverride) {
    const aid = String(articleId || "");
    let vid = "";
    try {
      if (versionIdOverride != null && String(versionIdOverride) !== "") {
        vid = String(versionIdOverride);
      } else if (typeof getCurrentVersionIdForArticle === "function") {
        const v = getCurrentVersionIdForArticle(aid);
        if (v != null) vid = String(v);
      }
    } catch (_) {}
    if (!vid) vid = _getVersionIdFromDom(aid);
    return aid + ":" + (vid || "");
  }

  
  function hlScopeKey(articleId, versionIdOverride) {
    return layerScopeKey(articleId, versionIdOverride);
  }

  function readLayers(articleId, versionIdOverride) {
    const raw = localStorage.getItem(layerKey());
    const map = raw ? JSON.parse(raw) : {};
    if (!map || typeof map !== "object" || Array.isArray(map)) {
      throw new Error("Invalid inline-diff layer state");
    }
    const sk = layerScopeKey(articleId, versionIdOverride);
    const obj = map[sk] == null ? {} : map[sk];
    if (!obj || typeof obj !== "object" || Array.isArray(obj)) {
      throw new Error("Invalid inline-diff layer scope state: " + sk);
    }
    return Object.keys(obj).filter((cid) => obj[cid] === true);
  }

  // Single-truth state helper: persists active overlay IDs for one article/version scope.
  function writeLayer(articleId, commentId, on, versionIdOverride) {
    const sid = String(commentId);
    const set = new Set(readLayers(articleId, versionIdOverride).map(String));
    if (on) set.add(sid);
    else set.delete(sid);
    setLayers(articleId, Array.from(set), versionIdOverride);
  }

  function hasAnyRawInlineDiffLayers(articleId) {
    const raw = localStorage.getItem(layerKey());
    const map = raw ? JSON.parse(raw) : {};
    if (!map || typeof map !== "object" || Array.isArray(map)) {
      throw new Error("Invalid inline-diff layer state");
    }
    const aid = String(articleId || "").trim();
    for (const scopeKey of Object.keys(map)) {
      const key = String(scopeKey || "");
      if (!/^\d+:\d+$/.test(key)) continue;
      if (aid && !key.startsWith(aid + ":")) continue;
      const rec = map[scopeKey];
      if (!rec || typeof rec !== "object" || Array.isArray(rec)) {
        throw new Error("Invalid inline-diff layer scope state: " + key);
      }
      if (Object.keys(rec).some((cid) => rec[cid] === true)) return true;
    }
    return false;
  }

  function removeInlineDiffIdsFromArticleState(articleId, ids, versionIdOverride) {
    const aid = String(articleId || "").trim();
    const stale = Array.from(new Set((Array.isArray(ids) ? ids : []).map((x) => String(x)).filter(Boolean)));
    if (!aid || !stale.length) return false;

    let changed = false;
    const layers = new Set(readLayers(aid, versionIdOverride).map(String));
    stale.forEach((cid) => { if (layers.delete(cid)) changed = true; });
    setLayers(aid, Array.from(layers), versionIdOverride);

    const highlights = new Set(readHighlights(aid, versionIdOverride).map(String));
    stale.forEach((cid) => { if (highlights.delete(cid)) changed = true; });
    setHighlights(aid, Array.from(highlights), versionIdOverride);

    const nextDraft = new Set(getNextDraftIds(aid).map(String));
    stale.forEach((cid) => { if (nextDraft.delete(cid)) changed = true; });
    setNextDraftIds(aid, Array.from(nextDraft));
    return changed;
  }

  
  function clearLayers(articleId, versionIdOverride) {
    const raw = localStorage.getItem(layerKey());
    const map = raw ? JSON.parse(raw) : {};
    if (!map || typeof map !== "object" || Array.isArray(map)) {
      throw new Error("Invalid inline-diff layer state");
    }
    const aid = String(articleId || "");
    const hasOverride = (versionIdOverride != null) && String(versionIdOverride).trim() !== "";
    if (hasOverride) {
      delete map[layerScopeKey(articleId, versionIdOverride)];
    } else {
      const prefix = aid + ":";
      for (const key of Object.keys(map)) {
        if (String(key).startsWith(prefix)) delete map[key];
      }
    }
    localStorage.setItem(layerKey(), JSON.stringify(map));
  }

  function setLayers(articleId, idsOn, versionIdOverride) {
    const raw = localStorage.getItem(layerKey());
    const map = raw ? JSON.parse(raw) : {};
    if (!map || typeof map !== "object" || Array.isArray(map)) {
      throw new Error("Invalid inline-diff layer state");
    }
    const sk = layerScopeKey(articleId, versionIdOverride);
    map[sk] = {};
    (Array.isArray(idsOn) ? idsOn : []).forEach((x) => {
      map[sk][String(x)] = true;
    });
    localStorage.setItem(layerKey(), JSON.stringify(map));
  }

  
  
  const nextDraftIdsByArticle = Object.create(null);
  function setNextDraftIds(articleId, ids) {
    const aid = String(articleId || "");
    if (!aid) return;
    const arr = Array.isArray(ids) ? ids.map((x) => String(x)).filter(Boolean) : [];
    nextDraftIdsByArticle[aid] = Array.from(new Set(arr));
  }
  function getNextDraftIds(articleId) {
    const aid = String(articleId || "");
    const v = nextDraftIdsByArticle[aid];
    return Array.isArray(v) ? v : [];
  }
  function clearNextDraftIds(articleId) {
    const aid = String(articleId || "");
    if (!aid) {
      for (const k of Object.keys(nextDraftIdsByArticle)) delete nextDraftIdsByArticle[k];
      return;
    }
    delete nextDraftIdsByArticle[aid];
  }

  const INLINE_DIFF_STATE = Object.freeze({
    getLayers(articleId, versionIdOverride) {
      return readLayers(articleId, versionIdOverride);
    },
    getActiveIds(articleId, versionIdOverride) {
      return readLayers(articleId, versionIdOverride);
    },
    setLayerActive(articleId, commentId, on, versionIdOverride) {
      return writeLayer(articleId, commentId, on, versionIdOverride);
    },
    clearLayers(articleId, versionIdOverride) {
      return clearLayers(articleId, versionIdOverride);
    },
    setLayers(articleId, idsOn, versionIdOverride) {
      return setLayers(articleId, idsOn, versionIdOverride);
    },
    getHighlights(articleId, versionIdOverride) {
      return readHighlights(articleId, versionIdOverride);
    },
    setHighlightActive(articleId, commentId, on, versionIdOverride) {
      return writeHighlight(articleId, commentId, on, versionIdOverride);
    },
    clearHighlights(articleId, versionIdOverride) {
      return clearHighlights(articleId, versionIdOverride);
    },
    setHighlights(articleId, idsOn, versionIdOverride) {
      return setHighlights(articleId, idsOn, versionIdOverride);
    },
    setNextDraft(articleId, ids) {
      return setNextDraftIds(articleId, ids);
    },
    getNextDraft(articleId) {
      return getNextDraftIds(articleId);
    },
    clearNextDraft(articleId) {
      return clearNextDraftIds(articleId);
    },
    clearAllForArticle(articleId, versionIdOverride) {
      try { clearLayers(articleId, versionIdOverride); } catch (_) {}
      try { clearHighlights(articleId, versionIdOverride); } catch (_) {}
      try { clearNextDraftIds(articleId); } catch (_) {}
    },
  });

  


  const INLINE_DIFF_API_VERSION = "canonical-indiff-v20";
  const INLINE_DIFF_API_CONTRACT = Object.freeze({
    version: INLINE_DIFF_API_VERSION,
    endpoint: "/api/merge-preview/multi",
    request: Object.freeze({
      articleId: "aid",
      activeCommentIds: "cids_in_apply_order",
    }),
    articleHtmlSource: "indiff_block_results[block].composed_html",
    baselineHtmlSource: "indiff_block_results[block].baseline_html",
    commentCardHtmlSource: "comment_cards_by_cid_html",
    commentCardRowsSource: "comment_card_rows_by_cid",
    finalDomStateAttribute: "data-user-state",
    primaryScope: "owner_id",
    secondaryScope: "span_id",
    structuralSelector: ".kgg-inline-op[data-kgg-owner-id], .kgg-inline-op[data-kgg-owner-ids]",
    forbiddenNormalPath: Object.freeze([
      "frontend diff reconstruction from old_text/new_text",
      "semantic state from CSS class names",
    ]),
  });

  function isPlainInlineDiffObject(value) {
    return !!(value && typeof value === "object" && !Array.isArray(value));
  }

  function objectKeysSafe(value) {
    return isPlainInlineDiffObject(value) ? Object.keys(value) : [];
  }

  function normalizeIndiffBlockResult(blockResult, rawKey) {
    if (!isPlainInlineDiffObject(blockResult)) {
      throw new Error("Invalid canonical indiff block result: " + String(rawKey || "?"));
    }
    const expectedKey = String(rawKey || "");
    if (!expectedKey || String(blockResult.block_key || "") !== expectedKey) {
      throw new Error("Canonical indiff block key mismatch: " + String(rawKey || "?"));
    }
    for (const name of ["style_table", "interaction_table", "span_registry", "operations", "comment_card_rows"]) {
      if (!Array.isArray(blockResult[name])) {
        throw new Error("Invalid canonical indiff block field " + name + ": " + expectedKey);
      }
    }
    if (!isPlainInlineDiffObject(blockResult.diagnostics)) {
      throw new Error("Invalid canonical indiff block diagnostics: " + expectedKey);
    }
    if (typeof blockResult.baseline_html !== "string" || typeof blockResult.composed_html !== "string") {
      throw new Error("Canonical indiff block HTML missing: " + expectedKey);
    }
    return {
      blockKey: expectedKey,
      baselineHtml: blockResult.baseline_html,
      composedHtml: blockResult.composed_html,
      styleTable: blockResult.style_table,
      interactionTable: blockResult.interaction_table,
      spanRegistry: blockResult.span_registry,
      operations: blockResult.operations,
      commentCardRows: blockResult.comment_card_rows,
      diagnostics: blockResult.diagnostics,
    };
  }

  function normalizeIndiffMergePayloadForInspect(payload) {
    const p = isPlainInlineDiffObject(payload) ? payload : {};
    const blocks = isPlainInlineDiffObject(p.indiffBlockResults) ? p.indiffBlockResults : {};
    const blockKeys = objectKeysSafe(blocks).map((k) => normalizeBlockKey(k)).filter(Boolean);
    const blockSummary = {};
    objectKeysSafe(blocks).forEach((rawKey) => {
      const key = normalizeBlockKey(rawKey);
      const b = normalizeIndiffBlockResult(blocks[rawKey], rawKey);
      blockSummary[key] = {
        hasBaselineHtml: !!b.baselineHtml,
        hasComposedHtml: !!b.composedHtml,
        styleRows: b.styleTable.length,
        interactionRows: b.interactionTable.length,
        spanRows: b.spanRegistry.length,
        operationRows: b.operations.length,
        commentCardRows: b.commentCardRows.length,
        diagnosticKeys: objectKeysSafe(b.diagnostics),
      };
    });
    return {
      ok: !!p.ok,
      aid: p.aid || p.articleId || "",
      inlineEngineIndiff: p.inlineEngineIndiff === true,
      cidsInApplyOrder: Array.isArray(p.cidsInApplyOrder) ? p.cidsInApplyOrder : [],
      commentCardHtmlCids: objectKeysSafe(p.commentCardsByCidHtml),
      commentCardRowsCids: objectKeysSafe(p.commentCardRowsByCid),
      blockKeys,
      blockSummary,
    };
  }

  function getInlineDiffDebugRecord(articleId, versionIdOverride) {
    try {
      const rec = getInlineDiffRecForRead(articleId, versionIdOverride);
      if (rec && typeof rec === "object" && inlineDiffRecordDataScore(rec) > 0) return rec;
    } catch (_) {}
    try {
      const scope = getInlineDiffScope(articleId, versionIdOverride);
      return ensureRec(scope.scopeKey);
    } catch (_) {
      return null;
    }
  }

  function auditInlineDiffArticleDom(articleId, versionIdOverride) {
    const scope = getInlineDiffScope(articleId, versionIdOverride);
    const roots = collectInlineDiffRoots(scope.articleId, scope.versionId);
    const out = {
      articleId: scope.articleId,
      versionId: scope.versionId,
      roots: roots.length,
      ownerElements: 0,
      ownerElementsWithoutUserState: 0,
      spanElements: 0,
      spanElementsWithoutUserState: 0,
      structuralOps: 0,
      structuralOpsWithoutUserState: 0,
      reopenedOl: 0,
      reopenedOlWithoutCurrentStart: 0,
    };
    roots.forEach((root) => {
      try {
        const ownerEls = Array.from(root.querySelectorAll("[data-kgg-owner-id],[data-kgg-owner-ids]") || []);
        const spanEls = Array.from(root.querySelectorAll("[data-kgg-span-id]") || []);
        const opEls = Array.from(root.querySelectorAll(".kgg-inline-op") || []);
        const reopened = Array.from(root.querySelectorAll('ol[data-kgg-reopen-ol="true"]') || []);
        out.ownerElements += ownerEls.length;
        out.ownerElementsWithoutUserState += ownerEls.filter((el) => !el.hasAttribute("data-user-state")).length;
        out.spanElements += spanEls.length;
        out.spanElementsWithoutUserState += spanEls.filter((el) => !el.hasAttribute("data-user-state")).length;
        out.structuralOps += opEls.length;
        out.structuralOpsWithoutUserState += opEls.filter((el) => !el.hasAttribute("data-user-state")).length;
        out.reopenedOl += reopened.length;
        out.reopenedOlWithoutCurrentStart += reopened.filter((el) => !el.hasAttribute("data-kgg-reopen-ol-start-current")).length;
      } catch (_) {}
    });
    return out;
  }

  function getInlineDiffScope(articleId, versionIdOverride) {
    const aid = String(articleId || "");
    const vid = String(
      versionIdOverride != null && String(versionIdOverride) !== ""
        ? versionIdOverride
        : (getCurrentVersionIdForArticle(aid) || "")
    );
    return {
      articleId: aid,
      versionId: vid,
      scopeKey: layerScopeKey(aid, vid),
    };
  }

  function getActiveInlineDiffIds(articleId, versionIdOverride) {
    const scope = getInlineDiffScope(articleId, versionIdOverride);
    let layerIds = INLINE_DIFF_STATE.getLayers(scope.articleId, scope.versionId).map((x) => String(x));
    let hlIds = INLINE_DIFF_STATE.getHighlights(scope.articleId, scope.versionId).map((x) => String(x));
    let ndIds = INLINE_DIFF_STATE.getNextDraft(scope.articleId).map((x) => String(x));
    try {
      const raw = safeGetLS(UI_COMMENT_OVERLAY_STORAGE_KEY) || "on";
      if (String(raw).trim().toLowerCase() === "off") {
        layerIds = [];
        hlIds = [];
      }
    } catch (_) {}
    return Array.from(new Set([].concat(layerIds, hlIds, ndIds).map((x) => String(x)).filter(Boolean)));
  }

  function listRenderedInlineDiffTargets() {
    const out = [];
    const seen = new Set();
    document.querySelectorAll(".article-wrap[data-article-id], article[data-article-id]").forEach((el) => {
      try {
        const articleId = String(
          el.getAttribute("data-article-id") || (el.dataset ? el.dataset.articleId : "") || ""
        ).trim();
        if (!articleId) return;
        const rawVersionId =
          el.getAttribute("data-current-version-id") ||
          el.getAttribute("data-version-id") ||
          (el.dataset ? (el.dataset.currentVersionId || el.dataset.versionId) : "") ||
          "";
        const n = Number(rawVersionId);
        const versionId = (Number.isFinite(n) && n > 0) ? String(n) : "";
        const key = articleId + "::" + versionId;
        if (seen.has(key)) return;
        seen.add(key);
        out.push({ articleId, versionId, root: el });
      } catch (_) {}
    });
    return out;
  }

  function collectInlineDiffRoots(articleId, versionIdOverride) {
    const aid = String(articleId || "").trim();
    const vid = (versionIdOverride == null ? "" : String(versionIdOverride)).trim();
    if (!aid) return [];
    const all = Array.from(
      document.querySelectorAll(
        '.article-wrap[data-article-id="' + cssEscape(aid) + '"], article[data-article-id="' + cssEscape(aid) + '"]'
      )
    );

    function versionMatches(el) {
      try {
        if (!vid) return true;
        const raw =
          el.getAttribute("data-current-version-id") ||
          el.getAttribute("data-version-id") ||
          (el.dataset ? (el.dataset.currentVersionId || el.dataset.versionId) : "") ||
          "";
        return !raw || String(raw).trim() === vid;
      } catch (_) {
        return true;
      }
    }

    function uniqueElements(items) {
      const out = [];
      const seen = new Set();
      Array.from(items || []).forEach((el) => {
        if (!el || seen.has(el)) return;
        seen.add(el);
        out.push(el);
      });
      return out;
    }

    


    const articleRoots = uniqueElements(
      all.filter((el) => {
        try {
          return String(el.tagName || "").toLowerCase() === "article" && versionMatches(el);
        } catch (_) {
          return false;
        }
      })
    );
    if (articleRoots.length) return articleRoots;

    return uniqueElements(
      all.filter((el) => {
        try {
          return String(el.tagName || "").toLowerCase() !== "article" && versionMatches(el);
        } catch (_) {
          return false;
        }
      })
    );
  }

  function inlineDiffHasAnyActiveIds() {
    try {
      if (hasAnyRawInlineDiffLayers()) return true;
      const inlineApi = getInlineDiffApiSafe();
      if (!inlineApi || typeof inlineApi.getActiveIds !== "function") return false;
      return listRenderedInlineDiffTargets().some(({ articleId, versionId }) => {
        try {
          const active = inlineApi.getActiveIds(articleId, versionId || undefined) || [];
          return Array.isArray(active) && active.length > 0;
        } catch (_) {
          return false;
        }
      });
    } catch (_) {
      return false;
    }
  }

  function resolveCommentFlagArticleIdFromBar(bar) {
    try {
      const direct = bar ? String(bar.getAttribute("data-article-id") || "").trim() : "";
      if (direct) return direct;
    } catch (_) {}
    try {
      const host = bar && typeof bar.closest === "function"
        ? bar.closest('.article-comments[data-article-id], article[data-article-id], .article-wrap[data-article-id]')
        : null;
      const fromHost = host ? String(host.getAttribute("data-article-id") || "").trim() : "";
      if (fromHost) return fromHost;
    } catch (_) {}
    try {
      const article = bar && typeof bar.closest === "function"
        ? bar.closest('article[data-article-id], .article-wrap[data-article-id]')
        : null;
      const fromArticle = article ? String(article.getAttribute("data-article-id") || "").trim() : "";
      if (fromArticle) return fromArticle;
    } catch (_) {}
    return "";
  }

  function resolveInlineDiffVersionIdForBar(bar, articleId) {
    const aid = String(articleId || "").trim();
    let vid = null;
    try {
      const host =
        bar && typeof bar.closest === "function"
          ? bar.closest('.article-comments[data-article-id], article[data-article-id], .article-wrap[data-article-id]')
          : null;
      const rootFromClick =
        host && host.closest
          ? host.closest('article[data-article-id],.article-wrap[data-article-id]')
          : null;
      const root =
        rootFromClick ||
        (aid ? findArticleRoot(aid) : null) ||
        (aid ? document.querySelector('article[data-article-id="' + cssEscape(String(aid)) + '"]') : null) ||
        (aid ? document.querySelector('.article-wrap[data-article-id="' + cssEscape(String(aid)) + '"]') : null);
      const n = getCurrentVersionIdSafe(aid, root || undefined);
      if (Number.isFinite(n) && n > 0) vid = n;
    } catch (_) {}
    try {
      if ((vid == null || String(vid || "").trim() === "") && aid) {
        const n = getCurrentVersionIdForArticle(aid);
        if (Number.isFinite(n) && n > 0) vid = n;
      }
    } catch (_) {}
    return (vid == null || String(vid || "").trim() === "") ? null : vid;
  }

async function applyInlineDiffToggle(articleId, commentId, on, versionIdOverride, options) {
    const cid = String(commentId || "").trim();
    if (!cid) {
      return refreshInlineDiff(articleId, versionIdOverride, options);
    }
    let effectiveVersionId = versionIdOverride;
    try {
      if ((effectiveVersionId == null || String(effectiveVersionId || "").trim() === "") && articleId != null) {
        const n = getCurrentVersionIdForArticle(articleId);
        if (Number.isFinite(n) && n > 0) effectiveVersionId = n;
      }
    } catch (_) {}
    if (!!on) {
      try { setCommentOverlayModeAndSync("on"); } catch (_) {}
      INLINE_DIFF_STATE.setLayerActive(articleId, cid, true, effectiveVersionId);
    } else {
      INLINE_DIFF_STATE.setLayerActive(articleId, cid, false, effectiveVersionId);
      try {
        if (hasAnyRawInlineDiffLayers()) {
          setCommentOverlayModeAndSync("on");
        } else {
          setCommentOverlayModeAndSync("off");
        }
      } catch (_) {}
    }
    return refreshInlineDiff(articleId, effectiveVersionId, Object.assign({}, options || {}, {
      source: "applyInlineDiffToggle",
      commentId: cid,
      on: !!on,
    }));
  }

  async function handleGuestFlagInlineDiffToggle(articleId, versionId, commentId, nextOn) {
    const aid = String(articleId || "").trim();
    const cid = String(commentId || "").trim();
    if (!aid || !cid) return false;
    let effectiveVersionId = (versionId == null || String(versionId).trim() === "") ? null : versionId;
    try {
      if (effectiveVersionId == null) {
        const n = getCurrentVersionIdForArticle(aid);
        if (Number.isFinite(n) && n > 0) effectiveVersionId = n;
      }
    } catch (_) {}
    await applyInlineDiffToggle(aid, cid, !!nextOn, effectiveVersionId, {
      source: "guestFlagToggleController",
      commentId: cid,
      on: !!nextOn,
    });
    if (!nextOn) {
      try {
        if (!inlineDiffHasAnyActiveIds()) {
          setCommentOverlayModeAndSync("off");
        }
      } catch (_) {}
    }
    return true;
  }

  async function applyInlineDiffDraftToggle(articleId, versionIdOverride, draftCommentId, on, options) {
    const cid = String(draftCommentId || "").trim();
    if (!cid) {
      return refreshInlineDiff(articleId, versionIdOverride, options);
    }
    return applyInlineDiffToggle(articleId, cid, !!on, versionIdOverride, Object.assign({}, options || {}, {
      source: "applyInlineDiffDraftToggle",
      draftCommentId: cid,
      isDraft: true,
    }));
  }

  function extractInlineDiffBlockInnerHtml(htmlSource, blockKey) {
    const html = String(htmlSource || "");
    if (!html) return "";
    const box = document.createElement("div");
    box.innerHTML = html;
    const key = normalizeBlockKey(blockKey);
    const direct = box.querySelector('section.content-block[data-block="' + cssEscape(key) + '"]');
    return direct ? String(direct.innerHTML || "") : html;
  }

  function parseInlineDiffArticleBlocks(payload) {
    if (!payload || typeof payload !== "object" || !isPlainInlineDiffObject(payload.indiffBlockResults)) {
      throw new Error("Canonical indiff_block_results missing");
    }
    const out = {};
    Object.keys(payload.indiffBlockResults).forEach((rawKey) => {
      const key = normalizeBlockKey(rawKey);
      if (!key) throw new Error("Invalid canonical indiff block key: " + String(rawKey || ""));
      const block = normalizeIndiffBlockResult(payload.indiffBlockResults[rawKey], rawKey);
      out[key] = extractInlineDiffBlockInnerHtml(block.composedHtml, key);
    });
    return out;
  }

  function updateInlineDiffBlockEmptyState(blockEl, html) {
    if (!blockEl) return false;
    const htmlText = String(html || "");
    let hasContent = !!htmlText.trim();
    if (!hasContent) {
      try {
        hasContent = !!String(blockEl.textContent || "").trim();
      } catch (_) {}
    }
    if (!hasContent) {
      try {
        hasContent = !!blockEl.querySelector(
          ".kgg-inline-diff-badge,.kgg-inline-diff-ins,.kgg-inline-diff-del,.kgg-inline-diff-warn-ins,.kgg-inline-diff-warn-del,.kgg-inline-op,[data-kgg-owner-id],[data-kgg-owner-ids],[data-kgg-span-id]"
        );
      } catch (_) {}
    }
    try {
      blockEl.classList.toggle("is-empty", !hasContent);
      blockEl.setAttribute("data-empty", hasContent ? "false" : "true");
      blockEl.hidden = !hasContent;
    } catch (_) {}
    return hasContent;
  }

  function normalizeInlineDiffStateValue(value) {
    const v = String(value || "").trim().toLowerCase();
    if (v === "normal" || v === "warn" || v === "none") return v;
    throw new Error("Invalid canonical inline-diff state: " + String(value));
  }

  function normalizeInlineDiffCid(value) {
    const s = String(value == null ? "" : value).trim();
    if (!s) return "";
    const n = Number(s);
    return Number.isFinite(n) && n > 0 ? String(Math.trunc(n)) : s;
  }

  function getInlineDiffOwnerIdsForElement(el) {
    const out = [];
    const seen = new Set();
    function add(raw) {
      const value = String(raw || "").trim();
      if (!value || seen.has(value)) return;
      seen.add(value);
      out.push(value);
    }
    try {
      String(el.getAttribute("data-kgg-owner-ids") || "").split(/\s+/).forEach(add);
      add(el.getAttribute("data-kgg-owner-id") || "");
    } catch (_) {}
    return out;
  }

  function buildIndiffStateMapsForBlock(blockResult) {
    const block = normalizeIndiffBlockResult(blockResult, blockResult && blockResult.block_key);
    const maps = {
      ownerStateById: {},
      ownerCidById: {},
      spanStateById: {},
      spanCidById: {},
      partStateById: {},
      partCidById: {},
    };

    Array.from(block.spanRegistry).forEach((row) => {
      const sid = String(row && row.span_id || "");
      const ownerId = String(row && row.owner_id || "");
      const partId = String(row && row.part_id || "");
      const cid = normalizeInlineDiffCid(row && row.cid);
      if (sid) maps.spanCidById[sid] = cid;
      if (ownerId) maps.ownerCidById[ownerId] = cid;
      if (partId) maps.partCidById[partId] = cid;
    });

    Array.from(block.interactionTable).forEach((group) => {
      const stateMap = group && group.recommended_state_map && typeof group.recommended_state_map === "object"
        ? group.recommended_state_map
        : {};
      Object.keys(stateMap).forEach((sid) => {
        maps.spanStateById[String(sid)] = normalizeInlineDiffStateValue(stateMap[sid]);
      });
    });

    Array.from(block.styleTable).forEach((row) => {
      const cid = normalizeInlineDiffCid(row && row.cid);
      const ownerId = String(row && row.owner_id || "");
      const defaultState = normalizeInlineDiffStateValue(row && row.default_state);
      const partIds = Array.isArray(row && row.part_ids) ? row.part_ids.map((x) => String(x)).filter(Boolean) : [];
      const spanIds = Array.isArray(row && row.span_ids) ? row.span_ids.map((x) => String(x)).filter(Boolean) : [];
      const states = spanIds.map((sid) => maps.spanStateById[sid] ? normalizeInlineDiffStateValue(maps.spanStateById[sid]) : defaultState);
      const chosen = states.includes("warn") ? "warn" : (states.length && states.every((state) => state === "none") ? "none" : defaultState);
      if (ownerId) { maps.ownerCidById[ownerId] = cid; maps.ownerStateById[ownerId] = chosen; }
      partIds.forEach((pid) => { maps.partCidById[pid] = cid; maps.partStateById[pid] = chosen; });
      spanIds.forEach((sid) => {
        maps.spanCidById[sid] = maps.spanCidById[sid] || cid;
        maps.spanStateById[sid] = maps.spanStateById[sid] ? normalizeInlineDiffStateValue(maps.spanStateById[sid]) : chosen;
      });
    });
    return maps;
  }

  function indiffOwnerStateForElement(el, maps) {
    const ownerIds = getInlineDiffOwnerIdsForElement(el);
    if (!ownerIds.length) return "";
    const states = ownerIds.map((ownerId) => {
      if (!maps.ownerStateById[ownerId]) throw new Error("Canonical owner state missing: " + ownerId);
      return normalizeInlineDiffStateValue(maps.ownerStateById[ownerId]);
    });
    if (states.includes("warn")) return "warn";
    if (states.length && states.every((state) => state === "none")) return "none";
    return "normal";
  }

  function setIndiffUserState(el, state, cid) {
    const userState = normalizeInlineDiffStateValue(state);
    el.setAttribute("data-user-state", userState);
    const cidNorm = normalizeInlineDiffCid(cid);
    if (cidNorm) el.setAttribute("data-kgg-cid", cidNorm);
  }

  function isIndiffWhitespaceOrCommentNode(node) {
    if (!node) return false;
    if (node.nodeType === Node.COMMENT_NODE) return true;
    if (node.nodeType === Node.TEXT_NODE) return String(node.textContent || "").trim() === "";
    return false;
  }

  function isIndiffStructuralOpElement(node) {
    return !!(
      node &&
      node.nodeType === Node.ELEMENT_NODE &&
      node.classList &&
      node.classList.contains("kgg-inline-op")
    );
  }

  function isIndiffHiddenForListNumbering(el) {
    if (!el || el.nodeType !== Node.ELEMENT_NODE) return false;
    if (String(el.getAttribute("data-user-state") || "") === "none") return true;
    try {
      const op = el.closest ? el.closest(".kgg-inline-op[data-user-state='none']") : null;
      if (op) return true;
    } catch (_) {}
    return false;
  }

  function isVisibleDirectIndiffLi(li) {
    if (!li || li.nodeType !== Node.ELEMENT_NODE) return false;
    if (String(li.tagName || "").toLowerCase() !== "li") return false;
    return !isIndiffHiddenForListNumbering(li);
  }

  function directVisibleIndiffLiCount(ol) {
    if (!ol || ol.nodeType !== Node.ELEMENT_NODE) return 0;
    return Array.from(ol.children || []).filter(isVisibleDirectIndiffLi).length;
  }

  function previousIndiffOlContinuationStartFor(reopenedOl) {
    let visibleLiBefore = 0;
    let cur = reopenedOl ? reopenedOl.previousSibling : null;
    while (cur) {
      if (isIndiffWhitespaceOrCommentNode(cur)) {
        cur = cur.previousSibling;
        continue;
      }
      if (cur.nodeType === Node.ELEMENT_NODE) {
        const tag = String(cur.tagName || "").toLowerCase();
        if (isIndiffStructuralOpElement(cur)) {
          if (tag === "ol" && !isIndiffHiddenForListNumbering(cur)) {
            visibleLiBefore += directVisibleIndiffLiCount(cur);
          }
          cur = cur.previousSibling;
          continue;
        }
        if (tag === "ol") {
          visibleLiBefore += directVisibleIndiffLiCount(cur);
          return visibleLiBefore + 1;
        }
      }
      return 0;
    }
    return 0;
  }

  function normalizeIndiffReopenedOlStarts(blockEl) {
    if (!blockEl) return;
    Array.from(blockEl.querySelectorAll('ol[data-kgg-reopen-ol="true"]') || []).forEach((ol) => {
      try {
        const computedStart = previousIndiffOlContinuationStartFor(ol);
        const baselineStart = Number(ol.getAttribute("data-kgg-reopen-ol-start-baseline") || "1");
        const nextStart = computedStart > 1 ? computedStart : (baselineStart > 1 ? baselineStart : 1);
        if (nextStart > 1) ol.setAttribute("start", String(nextStart));
        else ol.removeAttribute("start");
        ol.setAttribute("data-kgg-reopen-ol-start-current", String(nextStart));
      } catch (_) {}
    });
  }

  function applyIndiffStateToBlockElement(blockEl, blockResult, articleId, versionIdOverride) {
    if (!blockEl || !blockResult || typeof blockResult !== "object") return;
    const maps = buildIndiffStateMapsForBlock(blockResult);

    Array.from(blockEl.querySelectorAll("[data-kgg-owner-id],[data-kgg-owner-ids]") || []).forEach((el) => {
      const ownerIds = getInlineDiffOwnerIdsForElement(el);
      const firstOwner = ownerIds[0] || String(el.getAttribute("data-kgg-owner-id") || "");
      const cid = maps.ownerCidById[firstOwner] || String(el.getAttribute("data-kgg-cid") || "");
      const state = indiffOwnerStateForElement(el, maps) || "normal";
      setIndiffUserState(el, state, cid);
    });

    Array.from(blockEl.querySelectorAll("[data-kgg-span-id]") || []).forEach((el) => {
      if (el.hasAttribute("data-kgg-owner-id") || el.hasAttribute("data-kgg-owner-ids")) return;
      const sid = String(el.getAttribute("data-kgg-span-id") || "");
      const cid = maps.spanCidById[sid] || String(el.getAttribute("data-kgg-cid") || "");
      if (!maps.spanStateById[sid]) throw new Error("Canonical span state missing: " + sid);
      const state = normalizeInlineDiffStateValue(maps.spanStateById[sid]);
      setIndiffUserState(el, state, cid);
    });

    Array.from(blockEl.querySelectorAll("[data-kgg-op-id]") || []).forEach((el) => {
      if (el.hasAttribute("data-kgg-owner-id") || el.hasAttribute("data-kgg-owner-ids")) return;
      const opId = String(el.getAttribute("data-kgg-op-id") || "");
      const m = opId.match(/^(.*)_(badge|ins|del)$/);
      const partId = m ? String(m[1] || "") : "";
      const cid = maps.partCidById[partId] || String(el.getAttribute("data-kgg-cid") || "");
      if (!partId || !maps.partStateById[partId]) throw new Error("Canonical part state missing: " + partId);
      const state = normalizeInlineDiffStateValue(maps.partStateById[partId]);
      setIndiffUserState(el, state, cid);
    });

    normalizeIndiffReopenedOlStarts(blockEl);
  }

  async function fetchInlineDiffPayload(articleId, activeIds, versionIdOverride) {
    const aid = Number(articleId || 0);
    const cids = Array.from(new Set(
      (Array.isArray(activeIds) ? activeIds : [])
        .map((x) => Number(x))
        .filter((n) => Number.isFinite(n) && n > 0)
    ));
    if (!Number.isFinite(aid) || aid <= 0) throw new Error("Invalid article id for canonical inline diff");
    if (!cids.length) throw new Error("Canonical inline diff requested without comment ids");

    const data = await apiFetchJson(
      "/api/merge-preview/multi",
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ aid, cids_in_apply_order: cids }),
      },
      { authRequired: !!(typeof klimaggAuth !== "undefined" && klimaggAuth && klimaggAuth.accessToken) }
    );

    if (!isPlainInlineDiffObject(data) || data.ok !== true || data.inline_engine_indiff !== true) {
      throw new Error("Invalid canonical inline-diff response");
    }
    if (Number(data.aid || 0) !== aid) throw new Error("Canonical inline-diff article mismatch");
    if (!Array.isArray(data.cids_in_apply_order)) throw new Error("Canonical inline-diff CID order missing");
    const returnedCids = data.cids_in_apply_order.map((x) => Number(x));
    if (returnedCids.length !== cids.length || returnedCids.some((cid, idx) => cid !== cids[idx])) {
      throw new Error("Canonical inline-diff CID order mismatch");
    }
    if (!isPlainInlineDiffObject(data.indiff_block_results) || !Object.keys(data.indiff_block_results).length) {
      throw new Error("Canonical inline-diff block results missing");
    }
    Object.keys(data.indiff_block_results).forEach((key) => normalizeIndiffBlockResult(data.indiff_block_results[key], key));
    if (!isPlainInlineDiffObject(data.comment_cards_by_cid_html)) throw new Error("Canonical comment-card HTML map missing");
    if (!isPlainInlineDiffObject(data.comment_card_rows_by_cid)) throw new Error("Canonical comment-card row map missing");

    return {
      ok: true,
      aid,
      cidsInApplyOrder: cids.map(String),
      commentCardsByCidHtml: data.comment_cards_by_cid_html,
      commentCardRowsByCid: data.comment_card_rows_by_cid,
      appliedParts: Array.isArray(data.applied_parts) ? data.applied_parts : [],
      counts: isPlainInlineDiffObject(data.counts) ? data.counts : {},
      inlineEngineIndiff: true,
      indiffBlockResults: data.indiff_block_results,
    };
  }

  function mountInlineDiffArticle(articleId, blockMap, rec, roots, versionIdOverride) {
    const canonicalBlocks = (blockMap && typeof blockMap === "object") ? blockMap : {};
    const blockResults = rec && rec.backendMergePayload && rec.backendMergePayload.indiffBlockResults && typeof rec.backendMergePayload.indiffBlockResults === "object"
      ? rec.backendMergePayload.indiffBlockResults
      : {};
    let anyMarkers = false;
    for (const r of (roots && roots.length ? roots : [])) {
      if (!r) continue;
      for (const B of BLOCKS) {
        const k = normalizeBlockKey(B.key);
        const els = Array.from(r.querySelectorAll(B.sel) || []);
        for (const el of els) {
          const hasCanonicalBlock = Object.prototype.hasOwnProperty.call(canonicalBlocks, k);
          if (!hasCanonicalBlock) continue;
          const html = String(canonicalBlocks[k] || "");
          el.innerHTML = html;
          applyIndiffStateToBlockElement(
            el,
            blockResults && typeof blockResults === "object" ? blockResults[k] : null,
            articleId,
            versionIdOverride
          );
          const hasMountedContent = updateInlineDiffBlockEmptyState(el, html);
          const hasMarkers = /\bkgg-inline-diff-(?:badge|ins|del|warn)\b|\bkgg-ghost-del\b|data-kgg-cid\s*=/i.test(String(html || ""));
          el.classList.toggle("kgg-merge-preview-active", !!hasMarkers);
          el.classList.toggle("inline-diff-active", !!hasMarkers);
          if (hasMarkers || hasMountedContent) anyMarkers = true;
        }
      }
      try { r.classList.toggle("article-merge-preview-active", !!anyMarkers); } catch (_) {}
    }
    rec.backendMergeBlocksByKey = canonicalBlocks;
    return {
      articleId: String(articleId || ""),
      mounted: true,
      blockCount: Object.keys(canonicalBlocks || {}).length,
      anyMarkers: !!anyMarkers,
    };
  }

  function resetInlineDiffRuntime(rec) {
    if (!rec || typeof rec !== "object") return;
    rec.backendMergeKey = "";
    rec.backendMergeBlocksByKey = null;
    rec.backendMergeError = "";
  }

  function updateInlineDiffCommentCardCache(rec, payload, options) {
    if (!rec || typeof rec !== "object") return;

    const replace = !!(options && options.replace);
    const cardsByCid =
      payload && payload.commentCardsByCidHtml && typeof payload.commentCardsByCidHtml === "object"
        ? payload.commentCardsByCidHtml
        : null;
    const rowsByCid =
      payload && payload.commentCardRowsByCid && typeof payload.commentCardRowsByCid === "object"
        ? payload.commentCardRowsByCid
        : null;

    const nextCards = replace
      ? {}
      : (
          rec.backendCommentCardsByCid && typeof rec.backendCommentCardsByCid === "object"
            ? Object.assign({}, rec.backendCommentCardsByCid)
            : {}
        );
    const nextRows = replace
      ? {}
      : (
          rec.backendCommentCardRowsByCid && typeof rec.backendCommentCardRowsByCid === "object"
            ? Object.assign({}, rec.backendCommentCardRowsByCid)
            : {}
        );

    if (cardsByCid) {
      Object.keys(cardsByCid).forEach((cid) => {
        nextCards[String(cid)] = String(cardsByCid[cid] || "");
      });
    }
    if (rowsByCid) {
      Object.keys(rowsByCid).forEach((cid) => {
        nextRows[String(cid)] = Array.isArray(rowsByCid[cid]) ? rowsByCid[cid] : [];
      });
    }

    rec.backendCommentCardsByCid = nextCards;
    rec.backendCommentCardRowsByCid = nextRows;
  }

  function prepareInlineDiffContext(articleId, versionIdOverride) {
    const scope = resolveInlineDiffScope(articleId, versionIdOverride);
    const aid = scope.articleId;
    const versionId = scope.versionId;
    const roots = (typeof findArticleRoots === "function") ? findArticleRoots(aid) : [];
    const root = (roots && roots.length) ? roots[0] : findArticleRoot(aid);
    if (!root) return null;
    const allRoots = (roots && roots.length) ? roots : [root];
    const rec = ensureRec(scope.scopeKey);
    for (const r of allRoots) captureOrig(r, rec);
    return {
      scope,
      aid,
      versionId,
      root,
      roots: allRoots,
      rec,
    };
  }

  function clearInlineDiffDisplay(articleId, versionIdOverride, rec, options) {
    const aid = String(articleId || "").trim();
    const allRoots = collectInlineDiffRoots(aid, versionIdOverride);
    allRoots.forEach((r) => restore(r, rec));
    try {
      resetInlineDiffRuntime(rec);
    } catch (_) {}
    try {
      const aidKey = String(aid || "").trim();
      const loadedLists = Array.from(
        document.querySelectorAll('.article-comments[data-article-id="' + cssEscape(aidKey) + '"] .article-comments-list[data-loaded="true"]')
      );
      if (loadedLists.length) {
        const cached = (window.__klimaggCommentsCacheByArticleId && window.__klimaggCommentsCacheByArticleId[String(aidKey)])
          ? window.__klimaggCommentsCacheByArticleId[String(aidKey)]
          : [];
        loadedLists.forEach((listEl) => {
          try {
            renderCommentsIntoList(aidKey, Array.isArray(cached) ? cached : [], listEl);
          } catch (_) {}
        });
      }
    } catch (_) {}
    try {
      applyCommentBadgeVisibility();
    } catch (_) {}
    try { updateCommentOverlayResetControlState(); } catch (_) {}
    return true;
  }

  function invalidateInlineDiffCacheIfKeyChanged(rec, canonicalKey) {
    if (!rec || typeof rec !== "object") return false;
    const nextKey = String(canonicalKey || "");
    if (String(rec.backendMergeKey || "") === nextKey) return false;
    rec.backendMergeKey = nextKey;
    rec.backendMergeBlocksByKey = null;
    rec.backendMergeError = "";
    return true;
  }

  function applyInlineDiffSuccessState(articleId, scope, payload, rec) {
    const aid = String((scope && scope.articleId) || articleId || "").trim();
    const versionId = (scope && scope.versionId != null) ? scope.versionId : "";
    const allRoots = collectInlineDiffRoots(aid, versionId);
    allRoots.forEach((r) => restore(r, rec));
    mountInlineDiffArticle(
      aid,
      (rec && rec.backendMergeBlocksByKey && typeof rec.backendMergeBlocksByKey === "object")
        ? rec.backendMergeBlocksByKey
        : {},
      rec,
      allRoots,
      versionId
    );
    try {
      applyCommentBadgeVisibility();
    } catch (_) {}
    try { updateCommentOverlayResetControlState(); } catch (_) {}
    try {
      const aidKey = String(aid || articleId || "").trim();
      const loadedLists = Array.from(
        document.querySelectorAll('.article-comments[data-article-id="' + cssEscape(aidKey) + '"] .article-comments-list[data-loaded="true"]')
      );
      if (loadedLists.length) {
        const cached = (window.__klimaggCommentsCacheByArticleId && window.__klimaggCommentsCacheByArticleId[String(aidKey)])
          ? window.__klimaggCommentsCacheByArticleId[String(aidKey)]
          : [];
        loadedLists.forEach((listEl) => {
          try {
            renderCommentsIntoList(aidKey, Array.isArray(cached) ? cached : [], listEl);
          } catch (_) {}
        });
      } else {
        refreshCommentsForArticle(aidKey);
      }
    } catch (_) {}
    return true;
  }

  function applyInlineDiffFailureState(articleId, scope, err, rec) {
    const aid = String((scope && scope.articleId) || articleId || "").trim();
    const versionId = (scope && scope.versionId != null) ? scope.versionId : "";
    const allRoots = collectInlineDiffRoots(aid, versionId);
    const reason =
      (err && err.message) ? String(err.message) :
      (typeof err === "string" && err.trim()) ? String(err).trim() :
      "canonical_merge_unavailable";
    try {
      setCanonicalMergeFailureState(
        allRoots,
        true,
        reason
      );
    } catch (_) {}
    try {
      for (const r of allRoots) restore(r, rec);
    } catch (_) {}
    try {
      resetInlineDiffRuntime(rec);
    } catch (_) {}
    try {
      for (const r of allRoots) r.classList.remove("article-merge-preview-active");
    } catch (_) {}
    try {
      const loadedLists = Array.from(
        document.querySelectorAll('.article-comments[data-article-id="' + cssEscape(aid) + '"] .article-comments-list[data-loaded="true"]')
      );
      if (loadedLists.length) {
        const cached = (window.__klimaggCommentsCacheByArticleId && window.__klimaggCommentsCacheByArticleId[String(aid)])
          ? window.__klimaggCommentsCacheByArticleId[String(aid)]
          : [];
        loadedLists.forEach((listEl) => {
          try {
            renderCommentsIntoList(aid, Array.isArray(cached) ? cached : [], listEl);
          } catch (_) {}
        });
      }
    } catch (_) {}
    try { updateCommentBadges(aid, rec); } catch (_) {}
    try { applyCommentBadgeVisibility({ silent: true, syncControl: true }); } catch (_) {}
    try { updateCommentOverlayResetControlState(); } catch (_) {}
    return true;
  }

  function mountInlineDiffCommentCards(articleId, scope, payload, rec) {
    const safeScope = resolveInlineDiffScope(
      articleId,
      scope && typeof scope === "object" ? scope.versionId : ""
    );
    const targetRec = rec || ensureRec(safeScope.scopeKey);
    const cardsByCid = payload && payload.commentCardsByCidHtml && typeof payload.commentCardsByCidHtml === "object"
      ? payload.commentCardsByCidHtml
      : {};
    updateInlineDiffCommentCardCache(targetRec, payload, { replace: false });
    try {
      const box = document.querySelector('.article-comments[data-article-id="' + cssEscape(String(articleId || "")) + '"]');
      const listEl = box ? box.querySelector(".article-comments-list") : null;
      const cached = (window.__klimaggCommentsCacheByArticleId && window.__klimaggCommentsCacheByArticleId[String(articleId)])
        ? window.__klimaggCommentsCacheByArticleId[String(articleId)]
        : null;
      if (listEl && listEl.dataset.loaded === "true" && Array.isArray(cached)) {
        renderCommentsIntoList(Number(articleId), cached, listEl);
      } else if (
        box &&
        !box.hidden &&
        listEl &&
        listEl.dataset.loaded !== "true" &&
        listEl.dataset.loaded !== "loading" &&
        Object.keys(cardsByCid || {}).length > 0
      ) {
        try { loadCommentsForArticle(Number(articleId), listEl); } catch (_) {}
      }
    } catch (_) {}
    return {
      articleId: String(articleId || ""),
      mounted: true,
      cardCount: Object.keys(cardsByCid || {}).length,
    };
  }

  async function refreshInlineDiff(articleId, versionIdOverride, options) {
    const ctx = prepareInlineDiffContext(articleId, versionIdOverride, options);
    if (!ctx) return false;
    const { scope, rec, aid, versionId } = ctx;
    const activeIds = getActiveInlineDiffIds(aid, versionId);
    if (!activeIds.length) {
      return clearInlineDiffDisplay(aid, versionId, rec, { source: "refreshInlineDiff.noActiveIds" });
    }

    const canonicalKey = String(aid) + "::" + String(versionId || "") + "::" + activeIds.join(",");
    invalidateInlineDiffCacheIfKeyChanged(rec, canonicalKey);

    try {
      const existingBlocks = rec && rec.backendMergeBlocksByKey && typeof rec.backendMergeBlocksByKey === "object"
        ? Object.keys(rec.backendMergeBlocksByKey)
        : [];
      if (existingBlocks.length) {
        return applyInlineDiffSuccessState(aid, scope, rec.backendMergePayload || {}, rec);
      }

      if (!rec.backendMergePromise) {
        rec.backendMergePromise = (async () => {
          const payload = await fetchInlineDiffPayload(aid, activeIds, versionId);
          const parsedBlocks = parseInlineDiffArticleBlocks(payload);
          rec.backendMergePayload = payload || {};
          rec.backendMergeBlocksByKey = parsedBlocks || {};
          mountInlineDiffCommentCards(aid, scope, payload || {}, rec);
          return payload || {};
        })();
      }

      await rec.backendMergePromise;
      rec.backendMergePromise = null;

      const readyBlocks = rec && rec.backendMergeBlocksByKey && typeof rec.backendMergeBlocksByKey === "object"
        ? Object.keys(rec.backendMergeBlocksByKey)
        : [];
      if (readyBlocks.length) {
        return applyInlineDiffSuccessState(aid, scope, rec.backendMergePayload || {}, rec);
      }
      return clearInlineDiffDisplay(aid, versionId, rec, { source: "refreshInlineDiff.emptyBlocksAfterFetch" });
    } catch (err) {
      rec.backendMergePromise = null;
      rec.backendMergePayload = {};
      rec.backendMergeBlocksByKey = {};
      applyInlineDiffFailureState(aid, scope, err, rec);
      return false;
    }
  }

  function getInlineDiffApiDescriptor() {
    return {
      version: INLINE_DIFF_API_VERSION,
      contract: INLINE_DIFF_API_CONTRACT,
      rules: {
        articleSource: INLINE_DIFF_API_CONTRACT.articleHtmlSource,
        commentCardSource: INLINE_DIFF_API_CONTRACT.commentCardHtmlSource,
        requestEndpoint: INLINE_DIFF_API_CONTRACT.endpoint,
        finalDomStateAttribute: INLINE_DIFF_API_CONTRACT.finalDomStateAttribute,
        primaryScope: INLINE_DIFF_API_CONTRACT.primaryScope,
        frontendSemanticsAllowed: false,
      },
    };
  }

  const INLINE_DIFF_API = Object.freeze({
      version: INLINE_DIFF_API_VERSION,
      contract: INLINE_DIFF_API_CONTRACT,
      descriptor: getInlineDiffApiDescriptor,
      getScope: getInlineDiffScope,
      getActiveIds: getActiveInlineDiffIds,
      setActive: function(articleId, commentId, on, versionIdOverride) {
        return INLINE_DIFF_STATE.setLayerActive(articleId, commentId, on, versionIdOverride);
      },
      clear: function(articleId, versionIdOverride) {
        return INLINE_DIFF_STATE.clearAllForArticle(articleId, versionIdOverride);
      },
      toggle: applyInlineDiffToggle,
      toggleDraft: applyInlineDiffDraftToggle,
      fetch: fetchInlineDiffPayload,
      refresh: refreshInlineDiff,
      refreshWithDraft: function(articleId, versionIdOverride, draftCommentId, options) {
        return applyInlineDiffDraftToggle(articleId, versionIdOverride, draftCommentId, true, options);
      },
  });

  try {
    window.KlimaGG = window.KlimaGG || {};
    window.KlimaGG.inlineDiff = INLINE_DIFF_API;
    window.KlimaGG.inlineDiffState = INLINE_DIFF_STATE;
  } catch (_) {}

  try {
    ns.inspectContract = function() { return getInlineDiffApiDescriptor(); };
    ns.inspectLastPayload = function(articleId, versionIdOverride) {
      const rec = getInlineDiffDebugRecord(articleId, versionIdOverride);
      return normalizeIndiffMergePayloadForInspect(rec && rec.backendMergePayload ? rec.backendMergePayload : {});
    };
    ns.inspectCachedBlocks = function(articleId, versionIdOverride) {
      const rec = getInlineDiffDebugRecord(articleId, versionIdOverride);
      const blocks = rec && rec.backendMergeBlocksByKey && typeof rec.backendMergeBlocksByKey === "object"
        ? rec.backendMergeBlocksByKey
        : {};
      const out = {};
      objectKeysSafe(blocks).forEach((key) => { out[String(key)] = String(blocks[key] || "").length; });
      return out;
    };
    ns.inspectStoreSummary = function(articleId) {
      const aid = String(articleId || "").trim();
      const s = store();
      const byKey = s && s.byKey && typeof s.byKey === "object" ? s.byKey : {};
      return Object.keys(byKey).filter((key) => {
        return !aid || String(key || "").startsWith(aid + ":");
      }).map((key) => {
        const rec = byKey[key] || {};
        const payload = rec.backendMergePayload && typeof rec.backendMergePayload === "object"
          ? rec.backendMergePayload
          : {};
        const payloadBlocks = payload.indiffBlockResults && typeof payload.indiffBlockResults === "object"
          ? Object.keys(payload.indiffBlockResults).length
          : 0;
        const blocks = rec.backendMergeBlocksByKey && typeof rec.backendMergeBlocksByKey === "object"
          ? Object.keys(rec.backendMergeBlocksByKey).length
          : 0;
        const cards = rec.backendCommentCardsByCid && typeof rec.backendCommentCardsByCid === "object"
          ? Object.keys(rec.backendCommentCardsByCid).length
          : 0;
        const rows = rec.backendCommentCardRowsByCid && typeof rec.backendCommentCardRowsByCid === "object"
          ? Object.keys(rec.backendCommentCardRowsByCid).length
          : 0;
        return {
          key,
          score: inlineDiffRecordDataScore(rec),
          hasPayload: !!rec.backendMergePayload,
          payloadBlocks,
          cachedBlocks: blocks,
          cachedCards: cards,
          cachedRows: rows,
          hasPromise: !!rec.backendMergePromise,
          error: String(rec.backendMergeError || ""),
        };
      }).sort((a, b) => b.score - a.score);
    };
    ns.auditArticle = function(articleId, versionIdOverride) {
      return auditInlineDiffArticleDom(articleId, versionIdOverride);
    };
  } catch (_) {}

  function normalizeBlockKey(raw) {
    const key = String(raw || "").trim().toLowerCase();
    if (!["meta", "kurzinfo", "story", "einleitung", "juristisch", "juristisch2", "anmerkung"].includes(key)) {
      throw new Error("Invalid canonical inline-diff block key: " + String(raw));
    }
    return key;
  }

  const BLOCKS = [
    { key: "meta", sel: ".content-block.meta" },
    { key: "kurzinfo", sel: ".content-block.kurzinfo" },
    { key: "einleitung", sel: ".content-block.einleitung" },
    { key: "story", sel: ".content-block.story" },
    { key: "juristisch", sel: ".content-block.juristisch" },
    { key: "juristisch2", sel: ".content-block.juristisch2" },
    { key: "anmerkung", sel: ".content-block.anmerkung" },
  ];

  
  
  function findArticleRoots(articleId) {
    const out = [];
    const push = (el) => {
      try {
        if (!el) return;
        if (out.indexOf(el) !== -1) return;
        out.push(el);
      } catch (_) {}
    };
    try {
      if (typeof findArticleRootById === "function") push(findArticleRootById(articleId));
    } catch (_) {}
    const id = String(articleId || "");
    try { Array.from(document.querySelectorAll('.article-wrap[data-article-id="' + id + '"]')).forEach(push); } catch (_) {}
    try { Array.from(document.querySelectorAll('article[data-article-id="' + id + '"]')).forEach(push); } catch (_) {}
    try {
      for (const r of out.slice()) {
        if (!r || typeof r.querySelectorAll !== "function") continue;
        Array.from(r.querySelectorAll('article[data-article-id="' + id + '"]')).forEach(push);
        Array.from(r.querySelectorAll('.article-wrap[data-article-id="' + id + '"]')).forEach(push);
      }
    } catch (_) {}
    return out.filter(Boolean);
  }

  function findArticleRoot(articleId) {
    return (findArticleRoots(articleId)[0] || null);
  }

  
  // Usage: KlimaGG.inlineDiffTools.debugRoots(27)
  function debugRoots(articleId) {
    const aid = String(articleId || "");
    const roots = findArticleRoots(aid);
    let mode = "unknown";
    try { mode = (typeof getVersionDiffMode === "function") ? getVersionDiffMode() : "n/a"; } catch (_) { mode = "err"; }

    const sels = BLOCKS.map((b) => b.sel).join(",");
    const markerSel =
      '.kgg-inline-op,.kgg-inline-cid,[data-kgg-owner-id],[data-kgg-owner-ids],[data-kgg-span-id]';
    const countMarkers = (r) => {
      try {
        const blocks = sels ? Array.from(r.querySelectorAll(sels)) : [];
        return blocks.reduce((sum, el) => sum + (el.querySelectorAll ? el.querySelectorAll(markerSel).length : 0), 0);
      } catch (_) { return 0; }
    };
    console.log("[KlimaGG][MergePreview] roots#", roots.length, "mode", mode);
    roots.forEach((r, i) => {
      const vid =
        r.getAttribute("data-current-version-id") ||
        r.getAttribute("data-version-id") ||
        (r.dataset ? (r.dataset.currentVersionId || r.dataset.versionId) : "");
      console.log(`root[${i}] tag=${r.tagName} class=${r.className} vid=${vid} markers=${countMarkers(r)}`);
    });
    return roots.length;
  }

  
  
  function inlineDiffHtmlHasArtifacts(html) {
    return /\bkgg-inline-op\b|\bkgg-inline-cid\b|\bkgg-conflict-inline-marker\b|\bminimd-(ins|del|warn)\b|\bminimd-diff-inline\b|\bkgg-merged-diff\b|data-kgg-cid\b|data-kgg-part\b/.test(String(html || ""));
  }

  function assertCleanInlineDiffBaseline(html, context) {
    if (inlineDiffHtmlHasArtifacts(html)) {
      throw new Error("Inline-diff baseline contains diff artifacts: " + String(context || "unknown"));
    }
  }

  async function clearAllOverlays(articleId, opts) {
    const aid = String(articleId || "");
    const o = (opts && typeof opts === "object") ? opts : {};
    const disableVDM = (o.disableVersionDiffMode !== false);
    try { clearInlineDiffStateForArticleSafe(aid, undefined); } catch (_) {}
    if (disableVDM) {
      try { if (typeof setVersionDiffMode === "function") setVersionDiffMode("off"); } catch (_) {}
      try {
        if (typeof applyVersionDiffModeToRenderedArticles === "function") {
          await applyVersionDiffModeToRenderedArticles({ silent: true });
        }
      } catch (_) {}
    }
    try { await refreshInlineDiffSafe(aid, undefined, { source: "clearAllOverlays" }); } catch (_) {}
    return true;
  }

  function captureOrig(root, rec) {
    for (const b of BLOCKS) {
      const els = Array.from(root.querySelectorAll(b.sel) || []);
      if (!els.length) continue;
      for (const el of els) {
        const frozen = el.dataset && el.dataset.kggBaselineFrozen === "1";
        const source = frozen && typeof el.dataset.kggBaseHtml === "string"
          ? String(el.dataset.kggBaseHtml)
          : String(el.innerHTML || "");
        assertCleanInlineDiffBaseline(source, b.key || b.sel);
        if (!frozen) {
          el.dataset.kggBaseHtml = source;
          el.dataset.kggBaseText = String(el.textContent || "").replace(/\s+/g, " ").trim();
          el.dataset.kggBaselineFrozen = "1";
        }
        if (rec.orig[b.sel] == null) {
          rec.orig[b.sel] = source;
        } else if (String(rec.orig[b.sel]) !== source) {
          throw new Error("Inline-diff baseline mismatch for " + String(b.key || b.sel));
        }
      }
    }
  }

  function restore(root, rec) {
    for (const b of BLOCKS) {
      const els = Array.from(root.querySelectorAll(b.sel) || []);
      if (!els.length) continue;
      for (const el of els) {
        let source = null;
        if (el.dataset && el.dataset.kggBaselineFrozen === "1" && typeof el.dataset.kggBaseHtml === "string") {
          source = String(el.dataset.kggBaseHtml);
        } else if (rec && rec.orig && rec.orig[b.sel] != null) {
          source = String(rec.orig[b.sel]);
        }
        if (source == null) throw new Error("Inline-diff baseline missing for " + String(b.key || b.sel));
        assertCleanInlineDiffBaseline(source, b.key || b.sel);
        el.innerHTML = source;
        el.classList.remove("kgg-merge-preview-active", "kgg-merged-conflict", "inline-diff-active");
        updateInlineDiffBlockEmptyState(el, source);
      }
    }
    root.classList.remove("article-merge-preview-active");
  }

  
  function baselineAudit(articleId, versionIdOverride) {
    const aid = Number(articleId);
    const roots = (typeof findArticleRoots === "function") ? findArticleRoots(aid) : [];
    const root0 = roots && roots.length ? roots[0] : (findArticleRoot ? findArticleRoot(aid) : null);
    const vid = versionIdOverride || (typeof getCurrentVersionIdForArticle === "function" ? getCurrentVersionIdForArticle(aid) : null);

    const rows = [];
    if (!root0) return { aid, vid, rows, ok: false, reason: "no_root" };

    const hasArtifactsHtml = (html) => inlineDiffHtmlHasArtifacts(html);
    const hasArtifactsEl = (el) => !!(el && inlineDiffHtmlHasArtifacts(String(el.innerHTML || "")));

    const rootsToCheck = (roots && roots.length) ? roots : [root0];
    for (const b of BLOCKS) {
      const els = [];
      for (const r of rootsToCheck) {
        try {
          const el = r && r.querySelector ? r.querySelector(b.sel) : null;
          if (el) els.push(el);
        } catch (_) {}
      }
      if (!els.length) continue;
      const frozen = els.every((el) => (el.dataset && el.dataset.kggBaselineFrozen === "1"));
      const baseHtml = (els[0].dataset && typeof els[0].dataset.kggBaseHtml === "string") ? String(els[0].dataset.kggBaseHtml) : "";
      const baseText = (els[0].dataset && typeof els[0].dataset.kggBaseText === "string") ? String(els[0].dataset.kggBaseText) : "";
      const curHtml = String(els[0].innerHTML || "");
      rows.push({
        aid,
        vid,
        block: String(b.key || b.sel),
        frozen,
        rootCount: rootsToCheck.length,
        baseHtmlLen: baseHtml.length,
        baseTextLen: baseText.length,
        baseHasArtifacts: hasArtifactsHtml(baseHtml),
        curHasArtifacts: els.some((el) => hasArtifactsEl(el)),
        curHtmlLen: curHtml.length,
        curHead: curHtml.replace(/\s+/g, " ").trim().slice(0, 90),
      });
    }

    const ok = rows.every((r) => r.frozen && r.baseHtmlLen >= 0 && !r.baseHasArtifacts);
    return { aid, vid, rows, ok };
  }

  function readHighlights(articleId, versionIdOverride) {
    const raw = localStorage.getItem(hlKey());
    const map = raw ? JSON.parse(raw) : {};
    if (!map || typeof map !== "object" || Array.isArray(map)) {
      throw new Error("Invalid inline-diff highlight state");
    }
    const sk = hlScopeKey(articleId, versionIdOverride);
    const obj = map[sk] == null ? {} : map[sk];
    if (!obj || typeof obj !== "object" || Array.isArray(obj)) {
      throw new Error("Invalid inline-diff highlight scope state: " + sk);
    }
    return Object.keys(obj).filter((cid) => obj[cid] === true);
  }

  function clearHighlights(articleId, versionIdOverride) {
    const raw = localStorage.getItem(hlKey());
    const map = raw ? JSON.parse(raw) : {};
    if (!map || typeof map !== "object" || Array.isArray(map)) {
      throw new Error("Invalid inline-diff highlight state");
    }
    const aid = String(articleId || "");
    const hasOverride = (versionIdOverride != null) && String(versionIdOverride).trim() !== "";
    if (hasOverride) {
      delete map[hlScopeKey(articleId, versionIdOverride)];
    } else {
      const prefix = aid + ":";
      for (const key of Object.keys(map)) {
        if (String(key).startsWith(prefix)) delete map[key];
      }
    }
    localStorage.setItem(hlKey(), JSON.stringify(map));
  }

  function setHighlights(articleId, idsOn, versionIdOverride) {
    const raw = localStorage.getItem(hlKey());
    const map = raw ? JSON.parse(raw) : {};
    if (!map || typeof map !== "object" || Array.isArray(map)) {
      throw new Error("Invalid inline-diff highlight state");
    }
    const sk = hlScopeKey(articleId, versionIdOverride);
    map[sk] = {};
    (Array.isArray(idsOn) ? idsOn : []).forEach((x) => { map[sk][String(x)] = true; });
    localStorage.setItem(hlKey(), JSON.stringify(map));
  }

  function writeHighlight(articleId, commentId, on, versionIdOverride) {
    const sid = String(commentId);
    const set = new Set(readHighlights(articleId, versionIdOverride).map(String));
    if (on) set.add(sid);
    else set.delete(sid);
    setHighlights(articleId, Array.from(set), versionIdOverride);
  }

  function updateCommentBadges(articleId, rec) {
    const aid = String(articleId || "");
    const list = document.getElementById("comments-list-" + aid) || document.querySelector('[data-comments-for="' + aid + '"]') || document;
    const nodes = list.querySelectorAll('.article-comment[data-comment-id]');
    nodes.forEach((n) => {
      const cid = String(n.getAttribute("data-comment-id") || "");
      const isC = !!(rec && rec.conflictByCid && rec.conflictByCid[cid]);
      const parts = (rec && rec.conflictPartsByCid && Array.isArray(rec.conflictPartsByCid[cid]))
        ? rec.conflictPartsByCid[cid]
        : [];

      const tooltipFromParts = (() => {
        try {
          if (!parts.length) return "";
          
          return parts
            .map((p) => {
              const b = p && p.block_key ? String(p.block_key) : "";
              const pid = p && p.part_id ? String(p.part_id) : "";
              const r = p && p.reason ? String(p.reason) : "";
              return (b ? ("Block " + b) : "Block ?") + (pid ? (", Part " + pid) : "") + (r ? (": " + r) : "");
            })
            .slice(0, 6)
            .join(" | ");
        } catch (_) { return ""; }
      })();

      let badge = n.querySelector(".kgg-comment-merge-badge");
        const reasonStr = (rec && rec.conflictReasonByCid && rec.conflictReasonByCid[cid]) ? String(rec.conflictReasonByCid[cid]) : "";
        const isHardConflict = (() => {
          try {
            const r = (reasonStr || "").toLowerCase();
            if (r.includes("overlap") || r.includes("mehrdeutig") || r.includes("ambig")) return true;
            for (const p of parts) {
              const pr = (p && p.reason) ? String(p.reason).toLowerCase() : "";
              if (pr.includes("overlap") || pr.includes("mehrdeutig") || pr.includes("ambig")) return true;
            }
          } catch (_) {}
          return false;
        })();
        const badgeText = isHardConflict ? "Konflikt" : "Hinweis";

      if (isC) {
        if (!badge) {
          badge = document.createElement("span");
          badge.className = "kgg-comment-merge-badge";
          badge.textContent = badgeText;
          const meta = n.querySelector(".article-comment-meta") || n;
          meta.appendChild(badge);
        } else {
          badge.textContent = badgeText;
        }
        try {
          const reason = rec && rec.conflictReasonByCid ? rec.conflictReasonByCid[cid] : null;
          if (tooltipFromParts) badge.title = tooltipFromParts;
          else if (reason) badge.title = String(reason);
        } catch (_) {}
      } else if (badge) {
        badge.remove();
      }
    });
  }

  function setCanonicalMergeFailureState(roots, enabled, reason) {
    const rs = Array.isArray(roots) ? roots : [];
    rs.forEach((r) => {
      if (!r || !r.classList) return;
      try {
        if (enabled) {
          r.classList.add("article-merge-preview-failed");
          r.setAttribute("data-kgg-merge-preview-error", String(reason || "canonical_merge_unavailable"));
          let banner = null;
          try { banner = r.querySelector(":scope > .kgg-merge-preview-error"); } catch (_) { banner = r.querySelector(".kgg-merge-preview-error"); }
          if (!banner) {
            banner = document.createElement("div");
            banner.className = "kgg-merge-preview-error";
            banner.setAttribute("role", "status");
            banner.textContent = "Inline-Diff konnte nicht geladen werden (kanonisches Backend-Rendering fehlgeschlagen).";
            try { r.prepend(banner); } catch (_) {}
          }
        } else {
          r.classList.remove("article-merge-preview-failed");
          r.removeAttribute("data-kgg-merge-preview-error");
          const banner = r.querySelector(".kgg-merge-preview-error");
          if (banner) banner.remove();
        }
      } catch (_) {}
    });
  }

  function normalizeVersionLayerPayload(raw, articleId, mode) {
    if (!isPlainInlineDiffObject(raw) || raw.ok !== true || raw.inline_engine_indiff !== true) {
      throw new Error("Invalid canonical version-layer response");
    }
    if (mode !== "next_draft" && mode !== "last_version") {
      throw new Error("Unknown canonical version-layer mode");
    }
    const expectedAid = Number(articleId || 0);
    const actualAid = Number(raw.article_id);
    if (!Number.isFinite(actualAid) || actualAid !== expectedAid) {
      throw new Error("Canonical version-layer article mismatch");
    }
    const expectedLayerId = mode === "last_version" ? "LW" : "ND";
    if (raw.layer_type !== mode || raw.layer_id !== expectedLayerId) {
      throw new Error("Canonical version-layer identity mismatch");
    }
    if (typeof raw.is_empty !== "boolean") {
      throw new Error("Canonical version-layer is_empty flag missing");
    }
    if (!isPlainInlineDiffObject(raw.indiff_block_results)) {
      throw new Error("Canonical version-layer block results missing");
    }
    Object.keys(raw.indiff_block_results).forEach((key) => normalizeIndiffBlockResult(raw.indiff_block_results[key], key));
    if (!raw.is_empty && !Object.keys(raw.indiff_block_results).length) {
      throw new Error("Canonical version-layer response is non-empty without block results");
    }

    let cidsInApplyOrder = [];
    let currentVersionId = "";
    let previousVersionId = "";
    if (mode === "next_draft") {
      if (raw.version_id == null || !Array.isArray(raw.comment_ids) || !Array.isArray(raw.cids_in_apply_order)) {
        throw new Error("Canonical Next-Draft identity fields missing");
      }
      const commentIds = raw.comment_ids.map((x) => String(x));
      const appliedIds = raw.cids_in_apply_order.map((x) => String(x));
      if (commentIds.length !== appliedIds.length || commentIds.some((cid, idx) => cid !== appliedIds[idx])) {
        throw new Error("Canonical Next-Draft comment order mismatch");
      }
      cidsInApplyOrder = appliedIds;
      currentVersionId = String(raw.version_id);
    } else {
      if (!raw.is_empty && raw.current_version_id == null) {
        throw new Error("Canonical Last-Version current version missing");
      }
      currentVersionId = raw.current_version_id != null ? String(raw.current_version_id) : "";
      previousVersionId = raw.previous_version_id != null ? String(raw.previous_version_id) : "";
    }

    return {
      ok: true,
      aid: actualAid,
      cidsInApplyOrder,
      inlineEngineIndiff: true,
      indiffBlockResults: raw.indiff_block_results,
      layerType: mode,
      layerId: expectedLayerId,
      currentVersionId,
      previousVersionId,
      isEmpty: raw.is_empty,
      cache: isPlainInlineDiffObject(raw.cache) ? raw.cache : {},
    };
  }

  function setVersionLayerRootState(articleId, versionId, mode) {
    const roots = collectInlineDiffRoots(articleId, versionId);
    roots.forEach((root) => {
      if (!root || !root.classList) return;
      root.classList.remove("kgg-version-layer-next-draft", "kgg-version-layer-last-version");
      try { root.removeAttribute("data-kgg-version-layer"); } catch (_) {}
      if (mode === "next_draft") {
        root.classList.add("kgg-version-layer-next-draft");
        root.setAttribute("data-kgg-version-layer", "ND");
      } else if (mode === "last_version") {
        root.classList.add("kgg-version-layer-last-version");
        root.setAttribute("data-kgg-version-layer", "LW");
      }
    });
  }

  async function fetchVersionLayerPayload(mode, articleId, versionId) {
    const opts = { authRequired: false };
    if (mode === "next_draft") {
      const qs = versionId ? ("?version_id=" + encodeURIComponent(String(versionId))) : "";
      const data = await apiFetchJson(
        "/api/articles/" + encodeURIComponent(String(articleId)) + "/layers/next-draft" + qs,
        { method: "GET" },
        opts
      );
      return normalizeVersionLayerPayload(data, articleId, mode);
    }
    const data = await apiFetchJson(
      "/api/articles/" + encodeURIComponent(String(articleId)) + "/layers/last-version",
      { method: "GET" },
      opts
    );
    return normalizeVersionLayerPayload(data, articleId, mode);
  }

  function applyPreparedVersionLayerPayload(mode, aid, vid, payload) {
    if (!payload || payload.ok !== true) {
      throw new Error("Canonical version-layer payload missing");
    }
    const ids = Array.isArray(payload.cidsInApplyOrder) ? payload.cidsInApplyOrder.map(String).filter(Boolean) : [];
    if (mode === "next_draft") setNextDraftInlineDiffIdsSafe(aid, ids);
    else clearNextDraftInlineDiffIdsSafe(aid);

    const ctx = prepareInlineDiffContext(aid, vid || undefined);
    if (!ctx) {
      throw new Error("Rendered article context missing for canonical version layer");
    }
    const canonicalKey = [
      "version-layer",
      mode,
      String(ctx.aid),
      String(ctx.versionId || ""),
      String(payload.layerId || ""),
      String(payload.previousVersionId || ""),
      String(payload.currentVersionId || ""),
      ids.join(","),
    ].join("::");
    invalidateInlineDiffCacheIfKeyChanged(ctx.rec, canonicalKey);
    const parsedBlocks = parseInlineDiffArticleBlocks(payload);
    ctx.rec.backendMergePayload = payload || {};
    ctx.rec.backendMergeBlocksByKey = parsedBlocks || {};
    ctx.rec.backendMergePromise = null;

    if (Object.keys(parsedBlocks || {}).length) {
      applyInlineDiffSuccessState(ctx.aid, ctx.scope, payload || {}, ctx.rec);
    } else {
      clearInlineDiffDisplay(ctx.aid, ctx.versionId, ctx.rec, { source: "versionLayer.empty" });
    }
    setVersionLayerRootState(ctx.aid, ctx.versionId, mode);
    return true;
  }

  async function applyVersionLayerMode(rawMode, { silent = false } = {}) {
    const mode = normalizeVersionDiffMode(rawMode);
    const targets = listRenderedInlineDiffTargets();

    if (mode === "off") {
      for (const { articleId, versionId } of targets) {
        try { clearNextDraftInlineDiffIdsSafe(articleId); } catch (_) {}
        setVersionLayerRootState(articleId, versionId || undefined, "off");
        try {
          await refreshInlineDiffSafe(articleId, versionId || undefined, { source: "versionLayer.off" });
        } catch (err) {
          if (!silent) console.warn("[KlimaGG] Kommentar-Diffs nach Versionslayer konnten nicht wiederhergestellt werden", err);
        }
      }
      return true;
    }

    for (const { articleId, versionId } of targets) {
      const aid = String(articleId || "");
      const vid = String(versionId || "");
      
      
      
      try {
        const ctx = prepareInlineDiffContext(aid, vid || undefined);
        if (ctx) clearInlineDiffDisplay(ctx.aid, ctx.versionId, ctx.rec, { source: "versionLayer.begin" });
      } catch (_) {}
      setVersionLayerRootState(aid, vid || undefined, mode);
      try {
        const payload = await fetchVersionLayerPayload(mode, aid, vid);
        applyPreparedVersionLayerPayload(mode, aid, vid, payload);
      } catch (err) {
        const ctx = prepareInlineDiffContext(aid, vid || undefined);
        if (ctx) applyInlineDiffFailureState(ctx.aid, ctx.scope, err, ctx.rec);
        setVersionLayerRootState(aid, vid || undefined, mode);
        if (!silent) notify("Versionslayer konnte für Artikel #" + aid + " nicht geladen werden.", { type: "warn" });
      }
    }
    return true;
  }

  async function applyNextDraftMode(isOn) {
    return applyVersionLayerMode(isOn ? "next_draft" : "off", { silent: true });
  }

  ns.applyNextDraftMode = applyNextDraftMode;
  ns.applyVersionLayerMode = applyVersionLayerMode;
  ns.baselineAudit = baselineAudit;
  ns.debugRoots = debugRoots;
  ns.clearAllOverlays = clearAllOverlays;
  // expose version diff mode for debugging Clear vs Next-Draft overlays
  try { ns.getVersionDiffMode = getVersionDiffMode; } catch (_) {}
  try { ns.setVersionDiffMode = setVersionDiffMode; } catch (_) {}

  // -------------------------------------------------
  // Optional Debug Exports (disabled by default)
  // Enable by: localStorage.setItem("klimagg.debug.merge_trace","1"); location.reload();
  // Then access via: KlimaGG.inlineDiffTools.__dbg
  try {
    if (localStorage.getItem("klimagg.debug.merge_trace") === "1") {
      ns.__dbg = ns.__dbg || {};
      try { console.log("%c[KGG] inlineDiffTools debug exports enabled", "color:#b0f;font-weight:900"); } catch (_) {}
    }
  } catch (_) {}

})();

  function setupVersionDiffToggles() {
    const cbNext = document.getElementById("toggle-next-draft");
    const cbLast = document.getElementById("toggle-last-version");
    if (!cbNext && !cbLast) return;

    const syncFromMode = () => {
      const mode = getVersionDiffMode();
      if (cbNext) cbNext.checked = mode === "next_draft";
      if (cbLast) cbLast.checked = mode === "last_version";
    };

    syncFromMode();

    const onChange = async (ev) => {
      const source = ev && ev.target ? ev.target : null;
      let mode = "off";
      if (source === cbNext && cbNext && cbNext.checked) mode = "next_draft";
      else if (source === cbLast && cbLast && cbLast.checked) mode = "last_version";
      else if (cbNext && cbNext.checked) mode = "next_draft";
      else if (cbLast && cbLast.checked) mode = "last_version";

      if (mode === "next_draft" && cbLast) cbLast.checked = false;
      if (mode === "last_version" && cbNext) cbNext.checked = false;

      setVersionDiffMode(mode);
      await applyVersionDiffModeToRenderedArticles();
      syncFromMode();
    };

    if (cbNext) cbNext.addEventListener("change", onChange);
    if (cbLast) cbLast.addEventListener("change", onChange);
  }

  async function applyArticleScope(scope, { rerender = true, preserveScroll = true } = {}) {
    const s = normalizeArticleScope(scope);
    setArticleScope(s);

    // Buttons in sync (wie Font-Size-Chips)
    document.querySelectorAll("button[data-article-scope]").forEach((btn) => {
      const b = normalizeArticleScope(btn.dataset.articleScope);
      const on = b === s;
      btn.classList.toggle("is-on", on);
      btn.setAttribute("aria-pressed", on ? "true" : "false");
    });
    try { syncHomeReadingControlsFromState(); } catch (_) {}

    if (!rerender) return;
    if (!Array.isArray(appState.articles_all)) return;

    const oldAnchor = preserveScroll ? getAnchorInfoBeforeRerender() : null;
    const scoped = filterArticlesByScope(appState.articles_all, s);
    appState.articles = scoped;
    renderArticles(scoped);
    initVotingUI(scoped);
    computeAndRenderGlobalMood(scoped);
    if (klimaggAuth.user && klimaggAuth.accessToken) {
      applyPersonalVotesToRenderedArticles();
    } else {
      applyGuestVotesToRenderedArticles();
      applyGuestReactionsToRenderedArticles();
    }
    applyTextLayerTogglesOnce();
    await applyVersionDiffModeToRenderedArticles({ silent: true });
    await applyCommentOverlayModeToRenderedArticles({ silent: true });
    await rehydrateCommentOverlaysForRenderedArticles({ silent: true });
    await reapplyGuestCommentFlagOverlaysForLoadedArticles({ silent: true });
    if (preserveScroll) {
      restoreScrollAfterRerender(oldAnchor, scoped);
    }
  }

  function setupArticleScopeControls() {
    const buttons = Array.from(document.querySelectorAll("button[data-article-scope]"));
    
    const stored = (safeGetLS(UI_ARTICLE_SCOPE_STORAGE_KEY) || "").trim();
    if (!stored) {
      setArticleScope("toc");
    }

    
    applyArticleScope(getArticleScope(), { rerender: false });
    if (!buttons.length) return;
    buttons.forEach((btn) => {
      btn.addEventListener("click", async () => {
        if (btn.disabled || btn.classList.contains("is-disabled")) return;
        await applyArticleScope(btn.dataset.articleScope || "toc", { rerender: true });
      });
    });
  }

  function buildTOC(articles) {
    const tocList = document.getElementById("toc-list");
    if (!tocList) return;

    tocList.innerHTML = "";

    
    const tocArticles = Array.isArray(articles)
      ? articles.filter((a) => a && a.show_in_toc !== false)
      : [];

    tocArticles.forEach((article) => {
      const li = document.createElement("li");
      if (article.id != null) {
        li.setAttribute("data-article-id", String(article.id));
      }

      
      
      
      const bookmarkBarSpan = document.createElement("span");
      bookmarkBarSpan.className = "toc-bookmark toc-bookmark-bar";
      li.appendChild(bookmarkBarSpan);

      
      const indicator = document.createElement("span");
      indicator.className = "toc-vote-indicator";
      indicator.title = "Stimmungsbild zu diesem Artikel";
      li.appendChild(indicator);

      const bookmarkTitleSpan = document.createElement("span");
      bookmarkTitleSpan.className = "toc-bookmark toc-bookmark-title";
      li.appendChild(bookmarkTitleSpan);

      
      const a = document.createElement("a");
      const articleId = article.slug || `artikel-${article.id}`;
      a.href = `#${articleId}`;
      const tocText = _articleTocLabelForClient(article);
      a.textContent = tocText;
      li.appendChild(a);

      // 4) Mehrheits-Emoji (globales Stimmungsbild) direkt hinter dem Titel
      const majoritySpan = document.createElement("span");
      majoritySpan.className = "toc-majority-emoji";
      const majorityEmoji =
        article && typeof article.majority_emoji === "string"
          ? article.majority_emoji.trim()
          : "";
      if (majorityEmoji) {
        majoritySpan.textContent = " " + majorityEmoji;
        majoritySpan.title =
          "Aktuelles Gesamtstimmungsbild (Mehrheits-Emoji zu diesem Artikel)";
        li.setAttribute("data-majority-emoji", majorityEmoji);
      }
      li.appendChild(majoritySpan);

      
      const personalSpan = document.createElement("span");
      personalSpan.className = "toc-personal-mood";
      if (article.id != null) {
        personalSpan.setAttribute("data-article-id", String(article.id));
      }
      li.appendChild(personalSpan);

      tocList.appendChild(li);
    });

    
    // direkt im TOC anwenden.
    updateTOCPersonalMood();
  }


  function setPersonalArticleVoteLocal(articleId, mainVote) {
    const key = String(articleId);
    let currentVersionId = null;
    try {
      currentVersionId = getCurrentVersionIdForArticle(articleId);
    } catch (_) {}
    personalArticleVotes[key] = {
      article_id: articleId,
      version_id: currentVersionId || null,
      main_vote: mainVote,
    };
    try {
      if (currentVersionId) {
        personalArticleVotesByKey[String(articleId) + ':' + String(currentVersionId)] = {
          article_id: articleId,
          version_id: currentVersionId,
          main_vote: mainVote,
        };
      }
    } catch (_) {}
    updateTOCPersonalMood();
    applyPersonalVotesToRenderedArticles();
    renderMoodOverview();
    updateHeroStatsFromCache();
    renderUserVotesOverview();
  }

  function setPersonalArticleReactionsLocal(articleId, emojis) {
    const key = String(articleId);
    const safe = Array.isArray(emojis) ? emojis.filter(Boolean).map((x) => String(x)) : [];
    let currentVersionId = null;
    try { currentVersionId = getCurrentVersionIdForArticle(articleId); } catch (_) {}
    personalArticleReactions[key] = { article_id: articleId, version_id: currentVersionId || null, emojis: Array.from(new Set(safe)) };
    try {
      if (currentVersionId) {
        personalArticleReactionsByKey[String(articleId) + ':' + String(currentVersionId)] = {
          article_id: articleId, version_id: currentVersionId, emojis: Array.from(new Set(safe)),
        };
      }
    } catch (_) {}
    updateTOCPersonalMood();
    applyPersonalVotesToRenderedArticles();
    renderMoodOverview();
    updateHeroStatsFromCache();
    renderUserVotesOverview();
  }

  function updateCachedVoteSummary(articleId, summary) {
    if (!articleId || !summary) return;
    const arts = Array.isArray(appState.articles) ? appState.articles : null;
    if (!arts) return;
    const a = arts.find((x) => x && x.id === articleId);
    if (a && typeof a === "object") {
      a.vote_summary = summary;
    }
  }

  function refreshGlobalMoodFromCache() {
    const arts = Array.isArray(appState.articles) ? appState.articles : null;
    if (!arts) return;
    computeAndRenderGlobalMood(arts);
    updateHeroStatsFromCache();
  }
  
  function applyPersonalVotesToRenderedArticles() {
    
    const voteMap = personalArticleVotes || {};
    const rxMap = personalArticleReactions || {};

    
    document.querySelectorAll(".article-vote-bar").forEach((bar) => {
      bar.querySelectorAll(".article-vote-btn").forEach((b) => b.classList.remove("active"));
      bar.querySelectorAll(".article-flag-btn").forEach((b) => b.classList.remove("active"));
    });

    const keys = new Set([...
      Object.keys(voteMap || {}),
      ...Object.keys(rxMap || {})
    ]);
    if (!keys.size) return;

    keys.forEach((k) => {
      const v = voteMap[k];
      const r = rxMap[k];
      const aid = (v && v.article_id != null) ? v.article_id : (r && r.article_id != null ? r.article_id : null);
      if (aid == null) return;

      const bar = document.querySelector(`.article-vote-bar[data-article-id="${aid}"]`);
      if (!bar) return;

      const mainVote = (v && v.main_vote) ? v.main_vote : "";
      const flags = (r && Array.isArray(r.emojis)) ? r.emojis : ((v && Array.isArray(v.flags)) ? v.flags : []);
 
      // Main vote
      bar.querySelectorAll(".article-vote-btn").forEach((btn) => {
        const e = btn.getAttribute("data-emoji") || "";
        btn.classList.toggle("active", !!mainVote && e === mainVote);
      });

      // Flags
      bar.querySelectorAll(".article-flag-btn").forEach((btn) => {
        const f = btn.getAttribute("data-flag") || "";
        btn.classList.toggle("active", !!f && flags.includes(f));
      });
    });
  }

  function applyGuestVotesToRenderedArticles(articleId = null) {
    const map = getGuestArticleVotes();
    const bars = (articleId == null)
      ? Array.from(document.querySelectorAll(".article-vote-bar[data-article-id]"))
      : Array.from(document.querySelectorAll(`.article-vote-bar[data-article-id="${cssEscape(String(articleId))}"]`));
    bars.forEach((bar) => {
      const aid = Number(bar.getAttribute("data-article-id") || NaN);
      if (!Number.isFinite(aid)) return;
      let currentVersionId = null;
      try { currentVersionId = getCurrentVersionIdForArticle(aid); } catch (_) {}
      const kExact = String(aid) + ":" + String(currentVersionId == null ? "" : currentVersionId);
      const recExact = map[kExact];
      let rec = recExact || null;
      if (!rec) {
        const vals = Object.values(map || {});
        rec = vals.find((x) => x && Number(x.article_id) === aid) || null;
      }
      const vote = rec && rec.main_vote ? String(rec.main_vote) : "";
      bar.querySelectorAll(".article-vote-btn").forEach((btn) => {
        const e = String(btn.getAttribute("data-emoji") || "");
        btn.classList.toggle("active", !!vote && e === vote);
      });
    });
  }

  function applyGuestReactionsToRenderedArticles(articleId = null) {
    const map = getGuestArticleReactions();
    const bars = (articleId == null)
      ? Array.from(document.querySelectorAll(".article-vote-bar[data-article-id]"))
      : Array.from(document.querySelectorAll(`.article-vote-bar[data-article-id="${cssEscape(String(articleId))}"]`));
    bars.forEach((bar) => {
      const aid = Number(bar.getAttribute("data-article-id") || NaN);
      if (!Number.isFinite(aid)) return;
      let currentVersionId = null;
      try { currentVersionId = getCurrentVersionIdForArticle(aid); } catch (_) {}
      const kExact = String(aid) + ":" + String(currentVersionId == null ? "" : currentVersionId);
      const recExact = map[kExact];
      let rec = recExact || null;
      if (!rec) {
        const vals = Object.values(map || {});
        rec = vals.find((x) => x && Number(x.article_id) === aid) || null;
      }
      const emojis = rec && Array.isArray(rec.emojis) ? new Set(rec.emojis.map(String)) : new Set();
      bar.querySelectorAll(".article-flag-btn").forEach((btn) => {
        const e = String(btn.getAttribute("data-flag") || "");
        btn.classList.toggle("active", !!e && emojis.has(e));
      });
    });
  }


  // --- Comment Voting UI (Phase D1) -------------------------------------

  function setPersonalCommentVoteLocal(commentId, emoji) {
    const key = String(commentId);
    personalCommentVotes[key] = { comment_id: Number(commentId), main_vote: String(emoji || "") };
    try { applyPersonalVotesToRenderedComments(); } catch (_) {}
  }

  function setPersonalCommentReactionsLocal(commentId, emojis, articleId = null, versionId = null) {
    const key = String(commentId);
    const prev = personalCommentReactions[key] || {};
    const aid = articleId != null && String(articleId).trim()
      ? Number(articleId)
      : (prev.article_id != null ? prev.article_id : null);
    const vid = versionId != null && String(versionId).trim()
      ? Number(versionId)
      : (prev.version_id != null ? prev.version_id : null);
    const rec = {
      comment_id: Number(commentId),
      emojis: Array.isArray(emojis) ? emojis.map(String) : [],
    };
    if (Number.isFinite(aid)) rec.article_id = aid;
    if (Number.isFinite(vid)) rec.version_id = vid;
    personalCommentReactions[key] = rec;
    try { applyPersonalVotesToRenderedComments(); } catch (_) {}
  }

  async function fetchPersonalCommentVotes() {
    if (!klimaggAuth || !klimaggAuth.accessToken) return;
    const votes = await apiGetJson("/api/me/comment-votes");
    const reactions = await apiGetJson("/api/me/comment-reactions");
    const mV = {};
    const mR = {};
    (Array.isArray(votes) ? votes : []).forEach((v) => {
      if (!v || v.comment_id == null) return;
      mV[String(v.comment_id)] = { comment_id: Number(v.comment_id), main_vote: String(v.main_vote || "") };
    });
    (Array.isArray(reactions) ? reactions : []).forEach((r) => {
      if (!r || r.comment_id == null) return;
      const arr = Array.isArray(r.emojis) ? r.emojis.map(String) : [];
      const rec = { comment_id: Number(r.comment_id), emojis: arr };
      const aid = Number(r.article_id);
      const vid = Number(r.version_id);
      if (Number.isFinite(aid)) rec.article_id = aid;
      if (Number.isFinite(vid)) rec.version_id = vid;
      mR[String(r.comment_id)] = rec;
    });
    personalCommentVotes = mV;
    personalCommentReactions = mR;
    // Keep window mirrors in sync (useful for console testers and avoids "empty reactions" confusion)
    try {
      window.personalCommentVotes = personalCommentVotes;
      window.personalCommentReactions = personalCommentReactions;
    } catch (_) {}
    applyPersonalVotesToRenderedComments();
    try { await reapplyCommentFlagOverlaysForLoadedArticles({ silent: true }); } catch (_) {}
  }

  function applyPersonalVotesToRenderedComments() {
    document.querySelectorAll(".comment-vote-bar[data-comment-id]").forEach((bar) => {
      const cid = bar.getAttribute("data-comment-id");
      if (!cid) return;
      const myVote = personalCommentVotes[String(cid)] ? String(personalCommentVotes[String(cid)].main_vote || "") : "";
      const myFlags = personalCommentReactions[String(cid)] && Array.isArray(personalCommentReactions[String(cid)].emojis)
        ? new Set(personalCommentReactions[String(cid)].emojis.map(String))
        : new Set();

      bar.querySelectorAll(".comment-vote-btn").forEach((b) => {
        const e = String(b.getAttribute("data-emoji") || "");
        b.classList.toggle("active", !!e && e === myVote);
      });
      bar.querySelectorAll(".comment-flag-btn").forEach((b) => {
        const f = String(b.getAttribute("data-flag") || "");
        b.classList.toggle("active", !!f && myFlags.has(f));
      });
    });
    try { updateCommentOverlayResetControlState(); } catch (_) {}
  }

  function applyCommentVoteSummaryToDom(commentId, summary) {
    const cid = String(commentId);
    if (!summary || typeof summary !== "object") return;
    const bar = document.querySelector(`.comment-vote-bar[data-comment-id="${cssEscape(cid)}"]`);
    if (!bar) return;

    // Summary-Text
    const textEl = bar.querySelector(".comment-vote-summary-text") || bar.querySelector(".comment-vote-summary");
    const total = (summary.raw_total != null) ? Number(summary.raw_total) : Number(summary.total_votes || 0);
    if (textEl) {
      if (!total) textEl.textContent = "Noch keine Stimmen";
      else {
        const label = total === 1 ? "Stimme" : "Stimmen";
        const ap = (summary.raw_approval_percent != null)
          ? Math.round(Number(summary.raw_approval_percent))
          : (summary.approval_percent != null ? Math.round(Number(summary.approval_percent)) : null);
        textEl.textContent = ap == null ? `${total} ${label}` : `${total} ${label} · ${ap}% Zustimmung`;
      }
    }

    
    const top = computeTopFlagEmoji(summary.reactions_count || {});
    const topEl = bar.querySelector(".comment-top-flag");
    if (topEl) topEl.textContent = top ? String(top.emoji) : "";

    
    try {
      const card = bar.closest(".article-comment");
      if (card && summary.votes_by_main && typeof summary.votes_by_main === "object") {
        const vb = summary.votes_by_main;
        const cGreen = Number(vb["✅"] || 0) + Number(vb["🟢"] || 0);
        const cNeut = Number(vb["🟡"] || 0);
        const cRed  = Number(vb["🟠"] || 0) + Number(vb["🔴"] || 0);
        const totalMain = cGreen + cNeut + cRed;

        const strip = card.querySelector(".comment-mood-strip");
        
        if (!totalMain) {
          try { card.style.borderLeftColor = ""; } catch (_) {}
          if (strip) {
            strip.style.background = "transparent";
            strip.style.opacity = "0";
          }
        } else {
          
          let col = "rgba(245, 158, 11, 0.45)";
          if (cGreen > cRed && cGreen > cNeut) col = "rgba(21, 128, 61, 0.45)";
          else if (cRed > cGreen && cRed > cNeut) col = "rgba(185, 28, 28, 0.45)";

          // Fallback to the border color.
          card.style.borderLeftColor = col;

          
          if (strip) {
            strip.style.background = col;
            strip.style.opacity = "0.45";
          }
        }
      }
    } catch (_) {}

    
    try {
      const card = bar.closest(".article-comment");
      if (card) {
        const cc = (summary.conflict_count != null) ? Number(summary.conflict_count) : 0;
        const conflictBadge = card.querySelector(".article-comment-badge-conflict");
        if (conflictBadge) {
          if (cc > 0) conflictBadge.removeAttribute("hidden");
          else conflictBadge.setAttribute("hidden", "hidden");
        }
        const q = (typeof summary.qualified_for_next_release === "boolean") ? !!summary.qualified_for_next_release : false;
        const qualBadge = card.querySelector(".article-comment-badge-qualified");
        if (qualBadge) {
          // approval % nachziehen
          const ap = (summary.raw_approval_percent != null) ? Math.round(Number(summary.raw_approval_percent)) :
            (summary.approval_percent != null ? Math.round(Number(summary.approval_percent)) : null);
          qualBadge.innerHTML = 'zur Release qualifiziert' + (ap == null ? '' : (' · ' + escapeHtml(String(ap) + '%')));
          if (!q) {
            
            qualBadge.setAttribute("hidden", "hidden");
          } else {
            qualBadge.removeAttribute("hidden");
          }
        }
      }
    } catch (_) {}
  }

  async function refreshCommentVoteSummary(commentId) {
    const cid = String(commentId);
    const summary = await apiGetJson(`/api/comments/${encodeURIComponent(cid)}/votes/summary`);
    commentVoteSummaryCache[cid] = summary;
    applyCommentVoteSummaryToDom(cid, summary);

    
    try {
      const c = (window.__klimaggCommentCacheById && window.__klimaggCommentCacheById[cid]) ? window.__klimaggCommentCacheById[cid] : null;
      const aid = c && c.article_id != null ? String(c.article_id) : "";
      if (c && summary && typeof summary === "object") {
        if (summary.approval_percent != null) c.approval_percent = summary.approval_percent;
        if (summary.raw_approval_percent != null) c.raw_approval_percent = summary.raw_approval_percent;
        if (summary.total_votes != null) c.total_votes = summary.total_votes;
        if (summary.raw_total != null) c.effective_total_votes = summary.raw_total;
        if (typeof summary.qualified_for_next_release === "boolean") c.qualified_for_next_release = summary.qualified_for_next_release;
      }
      try {
        if (aid && window.__klimaggCommentsCacheByArticleId && Array.isArray(window.__klimaggCommentsCacheByArticleId[aid])) {
          const row = window.__klimaggCommentsCacheByArticleId[aid].find((x) => x && String(x.id) === cid);
          if (row && summary && typeof summary === "object") {
            if (summary.approval_percent != null) row.approval_percent = summary.approval_percent;
            if (summary.raw_approval_percent != null) row.raw_approval_percent = summary.raw_approval_percent;
            if (summary.total_votes != null) row.total_votes = summary.total_votes;
            if (summary.raw_total != null) row.effective_total_votes = summary.raw_total;
            if (typeof summary.qualified_for_next_release === "boolean") row.qualified_for_next_release = summary.qualified_for_next_release;
          }
        }
      } catch (_) {}
      if (aid) scheduleResortCommentsForArticle(aid);
    } catch (_) {}
  }

  async function handleCommentVoteClick(ev, bar, emoji) {
    ev.preventDefault();
    const cid = bar.getAttribute("data-comment-id");
    if (!cid) return;
	
    
    
    try {
      const owner = String(bar.getAttribute("data-owner-user-id") || "");
      const me = (window.klimaggAuth && klimaggAuth.user) ? String(klimaggAuth.user.id) : "";
      const isAdmin = !!(window.klimaggAuth && klimaggAuth.user && klimaggAuth.user.is_admin);
      if (owner && me && owner === me && !isAdmin) {
        notify("Du kannst nicht für deinen eigenen Kommentar abstimmen.", { type: "warn" });
        return;
      }
    } catch (_) {}

    if (!klimaggAuth.user || !klimaggAuth.accessToken) {
      const aidRaw = bar.getAttribute("data-article-id");
      const aid = (aidRaw == null || aidRaw === "") ? null : Number(aidRaw);
      let vid = null;
      try { if (Number.isFinite(aid)) vid = getCurrentVersionIdForArticle(aid); } catch (_) {}
      setGuestCommentVoteLocal(cid, Number.isFinite(aid) ? aid : null, vid, emoji);
      applyGuestVotesToRenderedComments(cid);
      notify("Lokal vorgemerkt. Nach Anmeldung wird deine Auswahl übernommen.");
      return;
    }

    bar.classList.add("is-busy");
    try {
      const current = personalCommentVotes[String(cid)] ? String(personalCommentVotes[String(cid)].main_vote || "") : "";
      // Do not model "unvote" as an empty value; the backend accepts defined vote emojis only.
      if (current === emoji) {
        notify("Du hast hier bereits abgestimmt.", { type: "info", timeoutMs: 2200 });
        return;
      }

      await apiPostJson(`/api/comments/${encodeURIComponent(String(cid))}/vote`, { main_vote: emoji });
      setPersonalCommentVoteLocal(cid, emoji);
      await refreshCommentVoteSummary(cid);
    } finally {
      bar.classList.remove("is-busy");
    }
  }

  // ---------------------------------------------------------------------------
  // CommentFlagOverlayController
  // ---------------------------------------------------------------------------
  // Single controller for the 🚩 side effect. All other comment flag emojis remain
  // normal reactions and must not trigger article inline-diff refreshes.
  const COMMENT_FLAG_EMOJI = "🚩";

  const COMMENT_FLAG_OVERLAY = Object.freeze({
    isInlineFlag(emoji) {
      return String(emoji || "").trim() === COMMENT_FLAG_EMOJI;
    },

    getInlineDiffApi() {
      try {
        const api = window.KlimaGG && window.KlimaGG.inlineDiff;
        if (api && typeof api.refresh === "function" && typeof api.clear === "function") return api;
      } catch (_) {}
      return null;
    },

    resolveContext(bar) {
      const commentId = bar ? String(bar.getAttribute("data-comment-id") || "").trim() : "";
      let articleId = "";
      try { articleId = resolveCommentFlagArticleIdFromBar(bar); } catch (_) {}
      if (!articleId) {
        try { articleId = bar ? String(bar.getAttribute("data-article-id") || "").trim() : ""; } catch (_) {}
      }
      let versionId = null;
      try { if (articleId) versionId = resolveInlineDiffVersionIdForBar(bar, articleId); } catch (_) {}
      return { articleId: String(articleId || "").trim(), versionId, commentId };
    },

    _isLoggedIn() {
      return !!(klimaggAuth && klimaggAuth.user && klimaggAuth.accessToken);
    },

    _reactionMap() {
      return this._isLoggedIn() ? (personalCommentReactions || {}) : getGuestCommentReactions();
    },

    _reactionHasFlag(commentId) {
      try {
        const rec = this._reactionMap()[String(commentId || "")];
        const arr = rec && Array.isArray(rec.emojis) ? rec.emojis.map(String) : [];
        return arr.includes(COMMENT_FLAG_EMOJI);
      } catch (_) {
        return false;
      }
    },

    _commentBelongsToArticle(commentId, articleId) {
      const cid = String(commentId || "").trim();
      const aid = String(articleId || "").trim();
      if (!cid || !aid) return false;
      try {
        const rec = this._reactionMap()[cid];
        if (rec && rec.article_id != null && String(rec.article_id) === aid) return true;
      } catch (_) {}
      try {
        const bar = document.querySelector('.article-comments[data-article-id="' + cssEscape(aid) + '"] .comment-vote-bar[data-comment-id="' + cssEscape(cid) + '"]');
        if (bar) return true;
      } catch (_) {}
      return false;
    },

    getSelectedCommentIdsForArticle(articleId) {
      const aid = String(articleId || "").trim();
      if (!aid) return [];
      const out = new Set();
      const map = this._reactionMap();
      Object.keys(map || {}).forEach((cid) => {
        try {
          if (this._reactionHasFlag(cid) && this._commentBelongsToArticle(cid, aid)) out.add(String(cid));
        } catch (_) {}
      });
      try {
        const box = document.querySelector('.article-comments[data-article-id="' + cssEscape(aid) + '"]');
        if (box) {
          box.querySelectorAll('.comment-vote-bar[data-comment-id]').forEach((bar) => {
            const cid = String(bar.getAttribute("data-comment-id") || "").trim();
            if (cid && this._reactionHasFlag(cid)) out.add(cid);
          });
        }
      } catch (_) {}
      return Array.from(out).filter(Boolean).sort((a, b) => Number(a) - Number(b));
    },

    applyButtons(commentId = null) {
      if (this._isLoggedIn()) {
        try { applyPersonalVotesToRenderedComments(); } catch (_) {}
      } else {
        try { applyGuestReactionsToRenderedComments(commentId); } catch (_) {}
      }
    },

    async clearArticle(articleId, versionId, options) {
      const aid = String(articleId || "").trim();
      if (!aid) return false;
      const api = this.getInlineDiffApi();
      if (api && typeof api.clear === "function") {
        try { api.clear(aid, versionId || undefined); } catch (_) {}
      }
      if (api && typeof api.refresh === "function" && options && options.refresh) {
        try {
          await api.refresh(aid, versionId || undefined, Object.assign({}, options || {}, {
            source: options.source || "commentFlagOverlay.clearArticle",
          }));
        } catch (_) {}
      }
      return true;
    },

    rebuildLayersForArticle(articleId, versionId, options) {
      const aid = String(articleId || "").trim();
      if (!aid) return [];
      const api = this.getInlineDiffApi();
      const selected = this.getSelectedCommentIdsForArticle(aid);
      if (!api || typeof api.setActive !== "function" || typeof api.getActiveIds !== "function") return selected;
      const current = Array.isArray(api.getActiveIds(aid, versionId || undefined))
        ? api.getActiveIds(aid, versionId || undefined).map((x) => String(x)).filter(Boolean)
        : [];
      const preserveExisting = !!(options && options.preserveExisting);
      if (!selected.length && preserveExisting && current.length) {
        return Array.from(new Set(current));
      }
      current.forEach((cid) => {
        if (!selected.includes(String(cid))) {
          try { api.setActive(aid, cid, false, versionId || undefined); } catch (_) {}
        }
      });
      selected.forEach((cid) => {
        try { api.setActive(aid, cid, true, versionId || undefined); } catch (_) {}
      });
      return selected;
    },

    async refreshArticle(articleId, versionId, options) {
      const aid = String(articleId || "").trim();
      if (!aid) return false;
      const api = this.getInlineDiffApi();
      if (!api || typeof api.refresh !== "function") return false;
      await api.refresh(aid, versionId || undefined, options || {});
      return true;
    },

    async syncArticleFromRenderedFlags(articleId, versionId, options) {
      // 🚩 is a bookmark only; it no longer synchronizes inline-diff state.
      try { this.applyButtons(); } catch (_) {}
      return false;
    },

    async rebuildAndRefreshArticle(articleId, versionId, options) {
      const aid = String(articleId || "").trim();
      if (!aid) return false;
      if (getCommentOverlayMode() === "off") {
        await this.clearArticle(aid, versionId || undefined, Object.assign({}, options || {}, {
          source: "commentFlagOverlay.masterOff",
          refresh: true,
        }));
        return false;
      }
      const selected = this.rebuildLayersForArticle(aid, versionId || undefined, options || {});
      if (!selected.length) {
        await this.clearArticle(aid, versionId || undefined, Object.assign({}, options || {}, {
          source: "commentFlagOverlay.empty",
          refresh: true,
        }));
        return false;
      }
      try { setCommentOverlayModeAndSync("on"); } catch (_) {}
      await this.refreshArticle(aid, versionId || undefined, Object.assign({}, options || {}, {
        source: "commentFlagOverlay.rebuildAndRefreshArticle",
        commentIds: selected,
      }));
      return true;
    },

    async handleGuestClick(ev, bar, emoji) {
      if (ev && typeof ev.preventDefault === "function") ev.preventDefault();
      const ctx = this.resolveContext(bar);
      if (!ctx.commentId) return false;
      const rec = toggleGuestCommentReactionLocal(ctx.commentId, ctx.articleId || null, ctx.versionId, emoji);
      this.applyButtons(ctx.commentId);
      
      try { notify("Lokal vorgemerkt. Nach Anmeldung wird deine Auswahl übernommen."); } catch (_) {}
      try { rerenderCompactCommentsForElement(bar); } catch (_) {}
      return true;
    },

    async handleUserClick(ev, bar, emoji) {
      if (ev && typeof ev.preventDefault === "function") ev.preventDefault();
      const ctx = this.resolveContext(bar);
      if (!ctx.commentId) return false;
      if (bar) bar.classList.add("is-busy");
      try {
        await apiPostJson(`/api/comments/${encodeURIComponent(ctx.commentId)}/reactions/toggle`, { emoji });
        const cur = personalCommentReactions[String(ctx.commentId)] && Array.isArray(personalCommentReactions[String(ctx.commentId)].emojis)
          ? new Set(personalCommentReactions[String(ctx.commentId)].emojis.map(String))
          : new Set();
        if (cur.has(emoji)) cur.delete(emoji); else cur.add(emoji);
        setPersonalCommentReactionsLocal(ctx.commentId, Array.from(cur), ctx.articleId || null, ctx.versionId || null);
        await refreshCommentVoteSummary(ctx.commentId);
        this.applyButtons(ctx.commentId);
        
        try { rerenderCompactCommentsForElement(bar); } catch (_) {}
      } finally {
        if (bar) bar.classList.remove("is-busy");
      }
      return true;
    },

    async handleClick(ev, bar, emoji) {
      if (this._isLoggedIn()) return this.handleUserClick(ev, bar, emoji);
      return this.handleGuestClick(ev, bar, emoji);
    },

    async rehydrateArticle(articleId, versionId, options) {
      const aid = String(articleId || "").trim();
      if (!aid) return false;
      if (getCommentOverlayMode() === "off") {
        await this.clearArticle(aid, versionId || undefined, Object.assign({}, options || {}, {
          source: "commentFlagOverlay.masterOff",
          refresh: true,
        }));
        return false;
      }
      this.applyButtons();
      return this.syncArticleFromRenderedFlags(aid, versionId || undefined, Object.assign({}, options || {}, {
        source: "commentFlagOverlay.rehydrateArticle",
        forceRefresh: true,
        preserveExisting: true,
      }));
    },

    async rehydrateLoaded({ silent = true, source = "commentFlagOverlay.rehydrateLoaded" } = {}) {
      try {
        this.applyButtons();
        const targets = listLoadedCommentOverlayTargets();
        for (const t of targets) {
          if (!t || !t.articleId) continue;
          await this.rehydrateArticle(t.articleId, t.versionId || undefined, { source });
        }
      } catch (err) {
        if (!silent) console.warn("[KlimaGG] COMMENT_FLAG_OVERLAY.rehydrateLoaded failed", err);
      }
    },

    async resetAllActiveFlags(options = {}) {
      let changed = 0;
      const targets = [];
      const seen = new Set();
      try {
        document.querySelectorAll("article[data-article-id], .article-wrap[data-article-id]").forEach((el) => {
          try {
            const articleId = String(el.getAttribute("data-article-id") || "").trim();
            if (!articleId) return;
            const rawVersionId =
              el.getAttribute("data-current-version-id") ||
              el.getAttribute("data-version-id") ||
              (el.dataset ? (el.dataset.currentVersionId || el.dataset.versionId) : "") ||
              "";
            const n = Number(rawVersionId);
            const versionId = (Number.isFinite(n) && n > 0) ? String(n) : "";
            const key = String(articleId) + "::" + String(versionId || "");
            if (seen.has(key)) return;
            seen.add(key);
            targets.push({ articleId, versionId });
          } catch (_) {}
        });
      } catch (_) {}
      const api = this.getInlineDiffApi();
      for (const { articleId, versionId } of targets) {
        try {
          
          
          if (api && typeof api.clear === "function") api.clear(articleId, undefined);
          else if (typeof clearInlineDiffStateForArticleSafe === "function") clearInlineDiffStateForArticleSafe(articleId, undefined);
          try { enforcePublishedNewArticleInlineDiff(articleId, versionId); } catch (_) {}
          changed += 1;
        } catch (_) {}
        try {
          if (api && typeof api.refresh === "function") {
            await api.refresh(articleId, versionId || undefined, { source: (options && options.source) || "commentOverlayResetControl" });
          } else if (typeof refreshInlineDiffSafe === "function") {
            await refreshInlineDiffSafe(articleId, versionId || undefined, { source: (options && options.source) || "commentOverlayResetControl" });
          }
        } catch (_) {}
      }
      try { this.applyButtons(); } catch (_) {}
      try { updateCommentOverlayResetControlState(); } catch (_) {}
      return changed;
    },

    async applyMasterMode(mode) {
      return this.resetAllActiveFlags({ source: "commentFlagOverlay.applyMasterMode" });
    },
  });

  try {
    window.KlimaGG = window.KlimaGG || {};
    window.KlimaGG.commentFlagOverlay = COMMENT_FLAG_OVERLAY;
  } catch (_) {}

  async function handleCommentFlagToggleClick(ev, bar, flagEmoji) {
    return COMMENT_FLAG_OVERLAY.handleClick(ev, bar, String(flagEmoji || ""));
  }

  function applyGuestVotesToRenderedComments(commentId = null) {
    const map = getGuestCommentVotes();
    const bars = (commentId == null)
      ? Array.from(document.querySelectorAll(".comment-vote-bar[data-comment-id]"))
      : Array.from(document.querySelectorAll(`.comment-vote-bar[data-comment-id="${cssEscape(String(commentId))}"]`));
    bars.forEach((bar) => {
      const cid = String(bar.getAttribute("data-comment-id") || "").trim();
      if (!cid) return;
      const rec = map[cid];
      const vote = rec && rec.main_vote ? String(rec.main_vote) : "";
      bar.querySelectorAll(".comment-vote-btn").forEach((btn) => {
        const e = String(btn.getAttribute("data-emoji") || "");
        btn.classList.toggle("active", !!e && e === vote);
      });
    });
  }

  function applyGuestReactionsToRenderedComments(commentId = null) {
    const map = getGuestCommentReactions();
    const bars = (commentId == null)
      ? Array.from(document.querySelectorAll(".comment-vote-bar[data-comment-id]"))
      : Array.from(document.querySelectorAll(`.comment-vote-bar[data-comment-id="${cssEscape(String(commentId))}"]`));
    bars.forEach((bar) => {
      const cid = String(bar.getAttribute("data-comment-id") || "").trim();
      if (!cid) return;
      const rec = map[cid];
      const emojis = rec && Array.isArray(rec.emojis) ? new Set(rec.emojis.map(String)) : new Set();
      bar.querySelectorAll(".comment-flag-btn").forEach((btn) => {
        const e = String(btn.getAttribute("data-flag") || "");
        btn.classList.toggle("active", !!e && emojis.has(e));
      });
    });
    try { updateCommentOverlayResetControlState(); } catch (_) {}
  }

  // Master OFF helper: disable all active 🚩 flags through the same per-comment toggle path.
  async function turnOffAllCommentFlagOverlaysViaTogglePath() {
    const bars = Array.from(document.querySelectorAll('.comment-vote-bar[data-comment-id]'));
    let changed = 0;
    for (const bar of bars) {
      if (!bar) continue;
      const cid = String(bar.getAttribute("data-comment-id") || "").trim();
      if (!cid) continue;
      let isOn = false;
      try {
        const rxMap = (klimaggAuth && klimaggAuth.user && klimaggAuth.accessToken) ? personalCommentReactions : getGuestCommentReactions();
        const entry = rxMap[String(cid)] ? rxMap[String(cid)] : null;
        const arr = entry && Array.isArray(entry.emojis) ? entry.emojis.map(String) : [];
        if (arr.includes("🚩")) isOn = true;
      } catch (_) {}
      if (!isOn) {
        try {
          const btn = bar.querySelector('.comment-flag-btn[data-flag="🚩"]');
          if (btn && btn.classList.contains("active")) isOn = true;
        } catch (_) {}
      }
      if (!isOn) continue;
      try {
        await handleCommentFlagToggleClick({ preventDefault() {} }, bar, "🚩");
        changed += 1;
      } catch (_) {}
    }
    return changed;
  }

  function initCommentVotingUI(listEl) {
    if (!listEl || listEl.dataset.commentVotingWired) return;
    listEl.dataset.commentVotingWired = "1";
    listEl.addEventListener("click", (ev) => {
      const voteBtn = ev.target && ev.target.closest ? ev.target.closest(".comment-vote-btn") : null;
      const flagBtn = ev.target && ev.target.closest ? ev.target.closest(".comment-flag-btn") : null;
      const bar = ev.target && ev.target.closest ? ev.target.closest(".comment-vote-bar") : null;
      if (!bar) return;
      if (voteBtn) {
        const e = String(voteBtn.getAttribute("data-emoji") || "");
        if (e) {
          Promise.resolve(withStableCommentBoxTop(bar, async () => {
            await handleCommentVoteClick(ev, bar, e);
          })).finally(() => {
            try { rerenderCompactCommentsForElement(bar); } catch (_) {}
          });
        }
        return;
      }
      if (flagBtn) {
        const f = String(flagBtn.getAttribute("data-flag") || "");
        if (f) {
          Promise.resolve(withStableCommentBoxTop(bar, async () => {
            await handleCommentFlagToggleClick(ev, bar, f);
          })).finally(() => {
            try { rerenderCompactCommentsForElement(bar); } catch (_) {}
          });
        }
      }
    });

    // initial apply + summaries
    if (!klimaggAuth.user || !klimaggAuth.accessToken) {
      try { applyGuestVotesToRenderedComments(); } catch (_) {}
      try { applyGuestReactionsToRenderedComments(); } catch (_) {}
    } else {
      try { applyPersonalVotesToRenderedComments(); } catch (_) {}
    }
    listEl.querySelectorAll(".comment-vote-bar[data-comment-id]").forEach((bar) => {
      const cid = bar.getAttribute("data-comment-id");
      if (!cid) return;
      try { refreshCommentVoteSummary(cid); } catch (_) {}
    });
  }

  // --- Voting-UI ---------------------------------------------------------

  function initVotingUI(articles) {
    const voteBars = document.querySelectorAll(".article-vote-bar");
    if (!voteBars.length) return;

    voteBars.forEach((bar) => {
      const articleIdAttr = bar.getAttribute("data-article-id");
      const articleId = articleIdAttr ? parseInt(articleIdAttr, 10) : null;
      if (!articleId) return;

      const buttons = bar.querySelectorAll(".article-vote-btn");
      buttons.forEach((btn) => {
        const emoji = btn.getAttribute("data-emoji");
        if (!emoji) return;

        btn.addEventListener("click", () => {
          handleVoteClick(articleId, emoji, bar, btn);
        });
      });

      
      const flagButtons = bar.querySelectorAll(".article-flag-btn");
      flagButtons.forEach((btn) => {
        const flag = btn.getAttribute("data-flag");
        if (!flag) return;
        btn.addEventListener("click", () => {
          handleFlagToggleClick(articleId, bar, btn);
        });
      });

      if (!votingRefreshIds.has(articleId)) {
        votingRefreshIds.add(articleId);
        refreshVoteSummary(articleId);
      }
    });

    
    
    if (!window.__klimaggVotingRefreshInterval) {
      window.__klimaggVotingRefreshInterval = window.setInterval(() => {
        votingRefreshIds.forEach((id) => {
          refreshVoteSummary(id);
        });
      }, 15000); 
    }
  }


  // --- Voting: Emojis/Lesezeichen (Reactions) -----------------------------
  
  
 
  function _getCurrentMainVoteFromDom(barEl) {
    try {
      const active = barEl ? barEl.querySelector(".article-vote-btn.active") : null;
      return active ? String(active.getAttribute("data-emoji") || "") : "";
    } catch (_) {
      return "";
    }
  }

  function _getCurrentVersionIdFromDom(articleId) {
    try {
      const art = document.querySelector(`article[data-article-id="${cssEscape(String(articleId))}"]`);
      if (!art) return null;
      const v = art.getAttribute("data-current-version-id");
      const n = v != null ? Number(v) : NaN;
      return Number.isFinite(n) ? n : null;
    } catch (_) {
      return null;
    }
  }

  function _cloneJson(obj) {
    try { return JSON.parse(JSON.stringify(obj)); } catch (_) { return null; }
  }

  function _optimisticUpdateCachedArticleSummary(articleId, prevEmoji, nextEmoji) {
    
    try {
      const arts = Array.isArray(appState.articles_all) ? appState.articles_all : (Array.isArray(appState.articles) ? appState.articles : []);
      const a = arts && arts.find ? arts.find((x) => x && x.id === Number(articleId)) : null;
      if (!a || !a.vote_summary) return null;
      const snap = _cloneJson(a.vote_summary);
      if (!snap || typeof snap !== "object") return null;

      const curVid = _getCurrentVersionIdFromDom(articleId);
      const versions = Array.isArray(snap.versions) ? snap.versions : [];
      let vs = null;
      if (versions.length && curVid != null) vs = versions.find((v) => v && v.version_id === curVid) || versions[0];
      else if (versions.length) vs = versions[0];
      else {
        // very old shape: fallback bucket
        vs = snap;
      }
      if (!vs || typeof vs !== "object") return null;

      const counts = vs.votes_by_main || vs.main_vote_counts || {};
      const getN = (k) => Number(counts[k] || 0) || 0;
      const setN = (k, n) => { counts[k] = Math.max(0, Number(n) || 0); };

      const hadPrev = !!prevEmoji;
      const prevWasValid = hadPrev && Object.prototype.hasOwnProperty.call(counts, prevEmoji);
      if (hadPrev && prevWasValid && prevEmoji !== nextEmoji) setN(prevEmoji, getN(prevEmoji) - 1);
      if (nextEmoji) setN(nextEmoji, getN(nextEmoji) + (prevEmoji && prevEmoji !== nextEmoji ? 1 : (prevEmoji ? 0 : 1)));

      
      const totalKey = (vs.total_votes != null) ? "total_votes" : ((vs.total != null) ? "total" : "total_votes");
      const curTotal = Number(vs[totalKey] || 0) || 0;
      if (!hadPrev) vs[totalKey] = curTotal + 1;

      // write back normalized
      if (vs.votes_by_main) vs.votes_by_main = counts;
      else if (vs.main_vote_counts) vs.main_vote_counts = counts;
      else vs.votes_by_main = counts;
      a.vote_summary = snap;
      return snap;
    } catch (_) {
      return null;
    }
  }

  async function handleFlagToggleClick(articleId, barEl, btnEl) {
    if (!klimaggAuth.user || !klimaggAuth.accessToken) {
      const aid = Number(articleId);
      let vid = null;
      try { vid = getCurrentVersionIdForArticle(aid); } catch (_) {}
      const flag = btnEl ? String(btnEl.getAttribute("data-flag") || "") : "";
      if (!flag) return;
      toggleGuestArticleReactionLocal(aid, vid, flag);
      applyGuestReactionsToRenderedArticles(aid);
      notify("Lokal vorgemerkt. Nach Anmeldung wird deine Auswahl übernommen.");
      return;
    }
    const flag = btnEl ? (btnEl.getAttribute("data-flag") || "") : "";
    if (!flag) return;

    
    const wasActive = !!(btnEl && btnEl.classList.contains("active"));
    if (btnEl) btnEl.classList.toggle("active");

    
    let prevFlags = [];
    try {
      const entry = personalArticleReactions ? personalArticleReactions[String(articleId)] : null;
      prevFlags = Array.isArray(entry && entry.emojis) ? entry.emojis.slice() : [];
    } catch (_) {}
    if (!Array.isArray(prevFlags)) prevFlags = [];
    let nextFlags = prevFlags.filter((f) => f && String(f) !== flag);
    if (!wasActive) nextFlags.push(flag);
    // unique
    nextFlags = Array.from(new Set(nextFlags.map((x) => String(x))));
    setPersonalArticleReactionsLocal(articleId, nextFlags);

    try {
      const data = await apiFetchJson(
        `/api/articles/${articleId}/reactions/toggle`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ emoji: flag }),
        },
        { authRequired: true }
      );

      const serverFlags = data && Array.isArray(data.emojis) ? data.emojis : nextFlags;
      setPersonalArticleReactionsLocal(articleId, serverFlags);

      
      try { await refreshVoteSummary(articleId); } catch (_) {}

      
      requestRefreshCascade("reaction", { articleId });
    } catch (err) {
      console.error("[KlimaGG] Fehler beim Speichern der Emojis/Lesezeichen:", err);
      const msg = err && err.message ? String(err.message) : "Speichern fehlgeschlagen.";
      notify("Deine Markierung konnte nicht gespeichert werden: " + msg, { type: "error" });

      
      if (btnEl) btnEl.classList.toggle("active", wasActive);
      try { applyPersonalVotesToRenderedArticles(); } catch (_) {}
      try { updateTOCPersonalMood(); } catch (_) {}
    }
  }


  async function handleVoteClick(articleId, emoji, barEl, btnEl) {
    if (!klimaggAuth.user || !klimaggAuth.accessToken) {
      const aid = Number(articleId);
      let vid = null;
      try { vid = getCurrentVersionIdForArticle(aid); } catch (_) {}
      setGuestArticleVoteLocal(aid, vid, emoji);
      applyGuestVotesToRenderedArticles(aid);
      notify("Lokal vorgemerkt. Nach Anmeldung wird deine Auswahl übernommen.");
      return;
    }

    const payload = { main_vote: emoji };

    // -----------------------
    // D2: Optimistic UI update
    // -----------------------
    const prevLocalEntry = personalArticleVotes ? personalArticleVotes[String(articleId)] : null;
    const prevEmojiLocal = (prevLocalEntry && prevLocalEntry.main_vote) ? String(prevLocalEntry.main_vote) : "";
    const prevEmojiDom = _getCurrentMainVoteFromDom(barEl);
    const prevEmoji = prevEmojiLocal || prevEmojiDom || "";

    // snapshot for revert
    const arts = Array.isArray(appState.articles_all) ? appState.articles_all : (Array.isArray(appState.articles) ? appState.articles : []);
    const aObj = arts && arts.find ? arts.find((x) => x && x.id === Number(articleId)) : null;
    const prevSummarySnap = aObj && aObj.vote_summary ? _cloneJson(aObj.vote_summary) : null;

    // 1) active button immediately
    try {
      const allButtons = barEl ? barEl.querySelectorAll(".article-vote-btn") : [];
      allButtons.forEach((b) => b.classList.remove("active"));
      if (btnEl) btnEl.classList.add("active");
    } catch (_) {}
    // 2) personal mapping immediately (TOC/personal mood)
    try { setPersonalArticleVoteLocal(articleId, emoji); } catch (_) {}
    // 3) best-effort cached summary update (so total/border can change instantly)
    try {
      const updated = _optimisticUpdateCachedArticleSummary(articleId, prevEmoji, emoji);
      if (updated) {
        try { applyVoteSummaryToDom(articleId, updated); } catch (_) {}
        try { refreshGlobalMoodFromCache(); } catch (_) {}
      }
    } catch (_) {}

    const wasBusy = barEl && barEl.classList.contains("is-busy");
    if (barEl && !wasBusy) barEl.classList.add("is-busy");
    try {
      const data = await apiFetchJson(
        `/api/articles/${articleId}/vote`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        },
        { authRequired: true }
      );

      applyVoteSummaryToDom(articleId, data);

      // Refresh-Kaskade (F24): Cache -> Donut/TOC
      updateCachedVoteSummary(articleId, data);
      refreshGlobalMoodFromCache();

      
      requestRefreshCascade("vote", { articleId });

    } catch (err) {
      console.error("[KlimaGG] Fehler beim Voting:", err);
      const msg = err && err.message ? String(err.message) : "Voting fehlgeschlagen.";
      notify("Deine Stimme konnte nicht gespeichert werden: " + msg, { type: "error" });

      // Revert optimistic state (best-effort)
      try {
        if (prevEmojiLocal) setPersonalArticleVoteLocal(articleId, prevEmojiLocal);
        else {
          // restore without TOC noise
          const k = String(articleId);
          if (personalArticleVotes && personalArticleVotes[k]) personalArticleVotes[k].main_vote = prevEmoji;
          applyPersonalVotesToRenderedArticles();
          updateTOCPersonalMood();
        }
      } catch (_) {}
      try {
        if (aObj && prevSummarySnap) {
          aObj.vote_summary = prevSummarySnap;
          applyVoteSummaryToDom(articleId, prevSummarySnap);
          refreshGlobalMoodFromCache();
        }
      } catch (_) {}
    } finally {
      if (barEl && !wasBusy) barEl.classList.remove("is-busy");
    }
  }

  async function refreshVoteSummary(articleId) {
    try {
      const data = await apiFetchJson(`/api/articles/${articleId}/votes/summary`);
      applyVoteSummaryToDom(articleId, data);

      // Refresh-Kaskade: Cache -> Donut/TOC
      updateCachedVoteSummary(articleId, data);
      refreshGlobalMoodFromCache();

      
      updateTOCPersonalMood();
    } catch (err) {
      console.warn(
        "[KlimaGG] Fehler beim Laden der Votes-Summary für Artikel",
        articleId,
        err
      );
    }
  }

  function applyVoteSummaryToDom(articleId, summary) {
    if (!summary || typeof summary !== "object") return;

    const articleSel = `article[data-article-id="${articleId}"]`;
    const articleEl = document.querySelector(articleSel);
    if (!articleEl) return;

    const currentVersionAttr = articleEl.getAttribute(
      "data-current-version-id"
    );
    const currentVersionId = currentVersionAttr
      ? parseInt(currentVersionAttr, 10)
      : null;

    const versions = Array.isArray(summary.versions) ? summary.versions : [];
    let versionSummary = null;

    if (versions.length === 0) {
      versionSummary = {
        total_votes: summary.total_votes || 0,
        main_vote_counts: {},
      };
    } else if (currentVersionId != null) {
      versionSummary =
        versions.find((v) => v.version_id === currentVersionId) ||
        versions[0];
    } else {
      versionSummary = versions[0];
    }

    if (!versionSummary) return;

    const total = versionSummary.total_votes || 0;
    const mainCounts = versionSummary.votes_by_main || versionSummary.main_vote_counts || {};
      const reactionsCount = versionSummary.reactions_count || versionSummary.reactionsCount || {};

    
    const summaryEl = articleEl.querySelector(".article-vote-summary");
    if (summaryEl) {
      if (total === 0) {
        summaryEl.textContent = "Noch keine Stimmen";
      } else {
        const label = total === 1 ? "Stimme" : "Stimmen";
        
        summaryEl.textContent = `${total} ${label}`;
      }
    }

    // Farblogik: Hauptkategorien direkt (✅/🟢/🟡/🟠/🔴) + Fallback
    const positive = (mainCounts["✅"] || 0) + (mainCounts["🟢"] || 0);
    const negative = (mainCounts["🟠"] || 0) + (mainCounts["🔴"] || 0);
    const neutral = (mainCounts["🟡"] || 0);

    
    let majorityEmoji = "";
    if (total > 0 && mainCounts) {
      const orderMajority = ["✅", "🟢", "🟡", "🟠", "🔴"];
      let bestEmoji = null;
      let bestCount = 0;
      let tie = false;

      orderMajority.forEach((emoji) => {
        const c = mainCounts[emoji] || 0;
        if (c > bestCount) {
          bestCount = c;
          bestEmoji = emoji;
          tie = false;
        } else if (c === bestCount && c > 0) {
          tie = true;
        }
      });

      
      if (!tie && bestEmoji && bestCount > 0) {
        majorityEmoji = bestEmoji;
      }
    }

    
    function cssVar(name, fallback) {
      try {
        const v = getComputedStyle(document.documentElement).getPropertyValue(name);
        const s = (v || "").trim();
        return s || fallback;
      } catch (_) {
        return fallback;
      }
    }

    function moodColorForEmoji(emoji) {
      if (emoji === "✅") return cssVar("--mood-ppp", "#16a34a");
      if (emoji === "🟢") return cssVar("--mood-plus", "#4ade80");
      if (emoji === "🟡") return cssVar("--mood-o", "#f1c40f");
      if (emoji === "🟠") return cssVar("--mood-minus", "#fb923c");
      if (emoji === "🔴") return cssVar("--mood-mmm", "#ef4444");
      return "#94a3b8";
    }

    
    const palette = {
      "✅": moodColorForEmoji("✅"),
      "🟢": moodColorForEmoji("🟢"),
      "🟡": moodColorForEmoji("🟡"),
      "🟠": moodColorForEmoji("🟠"),
      "🔴": moodColorForEmoji("🔴"),
    };

    let color = "#94a3b8"; // grau (Tie/unklar)
    if (total === 0) {
      color = "rgba(209,213,219,0.75)";
    } else if (majorityEmoji && palette[majorityEmoji]) {
      color = palette[majorityEmoji];
    } else {
      
      if (positive > negative && positive >= Math.ceil(total * 0.5)) color = palette["🟢"];
      else if (negative > positive && negative >= Math.ceil(total * 0.5)) color = palette["🟠"];
      else if (neutral >= Math.ceil(total * 0.5)) color = palette["🟡"];
    }

    
    const topFlag = computeTopFlagEmoji(reactionsCount);
    const majoritySpan = document.querySelector(
      `nav.toc li[data-article-id="${articleId}"] .toc-majority-emoji`
    );
    if (majoritySpan) {
      majoritySpan.textContent = topFlag ? (" " + topFlag.emoji) : "";
      if (topFlag) {
        majoritySpan.title = "Meistverwendetes Emoji (Markierungen) in den Stimmen dieses Artikels";
      } else {
        majoritySpan.removeAttribute("title");
      }
    }

    
    articleEl.style.borderLeftColor = color;

    
    const indicator = document.querySelector(
      `nav.toc li[data-article-id="${articleId}"] .toc-vote-indicator`
    );
    if (indicator) {
      indicator.style.backgroundColor = color;
      indicator.style.opacity = total > 0 ? "1" : "0.3";
    }
  }

  

  const HOME_READING_PRESETS = Object.freeze({
    entry: {
      scope: "toc",
      layers: {
        kurzinfo: true,
        einleitung: false,
        juristisch: false,
        juristisch2: false,
        anmerkung: false,
      },
      title: "10 Min · Einstieg",
      text: "Du siehst die Kurzinfos der im Inhaltsverzeichnis geführten Artikel und damit den roten Faden, ohne sofort in Einleitungen, Normtext und Anmerkungen einzusteigen.",
    },
    basis: {
      scope: "core",
      layers: {
        kurzinfo: true,
        einleitung: true,
        juristisch: false,
        juristisch2: false,
        anmerkung: false,
      },
      title: "60 Min · Grundlagen",
      text: "Du siehst die Kernartikel mit Kurzinfos und Einleitungen. Das zeigt Systemlogik, Einordnung und Zusammenhänge, ohne den vollständigen Normtext und die Rechtsverordnungen einzublenden.",
    },
    full: {
      scope: "all",
      layers: {
        kurzinfo: true,
        einleitung: true,
        juristisch: true,
        juristisch2: true,
        anmerkung: true,
      },
      title: "Volltext",
      text: "Du siehst alle Artikel sowie Kurzinfos, Einleitungen, juristischen Text, Rechtsverordnungen und Anmerkungen. Geschichten bleiben unabhängig davon zuschaltbar.",
    },
  });

  const HOME_READING_LAYER_IDS = Object.freeze({
    kurzinfo: "toggle-kurzinfo",
    story: "toggle-story",
    einleitung: "toggle-einleitung",
    juristisch: "toggle-juristisch",
    juristisch2: "toggle-juristisch2",
    anmerkung: "toggle-anmerkung",
  });

  function getHomeReadingLayerCheckbox(blockKey) {
    const id = HOME_READING_LAYER_IDS[String(blockKey || "")];
    return id ? document.getElementById(id) : null;
  }

  function getMatchingHomeReadingMode() {
    if (!isAppIndex) return "";
    const scope = getArticleScope();

    for (const [mode, preset] of Object.entries(HOME_READING_PRESETS)) {
      if (preset.scope !== scope) continue;

      let matches = true;
      for (const [blockKey, expected] of Object.entries(preset.layers)) {
        const cb = getHomeReadingLayerCheckbox(blockKey);
        if (!cb || !!cb.checked !== !!expected) {
          matches = false;
          break;
        }
      }
      if (matches) return mode;
    }
    return "";
  }

  function syncHomeReadingControlsFromState() {
    if (!isAppIndex) return;

    const matchedMode = getMatchingHomeReadingMode();

    document.querySelectorAll("button[data-home-reading-mode]").forEach((btn) => {
      const on = String(btn.dataset.homeReadingMode || "") === matchedMode;
      btn.setAttribute("aria-pressed", on ? "true" : "false");
    });

    const preset = matchedMode ? HOME_READING_PRESETS[matchedMode] : null;
    const titleEl = document.getElementById("home-reading-info-title");
    const textEl = document.getElementById("home-reading-info-text");

    if (titleEl) titleEl.textContent = preset ? preset.title : "Eigene Auswahl";
    if (textEl) {
      textEl.textContent = preset
        ? preset.text
        : "Die Anzeige wurde über die Seitenleiste individuell verändert. Wähle einen Lesemodus, um wieder ein definiertes Preset anzuwenden.";
    }

    const storyCb = getHomeReadingLayerCheckbox("story");
    const storiesOn = !!(storyCb && storyCb.checked);
    const storyButton = document.getElementById("home-reading-stories");
    if (storyButton) storyButton.setAttribute("aria-pressed", storiesOn ? "true" : "false");

    const storyInfo = document.getElementById("home-story-info");
    if (storyInfo) storyInfo.hidden = !storiesOn;
  }

  async function applyHomeReadingPreset(mode) {
    const preset = HOME_READING_PRESETS[String(mode || "")];
    if (!preset) return;

    Object.entries(preset.layers).forEach(([blockKey, value]) => {
      const cb = getHomeReadingLayerCheckbox(blockKey);
      if (cb) cb.checked = !!value;
    });

    const changeTrigger = getHomeReadingLayerCheckbox("kurzinfo");
    if (changeTrigger) {
      changeTrigger.dispatchEvent(new Event("change", { bubbles: true }));
    }

    await applyArticleScope(preset.scope, {
      rerender: true,
      preserveScroll: false,
    });
    syncHomeReadingControlsFromState();
  }

  function setupHomeLandingControls() {
    if (!isAppIndex) return;

    const useButtons = Array.from(document.querySelectorAll("button[data-home-use]"));
    const usePanels = Array.from(document.querySelectorAll("[data-home-use-panel]"));

    function activateUse(key) {
      const useKey = String(key || "");
      useButtons.forEach((btn) => {
        const selected = String(btn.dataset.homeUse || "") === useKey;
        btn.setAttribute("aria-pressed", selected ? "true" : "false");
      });
      usePanels.forEach((panel) => {
        panel.hidden = String(panel.dataset.homeUsePanel || "") !== useKey;
      });
    }

    useButtons.forEach((btn) => {
      btn.addEventListener("click", () => activateUse(btn.dataset.homeUse || "draft"));
    });

    if (useButtons.length) {
      const selected =
        useButtons.find((btn) => btn.getAttribute("aria-pressed") === "true") || useButtons[0];
      activateUse(selected.dataset.homeUse || "draft");
    }

    const projectTabs = Array.from(document.querySelectorAll("button[data-home-project-tab]"));
    const projectPanels = Array.from(document.querySelectorAll("[data-home-project-panel]"));
    let projectStatsLoaded = false;

    function activateProjectPanel(key) {
      const panelKey = String(key || "topics");
      projectTabs.forEach((btn) => {
        const selected = String(btn.dataset.homeProjectTab || "") === panelKey;
        btn.classList.toggle("is-active", selected);
        btn.setAttribute("aria-selected", selected ? "true" : "false");
      });
      projectPanels.forEach((panel) => {
        panel.hidden = String(panel.dataset.homeProjectPanel || "") !== panelKey;
      });
      if (panelKey === "stats" && !projectStatsLoaded) {
        projectStatsLoaded = true;
        Promise.resolve(fetchPublicProjectStats()).catch((err) => {
          projectStatsLoaded = false;
          console.warn("[KlimaGG] Projektstatistik konnte nicht geladen werden:", err);
        });
      }
    }

    projectTabs.forEach((btn) => {
      btn.addEventListener("click", () => activateProjectPanel(btn.dataset.homeProjectTab || "topics"));
    });
    if (projectTabs.length) activateProjectPanel("topics");

    document.querySelectorAll("button[data-home-reading-mode]").forEach((btn) => {
      btn.addEventListener("click", () => {
        Promise.resolve(applyHomeReadingPreset(btn.dataset.homeReadingMode || "")).catch((err) => {
          console.warn("[KlimaGG] Lesemodus konnte nicht angewendet werden:", err);
        });
      });
    });

    const storyButton = document.getElementById("home-reading-stories");
    if (storyButton) {
      storyButton.addEventListener("click", () => {
        const storyCb = getHomeReadingLayerCheckbox("story");
        if (!storyCb) return;
        storyCb.checked = !storyCb.checked;
        storyCb.dispatchEvent(new Event("change", { bubbles: true }));
        syncHomeReadingControlsFromState();
      });
    }

    syncHomeReadingControlsFromState();
  }

  // --- Landing-Buttons ----------------------------------------------------

  function syncAuthCTAButtons() {
    const loggedIn = !!(klimaggAuth && klimaggAuth.user);
    const btns = [
      document.getElementById("login-button"),
      document.getElementById("top-login-button"),
    ].filter(Boolean);

    for (const btn of btns) {
      btn.textContent = loggedIn ? "Abmelden" : "Anmelden";
      btn.setAttribute("data-auth-state", loggedIn ? "in" : "out");
      btn.setAttribute("title", loggedIn ? "Abmelden" : "Anmelden (Magic-Link)");
    }
  }

  function hookLandingButtons() {
    const btnRead2 = document.getElementById("btn-read-law-2");
    if (btnRead2) {
      btnRead2.addEventListener("click", () => {
        const section = document.getElementById("articles-section");
        if (!section) return;
        scrollToElementWithHeaderOffset(section, { behavior: "smooth", extraGapPx: 14 });
      });
    }

    function handleAuthCTA() {
      if (klimaggAuth && klimaggAuth.user) {
        
        clearSignupToken();
        logoutLocal({ reason: "Du wurdest abgemeldet." });
        syncAuthCTAButtons();
        return;
      }
      triggerLoginCTA({ behavior: "smooth" });
    }

    // Hero-CTA (index.html)
    const loginButton = document.getElementById("login-button");
    if (loginButton) loginButton.addEventListener("click", handleAuthCTA);

    
    const topLoginBtn = document.getElementById("top-login-button");
    if (topLoginBtn) topLoginBtn.addEventListener("click", handleAuthCTA);

    syncAuthCTAButtons();
	
    
    const versionsBtn = document.getElementById("btn-versions");
    if (versionsBtn) {
      versionsBtn.addEventListener("click", (ev) => {
        
        const isOnVersionsPage = window.location && window.location.pathname === "/versionen";
        if (ev && typeof ev.preventDefault === "function") ev.preventDefault();
        notify("Versionen → /versionen (Releases, Changelog, Snapshots).", { type: "info" });
        if (!isOnVersionsPage) {
          window.setTimeout(() => {
            window.location.href = "/versionen";
          }, 180);
        }
      });
    }
  }

  
  
  
  
  function setupDraftSidebarsActivation() {
    const body = document.body;
    if (!body) return;
    if (body.getAttribute("data-page") !== "home") return;

    const draftLayout = document.getElementById("draft-layout");
    const toc = document.querySelector("nav.toc");
    const userPanel = document.getElementById("user-panel");
    if (!draftLayout || !toc || !userPanel) return;

	
    function recalcDraftLayoutPaddings() {
      
      if (!body.classList.contains("sidebars-manual-on")) {
        draftLayout.style.setProperty("--draft-left", "0px");
        draftLayout.style.setProperty("--draft-right", "0px");
        return;
      }

      const leftW = body.classList.contains("left-collapsed")
        ? 0
        : (toc.getBoundingClientRect().width || 0);
      const rightW = body.classList.contains("right-collapsed")
        ? 0
        : (userPanel.getBoundingClientRect().width || 0);

      const leftPad = leftW ? (leftW + 18) : 0;
      const rightPad = rightW ? (rightW + 18) : 0;
      draftLayout.style.setProperty("--draft-left", leftPad + "px");
      draftLayout.style.setProperty("--draft-right", rightPad + "px");
    }

    function scheduleRecalc() {
      window.requestAnimationFrame(recalcDraftLayoutPaddings);
    }

    window.addEventListener("resize", scheduleRecalc, { passive: true });
    window.addEventListener("hashchange", scheduleRecalc, { passive: true });

    
    
    
    const mo = new MutationObserver(() => scheduleRecalc());
    mo.observe(body, { attributes: true, attributeFilter: ["class"] });

    scheduleRecalc();
  }

  // --- Panel-Toggles (linke/rechte Spalte) -------------------------------

  function setupPanelToggles() {
    const body = document.body;
    const pageKind = body ? body.getAttribute("data-page") : "";
    const tocSidebar = document.querySelector("nav.toc.app-sidebar") || document.querySelector("nav.toc");
    const userSidebar = document.getElementById("user-panel");

    
    
    const leftToggles = Array.from(document.querySelectorAll("#toggle-left-panel"));
    const rightToggles = Array.from(document.querySelectorAll("#toggle-right-panel"));
    const homeSidebarToggle = document.getElementById("home-sidebar-toggle");

    
    if (!userSidebar && rightToggles.length) rightToggles.forEach((b) => (b.style.display = "none"));
    
    if (!tocSidebar && leftToggles.length) leftToggles.forEach((b) => (b.style.display = "none"));

    function setSidebarsHidden(shouldHide) {
      
      if (tocSidebar) tocSidebar.hidden = !!shouldHide;
      if (userSidebar) userSidebar.hidden = !!shouldHide;
    }

    function syncSidebarsHidden() {
      if (!body) return;
      if (pageKind === "home") setSidebarsHidden(!body.classList.contains("sidebars-manual-on"));
      else setSidebarsHidden(false);
    }

    function setManualOnIfNeeded() {
      if (!body) return;
      if (pageKind !== "home") return;

      
      
      
      ensureHomeSidebarsVisible();

      
      syncSidebarsHidden();
    }

    function clearManualIfAllClosed() {
      if (!body) return;
      if (pageKind !== "home") return;
      const leftClosed = body.classList.contains("left-collapsed");
      const rightClosed = body.classList.contains("right-collapsed");
      if (leftClosed && rightClosed) {
        body.classList.remove("sidebars-manual-on");
        safeSetLS(UI_HOME_SIDEBARS_VISIBLE_KEY, "off");
        syncSidebarsHidden();
      }
    }
    function syncHomeSidebarToggle() {
      if (!homeSidebarToggle || !body || pageKind !== "home") return;

      const bothOpen =
        body.classList.contains("sidebars-manual-on") &&
        !body.classList.contains("left-collapsed") &&
        !body.classList.contains("right-collapsed");

      homeSidebarToggle.textContent = bothOpen ? "Hier schließen" : "Hier öffnen";
      homeSidebarToggle.setAttribute("aria-pressed", bothOpen ? "true" : "false");
    }

    if (homeSidebarToggle && pageKind === "home") {
      homeSidebarToggle.addEventListener("click", (ev) => {
        ev.preventDefault();

        const bothOpen =
          body.classList.contains("sidebars-manual-on") &&
          !body.classList.contains("left-collapsed") &&
          !body.classList.contains("right-collapsed");

        if (bothOpen) {
          body.classList.add("left-collapsed");
          body.classList.add("right-collapsed");
          safeSetLS(UI_LEFT_PANEL_STORAGE_KEY, "closed");
          safeSetLS(UI_RIGHT_PANEL_STORAGE_KEY, "closed");
          clearManualIfAllClosed();
        } else {
          ensureHomeSidebarsVisible();
          body.classList.remove("left-collapsed");
          body.classList.remove("right-collapsed");
          safeSetLS(UI_LEFT_PANEL_STORAGE_KEY, "open");
          safeSetLS(UI_RIGHT_PANEL_STORAGE_KEY, "open");
        }

        window.dispatchEvent(new Event("resize"));
        syncHomeSidebarToggle();
      });

      const homeSidebarToggleObserver = new MutationObserver(syncHomeSidebarToggle);
      homeSidebarToggleObserver.observe(body, {
        attributes: true,
        attributeFilter: ["class"],
      });

      syncHomeSidebarToggle();
    }

    // Restore collapse state (v1.0.7)
    const rightPref = safeGetLS(UI_RIGHT_PANEL_STORAGE_KEY);
    const leftPref = safeGetLS(UI_LEFT_PANEL_STORAGE_KEY);

    function cameFromStaticPage() {
      try {
        const ref = document.referrer || "";
        if (!ref) return false;
        const u = new URL(ref);
        if (u.origin !== window.location.origin) return false;
        const p = (u.pathname || "").replace(/\/+$/, "");
        return ["/mitmachen", "/methodik", "/kontakt", "/rechtliches", "/versionen"].includes(p);
      } catch (_) {
        return false;
      }
    }

    function hashWantsDraft() {
      const h = String((window.location && window.location.hash) || "").toLowerCase();
      return (h === "#entwurf" || h === "#articles-section" || h.startsWith("#article-"));
    }

    
    
    if (pageKind === "home") {
      const firstHome = !safeGetLS(UI_HOME_FIRST_VISIT_KEY);
      const homeVis = safeGetLS(UI_HOME_SIDEBARS_VISIBLE_KEY);

      let shouldShow = (homeVis === "on");
      if (!shouldShow && homeVis !== "off") {
        
        if (cameFromStaticPage() || hashWantsDraft()) shouldShow = true;
      }

      if (shouldShow) {
        body.classList.add("sidebars-manual-on");
        if (homeVis !== "on") safeSetLS(UI_HOME_SIDEBARS_VISIBLE_KEY, "on");
      } else {
        body.classList.remove("sidebars-manual-on");
      }
      syncSidebarsHidden();

      
      
      
      
      if (shouldShow) {
        const wantsDraftNow = hashWantsDraft();

        // Linke Navigation:
        
        
        
        if (leftPref === "closed") body.classList.add("left-collapsed");
        else if (leftPref === "open") body.classList.remove("left-collapsed");
        else if (firstHome && !wantsDraftNow) body.classList.add("left-collapsed");
        else body.classList.remove("left-collapsed");

        
        if (rightPref === "closed") body.classList.add("right-collapsed");
        else if (rightPref === "open") body.classList.remove("right-collapsed");
        else {
          body.classList.add("right-collapsed");
          if (firstHome) safeSetLS(UI_RIGHT_PANEL_STORAGE_KEY, "closed");
        }
      } else {
        
        body.classList.add("left-collapsed");
        body.classList.add("right-collapsed");
      }

      if (firstHome) safeSetLS(UI_HOME_FIRST_VISIT_KEY, "1");
    } else {
      
      if (leftPref === "closed") body.classList.add("left-collapsed");
      else body.classList.remove("left-collapsed");

      
      
      if (rightPref === "open") body.classList.remove("right-collapsed");
      else body.classList.add("right-collapsed");

      syncSidebarsHidden();
    }
	
	
    if (leftToggles.length) {
      leftToggles.forEach((btn) => {
        btn.addEventListener("click", (ev) => {
          ev.preventDefault();
          ev.stopPropagation();
        setManualOnIfNeeded();
        body.classList.toggle("left-collapsed");
        safeSetLS(
          UI_LEFT_PANEL_STORAGE_KEY,
          body.classList.contains("left-collapsed") ? "closed" : "open"
        );
        // Draft-Layout neu einziehen
        window.dispatchEvent(new Event("resize"));
        clearManualIfAllClosed();
      });
      });
    }

    if (rightToggles.length) {
      rightToggles.forEach((btn) => {
        btn.addEventListener("click", (ev) => {
          ev.preventDefault();
          ev.stopPropagation();
        setManualOnIfNeeded();
        body.classList.toggle("right-collapsed");
        safeSetLS(
          UI_RIGHT_PANEL_STORAGE_KEY,
          body.classList.contains("right-collapsed") ? "closed" : "open"
        );
        window.dispatchEvent(new Event("resize"));
        clearManualIfAllClosed();
      });
      });
    }

    
    
    
    function ensureInnerCollapseHook(side) {
      const isLeft = side === "left";
      const id = isLeft ? "hook-collapse-left" : "hook-collapse-right";
      if (document.getElementById(id)) return;

      const host = isLeft
        ? (document.querySelector("nav.toc") || tocSidebar)
        : userSidebar;
      if (!host) return;

      const btn = document.createElement("button");
      btn.type = "button";
      btn.id = id;
      btn.className = isLeft
        ? "panel-collapse-hook panel-collapse-hook-left"
        : "panel-collapse-hook panel-collapse-hook-right";
      btn.title = isLeft ? "Navigation einklappen" : "Nutzerbereich einklappen";
      btn.setAttribute("aria-label", btn.title);
      
      btn.textContent = isLeft ? "‹" : "›";

      btn.addEventListener("click", (ev) => {
        ev.preventDefault();
        ev.stopPropagation();
        setManualOnIfNeeded();

        if (isLeft) {
          if (!body.classList.contains("left-collapsed")) {
            body.classList.add("left-collapsed");
            safeSetLS(UI_LEFT_PANEL_STORAGE_KEY, "closed");
          }
        } else {
          if (!body.classList.contains("right-collapsed")) {
            body.classList.add("right-collapsed");
            safeSetLS(UI_RIGHT_PANEL_STORAGE_KEY, "closed");
          }
        }

        window.dispatchEvent(new Event("resize"));
        clearManualIfAllClosed();
      });

      host.appendChild(btn);
    }

    ensureInnerCollapseHook("left");
    ensureInnerCollapseHook("right");
  }
  
  
  

  function setupResizeHandles() {
    const toc = document.querySelector("nav.toc");
    const userPanel = document.getElementById("user-panel");
    
    const main = document.getElementById("draft-layout") || document.getElementById("hauptinhalt");
    const leftHandle = document.getElementById("left-resize-handle");
    const rightHandle = document.getElementById("right-resize-handle");

    if (!toc || !userPanel || !main || !leftHandle || !rightHandle) {
      return;
    }

    let isDraggingLeft = false;
    let isDraggingRight = false;

    function applyLeftWidth(px) {
      const w = Math.min(
        LEFT_SIDEBAR_MAX,
        Math.max(LEFT_SIDEBAR_MIN, px)
      );
      toc.style.width = w + "px";
      
      
      
      if (main && main.id === "draft-layout") {
        if (document.body.classList.contains("sidebars-manual-on") && !document.body.classList.contains("left-collapsed")) {
          main.style.setProperty("--draft-left", (w + 18) + "px");
        } else {
          main.style.setProperty("--draft-left", "0px");
        }
        main.style.marginLeft = "0px";
      } else if (main) {
        main.style.marginLeft = w + "px";
      }
    }

    function applyRightWidth(px) {
      const w = Math.min(
        RIGHT_SIDEBAR_MAX,
        Math.max(RIGHT_SIDEBAR_MIN, px)
      );
      userPanel.style.width = w + "px";
      if (main && main.id === "draft-layout") {
        if (document.body.classList.contains("sidebars-manual-on") && !document.body.classList.contains("right-collapsed")) {
          main.style.setProperty("--draft-right", (w + 18) + "px");
        } else {
          main.style.setProperty("--draft-right", "0px");
        }
        main.style.marginRight = "0px";
      } else if (main) {
        main.style.marginRight = w + "px";
      }
    }

    function clampNum(n, min, max) {
      return Math.min(max, Math.max(min, n));
    }

    function parsePx(val) {
      const n = parseInt(String(val || "").replace(/[^0-9]/g, ""), 10);
      return Number.isFinite(n) ? n : null;
    }

    
    function computeDefaultWidth(side) {
      const vw = window.innerWidth || document.documentElement.clientWidth || 0;
      const ref = document.querySelector("#start .shell") || document.querySelector("main .shell") || document.querySelector(".shell");
      if (!ref || !vw) return null;
      const r = ref.getBoundingClientRect();
      const edgeGap = 14;
      const handleRoom = 16;
      if (side === "left") return Math.max(0, r.left - edgeGap - handleRoom);
      return Math.max(0, (vw - r.right) - edgeGap - handleRoom);
    }

    function initSidebarWidthsIfNeeded() {
      const storedL = parsePx(safeGetLS(UI_LEFT_SIDEBAR_WIDTH_KEY));
      const storedR = parsePx(safeGetLS(UI_RIGHT_SIDEBAR_WIDTH_KEY));
      const defaultL = storedL != null ? storedL : computeDefaultWidth("left");
      const defaultR = storedR != null ? storedR : computeDefaultWidth("right");

      if (defaultL != null) {
        const w = clampNum(defaultL, LEFT_SIDEBAR_MIN, LEFT_SIDEBAR_MAX);
        applyLeftWidth(w);
        if (storedL == null) safeSetLS(UI_LEFT_SIDEBAR_WIDTH_KEY, String(w));
      }
      if (defaultR != null) {
        const w = clampNum(defaultR, RIGHT_SIDEBAR_MIN, RIGHT_SIDEBAR_MAX);
        applyRightWidth(w);
        if (storedR == null) safeSetLS(UI_RIGHT_SIDEBAR_WIDTH_KEY, String(w));
      }
    }

    initSidebarWidthsIfNeeded();

    leftHandle.addEventListener("mousedown", (ev) => {
      if (document.body.classList.contains("left-collapsed")) return;
      isDraggingLeft = true;
      ev.preventDefault();
    });

    rightHandle.addEventListener("mousedown", (ev) => {
      if (document.body.classList.contains("right-collapsed")) return;
      isDraggingRight = true;
      ev.preventDefault();
    });

    window.addEventListener("mousemove", (ev) => {
      if (!isDraggingLeft && !isDraggingRight) return;

      const viewportWidth =
        window.innerWidth || document.documentElement.clientWidth || 0;

      if (isDraggingLeft) {
        applyLeftWidth(ev.clientX);
      }

      if (isDraggingRight) {
        const newWidth = viewportWidth - ev.clientX;
        applyRightWidth(newWidth);
      }
    });

    window.addEventListener("mouseup", () => {
      const wasLeft = isDraggingLeft;
      const wasRight = isDraggingRight;
      isDraggingLeft = false;
      isDraggingRight = false;

      try {
        if (wasLeft) {
          const w = Math.round(toc.getBoundingClientRect().width || 0);
          if (w) safeSetLS(UI_LEFT_SIDEBAR_WIDTH_KEY, String(w));
        }
        if (wasRight) {
          const w = Math.round(userPanel.getBoundingClientRect().width || 0);
          if (w) safeSetLS(UI_RIGHT_SIDEBAR_WIDTH_KEY, String(w));
        }
      } catch (_) {}
    });
  }

  // --- Navigation: Toggle-Boxen (UI-only) ---------------------------------

  function setupNavToggleBoxes() {
    
    
    const labels = Array.from(document.querySelectorAll("label.nav-toggle"));
    if (!labels.length) return;

    function syncLabel(label) {
      const cb = label.querySelector('input[type="checkbox"]');
      if (!cb) return;
      label.classList.toggle("is-on", !!cb.checked);
    }

    labels.forEach((label) => {
      const cb = label.querySelector('input[type="checkbox"]');
      if (!cb) return;
      // initial
      syncLabel(label);
      // live
      cb.addEventListener("change", () => syncLabel(label));
    });
  }

  // --- Navigation: einklappbare Boxen (Details) ---------------------------
  function getUiNavboxStoragePrefix() {
    return "klimagg.ui.navbox.";
  }

  function hasActiveRenderedCommentFlags() {
    const visibleMarkerSelector =
      'article .kgg-inline-op[data-user-state="normal"], ' +
      'article .kgg-inline-op[data-user-state="warn"], ' +
      'article .kgg-inline-diff-ins[data-user-state="normal"], ' +
      'article .kgg-inline-diff-ins[data-user-state="warn"], ' +
      'article .kgg-inline-diff-del[data-user-state="normal"], ' +
      'article .kgg-inline-diff-del[data-user-state="warn"]';
    const isActuallyVisible = (el) => {
      try {
        if (!el || !el.isConnected) return false;
        if (el.hidden) return false;
        const style = window.getComputedStyle ? window.getComputedStyle(el) : null;
        if (style && (style.display === "none" || style.visibility === "hidden")) return false;
        if (el.getClientRects && el.getClientRects().length > 0) return true;
        if (el.offsetWidth || el.offsetHeight) return true;
      } catch (_) {}
      return false;
    };
    try {
      const markers = Array.from(document.querySelectorAll(visibleMarkerSelector) || []);
      for (const marker of markers) {
        if (isActuallyVisible(marker)) return true;
      }
    } catch (_) {}
    try {
      const markers = Array.from(document.querySelectorAll(visibleMarkerSelector) || []);
      return markers.some((marker) => {
        try {
          return String(marker.getAttribute("data-user-state") || "") !== "none" && isActuallyVisible(marker);
        } catch (_) {
          return false;
        }
      });
    } catch (_) {}
    return false;
  }

  function updateCommentOverlayResetControlState() {
    const cb = document.getElementById("toggle-comment-overlays");
    if (!cb) return;
    const hasAny = hasActiveRenderedCommentFlags();
    cb.checked = hasAny;
    cb.disabled = !hasAny;
    cb.setAttribute("aria-disabled", hasAny ? "false" : "true");
    cb.title = hasAny ? "Alle aktiven Kommentar-Änderungen zurücksetzen" : "Keine aktiven Kommentar-Änderungen";
    try {
      const label = cb.closest ? (cb.closest("label") || cb.parentElement) : null;
      if (label) label.classList.toggle("is-disabled", !hasAny);
    } catch (_) {}
  }

  
  
  
  
  
  
  function _findSidebarScrollContainer(el) {
    if (!el || !el.closest) return null;
    
    const sb = el.closest(".app-sidebar");
    if (sb && sb.scrollHeight > sb.clientHeight + 2) return sb;
    // Fallback to the nearest scrollable ancestor.
    let p = el.parentElement;
    while (p && p !== document.body) {
      if (p.scrollHeight > p.clientHeight + 2) return p;
      p = p.parentElement;
    }
    return null;
  }

  function _ensureDetailsVisibleInSidebar(detailsEl, { topGapPx = 12, bottomGapPx = 12 } = {}) {
    if (!detailsEl) return;
    const sc = _findSidebarScrollContainer(detailsEl);
    if (!sc) return;

    const cRect = sc.getBoundingClientRect();
    const dRect = detailsEl.getBoundingClientRect();

    const topLimit = cRect.top + Math.max(0, topGapPx);
    const bottomLimit = cRect.bottom - Math.max(0, bottomGapPx);

    
    const visibleHeight = Math.max(1, bottomLimit - topLimit);
    const boxHeight = Math.max(1, dRect.height);
    if (boxHeight > visibleHeight) {
      // top anlegen
      const delta = dRect.top - topLimit;
      if (Math.abs(delta) > 2) sc.scrollTop += delta;
      return;
    }

    
    if (dRect.bottom > bottomLimit + 2) {
      sc.scrollTop += (dRect.bottom - bottomLimit);
      return;
    }
    
    if (dRect.top < topLimit - 2) {
      sc.scrollTop -= (topLimit - dRect.top);
      return;
    }
  }

  function _scheduleEnsureDetailsVisible(detailsEl) {
    
    requestAnimationFrame(() => {
      requestAnimationFrame(() => {
        try { _ensureDetailsVisibleInSidebar(detailsEl, { topGapPx: 12, bottomGapPx: 12 }); } catch (_) {}
      });
    });
  }

  function setupNavPanelCollapsibles() {
    const root = document.querySelector("nav.toc");
    if (!root) return;
    const boxes = Array.from(root.querySelectorAll("details[data-collapsible]"));
    if (!boxes.length) return;

    boxes.forEach((d) => {
      const keyPart = String(d.getAttribute("data-collapsible") || d.id || "").trim();
      if (!keyPart) return;
      const key = getUiNavboxStoragePrefix() + keyPart;
      const stored = safeGetLS(key);

      if (stored === "open") d.open = true;
      else if (stored === "closed") d.open = false;
      

      d.addEventListener("toggle", () => {
        safeSetLS(key, d.open ? "open" : "closed");
        if (d.open) _scheduleEnsureDetailsVisible(d);
      });
    });
  }

  

  function setupLayoutControls() {
    const body = document.body;
    if (!body) return;

    const status = document.getElementById("layout-fontsize-status");
    const buttons = Array.from(
      document.querySelectorAll("button[data-ui-fontsize]")
    );

    function normalizeSize(val) {
      if (val === "normal" || val === "compact" || val === "dense") return val;
      if (val === "small") return "compact";
      return "normal";
    }

    function applySize(size) {
      const s = normalizeSize(size);

      body.classList.toggle("ui-density-compact", s === "compact");
      body.classList.toggle("ui-density-dense", s === "dense");
      body.classList.toggle("ui-font-small", s === "compact" || s === "dense");

      if (status) {
        if (s === "compact") status.textContent = "Aktiv: kompakt (mehr Übersicht).";
        else if (s === "dense") status.textContent = "Aktiv: dicht (maximale Übersicht).";
        else status.textContent = "Aktiv: normal.";
      }

      if (buttons.length) {
        buttons.forEach((btn) => {
          const b = normalizeSize(btn.dataset.uiFontsize);
          const active = b === s;
          btn.classList.toggle("is-active", active);
          btn.setAttribute("aria-checked", active ? "true" : "false");
        });
      }
    }

    
    const stored = normalizeSize(safeGetLS(UI_FONT_SIZE_STORAGE_KEY));
    applySize(stored);

    // Interaktion
    if (buttons.length) {
      buttons.forEach((btn) => {
        btn.addEventListener("click", () => {
          if (btn.disabled) return;
          const next = normalizeSize(btn.dataset.uiFontsize);
          safeSetLS(UI_FONT_SIZE_STORAGE_KEY, next);
          applySize(next);
        });
      });
    }
  }
  
  // --- Nutzerbereich: einklappbare Boxen (Start: eingeklappt) ----------------

  function setupUserPanelCollapsibles() {
    const root = document.getElementById("user-panel");
    if (!root) return;
    const boxes = Array.from(root.querySelectorAll("details[data-collapsible]"));
    if (!boxes.length) return;
  
    boxes.forEach((d) => {
      const keyPart = String(d.getAttribute("data-collapsible") || d.id || "").trim();
      if (!keyPart) {
        d.open = false;
        return;
      }
  
      const isStatus = (keyPart === "user-status" || d.id === "user-status-card");
      const key = UI_USERBOX_STORAGE_PREFIX + keyPart;
      const stored = safeGetLS(key);
  
      if (stored === "open") d.open = true;
      else if (stored === "closed") d.open = false;
      else d.open = isStatus ? true : false; // Default: Status offen, Rest zu
  
      d.addEventListener("toggle", () => {
        
        if (d.dataset && d.dataset.forceOpen === "1") {
          if (!d.open) d.open = true;
          return;
        }
        safeSetLS(key, d.open ? "open" : "closed");
      });
    });
  
    
    applyUserStatusCardCollapseMode({ locked: true });
  }

  // --- Text-Layer-Toggles -------------------------------------------------

  function setupTextLayerToggles() {
    const ids = [
      { checkboxId: "toggle-kurzinfo", selector: ".content-block.kurzinfo" },
      { checkboxId: "toggle-story", selector: ".content-block.story" },
      { checkboxId: "toggle-einleitung", selector: ".content-block.einleitung" },
      { checkboxId: "toggle-juristisch", selector: ".content-block.juristisch" },
      { checkboxId: "toggle-juristisch2", selector: ".content-block.juristisch2" },
      { checkboxId: "toggle-anmerkung", selector: ".content-block.anmerkung" },
    ];

    // Persist text-layer toggles (kurzinfo/story/einleitung/juristisch/...) across refreshes.
    const __KGG_TEXT_LAYER_LS_KEY = "klimagg.ui.text_layers_v1";
    const __kggLoadTextLayerState = () => {
      try {
        const raw = localStorage.getItem(__KGG_TEXT_LAYER_LS_KEY);
        return raw ? JSON.parse(raw) : null;
      } catch (_) { return null; }
    };
    const __kggSaveTextLayerState = () => {
      try {
        const out = {};
        ids.forEach(({ checkboxId }) => {
          const cb = document.getElementById(checkboxId);
          if (cb) out[checkboxId] = !!cb.checked;
        });
        localStorage.setItem(__KGG_TEXT_LAYER_LS_KEY, JSON.stringify(out));
      } catch (_) {}
    };

    // apply saved state before first render
    try {
      const st = __kggLoadTextLayerState();
      if (st && typeof st === "object") {
        ids.forEach(({ checkboxId }) => {
          const cb = document.getElementById(checkboxId);
          if (cb && checkboxId in st) cb.checked = !!st[checkboxId];
        });
      }
    } catch (_) {}

    function pickViewportAnchor() {
      const headerH = getStickyHeaderHeightPx() || 0;
      const y = Math.max(10, headerH + 18);
      const vw = (window.innerWidth || document.documentElement.clientWidth || 0);
      const x = Math.max(10, Math.floor(vw * 0.5));
      let el = null;
      try { el = document.elementFromPoint(x, y); } catch (_) { el = null; }
      if (!el) return null;

      const container = document.getElementById("articles-container") || document.getElementById("draft-layout");
      if (container && !container.contains(el)) return null;

      if (el.closest) {
        const blockAnchor = el.closest(".content-block.meta, .content-block.kurzinfo, .content-block.story, .content-block.einleitung, .content-block.juristisch, .content-block.juristisch2, .content-block.anmerkung, article");
        if (blockAnchor && !blockAnchor.classList.contains("hidden") && blockAnchor.getClientRects().length) {
          return blockAnchor;
        }
      }
      return el;
    }

    function applyTextLayerToggles({ preserveScroll = false } = {}) {
      const yBefore = window.pageYOffset || 0;
      const anchor = preserveScroll ? pickViewportAnchor() : null;
      const topBefore = (anchor && preserveScroll) ? anchor.getBoundingClientRect().top : null;
      ids.forEach(({ checkboxId, selector }) => {
        const cb = document.getElementById(checkboxId);
        const elements = document.querySelectorAll(selector);
        if (!cb) return;
        elements.forEach((el) => {
          if (cb.checked) el.classList.remove("hidden");
          else el.classList.add("hidden");
        });
      });

      if (!preserveScroll) return;

      window.requestAnimationFrame(() => {
        try {
          const yNow = window.pageYOffset || 0;
          if (anchor && document.contains(anchor) && anchor.getClientRects().length && topBefore !== null) {
            const topNow = anchor.getBoundingClientRect().top;
            const delta = topNow - topBefore;
            if (Math.abs(delta) > 1 || Math.abs(yNow - yBefore) > 2) {
              window.scrollTo({ top: Math.max(0, yBefore + delta), behavior: "auto" });
            }
          } else {
            if (Math.abs(yNow - yBefore) > 2) {
              window.scrollTo({ top: Math.max(0, yBefore), behavior: "auto" });
            }
          }
        } catch (_) {
          try { window.scrollTo({ top: Math.max(0, yBefore), behavior: "auto" }); } catch (__ ) {}
        }
      });
    }

    ids.forEach(({ checkboxId }) => {
      const cb = document.getElementById(checkboxId);
      if (!cb) return;
      cb.addEventListener("change", () => {
        __kggSaveTextLayerState();
        applyTextLayerToggles({ preserveScroll: true });
        try { syncHomeReadingControlsFromState(); } catch (_) {}
        try { rerenderAllLoadedCompactComments(); } catch (_) {}
      });
    });

    
    applyTextLayerToggles({ preserveScroll: false });
  }

  

  function updatePersonalMoodBodyClass(isOn) {
    const body = document.body;
    if (!body) return;
    body.classList.toggle("toc-personal-mood-on", !!isOn);
    body.classList.toggle("toc-personal-mood-off", !isOn);
  }

  function updateOwnMoodToggleAvailability() {
    const cb = document.getElementById("toggle-personal-mood");
    if (!cb) return;
    const loggedIn = !!klimaggAuth.user;

    if (!loggedIn) {
      cb.disabled = true;
      cb.checked = false;
      updatePersonalMoodBodyClass(false);
      try { window.localStorage.setItem(PERSONAL_MOOD_TOC_STORAGE_KEY, "off"); } catch (_) {}
      updateTOCPersonalMood();
      try { renderMoodOverview(); } catch (_) {}
      try { rerenderAllLoadedCompactComments(); } catch (_) {}
      return;
    }

    cb.disabled = false;
    
    try { renderMoodOverview(); } catch (_) {}
  }

  function setupPersonalMoodToggle() {
    const checkbox = document.getElementById("toggle-personal-mood");
    const hint = document.getElementById("personal-mood-hint");

    let initialOn = false;
    try {
      const stored = window.localStorage.getItem(
        PERSONAL_MOOD_TOC_STORAGE_KEY
      );
      if (stored === "on") {
        initialOn = true;
      }
    } catch (err) {
      console.warn(
        "[KlimaGG] Konnte persönliche Stimmungsbild-Präferenz nicht lesen:",
        err
      );
    }

    
    if (checkbox) {
      checkbox.checked = initialOn;
      checkbox.addEventListener("change", () => {
        const isOn = !!checkbox.checked;
        updatePersonalMoodBodyClass(isOn);
        try {
          window.localStorage.setItem(
            PERSONAL_MOOD_TOC_STORAGE_KEY,
            isOn ? "on" : "off"
          );
        } catch (err) {
          console.warn(
            "[KlimaGG] Konnte persönliche Stimmungsbild-Präferenz nicht speichern:",
            err
          );
        }
        // Beim Umschalten TOC-Klammern/Bookmark neu anwenden
        updateTOCPersonalMood();
        try { renderMoodOverview(); } catch (_) {}
        try { rerenderAllLoadedCompactComments(); } catch (_) {}
      });
    }

    
    updatePersonalMoodBodyClass(initialOn);

    if (hint && initialOn) {
      hint.classList.add("personal-mood-hint-active");
    }
  }


  

  function updateBookmarksBodyClass(isOn) {
    const body = document.body;
    if (!body) return;
    body.classList.toggle('toc-bookmarks-on', !!isOn);
    body.classList.toggle('toc-bookmarks-off', !isOn);
  }

  function updateBookmarksToggleAvailability() {
    const cb = document.getElementById('toggle-bookmarks');
    if (!cb) return;
    cb.disabled = false;

    
    let on = true;
    try {
      const stored = window.localStorage.getItem(BOOKMARKS_TOC_STORAGE_KEY);
      if (stored === 'off') on = false;
      if (stored === 'on') on = true;
    } catch (_) {}
    cb.checked = !!on;
    updateBookmarksBodyClass(!!on);
  }

  function setupBookmarksToggle() {
    const checkbox = document.getElementById('toggle-bookmarks');

    let initialOn = true;
    try {
      const stored = window.localStorage.getItem(BOOKMARKS_TOC_STORAGE_KEY);
      if (stored === 'off') initialOn = false;
      if (stored === 'on') initialOn = true;
    } catch (err) {
      console.warn('[KlimaGG] Konnte Lesezeichen-Präferenz nicht lesen:', err);
    }

    if (checkbox) {
      checkbox.checked = initialOn;
      checkbox.addEventListener('change', () => {
        const isOn = !!checkbox.checked;
        updateBookmarksBodyClass(isOn);
        try {
          window.localStorage.setItem(BOOKMARKS_TOC_STORAGE_KEY, isOn ? 'on' : 'off');
        } catch (err) {
          console.warn('[KlimaGG] Konnte Lesezeichen-Präferenz nicht speichern:', err);
        }
        updateTOCPersonalMood();
      });
    }

    updateBookmarksBodyClass(initialOn);
    updateBookmarksToggleAvailability();
  }



  // --- Navigation: Kommentare (Neue/Alte) --------------------------------

  function normalizeOnOffValue(v, defaultVal) {
    const s = String(v || "").trim().toLowerCase();
    if (s === "on" || s === "true" || s === "1") return "on";
    if (s === "off" || s === "false" || s === "0") return "off";
    return defaultVal ? "on" : "off";
  }

  function getCommentBadgeVisibilityPref() {
    const raw = safeGetLS(UI_COMMENT_BADGES_STORAGE_KEY);
    // Default ON to keep current visual behavior unchanged.
    return normalizeOnOffValue(raw, true) === "on";
  }

  function applyCommentBadgeVisibility({ silent = true, syncControl = true } = {}) {
    try {
      const on = getCommentBadgeVisibilityPref();
      const body = document.body;
      if (body) body.classList.toggle("kgg-hide-comment-badges", !on);
      if (syncControl) {
        const cb = document.getElementById("toggle-comment-badges");
        if (cb) cb.checked = !!on;
      }
    } catch (err) {
      if (!silent) console.warn("[KlimaGG] applyCommentBadgeVisibility failed", err);
    }
  }

  

  // (removed duplicate Master overlay helpers; use getCommentOverlayMode/setCommentOverlayMode + OverlayController)
 

  function getCommentsVisibilityPrefs() {
    const raw = safeGetLS(UI_COMMENTS_STORAGE_KEY);
    return { showComments: normalizeOnOffValue(raw, false) === "on" };
  }

  function setCommentsVisibilityPref(enabled) {
    safeSetLS(UI_COMMENTS_STORAGE_KEY, enabled ? "on" : "off");
  }

  function commentsSuppressedByVersionLayer() {
    try { return getVersionDiffMode() !== "off"; } catch (_) { return false; }
  }

  function updateCommentsBodyClass({ showComments }, suppressed) {
    const body = document.body;
    if (!body) return;
    const effective = !!showComments && !suppressed;
    body.classList.toggle("comments-any-on", effective);
    body.classList.toggle("comments-any-off", !effective);
    body.classList.toggle("comments-version-suppressed", !!suppressed);
  }

  function syncCommentsVisibilityControl() {
    const cb = document.getElementById("toggle-comments");
    if (!cb) return;
    const prefs = getCommentsVisibilityPrefs();
    const suppressed = commentsSuppressedByVersionLayer();
    cb.disabled = suppressed;
    cb.checked = suppressed ? false : !!prefs.showComments;
  }

  let commentsLazyObserver = null;

  function ensureCommentsLazyObserver() {
    if (commentsLazyObserver) return commentsLazyObserver;
    if (typeof window.IntersectionObserver !== "function") return null;

    commentsLazyObserver = new IntersectionObserver(
      (entries) => {
        const prefs = getCommentsVisibilityPrefs();
        if (commentsSuppressedByVersionLayer() || !prefs.showComments) return;
        entries.forEach((ent) => {
          if (!ent || !ent.isIntersecting) return;
          const box = ent.target;
          if (!box || box.hidden) return;
          ensureCommentsBoxLoaded(box);
        });
      },
      { root: null, rootMargin: "600px 0px", threshold: 0.01 }
    );

    return commentsLazyObserver;
  }

  function registerCommentsBoxForLazyLoad(box) {
    if (!box) return;
    const obs = ensureCommentsLazyObserver();
    if (!obs) return;
    try { obs.observe(box); } catch (_) {}
  }

  function applyCommentsVisibilityToAllRenderedArticles() {
    const prefs = getCommentsVisibilityPrefs();
    const suppressed = commentsSuppressedByVersionLayer();
    updateCommentsBodyClass(prefs, suppressed);
    syncCommentsVisibilityControl();

    const showComments = !!prefs.showComments && !suppressed;
    const boxes = Array.from(document.querySelectorAll(".article-comments[data-article-id]"));
    boxes.forEach((box) => {
      if (!box) return;
      if (!showComments) {
        box.hidden = true;
        return;
      }
      box.hidden = false;
      registerCommentsBoxForLazyLoad(box);
    });

    if (showComments) {
      window.requestAnimationFrame(() => {
        try {
          const vpH = window.innerHeight || document.documentElement.clientHeight || 0;
          boxes.forEach((box) => {
            if (!box || box.hidden) return;
            const r = box.getBoundingClientRect();
            if (r.top < vpH + 300 && r.bottom > -300) ensureCommentsBoxLoaded(box);
          });
        } catch (_) {}
      });
    }
  }

  function setupCommentsVisibilityControls() {
    const cbComments = document.getElementById("toggle-comments");
    let cbBadges = document.getElementById("toggle-comment-badges");
    if (!cbComments && !cbBadges) return;

    let cbMaster = document.getElementById("toggle-comment-overlays");
    if (!cbMaster) {
      const ref = cbComments;
      const refLabel = ref && ref.closest ? (ref.closest("label") || ref.parentElement) : null;
      if (ref) {
        const lbl = document.createElement("label");
        if (refLabel && typeof refLabel.className === "string" && refLabel.className) lbl.className = refLabel.className;
        lbl.innerHTML = '<input type="checkbox" id="toggle-comment-overlays"> <span>Kommentar-Änderungen zurücksetzen</span>';
        try {
          if (refLabel && refLabel.parentNode) refLabel.parentNode.insertBefore(lbl, refLabel.nextSibling);
          else if (ref.parentNode) ref.parentNode.insertBefore(lbl, ref.nextSibling);
        } catch (_) {
          try { document.body.appendChild(lbl); } catch (_) {}
        }
      }
      cbMaster = document.getElementById("toggle-comment-overlays");
    } else {
      try {
        const ref = cbComments;
        const refLabel = ref && ref.closest ? (ref.closest("label") || ref.parentElement) : null;
        const curLabel = cbMaster.closest ? (cbMaster.closest("label") || cbMaster.parentElement) : null;
        if (refLabel && curLabel && refLabel.parentNode && curLabel.parentNode && refLabel.parentNode !== curLabel.parentNode) {
          refLabel.parentNode.insertBefore(curLabel, refLabel.nextSibling);
        }
      } catch (_) {}
    }

    if (cbMaster) {
      try { updateCommentOverlayResetControlState(); } catch (_) {}
      cbMaster.addEventListener("change", async () => {
        if (cbMaster.disabled) return;
        try { cbMaster.disabled = true; } catch (_) {}
        try {
          await COMMENT_FLAG_OVERLAY.resetAllActiveFlags({ source: "commentOverlayResetControl" });
        } catch (err) {
          console.warn("[KlimaGG] Kommentar-Änderungen zurücksetzen fehlgeschlagen", err);
        } finally {
          try { updateCommentOverlayResetControlState(); } catch (_) {}
        }
      });
    }

    cbBadges = document.getElementById("toggle-comment-badges");
    if (!cbBadges) {
      const ref = cbMaster || cbComments;
      const refLabel = ref && ref.closest ? (ref.closest("label") || ref.parentElement) : null;
      if (ref) {
        const lbl = document.createElement("label");
        if (refLabel && typeof refLabel.className === "string" && refLabel.className) lbl.className = refLabel.className;
        lbl.innerHTML = '<input type="checkbox" id="toggle-comment-badges"> <span>Badges anzeigen</span>';
        try {
          if (refLabel && refLabel.parentNode) refLabel.parentNode.insertBefore(lbl, refLabel.nextSibling);
          else if (ref.parentNode) ref.parentNode.insertBefore(lbl, ref.nextSibling);
        } catch (_) {
          try { document.body.appendChild(lbl); } catch (_) {}
        }
      }
      cbBadges = document.getElementById("toggle-comment-badges");
    }
    if (cbBadges) {
      try { cbBadges.checked = getCommentBadgeVisibilityPref(); } catch (_) { cbBadges.checked = true; }
      cbBadges.addEventListener("change", () => {
        const on = !!cbBadges.checked;
        safeSetLS(UI_COMMENT_BADGES_STORAGE_KEY, on ? "on" : "off");
        applyCommentBadgeVisibility({ silent: true, syncControl: false });
      });
    }
    applyCommentBadgeVisibility({ silent: true, syncControl: true });

    applyCommentsVisibilityToAllRenderedArticles();

    if (cbComments) {
      cbComments.addEventListener("change", () => {
        if (cbComments.disabled) return;
        setCommentsVisibilityPref(!!cbComments.checked);
        applyCommentsVisibilityToAllRenderedArticles();

        if (!cbComments.checked) return;
        const repaintLoadedCommentLists = () => {
          try {
            Array.from(document.querySelectorAll(".article-comments-list[data-loaded='true']")).forEach((listEl) => {
              const box = listEl.closest(".article-comments[data-article-id]");
              const aid = box ? Number(box.getAttribute("data-article-id")) : NaN;
              const cached = (window.__klimaggCommentsCacheByArticleId && isFinite(aid))
                ? window.__klimaggCommentsCacheByArticleId[String(aid)]
                : null;
              if (cached && Array.isArray(cached)) renderCommentsIntoList(aid, cached, listEl);
            });
          } catch (_) {}
        };
        try { window.requestAnimationFrame(repaintLoadedCommentLists); } catch (_) { setTimeout(repaintLoadedCommentLists, 0); }
      });
    }

    try { updateCommentOverlayResetControlState(); } catch (_) {}
    try { kggLoadDebugJsOnce(); } catch (_) {}
  }

  

  async function fetchPersonalArticleVotes() {
    
    if (!klimaggAuth.user || !klimaggAuth.accessToken) {
      personalArticleVotes = {};
      personalArticleVotesByKey = {};
      updateTOCPersonalMood();
      applyPersonalVotesToRenderedArticles();
      renderMoodOverview();
      updateHeroStatsFromCache();
      renderUserVotesOverview();
      return;
    }

    try {
      const [votesData, reactionsData] = await Promise.all([
        apiFetchJson(
          "/api/me/article-votes",
          { headers: { Accept: "application/json" } },
          { authRequired: true }
        ),
        apiFetchJson(
          "/api/me/article-reactions",
          { headers: { Accept: "application/json" } },
          { authRequired: true }
        ),
      ]);
      const data = votesData;
      const byKey = {};
      const byArticle = {};
      if (Array.isArray(data)) {
        data.forEach((entry) => {
          if (!entry || typeof entry !== "object") return;
          const aid = entry.article_id;
          const vid = entry.version_id;
          if (typeof aid !== "number" || typeof vid !== "number") return;
          const key = String(aid) + ":" + String(vid);
          byKey[key] = entry;
          (byArticle[String(aid)] || (byArticle[String(aid)] = [])).push(entry);
        });
      }
      
      
      try {
        if (Array.isArray(reactionsData)) {
          reactionsData.forEach((r) => {
            if (!r || typeof r !== "object") return;
            const aid = r.article_id;
            const vid = r.version_id;
            if (aid == null || vid == null) return;
            const key = String(aid) + ":" + String(vid);
            const raw = Array.isArray(r.emojis) ? r.emojis : [];
            const clean = [];
            raw.forEach((e) => {
              if (typeof e !== "string") return;
              const t = e.trim();
              if (!t) return;
              if (!clean.includes(t)) clean.push(t);
            });
            if (!clean.length) return;

            const existing = byKey[key];
            if (existing && typeof existing === "object") {
              const flags = Array.isArray(existing.flags) ? existing.flags.slice() : [];
              clean.forEach((e) => {
                if (!flags.includes(e)) flags.push(e);
              });
              existing.flags = flags;
              return;
            }

            const entry = {
              article_id: aid,
              version_id: vid,
              main_vote: "",
              flags: clean,
            };
            byKey[key] = entry;
            const aKey = String(aid);
            if (!byArticle[aKey]) byArticle[aKey] = [];
            byArticle[aKey].push(entry);
          });
        }
      } catch (_) {}
	  
      personalArticleVotesByKey = byKey;
      const mapping = {};
      const arts = Array.isArray(appState.articles_all)
        ? appState.articles_all
        : (Array.isArray(appState.articles) ? appState.articles : []);
      const seen = new Set();
      if (arts.length) {
        arts.forEach((a) => {
          if (!a || a.id == null) return;
          const aidStr = String(a.id);
          const entries = byArticle[aidStr];
          if (!entries || !entries.length) return;
          seen.add(aidStr);
          const currentVid = a.current_version && a.current_version.id != null ? Number(a.current_version.id) : null;
          let chosen = null;
          if (currentVid) chosen = entries.find((e) => e && Number(e.version_id) === currentVid) || null;
          if (!chosen) {
            chosen = entries.reduce((best, e) => {
              if (!e || e.version_id == null) return best;
              if (!best || best.version_id == null) return e;
              return Number(e.version_id) > Number(best.version_id) ? e : best;
            }, null);
          }
          if (chosen) mapping[aidStr] = chosen;
        });
      }
      Object.keys(byArticle).forEach((aidStr) => {
        if (seen.has(aidStr)) return;
        const entries = byArticle[aidStr];
        if (!entries || !entries.length) return;
        const chosen = entries.reduce((best, e) => {
          if (!e || e.version_id == null) return best;
          if (!best || best.version_id == null) return e;
          return Number(e.version_id) > Number(best.version_id) ? e : best;
        }, null);
        if (chosen) mapping[aidStr] = chosen;
      });

      personalArticleVotes = mapping;
      updateTOCPersonalMood();
      applyPersonalVotesToRenderedArticles();
      renderMoodOverview();
      updateHeroStatsFromCache();
      renderUserVotesOverview();
    } catch (err) {
      console.error("[KlimaGG] Fehler beim Laden persönlicher Votes:", err);
    }
  }


  
  function applyFontSize(size) {
    const body = document.body;
    if (!body) return;

    function normalize(val) {
      if (val === "normal" || val === "compact" || val === "dense") return val;
      if (val === "small") return "compact"; // Migration alter localStorage-Werte
      return "normal";
    }

    const s = normalize(size);

    body.classList.toggle("ui-density-compact", s === "compact");
    body.classList.toggle("ui-density-dense", s === "dense");
    body.classList.toggle("ui-font-small", s === "compact" || s === "dense");

    
    const status = document.getElementById("layout-fontsize-status");
    if (status) {
      if (s === "compact") status.textContent = "Aktiv: kompakt (mehr Übersicht).";
      else if (s === "dense")
        status.textContent = "Aktiv: dicht (maximale Übersicht).";
      else status.textContent = "Aktiv: normal.";
    }

    
    document.querySelectorAll("button[data-font-size]").forEach((btn) => {
      const b = normalize(btn.dataset.fontSize);
      const on = b === s;
      btn.classList.toggle("is-on", on);
      btn.setAttribute("aria-pressed", on ? "true" : "false");
    });

    
    document.querySelectorAll("button[data-ui-fontsize]").forEach((btn) => {
      const b = normalize(btn.dataset.uiFontsize);
      const active = b === s;
      btn.classList.toggle("is-active", active);
      btn.setAttribute("aria-checked", active ? "true" : "false");
    });

    safeSetLS(UI_FONT_SIZE_STORAGE_KEY, s);
  }

  function setupFontSizeControls() {
    const buttons = Array.from(
      document.querySelectorAll("button[data-font-size]")
    );

    
    const stored = safeGetLS(UI_FONT_SIZE_STORAGE_KEY) || "normal";
    applyFontSize(stored);

    if (!buttons.length) return;

    buttons.forEach((btn) => {
      btn.addEventListener("click", () => {
        if (btn.disabled || btn.classList.contains("is-disabled")) return;
        applyFontSize(btn.dataset.fontSize || "normal");
      });
    });
  }

  // --- Navigation: Stimmungsbild (TOC + Donut) ----------------------------
  const MOOD_BUCKETS = [
    { emoji: "✅", label: "+++", color: "var(--mood-ppp)", dotClass: "m-ppp" },
    { emoji: "🟢", label: "+",   color: "var(--mood-plus)", dotClass: "m-plus" },
    { emoji: "🟡", label: "o",   color: "var(--mood-o)", dotClass: "m-o" },
    { emoji: "🟠", label: "-",   color: "var(--mood-minus)", dotClass: "m-minus" },
    { emoji: "🔴", label: "---", color: "var(--mood-mmm)", dotClass: "m-mmm" },
  ];


  // --- Farb- & Statistik-Helfer (v1.0.10) --------------------------------
  function getCssVarValue(name, fallback) {
    try {
      const v = getComputedStyle(document.documentElement).getPropertyValue(name);
      const s = v != null ? String(v).trim() : "";
      return s || fallback;
    } catch (_) {
      return fallback;
    }
  }

  function getMoodPalette() {
    return {
      "✅": getCssVarValue("--mood-ppp", "#16a34a"),
      "🟢": getCssVarValue("--mood-plus", "#22c55e"),
      "🟡": getCssVarValue("--mood-o", "#f1c40f"),
      "🟠": getCssVarValue("--mood-minus", "#fb923c"),
      "🔴": getCssVarValue("--mood-mmm", "#ef4444"),
    };
  }
  
  function computeTopFlagEmoji(flagsCount) {
    if (!flagsCount || typeof flagsCount !== "object") return null;

    
    const ignore = new Set(["🚩"]);
  
    let best = null;
    let bestN = 0;
    let tie = false;

    Object.keys(flagsCount || {}).forEach((k) => {
      if (ignore.has(k)) return;
      const n = Number(flagsCount[k] || 0) || 0;
      if (n <= 0) return;

      if (n > bestN) {
        best = k;
        bestN = n;
        tie = false;
      } else if (n === bestN) {
        tie = true;
      }
    });

    if (!best || !bestN || tie) return null;
    return { emoji: best, count: bestN };
  }



  function getDefaultPublicStatsUiConfig() {
    return {
      days_default: 30,
      mood: { enabled: false },
      kpis: {
        keys: ["votes", "comments", "reviews"],
        labels: { votes: "Stimmen", users: "Nutzer", comments: "Vorschläge", reviews: "Reviews" },
        show_total: true,
        show_delta: true,
      },
      graph: {
        
        metric_keys: ["votes_total", "users", "comments", "reviews", "pageviews", "visitors_est"],
        metric_labels: {
          
          votes_total: "Stimmen",
          users: "Nutzer",
          comments: "Kommentare",
          reviews: "Reviews",
          pageviews: "Pageviews",
          visitors_est: "Visitors",
        },
        include_main_votes: true,
        include_flags: true,
        
        default_selected: ["metric:votes_total"],
      },
    };
  }

  async function fetchPublicStatsUiConfigOnce() {
    if (!isAppIndex) return getDefaultPublicStatsUiConfig();
    if (publicStatsUiConfig) return publicStatsUiConfig;
    try {
      const cfg = await apiFetchJson("/api/public/stats-config");
      publicStatsUiConfig = (cfg && typeof cfg === "object") ? cfg : getDefaultPublicStatsUiConfig();
      applyDefaultProjectSeriesSelection(publicStatsUiConfig);
      return publicStatsUiConfig;
    } catch (err) {
      console.warn("[stats] public stats-config failed", err);
      publicStatsUiConfig = getDefaultPublicStatsUiConfig();
      applyDefaultProjectSeriesSelection(publicStatsUiConfig);
      return publicStatsUiConfig;
    }
  }

  function applyDefaultProjectSeriesSelection(cfg) {
    const g = (cfg && cfg.graph && typeof cfg.graph === "object") ? cfg.graph : null;
    const defaults = g && Array.isArray(g.default_selected) ? g.default_selected : [];
    if (!defaults || defaults.length === 0) return;
    if (applyDefaultProjectSeriesSelection.__applied) return;
    applyDefaultProjectSeriesSelection.__applied = true;

    projectSeriesSelection.metrics.clear();
    projectSeriesSelection.main.clear();
    projectSeriesSelection.flags.clear();

    defaults.forEach((t) => {
      if (typeof t !== "string") return;
      if (t.startsWith("metric:")) projectSeriesSelection.metrics.add(t.slice("metric:".length));
      else if (t.startsWith("main:")) projectSeriesSelection.main.add(t.slice("main:".length));
      else if (t.startsWith("flag:")) projectSeriesSelection.flags.add(t.slice("flag:".length));
    });

    if (
      projectSeriesSelection.metrics.size === 0 &&
      projectSeriesSelection.main.size === 0 &&
      projectSeriesSelection.flags.size === 0
    ) {
      projectSeriesSelection.main.add("✅");
    }
  }

  function formatIntOrDash(v) {
    if (v === null || v === undefined) return "—";
    const n = Number(v);
    if (!isFinite(n)) return "—";
    return String(Math.trunc(n));
  }

  function renderPublicKpisFromCache() {
    if (!isAppIndex) return;
    const ul = document.getElementById("project-kpi-list");
    if (!ul) return;

    const cfg = (publicStatsUiConfig && typeof publicStatsUiConfig === "object")
      ? publicStatsUiConfig
      : getDefaultPublicStatsUiConfig();

    const k = (cfg.kpis && typeof cfg.kpis === "object") ? cfg.kpis : getDefaultPublicStatsUiConfig().kpis;
    const keys = Array.isArray(k.keys) ? k.keys : [];
    const labels = (k.labels && typeof k.labels === "object") ? k.labels : {};
    const showTotal = k.show_total !== false;
    const showDelta = k.show_delta !== false;

    const totals = (publicProjectStatsCache && publicProjectStatsCache.totals && typeof publicProjectStatsCache.totals === "object")
      ? publicProjectStatsCache.totals
      : {};
    const delta = (publicProjectStatsCache && publicProjectStatsCache.delta && typeof publicProjectStatsCache.delta === "object")
      ? publicProjectStatsCache.delta
      : {};

    ul.innerHTML = "";
    keys.forEach((key) => {
      if (!key) return;
      const li = document.createElement("li");
      li.dataset.kpiKey = String(key);

      const labelEl = document.createElement("span");
      labelEl.className = "kpi-label";
      labelEl.textContent = String(labels[key] || key);
      li.appendChild(labelEl);

      const totalEl = document.createElement("span");
      totalEl.className = "kpi-total mono";
      totalEl.textContent = showTotal ? formatIntOrDash(totals[key]) : "";
      li.appendChild(totalEl);

      const deltaEl = document.createElement("span");
      deltaEl.className = "kpi-delta mono";
      deltaEl.textContent = showDelta ? ("+" + formatIntOrDash(delta[key] || 0)) : "";
      li.appendChild(deltaEl);

      ul.appendChild(li);
    });
  }

  async function fetchPublicProjectStats(days) {
    if (!isAppIndex) return;
    try {
      const cfg = await fetchPublicStatsUiConfigOnce();
      const dflt = (cfg && typeof cfg.days_default === "number") ? cfg.days_default : 30;
      const useDays = Number(days) ? Number(days) : dflt;
      const res = await apiFetchJson(`/api/public/project-stats?days=${encodeURIComponent(String(useDays))}`);
      publicProjectStatsCache = res;
      updateHeroStatsFromCache();
    } catch (err) {
      console.warn("[stats] public project-stats failed", err);
    }
  }

  function updateHeroStatsFromCache() {
    if (!isAppIndex) return;
    if (!publicProjectStatsCache) return;

    // KPI (config-gesteuert: Gesamtsumme + Delta im Zeitraum)
    renderPublicKpisFromCache();

    try { if (typeof window.klimaggUpdateCommentPanelButtons === "function") window.klimaggUpdateCommentPanelButtons(); } catch (_) {}

    // Donut (2-layer): innen = letzte X Tage, halo = Gesamt
    const donutSvg = document.querySelector('svg[data-donut="project-mood"]');
    const moodSummaryEl = document.getElementById("project-mood-summary");

    const mood30 =
      (publicProjectStatsCache && publicProjectStatsCache.mood && publicProjectStatsCache.mood.by_main)
        ? publicProjectStatsCache.mood
        : null;
    const moodTotal =
      (publicProjectStatsCache && publicProjectStatsCache.mood_total && publicProjectStatsCache.mood_total.by_main)
        ? publicProjectStatsCache.mood_total
        : (publicProjectStatsCache && publicProjectStatsCache.mood ? publicProjectStatsCache.mood : null);

    const windowDays =
      (publicProjectStatsCache && typeof publicProjectStatsCache.days === "number")
        ? publicProjectStatsCache.days
        : 30;
    const tWindow =
      (mood30 && typeof mood30.total === "number")
        ? mood30.total
        : 0;

    const pctApprove = (m) => {
      const counts = (m && m.by_main) ? m.by_main : { "✅": 0, "🟢": 0, "🟡": 0, "🟠": 0, "🔴": 0 };
      const total = (m && typeof m.total === "number") ? m.total : 0;
      const agree = (counts["✅"] || 0) + (counts["🟢"] || 0);
      return total > 0 ? (100 * agree) / total : 0;
    };

    const pct30 = mood30 ? pctApprove(mood30) : 0;
    const pctAll = moodTotal ? pctApprove(moodTotal) : 0;

    if (moodSummaryEl) {
      
      moodSummaryEl.textContent = (tWindow > 0)
        ? ("Aktuell: " + Math.round(pct30) + "% Zustimmung")
        : "Aktuell: —% Zustimmung";
    }

    if (donutSvg) {
      const palette = getMoodPalette();

      const updateLayer = (layer, m) => {
        const counts = (m && m.by_main) ? m.by_main : { "✅": 0, "🟢": 0, "🟡": 0, "🟠": 0, "🔴": 0 };
        const total = (m && typeof m.total === "number") ? m.total : 0;

        const agree = (counts["✅"] || 0) + (counts["🟢"] || 0);
        const neutral = (counts["🟡"] || 0);
        const disagree = (counts["🟠"] || 0) + (counts["🔴"] || 0);

        const pct = (n) => (total > 0 ? (100 * n) / total : 0);
        const pctA = pct(agree);
        const pctN = pct(neutral);
        const pctD = pct(disagree);

        const agreeEl = donutSvg.querySelector(`[data-layer="${layer}"][data-seg="agree"]`);
        const neutralEl = donutSvg.querySelector(`[data-layer="${layer}"][data-seg="neutral"]`);
        const disagreeEl = donutSvg.querySelector(`[data-layer="${layer}"][data-seg="disagree"]`);

        const setSeg = (el, percent, offset, color, title) => {
          if (!el) return;
          const clamped = Math.max(0, Math.min(100, Number(percent) || 0));
          el.setAttribute("stroke", color);
          el.setAttribute("stroke-dasharray", clamped.toFixed(3) + " " + (100 - clamped).toFixed(3));
          el.setAttribute("stroke-dashoffset", String(offset));
          if (title) el.setAttribute("data-tip", title);
          else el.removeAttribute("data-tip");
        };

        const fmt = (label, n, p) => `${label}: ${n} Stimmen (${Math.round(p)}%)`;
        let off = 25;
        setSeg(agreeEl, pctA, off, palette["🟢"], fmt(layer === "30d" ? (windowDays + " Tage Zustimmung (✅+🟢)") : "Gesamt Zustimmung (✅+🟢)", agree, pctA));
        off = off - pctA;
        setSeg(neutralEl, pctN, off, palette["🟡"], fmt(layer === "30d" ? (windowDays + " Tage Neutral (🟡)") : "Gesamt Neutral (🟡)", neutral, pctN));
        off = off - pctN;
        setSeg(disagreeEl, pctD, off, palette["🟠"], fmt(layer === "30d" ? (windowDays + " Tage Ablehnung (🟠+🔴)") : "Gesamt Ablehnung (🟠+🔴)", disagree, pctD));
      };

      updateLayer("total", moodTotal);
      updateLayer("30d", mood30);

      setupDonutHoverTooltipOnce(donutSvg);
    }

    // Chart (Zeitreihe)
    setupProjectSeriesFiltersOnce();
    renderProjectSeriesChart();
  }

  function toggleProjectSeriesKey(kind, key) {
    if (kind !== "main" && kind !== "flags" && kind !== "metrics") return;
    const set = projectSeriesSelection[kind];
    if (!set) return;
    if (set.has(key)) set.delete(key);
    else set.add(key);
    updateProjectSeriesButtonStates();
    renderProjectSeriesChart();
  }

  function updateProjectSeriesButtonStates() {
    if (!isAppIndex) return;
    const wrap = document.getElementById("project-series-buttons");
    if (!wrap) return;
    const btns = wrap.querySelectorAll("button[data-kind][data-key]");
    btns.forEach((btn) => {
      const kind = btn.dataset.kind;
      const k = btn.dataset.key;
      const on = (kind === "main")
        ? projectSeriesSelection.main.has(k)
        : (kind === "flags")
          ? projectSeriesSelection.flags.has(k)
          : projectSeriesSelection.metrics.has(k);
      btn.classList.toggle("active", on);
      btn.setAttribute("aria-pressed", on ? "true" : "false");
    });
  }

  function setupProjectSeriesFiltersOnce() {
    if (!isAppIndex) return;
    const wrap = document.getElementById("project-series-buttons");
    if (!wrap) return;
    if (wrap.__klimaggSeriesReady) return;
    wrap.__klimaggSeriesReady = true;

    const cfg = (publicStatsUiConfig && typeof publicStatsUiConfig === "object")
      ? publicStatsUiConfig
      : getDefaultPublicStatsUiConfig();
    const graph = (cfg.graph && typeof cfg.graph === "object") ? cfg.graph : getDefaultPublicStatsUiConfig().graph;

    // --- Metriken ---
    const metricKeys = Array.isArray(graph.metric_keys) ? graph.metric_keys : [];
    const metricLabels = (graph.metric_labels && typeof graph.metric_labels === "object") ? graph.metric_labels : {};
    const includeMainVotes = graph.include_main_votes !== false;
    let mainButtonsInsertedInline = false;

    const appendMainVoteButtons = () => {
      const mainKeys = ["✅", "🟢", "🟡", "🟠", "🔴"];
      const mainTitles = {
        "✅": "Starke Zustimmung",
        "🟢": "Zustimmung",
        "🟡": "Neutral",
        "🟠": "Ablehnung",
        "🔴": "Starke Ablehnung",
      };
      mainKeys.forEach((k) => {
        const btn = document.createElement("button");
        btn.type = "button";
        btn.className = "article-vote-btn";
        btn.textContent = k;
        btn.title = mainTitles[k] || "";
        btn.dataset.kind = "main";
        btn.dataset.key = k;
        btn.setAttribute("aria-pressed", projectSeriesSelection.main.has(k) ? "true" : "false");
        if (projectSeriesSelection.main.has(k)) btn.classList.add("active");
        btn.addEventListener("click", () => toggleProjectSeriesKey("main", k));
        wrap.appendChild(btn);
      });
    };

    metricKeys.forEach((k) => {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "stat-metric-btn";
      btn.textContent = String(metricLabels[k] || k);
      btn.title = String(metricLabels[k] || k);
      btn.dataset.kind = "metrics";
      btn.dataset.key = String(k);
      btn.setAttribute("aria-pressed", projectSeriesSelection.metrics.has(String(k)) ? "true" : "false");
      if (projectSeriesSelection.metrics.has(String(k))) btn.classList.add("active");
      btn.addEventListener("click", () => toggleProjectSeriesKey("metrics", String(k)));
      wrap.appendChild(btn);

      
      if (includeMainVotes && !mainButtonsInsertedInline && String(k) === "votes_total") {
        appendMainVoteButtons();
        mainButtonsInsertedInline = true;
      }
    });

    
    if (includeMainVotes && !mainButtonsInsertedInline) {
      appendMainVoteButtons();
    }

    
    if (graph.include_flags !== false) {
      const flagKeys = ["🧭", "✍️", "🧩", "⚖️"];
      const flagTitles = {
        "🧭": "Klimawirkung / strategisch stark",
        "✍️": "Gut formuliert / gut lesbar",
        "🧩": "Praktisch anschlussfähig / umsetzbar",
        "⚖️": "Fair / ausgewogen / rechtlich sauber",
      };
      flagKeys.forEach((k) => {
        const btn = document.createElement("button");
        btn.type = "button";
        btn.className = "article-flag-btn";
        btn.textContent = k;
        btn.title = flagTitles[k] || "";
        btn.dataset.kind = "flags";
        btn.dataset.key = k;
        btn.setAttribute("aria-pressed", projectSeriesSelection.flags.has(k) ? "true" : "false");
        if (projectSeriesSelection.flags.has(k)) btn.classList.add("active");
        btn.addEventListener("click", () => toggleProjectSeriesKey("flags", k));
        wrap.appendChild(btn);
      });
    }
  }


function renderProjectSeriesChart() {
  try {
    if (!isAppIndex) return;
    if (!publicProjectStatsCache || !publicProjectStatsCache.series) return;

    const svg = document.querySelector('svg[data-chart="project-series"]');
    const caption = document.getElementById("project-series-caption");
    if (!svg) return;

    const gLines = svg.querySelector('g[data-lines]');
    if (!gLines) return;

    const series = publicProjectStatsCache.series;
    const dates = Array.isArray(series.dates) ? series.dates : [];
    const main = series.main && typeof series.main === "object" ? series.main : {};
    const flags = series.flags && typeof series.flags === "object" ? series.flags : {};
    const metrics = series.metrics && typeof series.metrics === "object" ? series.metrics : {};

    // Auswahl sammeln
    const keys = [];
    projectSeriesSelection.metrics.forEach((k) => keys.push({ kind: "metrics", key: k }));
    projectSeriesSelection.main.forEach((k) => keys.push({ kind: "main", key: k }));
    projectSeriesSelection.flags.forEach((k) => keys.push({ kind: "flags", key: k }));

    if (keys.length === 0) {
      const cfg =
        publicStatsUiConfig && typeof publicStatsUiConfig === "object"
          ? publicStatsUiConfig
          : getDefaultPublicStatsUiConfig();

      applyDefaultProjectSeriesSelection(cfg);

      projectSeriesSelection.metrics.forEach((k) => keys.push({ kind: "metrics", key: k }));
      projectSeriesSelection.main.forEach((k) => keys.push({ kind: "main", key: k }));
      projectSeriesSelection.flags.forEach((k) => keys.push({ kind: "flags", key: k }));

      if (keys.length === 0) keys.push({ kind: "main", key: "✅" });
      updateProjectSeriesButtonStates();
    }

    // SVG-Geometry (zu deinem HTML passend)
    const W = 360;
    const H = 110;
    const padL = 22;
    const padR = 10;
    const padT = 10;
    const padB = 16;
    const innerW = W - padL - padR;
    const innerH = H - padT - padB;

    
    svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
    svg.setAttribute("width", String(W));
    svg.setAttribute("height", String(H));

    const ns = "http://www.w3.org/2000/svg";
    const mk = (name) => document.createElementNS(ns, name);

    
    while (gLines.firstChild) gLines.removeChild(gLines.firstChild);

    // Achsen (immer zeichnen)
    const axis = mk("path");
    axis.setAttribute("d", `M ${padL} ${padT} V ${padT + innerH} H ${padL + innerW}`);
    axis.setAttribute("fill", "none");
    axis.setAttribute("stroke", "rgba(148,163,184,0.7)");
    axis.setAttribute("stroke-width", "1");
    gLines.appendChild(axis);

    const n = dates.length;
    if (n === 0) {
      if (caption) caption.textContent = "";
      return;
    }

    // Daten vorbereiten
    const datasets = [];
    for (let i = 0; i < keys.length; i++) {
      const kind = keys[i].kind;
      const key = keys[i].key;
      const src = kind === "main" ? main : kind === "flags" ? flags : metrics;
      const arr = Array.isArray(src[key]) ? src[key] : [];
      const data = dates.map((_, idx) => {
        const v = Number(arr[idx] || 0);
        return Number.isFinite(v) && v > 0 ? v : 0;
      });
      datasets.push({ kind, key, data });
    }

    // Max ermitteln
    let maxY = 0;
    for (const ds of datasets) {
      for (const v of ds.data) {
        if (v > maxY) maxY = v;
      }
    }
    if (maxY < 1) maxY = 1;

    const xAt = (i) => (n <= 1 ? padL : padL + (innerW * i) / (n - 1));
    const yAt = (v) => padT + innerH - (innerH * v) / maxY;

    
    const t0 = mk("text");
    t0.setAttribute("x", String(padL - 6));
    t0.setAttribute("y", String(padT + innerH + 4));
    t0.setAttribute("text-anchor", "end");
    t0.setAttribute("font-size", "10");
    t0.setAttribute("fill", "rgba(100,116,139,0.9)");
    t0.textContent = "0";
    gLines.appendChild(t0);

    const tM = mk("text");
    tM.setAttribute("x", String(padL - 6));
    tM.setAttribute("y", String(padT + 4));
    tM.setAttribute("text-anchor", "end");
    tM.setAttribute("font-size", "10");
    tM.setAttribute("fill", "rgba(100,116,139,0.9)");
    tM.textContent = String(Math.round(maxY));
    gLines.appendChild(tM);

    const palette = getMoodPalette();
    const dash = [null, "6 3", "2 2", "1 3", "10 4", "4 2 1 2"];

    // Linien
    datasets.forEach((ds, idx) => {
      const path = mk("path");
      let d = "";
      ds.data.forEach((v, i) => {
        const x = xAt(i);
        const y = yAt(v);
        d += (i === 0 ? "M" : "L") + " " + x.toFixed(2) + " " + y.toFixed(2) + " ";
      });
      path.setAttribute("d", d.trim());
      path.setAttribute("fill", "none");

      let color = "rgba(2,132,199,0.95)";
      if (ds.kind === "main") color = palette[ds.key] || color;
      if (ds.kind === "flags") color = "rgba(15,118,110,0.9)";
      if (ds.kind === "metrics") color = "rgba(21,94,121,0.95)";

      path.setAttribute("stroke", color);
      path.setAttribute("stroke-width", "2");
      const da = dash[idx % dash.length];
      if (da) path.setAttribute("stroke-dasharray", da);
      path.setAttribute("vector-effect", "non-scaling-stroke");
      gLines.appendChild(path);
    });

    // X-Legende (Start/Ende)
    const lblStart = String(dates[0] || "");
    const lblEnd = String(dates[n - 1] || "");

    const xt1 = mk("text");
    xt1.setAttribute("x", String(padL));
    xt1.setAttribute("y", String(padT + innerH + 12));
    xt1.setAttribute("text-anchor", "start");
    xt1.setAttribute("font-size", "10");
    xt1.setAttribute("fill", "rgba(100,116,139,0.9)");
    xt1.textContent = lblStart;
    gLines.appendChild(xt1);

    const xt2 = mk("text");
    xt2.setAttribute("x", String(padL + innerW));
    xt2.setAttribute("y", String(padT + innerH + 12));
    xt2.setAttribute("text-anchor", "end");
    xt2.setAttribute("font-size", "10");
    xt2.setAttribute("fill", "rgba(100,116,139,0.9)");
    xt2.textContent = lblEnd;
    gLines.appendChild(xt2);

    if (caption) caption.textContent = "";
  } catch (e) {
    console.warn("[stats] renderProjectSeriesChart failed", e);
  }
}



  function setupDonutHoverTooltipOnce(donutSvg) {
    if (!donutSvg || donutSvg.__klimaggTooltipReady) return;
    donutSvg.__klimaggTooltipReady = true;
    const tooltip = document.getElementById("mood-tooltip");
    if (!tooltip) return;

    const show = (ev, text) => {
      if (!text) return;
      tooltip.textContent = text;
      tooltip.hidden = false;
      tooltip.setAttribute("aria-hidden", "false");
      tooltip.style.left = String(ev.clientX) + "px";
      tooltip.style.top = String(ev.clientY) + "px";
    };
    const move = (ev) => {
      if (tooltip.hidden) return;
      tooltip.style.left = String(ev.clientX) + "px";
      tooltip.style.top = String(ev.clientY) + "px";
    };
    const hide = () => {
      tooltip.hidden = true;
      tooltip.setAttribute("aria-hidden", "true");
    };

    donutSvg.querySelectorAll(".donut-seg").forEach((seg) => {
      seg.addEventListener("mouseenter", (ev) => show(ev, seg.getAttribute("data-tip") || ""));
      seg.addEventListener("mousemove", move);
      seg.addEventListener("mouseleave", hide);
      seg.addEventListener("touchstart", (ev) => {
        const t = ev.touches && ev.touches[0];
        if (t) show(t, seg.getAttribute("data-tip") || "");
      }, { passive: true });
    });
    donutSvg.addEventListener("mouseleave", hide);
    document.addEventListener("scroll", hide, { passive: true });
  }


  function setupMoodOverviewControls() {
    const cb = document.getElementById("toggle-mood-overview");
    const body = document.body;
    if (!cb || !body) return;

    
    const isDraftContext = () => {
      if (body.getAttribute("data-page") !== "home") return false;
      if (!document.getElementById("articles-section")) return false;
      return (
        body.classList.contains("sidebars-active") ||
        body.classList.contains("sidebars-manual-on")
      );
    };

    const parseOnOff = (val, fallback) => {
      if (val == null) return fallback;
      const s = String(val).trim().toLowerCase();
      if (["off", "0", "false", "no"].includes(s)) return false;
      if (["on", "1", "true", "yes"].includes(s)) return true;
      return fallback;
    };

    const stored = safeGetLS(MOOD_OVERVIEW_STORAGE_KEY);
    const initialOn = parseOnOff(stored, !!cb.checked);

    const apply = () => {
      const on = !!cb.checked;
      body.classList.toggle("mood-overview-off", !on);
      
      body.classList.toggle("show-stimmungsbild", on && isDraftContext());
      safeSetLS(MOOD_OVERVIEW_STORAGE_KEY, on ? "on" : "off");
    };

    cb.checked = initialOn;
    apply();

    cb.addEventListener("change", () => {
      apply();
      renderMoodOverview();
    });

    updateOwnMoodToggleAvailability();
    updateBookmarksToggleAvailability();
  }

  function computeCountsFromArticles(articles) {
    const counts = { "✅": 0, "🟢": 0, "🟡": 0, "🟠": 0, "🔴": 0 };
    let total = 0;
    if (!Array.isArray(articles)) return { counts, total };

    articles.forEach((article) => {
      if (!article || typeof article !== "object") return;
      const summary = article.vote_summary;
      if (!summary || typeof summary !== "object") return;

      const versions = Array.isArray(summary.versions) ? summary.versions : [];
      const currentVid =
        article.current_version && article.current_version.id != null
          ? article.current_version.id
          : null;

      const v =
        (currentVid != null
          ? versions.find((x) => x && x.version_id === currentVid)
          : null) || versions[0] || null;

      const map =
        (v && (v.votes_by_main || v.main_vote_counts)) ||
        summary.votes_by_main ||
        summary.main_vote_counts ||
        {};

      Object.keys(counts).forEach((emoji) => {
        const c = map && typeof map === "object" ? (map[emoji] || 0) : 0;
        if (typeof c === "number" && c > 0) {
          counts[emoji] += c;
          total += c;
        }
      });
    });

    return { counts, total };
  }

  function computeCountsFromPersonalVotes() {
    const counts = { "✅": 0, "🟢": 0, "🟡": 0, "🟠": 0, "🔴": 0 };
    let total = 0;

    
    const allowed = new Set();
    document.querySelectorAll('article[data-article-id]').forEach((el) => {
      const id = el.getAttribute("data-article-id");
      if (id) allowed.add(String(id));
    });

    Object.keys(personalArticleVotes || {}).forEach((key) => {
      const entry = personalArticleVotes[key];
      if (!entry || typeof entry !== "object") return;

      const aid =
        entry.article_id != null ? String(entry.article_id) : String(key);
      if (allowed.size && !allowed.has(aid)) return;

      const mv = entry.main_vote;
      if (mv && Object.prototype.hasOwnProperty.call(counts, mv)) {
        counts[mv] += 1;
        total += 1;
      }
    });

    return { counts, total };
  }

  function computeAndRenderGlobalMood(articles) {
    const res = computeCountsFromArticles(articles);
    globalMoodCounts = res.counts;
    globalMoodTotal = res.total || 0;
    renderMoodOverview();
    updateHeroStatsFromCache();
  }

  function renderMoodOverview() {
    if (document.body.classList.contains("mood-overview-off")) return;

    const chartEl = document.getElementById("mood-chart");
    const legendEl = document.getElementById("mood-legend");
    const noteEl = document.getElementById("mood-note");
    if (!chartEl || !legendEl) return;

    const counts =
      globalMoodCounts || { "✅": 0, "🟢": 0, "🟡": 0, "🟠": 0, "🔴": 0 };
    const total = typeof globalMoodTotal === "number" ? globalMoodTotal : 0;

    const haloOn =
      !!klimaggAuth.user && document.body.classList.contains("toc-personal-mood-on");
    const my = haloOn ? computeCountsFromPersonalVotes() : null;

    const fmtPct = (n) =>
      Number(n).toLocaleString("de-DE", {
        maximumFractionDigits: 1,
        minimumFractionDigits: 0,
      }) + "%";

    
    
    legendEl.innerHTML = "";
    legendEl.style.display = "none";

    const moodMeaning = (label) => {
      if (label === "+++") return "starke Zustimmung";
      if (label === "+") return "Zustimmung";
      if (label === "o") return "neutral";
      if (label === "-") return "Ablehnung";
      if (label === "---") return "starke Ablehnung";
      return label || "";
    };
    const moodColorName = (emoji) => {
      if (emoji === "✅") return "Dunkelgrün";
      if (emoji === "🟢") return "Grün";
      if (emoji === "🟡") return "Gelb";
      if (emoji === "🟠") return "Orange";
      if (emoji === "🔴") return "Rot";
      return "Farbe";
    };
 
    const cx = 100, cy = 100;
    const r = 70;
    const sw = 18;
    const circ = 2 * Math.PI * r;

    const haloR = 82;
    const haloW = 8;
    const haloCirc = 2 * Math.PI * haloR;

    const segCircles = (countMap, sum, radius, strokeW, circumference, extra, withTitle) => {
      if (!sum) return "";
      let offset = 0;
      return MOOD_BUCKETS.map((b) => {
        const c = countMap[b.emoji] || 0;
        if (!c) return "";
        const len = (c / sum) * circumference;
        const dash = `${len} ${Math.max(0, circumference - len)}`;
        const pct = sum > 0 ? (100 * c) / sum : 0;
        const meaning = moodMeaning(b.label);
        const colorName = moodColorName(b.emoji);
        const voteWord = c === 1 ? "Stimme" : "Stimmen";
        const title = `${colorName}: ${meaning} · ${c} ${voteWord} (${fmtPct(pct)})`;
        const titleTag = withTitle ? `<title>${escapeHtml(title)}</title>` : "";
        const el = `
          <circle cx="${cx}" cy="${cy}" r="${radius}"
            fill="none"
            stroke="${b.color}"
            stroke-width="${strokeW}"
            stroke-linecap="butt"
            stroke-dasharray="${dash}"
            stroke-dashoffset="${-offset}"
            pointer-events="stroke"
            ${extra || ""}>
            ${titleTag}
          </circle>
        `;
        offset += len;
        return el;
      }).join("");
    };

    const bg = `
      <circle cx="${cx}" cy="${cy}" r="${r}" fill="none"
        stroke="rgba(148,163,184,0.35)" stroke-width="${sw}" />
    `;

    const mainSegs = segCircles(counts, total, r, sw, circ, "", true);
    const haloSegs =
      my && my.total > 0
        ? segCircles(my.counts, my.total, haloR, haloW, haloCirc, 'stroke-opacity="0.35"', false)
        : "";

    const label = total === 1 ? "Stimme" : "Stimmen";
    const centerText = `
      <text x="${cx}" y="${cy + 2}" text-anchor="middle" class="mood-center">
        ${total} ${label}
      </text>
      <text x="${cx}" y="${cy + 22}" text-anchor="middle" class="mood-center-sub">
        gesamt
      </text>
    `;

    chartEl.innerHTML = `
      <svg viewBox="0 0 200 200" role="img" aria-label="Stimmungsbild als Donut-Diagramm">
        <g transform="rotate(-90 ${cx} ${cy})">
          ${haloSegs}
          ${bg}
          ${mainSegs}
        </g>
        ${centerText}
      </svg>
    `;

  }

  function updateTOCPersonalMood() {
    const tocList = document.getElementById("toc-list");
    if (!tocList) return;

    const items = tocList.querySelectorAll("li[data-article-id]");
    items.forEach((li) => {
      const articleId = li.getAttribute("data-article-id");
      const vote = articleId
        ? personalArticleVotes[String(articleId)] || null
        : null;
      const rx = articleId
        ? personalArticleReactions[String(articleId)] || null
        : null;

      const moodSpan = li.querySelector(".toc-personal-mood");
      const bookmarkBarSpan = li.querySelector(".toc-bookmark-bar");
      const bookmarkTitleSpan = li.querySelector(".toc-bookmark-title");

      
      if (!moodSpan) return;

      
      if (!vote && !rx) {
        moodSpan.textContent = "";
        if (bookmarkBarSpan) { bookmarkBarSpan.textContent = ""; bookmarkBarSpan.removeAttribute("title"); }
        if (bookmarkTitleSpan) { bookmarkTitleSpan.textContent = ""; bookmarkTitleSpan.removeAttribute("title"); }
        return;
      }

      const mainVote = (vote && vote.main_vote) ? String(vote.main_vote) : "";
      const flagsRaw = (rx && Array.isArray(rx.emojis)) ? rx.emojis.slice() : [];

      
      let hasBookmark = false;
      const cleanedFlags = [];
      (flagsRaw || []).forEach((f) => {
        const s = typeof f === "string" ? f.trim() : "";
        if (!s) return;
        if (s === "🚩") { hasBookmark = true; return; }
        cleanedFlags.push(s);
      });
      flagsRaw.length = 0;
      cleanedFlags.forEach((f) => flagsRaw.push(f));

      const parts = [];
      if (mainVote) parts.push(mainVote);
      flagsRaw.forEach((f) => {
        if (typeof f === "string" && f.trim()) {
          parts.push(f.trim());
        }
      });

      if (parts.length) {
        moodSpan.textContent = " (" + parts.join("") + ")";
      } else {
        moodSpan.textContent = "";
      }

      
      if (hasBookmark) {
        if (bookmarkBarSpan) { bookmarkBarSpan.textContent = "🚩"; bookmarkBarSpan.title = "Lesezeichen für diesen Artikel gesetzt"; }
        if (bookmarkTitleSpan) { bookmarkTitleSpan.textContent = "🚩"; bookmarkTitleSpan.title = "Lesezeichen für diesen Artikel gesetzt"; }
      } else {
        if (bookmarkBarSpan) { bookmarkBarSpan.textContent = ""; bookmarkBarSpan.removeAttribute("title"); }
        if (bookmarkTitleSpan) { bookmarkTitleSpan.textContent = ""; bookmarkTitleSpan.removeAttribute("title"); }
      }
    });
  }

  function ensureReviewStateHost(root) {
    if (!root) return null;
    let host = root.querySelector(".review-state");
    if (host) return host;
    host = document.createElement("div");
    host.className = "review-state";
    root.insertBefore(host, root.firstChild);
    return host;
  }

  function setReviewMetricsPlaceholders() {
    const root = document.querySelector(".review-overview-metrics");
    if (!root) return;
    const totalEl = root.querySelector('[data-review-metric="total"]');
    const recentEl = root.querySelector('[data-review-metric="recent"]');
    const hitEl = root.querySelector('[data-review-metric="hit-rate"]');
    if (totalEl) totalEl.textContent = "Reviews gesamt: –";
    if (recentEl) recentEl.textContent = "Reviews seit letztem eigenen Kommentar: –";
    if (hitEl) hitEl.textContent = "Trefferquote (Übernahmen): –";
  }
 
  async function loadUserReviewStats() {
    const root = document.querySelector(".review-overview-metrics");
    if (!root) return;

    const host = ensureReviewStateHost(root);
    setReviewMetricsPlaceholders();

    if (!klimaggAuth.user || !klimaggAuth.accessToken) {
      if (host) {
        renderAsyncState(host, {
          kind: "empty",
          message: "Melde dich an, um deine Review-Statistik zu sehen.",
        });
      }
      return;
    }

    if (host) {
      renderAsyncState(host, {
        kind: "loading",
        message: "Review-Statistik wird geladen …",
      });
    }

    try {
      const stats = await apiFetchJson("/api/me/review-stats", {}, { authRequired: true });
      renderUserReviewStats(stats);
    } catch (err) {
      console.error("[KlimaGG] Fehler beim Laden der Review-Stats:", err);
      if (host) {
        renderAsyncState(host, {
          kind: "error",
          message: "Review-Statistik konnte nicht geladen werden.",
          onRetry: () => loadUserReviewStats(),
        });
      }
    }
  }

  function renderUserReviewStats(stats) {
    const root = document.querySelector(".review-overview-metrics");
    if (!root) return;

    const host = ensureReviewStateHost(root);
    if (host) host.innerHTML = "";

    const totalEl = root.querySelector('[data-review-metric="total"]');
    const recentEl = root.querySelector('[data-review-metric="recent"]');
    const hitEl = root.querySelector('[data-review-metric="hit-rate"]');

    if (totalEl) {
      const total = typeof stats.total_reviews === "number"
        ? stats.total_reviews
        : 0;
      totalEl.textContent = "Reviews gesamt: " + String(total);
    }

    if (recentEl) {
      const value = stats.reviews_since_last_comment;
      if (typeof value === "number") {
        recentEl.textContent =
          "Reviews seit letztem eigenen Kommentar: " + String(value);
      } else {
        recentEl.textContent =
          "Reviews seit letztem eigenen Kommentar: –";
      }
    }

    if (hitEl) {
      const acc = stats.accuracy_ratio;
      if (typeof acc === "number") {
        const pct = Math.round(acc * 100);
        hitEl.textContent =
          "Trefferquote (Übernahmen): " + String(pct) + " %";
      } else {
        hitEl.textContent = "Trefferquote (Übernahmen): –";
      }
    }
  }

  
  const reviewQueueState = {
    inited: false,
    busy: false,
    caseData: null, // { comment: ... }
    activeArticleId: null,
    activeCommentId: null,
    activeVersionId: null,
    els: {
      status: null,
      btnNext: null,
      btnEnd: null,
      box: null,
      meta: null,
      btnSubmit: null,
    },
  };

  function _findArticleTitle(articleId) {
    const id = Number(articleId);
    const list = Array.isArray(appState.articles_all) ? appState.articles_all : (Array.isArray(appState.articles) ? appState.articles : []);
    const hit = list.find(a => a && Number(a.id) === id);
    return hit ? (_articleDisplayLabelForClient(hit) || null) : null;
  }

  function _ensureReviewQueueElements() {
    const section = document.getElementById('review-overview-section');
    if (!section) return null;
    const body = section.querySelector('.app-details-body');
    if (!body) return null;

    // existierende Elemente wiederverwenden, ansonsten anlegen
    let statusEl = document.getElementById('review-queue-status');
    let btnNext = document.getElementById('review-next-btn');
    let btnEnd = document.getElementById('review-end-btn');
    let box = document.getElementById('review-case');
    let meta = document.getElementById('review-case-meta');
    let btnSubmit = document.getElementById('review-submit-btn');

    if (!statusEl || !btnNext || !btnEnd || !box || !meta || !btnSubmit) {
      
      const hr = document.createElement('hr');
      hr.className = 'divider';

      const statusDiv = document.createElement('div');
      statusDiv.className = 'klein';
      statusDiv.id = 'review-queue-status';
      statusDiv.textContent = 'Melde dich an, um Reviews zu bearbeiten.';

      const actions = document.createElement('div');
      actions.className = 'review-queue-actions';
      actions.style.marginTop = '10px';
      actions.style.display = 'flex';
      actions.style.gap = '8px';
      actions.style.flexWrap = 'wrap';
      actions.innerHTML =
        '<button id="review-next-btn" class="btn btn-ghost btn-sm" type="button" disabled>Review starten</button>' +
        '<button id="review-end-btn" class="btn btn-ghost btn-sm" type="button" disabled>Review verlassen</button>';

      const caseBox = document.createElement('div');
      caseBox.id = 'review-case';
      caseBox.className = 'review-case';
      caseBox.style.display = 'none';
      caseBox.style.marginTop = '10px';
      caseBox.innerHTML =
        '<div class="klein" id="review-case-meta">–</div>' +
        '<div class="review-v2-block" aria-label="Review-Slider">' +
        '  <div class="klein review-v2-label">Bewertung</div>' +
        '  <label class="review-slider-row"><span>🧭 Zielbeitrag</span><input type="range" min="0" max="5" step="1" value="0" data-review-slider="slider_goal" /><output data-review-slider-value="slider_goal">0</output></label>' +
        '  <label class="review-slider-row"><span>✍️ Stil / Verständlichkeit</span><input type="range" min="0" max="5" step="1" value="0" data-review-slider="slider_style" /><output data-review-slider-value="slider_style">0</output></label>' +
        '  <label class="review-slider-row"><span>🧩 Praxis / Umsetzbarkeit</span><input type="range" min="0" max="5" step="1" value="0" data-review-slider="slider_practical" /><output data-review-slider-value="slider_practical">0</output></label>' +
        '  <label class="review-slider-row"><span>⚖️ Rechtssicherheit</span><input type="range" min="0" max="5" step="1" value="0" data-review-slider="slider_legal" /><output data-review-slider-value="slider_legal">0</output></label>' +
        '</div>' +
        '<div class="review-v2-block" aria-label="Compliance-Prüfung">' +
        '  <div class="klein review-v2-label">Pflichtprüfung</div>' +
        '  <div class="review-compliance-list" style="display:grid; gap:0.15rem;">' +
        '    <label class="klein check-row" style="display:flex; align-items:flex-start; justify-content:flex-start; width:100%; text-align:left;"><input type="checkbox" data-review-compliance="compliance_no_personal_data" style="flex:0 0 auto; margin-top:0.2rem;" /><span>Keine personenbezogenen Daten</span></label>' +
        '    <label class="klein check-row" style="display:flex; align-items:flex-start; justify-content:flex-start; width:100%; text-align:left;"><input type="checkbox" data-review-compliance="compliance_copyright_ok" style="flex:0 0 auto; margin-top:0.2rem;" /><span>Urheberrecht / Quellen sind ok</span></label>' +
        '    <label class="klein check-row" style="display:flex; align-items:flex-start; justify-content:flex-start; width:100%; text-align:left;"><input type="checkbox" data-review-compliance="compliance_no_illegal_content" style="flex:0 0 auto; margin-top:0.2rem;" /><span>Kein Rechtsverstoß, Hass oder Beleidigung</span></label>' +
        '  </div>' +
        '</div>' +
        '<div class="review-v2-block">' +
        '  <div class="klein review-v2-label">Entscheidung</div>' +
        '  <div class="toggle-row user-toggle-row" role="group" aria-label="Review-Entscheidung">' +
        '    <label class="toggle-chip"><input type="radio" name="review-rec" value="annehmen" /><span>Annehmen</span></label>' +
        '    <label class="toggle-chip"><input type="radio" name="review-rec" value="korrigieren" /><span>Korrigieren</span></label>' +
        '    <label class="toggle-chip"><input type="radio" name="review-rec" value="ablehnen" /><span>Ablehnen</span></label>' +
        '  </div>' +
        '</div>' +
        '<div class="review-v2-block" aria-label="Feedback für den Autor">' +
        '  <div class="klein review-v2-label">Feedback für den Autor</div>' +
        '  <div class="review-feedback-grid">' +
        '    <label class="toggle-chip"><input type="checkbox" data-review-feedback="sources_missing" /><span>Quellen fehlen</span></label>' +
        '    <label class="toggle-chip"><input type="checkbox" data-review-feedback="legal_unclear" /><span>Juristisch unklar</span></label>' +
        '    <label class="toggle-chip"><input type="checkbox" data-review-feedback="too_imprecise" /><span>Zu unpräzise</span></label>' +
        '    <label class="toggle-chip"><input type="checkbox" data-review-feedback="wrong_article_location" /><span>Artikelstelle passt nicht</span></label>' +
        '    <label class="toggle-chip"><input type="checkbox" data-review-feedback="practicality_unclear" /><span>Umsetzbarkeit fraglich</span></label>' +
        '    <label class="toggle-chip"><input type="checkbox" data-review-feedback="tone_or_style" /><span>Ton / Stil problematisch</span></label>' +
        '    <label class="toggle-chip"><input type="checkbox" data-review-feedback="formal_issue" /><span>Formaler Hinweis</span></label>' +
        '  </div>' +
        '</div>' +
        '<p class="klein review-vote-hint">Dein Vote macht das Review aussagekräftiger. Du kannst ihn direkt an der Kommentar-Karte setzen.</p>' +
        '<button id="review-submit-btn" class="btn btn-primary btn-full" type="button" disabled style="margin-top:10px;">Review senden</button>';

      body.appendChild(hr);
      body.appendChild(statusDiv);
      body.appendChild(actions);
      body.appendChild(caseBox);

      statusEl = statusDiv;
      btnNext = actions.querySelector('#review-next-btn');
      btnEnd = actions.querySelector('#review-end-btn');
      box = caseBox;
      meta = caseBox.querySelector('#review-case-meta');
      btnSubmit = caseBox.querySelector('#review-submit-btn');
    }

    reviewQueueState.els = { status: statusEl, btnNext, btnEnd, box, meta, btnSubmit };
    return reviewQueueState.els;
  }

  async function _endActiveReviewVisuals() {
    if (reviewQueueState.activeArticleId != null) {
      restoreBlocksAfterReview(reviewQueueState.activeArticleId);

      // Review-Layer (Inline-Diff) wieder entfernen
      try {
        await deactivateReviewInlinePreview("_endActiveReviewVisuals");
      } catch (_) {}
    }
    try { clearPrivateCommentOverlay("review"); } catch (_) {}
    reviewQueueState.activeArticleId = null;
    reviewQueueState.activeCommentId = null;
    reviewQueueState.activeVersionId = null;
    try { document.querySelectorAll('.review-target').forEach(el => el.classList.remove('review-target')); } catch (_) {}
  }

  function refreshReviewQueueUI() {
    const els = _ensureReviewQueueElements();
    if (!els) return;

    const authed = !!(klimaggAuth.user && klimaggAuth.accessToken);
    const hasCase = !!(reviewQueueState.caseData && reviewQueueState.caseData.comment);
    const busy = !!reviewQueueState.busy;

    if (els.btnNext) els.btnNext.disabled = !authed || busy;
    if (els.btnEnd) els.btnEnd.disabled = !authed || busy || !hasCase;

    if (!authed) {
      if (els.status) els.status.textContent = 'Melde dich an, um Reviews zu bearbeiten.';
      if (els.box) els.box.style.display = 'none';
      return;
    }

    if (els.status && !busy) {
      els.status.textContent = hasCase
        ? 'Review-Fall geladen. Bitte Entscheidung, Slider und Pflichtprüfung ausfüllen.'
        : 'Klicke auf „Review starten“, um einen Vorschlag zu prüfen.';
    }

    
    if (els.btnSubmit) {
      const rec = document.querySelector('input[name="review-rec"]:checked');
      const complianceInputs = Array.from(document.querySelectorAll('[data-review-compliance]'));
      const complianceOk = complianceInputs.length === 0 || complianceInputs.every((input) => !!input.checked);
      els.btnSubmit.disabled = busy || !authed || !hasCase || !rec || !complianceOk;
    }
  }

  function resetReviewV2Form() {
    try {
      document.querySelectorAll('input[name="review-rec"]').forEach((r) => { r.checked = false; });
      document.querySelectorAll('[data-review-compliance]').forEach((input) => { input.checked = false; });
      document.querySelectorAll('[data-review-feedback]').forEach((input) => { input.checked = false; });
      document.querySelectorAll('[data-review-slider]').forEach((input) => {
        input.value = "0";
        updateReviewSliderOutput(input);
      });
    } catch (_) {}
  }

  function updateReviewSliderOutput(input) {
    if (!input) return;
    const key = input.dataset ? String(input.dataset.reviewSlider || "") : "";
    if (!key) return;
    const out = document.querySelector('[data-review-slider-value="' + key + '"]');
    if (out) out.textContent = String(input.value || "0");
  }

  function collectReviewV2Payload(commentId) {
    const recEl = document.querySelector('input[name="review-rec"]:checked');
    const decision = recEl ? String(recEl.value || "").trim() : "";
    const payload = {
      comment_id: commentId,
      decision,
      report_triggered: false,
      feedback_tags: [],
      visible_to_public: false,
      review_note: null,
      checks: null,
    };

    document.querySelectorAll('[data-review-slider]').forEach((input) => {
      const key = input && input.dataset ? String(input.dataset.reviewSlider || "") : "";
      if (!key) return;
      const n = Number.parseInt(String(input.value || "0"), 10);
      payload[key] = Number.isFinite(n) ? Math.max(0, Math.min(5, n)) : 0;
    });

    document.querySelectorAll('[data-review-compliance]').forEach((input) => {
      const key = input && input.dataset ? String(input.dataset.reviewCompliance || "") : "";
      if (!key) return;
      payload[key] = !!input.checked;
    });

    document.querySelectorAll('[data-review-feedback]:checked').forEach((input) => {
      const key = input && input.dataset ? String(input.dataset.reviewFeedback || "") : "";
      if (key && !payload.feedback_tags.includes(key)) payload.feedback_tags.push(key);
    });

    return payload;
  }

  async function loadNextReviewCase(options) {
    const els = _ensureReviewQueueElements();
    if (!els) return;
    if (!klimaggAuth.user || !klimaggAuth.accessToken) {
      refreshReviewQueueUI();
      return;
    }
    const force = !!(options && options.force);
    if (reviewQueueState.busy && !force) return;

    
    
    
    reviewQueueState.busy = true;
    refreshReviewQueueUI();
    if (els.status) els.status.textContent = 'Lade Review-Fall …';

    
    await _endActiveReviewVisuals();
    reviewQueueState.caseData = null;
    resetReviewV2Form();

    try {
      const data = await apiFetchJson('/api/reviews/next', {}, { authRequired: true });
      if (!data || !data.comment) {
        throw new Error('Unerwartete Antwort von /api/reviews/next');
      }

      reviewQueueState.caseData = data;
      const c = data.comment;

      const articleTitle = _findArticleTitle(c.article_id) || ('Artikel #' + String(c.article_id));
      const created = (typeof c.created_at === 'string' && c.created_at.length >= 10) ? c.created_at.slice(0, 10) : '';
      const metaText = 'Kommentar #' + String(c.id) + ' · ' + articleTitle + (created ? (' · ' + created) : '');
      if (els.meta) els.meta.textContent = metaText;
      if (els.box) els.box.style.display = '';

      reviewQueueState.activeArticleId = c.article_id;
      reviewQueueState.activeCommentId = c.id;
      try { setPrivateCommentOverlay("review", c, { refresh: true }); } catch (_) {}
      try { setCommentFocusId(c.article_id, String(c.id)); } catch (_) {}
      try { await ensurePrivateOverlayCommentRendered(c.article_id, c.id, { markReviewTarget: true, scroll: true }); } catch (_) {}

      
      forceAllBlocksForReview(c.article_id);

      
      try {
        await activateReviewInlinePreview(c, "loadNextReviewCase");
      } catch (_) {}

      
      try { await ensurePrivateOverlayCommentRendered(c.article_id, c.id, { markReviewTarget: true, scroll: true }); } catch (_) {}

      if (els.status) {
        const hasArt = !!findArticleRootById(c.article_id);
        const hint = data && data.ui_hint ? String(data.ui_hint) + ' ' : '';
        els.status.textContent = hasArt
          ? hint + 'Bitte Entscheidung, Slider und Pflichtprüfung ausfüllen.'
          : hint + 'Review geladen, aber der betroffene Artikel ist im aktuellen Umfang nicht sichtbar.';
      }
    } catch (err) {
      
      const is404 = err && (err.status === 404 || (err.message && String(err.message).includes('404')));
      console.warn('[KlimaGG] Review-Fall konnte nicht geladen werden:', err);
      reviewQueueState.caseData = null;
      if (els.box) els.box.style.display = 'none';
      if (els.status) {
        els.status.textContent = is404
          ? 'Kein Review-Fall verfügbar.'
          : 'Review-Fall konnte nicht geladen werden.';
      }
    } finally {
      reviewQueueState.busy = false;
      refreshReviewQueueUI();
    }
  }

  async function submitActiveReview() {
    const els = _ensureReviewQueueElements();
    if (!els) return;
    if (!klimaggAuth.user || !klimaggAuth.accessToken) {
      refreshReviewQueueUI();
      return;
    }
    if (reviewQueueState.busy) return;

    const hasCase = !!(reviewQueueState.caseData && reviewQueueState.caseData.comment);
    if (!hasCase) {
      if (els.status) els.status.textContent = 'Kein Review-Fall geladen.';
      return;
    }

    const recEl = document.querySelector('input[name="review-rec"]:checked');
    const rec = recEl ? String(recEl.value || '').trim() : '';
    if (!rec) {
      if (els.status) els.status.textContent = 'Bitte wähle zuerst eine Entscheidung.';
      return;
    }

    const complianceInputs = Array.from(document.querySelectorAll('[data-review-compliance]'));
    const complianceOk = complianceInputs.length === 0 || complianceInputs.every((input) => !!input.checked);
    if (!complianceOk) {
      if (els.status) els.status.textContent = 'Bitte bestätige zuerst alle Pflichtprüfungen.';
      refreshReviewQueueUI();
      return;
    }

    const c = reviewQueueState.caseData.comment;
    const payload = collectReviewV2Payload(c.id);

    reviewQueueState.busy = true;
    refreshReviewQueueUI();
    if (els.status) els.status.textContent = 'Review wird gesendet …';

    try {
      await apiFetchJson(
        '/api/reviews',
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload),
        },
        { authRequired: true }
      );

      notify('Review gesendet.', { type: 'success' });

      
      try { await loadUserReviewStats(); } catch (_) {}
      try { requestRefreshCascade('review', { articleId: c.article_id }); } catch (_) {}

      
      await loadNextReviewCase({ force: true });
    } catch (err) {
      console.error('[KlimaGG] Fehler beim Senden des Reviews:', err);
      if (els.status) {
        els.status.textContent = 'Review konnte nicht gesendet werden.';
      }
      notify('Review konnte nicht gesendet werden.', { type: 'error' });
    } finally {
      reviewQueueState.busy = false;
      refreshReviewQueueUI();
    }
  }

  async function endReviewCase() {
    const els = _ensureReviewQueueElements();
    await _endActiveReviewVisuals();
    reviewQueueState.caseData = null;
    if (els && els.box) els.box.style.display = 'none';
    if (els && els.status && klimaggAuth.user && klimaggAuth.accessToken) {
      els.status.textContent = 'Review verlassen. Klicke auf „Review starten“. ';
    }
    refreshReviewQueueUI();
  }

  async function activateReviewInlinePreview(commentObj, source) {
    await deactivateVersionLayerForCommentAction();
    const c = (commentObj && typeof commentObj === "object") ? commentObj : null;
    if (!c || c.id == null || c.article_id == null) return false;
    const vid = (c.version_id != null)
      ? c.version_id
      : (typeof getCurrentVersionIdForArticle === "function"
          ? (getCurrentVersionIdForArticle(c.article_id) || null)
          : null);
    try {
      window.__klimaggCommentCacheById = window.__klimaggCommentCacheById || {};
      window.__klimaggCommentCacheById[String(c.id)] = c;
    } catch (_) {}
    try { setPrivateCommentOverlay("review", c, { refresh: false }); } catch (_) {}
    const api = getInlineDiffApiSafe();
    if (api && typeof api.toggle === "function") {
      await api.toggle(
        c.article_id,
        String(c.id),
        true,
        vid || undefined,
        { source: String(source || "activateReviewInlinePreview") }
      );
    }
    reviewQueueState.activeArticleId = c.article_id;
    reviewQueueState.activeCommentId = c.id;
    reviewQueueState.activeVersionId = vid || null;
    return true;
  }

  async function deactivateReviewInlinePreview(source) {
    const aid = reviewQueueState.activeArticleId;
    const vid = reviewQueueState.activeVersionId != null ? reviewQueueState.activeVersionId : undefined;
    const cid = reviewQueueState.activeCommentId != null ? String(reviewQueueState.activeCommentId) : null;
    if (!aid || !cid) return false;
    const api = getInlineDiffApiSafe();
    if (api && typeof api.toggle === "function") {
      await api.toggle(
        aid,
        cid,
        false,
        vid,
        { source: String(source || "deactivateReviewInlinePreview") }
      );
    }
    return true;
  }

  function setupReviewQueuePanel() {
    if (reviewQueueState.inited) return;
    const els = _ensureReviewQueueElements();
    if (!els) return;
    reviewQueueState.inited = true;

    if (els.btnNext) {
      els.btnNext.addEventListener('click', (ev) => {
        ev.preventDefault();
        loadNextReviewCase();
      });
    }

    if (els.btnEnd) {
      els.btnEnd.addEventListener('click', async (ev) => {
        ev.preventDefault();
        await endReviewCase();
      });
    }

    if (els.btnSubmit) {
      els.btnSubmit.addEventListener('click', (ev) => {
        ev.preventDefault();
        submitActiveReview();
      });
    }

    
    try {
      document.addEventListener('change', (ev) => {
        const t = ev && ev.target;
        if (!t) return;
        if (t.name === 'review-rec' || (t.dataset && (t.dataset.reviewCompliance || t.dataset.reviewFeedback))) {
          refreshReviewQueueUI();
        }
      });
      document.addEventListener('input', (ev) => {
        const t = ev && ev.target;
        if (!t || !t.dataset || !t.dataset.reviewSlider) return;
        updateReviewSliderOutput(t);
      });
    } catch (_) {}

    refreshReviewQueueUI();
  }

  function setupCommentPanel() {
    const formSection = document.querySelector(".comment-form-section");
    if (!formSection) return;

    const contextDisplay = document.getElementById("comment-context-display");
    const anchorInput = document.getElementById("comment-anchor");
    const proposalInput = document.getElementById("comment-proposal");
    const explanationInput = document.getElementById("comment-explanation");
    const sourcesInput = document.getElementById("comment-sources");
    const cc0Checkbox = document.getElementById("comment-cc0");
    const privacyCheckbox = document.getElementById("comment-privacy");
    const rulesCheckbox = document.getElementById("comment-rules");
    const saveDraftBtn = document.getElementById("comment-save-draft");
    const submitBtn = document.getElementById("comment-submit");
    const previewBtn = document.getElementById("comment-preview-toggle");
    const deleteDraftBtn = document.getElementById("comment-delete-draft");
    const DRAFT_PREVIEW_STATE_KEY = "klimagg.draft_preview_state_v1";
    const DRAFT_PREVIEW_ACTIVE_STYLE = "background: var(--accent-color, #2f6fed); color: #fff; border-color: transparent;";
    const statusText = document.getElementById("comment-status-text");
    const pickNearestBtn = document.getElementById("comment-pick-nearest");
    const articleSelect = document.getElementById("comment-article-select");
    const commentModeSelect = document.getElementById("comment-mode");
    const commentModeHint = document.getElementById("comment-mode-hint");
    const articlePickerEl = document.getElementById("comment-article-picker");
    const proposalField = document.getElementById("comment-proposal-field");
    const proposalLabel = document.getElementById("comment-proposal-label");
    const newArticleNote = document.getElementById("comment-new-article-note");
    const deleteArticleNote = document.getElementById("comment-delete-article-note");
    const llmSupportEl = document.getElementById("comment-llm-support");
    const llmSummaryEl = document.getElementById("comment-llm-summary");
    const llmDownloadLawBtn = document.getElementById("comment-llm-download-law");
    const llmDownloadArticleBtn = document.getElementById("comment-llm-download-article");
    const llmDownloadInstructionsBtn = document.getElementById("comment-llm-download-instructions");
    const llmCopyPromptBtn = document.getElementById("comment-llm-copy-prompt");
    const llmAssistedCheckbox = document.getElementById("comment-llm-assisted");
    const llmStatusEl = document.getElementById("comment-llm-status");
    const relatedEnabledCheckbox = document.getElementById("comment-related-enabled");
    const relatedBodyEl = document.getElementById("comment-related-body");
    const relatedGroupSelect = document.getElementById("comment-related-group-select");
    const relatedTitleInput = document.getElementById("comment-related-title");
    const relatedNoteInput = document.getElementById("comment-related-note");
    const relatedSourcesInput = document.getElementById("comment-related-sources");
    const relatedBundleCheckbox = document.getElementById("comment-related-integrate-bundle");

    const impactHost = document.getElementById("comment-impact-scores");
    const impactArt2Link = document.getElementById("comment-impact-art2-link");
    const impactInputs = {
      goal: {
        input: document.getElementById("comment-impact-goal"),
        valueEl: document.getElementById("comment-impact-goal-value"),
      },
      clarity: {
        input: document.getElementById("comment-impact-clarity"),
        valueEl: document.getElementById("comment-impact-clarity-value"),
      },
      practical: {
        input: document.getElementById("comment-impact-practical"),
        valueEl: document.getElementById("comment-impact-practical-value"),
      },
      legal: {
        input: document.getElementById("comment-impact-legal"),
        valueEl: document.getElementById("comment-impact-legal-value"),
      },
    };

    const IMPACT_KEYS = ["goal", "clarity", "practical", "legal"];
    const COMMENT_MODE_DEFAULT = "change";
    const NEW_ARTICLE_INSERT_AFTER_LABEL = "Einfügen nach folgender Artikel-Kennung";
    const NEW_ARTICLE_TEMPLATE_MINIMD = [
      "### start: meta ###",
      "$ Artikel-Kennung: $ <bitte ersetzen: z.B. Artikel 8a oder VO 8>",
      "$ Artikel-Titel: $ <bitte ersetzen: Titel des neuen Artikels>",
      "$ Artikel-Kurztitel: $ <optional: kurzer Titel für spätere TOC-Anzeige>",
      "$ Artikel im Inhaltsverzeichnis: $ nein",
      "$ " + NEW_ARTICLE_INSERT_AFTER_LABEL + ": $ <bitte ersetzen: sichtbare Kennung, z.B. Artikel 8>",
      "### end: meta ###",
      "",
      "### start: kurzinfo ###",
      "### end: kurzinfo ###",
      "",
      "### start: story ###",
      "### end: story ###",
      "",
      "### start: einleitung ###",
      "### end: einleitung ###",
      "",
      "### start: juristisch ###",
      "### end: juristisch ###",
      "",
      "### start: juristisch2 ###",
      "### end: juristisch2 ###",
      "",
      "### start: anmerkung ###",
      "### end: anmerkung ###",
      "",
    ].join("\n");
    const NEW_ARTICLE_BASELINE_MINIMD = [
      "### start: meta ###",
      "$ Artikel-Kennung: $ ",
      "$ Artikel-Titel: $ ",
      "$ Artikel-Kurztitel: $ ",
      "$ Artikel im Inhaltsverzeichnis: $ nein",
      "$ " + NEW_ARTICLE_INSERT_AFTER_LABEL + ": $ ",
      "### end: meta ###",
      "",
      "### start: kurzinfo ###",
      "### end: kurzinfo ###",
      "",
      "### start: story ###",
      "### end: story ###",
      "",
      "### start: einleitung ###",
      "### end: einleitung ###",
      "",
      "### start: juristisch ###",
      "### end: juristisch ###",
      "",
      "### start: juristisch2 ###",
      "### end: juristisch2 ###",
      "",
      "### start: anmerkung ###",
      "### end: anmerkung ###",
      "",
    ].join("\n");
    const LLM_ASSISTED_SOURCE_LINE = "Erstellt mit Unterstützung von Sprachmodellen.";
    const LLM_MODE_DEFAULT = "change";
    const LLM_PROMPT_TEMPLATE_START = "<!-- KGG-LLM-PROMPT-TEMPLATE-START -->";
    const LLM_PROMPT_TEMPLATE_END = "<!-- KGG-LLM-PROMPT-TEMPLATE-END -->";
    let llmContextStatusCache = null;
    let llmContextStatusLoaded = false;


    // ---------------------------------------------------------------------
    
    // View changes must not leave the editor form hidden by stale inline styles.
    
    
    // - #comment-draft-view   (Formular)
    // ---------------------------------------------------------------------
    const panelBody =
      formSection.closest(".app-details-body") || formSection.parentElement;
    const progressEl = panelBody
      ? panelBody.querySelector(".comment-progress")
      : null;

    
    const overviewView = document.getElementById("comment-overview-view");
    const draftView = document.getElementById("comment-draft-view");
    const overviewListEl = document.getElementById("comment-overview-list");
    const overviewStatusEl = document.getElementById("comment-overview-status");
    const filterTabsEl = document.getElementById("comment-filter-tabs");
    const refreshBtn = document.getElementById("comment-refresh");
    const newBtn = document.getElementById("comment-new");
    const backBtn = document.getElementById("comment-back-to-overview");
    const commentPanelDetails = document.getElementById("comment-overview-section");
    const commentPanelSummary = commentPanelDetails ? commentPanelDetails.querySelector("summary") : null;
 
    
    if (!commentState.overviewFilters) {
      commentState.overviewFilters = {
        draft: true,
        published: false,
        archived: false,
        integrated: false,
        deleted: false,
      };
    }

    
    if (!commentState.myDraftsById) commentState.myDraftsById = {};

    const RELATED_COMMENTS_KEY = "related_comments";

    function makeRelatedGroupKey() {
      try {
        if (window.crypto && typeof window.crypto.getRandomValues === "function") {
          const arr = new Uint32Array(2);
          window.crypto.getRandomValues(arr);
          return "rel_" + Date.now().toString(36) + "_" + arr[0].toString(36) + arr[1].toString(36);
        }
      } catch (_) {}
      return "rel_" + Date.now().toString(36) + "_" + Math.random().toString(36).slice(2, 10);
    }

    function getRelatedPayloadFromComment(c) {
      const sp = c && c.structure_payload && typeof c.structure_payload === "object" ? c.structure_payload : null;
      const rel = sp && sp[RELATED_COMMENTS_KEY] && typeof sp[RELATED_COMMENTS_KEY] === "object" ? sp[RELATED_COMMENTS_KEY] : null;
      return rel || null;
    }

    function upsertMyDraftCache(c) {
      if (!c || typeof c !== "object" || c.id == null) return;
      if (!commentState.myDraftsById) commentState.myDraftsById = {};
      commentState.myDraftsById[String(c.id)] = c;
      try { refreshRelatedGroupSelect(); } catch (_) {}
    }

    function setRelatedBodyVisible() {
      const on = !!(relatedEnabledCheckbox && relatedEnabledCheckbox.checked);
      if (relatedBodyEl) relatedBodyEl.hidden = !on;
    }

    function clearRelatedForm() {
      if (relatedEnabledCheckbox) relatedEnabledCheckbox.checked = false;
      if (relatedGroupSelect) relatedGroupSelect.value = "";
      if (relatedTitleInput) relatedTitleInput.value = "";
      if (relatedNoteInput) relatedNoteInput.value = "";
      if (relatedSourcesInput) relatedSourcesInput.value = "";
      if (relatedBundleCheckbox) relatedBundleCheckbox.checked = false;
      setRelatedBodyVisible();
    }

    function relatedDraftGroups() {
      const drafts = Object.values(commentState.myDraftsById || {}).filter((c) => c && typeof c === "object");
      const curId = commentState.currentCommentId != null ? String(commentState.currentCommentId) : "";
      const map = new Map();
      drafts.forEach((c) => {
        const rel = getRelatedPayloadFromComment(c);
        const key = rel && rel.group_key ? String(rel.group_key).trim() : "";
        if (!key) return;
        if (curId && String(c.id) === curId) return;
        if (!map.has(key)) {
          map.set(key, {
            key,
            title: String((rel && rel.title) || "Zusammenhängender Vorschlag").trim(),
            note: String((rel && rel.note) || "").trim(),
            sources: String((rel && rel.sources) || "").trim(),
            integrate_as_bundle: !!(rel && rel.integrate_as_bundle),
            ids: [],
          });
        }
        const row = map.get(key);
        if (c.id != null) row.ids.push(Number(c.id));
      });
      return Array.from(map.values()).sort((a, b) => String(a.title || "").localeCompare(String(b.title || ""), "de"));
    }

    function refreshRelatedGroupSelect(selectedKey) {
      if (!relatedGroupSelect) return;
      const selected = selectedKey != null ? String(selectedKey) : String(relatedGroupSelect.value || "");
      relatedGroupSelect.innerHTML = '<option value="">Neuen Zusammenhang beschreiben</option>';
      relatedDraftGroups().forEach((g) => {
        const opt = document.createElement("option");
        opt.value = g.key;
        opt.textContent = (g.title || "Zusammenhängender Vorschlag") + (g.ids.length ? " (#" + g.ids.join(", #") + ")" : "");
        relatedGroupSelect.appendChild(opt);
      });
      if (selected) {
        try { relatedGroupSelect.value = selected; } catch (_) {}
      }
    }

    function applySelectedRelatedGroup() {
      if (!relatedGroupSelect) return;
      const key = String(relatedGroupSelect.value || "").trim();
      if (!key) return;
      const group = relatedDraftGroups().find((g) => g.key === key);
      if (!group) return;
      if (relatedEnabledCheckbox) relatedEnabledCheckbox.checked = true;
      if (relatedTitleInput && !relatedTitleInput.value.trim()) relatedTitleInput.value = group.title || "";
      if (relatedNoteInput && !relatedNoteInput.value.trim()) relatedNoteInput.value = group.note || "";
      if (relatedSourcesInput && !relatedSourcesInput.value.trim()) relatedSourcesInput.value = group.sources || "";
      if (relatedBundleCheckbox) relatedBundleCheckbox.checked = !!group.integrate_as_bundle;
      setRelatedBodyVisible();
    }

    function applyRelatedFormFromComment(c) {
      const rel = getRelatedPayloadFromComment(c);
      if (!rel || !rel.enabled) {
        clearRelatedForm();
        refreshRelatedGroupSelect("");
        return;
      }
      if (relatedEnabledCheckbox) relatedEnabledCheckbox.checked = true;
      refreshRelatedGroupSelect(rel.group_key || "");
      if (relatedGroupSelect && rel.group_key) relatedGroupSelect.value = String(rel.group_key);
      if (relatedTitleInput) relatedTitleInput.value = rel.title ? String(rel.title) : "";
      if (relatedNoteInput) relatedNoteInput.value = rel.note ? String(rel.note) : "";
      if (relatedSourcesInput) relatedSourcesInput.value = rel.sources ? String(rel.sources) : "";
      if (relatedBundleCheckbox) relatedBundleCheckbox.checked = !!rel.integrate_as_bundle;
      setRelatedBodyVisible();
    }

    function collectRelatedStructurePayload(baseStructure) {
      const base = baseStructure && typeof baseStructure === "object" && !Array.isArray(baseStructure)
        ? Object.assign({}, baseStructure)
        : {};
      if (!relatedEnabledCheckbox || !relatedEnabledCheckbox.checked) {
        delete base[RELATED_COMMENTS_KEY];
        return Object.keys(base).length ? base : null;
      }
      const selectedKey = relatedGroupSelect ? String(relatedGroupSelect.value || "").trim() : "";
      const oldRel = commentState.currentStructurePayload && commentState.currentStructurePayload[RELATED_COMMENTS_KEY]
        ? commentState.currentStructurePayload[RELATED_COMMENTS_KEY]
        : null;
      const groupKey = selectedKey || (oldRel && oldRel.group_key ? String(oldRel.group_key) : makeRelatedGroupKey());
      const note = relatedNoteInput ? String(relatedNoteInput.value || "").trim() : "";
      const sources = relatedSourcesInput ? String(relatedSourcesInput.value || "").trim() : "";
      const title = relatedTitleInput ? String(relatedTitleInput.value || "").trim() : "";
      const ownIds = relatedDraftGroups()
        .filter((g) => g.key === groupKey)
        .flatMap((g) => g.ids || [])
        .filter((id) => Number.isFinite(Number(id)));
      if (commentState.currentCommentId != null) ownIds.push(Number(commentState.currentCommentId));
      base[RELATED_COMMENTS_KEY] = {
        version: 1,
        enabled: true,
        group_key: groupKey,
        title: title || "Zusammenhängender Vorschlag",
        note: note,
        sources: sources,
        own_comment_ids: Array.from(new Set(ownIds.map((x) => Number(x)).filter((x) => Number.isFinite(x) && x > 0))),
        integrate_as_bundle: !!(relatedBundleCheckbox && relatedBundleCheckbox.checked),
      };
      return base;
    }

    function setCommentPanelMode(mode) {
      const m = mode === "editor" ? "editor" : "overview";
      commentState.uiMode = m;

      const isEditor = m === "editor";
      if (overviewView) overviewView.hidden = isEditor;
      if (draftView) draftView.hidden = !isEditor;

      
      if (progressEl) progressEl.style.display = isEditor ? "" : "none";

      
      try {
        if (commentPanelSummary) {
          commentPanelSummary.textContent = isEditor ? "Kommentar-Entwurf" : "Kommentar-Übersicht";
        }
        if (commentPanelDetails && isEditor) {
          commentPanelDetails.open = true;
        }
      } catch (_) {}
    }

    function fmtDate10(s) {
      if (!s) return "–";
      const raw = String(s || "");
      const t = Date.parse(raw);
      if (!Number.isFinite(t)) {
        const iso = raw.slice(0, 10);
        const m = iso.match(new RegExp("^(\\d{4})-(\\d{2})-(\\d{2})$"));
        if (m) return m[3] + "." + m[2] + "." + m[1];
        return iso;
      }
      const d = new Date(t);
      const dd = String(d.getDate()).padStart(2, "0");
      const mm = String(d.getMonth() + 1).padStart(2, "0");
      const yyyy = String(d.getFullYear());
      return dd + "." + mm + "." + yyyy;
    }

    function getArticleLabel(articleId) {
      const aid = Number(articleId);
      const arts = Array.isArray(appState.articles_all) ? appState.articles_all : [];
      const a = arts.find((x) => x && Number(x.id) === aid);
      if (!a) return "Artikel " + String(articleId);
      return _articleDisplayLabelForClient(a) || ("Artikel " + String(articleId));
    }

    function statusBadgeLabel(c) {
      const st = String((c && c.status) || "").toLowerCase();
      const life = String((c && c.lifecycle_status) || "").toLowerCase();
      if (st === "entwurf") return "Entwurf";
      if (st === "reif") return "Reif";
      if (st === "review") return "Review";
      if (st === "veröffentlicht" || st === "veroeffentlicht") return "Veröffentlicht";
      if (st === "korrigieren") return "Korrigieren";
      if (st === "archiviert") return "Archiviert";
      if (st) return st;
      if (life) return life;
      return "—";
    }


    
    

    function _truncateLabel(s, maxLen) {
      const t = String(s || "").trim();
      const m = Math.max(3, Number(maxLen) || 25);
      if (t.length <= m) return t;
      return t.slice(0, Math.max(0, m - 1)) + "…";
    }

    function getArticleLabelFromOverview(c) {
      
      const full =
        (c && (c.article_title || c.articleTitle))
          ? String(c.article_title || c.articleTitle)
          : getArticleLabel(c && c.article_id);
      
      return _truncateLabel(full, 25);
    }

    async function openDraftFromOverviewItem(c) {
      if (!c || typeof c !== "object") return;
      if (!ensureLoggedIn()) return;
      await deactivateVersionLayerForCommentAction();

      const id = c.id != null ? String(c.id) : "";
      let full = null;

      // 1) Cache (drafts endpoint)
      try {
        if (id && commentState.myDraftsById && commentState.myDraftsById[id]) {
          full = commentState.myDraftsById[id];
        }
      } catch (_) {}

      // 2) Fallback: Einzelabruf
      if (!full && id) {
        try {
          full = await apiFetchJson(`/api/me/comments/${encodeURIComponent(id)}`, {}, { authRequired: true });
        } catch (err) {
          console.error("[KlimaGG] Konnte Draft nicht laden:", err);
          notify("Draft konnte nicht geladen werden – siehe Konsole.", { type: "warn" });
          return;
        }
      }

      if (!full) {
        notify("Draft nicht gefunden.", { type: "warn" });
        return;
      }

      try {
        openExistingDraft(full);
      } catch (err) {
        console.error("[KlimaGG] Fehler beim Öffnen des Drafts:", err);
        notify("Fehler beim Öffnen – siehe Konsole.", { type: "warn" });
      }
    }

    function openExistingDraft(c) {
      if (!c) return;
      if (!ensureLoggedIn()) return;

      commentState.currentArticleId = c.article_id;
      commentState.currentVersionId = c.version_id || null;
      commentState.currentCommentId = c.id;
      commentState.currentCommentStatus = String(c.status || "entwurf").toLowerCase();
      commentState.currentStructurePayload = (c && c.structure_payload && typeof c.structure_payload === "object") ? c.structure_payload : null;
      try { setPrivateCommentOverlay("draft", c, { refresh: true }); } catch (_) {}
      setCommentDraftMode(c.comment_mode || "change", { resetNewArticleState: false });
      if (String(c.comment_mode || "") === "new_article") {
        commentState.newArticleDraftCreated = true;
        commentState.newArticleCreatedArticleId = c.article_id || null;
        commentState.newArticleCreatedCommentId = c.id || null;
      }

      
      try {
        if (typeof window.klimaggRefreshCommentArticleSelect === "function") {
          window.klimaggRefreshCommentArticleSelect();
        }
        if (articleSelect) articleSelect.value = String(c.article_id || "");
      } catch (_) {}

      // Felder
      try {
        if (anchorInput) {
          const a = c.anchor && typeof c.anchor === "object" ? c.anchor : null;
          anchorInput.value = a && a.label ? String(a.label) : "";
        }
      } catch (_) {}
      if (proposalInput) proposalInput.value = c.proposal_text ? String(c.proposal_text) : "";
      if (explanationInput) explanationInput.value = c.explanation ? String(c.explanation) : "";
      if (sourcesInput) sourcesInput.value = c.sources ? String(c.sources) : "";
      applyRelatedFormFromComment(c);
      setLlmAssisted(!!c.llm_assisted);

      try { resetImpactSliders(); } catch (_) {}
      try { highlightImpactMissing([]); } catch (_) {}
      try {
        if (c.impact_scores && typeof c.impact_scores === "object") {
          try {
            if (proposalInput) { proposalInput.style.minHeight = MINIMD_DEFAULT_EDITOR_MIN_HEIGHT; if (!proposalInput.rows || Number(proposalInput.rows) < 16) proposalInput.rows = 16; }
          } catch (_) {}

          IMPACT_KEYS.forEach((k) => {
            const obj = impactInputs && impactInputs[k] ? impactInputs[k] : null;
            if (!obj || !obj.input) return;
            const v = c.impact_scores[k];
            if (typeof v === "number" && Number.isFinite(v)) {
              obj.input.value = String(v);
              setImpactTouched(k, true);
              setImpactValueUI(k);
            }
          });
        }
      } catch (_) {}

      // Kontextanzeige
      if (contextDisplay) {
        const label = getArticleLabel(c.article_id);
        const v = c.version_id != null ? String(c.version_id) : "";
        contextDisplay.textContent = "Gewählter Artikel: " + label + (v ? " · Version " + v : "");
      }

      // mini-md Baseline (Diff-Preview)
      try {
        if (KlimaGG && KlimaGG.minimd && typeof KlimaGG.minimd.initForComment === "function") {
          KlimaGG.minimd.initForComment({
            articleId: commentState.currentArticleId,
            versionId: commentState.currentVersionId,
            textareaId: "comment-proposal",
            preserveTextarea: true,
          });
        }
      } catch (_) {}

      updateButtonsDisabledState();
      updateCommentProgress(String(c.status || "entwurf").toLowerCase());
      setStatus("Du bearbeitest einen gespeicherten Entwurf (ID " + String(c.id) + ").");
      setCommentPanelMode("editor");
      if (commentState.currentCommentId != null && String(commentState.currentCommentId) === String(commentState.draftPreviewCid || "")) {
        updateDraftPreviewButtonUI();
      }

      try {
        const body = document.body;
        if (body && body.classList.contains("right-collapsed")) body.classList.remove("right-collapsed");
      } catch (_) {}
      try { anchorInput && anchorInput.focus(); } catch (_) {}
    }

    
    function setOverviewStatus(msg) {
      try {
        const el = document.getElementById("comment-overview-status");
        if (el) el.textContent = msg ? String(msg) : "";
      } catch (_) {}
    }

    function renderFilterTabs() {
      const host = document.getElementById("comment-filter-tabs");
      if (!host) return;

      if (!commentState.overviewFilters) {
        commentState.overviewFilters = {
          draft: true,
          published: false,
          archived: false,
          integrated: false,
          deleted: false,
        };
      }

      host.innerHTML = "";
      const defs = [
        ["draft", "Entwurf"],
        ["published", "Veröffentlicht"],
        ["archived", "Archiviert"],
        ["integrated", "Integriert"],
        ["deleted", "Gelöscht"],
      ];

      defs.forEach(([key, label]) => {
        const btn = document.createElement("button");
        btn.type = "button";
        btn.className = "comment-filter-chip" + (commentState.overviewFilters[key] ? " is-on" : "");
        btn.textContent = label;
        btn.addEventListener("click", () => {
          // toggle
          commentState.overviewFilters[key] = !commentState.overviewFilters[key];

          
          const anyOn = Object.keys(commentState.overviewFilters).some((k) => !!commentState.overviewFilters[k]);
          if (!anyOn) commentState.overviewFilters[key] = true;

          
          try {
            renderMyCommentsOverview(commentState.myCommentsCache || []);
          } catch (_) {}
        });
        host.appendChild(btn);
      });
    }

    function getActiveFilterKeys() {
      const f = commentState.overviewFilters || {};
      return Object.keys(f).filter((k) => !!f[k]);
    }

    function classifyStatus(c) {
      // Accept known API field-name variants for compatibility.
      const raw =
        (c && (c.status || c.state || c.lifecycle || c.bucket || c.phase)) ??
        "";
      const s = String(raw).toLowerCase();

      
      if (c && (c.is_deleted === true || c.deleted === true)) return "deleted";
      if (c && (c.is_integrated === true || c.integrated === true)) return "integrated";
      if (c && (c.is_archived === true || c.archived === true)) return "archived";

      // String-Mapping (de/en)
      if (s.includes("draft") || s.includes("entwurf")) return "draft";
      if (s.includes("publish") || s.includes("veröff")) return "published";
      if (s.includes("archiv")) return "archived";
      if (s.includes("integr")) return "integrated";
      if (s.includes("delet") || s.includes("gelösch") || s.includes("geloesch")) return "deleted";

      // Unknown states remain visible by falling back to the draft bucket.
      return "draft";
    }

    function statusBadgeClass(c) {
      const bucket = classifyStatus(c);
      if (!bucket) return "badge";
      // CSS: .badge-draft/.badge-review/.badge-published/.badge-archived/.badge-integrated/.badge-deleted
      return "badge badge-" + String(bucket);
    }

    function filterItems(items) {
      const arr = Array.isArray(items) ? items : [];
      const active = new Set(getActiveFilterKeys());
      if (!active.size) return [];
      return arr.filter((c) => active.has(classifyStatus(c)));
    }

    async function refreshDraftCache() {
      if (!klimaggAuth.user || !klimaggAuth.accessToken) return;
      if (!commentState.myDraftsById) commentState.myDraftsById = {};

      try {
        const drafts = await apiFetchJson(
          "/api/me/comments/drafts?limit=200&offset=0",
          {},
          { authRequired: true }
        );
        if (!Array.isArray(drafts)) return;
        drafts.forEach((c) => {
          if (!c || typeof c !== "object" || c.id == null) return;
          upsertMyDraftCache(c);
        });
        try { refreshRelatedGroupSelect(); } catch (_) {}
      } catch (_) {}
    }

    // ----------------------------------------------------------------------
    function renderMyCommentsOverview(items) {
      renderFilterTabs();
      if (overviewListEl) overviewListEl.innerHTML = "";

      if (!klimaggAuth.user || !klimaggAuth.accessToken) {
        setOverviewStatus("Für deine Kommentar-Übersicht bitte anmelden.");
        return;
      }

      async function _deleteCommentFromOverviewItem(c) {
        const cid = c && c.id != null ? String(c.id) : "";
        if (!cid) return;
        try {
          await apiFetchJson(`/api/comments/${encodeURIComponent(cid)}/delete`, { method: "POST" }, { authRequired: true });
          try {
            const aid = c && c.article_id != null ? String(c.article_id) : "";
            if (aid) removeCommentFromArticleCache(aid, cid);
          } catch (_) {}
          try {
            if (commentState.myCommentsCache && Array.isArray(commentState.myCommentsCache)) {
              commentState.myCommentsCache = commentState.myCommentsCache.filter((x) => String((x && x.id != null) ? x.id : "") !== cid);
            }
          } catch (_) {}
          try {
            if (commentState.myDraftsById) delete commentState.myDraftsById[cid];
          } catch (_) {}
          try {
            const persisted = getPersistedDraftPreviewState();
            if (persisted && persisted.commentId === cid) {
              clearPersistedDraftPreviewState();
            }
          } catch (_) {}
          try { renderMyCommentsOverview(commentState.myCommentsCache || []); } catch (_) {}
          notify("Kommentar gelöscht.", { type: "success" });
        } catch (err) {
          console.error("[KlimaGG] Kommentar löschen fehlgeschlagen:", err);
          notify("Kommentar konnte nicht gelöscht werden.", { type: "error" });
        }
      }

      const active = getActiveFilterKeys();
      if (!active.length) {
        setOverviewStatus("Bitte mindestens einen Filter aktivieren.");
        return;
      }

      const filtered = filterItems(items);
      if (!filtered.length) {
        setOverviewStatus("Keine passenden Kommentare gefunden.");
        return;
      }

      setOverviewStatus("" + filtered.length + " Einträge");

      filtered.forEach((c) => {
        if (!c || typeof c !== "object") return;

        const row = document.createElement("div");
        row.className = "comment-overview-item";

        const header = document.createElement("div");
        header.className = "comment-overview-item-header";

        const left = document.createElement("div");
        left.style.display = "flex";
        left.style.alignItems = "center";
        left.style.gap = "0.45rem";
        left.style.minWidth = "0";

        const badge = document.createElement("span");
        badge.className = statusBadgeClass(c);
        badge.textContent = statusBadgeLabel(c);

        const meta = document.createElement("span");
        meta.className = "klein";
        const date = fmtDate10(String(c.updated_at || c.created_at || ""));
        const art = getArticleLabelFromOverview(c);
        meta.textContent = (date ? (date + " · ") : "") + art;
        try {
          const fullArt =
            (c && (c.article_title || c.articleTitle))
              ? String(c.article_title || c.articleTitle)
              : getArticleLabel(c && c.article_id);
          const vlab = c && c.version_label ? String(c.version_label) : "";
          meta.title = fullArt + (vlab ? (" · " + vlab) : "") + (c && c.id != null ? (" · Kommentar #" + String(c.id)) : "");
        } catch (_) {}
        meta.style.whiteSpace = "nowrap";
        meta.style.overflow = "hidden";
        meta.style.textOverflow = "ellipsis";

        left.appendChild(badge);
        if (getRelatedCommentsPayload(c)) {
          const rb = document.createElement("span");
          rb.className = "badge badge-related";
          rb.textContent = "🔗";
          rb.title = "Zusammenhang mit anderen Kommentaren";
          left.appendChild(rb);
        }
        left.appendChild(meta);

        const actions = document.createElement("div");
        actions.className = "comment-overview-item-actions";

        const bucket = classifyStatus(c);
        if (bucket === "draft") {
          const b = document.createElement("button");
          b.type = "button";
          b.className = "btn btn-ghost btn-sm";
          b.textContent = "Öffnen";
          b.addEventListener("click", () => openDraftFromOverviewItem(c));
          actions.appendChild(b);

          const del = document.createElement("button");
          del.type = "button";
          del.className = "btn btn-ghost btn-sm";
          del.textContent = "Löschen";
          del.addEventListener("click", () => _deleteCommentFromOverviewItem(c));
          actions.appendChild(del);

        } else {
          const b = document.createElement("button");
          b.type = "button";
          b.className = "btn btn-ghost btn-sm";
          b.textContent = "Zum Kommentar";
          b.addEventListener("click", async () => {
            try {
              if (typeof window.klimaggJumpToCommentInArticle === "function") {
                await window.klimaggJumpToCommentInArticle({ articleId: c.article_id, commentId: c.id });
              } else {
                notify("Sprungfunktion nicht verfügbar.", { type: "warn" });
              }
            } catch (_) {}
          });
          actions.appendChild(b);

        }

        header.appendChild(left);
        header.appendChild(actions);
        row.appendChild(header);

        if (overviewListEl) overviewListEl.appendChild(row);
      });
    }


    async function loadMyCommentsOverview({ force = false, silent = false } = {}) {
      if (!klimaggAuth.user || !klimaggAuth.accessToken) {
        commentState.myCommentsCache = null;
        commentState.myCommentsFetchedAt = 0;
        renderMyCommentsOverview([]);
        return;
      }

      const now = Date.now();
      if (!force && commentState.myCommentsCache && (now - (commentState.myCommentsFetchedAt || 0) < 60_000)) {
        renderMyCommentsOverview(commentState.myCommentsCache);
        return;
      }

      if (!silent) {
        setOverviewStatus("Lade …");
      }

      try {
        const items = await apiFetchJson(
          "/api/me/comments/overview?limit=200&offset=0",
          {},
          { authRequired: true }
        );

        commentState.myCommentsCache = Array.isArray(items) ? items : [];
        commentState.myCommentsFetchedAt = Date.now();

        // Refresh the draft cache before opening an item (best effort).
        await refreshDraftCache();

        renderMyCommentsOverview(commentState.myCommentsCache);
        try { await restorePersistedDraftPreviewForCurrentArticle(); } catch (_) {}
      } catch (err) {
        console.error("[KlimaGG] Fehler beim Laden der Kommentar-Übersicht:", err);
        setOverviewStatus("Fehler beim Laden – siehe Konsole.");
      }
    }

    // Toolbar-Events
    if (refreshBtn) {
      refreshBtn.addEventListener("click", async () => {
        await loadMyCommentsOverview({ force: true, silent: false });
      });
    }

    if (newBtn) {
      newBtn.addEventListener("click", async () => {
        if (!ensureLoggedIn()) return;
        await resetForm();
        setCommentPanelMode("editor");
        try { if (articleSelect) articleSelect.focus(); } catch (_) {}
      });
    }

    if (backBtn) {
      backBtn.addEventListener("click", async () => {
        setCommentPanelMode("overview");
        await loadMyCommentsOverview({ force: false, silent: true });
      });
    }

    // Export for the shared refresh cascade (login/logout/comment/review).
    window.klimaggRefreshMyCommentsOverview = loadMyCommentsOverview;
 
    function refreshImpactArt2Link() {
      if (!impactArt2Link) return;
      let href = "/entwurf#artikel-2";
      try {
        const arts = Array.isArray(appState.articles_all) ? appState.articles_all : [];
        const art2 = arts.find((a) => {
          const t = a && typeof a.title === "string" ? a.title.trim().toLowerCase() : "";
          return t.startsWith("artikel 2") || t.includes("artikel 2");
        });
        if (art2) {
          if (art2.slug) href = "/a/" + String(art2.slug);
          else if (art2.id != null) href = "/entwurf#artikel-" + String(art2.id);
        }
      } catch (_) {}
      try { impactArt2Link.setAttribute("href", href); } catch (_) {}
    }

    function getImpactRow(key) {
      if (!impactHost) return null;
      return impactHost.querySelector('.impact-row[data-impact-key="' + String(key) + '"]');
    }

    function setImpactTouched(key, touched) {
      const obj = impactInputs && impactInputs[key] ? impactInputs[key] : null;
      if (!obj || !obj.input) return;
      obj.input.dataset.touched = touched ? "1" : "0";
      const row = getImpactRow(key);
      if (row) {
        row.classList.toggle("impact-untouched", !touched);
        row.classList.toggle("impact-touched", !!touched);
      }
    }

    function setImpactValueUI(key) {
      const obj = impactInputs && impactInputs[key] ? impactInputs[key] : null;
      if (!obj || !obj.input || !obj.valueEl) return;
      const touched = obj.input.dataset.touched === "1";
      if (!touched) {
        obj.valueEl.textContent = "–";
        return;
      }
      const v = Number(obj.input.value);
      obj.valueEl.textContent = Number.isFinite(v) ? String(v) : "–";
    }

    function resetImpactSliders() {
      if (!impactHost) return;
      IMPACT_KEYS.forEach((k) => {
        const obj = impactInputs && impactInputs[k] ? impactInputs[k] : null;
        if (obj && obj.input) {
          try { obj.input.value = "0"; } catch (_) {}
          setImpactTouched(k, false);
          const row = getImpactRow(k);
          if (row) row.classList.remove("impact-missing");
          setImpactValueUI(k);
        }
      });
    }

    function initImpactSliders() {
      if (!impactHost) return;
      IMPACT_KEYS.forEach((k) => {
        const obj = impactInputs && impactInputs[k] ? impactInputs[k] : null;
        if (!obj || !obj.input) return;

        // initial state
        if (obj.input.dataset.touched !== "1") {
          setImpactTouched(k, false);
          setImpactValueUI(k);
        }

        const onChange = () => {
          setImpactTouched(k, true);
          const row = getImpactRow(k);
          if (row) row.classList.remove("impact-missing");
          setImpactValueUI(k);
        };

        obj.input.addEventListener("input", onChange);
        obj.input.addEventListener("change", onChange);

        
        const onClick = (ev) => {
          try {
            if (obj.input.disabled) return;
            const rect = obj.input.getBoundingClientRect();
            if (!rect || !rect.width) return;
            const min = Number(obj.input.min || 0);
            const max = Number(obj.input.max || 0);
            const step = Number(obj.input.step || 1) || 1;
            const x = (ev && typeof ev.clientX === 'number') ? (ev.clientX - rect.left) : null;
            if (x == null) return;
            const ratio = Math.max(0, Math.min(1, x / rect.width));
            let v = min + ratio * (max - min);
            v = Math.round((v - min) / step) * step + min;
            v = Math.max(min, Math.min(max, v));
            obj.input.value = String(v);
            onChange();
          } catch (_) {}
        };
        obj.input.addEventListener("click", onClick);
      });

      refreshImpactArt2Link();
    }

    function collectImpactScores(requireComplete) {
      const scores = {};
      const missing = [];
      let anyTouched = false;

      IMPACT_KEYS.forEach((k) => {
        const obj = impactInputs && impactInputs[k] ? impactInputs[k] : null;
        if (!obj || !obj.input) {
          scores[k] = null;
          if (requireComplete) missing.push(k);
          return;
        }
        const touched = obj.input.dataset.touched === "1";
        if (!touched) {
          scores[k] = null;
          if (requireComplete) missing.push(k);
          return;
        }
        anyTouched = true;
        const v = Number(obj.input.value);
        const ok = Number.isFinite(v) && v >= 0 && v <= 4 && Math.floor(v) === v;
        scores[k] = ok ? v : null;
        if (requireComplete && scores[k] == null) missing.push(k);
      });

      return { anyTouched, scores, missing };
    }

    function highlightImpactMissing(missingKeys) {
      if (!impactHost) return;
      const set = new Set(Array.isArray(missingKeys) ? missingKeys : []);
      IMPACT_KEYS.forEach((k) => {
        const row = getImpactRow(k);
        if (!row) return;
        row.classList.toggle("impact-missing", set.has(k));
      });
    }

    // once per mount
    try { initImpactSliders(); } catch (_) {}

    function setDisabledStable(el, disabled) {
      if (!el) return;
      const next = !!disabled;
      
      
      
      if (el.disabled !== next) el.disabled = next;
    }

    function getSubmitReviewCooldownSeconds() {
      try {
        const meta = document.querySelector('meta[name="kgg-submit-review-cooldown-seconds"]');
        const n = meta ? Number(meta.getAttribute("content") || "0") : 0;
        return Number.isFinite(n) && n > 0 ? Math.floor(n) : 0;
      } catch (_) {
        return 0;
      }
    }

    function formatCooldownSeconds(seconds) {
      const n = Math.max(0, Math.floor(Number(seconds) || 0));
      if (n <= 0) return "0 Sekunden";
      const minutes = Math.floor(n / 60);
      const rest = n % 60;
      if (minutes && rest) return minutes + " Minuten " + rest + " Sekunden";
      if (minutes) return minutes + " Minuten";
      return rest + " Sekunden";
    }

    function normalizeCommentDraftText(value) {
      return String(value == null ? "" : value).replace(/\r\n/g, "\n").trim();
    }

    function currentDraftHasUnsavedChanges() {
      const id = commentState.currentCommentId != null ? String(commentState.currentCommentId) : "";
      if (!id) return true;
      const saved = commentState.myDraftsById && commentState.myDraftsById[id] ? commentState.myDraftsById[id] : null;
      if (!saved) return true;
      if (proposalInput && normalizeCommentDraftText(proposalInput.value) !== normalizeCommentDraftText(saved.proposal_text)) return true;
      if (explanationInput && normalizeCommentDraftText(explanationInput.value) !== normalizeCommentDraftText(saved.explanation)) return true;
      if (sourcesInput && normalizeCommentDraftText(sourcesInput.value) !== normalizeCommentDraftText(saved.sources)) return true;
      try {
        const imp = collectImpactScores(false);
        if (imp && imp.anyTouched) {
          const oldScores = saved.impact_scores && typeof saved.impact_scores === "object" ? saved.impact_scores : {};
          for (const k of IMPACT_KEYS) {
            if (Number((imp.scores || {})[k]) !== Number(oldScores[k])) return true;
          }
        }
      } catch (_) {}
      return false;
    }

    function updateButtonsDisabledState() {
      const canInteract = !!(klimaggAuth.user && klimaggAuth.accessToken);
      const hasArticles = Array.isArray(appState.articles_all) && appState.articles_all.length > 0;
      try { applyCommentModeTrustGate(); } catch (_) {}
      const mode = getCommentDraftMode();
      const isNewArticle = mode === "new_article";
      const pickerEnabled = canInteract && hasArticles && !isNewArticle;


      
      if (refreshBtn) refreshBtn.disabled = !canInteract;
      if (newBtn) newBtn.disabled = !(canInteract && hasArticles);
      setDisabledStable(pickNearestBtn, !pickerEnabled);
      setDisabledStable(articleSelect, !pickerEnabled);
      setDisabledStable(commentModeSelect, !canInteract);

      const hasArticle = !!commentState.currentArticleId;
      const canSubmit = canInteract && (isNewArticle || hasArticle);
      if (saveDraftBtn) saveDraftBtn.disabled = !canSubmit;
      if (submitBtn) submitBtn.disabled = !canSubmit;

      
      if (previewBtn) previewBtn.disabled = !(canInteract && hasArticle);
      updateDraftPreviewButtonUI();
      if (deleteDraftBtn) deleteDraftBtn.disabled = !canInteract;

      
      const canEditImpact = canInteract && (isNewArticle || hasArticle);
      if (impactHost) {
        IMPACT_KEYS.forEach((k) => {
          const obj = impactInputs && impactInputs[k] ? impactInputs[k] : null;
          if (obj && obj.input) obj.input.disabled = !canEditImpact;
        });
      }
    }

    
    window.klimaggUpdateCommentPanelButtons = updateButtonsDisabledState;

    function setStatus(msg) {
      if (statusText) statusText.textContent = msg;
    }

    function getCommentDraftMode() {
      const raw = commentModeSelect ? String(commentModeSelect.value || "") : "";
      if (raw === "new_article" || raw === "delete_article") return raw;
      return COMMENT_MODE_DEFAULT;
    }

    function setCommentDraftMode(mode, opts = {}) {
      const m = (mode === "new_article" || mode === "delete_article") ? mode : COMMENT_MODE_DEFAULT;
      if (commentModeSelect) commentModeSelect.value = m;
      commentState.commentDraftMode = m;
      if (opts && opts.resetNewArticleState !== false && m !== "new_article") {
        commentState.newArticleDraftCreated = false;
        commentState.newArticleCreatedArticleId = null;
        commentState.newArticleCreatedCommentId = null;
      }
      applyCommentDraftModeUI();
      updateButtonsDisabledState();
    }

    function getCurrentUserTrustLevel() {
      try {
        const u = klimaggAuth && klimaggAuth.user ? klimaggAuth.user : null;
        const v = u && u.trust_level != null ? Number(u.trust_level) : 0;
        return Number.isFinite(v) ? v : 0;
      } catch (_) {
        return 0;
      }
    }

    function applyCommentModeTrustGate() {
      if (!commentModeSelect) return;
      if (!(klimaggAuth.user && klimaggAuth.accessToken)) return;
      const trust = getCurrentUserTrustLevel();
      const modes = [
        { value: "change", label: "Änderung an bestehendem Artikel", required: 0 },
        { value: "new_article", label: "Neuen Artikel vorschlagen", required: getCommentModeRequiredTrust("new_article") },
        { value: "delete_article", label: "Artikel-Löschung beantragen", required: getCommentModeRequiredTrust("delete_article") },
      ];
      const currentSignature = Array.from(commentModeSelect.options || []).map((opt) => String(opt.value || "")).join("|");
      const fullSignature = modes.map((m) => String(m.value)).join("|");
      const maxRequired = Math.max(...modes.map((m) => Number(m.required || 0)));
      if (trust >= maxRequired && currentSignature === fullSignature) {
        Array.from(commentModeSelect.options || []).forEach((opt) => {
          if (opt.disabled) opt.disabled = false;
          if (opt.title) opt.title = "";
        });
        return;
      }
      const allowed = modes.filter((m) => trust >= Number(m.required || 0));
      const allowedValues = new Set(allowed.map((m) => String(m.value)));
      const currentValue = String(commentModeSelect.value || COMMENT_MODE_DEFAULT);
      const nextSignature = allowed.map((m) => String(m.value)).join("|");

      if (currentSignature !== nextSignature) {
        if (document.activeElement === commentModeSelect) return;
        commentModeSelect.replaceChildren();
        allowed.forEach((m) => {
          const opt = document.createElement("option");
          opt.value = String(m.value);
          opt.textContent = String(m.label);
          commentModeSelect.appendChild(opt);
        });
      }

      if (allowedValues.has(currentValue)) {
        commentModeSelect.value = currentValue;
      } else {
        commentModeSelect.value = COMMENT_MODE_DEFAULT;
        commentState.commentDraftMode = COMMENT_MODE_DEFAULT;
      }

      if (commentModeHint && trust < Math.min(
        getCommentModeRequiredTrust("new_article") || Number.POSITIVE_INFINITY,
        getCommentModeRequiredTrust("delete_article") || Number.POSITIVE_INFINITY
      )) {
        const minTrust = Math.min(
          getCommentModeRequiredTrust("new_article") || Number.POSITIVE_INFINITY,
          getCommentModeRequiredTrust("delete_article") || Number.POSITIVE_INFINITY
        );
        if (Number.isFinite(minTrust)) {
          commentModeHint.textContent = `Weitere Kommentar-Modi werden ab Trust-Level ${minTrust} sichtbar. Aktuell: ${trust}.`;
        }
      }
    }

    function getCommentModeRequiredTrust(mode) {
      if (!commentModeSelect) return 0;
      const attr = mode === "new_article"
        ? commentModeSelect.dataset.newArticleMinTrust
        : (mode === "delete_article" ? commentModeSelect.dataset.deleteArticleMinTrust : "0");
      const v = Number(attr);
      return Number.isFinite(v) ? v : 0;
    }

    function getCommentMetaRequiredTrust() {
      if (!commentModeSelect) return 0;
      const v = Number(commentModeSelect.dataset.metaMinTrust);
      return Number.isFinite(v) ? v : 0;
    }

    function canCurrentUserEditCommentMeta() {
      const u = klimaggAuth && klimaggAuth.user ? klimaggAuth.user : null;
      return !!(u && u.is_admin) || getCurrentUserTrustLevel() >= getCommentMetaRequiredTrust();
    }

    function setLlmStatus(msg) {
      if (llmStatusEl) llmStatusEl.textContent = msg || "";
    }

    function getLlmModeConfig(mode) {
      const modes = llmContextStatusCache && llmContextStatusCache.modes && typeof llmContextStatusCache.modes === "object"
        ? llmContextStatusCache.modes
        : {};
      return modes[String(mode || LLM_MODE_DEFAULT)] || null;
    }

    function isLlmAvailableForMode(mode) {
      const cfg = getLlmModeConfig(mode);
      return !!(cfg && cfg.available === true);
    }

    async function refreshLlmContextStatus() {
      try {
        const data = await apiFetchJson("/api/llm/context/status", { method: "GET" }, { authRequired: false });
        llmContextStatusCache = data && typeof data === "object" ? data : { modes: {} };
        llmContextStatusLoaded = true;
      } catch (err) {
        console.warn("[KlimaGG] LLM-Status konnte nicht geladen werden:", err);
        llmContextStatusCache = { modes: {} };
        llmContextStatusLoaded = true;
        setLlmStatus("LLM-Unterstützung ist aktuell nicht verfügbar.");
      }
      applyCommentDraftModeUI();
    }

    function normalizeSourceLines(text) {
      return String(text || "").replace(/\r\n/g, "\n").replace(/\r/g, "\n").split("\n");
    }

    function syncLlmAssistedSourceLine() {
      if (!sourcesInput || !llmAssistedCheckbox) return;
      const checked = !!llmAssistedCheckbox.checked;
      let lines = normalizeSourceLines(sourcesInput.value);
      lines = lines.filter((line) => String(line || "").trim() !== LLM_ASSISTED_SOURCE_LINE);
      if (checked) lines.push(LLM_ASSISTED_SOURCE_LINE);
      sourcesInput.value = lines.join("\n").replace(/^\n+/, "").replace(/\n+$/, "");
    }

    function setLlmAssisted(value, { syncSources = true } = {}) {
      if (llmAssistedCheckbox) llmAssistedCheckbox.checked = !!value;
      if (syncSources) syncLlmAssistedSourceLine();
    }

    function getLlmAssistedValue() {
      return !!(llmAssistedCheckbox && llmAssistedCheckbox.checked);
    }

    function currentLlmInstructionMode() {
      const mode = getCommentDraftMode();
      return mode || LLM_MODE_DEFAULT;
    }

    function updateLlmSupportUIForMode(mode) {
      if (!llmSupportEl) return;
      const loaded = !!llmContextStatusLoaded;
      const available = loaded && isLlmAvailableForMode(mode);
      llmSupportEl.hidden = !available;
      if (!available) {
        if (llmSummaryEl) {
          llmSummaryEl.textContent = loaded ? "Für diesen Kommentar-Modus nicht verfügbar" : "Status wird geladen";
        }
        setLlmStatus(loaded ? "" : "LLM-Status wird geladen …");
        return;
      }
      const cfg = getLlmModeConfig(mode) || {};
      if (llmSummaryEl) llmSummaryEl.textContent = cfg.ui_label || cfg.instructions_label || "Externe Hilfe für Kommentar-Entwürfe";
      if (llmDownloadInstructionsBtn) llmDownloadInstructionsBtn.disabled = false;
      if (llmDownloadLawBtn) llmDownloadLawBtn.disabled = false;
      if (llmDownloadArticleBtn) llmDownloadArticleBtn.disabled = false;
      if (llmCopyPromptBtn) llmCopyPromptBtn.disabled = false;
      if (llmAssistedCheckbox) llmAssistedCheckbox.disabled = false;
      setLlmStatus("");
    }

    function selectedArticleIdForLlm() {
      if (commentState.currentArticleId) return commentState.currentArticleId;
      if (articleSelect && articleSelect.value) return articleSelect.value;
      return null;
    }

    function downloadLlmContext(kind) {
      const mode = currentLlmInstructionMode();
      if (kind === "law") {
        window.location.href = "/api/llm/context/law";
        return;
      }
      if (kind === "instructions") {
        window.location.href = "/api/llm/context/instructions?mode=" + encodeURIComponent(mode);
        return;
      }
      if (kind === "article") {
        const aid = selectedArticleIdForLlm();
        if (!aid) {
          setLlmStatus("Bitte zuerst einen Artikel auswählen.");
          notify("Bitte zuerst einen Artikel auswählen.", { type: "warn" });
          return;
        }
        window.location.href = "/api/llm/context/article/" + encodeURIComponent(String(aid));
      }
    }

    function buildLlmPromptTemplate() {
      const mode = currentLlmInstructionMode();
      const articleId = selectedArticleIdForLlm();
      const articleLabel = articleId ? getArticleLabel(articleId) : "(noch kein Artikel gewählt)";
      const explanation = explanationInput ? String(explanationInput.value || "").trim() : "";
      const sources = sourcesInput ? normalizeSourceLines(sourcesInput.value).filter((line) => String(line || "").trim() !== LLM_ASSISTED_SOURCE_LINE).join("\n").trim() : "";
      return loadLlmPromptTemplate(mode).then((template) => fillLlmPromptTemplate(template, {
        mode,
        articleLabel,
        userTask: explanation || "[Bitte hier Zielrichtung, Problem oder gewünschte Verbesserung beschreiben.]",
        sources: sources || "[Optional: Quellen, Links, Stichpunkte oder Randbedingungen.]",
      }));
    }

    async function loadLlmPromptTemplate(mode) {
      const modeKey = String(mode || LLM_MODE_DEFAULT);
      const url = "/api/llm/context/instructions?mode=" + encodeURIComponent(modeKey);
      const resp = await fetch(url, {
        method: "GET",
        credentials: "same-origin",
        cache: "no-store",
        headers: { Accept: "text/markdown,text/plain,*/*" },
      });
      if (!resp.ok) {
        throw new Error("LLM-Anweisungen nicht verfügbar: HTTP " + resp.status);
      }
      const markdown = await resp.text();
      const template = extractLlmPromptTemplate(markdown);
      if (!template) {
        throw new Error("Keine LLM-Mustervorlage in der Anweisungsdatei gefunden.");
      }
      return template;
    }

    function extractLlmPromptTemplate(markdown) {
      const text = String(markdown || "");
      const start = text.indexOf(LLM_PROMPT_TEMPLATE_START);
      const end = text.indexOf(LLM_PROMPT_TEMPLATE_END);
      if (start < 0 || end < 0 || end <= start) return "";
      return text.slice(start + LLM_PROMPT_TEMPLATE_START.length, end).trim();
    }

    function fillLlmPromptTemplate(template, values) {
      const replacements = {
        "{{COMMENT_MODE}}": values && values.mode != null ? String(values.mode) : "",
        "{{ARTICLE_LABEL}}": values && values.articleLabel != null ? String(values.articleLabel) : "",
        "{{USER_TASK}}": values && values.userTask != null ? String(values.userTask) : "",
        "{{SOURCES}}": values && values.sources != null ? String(values.sources) : "",
      };
      let out = String(template || "");
      Object.keys(replacements).forEach((key) => {
        out = out.split(key).join(replacements[key]);
      });
      return out.trim();
    }

    async function copyTextToClipboard(text) {
      const value = String(text || "");
      if (navigator.clipboard && typeof navigator.clipboard.writeText === "function") {
        await navigator.clipboard.writeText(value);
        return;
      }
      const ta = document.createElement("textarea");
      ta.value = value;
      ta.setAttribute("readonly", "");
      ta.style.position = "fixed";
      ta.style.left = "-9999px";
      document.body.appendChild(ta);
      ta.select();
      document.execCommand("copy");
      ta.remove();
    }

    async function copyLlmPromptTemplate() {
      try {
        await copyTextToClipboard(await buildLlmPromptTemplate());
        setLlmAssisted(true);
        setLlmStatus("Mustervorlage kopiert. Transparenzhinweis wurde gesetzt.");
        notify("LLM-Mustervorlage kopiert.", { type: "success" });
      } catch (err) {
        console.warn("[KlimaGG] LLM-Prompt konnte nicht kopiert werden:", err);
        setLlmStatus("Kopieren fehlgeschlagen: Mustervorlage in den LLM-Anweisungen fehlt oder ist nicht verfügbar.");
        notify("Kopieren fehlgeschlagen.", { type: "warn" });
      }
    }

    function applyCommentDraftModeUI() {
      const mode = getCommentDraftMode();
      const isNewArticle = mode === "new_article";
      const isDeleteArticle = mode === "delete_article";

      if (articlePickerEl) articlePickerEl.hidden = isNewArticle;
      if (newArticleNote) newArticleNote.classList.toggle("hidden", !isNewArticle);
      if (deleteArticleNote) deleteArticleNote.classList.toggle("hidden", !isDeleteArticle);
      if (proposalField) proposalField.hidden = false;
      if (proposalInput) {
        proposalInput.disabled = isDeleteArticle;
        if (isDeleteArticle) {
          proposalInput.value = "Antrag zur Artikel-Löschung — der Voll-Löschpatch wird automatisch erzeugt.";
          proposalInput.placeholder = "Der Löschpatch wird automatisch erzeugt.";
        } else {
          proposalInput.placeholder = isNewArticle ? "MiniMD-Maske für den neuen Artikel …" : "Was soll sich ändern? …";
        }
      }
      updateLlmSupportUIForMode(mode);
      if (proposalLabel) {
        proposalLabel.textContent = isNewArticle ? "Neuer Artikel (MiniMD-Maske)" : (isDeleteArticle ? "Automatischer Löschpatch" : "Änderungsvorschlag");
      }
      if (proposalInput && isNewArticle && !String(proposalInput.value || "").trim() && !commentState.currentCommentId) {
        proposalInput.value = NEW_ARTICLE_TEMPLATE_MINIMD;
        try { proposalInput.rows = Math.max(Number(proposalInput.rows || 0), 18); } catch (_) {}
      }
      if (isNewArticle && KlimaGG && KlimaGG.minimd && typeof KlimaGG.minimd.setBaseMiniMdForComment === "function") {
        try {
          KlimaGG.minimd.setBaseMiniMdForComment(NEW_ARTICLE_BASELINE_MINIMD, {
            key: "new_article_template",
            textareaId: "comment-proposal",
            preserveTextarea: true,
          });
        } catch (_) {}
      }
      if (commentModeHint) {
        if (isNewArticle) {
          commentModeHint.textContent = "Neuer Artikel: Position und Artikeldaten werden im meta-Block festgelegt; angelegt wird erst beim ersten Speichern/Vorschau.";
        } else if (isDeleteArticle) {
          commentModeHint.textContent = "Artikel-Löschung: Wähle den bestehenden Artikel; der Löschpatch wird automatisch erzeugt.";
        } else {
          commentModeHint.textContent = "Standard: Änderung an einem bestehenden Artikel.";
        }
      }
      if (contextDisplay && isNewArticle && !commentState.currentArticleId) {
        contextDisplay.textContent = "Neuer Artikel: noch nicht angelegt";
      }
    }

    function parseMiniMdMetaForNewArticle(minimd) {
      const split = kggSplitMiniMdBlocks(String(minimd || ""));
      const blocks = split && split.blocks ? split.blocks : {};
      const metaText = String(blocks.meta || "");
      const meta = {};
      metaText.split(/\r?\n/).forEach((line) => {
        const m = String(line || "").trim().match(/^\$\s*([^$:\n]+):\s*\$\s*(.*)$/);
        if (!m) return;
        meta[String(m[1] || "").trim()] = String(m[2] || "").trim();
      });
      return meta;
    }

    function isNewArticlePlaceholderValue(value) {
      return /<\s*(bitte ersetzen|optional)\b/i.test(String(value || ""));
    }

    function sanitizeNewArticleMiniMdForSubmit(minimd) {
      let out = String(minimd || "");
      out = out.replace(/^\$\s*Artikel-Kurztitel:\s*\$\s*<optional:[^\n>]*>\s*$/mi, "$ Artikel-Kurztitel: $ ");
      return out;
    }

    function validateNewArticleMiniMdForSave() {
      const text = proposalInput ? String(proposalInput.value || "") : "";
      const meta = parseMiniMdMetaForNewArticle(text);
      const required = ["Artikel-Kennung", "Artikel-Titel", NEW_ARTICLE_INSERT_AFTER_LABEL];
      const missing = required
        .filter((label) => !String(meta[label] || "").trim() || isNewArticlePlaceholderValue(meta[label]));
      if (missing.length) {
        setStatus("Neuer Artikel unvollständig: " + missing.join(", "));
        notify("Bitte ersetze die Platzhalter in den Pflicht-Meta-Feldern.", { type: "warn" });
        try { proposalInput && proposalInput.focus(); } catch (_) {}
        return false;
      }
      return true;
    }

    function upsertArticleIntoClientState(article) {
      if (!article || article.id == null) return;
      if (!Array.isArray(appState.articles_all)) appState.articles_all = [];
      const id = String(article.id);
      const idx = appState.articles_all.findIndex((a) => a && String(a.id) === id);
      const isNewClientArticle = idx < 0;
      const sortOrder = Number(article.sort_order);
      if (isNewClientArticle && Number.isFinite(sortOrder)) {
        appState.articles_all.forEach((a) => {
          if (!a || String(a.id) === id) return;
          const s = Number(a.sort_order);
          if (Number.isFinite(s) && s >= sortOrder) {
            a.sort_order = s + 1;
          }
        });
      }
      if (idx >= 0) appState.articles_all[idx] = article;
      else appState.articles_all.push(article);
      appState.articles_all.sort((a, b) => {
        const sa = _articleSortKeyForClient(a);
        const sb = _articleSortKeyForClient(b);
        if (sa !== sb) return sa - sb;
        return Number(a && a.id || 0) - Number(b && b.id || 0);
      });
    }

    function scrollToArticleCenter(articleId) {
      try {
        const el = document.querySelector('#articles-container article[data-article-id="' + CSS.escape(String(articleId)) + '"]');
        if (el && typeof el.scrollIntoView === "function") {
          el.scrollIntoView({ behavior: "smooth", block: "center" });
        }
      } catch (_) {}
    }

    const previewDebugState = {
      version: KLIMAGG_WEB_JS_VERSION,
      articleId: null,
      previewCid: null,
      fromBackendSave: false,
      semanticEventsCount: 0,
      semanticEventsOk: false,
      cardInsertOk: false,
      inlineWriteOk: false,
      cleanupDone: false,
      canonicalBackend: false,
      attempted: false,
      lastErrorCode: "",
      lastErrorMessage: "",
    };

    function setPreviewDebug(patch) {
      try {
        if (!patch || typeof patch !== "object") return;
        Object.keys(patch).forEach((k) => { previewDebugState[k] = patch[k]; });
      } catch (_) {}
    }

    function getPersistedDraftPreviewState() {
      try {
        const raw = localStorage.getItem(DRAFT_PREVIEW_STATE_KEY);
        if (!raw) return null;
        const obj = JSON.parse(raw);
        if (!obj || typeof obj !== "object") return null;
        const aid = obj.articleId != null ? String(obj.articleId) : "";
        const cid = obj.commentId != null ? String(obj.commentId) : "";
        const vid = obj.versionId != null ? String(obj.versionId) : "";
        if (!aid || !cid) return null;
        return { articleId: aid, commentId: cid, versionId: vid || null };
      } catch (_) {
        return null;
      }
    }

    function setPersistedDraftPreviewState(articleId, commentId, versionId) {
      try {
        const aid = articleId != null ? String(articleId) : "";
        const cid = commentId != null ? String(commentId) : "";
        const vid = versionId != null ? String(versionId) : "";
        if (!aid || !cid) return;
        localStorage.setItem(
          DRAFT_PREVIEW_STATE_KEY,
          JSON.stringify({
            articleId: aid,
            commentId: cid,
            versionId: vid || null,
          })
        );
      } catch (_) {}
    }

    function clearPersistedDraftPreviewState() {
      try { localStorage.removeItem(DRAFT_PREVIEW_STATE_KEY); } catch (_) {}
    }

    function clearPersistedDraftPreviewStateIfMatches(articleId, commentId) {
      try {
        const persisted = getPersistedDraftPreviewState();
        if (!persisted) return false;
        const aid = articleId != null ? String(articleId) : "";
        const cid = commentId != null ? String(commentId) : "";
        if (aid && persisted.articleId !== aid) return false;
        if (cid && persisted.commentId !== cid) return false;
        clearPersistedDraftPreviewState();
        return true;
      } catch (_) {}
      return false;
    }

    function isDraftPreviewActiveForCurrentComment() {
      const aid = commentState.currentArticleId != null ? String(commentState.currentArticleId) : "";
      const cid = commentState.draftPreviewCid != null ? String(commentState.draftPreviewCid) : "";
      const curId = commentState.currentCommentId != null ? String(commentState.currentCommentId) : "";
      const persisted = getPersistedDraftPreviewState();
      if (aid && cid && curId && aid === String(commentState.currentArticleId) && cid === curId) return true;
      if (!persisted) return false;
      return !!(aid && curId && persisted.articleId === aid && persisted.commentId === curId);
    }

    function updateDraftPreviewButtonUI() {
      if (!previewBtn) return;
      const active = isDraftPreviewActiveForCurrentComment();
      previewBtn.dataset.active = active ? "true" : "false";
      previewBtn.textContent = active ? "Vorschau im Artikel aus" : "Vorschau im Artikel";
      if (active) {
        previewBtn.style.cssText += ";" + DRAFT_PREVIEW_ACTIVE_STYLE;
      } else {
        previewBtn.style.background = "";
        previewBtn.style.color = "";
        previewBtn.style.borderColor = "";
      }
    }

    async function restorePersistedDraftPreviewForCurrentArticle() {
      const persisted = getPersistedDraftPreviewState();
      if (!persisted) return false;
      if (!ensureLoggedIn()) return false;
      const aid = commentState.currentArticleId != null ? String(commentState.currentArticleId) : "";
      if (!aid || persisted.articleId !== aid) return false;
      try {
        const cid = String(persisted.commentId);
        let draftComment = null;
        if (commentState.myDraftsById && commentState.myDraftsById[cid]) {
          draftComment = commentState.myDraftsById[cid];
        }
        if (!draftComment) {
          draftComment = await apiFetchJson(`/api/me/comments/${encodeURIComponent(cid)}`, {}, { authRequired: true });
        }
        if (!draftComment || typeof draftComment !== "object" || draftComment.id == null) {
          clearPersistedDraftPreviewStateIfMatches(aid, cid);
          try { removeInlineDiffIdsFromArticleState(aid, [cid], commentState.currentVersionId || undefined); } catch (_) {}
          return false;
        }
        if (String(draftComment.article_id || "") !== aid) {
          clearPersistedDraftPreviewStateIfMatches(aid, cid);
          try { removeInlineDiffIdsFromArticleState(aid, [cid], commentState.currentVersionId || undefined); } catch (_) {}
          return false;
        }
        const activated = await activateDraftInlinePreview(draftComment, "restorePersistedDraftPreview", commentState.currentVersionId || undefined);
        if (!activated) {
          clearPersistedDraftPreviewStateIfMatches(aid, cid);
          try { removeInlineDiffIdsFromArticleState(aid, [cid], commentState.currentVersionId || undefined); } catch (_) {}
          return false;
        }
        if (commentState.currentCommentId != null && String(commentState.currentCommentId) === String(draftComment.id)) {
          commentState.currentCommentStatus = String(draftComment.status || commentState.currentCommentStatus || "entwurf").toLowerCase();
        }
        updateDraftPreviewButtonUI();
        return true;
      } catch (err) {
        try {
          clearPersistedDraftPreviewStateIfMatches(aid, persisted.commentId);
          removeInlineDiffIdsFromArticleState(aid, [persisted.commentId], commentState.currentVersionId || undefined);
          commentState.draftPreviewCid = null;
          updateDraftPreviewButtonUI();
        } catch (_) {}
        console.warn("[KlimaGG] Persistierte Draft-Vorschau konnte nicht wiederhergestellt werden:", err);
      }
      return false;
    }

    function removeCommentFromArticleCache(articleId, commentId) {
      const aid = String(articleId || "").trim();
      const cid = String(commentId || "").trim();
      if (!aid || !cid) return;
      try {
        window.__klimaggCommentsCacheByArticleId = window.__klimaggCommentsCacheByArticleId || {};
        const cache = window.__klimaggCommentsCacheByArticleId;
        const cur = Array.isArray(cache[aid]) ? cache[aid].slice() : [];
        cache[aid] = cur.filter((c) => String((c && c.id != null) ? c.id : "") !== cid);
        const box = document.querySelector('.article-comments[data-article-id="' + cssEscape(aid) + '"]');
        if (box) box.hidden = false;
        const listEl = box ? box.querySelector(".article-comments-list") : null;
        if (listEl) {
          listEl.dataset.loaded = "true";
          const aidNum = Number(aid);
          renderCommentsIntoList(Number.isFinite(aidNum) ? aidNum : aid, cache[aid], listEl);
        }
      } catch (_) {}
    }

    function cacheCommentForInlineDiff(commentObj) {
      const c = (commentObj && typeof commentObj === "object") ? commentObj : null;
      if (!c || c.id == null) return null;
      try {
        window.__klimaggCommentCacheById = window.__klimaggCommentCacheById || {};
        window.__klimaggCommentCacheById[String(c.id)] = c;
      } catch (_) {}
      return c;
    }

    async function activateDraftInlinePreview(draftComment, source, fallbackVersionId) {
      await deactivateVersionLayerForCommentAction();
      const c = cacheCommentForInlineDiff(draftComment);
      if (!c || c.id == null) return false;
      const aid = String(c.article_id || commentState.currentArticleId || "").trim();
      if (!aid) return false;
      const cid = String(c.id);
      const previewVid = c.version_id != null ? c.version_id : (fallbackVersionId != null ? fallbackVersionId : (commentState.currentVersionId || undefined));
      const inlineApi = getInlineDiffApiOrThrow();
      const toggled = await inlineApi.toggleDraft(aid, previewVid, cid, true, { source: String(source || "activateDraftInlinePreview") });
      let stillActive = false;
      try {
        const activeIds = (typeof getActiveInlineDiffIds === "function")
          ? getActiveInlineDiffIds(aid, previewVid).map((x) => String(x))
          : [];
        stillActive = activeIds.includes(String(cid));
      } catch (_) {
        stillActive = !!toggled;
      }
      if (!toggled || !stillActive) {
        try { removeInlineDiffIdsFromArticleState(aid, [cid], previewVid); } catch (_) {}
        try { clearPersistedDraftPreviewStateIfMatches(aid, cid); } catch (_) {}
        return false;
      }
      commentState.draftPreviewCid = cid;
      setPersistedDraftPreviewState(aid, cid, previewVid || "");
      try {
        upsertCommentIntoArticleCache(aid, c);
      } catch (_) {}
      try { setPrivateCommentOverlay("draft", c, { refresh: true }); } catch (_) {}
      try { await ensurePrivateOverlayCommentRendered(aid, cid, { scroll: false }); } catch (_) {}
      return true;
    }

    async function deactivateDraftInlinePreview(articleId, versionId, commentId, source, opts) {
      const aid = String(articleId || "").trim();
      const cid = String(commentId || "").trim();
      const o = (opts && typeof opts === "object") ? opts : {};
      if (!aid || !cid) return false;
      try {
        const inlineApi = getInlineDiffApiOrThrow();
        await inlineApi.toggleDraft(aid, versionId, cid, false, { source: String(source || "deactivateDraftInlinePreview") });
      } catch (_) {}
      if (o.removeCommentCache !== false) {
        try { removeCommentFromArticleCache(aid, cid); } catch (_) {}
      }
      if (o.removeGlobalCache !== false) {
        try {
          if (window.__klimaggCommentCacheById) delete window.__klimaggCommentCacheById[String(cid)];
        } catch (_) {}
      }
      if (o.clearPersisted !== false) {
        try { clearPersistedDraftPreviewState(); } catch (_) {}
      }
      try { clearPrivateCommentOverlay("draft"); } catch (_) {}
      return true;
    }

    function upsertCommentIntoArticleCache(articleId, commentObj) {
      const aid = String(articleId || "").trim();
      const cid = String(commentObj && commentObj.id != null ? commentObj.id : "").trim();
      if (!aid || !cid || !commentObj || typeof commentObj !== "object") return;
      try {
        window.__klimaggCommentsCacheByArticleId = window.__klimaggCommentsCacheByArticleId || {};
        const cache = window.__klimaggCommentsCacheByArticleId;
        const cur = Array.isArray(cache[aid]) ? cache[aid].slice() : [];
        const next = cur.filter((c) => String((c && c.id != null) ? c.id : "") !== cid);
        next.unshift(commentObj);
        cache[aid] = next;
        const box = document.querySelector('.article-comments[data-article-id="' + cssEscape(aid) + '"]');
        const listEl = box ? box.querySelector(".article-comments-list") : null;
        if (listEl && listEl.dataset.loaded === "true") {
          const aidNum = Number(aid);
          renderCommentsIntoList(Number.isFinite(aidNum) ? aidNum : aid, cache[aid], listEl);
        }
      } catch (_) {}
    }

    async function invalidateDraftPreview({ clearInArticle = true } = {}) {
      if (!clearInArticle) return;
      const aid = commentState.currentArticleId;
      if (!aid) return;
      try {
        const cid = commentState.draftPreviewCid;
        if (cid) {
          await deactivateDraftInlinePreview(
            aid,
            commentState.currentVersionId,
            cid,
            "invalidateDraftPreview",
            {
              removeCommentCache: true,
              removeGlobalCache: true,
              clearPersisted: false,
            }
          );
        }
      } catch (_) {}

      // Restore previous Master state after closing draft preview (best-effort)
      try {
        const prev = commentState.draftPreviewPrevMaster;
        if (prev) {
          setCommentOverlayModeAndSync(prev);
        }
      } catch (_) {}
      commentState.draftPreviewPrevMaster = null;
      commentState.draftPreviewCid = null;
      clearPersistedDraftPreviewState();
      setPreviewDebug({
        articleId: aid,
        previewCid: null,
        inlineWriteOk: false,
        cardInsertOk: false,
        cleanupDone: true,
        canonicalBackend: false,
      });
    }

    function ensureLoggedIn() {
      if (!klimaggAuth.user || !klimaggAuth.accessToken) {
        setStatus("Für Kommentarentwürfe musst du angemeldet sein. Bitte melde dich zuerst an.");
        notify("Bitte melde dich an, um Kommentare zu speichern.", { type: "warn" });
        if (typeof startLoginFlow === "function") {
          startLoginFlow();
        }
        return false;
      }
      return true;
    }

    function updateCommentProgress(stage) {
      const container = formSection.parentElement;
      if (!container) return;
      const steps = container.querySelectorAll(".comment-progress-step");
      steps.forEach((step) => {
        step.classList.remove("active");
        step.classList.remove("future");
      });
      steps.forEach((step) => {
        const label = (step.textContent || "").toLowerCase();
        if (label.includes("entwurf") && stage === "entwurf") {
          step.classList.add("active");
        } else if (label.includes("review") && stage === "review") {
          step.classList.add("active");
        } else if (
          label.includes("veröffentlicht") &&
          stage === "veröffentlicht"
        ) {
          step.classList.add("active");
        } else {
          step.classList.add("future");
        }
      });
    }

    async function resetForm() {
      // Reset draft preview state, including the article inline diff.
      try {
        const prevAid = commentState.currentArticleId;
        if (prevAid) {
          try {
            const inlineApi = getInlineDiffApiOrThrow();
            await inlineApi.clear(prevAid);
            await inlineApi.refresh(prevAid, undefined, { source: "resetForm" });
          } catch (_) {}
        }
      } catch (_) {}
      commentState.currentArticleId = null;
      commentState.currentVersionId = null;
      commentState.currentCommentId = null;
      commentState.currentCommentStatus = "entwurf";
      commentState.currentStructurePayload = null;
      commentState.newArticleDraftCreated = false;
      commentState.newArticleCreatedArticleId = null;
      commentState.newArticleCreatedCommentId = null;
      setCommentDraftMode("change");
      if (anchorInput) anchorInput.value = "";
      if (proposalInput) proposalInput.value = "";
      if (explanationInput) explanationInput.value = "";
      if (sourcesInput) sourcesInput.value = "";
      clearRelatedForm();
      setLlmAssisted(false);
      try { resetImpactSliders(); } catch (_) {}
      try { highlightImpactMissing([]); } catch (_) {}
      if (cc0Checkbox) cc0Checkbox.checked = false;
      if (rulesCheckbox) rulesCheckbox.checked = false;
      if (privacyCheckbox) privacyCheckbox.checked = false;
      if (rulesCheckbox) rulesCheckbox.checked = false;
      if (contextDisplay) {
        contextDisplay.textContent = "Gewählter Artikel: –";
      }
      try {
        if (articleSelect) articleSelect.value = "";
      } catch (_) {}

      try {
        if (KlimaGG && KlimaGG.minimd && typeof KlimaGG.minimd.resetForComment === "function") {
          KlimaGG.minimd.resetForComment();
        }
      } catch (_) {}
      updateCommentProgress("entwurf");
      setStatus(
        "Wähle im Gesetzestext „Kommentar schreiben“, um einen neuen Entwurf zu starten."
      );
      updateDraftPreviewButtonUI();
      updateButtonsDisabledState();
    }

    
    window.klimaggOpenCommentForArticle = async function (articleMeta) {
      if (!ensureLoggedIn()) return;
      await deactivateVersionLayerForCommentAction();
      
      setCommentPanelMode("editor");

      
      try { await invalidateDraftPreview({ clearInArticle: true }); } catch (_) {}

      commentState.currentArticleId = articleMeta.articleId;
      commentState.currentVersionId = articleMeta.versionId || null;
      commentState.currentCommentId = null;
      commentState.currentCommentStatus = "entwurf";
      commentState.currentStructurePayload = null;
      commentState.newArticleDraftCreated = false;
      commentState.newArticleCreatedArticleId = null;
      commentState.newArticleCreatedCommentId = null;
      setCommentDraftMode("change");

      // WICHTIG:
      
      if (anchorInput) anchorInput.value = "";
      if (proposalInput) proposalInput.value = "";
      if (explanationInput) explanationInput.value = "";
      if (sourcesInput) sourcesInput.value = "";
      clearRelatedForm();
      setLlmAssisted(false);
      if (cc0Checkbox) cc0Checkbox.checked = false;
      if (privacyCheckbox) privacyCheckbox.checked = false;
      if (rulesCheckbox) rulesCheckbox.checked = false;
      try { if (proposalInput) { proposalInput.style.minHeight = MINIMD_DEFAULT_EDITOR_MIN_HEIGHT; if (!proposalInput.rows || Number(proposalInput.rows) < 16) proposalInput.rows = 16; } } catch (_) {}

      try { resetImpactSliders(); } catch (_) {}
      try { highlightImpactMissing([]); } catch (_) {}
      try { refreshImpactArt2Link(); } catch (_) {}

      updateButtonsDisabledState();

      const parts = [];
      if (articleMeta.title) parts.push(articleMeta.title);
      if (articleMeta.versionLabel) parts.push("Version " + articleMeta.versionLabel);
      if (contextDisplay) {
        const label =
          parts.length > 0
            ? parts.join(" · ")
            : "Artikel-ID " + String(articleMeta.articleId || "");
        contextDisplay.textContent = "Gewählter Artikel: " + label;
      }

      try {
        if (typeof window.klimaggRefreshCommentArticleSelect === "function") {
          window.klimaggRefreshCommentArticleSelect();
        }
        if (articleSelect) {
          articleSelect.value = String(articleMeta.articleId || "");
        }
      } catch (_) {}

      if (anchorInput) {
        anchorInput.focus();
      }

      
      try {
        if (KlimaGG && KlimaGG.minimd && typeof KlimaGG.minimd.initForComment === "function") {
          KlimaGG.minimd.initForComment({
            articleId: commentState.currentArticleId,
            versionId: commentState.currentVersionId,
            textareaId: "comment-proposal",
            preserveTextarea: false,
            forceBaseIntoTextarea: true,
          });
        }
      } catch (_) {}

      updateCommentProgress("entwurf");
      setStatus(
        "Du bearbeitest einen neuen Entwurf. Speichere ihn oder reiche ihn zur Review ein."
      );

      const body = document.body;
      if (body && body.classList.contains("right-collapsed")) {
        body.classList.remove("right-collapsed");
      }
    };


    
    function buildVersionLabel(v) {
      if (!v) return null;
      const keys = ["version_number", "version_label", "label", "id"];
      for (const k of keys) {
        if (v[k] != null && String(v[k]).trim()) return String(v[k]).trim();
      }
      return null;
    }

    function openCommentForArticleById(articleId) {
      const idNum = Number(articleId);
      if (!Number.isFinite(idNum)) return false;
      const arts = Array.isArray(appState.articles_all) ? appState.articles_all : [];
      const art = arts.find((a) => a && Number(a.id) === idNum);
      if (!art) return false;
      const current = art.current_version || {};
      const versionLabel = buildVersionLabel(current);
      window.klimaggOpenCommentForArticle({
        articleId: art.id,
        versionId: current.id || null,
        title: _articleDisplayLabelForClient(art),
        versionLabel: versionLabel,
      });
      return true;
    }

    function getNearestRenderedArticleMeta() {
      const els = Array.from(document.querySelectorAll('#articles-container article[data-article-id]'));
      if (!els.length) return null;
      const refY = 120; // unter Sticky-Header
      let bestEl = null;
      let bestDist = Infinity;
      for (const el of els) {
        const r = el.getBoundingClientRect();
        const d = Math.abs((r.top || 0) - refY);
        if (d < bestDist) {
          bestDist = d;
          bestEl = el;
        }
      }
      if (!bestEl) return null;
      const id = Number(bestEl.getAttribute('data-article-id'));
      if (!Number.isFinite(id)) return null;
      const versionId = Number(bestEl.getAttribute('data-current-version-id'));
      const h2 = bestEl.querySelector('h2');
      const title = (h2 && typeof h2.textContent === 'string' ? h2.textContent : '').trim();
      return {
        articleId: id,
        versionId: Number.isFinite(versionId) ? versionId : null,
        title: title || ("Artikel " + String(id)),
        versionLabel: Number.isFinite(versionId) ? String(versionId) : null,
      };
    }

    function refreshArticleSelect() {
      if (!articleSelect) return;

      const canInteract = !!(klimaggAuth.user && klimaggAuth.accessToken);
      const arts = Array.isArray(appState.articles_all) ? appState.articles_all : [];
      const hasArticles = arts.length > 0;

      const enabled = canInteract && hasArticles;
      setDisabledStable(pickNearestBtn, !enabled);
      setDisabledStable(articleSelect, !enabled);

      
      if (!hasArticles) return;

      const currentValue = String(articleSelect.value || '');
      const preferred = commentState.currentArticleId != null ? String(commentState.currentArticleId) : '';

      const sorted = arts
        .filter((a) => a && a.id != null)
        .slice()
        .sort((a, b) => {
          const sa = _articleSortKeyForClient(a);
          const sb = _articleSortKeyForClient(b);
          if (sa !== sb) return sa - sb;
          return Number(a.id) - Number(b.id);
        });

      const nextSignature = sorted
        .map((a) => String(a.id) + "\t" + _articleDisplayLabelForClient(a))
        .join("\n");
      const currentSignature = String(articleSelect.dataset.optionsSignature || "");
      const mustRebuild = currentSignature !== nextSignature;

      
      
      
      if (mustRebuild) {
        if (document.activeElement === articleSelect && articleSelect.options.length > 1) return;

        articleSelect.innerHTML = '';
        const opt0 = document.createElement('option');
        opt0.value = '';
        opt0.textContent = 'Artikel auswählen …';
        articleSelect.appendChild(opt0);


        for (const a of sorted) {
          const o = document.createElement('option');
          o.value = String(a.id);
          o.textContent = _articleDisplayLabelForClient(a);
          articleSelect.appendChild(o);
        }
        articleSelect.dataset.optionsSignature = nextSignature;
      }

      const trySet = (val) => {
        if (!val) return false;
        const exists = Array.from(articleSelect.options).some((o) => o.value === val);
        if (exists) {
          articleSelect.value = val;
          return true;
        }
        return false;
      };

      if (!trySet(currentValue)) {
        if (!trySet(preferred)) {
          articleSelect.value = '';
        }
      }
    }

    window.klimaggRefreshCommentArticleSelect = refreshArticleSelect;

    if (llmDownloadLawBtn) llmDownloadLawBtn.addEventListener("click", () => downloadLlmContext("law"));
    if (llmDownloadArticleBtn) llmDownloadArticleBtn.addEventListener("click", () => downloadLlmContext("article"));
    if (llmDownloadInstructionsBtn) llmDownloadInstructionsBtn.addEventListener("click", () => downloadLlmContext("instructions"));
    if (llmCopyPromptBtn) llmCopyPromptBtn.addEventListener("click", () => { Promise.resolve(copyLlmPromptTemplate()).catch(() => {}); });
    if (relatedEnabledCheckbox) {
      relatedEnabledCheckbox.addEventListener("change", () => {
        setRelatedBodyVisible();
        try { refreshRelatedGroupSelect(); } catch (_) {}
      });
    }
    if (relatedGroupSelect) {
      relatedGroupSelect.addEventListener("change", () => applySelectedRelatedGroup());
    }
    if (llmAssistedCheckbox) llmAssistedCheckbox.addEventListener("change", () => syncLlmAssistedSourceLine());
    try { Promise.resolve(refreshLlmContextStatus()).catch(() => {}); } catch (_) {}

    if (commentModeSelect) {
      commentModeSelect.addEventListener("change", () => {
        const mode = getCommentDraftMode();
        commentState.commentDraftMode = mode;
        if (mode === "new_article") {
          commentState.currentArticleId = null;
          commentState.currentVersionId = null;
          if (!commentState.currentCommentId) {
            commentState.newArticleDraftCreated = false;
            commentState.newArticleCreatedArticleId = null;
            commentState.newArticleCreatedCommentId = null;
            if (proposalInput) proposalInput.value = NEW_ARTICLE_TEMPLATE_MINIMD;
          }
        }
        if (mode === "delete_article" && !commentState.currentCommentId) {
          if (proposalInput) proposalInput.value = "";
        }
        applyCommentDraftModeUI();
        updateButtonsDisabledState();
      });
      applyCommentDraftModeUI();
    }

    if (articleSelect) {
      articleSelect.addEventListener('change', () => {
        if (!ensureLoggedIn()) {
          try { refreshArticleSelect(); } catch (_) {}
          try { updateButtonsDisabledState(); } catch (_) {}
          return;
        }
        const val = String(articleSelect.value || '');
        if (!val) return;
        if (!openCommentForArticleById(val)) {
          setStatus('Artikel nicht gefunden.');
        }
      });
      articleSelect.addEventListener('blur', () => {
        window.setTimeout(() => {
          try { refreshArticleSelect(); } catch (_) {}
        }, 0);
      });
    }

    if (pickNearestBtn) {
      pickNearestBtn.addEventListener('click', () => {
        if (!ensureLoggedIn()) return;
        const meta = getNearestRenderedArticleMeta();
        if (!meta) {
          setStatus('Kein Artikel im Entwurf gefunden.');
          return;
        }
        window.klimaggOpenCommentForArticle(meta);
      });
    }

    
    try { refreshArticleSelect(); } catch (_) {}
    try { updateButtonsDisabledState(); } catch (_) {}


    async function saveDraft({ forPreview = false } = {}) {
      if (!ensureLoggedIn()) return false;
      const draftMode = getCommentDraftMode();

      if (draftMode === "new_article" && !commentState.currentCommentId) {
        if (!proposalInput || !String(proposalInput.value || "").trim()) {
          setStatus("Bitte fülle die MiniMD-Maske für den neuen Artikel aus.");
          notify("MiniMD-Maske fehlt.", { type: "warn" });
          try { proposalInput && proposalInput.focus(); } catch (_) {}
          return false;
        }
        if (!validateNewArticleMiniMdForSave()) return false;

        const payload = {
          minimd: sanitizeNewArticleMiniMdForSubmit(String(proposalInput.value || "")),
          structure_payload: collectRelatedStructurePayload(commentState.currentStructurePayload),
          explanation: explanationInput && explanationInput.value.trim() ? explanationInput.value.trim() : null,
          sources: sourcesInput && sourcesInput.value.trim() ? sourcesInput.value.trim() : null,
          impact_scores: null,
          llm_assisted: getLlmAssistedValue(),
          llm_context_hash: null,
        };
        try {
          const imp = collectImpactScores(false);
          payload.impact_scores = (imp && imp.anyTouched) ? imp.scores : null;
        } catch (_) {}

        const wasSaveDisabled = saveDraftBtn ? saveDraftBtn.disabled : false;
        const wasSubmitDisabled = submitBtn ? submitBtn.disabled : false;
        if (saveDraftBtn) saveDraftBtn.disabled = true;
        if (submitBtn) submitBtn.disabled = true;
        setStatus("Neuer Artikel wird angelegt …");

        let data;
        try {
          data = await apiFetchJson(
            "/api/comments/new-article-draft",
            {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify(payload),
            },
            { authRequired: true }
          );
        } catch (err) {
          console.error("[KlimaGG] Fehler beim Anlegen des neuen Artikel-Drafts:", err);
          setStatus("Der neue Artikel konnte nicht angelegt werden: " + (err && err.message ? String(err.message) : "Unbekannter Fehler"));
          notify("Neuer Artikel konnte nicht angelegt werden.", { type: "error" });
          if (saveDraftBtn) saveDraftBtn.disabled = wasSaveDisabled;
          if (submitBtn) submitBtn.disabled = wasSubmitDisabled;
          return false;
        }

        if (saveDraftBtn) saveDraftBtn.disabled = wasSaveDisabled;
        if (submitBtn) submitBtn.disabled = wasSubmitDisabled;

        const article = data && data.article ? data.article : null;
        const comment = data && data.comment ? data.comment : null;
        const articleId = data && data.article_id != null ? data.article_id : (article && article.id);
        const versionId = data && data.version_id != null ? data.version_id : (article && article.current_version && article.current_version.id);
        const commentId = data && data.activated_comment_id != null ? data.activated_comment_id : (comment && comment.id);
        if (!article || !comment || articleId == null || commentId == null) {
          setStatus("Neuer Artikel angelegt, aber die Serverantwort war unvollständig. Bitte Seite neu laden.");
          notify("Unvollständige Serverantwort.", { type: "warn" });
          return false;
        }

        upsertArticleIntoClientState(article);
        try {
          const scoped = filterArticlesByScope(appState.articles_all || [], getArticleScope());
          appState.articles = scoped;
          renderArticles(scoped);
        } catch (_) {}
        try { window.klimaggRefreshCommentArticleSelect && window.klimaggRefreshCommentArticleSelect(); } catch (_) {}

        commentState.currentArticleId = articleId;
        commentState.currentVersionId = versionId || null;
        commentState.currentCommentId = commentId;
        commentState.currentCommentStatus = String((comment && comment.status) || "entwurf").toLowerCase();
        commentState.currentStructurePayload = (comment && comment.structure_payload && typeof comment.structure_payload === "object") ? comment.structure_payload : collectRelatedStructurePayload(commentState.currentStructurePayload);
        try { upsertMyDraftCache(comment); } catch (_) {}
        commentState.newArticleDraftCreated = true;
        commentState.newArticleCreatedArticleId = articleId;
        commentState.newArticleCreatedCommentId = commentId;
        if (articleSelect) articleSelect.value = String(articleId);
        if (contextDisplay) contextDisplay.textContent = "Neuer Artikel: " + (_articleDisplayLabelForClient(article) || String(articleId));

        try { upsertCommentIntoArticleCache(articleId, comment); } catch (_) {}
        try {
          const prev = getCommentOverlayMode();
          commentState.draftPreviewPrevMaster = prev;
          setCommentOverlayModeAndSync("on");
        } catch (_) {}
        try {
          await activateDraftInlinePreview(comment, "newArticleDraftSave", versionId || undefined);
          commentState.draftPreviewCid = String(commentId);
          setPersistedDraftPreviewState(articleId, commentId, versionId || "");
        } catch (err) {
          console.warn("[KlimaGG] Neuer Artikel angelegt, Inline-Vorschau konnte nicht aktiviert werden:", err);
        }
        updateDraftPreviewButtonUI();
        updateCommentProgress("entwurf");
        setStatus("Neuer Artikel angelegt. Die Vorschau ist aktiv; du kannst den Entwurf jetzt weiter bearbeiten oder zur Review einreichen.");
        notify("Neuer Artikel angelegt.", { type: "success" });
        scrollToArticleCenter(articleId);
        return comment;
      }

      if (!commentState.currentArticleId) {
        setStatus(
          "Bitte wähle zuerst einen Artikel im Gesetzestext aus (Button „Kommentar schreiben“)."
        );
        notify("Bitte zuerst einen Artikel auswählen.", { type: "warn" });
        return false;
      }
      if (draftMode === "delete_article") {
        const payload = {
          article_id: commentState.currentArticleId,
          version_id: commentState.currentVersionId,
          type: "standard",
          comment_mode: "delete_article",
          structure_payload: collectRelatedStructurePayload(commentState.currentStructurePayload),
          proposal_text: "Antrag zur Artikel-Löschung",
          explanation: explanationInput && explanationInput.value.trim() ? explanationInput.value.trim() : null,
          sources: sourcesInput && sourcesInput.value.trim() ? sourcesInput.value.trim() : null,
          impact_scores: null,
          llm_assisted: getLlmAssistedValue(),
          llm_context_hash: null,
        };
        try {
          const imp = collectImpactScores(false);
          payload.impact_scores = (imp && imp.anyTouched) ? imp.scores : null;
        } catch (_) {}

        const method = commentState.currentCommentId ? "PUT" : "POST";
        const url = commentState.currentCommentId
          ? `/api/comments/${commentState.currentCommentId}`
          : "/api/comments";
        const wasSaveDisabled = saveDraftBtn ? saveDraftBtn.disabled : false;
        const wasSubmitDisabled = submitBtn ? submitBtn.disabled : false;
        if (saveDraftBtn) saveDraftBtn.disabled = true;
        if (submitBtn) submitBtn.disabled = true;
        setStatus("Löschantrag wird gespeichert …");
        let data;
        try {
          data = await apiFetchJson(
            url,
            {
              method,
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify(payload),
            },
            { authRequired: true }
          );
        } catch (err) {
          console.error("[KlimaGG] Fehler beim Speichern des Löschantrags:", err);
          setStatus("Der Löschantrag konnte nicht gespeichert werden: " + (err && err.message ? String(err.message) : "Unbekannter Fehler"));
          notify("Löschantrag konnte nicht gespeichert werden.", { type: "error" });
          if (saveDraftBtn) saveDraftBtn.disabled = wasSaveDisabled;
          if (submitBtn) submitBtn.disabled = wasSubmitDisabled;
          return false;
        }
        if (saveDraftBtn) saveDraftBtn.disabled = wasSaveDisabled;
        if (submitBtn) submitBtn.disabled = wasSubmitDisabled;
        commentState.currentCommentId = data && data.id != null ? data.id : commentState.currentCommentId;
        commentState.currentCommentStatus = String((data && data.status) || commentState.currentCommentStatus || "entwurf").toLowerCase();
        commentState.currentStructurePayload = (data && data.structure_payload && typeof data.structure_payload === "object") ? data.structure_payload : collectRelatedStructurePayload(commentState.currentStructurePayload);
        try { if (data && typeof data === "object") upsertMyDraftCache(data); } catch (_) {}
        updateDraftPreviewButtonUI();
        updateCommentProgress("entwurf");
        setStatus("Löschantrag gespeichert. Du kannst ihn jetzt zur Review einreichen.");
        if (!forPreview) requestRefreshCascade("comment", { articleId: commentState.currentArticleId });
        return (data && typeof data === "object") ? data : true;
      }

      if (!proposalInput || !String(proposalInput.value || "").trim()) {
        setStatus("Bitte formuliere einen Änderungsvorschlag.");
        notify("Änderungsvorschlag fehlt.", { type: "warn" });
        try { proposalInput && proposalInput.focus(); } catch (_) {}
        return false;
      }

      
      
      
      
      
      let baseMiniMd = null;
      try {
        const aid = commentState.currentArticleId;
        const vid = commentState.currentVersionId;
        if (aid != null && vid != null) {
          const r = await apiFetchJson(
            `/api/articles/${encodeURIComponent(String(aid))}/versions/${encodeURIComponent(String(vid))}/minimd`,
            { method: "GET" },
            { authRequired: true }
          );
          if (r && typeof r.minimd === "string") {
            baseMiniMd = r.minimd;
          }
        }
      } catch (err) {
        console.error("[KlimaGG] Canonical MiniMD baseline request failed:", err);
      }
      if (typeof baseMiniMd !== "string" || !baseMiniMd.trim()) {
        setStatus("Basistext (MiniMD) konnte nicht geladen werden – bitte Seite neu laden und erneut versuchen.")
        notify("Basistext konnte nicht geladen werden.", { type: "warn" });
        return false;
      }
      try {
        if (KlimaGG && KlimaGG.minimd && typeof KlimaGG.minimd.setBaseMiniMdForComment === "function") {
          KlimaGG.minimd.setBaseMiniMdForComment(baseMiniMd, {
            key: "server_save_" + String(commentState.currentArticleId || "") + "_" + String(commentState.currentVersionId || ""),
            preserveTextarea: true,
          });
        }
      } catch (_) {}

      
      try {
        if ((proposalInput.value || "") === baseMiniMd) {
          setStatus("Dein Vorschlag ist identisch mit dem Bestand – bitte ändere den Text oder breche ab.");
          notify("Keine Änderung gegenüber Bestand.", { type: "warn" });
          try { proposalInput && proposalInput.focus(); } catch (_) {}
          return false;
        }
      } catch (_) {}

      // Strict MiniMD invariant: both proposal and baseline require structural markers before saving.
      const baseSplit = kggSplitMiniMdBlocks(baseMiniMd);
      const newSplit = kggSplitMiniMdBlocks(String(proposalInput.value || ""));
      if (!baseSplit.hasMarkers || !newSplit.hasMarkers) {
        setStatus("Dein Vorschlag muss das MiniMD-Blockformat nutzen (### start/end Marker). Ohne Marker können wir keinen eindeutigen Kommentar-Patch erzeugen.");
        notify("MiniMD-Marker fehlen.", { type: "warn" });
        try { proposalInput && proposalInput.focus(); } catch (_) {}
        return false;
      }

      const baseBlocks = kggFilterTopLevelMiniMdBlocks((baseSplit && baseSplit.blocks) ? baseSplit.blocks : {});
      const newBlocks = kggFilterTopLevelMiniMdBlocks((newSplit && newSplit.blocks) ? newSplit.blocks : {});

      
      const keys = [];
      const seen = new Set();
      Object.keys(baseBlocks).forEach((k) => {
        if (Object.prototype.hasOwnProperty.call(baseBlocks, k)) { seen.add(k); keys.push(k); }
      });
      Object.keys(newBlocks).forEach((k) => {
        if (!KGG_TOP_LEVEL_MINIMD_BLOCK_KEYS.includes(String(k || "").trim().toLowerCase())) {
          return;
        }
        if (!seen.has(k)) { seen.add(k); keys.push(k); }
      });
      const parts = [];
      for (const k0 of keys) {
        const k = String(k0 || "").trim().toLowerCase();
        if (k === "meta" && !canCurrentUserEditCommentMeta()) {
          
          
          continue;
        }
        const oldB = kggNormalizeMiniMdBlockBodyForPatch((baseBlocks || {})[k] ?? "");
        const newB = kggNormalizeMiniMdBlockBodyForPatch((newBlocks || {})[k] ?? "");
        if (oldB === newB) continue;
        const baselineHash = await _kggSha256HexText(oldB);
        const part = _kggBuildExactSelectionPart("p_" + k, k, oldB, newB, baselineHash);
        if (!Number.isInteger(part.sel_start) || !Number.isInteger(part.sel_end) || !baselineHash) {
          setStatus("Patch-Metadaten konnten nicht erzeugt werden (Selection/Hash). Bitte Seite neu laden und erneut versuchen.");
          notify("Patch-Metadaten fehlen (Selection/Hash).", { type: "error" });
          return false;
        }
        parts.push(part);
      }

      if (!parts.length) {
        setStatus("Keine Änderungen erkannt (dein Vorschlag entspricht dem Bestand).");
        notify("Keine Änderungen erkannt.", { type: "info" });
        return false;
      }

      const proposalMiniMd = String(proposalInput.value || "");

      
      const manualAnchorNote = 
        anchorInput && anchorInput.value.trim()
          ? String(anchorInput.value.trim())
          : null;

      const payload = {
        article_id: commentState.currentArticleId,
        version_id: commentState.currentVersionId,
        type: "standard",
        comment_mode: draftMode === "new_article" ? "new_article" : "change",
        anchor: manualAnchorNote ? { kind: "note", label: manualAnchorNote } : null,
        anchor_payload: manualAnchorNote ? { kind: "note", label: manualAnchorNote } : null,
        structure_payload: collectRelatedStructurePayload(commentState.currentStructurePayload),
        patch_payload: {
          version: 2,
          editor: { kind: "minimd_blocks", ui: "comment_draft", strict: true },
          base_version_id: commentState.currentVersionId || null,
          parts: parts,
        },
        proposal_text: proposalMiniMd,
        explanation:
          explanationInput && explanationInput.value.trim()
            ? explanationInput.value.trim()
            : null,
        sources:
          sourcesInput && sourcesInput.value.trim()
            ? sourcesInput.value.trim()
            : null,
        impact_scores: null,
        llm_assisted: getLlmAssistedValue(),
        llm_context_hash: null,
      };

      
      try {
        const imp = collectImpactScores(false);
        payload.impact_scores = (imp && imp.anyTouched) ? imp.scores : null;
      } catch (_) {}

      const method = commentState.currentCommentId ? "PUT" : "POST";
      const url = commentState.currentCommentId
        ? `/api/comments/${commentState.currentCommentId}`
        : "/api/comments";
      const previewWasActive = (() => {
        const persisted = getPersistedDraftPreviewState();
        const curAid = commentState.currentArticleId != null ? String(commentState.currentArticleId) : "";
        return !!(curAid && persisted && persisted.articleId === curAid);
      })();

      const wasSaveDisabled = saveDraftBtn ? saveDraftBtn.disabled : false;
      const wasSubmitDisabled = submitBtn ? submitBtn.disabled : false;
      if (saveDraftBtn) saveDraftBtn.disabled = true;
      if (submitBtn) submitBtn.disabled = true;
      setStatus("Entwurf wird gespeichert …");

      let data;
      try {
        data = await apiFetchJson(
          url,
          {
            method,
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload),
          },
          { authRequired: true }
        );
      } catch (err) {
        console.error(
          "[KlimaGG] Fehler beim Speichern des Kommentarentwurfs:",
          err
        );
        setStatus(
          "Der Entwurf konnte nicht gespeichert werden: " +
            (err && err.message ? String(err.message) : "Unbekannter Fehler")
        );
        notify("Entwurf konnte nicht gespeichert werden.", { type: "error" });
        if (saveDraftBtn) saveDraftBtn.disabled = wasSaveDisabled;
        if (submitBtn) submitBtn.disabled = wasSubmitDisabled;
        return false;
      }

      if (saveDraftBtn) saveDraftBtn.disabled = wasSaveDisabled;
      if (submitBtn) submitBtn.disabled = wasSubmitDisabled;
      commentState.currentCommentId = data && data.id != null ? data.id : commentState.currentCommentId;
      commentState.currentCommentStatus = String((data && data.status) || commentState.currentCommentStatus || "entwurf").toLowerCase();
      commentState.currentStructurePayload = (data && data.structure_payload && typeof data.structure_payload === "object") ? data.structure_payload : collectRelatedStructurePayload(commentState.currentStructurePayload);
      try { if (data && typeof data === "object") upsertMyDraftCache(data); } catch (_) {}
      if (previewWasActive && data && typeof data === "object" && data.id != null && commentState.currentArticleId) {
        try {
          await activateDraftInlinePreview(
            data,
            "submitDraft.previewWasActive",
            commentState.currentVersionId || undefined
          );
        } catch (_) {}
      }
      updateDraftPreviewButtonUI();
      updateCommentProgress("entwurf");
      setStatus(
        "Entwurf gespeichert. Du kannst ihn jetzt weiter bearbeiten oder zur Review einreichen."
      );

      
      if (!forPreview) {
        requestRefreshCascade("comment", { articleId: commentState.currentArticleId });
        try {
          if (typeof window.klimaggRefreshMyCommentsOverview === "function") {
            await window.klimaggRefreshMyCommentsOverview({ force: true, silent: true });
            try { if (typeof window.klimaggUpdateCommentPanelButtons === "function") window.klimaggUpdateCommentPanelButtons(); } catch (_) {}
          }
        } catch (_) {}
      }
      if (forPreview && (!data || typeof data !== "object" || data.id == null)) {
        setStatus("Preview fehlgeschlagen: Server lieferte keinen gültigen Entwurf zurück.");
        notify("Preview fehlgeschlagen: ungültige Serverantwort.", { type: "error" });
        return false;
      }
      return (data && typeof data === "object") ? data : true;
    }
    async function runDraftPreview() {
      let failCode = "";
      let failErr = null;
      let previewCid = null;
      let previewAid = null;
      const setDraftPreviewFail = (code, errObj) => {
        failCode = String(code || "canonical_preview_failed");
        failErr = errObj || null;
        try {
          const msg = "Vorschau konnte nicht erzeugt (" + failCode + ").";
          setStatus(msg);
          notify(msg, { type: "warn" });
        } catch (_) {}
        try {
          const em = failErr && failErr.message ? String(failErr.message) : "";
          console.warn("[DraftPreviewFail] code=" + failCode + (em ? " err=" + em : ""), failErr || "");
        } catch (_) {}
      };
      const mapFailCodeFromError = (errObj, fallbackCode) => {
      const raw = (errObj && errObj.message) ? String(errObj.message || "") : "";
      const low = raw.trim().toLowerCase();
      if (!low) return String(fallbackCode || "canonical_preview_failed");
      if (low.includes("resolveinlinediffscope is not defined")) return "inline_write_failed";
      if (low.includes("getinlinediffapiorthrow is not defined")) return "inline_write_failed";
      if (low.includes("getinlinediffscope is not defined")) return "inline_write_failed";
      if (low.includes("inline_diff_api is not defined")) return "inline_write_failed";
      if (low.includes("inline_diff_api unavailable via window.klimagg.inlinediff")) return "inline_write_failed";
      if (low.includes("inlinediffapi unavailable")) return "inline_write_failed";
        if (low === "minimd_no_markers" || low.includes("minimd_no_markers")) return "minimd_no_markers";
        if (low.includes("canonical_base_missing")) return "canonical_base_missing";
        if (low.includes("canonical_merge_preview_unavailable")) return "canonical_merge_preview_unavailable";
        if (low.includes("canonical_parts_empty")) return "canonical_parts_empty";
        if (low.includes("canonical_comment_missing")) return "canonical_comment_missing";
        if (low.includes("canonical_comment_id_missing")) return "canonical_comment_id_missing";
        if (low.includes("canonical_patch_payload_missing")) return "canonical_patch_payload_missing";
        if (low.includes("canonical_semantic_events_missing")) return "canonical_semantic_events_missing";
        if (low.includes("draft_save_failed")) return "draft_save_failed";
        return String(fallbackCode || "canonical_preview_failed");
      };

      if (!ensureLoggedIn()) {
        setPreviewDebug({
          attempted: true,
          articleId: commentState.currentArticleId || null,
          canonicalBackend: false,
          lastErrorCode: "not_logged_in",
          lastErrorMessage: "",
          cleanupDone: true,
        });
        setDraftPreviewFail("not_logged_in", null);
        return false;
      }
      const aid = commentState.currentArticleId;
      const vid = commentState.currentVersionId || (typeof getCurrentVersionIdForArticle === "function" ? (getCurrentVersionIdForArticle(aid) || null) : null);
      try { console.info("[DraftPreview] start aid=" + String(aid || "") + " vid=" + String(vid || "")); } catch (_) {}
      if (!aid) {
        setPreviewDebug({
          attempted: true,
          articleId: null,
          canonicalBackend: false,
          lastErrorCode: "missing_article_id",
          lastErrorMessage: "",
          cleanupDone: true,
        });
        setDraftPreviewFail("missing_article_id", null);
        return false;
      }
      if (!proposalInput) {
        setPreviewDebug({
          attempted: true,
          articleId: aid,
          canonicalBackend: false,
          lastErrorCode: "missing_proposal_input",
          lastErrorMessage: "",
          cleanupDone: true,
        });
        setDraftPreviewFail("missing_proposal_input", null);
        return false;
      }

      setPreviewDebug({
        attempted: true,
        version: KLIMAGG_WEB_JS_VERSION,
        articleId: aid,
        previewCid: null,
        fromBackendSave: false,
        semanticEventsCount: 0,
        semanticEventsOk: false,
        cardInsertOk: false,
        inlineWriteOk: false,
        cleanupDone: false,
        canonicalBackend: false,
        lastErrorCode: "",
        lastErrorMessage: "",
      });

      // Remove the previous preview before rendering the next one.
      if (isDraftPreviewActiveForCurrentComment()) {
        await invalidateDraftPreview({ clearInArticle: true });
        updateDraftPreviewButtonUI();
        setStatus("Vorschau im Artikel deaktiviert.");
        return true;
      }

      const newText = String(proposalInput.value || "");

      // Backend-backed preview: always persist/update draft first.
      try {
        const inlineApi = getInlineDiffApiOrThrow();
        const hasInlineApi = !!(inlineApi && typeof inlineApi.toggleDraft === "function" && typeof inlineApi.refresh === "function");
        if (!hasInlineApi) {
          failCode = "canonical_merge_preview_unavailable";
          throw new Error(failCode);
        }
        const saved = await saveDraft({ forPreview: true });
        if (!saved || typeof saved !== "object" || saved.id == null) {
          failCode = "draft_save_failed";
          throw new Error(failCode);
        }
        setPreviewDebug({ fromBackendSave: true });
        const draftComment = saved;
        if (!draftComment || typeof draftComment !== "object") {
          failCode = "canonical_comment_missing";
          throw new Error(failCode);
        }
        if (draftComment.id == null) {
          failCode = "canonical_comment_id_missing";
          throw new Error(failCode);
        }
        const pp = (draftComment.patch_payload && typeof draftComment.patch_payload === "object")
          ? draftComment.patch_payload
          : null;
        if (!pp) {
          failCode = "canonical_patch_payload_missing";
          throw new Error(failCode);
        }
        const cid = String(draftComment.id);
        previewCid = cid;
        previewAid = aid;
        setPreviewDebug({ previewCid: cid });
        const previewVid = (draftComment.version_id != null) ? draftComment.version_id : (vid || undefined);

        window.__klimaggCommentCacheById = window.__klimaggCommentCacheById || {};
        window.__klimaggCommentCacheById[cid] = draftComment;

        try {
          if (!draftComment.public_author_label && !draftComment.author_label && !draftComment.author) {
            const me = (typeof klimaggAuth !== "undefined" && klimaggAuth && klimaggAuth.user) ? klimaggAuth.user : null;
            const pseudo = me && (me.pseudonym || me.public_author_label || me.author_label || me.author)
              ? String(me.pseudonym || me.public_author_label || me.author_label || me.author)
              : "";
            if (pseudo) {
              draftComment.public_author_label = pseudo;
            }
          }
        } catch (_) {}

        // Draft/Review preview must be visible regardless of Master toggle.
        // Temporarily force Master ON (and remember previous state).
        try {
          const prev = getCommentOverlayMode();
          commentState.draftPreviewPrevMaster = prev;
          setCommentOverlayModeAndSync("on");
        } catch (_) {}

        try {
          await activateDraftInlinePreview(
            draftComment,
            "runDraftPreview",
            previewVid
          );
          setPreviewDebug({ inlineWriteOk: true });
        } catch (e) {
          failCode = "inline_write_failed";
          throw e;
        }

        try {
          upsertCommentIntoArticleCache(aid, draftComment);
          setPreviewDebug({ cardInsertOk: true });
        } catch (_) {}

        commentState.currentCommentId = draftComment.id;
        commentState.currentCommentStatus = String(draftComment.status || "entwurf").toLowerCase();
        commentState.draftPreviewCid = cid;
        setPersistedDraftPreviewState(aid, cid, previewVid || "");
        updateDraftPreviewButtonUI();
        setPreviewDebug({
          canonicalBackend: true,
          cleanupDone: false,
          lastErrorCode: "",
          lastErrorMessage: "",
        });
        setStatus("Vorschau im Artikel aktiv. Wenn alles passt: Entwurf speichern oder einreichen.");
        return true;
      } catch (err) {
        try {
          if (previewCid && window.__klimaggCommentCacheById) {
            delete window.__klimaggCommentCacheById[String(previewCid)];
          }
        } catch (_) {}
        try {
          if (previewAid && previewCid) removeCommentFromArticleCache(previewAid, previewCid);
        } catch (_) {}
        try {
          if (previewAid && previewCid) {
            await deactivateDraftInlinePreview(
              previewAid,
              commentState.currentVersionId,
              previewCid,
              "runDraftPreview.cleanup",
              {
                removeCommentCache: true,
                removeGlobalCache: true,
                clearPersisted: false,
              }
            );
          }
        } catch (_) {}
        commentState.draftPreviewCid = null;
        try { clearPersistedDraftPreviewState(); } catch (_) {}
        try { if (previewAid && previewCid) removeInlineDiffIdsFromArticleState(previewAid, [previewCid], commentState.currentVersionId || undefined); } catch (_) {}
        setPreviewDebug({
          canonicalBackend: false,
          cleanupDone: true,
          cardInsertOk: false,
          inlineWriteOk: false,
          previewCid: null,
          lastErrorCode: String(failCode || mapFailCodeFromError(err, "canonical_preview_failed")),
          lastErrorMessage: (err && err.message) ? String(err.message) : "",
        });
        setDraftPreviewFail(
          failCode || mapFailCodeFromError(err, "canonical_preview_failed"),
          err
        );
      }

      if (!failCode) {
        setDraftPreviewFail("canonical_preview_failed", failErr);
      }
      return false;
    }


    async function submitDraft() {
      if (!ensureLoggedIn()) return;
      const draftMode = getCommentDraftMode();
      if (!commentState.currentArticleId && draftMode !== "new_article") {
        setStatus(
          "Bitte wähle zuerst einen Artikel im Gesetzestext aus (Button „Kommentar schreiben“)."
        );
        notify("Bitte zuerst einen Artikel auswählen.", { type: "warn" });
        return;
      }

      
      if (cc0Checkbox && !cc0Checkbox.checked) {
        setStatus("Bitte bestätige die Lizenz-Freigabe (CC0/ähnlich), damit wir deinen Beitrag veröffentlichen dürfen.");
        notify("Lizenz-Freigabe fehlt.", { type: "warn" });
        return;
      }
      if (rulesCheckbox && !rulesCheckbox.checked) {
        setStatus("Bitte bestätige, dass du die Nutzungsbedingungen gelesen hast (keine personenbezogenen Daten Dritter, kein Spam).");
        notify("Nutzungsbedingungen nicht bestätigt.", { type: "warn" });
        return;
      }

      // The author assessment is required before review submission.
      const imp = collectImpactScores(true);
      if (imp && Array.isArray(imp.missing) && imp.missing.length) {
        highlightImpactMissing(imp.missing);
        setStatus(
          "Bitte fülle vor dem Einreichen die eigene Einschätzung vollständig aus (alle vier Slider)."
        );
        notify("Eigene Einschätzung fehlt.", { type: "warn" });
        try {
          const first = imp.missing[0];
          const obj = impactInputs && impactInputs[first] ? impactInputs[first] : null;
          if (obj && obj.input) obj.input.focus();
        } catch (_) {}
        return;
       }

      if (!commentState.currentCommentId) {
        const saved = await saveDraft();
        if (!saved || !commentState.currentCommentId) return;
        const cooldown = getSubmitReviewCooldownSeconds();
        setStatus(
          cooldown > 0
            ? "Entwurf gespeichert. Bitte lies ihn noch einmal durch; Einreichen ist möglich nach " + formatCooldownSeconds(cooldown) + "."
            : "Entwurf gespeichert. Du kannst ihn jetzt zur Review einreichen."
        );
        notify("Entwurf gespeichert.", { type: "info" });
        return;
      }

      if (currentDraftHasUnsavedChanges()) {
        const saved = await saveDraft();
        if (!saved) return;
        const cooldown = getSubmitReviewCooldownSeconds();
        setStatus(
          cooldown > 0
            ? "Änderungen gespeichert. Bitte lies den Entwurf noch einmal durch; Einreichen ist möglich nach " + formatCooldownSeconds(cooldown) + "."
            : "Änderungen gespeichert. Bitte klicke erneut auf „Zur Review einreichen“."
        );
        notify("Änderungen gespeichert.", { type: "info" });
        return;
      }

      const wasSaveDisabled = saveDraftBtn ? saveDraftBtn.disabled : false;
      const wasSubmitDisabled = submitBtn ? submitBtn.disabled : false;
      if (saveDraftBtn) saveDraftBtn.disabled = true;
      if (submitBtn) submitBtn.disabled = true;
      setStatus("Entwurf wird eingereicht …");

      try {
        await apiFetchJson(
          `/api/comments/${commentState.currentCommentId}/submit`,
          {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({}),
          },
          { authRequired: true }
        );
      } catch (err) {
        console.error("[KlimaGG] Fehler beim Einreichen des Kommentarentwurfs:", err);
        setStatus(
          "Der Entwurf konnte nicht eingereicht werden: " +
            (err && err.message ? String(err.message) : "Unbekannter Fehler")
        );
        notify("Entwurf konnte nicht eingereicht werden.", { type: "error" });
        if (saveDraftBtn) saveDraftBtn.disabled = wasSaveDisabled;
        if (submitBtn) submitBtn.disabled = wasSubmitDisabled;
        return;
      }

      if (saveDraftBtn) saveDraftBtn.disabled = true;
      if (submitBtn) submitBtn.disabled = true;
      if (previewBtn) previewBtn.disabled = true;
      if (deleteDraftBtn) deleteDraftBtn.disabled = true;

      updateCommentProgress("review");
      setStatus(
        "Entwurf eingereicht. Er erscheint in der Review-Phase und kann später veröffentlicht werden."
      );

      try { await deactivateDraftInlinePreview({ preservePersisted: false }); } catch (_) {}
      try { setCommentPanelMode("overview"); } catch (_) {}
      notify("Entwurf eingereicht.", { type: "success" });
      requestRefreshCascade("comment", { articleId: commentState.currentArticleId });
      try {
        if (typeof window.klimaggRefreshMyCommentsOverview === "function") {
          await window.klimaggRefreshMyCommentsOverview({ force: true, silent: true });
          try { if (typeof window.klimaggUpdateCommentPanelButtons === "function") window.klimaggUpdateCommentPanelButtons(); } catch (_) {}
        }
      } catch (_) {}
    }

    if (saveDraftBtn) {
      saveDraftBtn.addEventListener("click", async (ev) => {
        ev.preventDefault();
        await saveDraft();
      });
    }

    if (submitBtn) {
      submitBtn.addEventListener("click", async (ev) => {
        ev.preventDefault();
        await submitDraft();
      });
    }

    // Preview the draft as an inline diff in the article.
    if (previewBtn) {
      previewBtn.addEventListener("click", async (ev) => {
        ev.preventDefault();
        await runDraftPreview();
      });
    }

    
    
    if (deleteDraftBtn) {
      deleteDraftBtn.addEventListener("click", async (ev) => {
        ev.preventDefault();

        
        try { await invalidateDraftPreview({ clearInArticle: true }); } catch (_) {}

        const cid = commentState.currentCommentId;
        const st = String(commentState.currentCommentStatus || "").toLowerCase();

        
        if (cid != null && (st === "entwurf" || st === "reif" || st === "korrigieren")) {
          try {
            if (ensureLoggedIn()) {
              await apiFetchJson(
                `/api/comments/${cid}/delete`,
                { method: "POST" },
                { authRequired: true }
              );
            }
          } catch (err) {
            console.warn("[KlimaGG] Entwurf löschen (server) fehlgeschlagen – wird lokal verworfen:", err);
          }
        }

        try {
          if (cid != null && commentState.myCommentsCache && Array.isArray(commentState.myCommentsCache)) {
            commentState.myCommentsCache = commentState.myCommentsCache.filter((x) => String((x && x.id != null) ? x.id : "") !== String(cid));
          }
        } catch (_) {}

        
        try { await resetForm(); } catch (_) {}
        try { setCommentPanelMode("overview"); } catch (_) {}
        try { await loadMyCommentsOverview({ force: true, silent: true }); } catch (_) {}

        setStatus("Entwurf verworfen.");
        notify("Entwurf gelöscht.", { type: "info" });
        updateButtonsDisabledState();
        updateDraftPreviewButtonUI();
      });
    }

    
    if (proposalInput) {
      proposalInput.addEventListener("input", () => {
        if (isDraftPreviewActiveForCurrentComment()) {
          Promise.resolve(invalidateDraftPreview({ clearInArticle: true })).catch(() => {});
        }
        updateButtonsDisabledState();
      });
    }

    Promise.resolve(resetForm()).catch(() => {});
    
    try {
      setCommentPanelMode("overview");
      Promise.resolve(loadMyCommentsOverview({ force: true, silent: true })).catch(() => {});
    } catch (_) {}
  }
  

  // -------------------------------------------------------------------------
  // Soft-module exports for diagnostics and future physical modularization.
  // -------------------------------------------------------------------------
  KlimaGG.core.version = KLIMAGG_WEB_JS_VERSION;
  KlimaGG.core.isAppIndex = isAppIndex;
  KlimaGG.core.state = appState;

  KlimaGG.keys = {
    AUTH_STORAGE_KEY,
    AUTH_STATE_CHANGED_KEY,
    PERSONAL_MOOD_TOC_STORAGE_KEY,
    BOOKMARKS_TOC_STORAGE_KEY,
    UI_RIGHT_PANEL_STORAGE_KEY,
    UI_LEFT_PANEL_STORAGE_KEY,
  };

  KlimaGG.api.fetchJson = apiFetchJson;
  KlimaGG.ui.notify = notify;
  KlimaGG.ui.setStatusText = setStatusText;

  KlimaGG.auth.state = klimaggAuth;
  KlimaGG.auth.init = initAuth;
  KlimaGG.auth.startLoginFlow = startLoginFlow;
  KlimaGG.auth.refreshAccessToken = refreshAccessToken;

  KlimaGG.ui.updateUserUI = updateUserUI;
  KlimaGG.ui.setupPanelToggles = setupPanelToggles;
  KlimaGG.ui.setupResizeHandles = setupResizeHandles;
  KlimaGG.ui.setupTextLayerToggles = setupTextLayerToggles;

  KlimaGG.features.fetchArticles = fetchArticles;
  KlimaGG.features.renderArticles = renderArticles;
  KlimaGG.features.loadUserReviewStats = loadUserReviewStats;
  KlimaGG.features.setupReviewQueuePanel = setupReviewQueuePanel;
  KlimaGG.features.setupCommentPanel = setupCommentPanel;


})();
