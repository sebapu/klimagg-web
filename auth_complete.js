// KlimaGG-Web — magic-link browser completion bridge
// Version: v2.0.0
// Loaded by /auth/complete under the self-only script CSP. It exchanges the
// e-mail token through /api/auth/callback, persists either the access token or
// the signup token, and redirects to the requested application location.

(function () {
  "use strict";

  const AUTH_STORAGE_KEY = "klimagg_auth_token_v1";
  const SIGNUP_TOKEN_STORAGE_KEY = "klimagg_signup_token_v1";
  const AUTH_STATE_CHANGED_KEY = "klimagg_auth_state_changed_v1";
  const LAST_COMPLETED_MAGIC_TOKEN_KEY = "klimagg_auth_last_completed_magic_token_v1";
  // The homepage sidebars start hidden to avoid FOUC. Successful magic-link
  // completion sets this flag so script.js opens the user panel after redirect.
  const POST_LOGIN_OPEN_RIGHT_PANEL_KEY = "klimagg.ui.post_login_open_right_panel";

  // Default destination after authentication. Avoid the login hash so the CTA
  // does not restart the authentication flow.
  const DEFAULT_NEXT = "/#entwurf";

  function setStatus(msg) {
    const el = document.getElementById("auth-complete-status");
    if (el) el.textContent = String(msg || "");
  }

  function safeGetLocalStorage(key) {
    try {
      return window.localStorage.getItem(key) || "";
    } catch (_) {
      return "";
    }
  }

  function safeSetLocalStorage(key, value) {
    try {
      window.localStorage.setItem(key, value);
      return true;
    } catch (_) {
      return false;
    }
  }

  function safeRemoveLocalStorage(key) {
    try {
      window.localStorage.removeItem(key);
      return true;
    } catch (_) {
      return false;
    }
  }

  function signalAuthStateChanged(reason) {
    safeSetLocalStorage(
      AUTH_STATE_CHANGED_KEY,
      String(Date.now()) + ":" + String(reason || "auth_complete")
    );
  }

  function clearLocalAuthState(reason) {
    safeRemoveLocalStorage(AUTH_STORAGE_KEY);
    safeRemoveLocalStorage(SIGNUP_TOKEN_STORAGE_KEY);
    safeRemoveLocalStorage(POST_LOGIN_OPEN_RIGHT_PANEL_KEY);
    signalAuthStateChanged(reason || "auth_clear");
  }

  function getAuthError(data) {
    const detail = data && data.detail;
    if (!detail || typeof detail !== "object" || !detail.code || !detail.message) {
      throw new Error("auth_callback_error_contract_invalid");
    }
    return {
      code: String(detail.code),
      message: String(detail.message),
    };
  }

  function safeNextTarget(raw) {
    const value = String(raw || "").trim();
    if (!value || !value.startsWith("/") || value.startsWith("//")) {
      return DEFAULT_NEXT;
    }

    try {
      const url = new URL(value, window.location.origin);
      if (url.origin !== window.location.origin) {
        return DEFAULT_NEXT;
      }
      return url.pathname + url.search + url.hash;
    } catch (_) {
      return DEFAULT_NEXT;
    }
  }

  async function verifyAccessToken(accessToken) {
    if (!accessToken) return false;
    const resp = await fetch("/api/me", {
      method: "GET",
      headers: { "Accept": "application/json", "Authorization": "Bearer " + accessToken },
      credentials: "same-origin",
      cache: "no-store",
    });
    return !!resp.ok;
  }

  async function run() {
    const params = new URLSearchParams(window.location.search || "");
    const token = params.get("token");
    const next = safeNextTarget(params.get("next"));
    const previousAccessToken = safeGetLocalStorage(AUTH_STORAGE_KEY);
    const previousCompletedMagicToken = safeGetLocalStorage(LAST_COMPLETED_MAGIC_TOKEN_KEY);

    // Starting a magic-link completion switches authentication context. Clear
    // any current access/signup state before consuming the new token.
    clearLocalAuthState("auth_complete_start");

    if (!token) {
      setStatus("Fehler: Link ohne Token. Bitte fordere einen neuen Link an.");
      return;
    }

    try {
      setStatus("Anmeldung wird abgeschlossen …");

      const resp = await fetch("/api/auth/callback?token=" + encodeURIComponent(token), {
        method: "GET",
        headers: { "Accept": "application/json" },
        credentials: "same-origin",
        cache: "no-store",
      });

      const ct = (resp.headers.get("content-type") || "").toLowerCase();
      if (!ct.includes("application/json")) {
        throw new Error("auth_callback_non_json_response");
      }
      const data = await resp.json();

      if (!resp.ok) {
        const authError = getAuthError(data);
        const code = authError.code;
        if (
          code === "token_used" &&
          previousAccessToken &&
          previousCompletedMagicToken &&
          previousCompletedMagicToken === token
        ) {
          setStatus("Anmeldestatus wird geprüft …");
          if (await verifyAccessToken(previousAccessToken)) {
            safeSetLocalStorage(AUTH_STORAGE_KEY, previousAccessToken);
            safeRemoveLocalStorage(SIGNUP_TOKEN_STORAGE_KEY);
            safeSetLocalStorage(POST_LOGIN_OPEN_RIGHT_PANEL_KEY, "1");
            signalAuthStateChanged("auth_complete_reuse_verified");
            setStatus("Du bist bereits angemeldet. Weiterleitung …");
            try {
              window.location.replace(next);
            } catch (_) {
              window.location.href = next;
            }
            return;
          }
          clearLocalAuthState("auth_complete_reuse_invalid");
        }
        setStatus("Fehler: " + authError.message);
        return;
      }

      // New accounts complete profile creation through a signup token rather than receiving a JWT immediately.
      const signupRequired = !!(data && data.signup_required);
      const signupToken = data && data.signup_token ? String(data.signup_token) : "";

      if (signupRequired && signupToken) {
        safeRemoveLocalStorage(AUTH_STORAGE_KEY);
        safeSetLocalStorage(SIGNUP_TOKEN_STORAGE_KEY, signupToken);
        safeSetLocalStorage(POST_LOGIN_OPEN_RIGHT_PANEL_KEY, "1");
        signalAuthStateChanged("auth_complete_signup_required");

        setStatus("E-Mail bestätigt. Bitte wähle jetzt ein Pseudonym und schließe die Registrierung ab …");
        try {
          window.location.replace(next);
        } catch (_) {
          window.location.href = next;
        }
        return;
      }

      // Existing accounts receive the access token directly from the callback.
      const accessToken = data && data.access_token ? String(data.access_token) : "";
      if (!accessToken) {
        setStatus("Fehler: Callback ohne Access-Token.");
        return;
      }

      // Authenticated and signup states are mutually exclusive.
      safeRemoveLocalStorage(SIGNUP_TOKEN_STORAGE_KEY);
      safeSetLocalStorage(AUTH_STORAGE_KEY, accessToken);
      safeSetLocalStorage(LAST_COMPLETED_MAGIC_TOKEN_KEY, token);
      safeSetLocalStorage(POST_LOGIN_OPEN_RIGHT_PANEL_KEY, "1");
      signalAuthStateChanged("auth_complete_success");

      setStatus("Erfolgreich angemeldet. Weiterleitung …");
      try {
        window.location.replace(next);
      } catch (_) {
        window.location.href = next;
      }
    } catch (e) {
      clearLocalAuthState("auth_complete_exception");
      setStatus("Fehler: Login-Bridge konnte nicht ausgeführt werden. Bitte fordere einen neuen Link an.");
      try {
        console.error("[KlimaGG] auth_complete.js failed:", e);
      } catch (_) {}
    }
  }

  document.addEventListener("DOMContentLoaded", run);
})();
