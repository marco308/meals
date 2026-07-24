# 05 — Build Status (2026-07-24)

Traceability from the plan to what's built. Evidence: 201 backend tests (98%
coverage), 34 iOS tests, 11 MCP tests — all green — plus live verification in
the iPhone simulator against the Docker stack (including an offline round-trip
with the API container stopped).

**Legend:** ✅ built & verified · 🔷 mechanism built, deployment step pending · ⬜ deferred by decision

## F1 — Recipe ingestion & library ✅

| Plan item | Status | Where |
|---|---|---|
| Submit URL → structured recipe (no LLM, JSON-LD) | ✅ | `POST /recipes/ingest`; parses `@graph`, type lists, HowToSections, ISO8601 durations; verified live on BBC Good Food |
| Cache: same URL never re-parsed | ✅ | unique `(household, source_url)`; re-ingest returns cached, tested |
| Browse/search (title, tag, cook time) | ✅ | `GET /recipes?search=&tag=&max_total_minutes=` + iOS search |
| Manual/no-URL recipes (Q3) | ✅ | `POST /recipes`; seed's three recipes are manual |
| Edits stick, never clobbered by re-parse | ✅ | `edited` flag; re-POST of a known URL returns stored recipe unchanged (tested) |
| Messy pages → user's AI parses and submits (Q13 hybrid) | ✅ | 422 with instructions; `parse_source="ai"` submissions; MCP `submit_recipe` |

## F2 — Meal options plan ✅

| Plan item | Status | Where |
|---|---|---|
| Flat list grouped by slot, no days ever | ✅ | API, iOS Plan tab, MCP `get_plan` |
| Tap meal → recipes + full ingredient list | ✅ | single-recipe meals open the recipe directly (times, method, source link); composite meals get the aggregate view; every ingredient links to its editor |
| Mark cooked | ✅ | API idempotent; iOS swipe/buttons (full-swipe disabled — no un-cook in v1); MCP `mark_meal_cooked`; `cooked_at` stored as the future-insights hook |
| Build plan by picking from library or creating inline | ✅ | AddMealSheet + NewMealView (name, slot, recipes, loose sides) |
| Weekly-ish plans: label, archivable, copy-from (Q4) | ✅ | `POST /plans` w/ `copy_from_plan_id`, archive endpoint; iOS New plan / Past plans ("Again") / Archive menu |

## F3 — Shopping list ✅

| Plan item | Status | Where |
|---|---|---|
| Auto-populate from plan, merged where possible | ✅ | exact canonical-unit merging (Q2); provenance rows per contribution |
| Meal removal decrements, never touches ad-hoc | ✅ | tested (backend + live on Postgres) |
| Fast ad-hoc adds | ✅ | iOS quick-add, MCP `add_to_list`, idempotent client ids |
| Shopping mode: aisle-emoji sort + check-off | ✅ | store-walking order, offline-capable check-offs (Q11 hard requirement, proven end-to-end) |
| "Already have it" without deleting provenance | ✅ | exclude + reveal toggle + put-back (offline-capable) |
| Staples flag + staples check (Q5) | ✅ | hidden by default, editable per ingredient; staples-only check view with per-item "I'm low" surfacing (`staple_needed`, #4) |
| One live list, archived on completion (Q1) | ✅ | finish-shop archive; history endpoint |

## F4 — AI access layer ✅

| Plan item | Status | Where |
|---|---|---|
| Layer 1: OpenAPI REST, AI-ergonomic | ✅ | rich actionable errors, idempotent writes, bulk recipe submit, unit-convention 422s with conversions |
| Layer 2: MCP server, task-level tools | ✅ | 16 tools; stdio + streamable HTTP modes |
| Layer 3: skill + portable prompt pack (Q14) | ✅ | `skill/SKILL.md`, `skill/prompt-pack.md` |
| Acceptance scenarios (03 §use cases 1–5) | ✅ | all run live (multi-URL ingest, loose-ingredient meal, cook-tonight w/ times, aisle list, scratch-a-meal decrement) |
| Use case 6 (Ocado shop) | ⬜ | F5, deferred by decision |

## F6 — Users & auth ✅

Per-user accounts (bcrypt, hashed opaque tokens), session tokens + PATs for AI
clients (Q7), rate-limited public auth endpoints, keychain storage in iOS.
Q16 single shared household: implemented as assumed (household modelled
explicitly so multi-tenancy stays cheap later).

## Architecture (02) — cross-cutting

| Item | Status | Notes |
|---|---|---|
| FastAPI + async SQLAlchemy + Alembic, one container (Q9) | ✅ | `make dev` (Docker: Postgres+API), `make run` (SQLite, zero deps) |
| Postgres (Q10) | ✅ | compose stack; full flows verified live on Postgres |
| Native SwiftUI iOS, offline list hard requirement (Q11) | ✅ | queued ops, disk persistence, relaunch survival, ordered replay w/ id-remapping — proven with the API stopped |
| Public exposure via Swarm+Traefik on `*.marcuslab.uk` (Q12) | ✅ | **deployed 2026-07-24**: `https://meals.marcuslab.uk` (stack on the swarm manager, Postgres 17, Cloudflare-resolved TLS); `make deploy` redeploys. Registration stays open until the household registers |
| Remote MCP over HTTPS (Q15) | ✅ | the public API is live, so MCP works remotely today with `MEALS_API_URL=https://meals.marcuslab.uk` + a PAT (stdio or streamable-HTTP per instance); a single hosted multi-user MCP endpoint stays on the backlog |
| Open-source readiness | ✅ | permissive deps, env config, explicit household model, no filesystem coupling beyond the DB |

## Deferred by decision (unchanged)

Supermarket product URLs (F5, Q6 targets noted), multi-household tenancy,
pantry/stock beyond staples, cooked-meal analytics (hook stored), notifications,
sharing, imports.

## Deployed (2026-07-24)

`https://meals.marcuslab.uk` — swarm stack on the swarm manager behind Traefik
(`deploy/docker-stack.yml`, `make deploy`). Verified live: health, docs, auth
guard, TLS. Post-deploy tail (see BACKLOG.md): register the household's
accounts then set `REGISTRATION_ENABLED=false` in `~/meals-deploy/.env` on
the swarm manager and redeploy; point the iOS app at the domain; add a pg_dump backup.
The plan is now fully implemented and live.
