# Backlog

What's *not* done, in rough priority order. The plan itself is fully built —
see [planning/05-status.md](planning/05-status.md) — so this is the deferred
tail plus engineering debt worth naming.

## Now

- [x] ~~**Deploy to the homelab**~~ — ✅ live at `https://meals.marcuslab.uk` (2026-07-24; `make deploy`, stack in the gitignored local `deploy/`)
- [x] ~~**Close registration**~~ — ✅ superseded by decision Q19: registering creates a *new* household, so an open `/auth/register` no longer exposes this household's data. **This branch must be deployed for that to be true** — the live instance runs the pre-Q19 code until it is. `REGISTRATION_ENABLED=false` is now optional (it blocks new households but still honours invites)
- [ ] **Postgres backups** — nightly `pg_dump` from the `meals_db` service onto the swarm manager. Still nothing. Now that the repo is public and other people may self-host, worth writing up rather than just doing
- [ ] **Point the iOS app at the domain** — set the server field to `https://meals.marcuslab.uk`, register, re-test offline sync over the real network
- [ ] **Deploy Q19 and re-invite the household** — after this lands, existing accounts keep the household they're already in (nothing migrates), but the *second* person to join a fresh install now needs an invite. Issue one from `POST /auth/invites`

## Account lifecycle (prerequisites, not polish)

Both of these are App Store review requirements for an app that creates
accounts, and both are needed before anyone but us could reasonably pay for
hosting. Neither exists today.

- [ ] **Password reset** — there is no email path at all: `POST /auth/password` needs the *current* password, so a forgotten one is unrecoverable without database access. Needs an email sender, which the stack currently has none of
- [ ] **Account deletion** — no `DELETE /auth/me`. Apple requires in-app deletion. Non-trivial here: deleting the last user of a household should take the household's data with it, and deleting a user who contributed shopping-list sources must not corrupt `ListItemSource` provenance
- [ ] **Household admin** — you can create a household and invite into it, and that's all: no rename after signup, no way to leave, no way to remove someone you invited by mistake, and the iOS register screen can't set `household_name` (it takes the "Home" default). `GET /auth/me` now returns `household_id`/`household_name`, so the app has what it needs to show which household you're in

## Next (product tail from the plan)

- [ ] **Cooked → release ingredients** (F2 nice-to-have): marking a meal cooked optionally checks off / removes its outstanding list items
- [ ] **Un-cook** — no way to undo a mistaken "cooked" today (v1 accepted this; it already bit us once). Now also wrong in the cooked history: a mis-tap permanently inflates `times_cooked`. The fix is to delete the `cooked_events` rows for that plan-meal and re-derive the counters (`app/services/cooking.py` already recomputes rather than increments)
- [ ] **Servings scaling** — scale a recipe's quantities when adding to a meal ("×2 for batch cooking"); skill/prompt pack tells AIs to confirm scaling, the API has no first-class support
- [ ] **Archived shopping lists in iOS** — API has `GET /shopping-list/archived`; no screen for "what did we buy last week"
- [ ] **Re-parse endpoint** — refresh a cached recipe from its URL on demand (edits win; needs an explicit force flag)
- [ ] **Premium/budget browse screen in iOS** — `GET /ingredients?value_tier=premium` and the MCP `list_ingredients_by_value` read the tagged set back (Q17); the app only shows a tier on ingredients you happen to open
- [ ] **iOS offline breadth** — plan and recipe library are online-only by design (Q11); cache read-only copies so the whole app opens signal-less
- [x] ~~**Remote MCP multi-user auth**~~ — ✅ shipped (issue #6): the stack serves streamable HTTP at `https://meals.marcuslab.uk/mcp` and forwards each caller's own bearer PAT per request; stdio stays for local dev
- [ ] **MCP OAuth** — claude.ai custom connectors authenticate via OAuth, not custom headers; the remote MCP is bearer-header-only today (Claude Code and header-capable clients work)

## Later (explicitly deferred in the plan)

- [ ] **F5 supermarket integration** — per-ingredient `(supermarket, product_url)` pairs for Sainsbury's / Ocado / M&S (Q6); enables the "do the Ocado shop" AI flow (use case 6)
- [x] ~~**Multi-household tenancy**~~ — ✅ shipped with decision Q19: registration creates its own household, `POST /auth/invites` admits people to an existing one. What's left is the *product* on top, not the tenancy: no way to leave a household, remove a member, rename one after signup, or move data between them
- [ ] **Cooked-meal analytics** — "what do we actually eat"; the `cooked_events` log and `times_cooked` / `last_cooked_at` counters landed with issue #13, so what's left is the reporting on top (per-month breakdowns, "you always cook this on the week you shop late", etc.)
- [ ] **Notifications / sharing / collaborative lists**
- [ ] **Imports** (Paprika, Mealie, Tandoor)

## Engineering debt

- [x] ~~**CI**~~ — ✅ GitHub Actions (`.github/workflows/ci.yml`): lint, tests, migrations against real Postgres, and a `docker compose` boot + endpoint smoke on every PR
- [ ] **CI for iOS** — deliberately skipped: macOS runners are billed per minute even on public repos, so `make ios-build` / `make ios-test` stay local. Revisit when the repo is open sourced
- [ ] **CD** — deploys are still `make deploy` from a laptop. GitHub-hosted runners can't reach the homelab, so this needs either a self-hosted runner on the swarm or a Tailscale OAuth step in the workflow
- [ ] **Image pipeline** — images are built on the swarm node by hand; a registry (or at least a pinned tag scheme) would make rollbacks sane
- [ ] **Rate limiting is per-process in-memory** — fine for one replica; revisit if the API ever scales out
- [ ] **Ingredient-line parser tail** — "1 garlic clove crushed" style lines parse with the container word in the name; the AI-cleanup path covers it, but the regex could learn the `<n> <food> <unit>` shape
- [ ] **Demo/test data hygiene in prod** — if a smoke-test account was used during deploy verification, remove it (single shared household means it sees real data)
