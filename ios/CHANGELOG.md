# iOS build ledger

One row per `CFBundleVersion`, because a build that reaches TestFlight cannot
be recalled — the only way to undo one is to ship another. This is the answer
to "what have people actually got, and what is only on my laptop".

**App Store Connect is the source of truth**; this file is the readable copy.
Verify with the API rather than trusting a stale row:

```bash
xcrun altool --list-builds --apiKey "$ASC_KEY_ID" --apiIssuer "$ASC_ISSUER"
```

## The ritual when you bump a build

`CFBundleVersion` in [Meals/project.yml](Meals/project.yml) is not the only
number involved. All four steps, in order:

1. Bump `CFBundleVersion` in `ios/Meals/project.yml` to one above the highest
   build in App Store Connect (not one above the last row here — uploads have
   come from outside this repo before).
2. Add a row below with status **Local**, and write what's in it.
3. `make ios-testflight`. When it lands, move the row to **TestFlight**.
4. Move `current_ios_build` in `backend/app/config.py` to match, and deploy.
   That number drives the app's soft upgrade nudge; if it lags, nobody is ever
   told a newer build exists. Leave `min_ios_build` at `0` unless a change
   genuinely can't be made backwards compatible — raising it hard-blocks every
   install below it.

## Status vocabulary

| Status | Means |
|---|---|
| **Local** | Built on a laptop. Nobody else has it. Freely rewritable. |
| **TestFlight** | Uploaded. Testers can install it. Cannot be recalled. |
| **In review** | Submitted to App Review, awaiting a verdict. |
| **App Store** | Public. Cannot be recalled; only superseded. |

## Builds

| Build | Version | Uploaded | Status | What's in it |
|---:|---|---|---|---|
| 16 | 1.0 | — | Local | First build aimed at App Review. Defaults to `https://meals.marcuslab.uk` instead of localhost; login screen explains the server field and self-hosting; account settings (password, sign-out, delete) moved into a Settings screen reachable from every tab; marketing version raised 0.1 → 1.0 so the build can attach to the 1.0 App Store record. |
| 15 | 0.1 | 2026-07-25 | TestFlight | Password reset and account deletion flows in the app (decision Q20). |
| 14 | 0.1 | 2026-07-25 | TestFlight | Invite-code field on the register screen, so a second household member can join without going through the API by hand (Q19). |
| 13 | 0.1 | 2026-07-25 | TestFlight | Not recorded at the time — reconstructed from the upload date only. |
| 12 | 0.1 | 2026-07-25 | TestFlight | Not recorded at the time. |
| 11 | 0.1 | 2026-07-25 | TestFlight | Not recorded at the time. Around here: recipe photos, recipe editing, per-meal scaling, offline reads, and the client version gate. |
| 6 | 0.1 | 2026-07-25 | TestFlight | Premium/budget ingredient verdicts (Q17). |
| 5 | 0.1 | 2026-07-25 | TestFlight | Staple glyph sizing; fixed orphaned plan rows on meal delete. |
| 4 | 0.1 | 2026-07-25 | TestFlight | Recipe usage counts, deletes, meal editing, staple markers. |
| 2 | 0.1 | 2026-07-24 | TestFlight | Change-your-own-password flow. |
| 1 | 0.1 | 2026-07-24 | TestFlight | First upload — icon, export options, and the `make ios-testflight` path. |

Builds 3, 7, 8, 9 and 10 don't exist in App Store Connect. The commit
"iOS: set the build counter clear of App Store Connect" suggests the counter was
jumped past numbers already taken there, which is also why step 1 above says to
check App Store Connect rather than this file.

Rows above build 14 are reconstructed from upload dates and commit history —
this ledger did not exist yet, which is the reason it does now. Treat build 14
onwards as recorded, and anything earlier as best effort.

## App Store status

| | |
|---|---|
| App record | `com.marcuslab.meals`, App Store Connect app id `6794266229` |
| Registered name | **Meal Options Planner** — to be renamed before submission (see [AppStore/metadata.md](AppStore/metadata.md)) |
| Version record | 1.0, `PREPARE_FOR_SUBMISSION` |
| Ever submitted? | No. Nothing has been through App Review. |
