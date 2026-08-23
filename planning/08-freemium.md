# 08 — Freemium on the hosted instance (2026-08-21)

Decisions, not options. This is what the hosted tier at
`meals.marcuslab.uk` sells, what the free tier allows, how the limits are
enforced, and why no money is ever mentioned inside the iPhone app.

It **amends [`06-marketing.md`](06-marketing.md) §Route 2**, which recommends
against a premium tier. That recommendation stands for what it was aimed at:
fencing off a tool that self-hosters already have. What follows is a different
animal, and §1 is the whole distinction.

## 1. The rule: a quota on my hosting, never a fence around the tool

Every limit in this document is a config value that **defaults to unlimited**,
with the enforcement code in this repo under AGPL like everything else. A
self-hoster who sets nothing sees no change, no paywall, no upsell, and no
mention that a hosted tier exists.

That keeps three promises at once: the site's "Not a free tier. The whole
thing." stays literally true of the software, the r/selfhosted audience that
06 calls the marketing department is never the one being fenced, and there is
no closed fork to maintain (§Route 2's second objection).

Two corollaries that decide future arguments:

- **A cap must never make the self-hosted default worse.** If a limit is ever
  tempting to bake into a default, that is the signal it crossed the line.
- **The AI layer is never a tier.** PATs, `/mcp`, `/skill` and `/prompt-pack`
  work on the free tier. They are pillar 1 of the positioning; gating them
  would make the marketing doc a lie. They still carry *ceilings* (§3), which
  is a different thing: a ceiling protects the box, a gate sells an upgrade.

## 2. The gate is members, not recipes

Free hosted is **one member**. Paid is the household.

- Pricing is already per household, and `households.lead_user_id` (Q23) is
  already defined as "the member a household is billed to". The schema was
  built for this.
- The paywall lands at the moment of proven value, which is inviting the
  person you shop with, rather than at a random Tuesday months in when a
  recipe counter runs out.
- A solo free user still has the entire product for themselves. That is a
  taste of the real thing, not a crippled trial, which matters for an audience
  that arrives from the self-hosting world and can walk away to Docker.

Price stays as 06 §6 set it: **£20/year per household, founding price for
life**, annual only. The founding price is stored as a snapshot on the
household, not promised in a document.

## 3. The limits

Two kinds, and they are not the same error:

- **Tier caps.** Money fixes these.
- **Fair-use ceilings.** Money does not fix these. They exist so one household,
  or one assistant in a loop, cannot destabilise the instance. A *paid*
  household reaching one is usually a bug, so it pages me rather than only
  telling them.

### Per household

| Resource | Free | Paid | Ceiling | Why the ceiling |
|---|---|---|---|---|
| Members | 1 | 8 | 12 | the gate; the ceiling is anti-shared-login |
| Recipes | 50 | 2,000 | 5,000 | rows plus ingest egress |
| Ingredients | 500 | 5,000 | 10,000 | grows with recipes, and a bad merge sweep can run away |
| Meals | 100 | 2,000 | 5,000 | cheap rows, unbounded through the API |
| Lines per meal | 50 | 50 | 50 | same in both tiers, a sanity bound |
| Plans | 20 | 1,000 | 2,000 | about one a week, so 1,000 is twenty years |
| Meals per plan | 30 | 30 | 30 | same in both tiers |
| Archived shops | last 3 readable | all | 2,000 | the only thing that grows forever |
| Items per list | 300 | 300 | 300 | same in both tiers |
| Supermarkets | 2 | 20 | 20 | real households shop at two to four |
| API tokens | 3 | 10 | 10 | credential hygiene, not a paywall |
| URL ingests | 20 / month | 500 / month | 1,000 / month | the only limit that costs real bandwidth |
| API requests per token | 60/min, 5,000/day | 120/min, 20,000/day | same | covers MCP, which is just a client |

### Per instance

This is the half that makes capacity planning real, and it applies to any
deployment that sets it, mine included:

- `MAX_HOUSEHOLDS`: registration refuses with a waitlist sentence rather than
  falling over. This is how 06's "founding cohort capped at 25" becomes a fact
  instead of a note.
- `MAX_USERS`: the same, because invites grow the user count without growing
  the household count.

### The three that do not fit the table

- **MCP gets no quota of its own.** It is an HTTP client holding a PAT, so the
  per-token request limits already cover it. A household with different limits
  depending on which door it walked through would produce a 4xx no assistant
  could act on.
- **Skill and prompt pack cannot be metered per household.** They are
  unauthenticated GETs of static text. The control is a per-IP rate limit plus
  `ETag`/`Cache-Control` so the common case never reaches Python. They stay
  ungated in every tier: they are the marketing surface, and a scraper reading
  them is cheaper than a scraper reading `/recipes`.
- **Ingest keeps a per-IP rate limit independent of tier.** Twenty a month is
  generous for a human and tight for someone using the server as a
  general-purpose fetcher. That part is hygiene, not pricing.

### Enforcement shape

One `app/limits.py`: a `Limits` dataclass per tier resolved from settings,
defaults unlimited, and a single `enforce(...)` helper called at the service
layer. Counts are plain `COUNT` queries on the existing `household_id`
indexes. **Do not lock.** Two concurrent creates overshooting a cap by one is
cheaper to tolerate than to prevent.

## 4. Errors and discoverability

Status codes carry the difference, because the caller has to act differently:

| Code | Means | The `detail` says |
|---|---|---|
| 402 | a tier cap money would fix | the limit, the tier, and the number in use |
| 403 | a fair-use ceiling | the limit, and that this needs a conversation |
| 429 | a rate limit | when to retry |

The AI-facing rule from CLAUDE.md applies with more force than usual here: an
assistant that is bulk-importing will hit these, and the sentence it reads is
the whole of what it can do next.

Better than a good error is not hitting the wall at all, so limits are
**published**:

- `GET /limits`: caps, usage and remaining for the calling household, plus an
  MCP tool and a line in the skill, so an assistant checks before importing two
  hundred recipes rather than after fifty.
- The free-tier numbers also ride on `GET /client-config`, unauthenticated, so
  the web signup page can show the table without a login.

## 5. Lapse and downgrade

**Nothing is deleted and nobody is ejected.** Going over cap, or lapsing,
blocks only the writes that *grow* the account: no recipe 51, no new invite.
Everything already there stays readable, plans stay usable, members stay
members, and the shopping list keeps working.

`/shopping-list*` is **exempt from every billing block**, exactly as it is
exempt from the client gate. A lapsed household whose queued `PendingOp`s
cannot drain has had its data destroyed rather than its features reduced
(Q11), and no amount of unpaid invoice justifies that.

Dunning is one email before expiry and one after, through the SMTP that
password reset already uses. Apple's billing grace period has no equivalent
here because there is no Apple (§6), so the grace period is mine to set: 14
days after expiry before caps re-apply.

## 6. No money in the iPhone app

**All commerce lives on the web.** Register, pay, invite: web. The app logs
in and, occasionally, reports a limit.

Guideline 3.1.3 has a **Free Stand-alone Apps** case: a free app acting as a
companion to a paid web based tool, with web hosting and cloud storage as the
given examples, does not need in-app purchase, provided there is no purchasing
inside the app and no call to action for purchasing outside it. YAMP fits that
better than most apps that lean on it, because the app genuinely works against
a server the user owns, and the paid thing genuinely is hosting.

The risk is entirely in the wording, so the rule is stricter than "no payment
screen":

- No price, no "upgrade", no "plans", no link to the pricing page, no address
  to write to about paying. **An error string is a call to action if it points
  anywhere.**
- Cap messages stay factual and server-shaped: "This server's free tier allows
  50 recipes. Your household is at the limit." That sentence is equally true on
  a self-hosted instance, which is the tell that the framing is right.
- Render them as ordinary inline errors like every other 4xx, **never a
  full-screen blocker**. A wall that reads as broken functionality invites the
  2.1 rejection this app has already eaten once.
- The App Store description and keywords say nothing about subscriptions.
- **The Apple review account is comped to the paid tier** in
  `app/provision.py`. A reviewer who hits a cap sees a broken app and will not
  read this reasoning.

What this costs: conversion, materially. Someone who meets a wall with no
action available inside the app converts far below someone tapping a button.
At £20/year and a founding twenty-five that is a good trade for keeping the
15%, keeping Apple's paperwork off the critical path, and keeping the app free
of commerce entirely. StoreKit can be added later if hosted ever grows enough
for the gap to matter, and Apple explicitly allows honouring purchases made
elsewhere once you do.

**Re-read verified 2026-08-22** (issue #100). The caveat above was that the
external-link rules moved during 2025 and the conservative version should hold
unless they had clearly loosened. They have loosened, but only for one
storefront, so it holds.

The case this rests on is now numbered **3.1.3(f)**, and it names web hosting
outright:

> Free apps acting as a stand-alone companion to a paid web based tool (i.e.
> VoIP, Cloud Storage, Email Services, Web Hosting) do not need to use in-app
> purchase, provided there is no purchasing inside the app, or calls to action
> for purchase outside of the app.

What changed is the preamble to 3.1.3, which now reads "Apps in this section
cannot, within the app, encourage users to use a purchasing method other than
in-app purchase, **except for apps on the United States storefront** and as set
forth in 3.1.1(a) and 3.1.3(a)"; 3.1.1(a) says the same from the other side, that
the prohibition on buttons, external links and other calls to action applies "in
all other storefronts, except for the United States storefront, where this
prohibition does not apply".

So a call to action would be permitted on the US storefront and forbidden
everywhere else. This app ships worldwide from one binary and one set of
metadata, which means the strictest storefront sets the rule: **no change, and
the wording above stands.** If that ever stops being true it will be because
the app ships storefront-specific copy, which is its own decision and a much
larger one than a link.

Two things follow for whoever reads this next:

- The exemption is conditional on *both* halves, and the second half is the one
  a cap error can break. "No purchasing inside the app" is easy; "no calls to
  action for purchase outside of the app" is a property of every sentence the
  server can make the app render.
- Re-read it again anyway before the first submission that carries cap errors.
  This paragraph is a snapshot, and the last one aged in under a year.

## 7. What still has to be true before taking money

06 §1b listed four blockers. Three are done: backups with a tested restore
(2026-08-17), SMTP on the deployment (2026-08-13), and a payment mechanism is
now scoped as web-only (§6) and taken through a **merchant of record**, because
EU B2C digital services VAT applies from the first sale regardless of the UK
threshold.

**Corrected 2026-08-23.** This used to read "a merchant of record rather than
raw Stripe", which stated the requirement and then a false dichotomy. The
requirement is right and unchanged: somebody has to be the legal seller and file
in the customer's country, and doing that ourselves means registering for VAT
OSS, charging 27 rates, keeping two pieces of location evidence per sale and
filing quarterly, for the sake of the ~3% between a processor's fee and an
MoR's. At 25 households that difference is about £17 a year.

What was wrong is the implication that an MoR means *not Stripe*. **Stripe
Managed Payments** (2026) is Stripe acting as merchant of record: it handles
sales tax, VAT and GST in 80+ countries, plus fraud, disputes and
transaction-level support, on the Stripe account this project already has. So
the choice is now between three, and the code supports all three
(`app/services/billing.py`, `BILLING_PROCESSOR`):

| | Merchant of record | Notes |
|---|---|---|
| **Stripe Managed Payments** | Stripe | The recommendation. Uses the existing account. Enabled on Checkout or Payment Links; **does not support creating a subscription outside those**, which is fine for a web-only flow and rules out a bespoke checkout later. |
| Paddle | Paddle | The independent option, if an MoR that is not also the payment processor is worth something. |
| Lemon Squeezy | Lemon Squeezy | **Do not start here.** Stripe acquired it in 2024 and its founder confirmed in January 2026 that migration paths *off* it and into Managed Payments are being built. The adapter stays for anyone already on it. |

Still to confirm before the first sale: Managed Payments pricing (reported at
5% + $0.50, not read off Stripe's own pricing page), and an accountant's five
minutes on the UK-seller position. The adapter is written either way.

The fourth is not, and freemium makes it worse rather than better, because free
households arrive faster than paying ones: **strangers' data should not share a
Postgres with the family's**. That move comes before the first pound, not
after it. It stays out of the issue tracker because it is deployment topology,
which this repo deliberately does not carry.

Opening registration is its own piece of work and is currently `false` on the
deployment. A public commercial instance needs email verification, which does
not exist yet, signup rate limits, and a policy for reaping abandoned free
households after warning them.

Also required, none of it code: a legal entity and business bank details, a
terms and refunds page served like `/privacy` and `/support`, a billing
paragraph in `PRIVACY.md` naming the processor, and an ops surface for comping,
extending and revoking, because comps start on day one.

## 8. Order of work

1. `app/limits.py` and the tier model, defaults unlimited ([#94](https://github.com/marco308/meals/issues/94))
2. `GET /limits`, the MCP tool and the skill line ([#95](https://github.com/marco308/meals/issues/95))
3. Instance ceilings and the waitlist refusal ([#96](https://github.com/marco308/meals/issues/96))
4. Household export, free in every tier ([#97](https://github.com/marco308/meals/issues/97))
5. `/terms` ([#98](https://github.com/marco308/meals/issues/98))
6. Entitlements, web purchase, webhooks and comp tooling ([#99](https://github.com/marco308/meals/issues/99))
7. Keep the app free of commerce, and comp the review account ([#100](https://github.com/marco308/meals/issues/100))

Backend first is deliberate: every one of 1 to 5 is worth having on a
self-hosted instance too, so if the hosted business never happens they are not
wasted, which is the same test 06 §Phase 3 applies to the whole plan.

## 9. Decision metric

Unchanged from 06 §Phase 3: waitlist size one month after the Show HN push.
Under ten households, none of this ships, the caps stay unset, and YAMP stays
a free tool with a PikaPods listing. Writing that down in advance is what keeps
it from feeling like failure later.
