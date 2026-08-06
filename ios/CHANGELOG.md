# iOS build ledger

One row per `CFBundleVersion`, because a build that reaches TestFlight cannot
be recalled — the only way to undo one is to ship another. This is the answer
to "what have people actually got, and what is only on my laptop".

**App Store Connect is the source of truth**; this file is the readable copy.
Verify with the API rather than trusting a stale row. `altool` has no
`--list-builds` (it only uploads and lists *apps*), so ask the App Store
Connect API directly — `scripts/asc-builds.py` signs the ES256 JWT and prints
every build plus the version record's review state:

```bash
set -a; . ios/.env; set +a
uv run --with cryptography python ios/scripts/asc-builds.py
```

## The numbers diverged once — read this before bumping

`CFBundleVersion` and the build number App Store Connect shows are **back in
step as of build 19**, but they were not for builds 17–18: the build carrying
`CFBundleVersion: 17` is listed there as **build 18**, because a build numbered
17 that this repo did not produce got there first. Skipping 18 entirely closed
the gap — 19 uploaded as 19 and lists as 19.

What that leaves standing:

- **Never bump from the last row here.** Bump from what App Store Connect
  actually holds (step 1 below). That is how the gap opened, and skipping a
  number to close it is cheap — build numbers are free.
- **`current_ios_build` tracks `CFBundleVersion`, not the App Store Connect
  number** — it is compared against the number *inside* the installed app. For
  17 vs ASC-18 those differed; from 19 on they agree, but the rule stands.
- **Attach builds by id, not by number.** `ios/AppStore/` scripts identify the
  build by the delivery UUID `altool` prints, because matching on the number
  silently attached the wrong binary once already. The UUIDs are in the rows
  below from build 19 onward.

## The ritual when you bump a build

`CFBundleVersion` in [Meals/project.yml](Meals/project.yml) is not the only
number involved. All four steps, in order:

1. Bump `CFBundleVersion` in `ios/Meals/project.yml` to one above the highest
   build in App Store Connect — **not** one above the last row here. Uploads
   have come from outside this repo before, which is exactly how the numbering
   diverged. Check with `ios/scripts/asc-builds.py` (see the top of this file).
2. Add a row below with status **Local**, and write what's in it.
3. `make ios-testflight`. When it lands, move the row to **TestFlight**.
4. Move `current_ios_build` in `backend/app/config.py` to match, and deploy.
   That number drives the app's soft upgrade nudge; if it lags, nobody is ever
   told a newer build exists. Leave `min_ios_build` at `0` unless a change
   genuinely can't be made backwards compatible — raising it hard-blocks every
   install below it.

## Status vocabulary

| Status | Means |
|---|---|
| **Local** | Built on a laptop. Nobody else has it. Freely rewritable. |
| **TestFlight** | Uploaded. Testers can install it. Cannot be recalled. |
| **In review** | Submitted to App Review, awaiting a verdict. |
| **Rejected** | App Review returned it with issues. Still attached; resubmittable. |
| **App Store** | Public. Cannot be recalled; only superseded. |

## Builds

| Build | Version | Uploaded | Status | What's in it |
|---:|---|---|---|---|
| 23 | 1.0 | 2026-08-05 | TestFlight | **Rename by tapping the ingredient's name** (PR #41). The editor's name row now opens the fold-aware rename alert directly, with a pencil glyph as the affordance; the "Rename…" button at the foot of the screen is gone, and the merge section's footer teaches the tap. Rename itself is unchanged from build 22. iOS-only view change, no API change; any server can serve this build. Delivery UUID `c55cc542-33f6-4414-be75-2734f21c32e9`, uploaded 2026-08-05 07:43 UTC and VALID. TestFlight only: it is **not** attached to the 1.0 review, which still carries ASC-18. |
| 22 | 1.0 | 2026-08-04 | TestFlight | **Ingredient rename**, as sugar over create-and-merge (Q21): "Rename…" in the ingredient editor resolves what the typed name folds to — same row explains itself, an existing row asks before merging (its curation wins), a free name is created carrying this row's curation and the old row folds in. The editor hands over to the survivor, so further edits land on the right row. No API change; any server can serve this build. Delivery UUID `6e78a7cf-e78e-4e05-8752-ef98fd8baadc`, uploaded 2026-08-04 21:14 UTC. TestFlight only: it is **not** attached to the 1.0 review, which still carries ASC-18. |
| 21 | 1.0 | 2026-08-04 | TestFlight | **The web app's features, ported** (PR #39). New Ingredients tab: the catalogue with search, staples/verdict filters, name/aisle/verdict sorting, delete, the duplicates sweep (Q21) and manual merge from the ingredient editor. Settings grows Supermarkets & aisle order (save stores, drag their walks, pick the active one — Q2), an invites list with revoke, and AI access (mint/reveal-once/revoke API tokens). Shopping list: "sorting aisles for" switcher in the menu, Previous shops, and offline-queued delete for ad-hoc lines. Plan: rename, and past plans open read-only. Recipes: tag and under-30-min filters; meal-library rows in the add sheet gain edit/delete. All additive against the API — any server can serve this build. Delivery UUID `d294f722-8e5d-4340-9f34-4c99805affe4`, uploaded 2026-08-04 19:21 UTC. TestFlight only: it is **not** attached to the 1.0 review, which still carries ASC-18. |
| 20 | 1.0 | 2026-07-30 | TestFlight | **"Add to this week's plan" works with no active plan.** From a recipe, that tap said "Added to plan" and did nothing whenever the household had no plan: `PlanStore.addMeal` returned early on a nil plan while the recipe screen showed its success alert regardless, leaving an orphan meal in the library and nothing on the shopping list. It now resolves the plan from the server and starts one, labelled "This week's options", when there genuinely isn't one; the alert names the plan, and a real failure gets an error instead of a false success. Also fixes the same dead tap when a plan did exist but the Plan tab hadn't been opened yet that session. Only iOS changed, so any server can serve this build. Delivery UUID `ee327728-3b3b-4d98-966a-bd7fa2312f88`, uploaded 2026-07-30 07:55 UTC and VALID, listed in App Store Connect as 20, so `CFBundleVersion` and the ASC number stay in step. TestFlight only: it is **not** attached to the 1.0 review, which still carries ASC-18. |
| 19 | 1.0 | 2026-07-29 | TestFlight | **Shopping list: checked-off items leave the aisle.** Ticking something off used to leave it in place with a strikethrough, so the aisle you were standing in kept showing what was already in the trolley. It now drops out of the list into a collapsed "In the basket (N)" section at the foot of it, and one tap there puts it back in its aisle. The "Show checked-off" menu toggle is gone, replaced by that section. Also: the whole row is tappable now (the gap between the name and the quantity used to be dead space), and check-offs animate out. **18 is taken in App Store Connect, so this is 19, not 18** — the `CFBundleVersion`/ASC divergence closes here: it uploaded as 19 and App Store Connect lists it as 19. Delivery UUID `a9d80ccf-84a5-4089-91a4-023adfa9e39d`. TestFlight only — it is **not** attached to the 1.0 review, which still carries ASC-18. |
| 17 → **ASC 18** | 1.0 | 2026-07-27 | **Rejected** | **The first genuinely iPhone-only build**, submitted to App Review 2026-07-27 19:35 UTC, **rejected 2026-08-06 under guideline 2.1(a)** — see [The 2.1(a) rejection](#the-21a-rejection) below. Nothing was wrong with this binary. Builds up to here all shipped `UIDeviceFamily = [1, 2]`: `TARGETED_DEVICE_FAMILY: "1"` was set at the *project* level in `project.yml`, and xcodegen writes `"1,2"` onto every iOS target, which wins. So the app claimed iPad support it was never designed or tested for. App Store Connect noticed, and demanded 13" iPad screenshots. **Its `CFBundleVersion` is 17 but App Store Connect lists it as build 18** — see the numbering note below. |
| — (ASC 17) | 1.0 | 2026-07-26 | TestFlight | **Not built from this repo**, and not accounted for here: it appeared six minutes after build 16 and matches no upload recorded in this session. It predates the iPad fix, so treat it as iPhone+iPad and do not submit it. |
| 16 | 1.0 | 2026-07-26 | TestFlight | First build aimed at App Review, and the first at version 1.0 — so the first that can attach to the App Store record at all. Superseded before submission; **claims iPad support**. Defaults to `https://meals.marcuslab.uk` instead of localhost; login screen explains the server field and self-hosting; account settings (password, sign-out, delete) moved into a Settings screen reachable from every tab; marketing version raised 0.1 → 1.0 so the build can attach to the 1.0 App Store record. |
| 15 | 0.1 | 2026-07-25 | TestFlight | Password reset and account deletion flows in the app (decision Q20). |
| 14 | 0.1 | 2026-07-25 | TestFlight | Invite-code field on the register screen, so a second household member can join without going through the API by hand (Q19). |
| 13 | 0.1 | 2026-07-25 | TestFlight | Not recorded at the time — reconstructed from the upload date only. |
| 12 | 0.1 | 2026-07-25 | TestFlight | Not recorded at the time. |
| 11 | 0.1 | 2026-07-25 | TestFlight | Not recorded at the time. Around here: recipe photos, recipe editing, per-meal scaling, offline reads, and the client version gate. |
| 6 | 0.1 | 2026-07-25 | TestFlight | Premium/budget ingredient verdicts (Q17). |
| 5 | 0.1 | 2026-07-25 | TestFlight | Staple glyph sizing; fixed orphaned plan rows on meal delete. |
| 4 | 0.1 | 2026-07-25 | TestFlight | Recipe usage counts, deletes, meal editing, staple markers. |
| 2 | 0.1 | 2026-07-24 | TestFlight | Change-your-own-password flow. |
| 1 | 0.1 | 2026-07-24 | TestFlight | First upload — icon, export options, and the `make ios-testflight` path. |

Builds 3, 7, 8, 9 and 10 don't exist in App Store Connect. The commit
"iOS: set the build counter clear of App Store Connect" suggests the counter was
jumped past numbers already taken there, which is also why step 1 above says to
check App Store Connect rather than this file.

Rows above build 14 are reconstructed from upload dates and commit history —
this ledger did not exist yet, which is the reason it does now. Treat build 14
onwards as recorded, and anything earlier as best effort.

## App Store status

| | |
|---|---|
| App record | `com.marcuslab.meals`, App Store Connect app id `6794266229` |
| Registered name | **Yet Another Meal Planner** — the rename landed; App Review's correspondence uses it (see [AppStore/metadata.md](AppStore/metadata.md)) |
| Version record | 1.0, `REJECTED`, build ASC-18 attached |
| Review submission | `556775c4-63cc-431c-8caf-5a7e4b6339bf`, state `UNRESOLVED_ISSUES` |
| Ever submitted? | Yes — first submission 2026-07-27 19:35 UTC, rejected 2026-08-06 08:19 UTC. |
| Nothing is public yet | **No build has ever reached the App Store.** Every row above is TestFlight-only. The 1.0 record now holds a rejection with ASC-18 attached, so anything uploaded after it (build 19 onward) is testers-only until someone attaches it to a version and resubmits. Uploading to TestFlight does not touch a submission in review: `make ios-testflight` archives, exports and uploads, and nothing more. |

### The 2.1(a) rejection

Rejected 2026-08-06 for *Performance — App Completeness*, on one sentence:
"an error message is displayed when attempting to log in". Reviewed on an
**iPad Air 11-inch (M3), iPadOS 26.6** — an iPhone-only app runs there in
compatibility mode, and Apple reviews it there anyway.

**It was not an app bug, and not an iPad bug. It was the server.** The reviewer's
session is in the API log in full:

```
08:16:22 GET  /client-config      200
08:16:23 POST /auth/login         500   asyncpg ConnectionDoesNotExistError
08:19:08 rejection email
```

`create_async_engine` was built with no `pool_pre_ping` and no `pool_recycle`,
so the pool held Postgres connections that the overlay network had already
dropped. The first request to check one out died; `/client-config` survived a
second earlier only because it touches no database. Traffic is low enough that
"the first request after a quiet spell" is a real category of user, and on that
morning it was App Review. Four 500s in the preceding 24 hours, all the same
fault, all first-of-the-morning. `/healthz` touches no database either, so the
container healthcheck — the same one that gates the zero-downtime rollout —
was green throughout.

Fixed in `backend/app/database.py`; verified both ways against real Postgres by
killing the pooled connections with `pg_terminate_backend` and retrying the
login (500 before, 200 after). **The fix must be deployed before 1.0 is
resubmitted** — the binary needs no change, though attaching a current build is
free and sensible.

Two things this exposed that are worth fixing on their own schedule:

- **App Review notes were never submitted.** `appStoreReviewDetail.notes` is
  `null` on the 1.0 record, so the whole block in
  [AppStore/review-notes.md](AppStore/review-notes.md) — self-hosting, where
  account deletion lives, why 4.8 doesn't apply — never reached a reviewer.
- **The auth rate limit is a single global bucket.** `deps.auth_rate_limit`
  keys on `request.client.host`, and uvicorn only trusts forwarded headers from
  `127.0.0.1` while Traefik connects from the overlay. So every client on the
  server shares one 10/min allowance, and one person retrying a password can
  lock everyone out of logging in. Not what caused this rejection, but it is
  the same shape of thing.

The 1.0 listing metadata — name, subtitle, categories, age rating, description,
keywords, URLs, screenshots, copyright, content rights — was set through the
App Store Connect API and is reproducible from
[AppStore/metadata.md](AppStore/metadata.md). Two things the API key can't
reach, and which have to be done in the web UI: **App Privacy** and
**Pricing**.
