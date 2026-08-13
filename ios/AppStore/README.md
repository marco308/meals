# App Store submission

Everything needed to put a build in front of App Review, and the order to do it
in.

Most of the listing **can** be set through the App Store Connect API, and was:
name, subtitle, categories, age rating, description, keywords, promotional
text, URLs, screenshots, copyright, content rights, the attached build and the
release type. The app *record* itself can't be created that way, and two
sections refuse an API key with these permissions — **App Privacy** and
**Pricing** — so those are the web UI, always.

| File | What it's for |
|---|---|
| [metadata.md](metadata.md) | The listing: name, subtitle, description, keywords, URLs, categories, and every field on the form |
| [review-notes.md](review-notes.md) | What App Review is told, the demo account, and how to provision it |
| [app-privacy.md](app-privacy.md) | The App Privacy questionnaire, answer by answer, with the reasoning |
| [resolution-center.md](resolution-center.md) | Replies to App Review, and what evidence backs each claim in them |

Current state, verified against the App Store Connect API on 2026-08-06:

- App record `com.marcuslab.meals` (id **6794266229**), renamed to
  **"Yet Another Meal Planner"**.
- Version **1.0** back in `PREPARE_FOR_SUBMISSION`, fully populated: subtitle,
  Food & Drink / Productivity, 4+, description, keywords, promotional text,
  privacy + support + marketing URLs, five 6.9" screenshots, copyright,
  content rights, manual release.
- **1.0 is `READY_FOR_SALE` — the app is live**, approved 2026-08-12 00:12 UTC.
- Submitted 2026-07-27 19:35 UTC, rejected 2026-08-06 08:19 UTC under
  guideline 2.1(a) — a server fault, not an app one, diagnosed and fixed in
  full at [ios/CHANGELOG.md](../CHANGELOG.md#the-21a-rejection). Replied to in
  Resolution Center (kept in [resolution-center.md](resolution-center.md)) and
  resubmitted 2026-08-06 21:27 UTC. Five days in the queue, then thirteen
  minutes in review.
- **Build 23** is the public one (uploaded 2026-08-05, `VALID`), attached in
  place of the one App Store Connect calls **18**. Builds 1–15 are on
  TestFlight at
  marketing version **0.1**, so none of them can attach to the 1.0 record at
  all, and builds 16 and 17 claim iPad support and would oblige you to supply
  13" iPad screenshots.
- **The App Review notes are filled in**, from
  [review-notes.md](review-notes.md). They were `null` for the whole first
  review, so nothing explaining self-hosting or pointing at account deletion
  reached the reviewer. Writing them in this repo is not submitting them —
  check the field, not the file.

## Order of operations

### 1. Deploy the server first

The privacy and support URLs are served by the backend. If you submit before
deploying, Apple opens a 404 and rejects.

```bash
make deploy
curl -fsS https://meals.marcuslab.uk/healthz
curl -fsSI https://meals.marcuslab.uk/privacy | head -1
curl -fsSI https://meals.marcuslab.uk/support | head -1
```

Check `MIN_IOS_BUILD` is still `0` on the deployment — a floor above the
submitted build shows the reviewer an upgrade wall instead of the app.

### 2. Provision the review account

See [review-notes.md](review-notes.md#provisioning). Keep the password; it goes
in the form in step 5.

### 3. Ship the build — ✅ done

```bash
make ios-testflight
```

**Build 23** was uploaded on 2026-08-05, is `VALID`, and is the one attached.
`current_ios_build` in `backend/app/config.py` is `23` and is live — it is
compared against the `CFBundleVersion` *inside* the installed app, so it tracks
that number and not the one App Store Connect displays. Changing it only goes
live on the next `make deploy`.

Next time: move the new row in [ios/CHANGELOG.md](../CHANGELOG.md) from
**Local** to **TestFlight** and bump `current_ios_build` with it.

> `make ios-testflight` needs `ios/.env` (`MEALS_DEVELOPMENT_TEAM`,
> `ASC_KEY_ID`, `ASC_ISSUER`). It's gitignored, so a fresh clone — or a git
> worktree — won't have it; copy it across from your main checkout.

### 4. Regenerate the screenshots

```bash
make ios-screenshots
```

Only needed if the UI moved since the last run — but it takes 90 seconds and
stale screenshots are a rejection risk, so just run it.

### 5. Fill in App Store Connect

Done by API, and re-runnable: App Information (name, subtitle, categories,
content rights), age rating, the whole 1.0 version page, screenshots,
copyright, the attached build, and manual release.

Left in the web UI, because an API key with these permissions gets a 404 on
both:

1. **[App Privacy](https://appstoreconnect.apple.com/apps/6794266229/distribution/privacy)**
   — the answers are in [app-privacy.md](app-privacy.md). A separate section
   from the version page and easy to miss; the submit button stays disabled
   until it's complete.
2. **[Pricing](https://appstoreconnect.apple.com/apps/6794266229/distribution/pricing)**
   — Free.

And one field that needs a human: **App Review Information → contact phone
number**. Apple requires it in international format and rejects the request
without one. The rest of that section (contact name and email, the demo
account, the notes) goes in with it.

### 6. Submit, then wait — rejected 2026-08-06, **resubmitted the same day**

Submitting is three API calls: `POST /v1/reviewSubmissions`, then
`POST /v1/reviewSubmissionItems` naming the submission and the version, then
`PATCH` the submission with `{"submitted": true}`. **After a rejection there is
a fourth**, and it comes first: the rejected submission still owns the version,
so cancel it with `PATCH /v1/reviewSubmissions/<old>` `{"canceled": true}` and
*wait for it to leave `CANCELING`*. Until it reaches `COMPLETE` the version is
locked and every attempt to add it returns 409
`ITEM_PART_OF_ANOTHER_SUBMISSION`. Removing the item directly is refused too.


Typically 24–48 hours; this one took ten days. If it's rejected, the reply
arrives in Resolution Center; answer it there rather than resubmitting blind —
a reply usually gets a same-day second look, whereas a silent resubmission goes
to the back of the queue. Keep the reply in
[resolution-center.md](resolution-center.md).

The rejection also has a lesson for step 1. **Deploying is not the same as
being reliable afterwards.** The 2.1(a) rejection was a 500 from `/auth/login`
caused by dead pooled database connections, on a server that had been up for
days and whose `/healthz` was green the entire time, because `/healthz` touches
no database. The curl checks in step 1 would all have passed that morning. If
you want a check that would have caught it, hit an endpoint that reads the
database with the review credentials, not just `/healthz`:

```bash
curl -fsS -X POST https://meals.marcuslab.uk/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"email":"apple.review@marcuslab.uk","password":"<the one in the form>"}'
```

### 7. After approval

Move the shipped row in [ios/CHANGELOG.md](../CHANGELOG.md) to **App Store** —
done for build 23 — and delete the review household (see
[review-notes.md](review-notes.md#after-approval)). **The household is still
there**; it only wants deleting once no submission is in flight, and it costs
nothing but a stale account until then.

### When a new version is *not* the answer

The app is a client for a REST API, so anything that lives on the server
reaches every install the moment it deploys. No build, no review, no version
record. That covers the whole backend: endpoints, validation, aisle order,
error strings, the skill, and both fixes that came out of the 2.1(a) rejection.

A new App Store version is only worth opening when the **Swift** changes. If
`git log <last-shipped-build>..main -- ios/Meals/Meals ios/Meals/project.yml`
is empty, there is nothing a submission could deliver, and resubmitting an
identical binary just buys another turn in the review queue and another chance
to be rejected.

## The rejections this app is most likely to get

Not a general checklist — the specific risks of *this* app, and what's already
been done about each.

| Risk | Guideline | Mitigation |
|---|---|---|
| Reviewer can't sign in | 2.1 | **This is what happened on 2026-08-06.** Demo account provisioned per submission, server pre-filled on the sign-in screen, credentials tested on a device first — and none of that helps if the *server* fails the one request they make. Sign in against production immediately before submitting |
| "The app requires a server we don't have" | 2.1 / 4.2 | Subtitle, description and review notes all say it up front; the demo account means they never have to set one up |
| Account creation with no way to delete | 5.1.1(v) | Settings → Delete account, deletes immediately, called out in the review notes |
| Missing or unreachable privacy policy | 5.1.1 | Served at `/privacy`, checked in step 1 |
| Sign in with Apple missing | 4.8 | Doesn't apply: no third-party or social login is offered |
| Privacy labels don't match behaviour | 5.1.2 | Declared conservatively — see [app-privacy.md](app-privacy.md) |
| Minimum functionality | 4.2 | It's a full app with offline sync, not a wrapper; the screenshots show real screens |
