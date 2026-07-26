# App Store submission

Everything needed to put a build in front of App Review, and the order to do it
in. Nothing here is automated on purpose — App Store Connect app records and
metadata are UI-only, and the parts that *can* be scripted (the build, the
screenshots, the review account) already are.

| File | What it's for |
|---|---|
| [metadata.md](metadata.md) | The listing: name, subtitle, description, keywords, URLs, categories, and every field on the form |
| [review-notes.md](review-notes.md) | What App Review is told, the demo account, and how to provision it |
| [app-privacy.md](app-privacy.md) | The App Privacy questionnaire, answer by answer, with the reasoning |

Current state, verified against the App Store Connect API on 2026-07-26:

- App record `com.marcuslab.meals` (id **6794266229**) exists, named
  **"Meal Options Planner"** — needs renaming.
- Version **1.0** exists in `PREPARE_FOR_SUBMISSION` with **no metadata at all**:
  no description, no keywords, no URLs, no age rating, no screenshots.
- Builds 1–15 are on TestFlight at marketing version **0.1**, so **none of them
  can attach to the 1.0 record**. Build 16 is the first that can.
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

### 3. Ship build 16

```bash
make ios-testflight
```

Then move the build 16 row in [ios/CHANGELOG.md](../CHANGELOG.md) from **Local**
to **TestFlight**, and confirm `current_ios_build` in `backend/app/config.py` is
16 (it is) so older installs get the upgrade nudge.

### 4. Regenerate the screenshots

```bash
make ios-screenshots
```

Only needed if the UI moved since the last run — but it takes 90 seconds and
stale screenshots are a rejection risk, so just run it.

### 5. Fill in App Store Connect

In this order, because the form hides fields until earlier ones are set:

1. **App Information** — rename to "Yet Another Meal Planner", set the
   subtitle, categories (Food & Drink / Productivity), and the content rights
   declaration.
2. **Age rating** — answer every question "None"; the result is 4+.
3. **Pricing** — Free.
4. **1.0 version page** — description, keywords, promotional text, support and
   marketing URLs, screenshots, "What's New", and attach build 16.
5. **App Review Information** — tick "Sign-in required", paste the demo account
   and the notes from [review-notes.md](review-notes.md).
6. **App Privacy** — the answers in [app-privacy.md](app-privacy.md). This is a
   separate section from the version page and is easy to miss; the submit
   button stays disabled until it's complete.
7. **Release** — "Manually release this version".

### 6. Submit, then wait

Typically 24–48 hours. If it's rejected, the reply arrives in Resolution
Center; answer it there rather than resubmitting blind — a reply usually gets a
same-day second look, whereas a silent resubmission goes to the back of the
queue.

### 7. After approval

Delete the review household (see
[review-notes.md](review-notes.md#after-approval)), and move build 16 in
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
