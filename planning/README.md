# Meal Planning & Shopping List App — Planning

Planning docs for the app described in `../init.md`.

> ✅ **BUILT & DEPLOYED (2026-07-24).** The MVP cut is implemented, tested,
> verified, and live at `https://meals.marcuslab.uk` (swarm stack behind
> Traefik). Item-by-item traceability in [05-status.md](05-status.md);
> remaining tail in [../BACKLOG.md](../BACKLOG.md).

## Documents

| File | What's in it |
|------|--------------|
| [01-features.md](01-features.md) | Feature breakdown: core concepts, MVP vs later, user workflows |
| [02-architecture.md](02-architecture.md) | High-level architecture: backend, API, data model concepts, hosting, frontend |
| [03-ai-integration.md](03-ai-integration.md) | The BYO-AI strategy: MCP vs skill vs plain API, recipe parsing ownership |
| [04-open-questions.md](04-open-questions.md) | **Decisions log** — all questions answered; Q16 implemented as assumed |
| [05-status.md](05-status.md) | **Build status** — plan→implementation traceability with evidence |

## Product one-liner

A meal *options* planner (not a rigid Mon–Sun grid) with a recipe library and an aisle-sorted shopping list, exposed through an AI-friendly API so users can drive it with their own AI assistant.

## Guiding principles (from init.md)

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

## Next steps

1. ✅ ~~Marcus confirms **Q16**~~ — implemented as assumed (users share one household; household modelled explicitly so multi-tenancy stayed cheap). Amended by **Q19**: registration now creates a new household and joining one needs an invite.
2. ✅ ~~Design doc: data model + API spec~~ — superseded by the implementation; the OpenAPI spec at `/openapi.json` is the API contract, and the offline-sync contract (client ids, LWW, idempotent adds) is implemented and tested.
3. ✅ ~~Repo scaffolding (`backend/`, `ios/`, `mcp/`, `skill/`) and build~~ — all four built and verified; see [05-status.md](05-status.md).
4. ✅ ~~Deploy to the homelab~~ — live at `https://meals.marcuslab.uk` (`make deploy`). Remaining: deploy the Q19 tenancy change.
