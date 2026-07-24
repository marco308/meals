# 01 — Features

## Core concepts (the nouns)

These four concepts drive everything. Getting their relationships right is the most important design decision in the app.

### Recipe
A structured, cached representation of an external recipe.
- Source URL (canonical key — same URL never gets re-parsed)
- Parsed ingredients with quantities and units
- Metadata: cook time, prep time, servings, and probably: title, image, cuisine/tags
- Ingested by an LLM (see [03-ai-integration.md](03-ai-integration.md) for *whose* LLM)

### Ingredient
A canonical grocery item, shared across recipes.
- Name (canonicalised — "chopped tomatoes" from two recipes should be the same ingredient)
- **Aisle tag** (emoji) — e.g. 🥫 tinned, 🥬 produce, 🧊 frozen — used to sort the shopping list into store-walking order
- *(Later)* per-supermarket product URLs, so an AI doing online shopping can jump straight to the product page instead of searching

### Meal
The unit of planning. A meal is a named thing you'd actually eat, composed of:
- **Zero or more recipes** (a meal can have several — e.g. a main + a sauce recipe)
- **Plus loose ingredients** with no recipe (the cottage-pie-with-peas-and-carrots case: peas and carrots are just ingredients attached to the meal, no recipe needed)
- Optionally a category/slot like *dinner* / *lunch* — used for grouping in the plan view, **not** tied to specific days

### Shopping list item
- Quantity + unit
- **Provenance links**: which meal(s)/recipe(s) need it. One list item, many links. Adding a second recipe that needs onions bumps the quantity and adds a second link.
- Ad-hoc items with no provenance (milk, bin bags) are fully supported
- Sortable/groupable by aisle tag
- Check-off state while shopping

> ✅ **DECIDED (Q1):** One live shopping list for now, archived on completion. Multiple named lists deferred.

> ✅ **DECIDED (Q2):** The writing client normalises quantities (option c) against a strict API convention: everything is either **metric** (g/kg/ml/l) or a **count of a natural unit** ("2 tins", "3 cloves"). No cups/oz. The backend merges exact-matching units only; the published skill carries the conversion rules for AIs.

---

## Feature areas

### F1 — Recipe ingestion & library ✅ BUILT
- Submit a recipe URL → structured recipe stored (LLM-parsed)
- Cache: resubmitting a known URL returns the stored recipe instantly
- Browse/search the library (by title, tag, cook time…)
- Manual recipes with no URL (family recipes, "things I just know how to make")
- Edit a parsed recipe (parsing will sometimes be wrong; corrections must stick and not be clobbered by a re-parse)

> ✅ **DECIDED (Q3):** Manual/no-URL recipes are in v1 (title + ingredients minimum; steps optional).

### F2 — Meal options plan (the anti-calendar) ✅ BUILT
- The plan is a flat, glanceable list grouped by slot: **Dinners:** spag bol, cottage pie · **Lunches:** Caesar wraps, burgers
- No days, no dates on individual meals. A plan might have a loose horizon ("this week's options") but nothing breaks when life changes
- Tap a meal → its recipe URL(s) (deep link out to the original site) + full ingredient list for that meal
- Mark a meal as **cooked** (nice-to-have: this is the hook for future "what do we actually eat" insights, and could optionally tick its ingredients off / release them)
- Building a plan = picking meals from the library (or creating new ones inline)

> ✅ **DECIDED (Q4):** Plans are **weekly-ish entities**: a loose label/date-range ("w/c 20 July"), archivable, and a new plan can be started by copying an old one. Still no per-day scheduling inside a plan — just grouped options.

### F3 — Shopping list ✅ BUILT
- Auto-populated when a meal is added to the plan (all its recipe ingredients + loose ingredients land on the list, merged into existing items where possible)
- Removing a meal from the plan removes/decrements its contributions — but never touches ad-hoc items
- Add ad-hoc items directly (this should be *fast* — it's the milk-is-out use case)
- Shopping mode: sorted by aisle emoji, check items off as you go
- "Already have it" — exclude an item from this shop without deleting the provenance (you have onions in the cupboard)

> ✅ **DECIDED (Q5):** Per-ingredient **staples flag** in v1. Staples are hidden from the shopping list by default, with a toggle to reveal them for a **"staples check"** before shopping. No real stock tracking.

### F4 — AI access layer ✅ BUILT
The headline differentiator. Covered fully in [03-ai-integration.md](03-ai-integration.md). Summary:
- Clean REST API for everything above
- Probably an MCP server + a published skill so BYO-AI users get a great out-of-box experience

### F5 — (Later) Supermarket integration ⬜ DEFERRED (as planned)
- Per-ingredient product URLs per supermarket
- Enables an AI to do the online shop near-deterministically: walk the list, open each product URL, add to basket; only fall back to searching when a link is dead
- Explicitly **not** v1, but the ingredient model should leave room for it (an ingredient can carry a set of `(supermarket, url)` pairs)

> ✅ **DECIDED (Q6):** Target supermarkets: **Sainsbury's, Ocado, M&S**.

### F6 — Users & auth (promoted into v1) ✅ BUILT
- **Separate identities from day one**: real per-user accounts and auth are in v1
- Working assumption: multiple users share one **household** — one recipe library, one plan, one shopping list (multi-household tenancy stays deferred to the freemium future)

> ✅ **DECIDED (Q7):** Separate identities/auth in v1 (not the static-API-key shortcut).
> ❓ **INPUT NEEDED (Q16):** Confirm the household assumption above — all v1 users share the same plan/list/library. (Assuming yes unless you say otherwise.)

---

## MVP cut (approved) ✅ SHIPPED

> All in-v1 items below are built, tested, and verified — see [05-status.md](05-status.md).

**In v1:** F1 (URL ingestion + cache + edit, plus manual recipes), F2 (weekly-ish plans), F3 (incl. staples check), F4 (API + remote MCP), **per-user auth within a single household**.
**Out of v1:** supermarket URLs (F5), multi-household tenancy, pantry tracking beyond the staples flag, cooked-meal analytics, native notifications.

> ✅ **DECIDED (Q8):** MVP cut approved, amended by Q7 (auth promoted into v1).
