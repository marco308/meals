# 06 — Marketing & Monetisation (2026-07-28)

The product is built, deployed and about to be renamed for the outside world:
**YAMP, Yet Another Meal Planner**. This doc is the strategy behind the
landing page in [`docs/`](../docs/): who it is for, what we charge, in what
order, and what we refuse to do. Market facts below were verified 2026-07-28;
sources are linked inline.

## 1. The name

**YAMP** is a self-aware joke that does real work:

- It disarms the only objection everyone has ("another meal planner?") by
  making it the title. You cannot heckle a product that heckles itself first.
- It rhymes with YAML in the head of exactly the audience we want first:
  self-hosters and MCP tinkerers, without the site having to look like a
  terminal to land it.
- It sets the voice: honest, dry, technical. The tagline is
  *"Unfortunately, this one is good."*

YAMP is the **marketing name**. Internal identifiers, the `X-Meals-Client`
header and API vocabulary do not change (additive-only contract, see
CLAUDE.md). The rename checklist is §8.

## 2. Positioning

> **Yet Another Meal Planner: the self-hosted one built for your AI.**

Three pillars, each one a thing no competitor claims:

1. **Bring your own AI.** No LLM inside, ever. A first-party MCP server (21
   task-level tools) plus a skill/prompt pack the server publishes about
   itself at `/skill`. Your assistant, your model bill, your data.
2. **Plans are pools, not calendars.** Five dinners in no particular order.
   Every other planner assumes you know what Tuesday looks like.
3. **The list survives the shop.** Offline-first native iPhone app, aisle
   order, and provenance: every line knows which meals put it there.

### Why pillar 1 is the wedge (verified 2026-07-28)

- **No incumbent ships an official MCP server**: not Mealie, Tandoor,
  KitchenOwl, Paprika, AnyList, Mealime or Samsung Food.
- **Demand is proven by the community doing it badly for them**: roughly 47
  third-party MCP wrapper repos for Mealie (~12), Tandoor (~11), Paprika
  (~13), AnyList (~7) and KitchenOwl (~4), nearly all under 8 months old.
  The biggest ([rldiao/mealie-mcp-server](https://github.com/rldiao/mealie-mcp-server))
  has 118 stars and 65 forks for a thin wrapper.
- **The phrase is unclaimed.** Nobody established markets "built for your
  AI"; the only products trying are months-old pantry trackers (Pantry
  Persona, SaltyBytes). `awesome-mcp-servers` has no food category at all.

Caution that keeps us honest: the wrapper layer is a commodity (dozens exist
at 0 stars). The moat is the **whole product designed for AI use**: the
self-publishing skill, task-shaped tools, actionable 4xx errors, idempotent
writes. Market the product, not the protocol.

## 3. Audiences, in order of pursuit

| # | Who | Where they live | What they need to hear |
|---|---|---|---|
| 1 | Self-hosters / homelab | r/selfhosted, awesome-selfhosted, HN, lobste.rs, fediverse | AGPL with no asterisk, two-command start, no LLM bill hiding inside |
| 2 | AI tinkerers | MCP directories, r/ClaudeAI, r/LocalLLaMA, X/Mastodon AI crowd | `claude mcp add` one-liner, 21 task-level tools, server publishes its own manual |
| 3 | Households who just want dinner sorted | App Store, word of mouth from 1 and 2 | The app is free, the hosted tier means never hearing the word Docker |

Audience 3 is where hosted revenue lives, but they arrive through the first
two. Do not buy ads; this product spreads by being written up.

## 4. Competitive map (verified 2026-07-28)

| Product | What it is | Licence | Stars | Money model | MCP |
|---|---|---|---|---|---|
| [Mealie](https://mealie.io) | Recipe manager + planner | AGPL-3.0 | ~12.8k | Donations only, no official hosting | None official |
| [Tandoor](https://tandoor.dev) | Recipe management | AGPL **+ Commons Clause** (not OSI open source) | ~8.5k | Official hosted €1.99 to €4.99/mo | None official |
| [KitchenOwl](https://kitchenowl.org) | Grocery list + recipes | AGPL-3.0 | ~3.5k | Donations only | None official |
| [Grocy](https://grocy.info) | Household ERP / stock | MIT | ~9.3k | Donations only | None official |
| Paprika | Closed, per-device | proprietary | n/a | One-time per platform ($4.99 iOS, $29.99 Mac) | No |
| AnyList | Closed, sync service | proprietary | n/a | $9.99 to $14.99/yr subscription | No |

What this table says:

- **The niche pays two ways today**: one-time app purchases (Paprika, Mela)
  or small subscriptions (AnyList $14.99/yr household, Tandoor from
  €1.99/mo). **£20/yr per household sits exactly in the accepted band.**
- **Tandoor's Commons Clause is our opening.** They advertise "completely
  open source" while their licence forbids anyone selling hosting of it.
  YAMP being plain AGPL is a genuine differentiator for audience 1, stated
  without naming and shaming: *"AGPL-3.0, no asterisk, no Commons Clause."*
- **Mealie is the giant.** Never fight it on recipe management; the FAQ on
  the site already takes the generous line ("run it next to Mealie, it will
  not be offended"). We win on planning model, offline list and AI layer.

## 5. Monetisation: the four routes

### Route 0: self-host, free (the engine, not the revenue)

Free forever, whole product, AGPL. This is distribution, social proof and
the talent pool for PRs. AGPL also means anyone reselling YAMP hosting must
publish their changes, which keeps that door survivable. **Do not attempt to
monetise this group; they are the marketing department.**

### Route 1: hosted, paid (RECOMMENDED PRIMARY)

Two sub-paths, both worth doing, in this order:

**1a. PikaPods listing (do first, near-zero effort).**
[PikaPods](https://www.pikapods.com) pays open-source authors a **20%
revenue share** (verified on their FAQ; the 10-15% figure floating around is
from 2022). Their food category currently lists **only Grocy and Mealie,
neither with a revenue-share deal**: an open slot. A small Python+Postgres
pod costs users about **$2 to $5/month**, so the share is modest
(100 pods × $3 × 20% ≈ $60/mo) but it makes "paid hosting exists" true
without us running anyone's dinner, and it is a distribution channel in
itself (126-app catalogue). Action: email hello@pikapods.com; requirement is
essentially a clean container + env config + healthcheck, which
`docker-compose.yml` already demonstrates.

**1b. First-party hosted, £20/year per household (founding price, for life).**
This is the real business case: audience 3 will pay to never see Docker, and
meal planning is a shared family utility with natural yearly cadence.

Hard blockers before taking anyone's money, in order:

1. **Nightly off-site Postgres backups + a tested restore** (already in
   BACKLOG; it stops being optional the day a stranger's dinner is on it).
2. **SMTP on the deployment** so password reset works (shipped in code,
   unset in prod).
3. **Stripe Payment Links** (no billing code; a link per year is enough at
   this scale) plus a one-paragraph terms + refunds line.
4. **Move stranger data off the family homelab**: a ~£4/mo VPS covers
   dozens of households, so 3 paying households make it profitable. Founding
   cohort capped at 25 until the ops are provably boring.

The landing page already sells this honestly: waitlist now (a GitHub issue
costs nothing and measures demand), money only when the boring parts exist.
**Annual only at launch**; monthly is churn admin we do not need yet.

### Route 2: paid premium features (RECOMMEND AGAINST, here is the analysis)

The ask was to consider a premium-features tier. Reasons not to, in strength
order:

1. **It breaks the licence story.** The repo is AGPL; a gated feature must
   live in a closed fork or hosted-only code. That is the Tandoor Commons
   Clause trap wearing a different hat, and audience 1 (our marketing
   department) punishes open-core harder than any other crowd.
2. **It splits a one-person codebase.** Every hour on entitlement checks is
   an hour not spent on the product; the additive-only client contract would
   double in complexity the day features vary by tier.
3. **The audience punishes fences.** The site now deliberately promises
   nothing about premium either way, which keeps the option open. But the
   day a gate appears around something self-hosters already had, the
   r/selfhosted goodwill this plan relies on is spent. If premium ever
   happens, ship it as genuinely new hosted-side convenience, never as a
   fence around an existing tool.

**What "premium" can mean without the trap**: convenience around the hosted
tier only. Backups with restore-on-request, priority support, custom
subdomain, maybe a higher-touch onboarding. Ops guarantees, never withheld
tools. If a genuinely premium *feature* idea ever appears, the test is:
would gating it embarrass us in front of r/selfhosted? It always would.

**The iOS app stays free.** It is a client of the user's own server;
charging for it (Paprika-style) suppresses the exact funnel that feeds
routes 1 and 3. A tip-jar IAP can come later without changing this.

### Route 3: donations and sponsorship (DO NOW, expect little)

- **GitHub Sponsors** first: **0% platform fee** from personal accounts,
  native Sponsor button, FUNDING.yml scaffold is in this branch (commented
  until the account is enrolled).
- **Buy Me a Coffee** second, for the non-GitHub crowd: ~5% platform fee,
  realistically 8 to 9% after processing.
- Perks that cost nothing: a name in `SUPPORTERS.md` (file created in this
  branch), first crack at TestFlight builds.

Realistic expectation: donations for projects this size are £0 to £50/month
until several thousand stars. This route is goodwill infrastructure, not
income.

### Revenue reality check

| Milestone | Realistic annual money |
|---|---|
| Launch year (hundreds of stars) | £100 to £500: a few founding households + coffees |
| Mealie-scale community (~10k stars, years) | £2k to £10k: 50 to 200 hosted households + PikaPods share + sponsors |
| Ceiling if hosted genuinely lands | A pleasant side business, never a salary. Price honesty accordingly. |

This is a homelab product with a real but small market. The strategy is
built so every route is worth doing at that scale, and none of them poisons
the open-source position that makes the project fun to run.

## 6. Published pricing (as on the site)

| Tier | Price | What it is |
|---|---|---|
| Self-host | £0 forever | Everything. "Not a free tier. The whole thing." |
| Hosted | Shown as **TBA** on the site; recommended £20/yr per household, founding price for life, announced when the waitlist opens for real | Same software, our ops. Waitlist until backups are boring. |
| Supporter | £n, you pick n | Sponsors/BMaC. SUPPORTERS.md + early TestFlight. |

Anchors: Tandoor Basic €1.99/mo (~€24/yr), AnyList household $14.99/yr,
PikaPods DIY ~$2 to $5/mo. £20 is cheap enough to be an impulse, expensive
enough to matter across 50 households.

## 7. Launch plan

**Phase 0, plumbing (this week):**
merge this branch; enable GitHub Pages from `docs/` on main; enroll GitHub
Sponsors + create BMaC; uncomment FUNDING.yml; rename the App Store record
(still unsubmitted, currently "Meal Options Planner") to
**"YAMP: Yet Another Meal Planner"**; decide the domain (§8).

**Phase 1, existence (next 2 to 4 weeks):**
finish the staged App Store submission (BACKLOG); email PikaPods; PR to
[awesome-selfhosted](https://github.com/awesome-selfhosted/awesome-selfhosted)
(read their criteria first: active maintenance and real releases; cut a
tagged release as part of this); PR to `awesome-mcp-servers` proposing the
missing food category; get the MCP server into the registries MCP clients
actually read.

**Phase 2, the push (when App Store live):**
- **Show HN**: *"Show HN: YAMP, a meal planner with no AI inside, built for
  yours"*. The no-LLM angle is the hook; the comment section will supply
  the scepticism the site already answers.
- **r/selfhosted** post (their self-promo rules reward being a participant
  first; seed a week of genuine activity before posting).
- **lobste.rs**, fediverse, r/ClaudeAI (the `claude mcp add` one-liner demo
  gif is the whole post).
- Three blog posts that write themselves, staggered: *"There is no AI in
  this app; that is the feature"*, *"Plans are pools, not calendars"*,
  *"The offline queue must always drain"*. Engineering-story marketing is
  the only kind this audience does not block.

**Phase 3, money (only after demand signal):**
waitlist ≥ 10 households → do the backup work, Stripe links, open the
founding 25. Waitlist < 10 after the Phase 2 push → park route 1b, keep
PikaPods + donations, and the project remains a healthy free tool. Write
that outcome down as acceptable now so it never feels like failure later.

## 8. Rename checklist (Meals → YAMP)

| Item | Action | Cost |
|---|---|---|
| App Store record | Rename to "YAMP: Yet Another Meal Planner" **before** first submission (record is unsubmitted, so this is free; afterwards it is a versioned change) | Trivial, do now |
| GitHub repo | `marco308/meals` → `marco308/yamp` if wanted; GitHub redirects old URLs. Decide **before** Show HN, never after | Small |
| Domain | Candidates to check: `yamp.app`, `yamp.dev`, `yamp.cooking`, `getyamp.com`, `yamp.uk`. GitHub Pages CNAME once chosen; until then `marco308.github.io` is fine | ~£10/yr |
| Landing page, README title | This branch does the landing page; README retitle rides the repo rename | Trivial |
| Production instance | `meals.marcuslab.uk` stays as-is; it is the private family instance, not the brand | None |
| Code identifiers, `X-Meals-Client`, API vocab | **Do not touch.** The additive-only contract forbids renames on the wire, and internal churn buys nothing | Zero, deliberately |

## 9. Voice rules (for anything public)

- Honest to the point of self-deprecation; the name sets the register.
- Say what things cost and why. Never hide the AGPL, the bus factor, or the
  fact that hosted runs on a small operation.
- Technical claims must be checkable in the repo ("21 tools" links to the
  code, "98% coverage" to CI).
- No hype vocabulary, no exclamation marks, no em dashes.
- When comparing to competitors, be generous by name (Mealie, Tandoor) and
  sharp only about categories ("planners that plan Tuesdays").

## 10. Metrics (once a month, 30 minutes, in this doc's git history)

Stars; GitHub Pages traffic (repo Insights, no analytics on the site by
design); TestFlight installs; App Store installs; waitlist issue count;
sponsor count; PikaPods pod count if listed. The one decision metric:
**waitlist size at one month after the Phase 2 push** (see Phase 3 gate).
