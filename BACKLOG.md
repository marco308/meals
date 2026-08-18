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
- [x] ~~**Postgres backups**~~ — ✅ [#9](https://github.com/marco308/meals/issues/9): a sidecar in the stack (`backup/`) dumps nightly, reads each dump back with `pg_restore --list` before counting it, keeps 7 daily + 4 weekly, and sends a gpg-encrypted copy to Google Drive. The restore was actually performed on 2026-08-17 — production dump → scratch database → API booted against it → shopping list, aisles and cooked history read back through the public API — and the steps are in [backup/README.md](backup/README.md). Staleness is a container healthcheck plus a Grafana rule over the run's log line
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
- [x] ~~**Servings scaling UI**~~ — ✅ shipped with the API it was blocked on
  ([#53](https://github.com/marco308/meals/issues/53)): the meal editor asks in
  portions when the recipe declares servings and in multiples when it doesn't,
  showing the multiplier either way
- [x] ~~**Re-ingest from the recipe page**~~ — ✅ shipped with the endpoint it
  was blocked on ([#54](https://github.com/marco308/meals/issues/54)):
  "↻ Re-read the page" on the recipe, which asks before replacing edits
- [x] ~~**Marketing site mention**~~ — ✅ `docs/` sells it now ("Web ships in
  the box: your server serves the web app itself at `/app`")

## Account lifecycle

- [x] ~~**Password reset**~~ — ✅ shipped (Q20): `POST /auth/password-reset` emails a typeable code, `POST /auth/password/reset-confirm` redeems it. Needs SMTP configured — `SMTP_HOST`, `SMTP_FROM`, and usually `SMTP_USERNAME`/`SMTP_PASSWORD` — or the endpoint returns 503 saying so, and `GET /client-config` reports which way it went as `password_reset_enabled`. **Live on the deployment since 13 Aug 2026**, relayed through Resend on the verified `marcuslab.uk` domain
- [x] ~~**Account deletion**~~ — ✅ shipped (Q20): `DELETE /auth/me`, and in the app's account menu, which is what App Store review actually requires
- [x] ~~**Household admin**~~ — ✅ built (issue [#52](https://github.com/marco308/meals/issues/52), decision Q23): `GET`/`PATCH /auth/household` (members, rename, hand over the lead), `DELETE /auth/household/members/{id}` (remove someone, or pass your own id to leave), `POST /auth/invites/redeem` so leaving isn't a one-way door. A household now has a **lead** — the member it's billed to, and the only one who can invite or remove people; everything about the food stays equal. **Deployed 2026-08-18**; iOS half is in TestFlight build 26

## Next (product tail from the plan)

- [ ] **Cooked → release ingredients** (F2 nice-to-have): marking a meal cooked optionally checks off / removes its outstanding list items
- [x] ~~**Un-cook**~~ — ✅ shipped (issue [#51](https://github.com/marco308/meals/issues/51)): `DELETE /plans/{id}/meals/{plan_meal_id}/cooked` deletes that plan-meal's `cooked_events` and re-derives, so the counts come back down for the meal and for the recipes it held *at the time* (read off the events, not the meal as it stands now). Other weeks' cookings are untouched. Web has "↶ not cooked", the MCP has `undo_meal_cooked`, and iOS turns the swipe into a toggle — **full swipe is back on**, since it was only disabled for want of an undo. iOS needs a build
- [x] ~~**Servings scaling**~~ — ✅ shipped (issue [#53](https://github.com/marco308/meals/issues/53)) server-side; the iOS half needs a build. A meal recipe line takes `servings` as well as `scale`: the server divides by the recipe's own servings, stores the multiple it already stored, and reads back `scaled_servings` (a new name — `servings` on that recipe still means the recipe's own, so nothing existing changed meaning). No migration, the column was already there. Web asks in portions when the recipe declares them and in multiples when it doesn't; iOS does the same and can finally halve a recipe, which its whole-number stepper couldn't say. iOS computes the multiple locally rather than sending `servings`, because a newer app against an older self-hosted server would have the key ignored and quietly save ×1
- [x] ~~**Archived shopping lists in iOS**~~ — ✅ shipped (issue [#56](https://github.com/marco308/meals/issues/56)) in build 21, public since build 23: "Previous shops" in the shopping list's menu, showing each finished shop's date, start and item count. That is the same depth the web app has, because `GET /shopping-list/archived` returns summaries only; showing what was actually *in* a past shop needs an endpoint neither client has
- [x] ~~**Re-parse endpoint**~~ — ✅ shipped (issue [#54](https://github.com/marco308/meals/issues/54)): `POST /recipes/{id}/reparse` re-reads a recipe from its `source_url`, in place so every meal and list line keeps pointing at it, and the active list follows the new ingredients. An edited recipe is a 409 until you send `{"force": true}`; a failed fetch or a page that lost its JSON-LD leaves the stored recipe exactly as it was. Web has the "↻ Re-read the page" button with the force confirmation; the MCP has `reparse_recipe`. iOS doesn't — it needs a build, and the issue scoped this to web
- [x] ~~**Duplicate ingredients in iOS**~~ — ✅ shipped (issue [#57](https://github.com/marco308/meals/issues/57)) in build 21, public since build 23: the Ingredients toolbar opens the duplicates sweep (groups with keeper selection, the creep-back warning, and the "old spellings" file-under), and the ingredient editor carries the manual merge for the pairs the finder won't guess at (Q21). The issue was filed after the build that closed it
- [ ] **One ingredient, two units on the list** — folding names (Q21) makes "mint" one ingredient, but "1 bunch" and "10 g" are still two lines, because merging is exact-unit-only by design (Q2). Converting bunches to grams means guessing at densities, which is the wrong fix; grouping an ingredient's lines together in the iOS list is the right one
- [x] ~~**Premium/budget browse screen in iOS**~~ — ✅ shipped (issue [#58](https://github.com/marco308/meals/issues/58)) in build 21, public since build 23: the Ingredients tab carries the badge and the household's note on every row, filters to ⭐ premium or 💷 budget, and sorts by verdict (Q17), so the verdicts read back on the phone rather than only through the API
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
- [ ] **CD** — deploys are still `make deploy` by hand. Not necessarily *from a laptop* any more: 2026-08-18 went from the Mac mini over Tailscale, so the constraint is where `deploy/deploy.sh` lives (untracked, one checkout) rather than which machine runs it. GitHub-hosted runners still can't reach the homelab, so this needs either a self-hosted runner on the swarm or a Tailscale OAuth step in the workflow
- [ ] **Image pipeline** — images are built on the swarm node by hand; a registry (or at least a pinned tag scheme) would make rollbacks sane. **The sharper problem is deploys, not rollbacks:** every image is `:latest`, so with no registry digest to resolve, the service spec never changes — `docker stack deploy` reports success and rolls *nothing*, leaving the old task serving. It takes a `docker service update --force` to cut over (confirmed 2026-08-18). A failed deploy that looks exactly like a successful one is the strongest argument here
- [ ] **Rate limiting is per-process in-memory** — fine for one replica; revisit if the API ever scales out
- [x] ~~**Structured logging**~~ — ✅ `app/observability.py`: JSON lines in production (text elsewhere), a request id on every response, one access line per request in place of uvicorn's, unhandled errors answered with a 500 that quotes the id, and named domain events (registrations, deletions, ingest outcomes, gate rejections). Stdout only — shipping the lines anywhere is the deployment's job
- [x] ~~**Metrics endpoint**~~ — ✅ phase 2: `app/metrics.py` + `GET /metrics` (Prometheus text). Request counters/latency histograms by route template, `meals_events_total` incremented by `log_event` itself, process metrics, and usage gauges (households/users/recipes) refreshed once a minute by a lifespan task. Guarded by `METRICS_TOKEN` (404 when unset) because Traefik-style deployments route every path on the host to the API
- [x] ~~**Log shipping + dashboards**~~ — ✅ phase 3, live since 2026-08-13: Loki + Grafana Alloy (tailing every container on both nodes) + Prometheus scraping `/metrics` with `METRICS_TOKEN`, a postgres-exporter, dashboards for the API and the database, and email alerts on API down, a 5xx burst, Postgres down, node disk, and backups going stale. **None of it is in this repo and that's the point** — it's a description of specific hardware, so it lives with the deployment config alongside `deploy/`, exactly like the swarm stack does. What this repo owes it is the thing it already does: emit records worth shipping, on stdout, with a name
- [x] ~~**Ingredient-line parser tail**~~ — ✅ the regex learned the `<n> <food> <unit>` shape (Q21): "3 garlic cloves" is now 3 cloves of garlic rather than ×3 of an ingredient called "garlic cloves", except where the last word is load-bearing ("2 bay leaves")
- [ ] **The Drive backup credential is on borrowed time** — the offsite copy authenticates with rclone's *shared* Google OAuth client id, which Google is retiring during 2026 (rclone says so on every run). Uploads will start failing when it goes; local dumps are unaffected and the backup-failed alert catches it, so this is a chore with a deadline rather than a risk. The fix is a client id of one's own — see [backup/README.md](backup/README.md)
- [x] ~~**Ingestion has no response-size cap**~~ — ✅ shipped (issue [#55](https://github.com/marco308/meals/issues/55)): `fetch_page` streams and stops at `recipe_fetch_max_bytes` (5 MB), refusing with the usual actionable 422. A declared `Content-Length` is a fast reject, the running total is the real guard, and redirect hops stream too
- [x] ~~**Flaky provision password test**~~ — ✅ fixed by asserting on the alphabet rather than on one draw from it: `8` is gone from `PASSWORD_ALPHABET`, and the test now checks that `PASSWORD_ALPHABET` and `AMBIGUOUS_CHARACTERS` are disjoint, which a re-introduced look-alike can't slip past
- [ ] **Demo/test data hygiene in prod** — if a smoke-test account was used during deploy verification, remove it. Since Q19 a stray registration lands in its own empty household rather than the family's, so this is now tidiness rather than exposure; the Apple Review account made by `python -m app.provision` is the deliberate version of the same thing
