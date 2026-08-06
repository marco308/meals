# App Review information

The reviewer has minutes, a phone, and no idea what a self-hosted app is. This
is where the submission is won or lost — a rejection here costs days, and a
2.1 "we couldn't sign in" costs a whole review cycle.

## Sign-in required

**Yes.** Fill in the demo account below.

| Field | Value |
|---|---|
| Username | `apple.review@marcuslab.uk` |
| Password | *(generated when you provision it — see below)* |

Provision it before every submission, and paste the password into App Store
Connect at the same time. See [Provisioning](#provisioning) below.

## Notes — paste this into the "Notes" box

```
Meals is a client for a meal-planning server that the user runs themselves.
There is no company cloud and no shared service; the app talks only to the
server whose address is on the sign-in screen. This is stated in the app
description and in the first line of the subtitle.

For review, the sign-in screen is pre-filled with a working server
(https://meals.marcuslab.uk) and the demo account above signs straight into it.
Nothing needs to be installed or configured — just tap Log in.

The demo account is in its own household containing sample recipes, a plan and
a shopping list, created for this review. It shares no data with any other
account on that server.

WHERE THINGS ARE
- Account deletion: Settings tab (last) > Delete account. It asks for the
  password and for the word DELETE to be typed, then deletes immediately with
  no grace period. Please note it is permanent — if you delete the demo
  account, tell us and we will provision another.
- Privacy policy: Settings > Privacy policy, and at
  https://meals.marcuslab.uk/privacy
- Support: Settings > Help & support, and at
  https://meals.marcuslab.uk/support

THINGS YOU MIGHT LOOK FOR
- There is no third-party or social login, so Sign in with Apple does not
  apply (guideline 4.8).
- There are no purchases, subscriptions, or paid content of any kind. The app
  and the server are free and open source (AGPL-3.0).
- No analytics, advertising, tracking or third-party SDKs are present.
- The shopping list works with no network on purpose: ticking items off and
  adding them queues on the device and syncs later. Turning airplane mode on
  and using the Shopping tab demonstrates it.
- The app is iPhone-only and portrait-only.

The source code, including the server, is public:
https://github.com/marco308/meals
```

## Provisioning

The production server has registration closed (`REGISTRATION_ENABLED=false`),
which is deliberate — it is one family's server. So the review account is made
directly, in a **new and empty household** of its own. An invite would be the
wrong tool: an invite admits someone *into* an existing household, and the
whole point is that the reviewer sees none of it.

On the machine running the API:

```bash
docker exec -it "$(docker ps -qf name=meals_api)" \
  .venv/bin/python -m app.provision --email apple.review@marcuslab.uk
```

It prints a generated password. Then fill the household with the demo data
from anywhere that can reach the server:

```bash
cd backend && read -rs SEED_PASSWORD && export SEED_PASSWORD
SEED_API_URL=https://meals.marcuslab.uk SEED_EMAIL=apple.review@marcuslab.uk \
  uv run python -m app.seed
```

`read -rs` keeps the password out of your shell history. Given `SEED_EMAIL`,
the seed prints no credentials back and mints no API token — that path assumes
a real server.

Re-running `app.provision` with the same email resets that account's password
and creates nothing new, which is what a resubmission needs.

## After approval

- **Delete the review household.** Sign in as the review account in the app and
  use Settings → Delete account; it is the last member, so its data goes with
  it. Keep it only while a submission is in flight.
- If a *later* version is submitted, provision it again with a fresh password.
  Apple keeps the credentials from the previous submission and they should not
  still work.

## Things that would sink this submission

Checked before submitting, because each one is a rejection rather than a note:

- **The demo account doesn't work.** Sign in on a real device with the exact
  credentials in the form, from the build being submitted. This is the single
  most common rejection and it is entirely self-inflicted.
- **The server is down.** `curl -fsS https://meals.marcuslab.uk/healthz`. The
  app is useless without it and the reviewer will not wait.
- **The privacy URL 404s.** It's served by the backend, so it only exists once
  a build carrying `PRIVACY.md` is deployed. Open it in a browser.
- **Account deletion can't be found.** It's in the Settings tab, and the notes
  above say so explicitly. Say "last" rather than a tab *number* — build 21
  added an Ingredients tab and turned the fourth tab into the fifth, which the
  notes would otherwise still be getting wrong.
- **The notes box is empty.** Writing them here is not submitting them:
  `appStoreReviewDetail.notes` was `null` for the whole first review, so none
  of this reached the reviewer. Check it after filling the form in.
- **`MIN_IOS_BUILD` above 0 on the deployment.** The client gate would 426 the
  reviewer's build and they'd see an upgrade wall instead of the app.
