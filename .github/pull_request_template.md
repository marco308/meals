## What and why

<!-- One or two sentences. Link the issue if there is one. -->

## Checklist

Delete what doesn't apply — these are the invariants CI can't fully check
(see CLAUDE.md).

- [ ] **API contract stayed additive** — no removed/renamed response field, no
      new required request field, no tightened validation, no changed meaning
      for an existing value. TestFlight builds can't be recalled.
- [ ] **Model change has a migration** (`make migration m="..."`) chained onto
      the single existing head.
- [ ] **Shopping-list quantities still derive from `ListItemSource`** — nothing
      mutates `ListItem.quantity`.
- [ ] **Every new query filters on `household_id`.**
- [ ] **Skill/prompt pack updated** if endpoints, units, aisles or value tiers
      changed — they ship with the API and must stay in step.
- [ ] **New 4xx strings say what to do instead** — they land in an agent's
      context.
