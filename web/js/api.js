// The API client. The web app is served by the API itself, so every call is
// same-origin ("../" from /app/) and there is no base URL to configure.
// Bearer token in localStorage — same trust model as the iOS keychain: this
// is your own server, and the app ships no third-party script that could
// read it (the CSP in index.html enforces that).

const TOKEN_KEY = "yamp.token";
const USER_KEY = "yamp.user";

// Matches app/client_gate.py's header shape. Platform "web" is never gated
// (only ios/* is), but identifying ourselves keeps server logs honest.
const CLIENT_HEADER = "web/1.0 (1)";

export const session = {
  get token() {
    return localStorage.getItem(TOKEN_KEY);
  },
  get user() {
    try {
      return JSON.parse(localStorage.getItem(USER_KEY));
    } catch {
      return null;
    }
  },
  save(auth) {
    localStorage.setItem(TOKEN_KEY, auth.token);
    localStorage.setItem(USER_KEY, JSON.stringify(auth.user));
  },
  saveUser(user) {
    localStorage.setItem(USER_KEY, JSON.stringify(user));
  },
  clear() {
    localStorage.removeItem(TOKEN_KEY);
    localStorage.removeItem(USER_KEY);
  },
};

export class ApiError extends Error {
  constructor(status, detail) {
    super(detail);
    this.status = status;
    this.detail = detail;
  }
}

// FastAPI's 422 validation detail is a list of {loc, msg}; everything else in
// this API is a deliberate sentence written to be shown to the caller.
function detailText(data, status) {
  const detail = data?.detail;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return detail
      .map((e) => {
        const field = (e.loc || []).filter((p) => p !== "body").join(".");
        const msg = (e.msg || "").replace(/^Value error,\s*/i, "");
        return field ? `${field}: ${msg}` : msg;
      })
      .join("\n");
  }
  return `request failed (${status})`;
}

function headers(extra) {
  const out = { "X-Meals-Client": CLIENT_HEADER, ...extra };
  if (session.token) out["Authorization"] = `Bearer ${session.token}`;
  return out;
}

export async function api(path, { method = "GET", body, query } = {}) {
  const url = new URL(".." + path, window.location.href);
  for (const [key, value] of Object.entries(query || {})) {
    if (value !== undefined && value !== null && value !== "") url.searchParams.set(key, value);
  }

  const requestHeaders = headers(body === undefined ? {} : { "Content-Type": "application/json" });

  const response = await fetch(url, {
    method,
    headers: requestHeaders,
    body: body === undefined ? undefined : JSON.stringify(body),
  });

  if (response.status === 204) return null;
  const data = await response.json().catch(() => null);

  if (!response.ok) {
    // A 401 usually means the session token expired or was revoked → back to
    // the login screen. But three endpoints answer 401 for a *typed* password
    // being wrong, which must not sign anyone out.
    const typedPassword =
      path === "/auth/login" || path === "/auth/password" || (path === "/auth/me" && method === "DELETE");
    if (response.status === 401 && session.token && !typedPassword) {
      session.clear();
      window.location.hash = "#/login";
    }
    throw new ApiError(response.status, detailText(data, response.status));
  }
  return data;
}

// A file, not a payload: /household/export streams a whole household and
// arrives as a download. The bearer token has to travel in a header — never in
// a URL, which /privacy is a promise about — so the browser cannot simply be
// pointed at the endpoint. We fetch it, wrap the body in a blob, and click our
// own link. The CSP needs no `blob:` source for that — a download is not a
// fetch — and `connect-src 'self'` already covers the request itself.
export async function download(path) {
  const response = await fetch(new URL(".." + path, window.location.href), { headers: headers() });

  if (!response.ok) {
    const data = await response.json().catch(() => null);
    if (response.status === 401 && session.token) {
      session.clear();
      window.location.hash = "#/login";
    }
    throw new ApiError(response.status, detailText(data, response.status));
  }

  // The server names the file (Content-Disposition), because it knows the date
  // it assembled and we would only be guessing it.
  const named = /filename="?([^";]+)"?/i.exec(response.headers.get("Content-Disposition") || "");
  const href = URL.createObjectURL(await response.blob());
  const link = document.createElement("a");
  link.href = href;
  link.download = named ? named[1] : path.slice(path.lastIndexOf("/") + 1);
  document.body.append(link);
  link.click();
  link.remove();
  // Revoking straight away cancels the save in some browsers; the tab keeping
  // one blob alive for a minute is the cheaper mistake.
  setTimeout(() => URL.revokeObjectURL(href), 60_000);
}

let aisleCache = null;
export async function aisles() {
  aisleCache ||= await api("/aisles");
  return aisleCache;
}

// The order /aisles returns follows the active supermarket, so anything that
// changes supermarkets (settings, the shopping-page switcher) must drop the
// cache or the ingredients screen keeps grouping by the old walk.
export function invalidateAisles() {
  aisleCache = null;
}
