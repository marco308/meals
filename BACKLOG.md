# Backlog

What's *not* done, in rough priority order. The plan itself is fully built —
see [planning/05-status.md](planning/05-status.md) — so this is the deferred
tail plus engineering debt worth naming.

**This file is not the tracker.** Anything actionable enough for someone else
to pick up lives in
[GitHub issues](https://github.com/marco308/meals/issues) and is linked from
here, not restated. What stays in this file is the reasoning: what was
deliberately *not* built, and why the debt that is still here is tolerable.
An entry that grows a plan of attack has outgrown the file.

## Now

- [x] ~~**Deploy to the homelab**~~ — ✅ live at `https://meals.marcuslab.uk` (2026-07-24; `make deploy`, stack in the gitignored local `deploy/`)
- [x] ~~**Close registration**~~ — ✅ done twice over: decision Q19 means a registration creates its own household rather than joining this one, and `REGISTRATION_ENABLED=false` is set on the deployment as well, so new households are refused outright. Invite codes are still honoured, which is how the next household member gets in
- [ ] **Postgres backups** — [#9](https://github.com/marco308/meals/issues/9). Still no automation, no off-node copy, and no restore ever attempted
- [x] ~~**Point the iOS app at the domain**~~ — ✅ `https://meals.marcuslab.uk` is now the default a fresh install starts on (build 16), not `localhost`. Offline sync over the real network still wants a proper walk round a supermarket
- [x] ~~**First App Store submission**~~ — ✅ **1.0 is live on the App Store**, carrying build 23, approved 2026-08-12. Submitted 2026-07-27, rejected 2026-08-06 under guideline 2.1(a) for a server fault (stale pooled connections), fixed, replied to in Resolution Center and resubmitted the same day. The record is renamed to "Yet Another Meal Planner". Full state in [ios/AppStore/README.md](ios/AppStore/README.md)
- [x] ~~**Deploy Q19**~~ — ✅ deployed 2026-07-25; migration `d9e4b17c3a86` applied, and the existing account kept its household as intended (nothing migrated)
- [ ] **Onboard the second household member** — no longer needs curl at either end: build 16 mints the code (Settings → Invite someone) and build 14 onwards can redeem it on the register screen. Both are on TestFlight, so this is now just a thing to do
- [ ] **Clean the BBC misparse junk out of prod** — dual-measure lines left ingredients named "/3½oz vermicelli rice noodles", "/10½oz cooked" and friends behind (parser fixed + guarded `DELETE /ingredients/{id}` added 2026-07-29, Q22; summer_rolls_15105 is a known affected recipe). The recipe lines kept their raw text and correct metric quantities, so merging each junk row into the real food (`POST /ingredients/{keeper_id}/merge`) heals the recipes too; `DELETE /ingredients/{id}` mops up anything left unreferenced. An AI on the v9 playbook can do the whole sweep

## Web app tail

The web client (`web/`, served at `/app`) shipped covering the whole loop;
what it deliberately doesn't do yet:

- [ ] **Offline** — it's online-only by design (the iPhone in the supermarket
  is the offline story; the web app is the kitchen/desk screen). If that ever
  changes, the `PendingOp` queue semantics from iOS (Q11) are the model
- [ ] **Servings scaling UI** — same gap as iOS; the API has no first-class
  support yet (see "Servings scaling" below), the meal editor only exposes the
  per-recipe `scale` factor
- [ ] **Re-ingest from the recipe page** — blocked on the same "Re-parse
  endpoint" item below
- [x] ~~**Marketing site mention**~~ — ✅ `docs/` sells it now ("Web ships in
  the box: your server serves the web app itself at `/app`")

## Account lifecycle

- [x] ~~**Password reset**~~ — ✅ shipped (Q20): `POST /auth/password-reset` emails a typeable code, `POST /auth/password/reset-confirm` redeems it. Needs SMTP configured — `SMTP_HOST`, `SMTP_FROM`, and usually `SMTP_USERNAME`/`SMTP_PASSWORD` — or the endpoint returns 503 saying so, and `GET /client-config` reports which way it went as `password_reset_enabled`. **Live on the deployment since 13 Aug 2026**, relayed through Resend on the verified `marcuslab.uk` domain
- [x] ~~**Account deletion**~~ — ✅ shipped (Q20): `DELETE /auth/me`, and in the app's account menu, which is what App Store review actually requires
- [ ] **Household admin** — inviting someone is now a button (Settings → Invite someone), but that's still all: no rename after signup, no way to leave a household without deleting your account, no way to remove someone you invited by mistake, no list of who's in it, and the iOS register screen can't set `household_name` (it takes the "Home" default)

## Next (product tail from the plan)

- [ ] **Cooked → release ingredients** (F2 nice-to-have): marking a meal cooked optionally checks off / removes its outstanding list items
- [ ] **Un-cook** — no way to undo a mistaken "cooked" today (v1 accepted this; it already bit us once). Now also wrong in the cooked history: a mis-tap permanently inflates `times_cooked`. The fix is to delete the `cooked_events` rows for that plan-meal and re-derive the counters (`app/services/cooking.py` already recomputes rather than increments)
- [ ] **Servings scaling** — scale a recipe's quantities when adding to a meal ("×2 for batch cooking"); skill/prompt pack tells AIs to confirm scaling, the API has no first-class support
- [ ] **Archived shopping lists in iOS** — API has `GET /shopping-list/archived`; no screen for "what did we buy last week"
- [ ] **Re-parse endpoint** — refresh a cached recipe from its URL on demand (edits win; needs an explicit force flag)
- [ ] **Duplicate ingredients in iOS** — `GET /ingredients/duplicates` and the merge endpoint clean the catalogue up (Q21), and the MCP exposes both, but the app has no screen for it: today the tidy-up only happens if you ask an AI
- [ ] **One ingredient, two units on the list** — folding names (Q21) makes "mint" one ingredient, but "1 bunch" and "10 g" are still two lines, because merging is exact-unit-only by design (Q2). Converting bunches to grams means guessing at densities, which is the wrong fix; grouping an ingredient's lines together in the iOS list is the right one
- [ ] **Premium/budget browse screen in iOS** — `GET /ingredients?value_tier=premium` and the MCP `list_ingredients_by_value` read the tagged set back (Q17); the app only shows a tier on ingredients you happen to open
- [ ] **iOS offline breadth** — plan and recipe library are online-only by design (Q11); cache read-only copies so the whole app opens signal-less
- [x] ~~**Remote MCP multi-user auth**~~ — ✅ shipped (issue #6): the stack serves streamable HTTP at `https://meals.marcuslab.uk/mcp` and forwards each caller's own bearer PAT per request; stdio stays for local dev
- [ ] **MCP OAuth** — [#8](https://github.com/marco308/meals/issues/8). claude.ai custom connectors want OAuth; the remote MCP is bearer-header-only today

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
- [x] ~~**Ingredient-line parser tail**~~ — ✅ the regex learned the `<n> <food> <unit>` shape (Q21): "3 garlic cloves" is now 3 cloves of garlic rather than ×3 of an ingredient called "garlic cloves", except where the last word is load-bearing ("2 bay leaves")
- [ ] **Ingestion has no response-size cap** — `fetch_page` reads the whole body into memory with only the 15s timeout as a bound; a pathological page is a memory spike. Wants a streamed read with a byte ceiling (a few MB covers any real recipe page)
- [x] ~~**Flaky provision password test**~~ — ✅ fixed by asserting on the alphabet rather than on one draw from it: `8` is gone from `PASSWORD_ALPHABET`, and the test now checks that `PASSWORD_ALPHABET` and `AMBIGUOUS_CHARACTERS` are disjoint, which a re-introduced look-alike can't slip past
- [ ] **Demo/test data hygiene in prod** — if a smoke-test account was used during deploy verification, remove it. Since Q19 a stray registration lands in its own empty household rather than the family's, so this is now tidiness rather than exposure; the Apple Review account made by `python -m app.provision` is the deliberate version of the same thing
