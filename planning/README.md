# Meal Planning & Shopping List App — Planning

Planning docs for the app described in `../init.md`. High-level only — no code, no schemas-in-stone yet.

## Documents

| File | What's in it |
|------|--------------|
| [01-features.md](01-features.md) | Feature breakdown: core concepts, MVP vs later, user workflows |
| [02-architecture.md](02-architecture.md) | High-level architecture: backend, API, data model concepts, hosting, frontend |
| [03-ai-integration.md](03-ai-integration.md) | The BYO-AI strategy: MCP vs skill vs plain API, recipe parsing ownership |
| [04-open-questions.md](04-open-questions.md) | **Decisions log** — all original questions answered 2026-07-23; one new question (Q16) open |

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

1. Marcus confirms **Q16** (single shared household in v1) in [04-open-questions.md](04-open-questions.md).
2. Design doc: data model + API spec (entities, relationships, offline-sync contract for the list).
3. Repo scaffolding (`backend/`, `ios/`, `mcp/`, `skill/`) and build.
