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

export async function api(path, { method = "GET", body, query } = {}) {
  const url = new URL(".." + path, window.location.href);
  for (const [key, value] of Object.entries(query || {})) {
    if (value !== undefined && value !== null && value !== "") url.searchParams.set(key, value);
  }

  const headers = { "X-Meals-Client": CLIENT_HEADER };
  if (session.token) headers["Authorization"] = `Bearer ${session.token}`;
  if (body !== undefined) headers["Content-Type"] = "application/json";

  const response = await fetch(url, {
    method,
    headers,
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

let aisleCache = null;
export async function aisles() {
  aisleCache ||= await api("/aisles");
  return aisleCache;
}
