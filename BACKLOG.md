# Backlog

What's *not* done, in rough priority order. The plan itself is fully built —
see [planning/05-status.md](planning/05-status.md) — so this is the deferred
tail plus engineering debt worth naming.

## Now

- [x] ~~**Deploy to the homelab**~~ — ✅ live at `https://meals.marcuslab.uk` (2026-07-24; `make deploy`, stack in `deploy/`)
- [ ] **Close registration** — the API is public with `REGISTRATION_ENABLED=true`; once the household's accounts exist, set it to `false` in `~/meals-deploy/.env` on the swarm manager and `make deploy`
- [ ] **Postgres backups** — nightly `pg_dump` from the `meals_db` service into the swarm manager `~/backups`
- [ ] **Point the iOS app at the domain** — set the server field to `https://meals.marcuslab.uk`, register, re-test offline sync over the real network

## Next (product tail from the plan)

- [ ] **Cooked → release ingredients** (F2 nice-to-have): marking a meal cooked optionally checks off / removes its outstanding list items
- [ ] **Un-cook** — no way to undo a mistaken "cooked" today (v1 accepted this; it already bit us once)
- [ ] **Servings scaling** — scale a recipe's quantities when adding to a meal ("×2 for batch cooking"); skill/prompt pack tells AIs to confirm scaling, the API has no first-class support
- [ ] **Archived shopping lists in iOS** — API has `GET /shopping-list/archived`; no screen for "what did we buy last week"
- [ ] **Re-parse endpoint** — refresh a cached recipe from its URL on demand (edits win; needs an explicit force flag)
- [ ] **iOS offline breadth** — plan and recipe library are online-only by design (Q11); cache read-only copies so the whole app opens signal-less
- [ ] **Remote MCP multi-user auth** — today each user runs the MCP server with their own PAT (env var); proper per-request auth passthrough would let one hosted MCP endpoint serve the household

## Later (explicitly deferred in the plan)

- [ ] **F5 supermarket integration** — per-ingredient `(supermarket, product_url)` pairs for Sainsbury's / Ocado / M&S (Q6); enables the "do the Ocado shop" AI flow (use case 6)
- [ ] **Multi-household tenancy** — the freemium split; household is already modelled, so this is auth + scoping work, not a data-model rewrite
- [ ] **Cooked-meal analytics** — "what do we actually eat"; `cooked_at` is already stored
- [ ] **Notifications / sharing / collaborative lists**
- [ ] **Imports** (Paprika, Mealie, Tandoor)

## Engineering debt

- [ ] **CI** — no pipeline; `make test` / `make ios-test` / mcp tests run locally only
- [ ] **Image pipeline** — images are built on the swarm node by hand; a registry (or at least a pinned tag scheme) would make rollbacks sane
- [ ] **Rate limiting is per-process in-memory** — fine for one replica; revisit if the API ever scales out
- [ ] **Ingredient-line parser tail** — "1 garlic clove crushed" style lines parse with the container word in the name; the AI-cleanup path covers it, but the regex could learn the `<n> <food> <unit>` shape
- [ ] **Demo/test data hygiene in prod** — if a smoke-test account was used during deploy verification, remove it (single shared household means it sees real data)
