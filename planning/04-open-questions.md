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

- **Q16** (above, under Q7): ✅ resolved — implemented as assumed.
