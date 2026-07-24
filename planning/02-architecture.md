# 02 — High-Level Architecture

## Shape of the system

```
                       ┌─────────────────────────────┐
                       │        User's own AI         │
                       │  (OpenClaw / Claude / etc.)  │
                       └──────┬───────────────┬──────┘
                              │ MCP           │ REST
                              ▼               ▼
┌──────────────┐        ┌─────────────────────────┐
│  iOS app     │  REST  │        Backend API       │
│  (frontend)  ├───────▶│  (single service, v1)    │
└──────────────┘        └───────────┬─────────────┘
                                    │
                              ┌─────▼─────┐
                              │ Database  │
                              └───────────┘
```

Three clients, one API, one database. The AI is *outside* the system boundary — it's just another API client with better ergonomics (MCP). That keeps the app itself LLM-free, cheap to run, and open-sourceable without anyone needing an API key to use the basics.

## Components

### Backend API (the heart)
- A single service exposing a REST API — meals, recipes, ingredients, shopping list, plan
- This is the **only** way anything touches the data. The iOS app and the AI use the same API, which forces the API to be complete and keeps AI and human views consistent
- Should be trivially self-hostable (single container + a database) since open-sourcing is the goal

> ✅ **DECIDED (Q9):** **Python / FastAPI**, following the proven pattern in `~/Documents/GitHub/podcast-manager`: FastAPI + async SQLAlchemy + Alembic migrations, one Docker image per service, deployed as a Docker Swarm stack behind Traefik with Let's Encrypt on a `*.marcuslab.uk` subdomain. That repo also contains a native iOS app living alongside the backend in the same repo — we'll mirror that monorepo layout (`backend/`, `ios/`, plus `mcp/` and `skill/`).

### Database
- Relational fits this domain well (recipes↔ingredients↔meals↔list items are all many-to-many with metadata on the joins)

> ✅ **DECIDED (Q10):** **Postgres** in its own Docker container within the stack. (podcast-manager uses SQLite, but per-user auth plus concurrent writers — iOS app, remote AI, web — justify Postgres here.)

### iOS app (frontend)

> ✅ **DECIDED (Q11):** **Native Swift/SwiftUI.** And **offline support for the shopping list is a hard requirement**: the list must be cached on-device, check-offs and ad-hoc additions must work with no signal, and changes sync when connectivity returns. Architecture implication: the list API needs to support offline-first sync — client-generated IDs, timestamps/versions on list items, and a conflict rule (last-write-wins per item is fine for a household list). The rest of the app can be online-only in v1.

### AI access layer
See [03-ai-integration.md](03-ai-integration.md). Architecturally it's: MCP server as a thin wrapper over the REST API (same process or sidecar — decide later).

## Cross-cutting concerns

### Auth (v1)
> ✅ **DECIDED (Q7):** Real per-user accounts and auth in v1 — separate identities, not shared API keys.
- Users belong to a single shared **household** in v1 (one library/plan/list); multi-household tenancy deferred (see Q16 in the decisions log)
- Human clients (iOS app): standard account login with token/session auth
- AI clients: **per-user API tokens / PATs** so an AI acts *as* a specific user — same identity model, different credential shape. This also serves the remote MCP requirement (Q15)
- podcast-manager's auth (OAuth2 sessions, encrypted token storage) is prior art for the plumbing, though identity here is our own accounts rather than Spotify's

### Exposure / networking
> ✅ **DECIDED (Q12):** Public internet in v1, using the existing homelab ingress pattern: Docker Swarm + Traefik + Let's Encrypt on a `*.marcuslab.uk` subdomain (exactly as podcast-manager does). Being public makes real auth (above) and basic hardening (rate limiting, HTTPS-only) non-optional from day one.

### Recipe parsing pipeline (where structure comes from)
Two candidate designs — **this is the biggest open architecture question**, detailed in [03-ai-integration.md](03-ai-integration.md):
1. **AI-does-the-parsing:** backend stores whatever structured recipe the user's AI submits; backend never calls an LLM. Purest BYO-AI, zero running cost, but the web UI/iOS app alone can't ingest a URL without an AI attached.
2. **Backend-does-the-parsing:** backend fetches the URL and parses it — first with the `schema.org/Recipe` JSON-LD most recipe sites embed (free, no LLM!), falling back to a user-configured LLM key for messy pages.

> ✅ **DECIDED (Q13):** **Hybrid.** The backend extracts `schema.org/Recipe` JSON-LD itself (free, no LLM, covers most recipe sites); pages without usable JSON-LD are read by the user's AI, which submits the structured recipe through the API. Same philosophy for aisle-tagging: built-in lookup table for common ingredients, AI assigns tags for unknowns.

### Open-source / freemium readiness
Decisions to make *now* that are cheap now and expensive later:
- Licence choice can wait, but keep dependencies permissively licensed
- No hard-coded single-user assumptions in the data model (a nullable `household` concept costs nothing)
- Config via environment, no homelab-specific paths baked in
- If freemium: the natural split is "self-host free / hosted+sync paid" — nothing in v1 needs to change for that, just don't couple storage to the local filesystem beyond the DB

## Explicitly deferred (don't design yet)
- Multi-*household* tenancy (per-user auth is in v1; multiple isolated households is not)
- Supermarket product-URL enrichment (F5)
- Notifications, sharing, collaborative lists
- Analytics on cooked meals
- Import from other apps (Paprika, Mealie, etc.) — though worth a look at Mealie/Tandoor as prior art before we finalise the data model
