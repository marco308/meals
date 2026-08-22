# Terms and refunds

**Last updated: 22 August 2026**

Meals is a meal planner you run on your own server. The software is free and
open source under the AGPL, and it always will be. Separately from that, the
author offers to run it for you, and these terms cover that offer.

## If you self-host, almost none of this applies

Running the software yourself is a licence, not a service. What governs it is
the [AGPL-3.0](https://github.com/marco308/meals/blob/main/LICENSE) and nothing
here: no fee, no account with anybody, no uptime promise from anyone but you,
and no way for this project to switch anything off. The only sections below
that are worth your time are [Acceptable use](#acceptable-use), which is about
a server you do not own, and [The software itself](#the-software-itself).

Everything else assumes you are paying somebody to run Meals for you.

## What is on offer, and what it costs

The hosted service is the same software as the repository, running on the
author's hardware, with the database backed up nightly and the backups
[tested](https://github.com/marco308/meals/tree/main/backup).

| | |
|---|---|
| What you get | One household on a shared server: the API, the web app, the iOS app, the MCP endpoint, and the AI skill. No feature is held back for a higher tier. |
| Price | £20 per year, per household. Annual only, paid up front. |
| Founding price | Whatever you first paid is what you keep paying, for as long as you keep paying it. It is recorded against your household rather than promised in a document. |
| Included | Free members of the same household up to the published limit, every endpoint, and unlimited export. |

**Nothing is on sale yet.** The hosted service is waitlist-only, no payment
mechanism exists, and nobody has been charged. This page is here before the
money rather than after it, so that what is being agreed to is public first.
When it does open, the price above is the price.

## Paying

There is no payment processor yet, and this section will name the one that gets
chosen before a single payment is taken. It will be a merchant of record, which
means the card details go to them and never to this server or its author: what
this server would ever hold is the fact that a household is paid up, until when,
and what it agreed to pay. The privacy policy (`/privacy` on the same server)
says the same thing from the other direction, and both are updated together.

VAT, where it applies, is included in the price.

## Refunds

**Full refund inside 30 days, for any reason or none.** Ask and it is done.
There is nothing to argue about at £20, and arguing would cost more than the
refund.

After 30 days, a year already paid for is not refunded, but you can cancel at
any time and simply not be charged again. If the service is shut down mid-year,
see [If the service ends](#if-the-service-ends) below.

## What is actually promised

This is a one-person operation and the promise is sized to match, because a
number nobody can keep is worth less than an honest one.

- **Best effort, no SLA.** No uptime percentage is promised, because there is no
  second person to page at three in the morning. In practice it runs on a
  monitored machine with alerting, and the author uses it daily for his own
  household, which is the real guarantee.
- **Nightly backups, and they are tested.** A dump is taken every night and read
  back before it counts, retained daily and weekly, and copied off the machine
  encrypted. The restore procedure is written down and has been rehearsed.
- **Planned downtime is not announced.** Deploys are zero downtime and take
  seconds. Anything larger will be brief.
- **Support is GitHub issues**, with a reply within a week, which is what the
  support page (`/support` on the same server) says too.
- **Data loss.** Backups exist and are tested precisely because no promise can
  replace them. Keep your own copy: `GET /household/export` returns everything
  your household owns in one request, free, on every tier, forever.

## Cancelling, and what happens next

Cancel whenever you like. Nothing about it is designed to be difficult.

- **Nothing is deleted.** Not at cancellation, not at expiry, not later. Your
  recipes, plans, lists and cooked history stay exactly where they are and stay
  readable.
- **There is a grace period of 14 days** after the paid year ends. During it
  nothing changes at all.
- **After that, the free limits re-apply.** That blocks only the writes that
  *grow* the household: no new recipe past the free allowance, no new invite.
  Everything already there stays readable, the plan stays usable, members stay
  members, and the shopping list keeps working in full. The published numbers
  are in
  [the freemium plan](https://github.com/marco308/meals/blob/main/planning/08-freemium.md).
- **Export never stops working.** It is free in every tier and is not affected
  by lapsing, because a service that makes leaving difficult has not earned
  anybody staying.
- **Deleting your account is yours to do**, from inside the app, at any time.
  That one really does delete things, immediately and without undo, as the
  privacy policy (`/privacy`) spells out.

## If the service ends

If the hosted service is withdrawn, you get **at least 90 days' notice and a
pro-rata refund of the unused part of your year**. The software is AGPL and the
export is one request, so the migration path is the same one that has always
been there: run it yourself, or ask somebody else to.

## Acceptable use

Short, because it is a meal planner.

- Do not use it to break the law, to attack the server, or to store other
  people's personal data without their say-so.
- Do not use the recipe importer as a general-purpose web fetcher. It exists to
  read recipe pages, and it is metered.
- An invite code admits somebody to your household and everything in it. Who you
  give one to is your decision and your responsibility.
- Accounts may be suspended for any of the above. That is the only reason an
  account gets suspended, and it comes with an explanation and a refund of the
  unused part of the year.

## The software itself

The software is licensed under the AGPL-3.0 and comes with no warranty, exactly
as that licence says. These terms cover the service of running it for you, and
they do not add any warranty to the software or take one away.

If you self-host, the AGPL is the whole agreement. There is nothing to accept
and nobody to accept it from.

## Changes to these terms

They live in the public source repository, so the full history is public.
Material changes are noted in the changelog and the date at the top of this file
changes. A change that affects what you are paying for takes effect at your next
renewal, never mid-year.

## Who you are dealing with

The hosted service is operated by the author of this project, in the United
Kingdom. Nothing on this page limits any right you have under UK consumer law,
including your statutory cancellation rights, which sit alongside the refund
promise above rather than replacing it.

## Contact

Open an issue at <https://github.com/marco308/meals/issues>, which reaches the
author directly. For anything you would rather not say in public, including a
billing question, use GitHub's
[private reporting form](https://github.com/marco308/meals/security/advisories/new),
which is private to the two of us and is not only for security reports.

This is a one-person project, not a company with a support desk. Expect a reply
within a week.
