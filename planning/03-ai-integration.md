# 03 — AI Integration (BYO-AI Strategy)

> ✅ **BUILT (2026-07-24).** All three layers shipped; acceptance use cases 1–5 run live (6 is F5, deferred). Remote-MCP public endpoint live at `https://meals.marcuslab.uk/mcp` since 2026-07-25 (issue #6): per-request bearer forwarding, no server-side token. Traceability: [05-status.md](05-status.md).

The product bet: the app has **no built-in AI**. Users bring their own (OpenClaw, Claude, ChatGPT, local models…). Our job is to make the app the easiest possible tool for *any* AI to drive. init.md asks: *"Do we publish a skill? Write an MCP?"* — answer below: **both, layered on one API.**

## The three layers

### Layer 1 — REST API (the foundation, always)
Everything the app can do, exposed as a clean, well-documented REST API with an OpenAPI spec.
- This alone makes the app AI-usable: any agent that can make HTTP calls can use it
- The OpenAPI spec doubles as machine-readable documentation an AI can ingest
- Design the API to be **AI-ergonomic**, which mostly means human-ergonomic but with extra care for:
  - Idempotency (an AI retrying a call shouldn't double-add 500g of mince)
  - Rich errors that say what to do instead ("ingredient 'choped tomatoes' not found; similar: 'chopped tomatoes'") — error messages become part of the agent's prompt, so make them helpful
  - Bulk operations (add a whole parsed recipe + all ingredients in one call, not 15 round trips)
  - A "submit parsed recipe" endpoint that accepts the full structured recipe an AI extracted from a URL

### Layer 2 — MCP server (the ergonomic wrapper)
A thin MCP server wrapping the REST API, exposing task-level tools rather than raw CRUD, e.g.:
- `ingest_recipe(url)` → returns cached recipe or instructs/receives a parse
- `add_meal_to_plan(...)`, `get_meal_options()`
- `get_shopping_list(sort_by_aisle=true)`, `add_to_list(item, qty)`, `check_off(item)`

Why MCP and not just the API: MCP is becoming the standard plug-in mechanism for Claude, and increasingly others; a user adds one config line and their AI has the tools with descriptions attached. It's the difference between "technically usable" and "works out of the box".

### Layer 3 — Published skill (the playbook)
An Agent Skill (and/or a well-crafted "system prompt pack" for non-Claude AIs) that teaches an AI *how to be a good meal-planning assistant with these tools*:
- The workflow: user shares recipe URLs → parse → confirm servings scaling → add meals to plan → maintain list
- Conventions: aisle emoji vocabulary, unit normalisation rules, when to ask vs. act
- The parsing contract: exactly what fields to extract from a recipe page and how to submit them

Skills are cheap to write, versionable in the repo, and they're where the *product feel* of the AI experience lives. MCP gives the AI hands; the skill gives it the recipe (sorry).

## The big architectural question: who parses recipes?

This is Q13 in [04-open-questions.md](04-open-questions.md). The tension:

| | Pure BYO-AI parses | Backend parses | **Hybrid (recommended)** |
|---|---|---|---|
| App works without any AI | ❌ can't ingest URLs | ✅ | ✅ mostly (JSON-LD covers most sites) |
| Running cost for us/self-hosters | zero | needs an LLM key | zero for JSON-LD; LLM only for messy pages |
| Parse quality on messy pages | best (frontier model) | depends on configured key | best (AI handles the messy tail) |
| Complexity | lowest | highest | middle |

> ✅ **DECIDED (Q13): Hybrid.**

**Hybrid in one sentence:** the backend extracts `schema.org/Recipe` JSON-LD itself (most recipe sites embed it — no LLM needed), and pages without it are handled by the user's AI, which reads the page and submits the structured recipe through the API.

One more wrinkle: **aisle-tagging** ingredients is also an "intelligence" task. Options: a built-in lookup table for common ingredients (backend, free, covers 90%) with the AI assigning tags for unknowns via the API. Same hybrid philosophy.

## Concrete AI use cases to design for (acceptance tests for the whole layer)

1. *"Here are 3 recipe links for this week"* → AI ingests all three, creates meals, plan and shopping list are ready — one message
2. *"We're doing cottage pie but add peas and carrots on the side"* → AI creates a meal with 1 recipe + 2 loose ingredients
3. *"What can I cook tonight?"* → AI reads the plan (and maybe checks what's been bought) and lists options with cook times
4. *"I'm at Tesco, what do I need?"* → AI returns the list sorted by aisle
5. *"Actually scratch the burgers, we're out Friday"* → AI removes the meal; list decrements correctly
6. *(Later, F5)* *"Do the Ocado shop"* → AI walks product URLs into a basket

> ✅ **DECIDED (Q14):** Target is **any LLM** — no single-assistant favouritism. Practical consequences:
> - The OpenAPI-documented REST API is the universal floor: anything that can call HTTP works
> - The MCP server serves the (growing) set of MCP-capable assistants
> - Layer 3 ships in two forms: an Agent Skill for Claude-family tools **and** a portable markdown "prompt pack" with the same playbook (workflow, unit-normalisation rules from Q2, aisle emoji vocabulary, parsing contract) that can be pasted into any assistant's instructions

> ✅ **DECIDED (Q15):** **Remote MCP in v1.** The MCP server is served over HTTPS at the public endpoint (behind Traefik, same as the API) with per-user auth tokens, so cloud-hosted AIs can connect. Local/stdio use works against the same server for anyone self-hosting.
