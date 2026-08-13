# Privacy policy

**Last updated: 13 August 2026**

Meals is a meal planner you run on your own server. The iOS app is a client:
it talks to whichever server you point it at, and to nothing else. There is no
Meals account, no Meals cloud, and no central service that sees your data.

That shape is the whole privacy story, so it is worth being precise about it.

## The short version

- The app contains **no advertising, no tracking, and no third-party SDKs of
  any kind**: no analytics or crash-reporting code ships in it. The only
  usage information the developer ever sees is the aggregated, opt-in
  statistics Apple offers every App Store developer, described
  [below](#app-store-analytics-and-crash-reports).
- Everything you enter goes to **the server you chose**, and stays there.
- If you run that server, you hold the data and nobody else can read it.
- You can delete your account, and everything belonging to it, from inside the
  app.

## What the app stores on your device

| What | Where | Why |
|---|---|---|
| Your sign-in token | iOS Keychain | So you don't sign in every launch. Removed on sign-out and on account deletion. |
| The server URL you chose | App preferences | So the app knows where to connect. |
| A cached copy of your shopping list, plan and recipes | App container on disk | So the app works in a supermarket with no signal. Cleared on sign-out and on account deletion. |
| Shopping-list changes made offline | App container on disk | Queued until the server is reachable, then sent and discarded. |

None of this leaves the device except to reach your server.

## What your server stores

The server holds an account and a household:

- **Your account** — email address, display name, a bcrypt hash of your
  password (never the password), and the creation date.
- **Access tokens** — stored as hashes, not as usable tokens. These are your
  app sign-in and any personal API tokens you create for AI assistants.
- **Invites** — if someone invited you into their household, a record of who
  invited whom, so the household knows who has access. The code itself is
  stored hashed.
- **Your household's content** — recipes, meals, plans, shopping lists,
  ingredient notes, and which meals you have cooked.

A household is the entire privacy boundary. Everyone in your household can see
and edit all of its content; nobody outside it can see any of it. There are no
roles and no administrator, so treat an invite code like a password.

Servers also keep ordinary web-server logs, which typically include IP
addresses and request paths. How long those are kept is up to whoever runs the
server.

## What leaves your server, and when

Only three things ever cause an outbound request, and each one is something you
asked for:

- **Recipe import.** When you submit a recipe URL, the server fetches that page
  to read its structured recipe data. The site you named sees a request from
  your server.
- **Recipe photos.** Imported recipes carry a photo URL from the original site.
  When the app displays one, your device requests that image from that site,
  which sees your device's IP address. Delete the photo URL on a recipe if you'd
  rather it didn't.
- **Password reset emails.** If your server has email configured, a reset code
  is sent through its mail relay. If it isn't configured, password reset is
  simply unavailable.

If you connect an AI assistant to the API with a personal token, that assistant
sees whatever it asks for. That connection is yours to make and yours to revoke
— delete the token and it stops working immediately.

## App Store analytics and crash reports

If you installed the app through the App Store or TestFlight, Apple offers its
developer the same opt-in statistics it offers every developer: aggregated
usage figures (installs, sessions, active devices, retention) and crash
reports. To be clear about what that is and is not:

- **It is Apple's collection, not the app's.** The app ships no analytics or
  crash-reporting code; iOS itself gathers this for every app on the phone.
- **It is opt-in.** Apple only shares it if you enabled *Share with App
  Developers* when setting up your device. You can check or change this any
  time in Settings → Privacy & Security → Analytics & Improvements.
- **It is aggregated and anonymous.** The developer sees counts and crash
  traces, never your identity, your Meals account, or any of your content:
  no recipes, no lists, no plans.
- **It never involves your server.** A self-built or sideloaded install, or an
  opted-out device, shares nothing at all.

The developer uses it for exactly what you'd hope: knowing whether the app
crashes and roughly how many people use it.

## Who is responsible for your data

**Whoever runs the server you use.** If you self-host, that is you: you are the
data controller, and this policy describes what the software does rather than
what any particular operator promises.

If you use `meals.marcuslab.uk`, that server is operated by the author of this
project, in the United Kingdom, as a private household instance. It is not a
public service, registration on it is closed, and it is not offered for general
use. See [Contact](#contact) below for anything about it, including data
requests.

## Your choices

- **Delete your account** — Settings → Delete account, inside the app. It asks
  for your password and a typed confirmation, then deletes immediately: no
  grace period and no undo. Your sign-in and every API token you created are
  removed. If you were the last person in your household, its recipes, meals,
  plans, shopping list and cooked history go with it. If other people are still
  in the household, their shared content stays — it is theirs too.
- **Export your data** — every endpoint the app uses is a documented REST API
  (`/docs` on your server). Your data is readable with a personal token and a
  single `curl`.
- **Move servers** — change the server URL on the sign-in screen. Nothing ties
  the app to any particular deployment.

If you are in the UK or EU, the GDPR rights of access, rectification, erasure,
restriction, portability and objection apply against whoever operates your
server. On a self-hosted instance those rights are exercised with a database
you already control.

## Children

Meals is not directed at children and asks for no information about age. It is
rated 4+ because it contains nothing unsuitable, not because it is aimed at
children.

## Changes to this policy

This policy lives in the app's public source repository, so its full history is
public. Material changes will be noted in the changelog and the date at the top
of this file will change.

## Contact

Open an issue at <https://github.com/marco308/meals/issues>, which reaches the
author directly. For anything you'd rather not say in public — including a data
request about `meals.marcuslab.uk` — use GitHub's
[private vulnerability reporting](https://github.com/marco308/meals/security/advisories/new)
form, which is private to the two of us and is not only for security reports.

This is a one-person project, not a company with a support desk. Expect a reply
within a week.
