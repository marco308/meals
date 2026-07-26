# Changelog

What has shipped, and what has only been written.

This repo ships two things on two different clocks, so they get two changelogs:

- **The server** (backend API, MCP server, skill and prompt pack) ships when
  `make deploy` runs. Its releases are the dated sections below, and "released"
  means *live on the deployment*, not *merged to main*.
- **The iOS app** ships one build at a time and can never be recalled once it
  reaches TestFlight, so every build gets a row in
  [ios/CHANGELOG.md](ios/CHANGELOG.md) recording where it got to.

Anything merged but not yet deployed lives under **Unreleased**. If you are
about to deploy, that section is the release note.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
The API contract is additive-only (see CLAUDE.md), so **Removed** and
**Changed** entries for response fields should be rare enough to be alarming.

## Unreleased

### Added

- `GET /privacy` and `GET /support` — the App Store requires publicly reachable
  policy and support URLs, and the deployment is the only thing this project
  already hosts. Unauthenticated, exempt from the client gate, and rendered
  from `PRIVACY.md` / `SUPPORT.md` in the repo so the published page can't
  drift from the one GitHub shows.
- An in-app invite flow (Settings → Invite someone). `POST /auth/invites` has
  existed since Q19 but only through the API, which left "get your partner onto
  your server" as a curl command.
- App Store submission material under `ios/AppStore/`: listing copy, App Review
  notes, the App Privacy questionnaire answers, and a submission checklist.
- `make ios-screenshots` — boots a simulator against a locally seeded API and
  captures the App Store screenshot set through a UI test, so the shots can be
  regenerated instead of re-staged by hand.
- `python -m app.provision` — creates one account, in a **new and empty
  household**, on a server whose registration is closed. Written for the Apple
  Review account: a reviewer has to be able to sign in and use the app, and must
  not see a real household's data while doing it. An invite would have done the
  opposite.
- `app.seed` now honours `SEED_EMAIL` / `SEED_PASSWORD`, so the demo content can
  fill an account that already exists rather than only creating its own.
- CI now checks `/privacy` and `/support` against the built image. They render
  markdown that is COPYed into the image separately from `app/`, so forgetting
  them is a live App Store listing pointing at a 404.

### Changed

- The iOS app now defaults to `https://meals.marcuslab.uk` rather than
  `http://localhost:8000`. A public download that opens on a dead localhost URL
  looks broken; the field is still editable, which is the whole point of a
  self-hosted client.
- The login screen says what the server field is for and that self-hosting is
  the supported route, instead of presenting an unexplained URL box.
- Account settings (password, sign-out, deletion) moved out of the Plan tab's
  overflow menu into a Settings tab. App Review expects account deletion to be
  findable, and buried in a plan menu it was findable by neither them nor a user.
- iOS marketing version 0.1 → **1.0**, build 15 → **16**, and
  `current_ios_build` with it. A build can only attach to an App Store version
  record whose version string it matches, which makes every 0.1 build
  TestFlight-only forever.

## 2026-07-25 — multi-household tenancy, account lifecycle

Deployed to `meals.marcuslab.uk`. Migration `d9e4b17c3a86`; the existing
account kept its household, and nothing was migrated between households.

### Added

- **Multiple households on one server** (decision Q19). `POST /auth/register`
  now creates a new, empty household; joining an existing one requires a
  single-use code from `POST /auth/invites`. `household_id` scoping was already
  enforced on every query, which is what made this a small change.
- **Password reset** (Q20): `POST /auth/password-reset` emails a typeable code,
  `POST /auth/password/reset-confirm` redeems it. Reset codes live in
  `auth_tokens` under `kind="reset"` and are barred from authenticating by the
  `deps.AUTHENTICATING_KINDS` allow-list.
- **Account deletion** (Q20): `DELETE /auth/me`. Deleting the last member of a
  household deletes that household's data; deleting anyone else deletes only
  them.
- **Remote MCP with per-caller auth**: streamable HTTP at `/mcp`, forwarding
  each request's own bearer token to the API. The MCP server holds no
  credentials of its own in http mode.
- **Client version gate** (`app/client_gate.py`): `GET /client-config`
  publishes `min_ios_build` and `current_ios_build`; builds below the floor get
  a 426. `/shopping-list*` is permanently exempt so a blocked app can still
  drain its offline queue.
- **Premium vs budget ingredient verdicts** (Q17), a 🧼 Toiletries aisle, recipe
  photos, recipe editing, per-meal scaling, and offline reads for plan and
  recipe views.
- CI (`.github/workflows/ci.yml`): lint, tests, Alembic against real Postgres,
  and a `docker compose` boot with an endpoint smoke test.

### Fixed

- Two Alembic heads that took the API down, plus a test that fails if it
  happens again.
- Orphaned plan rows when a meal was deleted.

### Security

- `REGISTRATION_ENABLED=false` set on the deployment. Invite codes are still
  honoured, so a closed server can still admit the people it chose.
- Relicensed AGPL-3.0 with an `ios/` carve-out for App Store distribution.

## 2026-07-24 — first deployment

`https://meals.marcuslab.uk` went live: swarm stack behind Traefik, Postgres
17, Cloudflare-resolved TLS. Moved to the `zaphod` worker node on 2026-07-25.

### Added

- The whole of F1–F4 and F6: recipe ingestion from schema.org JSON-LD, the
  recipe library, meal-options plans (pools, never calendars), the
  provenance-tracked aisle-sorted shopping list, per-user auth with PATs, the
  MCP server, and the published skill and prompt pack.
- Native SwiftUI iOS app with the offline-first shopping list (decision Q11),
  proven end to end with the API stopped.
