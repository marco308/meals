# Meal Planning & Shopping List App — Planning

The planning docs this app was built from. The brief they were written against
(`init.md`) is not in this repo; the principles it set out are restated below.

> ✅ **BUILT, DEPLOYED, AND SHIPPED.** The MVP cut is implemented, tested and
> live at `https://meals.marcuslab.uk` (swarm stack behind Traefik), and the
> iPhone app has been on the App Store as 1.0 since 2026-08-12. Item-by-item
> traceability as of the first deploy in [05-status.md](05-status.md); what
> has shipped since in [../CHANGELOG.md](../CHANGELOG.md); what's left in
> [../BACKLOG.md](../BACKLOG.md) and
> [GitHub issues](https://github.com/marco308/meals/issues).
>
> These files are **history, not a roadmap**, with one exception:
> [04-open-questions.md](04-open-questions.md) is the live decisions log that
> code comments cite by number (Q1–Q23), so it is maintained and must never
> be turned into issues.

## Documents

| File | What's in it |
|------|--------------|
| [01-features.md](01-features.md) | Feature breakdown: core concepts, MVP vs later, user workflows |
| [02-architecture.md](02-architecture.md) | High-level architecture: backend, API, data model concepts, hosting, frontend |
| [03-ai-integration.md](03-ai-integration.md) | The BYO-AI strategy: MCP vs skill vs plain API, recipe parsing ownership |
| [04-open-questions.md](04-open-questions.md) | **Decisions log** — all questions answered; Q16 implemented as assumed |
| [05-status.md](05-status.md) | **Build status** — plan→implementation traceability with evidence, frozen at the first deploy |
| [06-marketing.md](06-marketing.md) | **Marketing & monetisation** — YAMP positioning, pricing, launch plan; the strategy behind the landing page in `docs/` |

## Product one-liner

A meal *options* planner (not a rigid Mon–Sun grid) with a recipe library and an aisle-sorted shopping list, exposed through an AI-friendly API so users can drive it with their own AI assistant.

## Guiding principles (from the original brief)

1. **Flexible plans, not schedules.** The plan is a pool of meal options ("dinners this week: spag bol, cottage pie"), not a calendar. Plans changing (e.g. an unexpected London trip) must not require any re-planning work.
2. **Parse a recipe once, reuse forever.** Recipe URLs are ingested, structured, and cached.
3. **The shopping list knows *why*.** Every list item links back to the recipe(s)/meal(s) that need it; quantities merge; ad-hoc items (milk) are first-class.
4. **AI is a client, not a component.** The app ships no built-in LLM. It ships an API (and probably an MCP server) that any AI — OpenClaw, Claude, whatever — can drive.
5. **Open-source / freemium later** — keep licensing, self-hostability, and multi-tenancy in mind from day one, even if v1 is single-user on the homelab.

## Key decisions (2026-07-23)

- **Stack:** Python/FastAPI + Postgres (Docker), following the podcast-manager pattern; Docker Swarm + Traefik + Let's Encrypt on `*.marcuslab.uk`, public internet
- **Frontend:** native Swift/SwiftUI iOS app; **offline shopping-list support is a hard requirement**
- **Auth:** real per-user accounts in v1, sharing one household's data
- **Recipe parsing:** hybrid — backend does free JSON-LD extraction; the user's AI handles messy pages via the API
- **Quantities:** clients normalise to metric or natural counts ("2 tins"); backend merges exact units only
- **AI layer:** OpenAPI REST API + remote MCP (HTTPS, per-user tokens) + skill/prompt pack; works with any LLM
- Full record in [04-open-questions.md](04-open-questions.md)

## How the plan closed out

Every step this section once tracked is done, and is recorded here rather than
deleted so the plan can be read end to end.

1. **Q16** — implemented as assumed (users share one household; household modelled explicitly so multi-tenancy stayed cheap). Amended by **Q19**: registration now creates a new household and joining one needs an invite.
2. **Design doc: data model + API spec** — superseded by the implementation. The OpenAPI spec at `/openapi.json` is the API contract, and the offline-sync contract (client ids, LWW, idempotent adds) is implemented and tested.
3. **Repo scaffolding (`backend/`, `ios/`, `mcp/`, `skill/`) and build** — all four built and verified; see [05-status.md](05-status.md). `web/` joined them later.
4. **Deploy to the homelab** — live at `https://meals.marcuslab.uk` (`make deploy`), Q19 tenancy included.

New work does not get added here. It goes to
[GitHub issues](https://github.com/marco308/meals/issues).
