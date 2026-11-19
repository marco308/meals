// Signing in through the operator's identity provider (Q33, issue #93).
//
// A trip is a navigation, not a fetch: the API answers where to send the
// browser, we go there, the provider sends us back to /auth/oidc/callback, and
// that lands on #/sso/<handoff>. Trading the handoff (POST /auth/oidc/complete)
// is what gets the session, so no token ever sits in an address bar. Nothing
// here fetches anything from the provider, which is why the CSP needs no new
// source: leaving the page is not a request the CSP polices.
//
// `after` remembers what a re-authentication was for (deleting the account,
// setting a password, leaving a household), so Settings can pick it up again
// when we come back. sessionStorage, because it means nothing in another tab,
// and wrapped, because a private window may refuse it: then the person just
// presses the button again.

import { api, session } from "./api.js";
import { toast } from "./dom.js";

const AFTER_KEY = "yamp.sso.after";

export async function goToProvider(path, body = {}, after = null) {
  const { authorization_url } = await api(path, { method: "POST", body });
  try {
    if (after) sessionStorage.setItem(AFTER_KEY, after);
    else sessionStorage.removeItem(AFTER_KEY);
  } catch {
    // nothing to remember it in; the button still works the second time
  }
  window.location.assign(authorization_url);
}

// What a re-authentication was for, once: reading it forgets it.
export function takeAfter() {
  try {
    const after = sessionStorage.getItem(AFTER_KEY);
    sessionStorage.removeItem(AFTER_KEY);
    return after;
  } catch {
    return null;
  }
}

export async function finishSso(handoff) {
  // Out of the address bar and the back button before anything else: it is
  // single-use, but nobody needs to see it.
  history.replaceState(null, "", window.location.pathname + window.location.search);
  try {
    const out = await api("/auth/oidc/complete", { method: "POST", body: { handoff } });
    if (out.token) session.save({ token: out.token, user: out.user });
    else session.saveUser(out.user);
    if (out.detail) toast(out.detail, "ok");
    if (out.intent === "signin") {
      if (out.user.email_verification_pending) {
        toast(`We've emailed a code to ${out.user.email}. Enter it in Settings to invite people and import recipes from links.`, "ok");
      }
      window.location.hash = "#/plan";
      return;
    }
    if (out.intent === "reauth") toast("Confirmed. You have ten minutes to finish what you were doing.", "ok");
    window.location.hash = "#/settings";
  } catch (error) {
    takeAfter();
    toast(error.detail || error.message, "error");
    window.location.hash = session.token ? "#/settings" : "#/login";
  }
}
