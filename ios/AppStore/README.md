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

Current state, verified against the App Store Connect API on 2026-07-27:

- App record `com.marcuslab.meals` (id **6794266229**), renamed to
  **"Yet Another Meal Planner"**.
- Version **1.0** in `PREPARE_FOR_SUBMISSION`, fully populated: subtitle,
  Food & Drink / Productivity, 4+, description, keywords, promotional text,
  privacy + support + marketing URLs, five 6.9" screenshots, copyright,
  content rights, manual release.
- **Outstanding:** App Privacy, Pricing, and the App Review contact phone
  number.
- Builds 1–15 are on TestFlight at marketing version **0.1**, so **none of them
  can attach to the 1.0 record**. The attached build is the one App Store
  Connect calls **18** (uploaded 2026-07-27, `VALID`) — the first that is
  really iPhone-only. Its `CFBundleVersion` is 17; the numbering diverged, see
  [ios/CHANGELOG.md](../CHANGELOG.md). Builds 16 and 17 claim iPad support and
  would oblige you to supply 13" iPad screenshots.
- Nothing has ever been submitted for review.

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

The iPhone-only build was uploaded on 2026-07-27, is `VALID`, and is attached.
`current_ios_build` in `backend/app/config.py` is `17` — it is compared against
the `CFBundleVersion` *inside* the installed app, so it tracks that number and
not the one App Store Connect displays. It only goes live on the next
`make deploy`.

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

### 6. Submit, then wait

Typically 24–48 hours. If it's rejected, the reply arrives in Resolution
Center; answer it there rather than resubmitting blind — a reply usually gets a
same-day second look, whereas a silent resubmission goes to the back of the
queue.

### 7. After approval

Delete the review household (see
[review-notes.md](review-notes.md#after-approval)), and move the shipped row in
[ios/CHANGELOG.md](../CHANGELOG.md) to **App Store**.

## The rejections this app is most likely to get

Not a general checklist — the specific risks of *this* app, and what's already
been done about each.

| Risk | Guideline | Mitigation |
|---|---|---|
| Reviewer can't sign in | 2.1 | Demo account provisioned per submission, server pre-filled on the sign-in screen, credentials tested on a device first |
| "The app requires a server we don't have" | 2.1 / 4.2 | Subtitle, description and review notes all say it up front; the demo account means they never have to set one up |
| Account creation with no way to delete | 5.1.1(v) | Settings → Delete account, deletes immediately, called out in the review notes |
| Missing or unreachable privacy policy | 5.1.1 | Served at `/privacy`, checked in step 1 |
| Sign in with Apple missing | 4.8 | Doesn't apply: no third-party or social login is offered |
| Privacy labels don't match behaviour | 5.1.2 | Declared conservatively — see [app-privacy.md](app-privacy.md) |
| Minimum functionality | 4.2 | It's a full app with offline sync, not a wrapper; the screenshots show real screens |
