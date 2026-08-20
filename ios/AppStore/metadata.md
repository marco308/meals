# App Store listing

Copy-paste source for App Store Connect. Character limits are Apple's and are
enforced at save time, so the counts below are the real constraint, not advice.

App Store Connect app id **6794266229**, bundle id `com.marcuslab.meals`.

## Name — 30 characters max

```
Yet Another Meal Planner
```

24 characters. **Applied** — the record was renamed from "Meal Options Planner"
on 2026-07-27 and App Store Connect accepted it, so the name was free. "Meals"
is taken by someone else and can't be used.

**This is the YAMP name already; don't "finish" it.** The store name *is* the
acronym, spelled out, which is the whole joke. `planning/06-marketing.md` once
called for the redundant "YAMP: Yet Another Meal Planner" and that prefix is
deliberately not here: the name is only editable on a version in Prepare for
Submission, and 1.0 and 1.1 are both `READY_FOR_SALE`, so it would cost a review
pass to buy five characters that the name already says.

The one thing the expansion does not buy is the literal string, and Apple
indexes strings: a search for "yamp" does not match "Yet Another Meal Planner".
That belongs in **keywords** below, not in the name.

The home-screen name stays **Meals** (`CFBundleName`), which is allowed — the
store name and the icon name don't have to match, they only have to be
plausibly the same app.

## Subtitle — 30 characters max

```
Self-hosted meal planning
```

25 characters. It spends the whole subtitle on the one thing that will
otherwise generate one-star reviews: this needs a server. Better to lose the
download than to get the install and the complaint.

## Promotional text — 170 characters max

Editable any time without a new review, so it's the place to put anything
time-sensitive.

```
Plan meals as options rather than a rigid Mon–Sun grid, and get one aisle-sorted shopping list that works with no signal. Free, open source, runs on your server.
```

159 characters.

## Description — 4000 characters max

```
Meals is a meal planner for people who don't want a diary. Pick a handful of
options for the week, and decide what you actually fancy on the night.

Everything runs on a server you host yourself. There is no company account, no
subscription and no cloud in the middle: your recipes and your shopping list
live on your machine, and the app talks to it directly. The server is free and
open source (AGPL), and starting one is a single command.

A PLAN, NOT A CALENDAR
Add four or five meals as this week's options, grouped into dinners and
lunches. Nothing is pinned to a Tuesday, so nothing is "late" when Tuesday
turns out differently. Mark a meal cooked when you cook it, and the app quietly
learns what your household actually eats.

ONE SHOPPING LIST THAT ADDS UP
Every meal's ingredients merge into a single list, so 500g of mince for the
bolognese and 500g for the cottage pie become one 1kg line — and every line
tells you which meals it's for. Take a meal off the plan and its share comes
off the list, leaving anything you added by hand untouched.

WORKS IN THE SUPERMARKET
Supermarket signal is famously bad, so the shopping list doesn't need any.
Ticking things off, adding what you spot, and putting things back all happen
instantly and sync when you're back in range. The list is sorted in the order
you walk a shop, aisle by aisle, so you stop doubling back.

RECIPES WITHOUT RETYPING
Paste a recipe URL and the ingredients and method come across. Recipes are
parsed once and kept, so the same link is never re-fetched, and your edits are
never overwritten by the original page. Or just type it in — plenty of the best
recipes were never on a website.

KNOWS WHAT'S WORTH THE MONEY
Mark an ingredient as one to buy well or one to buy cheap, with a note on why,
and the verdict shows up on the shopping list — at the shelf, where the
decision is actually made.

BUILT FOR AI ASSISTANTS TOO
The server speaks a documented REST API and MCP, so an assistant you already
use can add recipes, build a plan and manage the list on your behalf. That is a
first-class way to use it, not a bolt-on: the app and the assistant see exactly
the same data.

SHARE IT WITH YOUR HOUSEHOLD
Send an invite code and someone else joins the same recipes, plan and list.
Everyone sees everything, which is what a household is.

WHAT YOU NEED
A Meals server. It's free, open source and runs on a spare machine, a NAS or a
small VPS with one command. Full instructions:
https://github.com/marco308/meals

The server at meals.marcuslab.uk is a private household instance and does not
accept new accounts — please run your own.

PRIVACY
No analytics, no advertising, no tracking, no third-party SDKs. The app talks
to your server and nothing else. Delete your account from inside the app at any
time, and if you were the last person in your household, its data goes with it.
```

Roughly 2,700 characters.

## Keywords — 100 characters max, comma separated, no spaces after commas

```
meal,planner,recipe,shopping,list,groceries,selfhosted,offline,pantry,cooking,aisle,mealprep,yamp
```

96 characters. **`yamp` is pending, not applied** — keywords are versioned
metadata like the name, so this rides along with the next submission at no extra
cost. It earns its slot precisely because it is *not* a word in the name: Apple
indexes "Yet Another Meal Planner" as those four words, so anyone who heard the
name as an acronym and searched "yamp" found nothing. `grocery` was dropped to
pay for it; the live string was at exactly 100 of 100, not the 99 this file
claimed, so there was no headroom. `groceries` still covers the search.

Don't repeat words from the name or subtitle — Apple indexes those already, so
"meal" and "planner" earn nothing here beyond the phrase matches, and the rest
is spent on searches this app can actually win.

## URLs

| Field | Value |
|---|---|
| Privacy Policy URL | `https://meals.marcuslab.uk/privacy` |
| Support URL | `https://meals.marcuslab.uk/support` |
| Marketing URL | `https://github.com/marco308/meals` |

All three are served by the deployment or by GitHub, and the first two must be
live **before** you submit — Apple opens them. They come from `PRIVACY.md` and
`SUPPORT.md` in the repo, so deploy the version of the backend that ships them.

## Categories

| | |
|---|---|
| Primary | Food & Drink |
| Secondary | Productivity |

## The rest of the form

| Field | Value | Why |
|---|---|---|
| Version | 1.0 | Matches `CFBundleShortVersionString`. Builds 1–15 went up as 0.1 and can never attach to this record. |
| Build | the one shown as **18** | The first iPhone-only build (`CFBundleVersion` 17 — the numbering diverged, see [ios/CHANGELOG.md](../CHANGELOG.md)). Builds 16 and 17 claim iPad support and would oblige you to supply 13" iPad screenshots. Attach by build id, not by number. |
| Price | Free | |
| Age rating | 4+ | No objectionable content of any kind; nothing age-gated. |
| Copyright | `2026 Marcus Williams` | |
| Content rights | Does not contain, show, or access third-party content | Recipe pages the *user* imports are their own doing, on their own server. |
| Export compliance | Nothing to answer | `ITSAppUsesNonExemptEncryption: false` is already in the Info.plist, so the question is skipped. HTTPS-only use is exempt anyway. |
| Sign in with Apple | Not required | Only offered when an app offers a *third-party* login. This one has email and password against your own server, and no social login at all. |
| Release | Manually release this version | A first submission approved at 3am shouldn't go live before you've looked at it. |

## What's New — for version 1.0

```
First release.
```

## Screenshots

Generated by `make ios-screenshots` — see
[ios/screenshots/README.md](../screenshots/README.md). Upload the five 6.9"
(1320 × 2868) PNGs under the iPhone 6.9" size class; App Store Connect scales
them for every smaller iPhone.

No iPad set, because from the build attached here the app really is
iPhone-only. If App Store
Connect ever asks for 13" iPad screenshots again, that is not a metadata
problem — it means the attached build is declaring `UIDeviceFamily = [1, 2]`,
and the fix is in `project.yml`, not here.

Order matters — the first two are all most people see:

1. `02-shopping-list.png` — the distinctive screen, and the one that sells it
2. `01-plan.png` — options, not a calendar
3. `03-recipe-detail.png`
4. `04-recipe-library.png`
5. `05-settings.png`

## App previews

None. Optional, and a video is a maintenance liability for a one-person
project — it has to be re-shot for every visual change, and unlike the
screenshots there's no script to do it.
