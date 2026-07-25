# 04 — Decisions Log

All questions answered. This is the decision record; every decision below is implemented — see [05-status.md](05-status.md) for traceability (Q12/Q15 public exposure: mechanism built, homelab deploy pending).

## Product shape

**Q1 — Shopping lists: one live list** for now. Archive on completion; multiple named lists is a maybe-later.

**Q2 — Quantities & merging: the writing client normalises (option c), against a strict convention.**
Every quantity submitted to the API must be either:
- **metric** (g, kg, ml, l), or
- **a count of a natural unit** ("2 tins", "3 cloves", "1 bunch", "6 items")

No cups, oz, sticks, etc. — the AI (or human) converts before writing. The backend then only merges exact-matching units, which stays simple and predictable. The published skill/prompt pack (Layer 3) carries the conversion rules.

**Q3 — Manual (no-URL) recipes: yes, in v1.** Title + ingredients minimum; steps optional.

**Q4 — Plans are weekly-ish entities.** A plan has a loose label/date-range ("w/c 20 July"), can be archived, and a new plan can be started by copying a previous one ("same as two weeks ago"). Still no per-day/per-meal scheduling inside a plan — just grouped options.

**Q5 — Staples flag: yes.** Ingredients flagged as staples are excluded from the shopping list by default, with a toggle to reveal them for a **"staples check"** before shopping.

**Q6 — Supermarkets (for later F5): Sainsbury's, Ocado, M&S.**

**Q7 — Separate identities from day one.** Real per-user accounts and auth are **in v1** (not the static-API-key shortcut). Working assumption: multiple user accounts sharing one household's data (one recipe library, one plan, one list).
> ✅ **RESOLVED (Q16, 2026-07-24):** Implemented as assumed — all v1 users share one household (one library, plan, list). The household is modelled explicitly (own table, FKs everywhere) so multi-household tenancy stays a cheap future change.
> ⚠️ **AMENDED by Q19 (2026-07-25):** how you *join* a household changed. Users still share one household's data; registration no longer puts you in someone else's.

**Q8 — MVP cut: approved**, amended by Q7 (auth/user accounts promoted into v1).

## Infrastructure & access

**Q9 — Stack: Python / FastAPI**, following the podcast-manager pattern (FastAPI, async SQLAlchemy, Alembic migrations, Docker image per service).

**Q10 — Database: Postgres** in its own Docker container (departure from podcast-manager's SQLite — justified by multi-user auth and concurrent AI + app writes).

**Q11 — Frontend: native Swift/SwiftUI iOS app.** **Offline support for the shopping list is a hard requirement** — check-off must work in a supermarket with no signal and sync when back online.

**Q12 — Public internet exposure: yes**, via the existing homelab ingress (Docker Swarm + Traefik + Let's Encrypt on a `*.marcuslab.uk` subdomain), same pattern as podcast-manager.

**Q13 — Recipe parsing: hybrid.** Backend extracts `schema.org/Recipe` JSON-LD itself (no LLM, covers most sites); pages without usable JSON-LD are parsed by the user's AI, which submits the structured recipe via the API.

## AI layer

**Q14 — Target AI: any LLM.** No single-assistant favouritism. Priorities: (1) clean OpenAPI-documented REST API usable by anything, (2) MCP server for the growing set of MCP-capable assistants, (3) a portable "prompt pack" version of the skill so non-MCP/non-Claude AIs get the same playbook.

**Q15 — Remote MCP in v1: yes.** MCP served over HTTP(S) at the public endpoint with per-user auth tokens, so cloud-hosted AIs can connect. (Local/stdio use still works against the same server.)

## Newly raised

- **Q16** (above, under Q7): ✅ resolved — implemented as assumed, then amended by Q19.

**Q17 — Premium vs budget, per ingredient: yes** (2026-07-25, inspired by the
blind premium-vs-budget tastings). Ingredients carry a `value_tier` —
`premium` (⭐ worth paying up for), `budget` (💷 own-brand is fine) or `any`
(no opinion, the default) — plus a one-line `value_note` reason. Decisions:

- It hangs off the **ingredient**, not the recipe or the list line, so the
  verdict is decided once and applies everywhere that ingredient appears.
- It is **never guessed** — unlike an aisle, "is the posh one worth it" is a
  household's own taste and budget. No keyword table; the user or their AI
  sets it, and the skill tells AIs to suggest but not assume.
- The tier and note ride along on shopping-list items and recipe lines, so the
  advice appears at the shelf rather than in a settings screen.
- No price tracking, no supermarket-specific products: that's still F5.

**Q18 — Servings scaling, per meal-recipe link: yes** (2026-07-25, issue #32 —
batch cooking for the freezer). A recipe can sit in a meal at a multiple of its
own quantities. Decisions:

- The factor lives on the **meal↔recipe link** (`meal_recipes.scale`), not on
  the recipe (it would leak into every other meal) and not on the meal (a meal
  can be "×2 the curry, ×1 the rice").
- It is a **factor, not a target servings count**. `Recipe.servings` is
  nullable, so an ingested recipe without it could not derive one; a servings
  target is a client-side convenience where servings is known.
- Loose ingredients are **not** scaled — they're already stated as the absolute
  amount the meal needs.
- **Stored exact, rounded up only for display on the shopping list**: a half
  tin is not buyable, so the list shows "1 tin", but the stored quantity stays
  the exact sum of its sources — otherwise two meals each needing half a tin
  would buy two. Recipe lines are never rounded: that's what you cook.
- The API stays additive: `recipe_ids` keeps working and means ×1; the new
  `recipes: [{recipe_id, scale}]` carries the factor. Sending both is a 422
  rather than a silent winner.
- The scale is captured on the `cooked_event`, because "what we actually
  cooked" includes how much of it.

**Q19 — Registration creates a new household; joining one needs an invite: yes**
(2026-07-25, prompted by opening the repo to the public). This **amends Q7/Q16**,
which had registration join the single existing household. That was defensible
while the code was private and the deployment was one family's; published
alongside a live URL, it meant any stranger who signed up landed inside that
family's recipes, plan and shopping list, with write access. Decisions:

- **`POST /auth/register` with no invite code creates a new, empty household**,
  optionally named via `household_name` (default "Home"). The reversed default is
  the whole point: the failure mode of getting this wrong is a data breach, so
  the safe outcome has to be the one you get by doing nothing.
- **Joining an existing household needs a single-use invite code** from
  `POST /auth/invites`, issued by a member of it. Codes are 12 Crockford-base32
  characters (~60 bits) shown as `XXXX-XXXX-XXXX` — short enough to read off one
  phone and type into another, long enough that guessing is hopeless behind the
  existing `/auth/register` rate limit. Stored as a SHA-256 hash like every other
  credential, shown once, and forgiving about case, separators and look-alike
  characters on entry.
- **Invites are honoured even when `REGISTRATION_ENABLED=false`.** Otherwise the
  flag is unusable: closing the server to strangers would also lock out your own
  family. Closed now means "no new households", not "no new people".
- **Redeemed invites are kept, not deleted.** `accepted_by_user_id` is the only
  record of who admitted whom, which matters when the household *is* the
  authorisation boundary.
- **Still no roles, no admin, no per-user permissions inside a household** —
  everyone in it can do everything, exactly as under Q16. Being invited is the
  whole of the permission model.
- **Q16 stays true about the data model**: users share one household's library,
  plan and list. Nothing about scoping changed; every query already filtered on
  `household_id`, which is why this was an auth change and not a rewrite. Note
  that this also removes the blocker on multi-tenant hosting, which BACKLOG.md
  had filed under "the freemium split".

**Q20 — Password reset and account deletion: yes** (2026-07-25, prompted by
opening the repo and shipping through TestFlight). Both are App Store review
requirements for an app that creates accounts, and neither existed. Decisions:

*Deletion.*

- **`DELETE /auth/me`, confirmed with the current password**, in the body — a
  password in a query string ends up in access logs and proxy caches.
- **The last member out takes the household with them.** Its recipes, meals,
  plans, lists and cooked history are deleted. Nobody could ever reach that data
  again, so keeping it is hoarding rather than caretaking, and it is exactly what
  the person asked us not to do.
- **Anyone else leaving takes only themselves.** What they contributed belongs to
  the household, not to them: `Recipe.created_by`, `CookedEvent.created_by` and
  `HouseholdInvite.accepted_by_user_id` are all SET NULL, so "cooked 12×"
  survives the cook leaving. `household_invites.created_by_user_id` moved from
  CASCADE to SET NULL for the same reason — the record of who admitted whom
  should outlast the person who issued the invite.
- **Deletion is explicit and ordered, not delegated to cascades.** The
  `household_id` columns deliberately carry no `ondelete`, so Postgres refuses to
  drop a household while its rows survive; the order in `services/accounts.py` is
  load-bearing and doing it in code behaves identically on both engines.
- **No grace period and no soft delete.** A recoverable deletion is a different
  feature with different promises; this one means what it says.

*Reset.*

- **Plain SMTP, configured by env, not a provider SDK.** This is self-hosted by
  design: every relay speaks SMTP, while every SDK needs an account with one
  particular company. Unconfigured servers return 503 saying which variables to
  set, rather than pretending to send.
- **A typed code, not a link.** The same XXXX-XXXX-XXXX format as invites (Q19),
  so no web page has to exist and no deep link has to be registered. The backend
  serves an API and an app, not a website.
- **`POST /auth/password-reset` always returns 202** — unknown address, bounced
  email, doesn't matter. Anything else turns it into a way to ask "does this
  person have an account here?". Delivery failures are logged for the operator.
- **Reset tokens live in `auth_tokens` under `kind="reset"` and cannot
  authenticate.** `get_current_user` allow-lists `session` and `api`. Without
  that filter, receiving the email would be enough to read the household's data
  without ever setting a password — the codes hash to the same value the bearer
  path computes once separators are stripped.
- **Redeeming revokes every session token and returns a fresh one**, matching
  `POST /auth/password`. API tokens survive: rotating a password shouldn't
  silently break every AI client.
