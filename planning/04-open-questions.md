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
  > ⚠️ **AMENDED by Q23 (2026-08-18):** this bullet only. A household now has a
  > **lead**, and inviting, revoking, removing a member and renaming are theirs
  > alone. Everything else here stands: no read-only members, no per-user
  > permissions, and nothing about the food is unequal between members.
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

**Q21 — Ingredient names are folded to one identity: yes** (2026-07-29,
prompted by a shopping list showing "garlic" and "garlic cloves", "mint" and
"mint leaves" as separate lines). An ingredient's *name* is its identity key,
so two recipes describing the same food differently produced two ingredients
and two lines that could never merge. Decisions:

- **Fold at `get_or_create_ingredient`, not per client.** Every write path —
  JSON-LD ingest, an AI's `POST /recipes`, a meal's loose ingredient, an ad-hoc
  list add — already funnels through it. One place to fold means the answer is
  the same however the food arrived, and no client has to know the rules.
- **Fold only what doesn't change the shop.** Prep and size adjectives
  ("fresh", "grated", "large"), the "how much of the plant" nouns ("mint
  *leaves*", "garlic *cloves*", "*root* ginger") and plurals. Anything that
  changes which product you buy stays: "ground" coriander, "dried" oregano,
  "smoked" paprika, "minced" beef, "red" onion, "whole" milk. **A missed merge
  is a cosmetic annoyance; a wrong merge silently changes someone's
  shopping** — so when in doubt, the word stays.
- **This is Q13's hybrid, not Q17's abstention.** Unlike "is the posh one worth
  it", a plural is not a matter of household taste, so a table can settle it.
  What the table won't claim ("beef mince" vs "minced beef") goes to the AI via
  `GET /ingredients/duplicates` and `POST /ingredients/{id}/merge`, the same
  shape as an ❓ aisle.
- **Protected compounds keep the spelling they are bought under.** "chopped
  tomatoes" is a tin, not a tomato, and folding it to the singular would fix a
  duplicate while ruining the list ("2 tins chopped tomato"). Both spellings
  key to one row and it is stored plural. The protected list is seeded from the
  multi-word entries in the aisle table — an ingredient specific enough to have
  earned its own aisle is by definition its own product.
- **Detection reports facts, not guesses.** `GET /ingredients/duplicates` only
  groups names that fold to the same string. Looser heuristics were considered
  and rejected: "garlic" is a subset of "garlic bread", and same-aisle
  proximity would have proposed exactly the merges that lose someone's dinner.
- **Merging repoints, it does not recompute.** Recipe lines, loose meal
  ingredients and list lines all move to the survivor, carrying their
  `ListItemSource` rows, so a merged line still knows why it is there
  (principle 3) and its quantity is still the sum of its sources. Colliding
  lines combine their shop state pessimistically — ticked off only if *both*
  halves were, so an outstanding need resurfaces rather than being quietly
  bought.
- **The survivor's curation is untouched.** Aisle, staple flag and value tier
  are the household's decisions (Q17); a merge has no opinion about them.
- **No migration, and no backfill on deploy.** Existing rows keep their names
  and ids: a rewrite would change the identity of rows that iOS may hold
  queued offline operations against (Q11). The catalogue is cleaned up
  deliberately, through the merge endpoint, by someone who can see what they
  are merging.
- **Also fixed at the source: `<n> <food> <unit>`.** "3 garlic cloves" parsed
  as `×3` of an ingredient called "garlic cloves" while "2 cloves garlic"
  parsed correctly, so the same food arrived under two names *and* two units.
  The parser now lifts a trailing container word into the unit, except where
  it is load-bearing ("2 bay leaves" is not two leaves of bay).

**Q22 — Removing junk ingredients: a guarded DELETE, not a garbage collector**
(2026-07-29, prompted by BBC Food's dual-measure lines: "100g/3½oz vermicelli
rice noodles" parsed to quantity 100 g but name "/3½oz vermicelli rice
noodles" — the slash glued the imperial rendering onto the name — and the
junk rows it created had no way out, because the API had no ingredient
delete at all). Decisions:

- **Fixed at the source first.** The parser drops the "/imperial" tail
  (including compound "2lb 4oz" and triple "40g/1½oz/3 tbsp" forms) before any
  other parsing, keeping the exact metric figure rather than an
  `INGEST_CONVERSIONS` approximation of the rounded imperial one; the strip
  requires a metric unit before the slash, so real fractions ("juice of 1/2
  lemon") are untouched. The comma rule also stopped assuming prep notes only
  trail: "300g/10½oz cooked, peeled king prawns" used to name the ingredient
  "cooked"; the name is now the first comma segment that isn't purely
  preparation words ("peeled king prawns").
- **`DELETE /ingredients/{id}`, guarded like `DELETE /recipes`.** 404 for a
  row this household doesn't own, 409 naming what still references it (recipe
  lines, loose meal ingredients, shopping-list lines — archived ones too)
  while anything does. The 409 points at the merge endpoint (Q21), which is
  usually the better fix for a misparse of a real food: it repoints every
  reference onto the right ingredient and deletes the junk in the same stroke.
  Delete is for the leftovers nothing references.
- **No unreferenced-ingredient GC**, considered and rejected: "unreferenced"
  is not "worthless". An ingredient between uses still carries the household's
  aisle, staple flag and value tier — curation that is never guessed (Q17) and
  so must never be silently destroyed. Cleanup stays a decision someone makes,
  with the guard rails saying what it would break.

**Q23 — A household has a lead, and only the lead decides who is in it: yes**
(2026-08-18, prompted by issue [#52](https://github.com/marco308/meals/issues/52)
and by pricing the hosted tier per household rather than per seat). This
**amends Q19**, which said being invited was the whole of the permission model
and that nobody inside a household outranked anybody. That is still true of the
*food* and no longer true of the *guest list*. Decisions:

- **`households.lead_user_id`, set to whoever registered the household.**
  Existing households backfill to their earliest user, who is that person by
  construction: everyone else arrived later, through an invite. Nullable in the
  schema for two mechanical reasons — the household row is inserted before the
  user who will lead it exists, and `SET NULL` is what lets `accounts.py` delete
  a household's users before the household — but never NULL in practice.
  "Exactly one lead, always" is an invariant that module holds, not the schema.
- **The lead mints invites, revokes them, removes members and renames the
  household. Nothing else.** They cannot touch a recipe, plan, list or
  ingredient that any other member cannot; there is still no such thing as
  read-only membership. The reason is billing and only billing: hosted YAMP is
  £20/yr *per household*, so it needs one unambiguous answer to "whose card is
  this?", and the person paying should be the person deciding how many people
  are in it. Every future feature will be tempted to ask "should only the lead
  do this?" — the answer is no unless money is involved.
- **Leaving is everybody's own.** `DELETE /auth/household/members/{user_id}`
  with your own id, no permission needed. A household you could only get out of
  by deleting your account would be a worse trap than the one this whole issue
  exists to open, and Q20 exists precisely because that corner is unacceptable.
  Removal and leaving are one endpoint because they are one act with two
  callers; they are deliberately *not* one permission.
- **Nobody is deleted by being removed.** They keep their account, their
  password and every session and API token, and land in a new household of their
  own with nothing in it. `DELETE /auth/me` remains the only thing in the API
  that ends an account.
- **One write underneath all of it.** Leaving, being removed and redeeming an
  invite all move `user.household_id` and then collect the household behind them
  if nobody is left in it — `move_user_to_household`, built out of the two halves
  Q20 already needed. Tokens hang off `user_id` and every request reads the
  household off the user row, so nothing is revoked and nothing is copied.
- **`POST /auth/invites/redeem`, so leaving is not a one-way door.** Until now a
  code could only be spent at `POST /auth/register`: you could get out of a
  household and then had no way into any other without deleting your account and
  signing up again. Redeeming while signed in is the missing edge, and it is the
  *only* door that can destroy anything — leaving is refused when you are the
  household's only member, so it can never vacate one. Redeeming out of a
  household of one that still holds recipes needs `{"force": true}`, the same
  idiom as a re-parse that would discard someone's edits; a household nobody
  ever put anything in doesn't ask.
- **A lead who deletes their account leaves one behind.** The role passes to the
  longest-standing remaining member automatically, because nobody is around to
  be asked and a household with a subscription and no lead is a support ticket.
  A lead who wants to *leave* has to hand over first — that one is refused with a
  409, because they are still there to decide.
- **Handing over needs no acceptance, for now.** While the lead only gates a
  guest list, `PATCH /auth/household {"lead_user_id": …}` taking effect
  immediately is a fair trade for keeping it simple. The day it carries a
  payment obligation, taking it on becomes something the other person has to
  agree to — a different endpoint, shipped with the billing work.
- **Tightening `POST`/`DELETE /auth/invites` to the lead is the first
  non-additive change this API has made**, and it is accepted with its eyes
  open. Build 16 onwards puts "Invite someone" in front of every member and
  those builds cannot be recalled, so a non-lead on an old build now gets a 403.
  It fails legibly — `APIError.server` carries the server's `detail` through
  `errorDescription` and `InvitesView` prints it — so the sentence names the
  lead and says to ask them. `MIN_IOS_BUILD` stays at 0: a readable refusal is
  not worth cutting off every install below the new build.
- **Members can still read `GET /auth/invites`**, and `GET /auth/household`
  lists everyone with their email. Who else is in the house, and who could still
  walk into it, is not the lead's private business — these people already share
  a recipe library, a plan and a shopping list.
- **No MCP tools.** All 29 are about food. Household admin is a settings screen,
  not something an assistant should be reaching for on someone's behalf.

**Q24 — Freezer stock: a tab of batches, not a merged tally** (2026-09-03).
Batch cooking (Q18) ends with portions in the freezer, and nothing remembered
them. `freezer_items` is one row per **batch** — a label, the portions left,
the date it went in, a note — scoped to the household like everything else.
Decisions:

- **A batch, not a count per dish.** Two batches of chilli frozen a month apart
  are two things to eat oldest-first; merging them into one number would lose
  the date that says which is which. Clients total by label when they want to.
- **The label is the record; the links are a courtesy.** `meal_id` /
  `recipe_id` say where a batch came from when it came from the app and are
  `SET NULL` on delete, so tidying the library never empties a freezer. Free
  text (both null) is for what never passed through a plan — half a lasagne
  from a friend, the stock.
- **Portions is what is left.** Taking one decrements; the row goes at zero, so
  the table *is* the freezer. No history of what was eaten from it: the cooked
  record already says what was made, and eating from the freezer is not a
  cooking, so it touches neither the plan nor the list.
- **Every add is a new batch, never a merge**, and the API says so — the
  shopping list merges because a line is one thing to buy; a freezer batch is
  one thing to eat.
- It is a limited resource (`freezer_items`, counted in batches) so the hosted
  ceiling story stays whole; unlimited by default like everything else.
- iOS has no freezer screen yet: the API is additive, so the web app and the
  MCP tools carry it until a build does.
