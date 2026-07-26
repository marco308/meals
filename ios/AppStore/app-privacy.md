# App Privacy questionnaire

The answers to fill in under **App Privacy** in App Store Connect, and the
reasoning, because this one is genuinely ambiguous for a self-hosted app and
future-you will want to know why it was answered this way.

## The judgement call

Apple's definition of "collect" is *transmitted off the device and retained
beyond what's needed to service the request*. For Meals, data leaves the device
and is retained — but by a server the **user** runs, not by the developer. That
is arguably not collection by anyone Apple is asking about.

**Answer as if it is collected anyway.** Over-declaring costs nothing but a few
labels on the product page. Under-declaring is a rejection now and a removal
later, and the argument that "the developer never sees it" is one you'd be
making to an automated review after the fact. The privacy nutrition labels this
produces are still honest: the data does leave the phone.

What must *not* be declared is anything used for tracking or advertising. There
is none, and that's the part that actually matters to a reader.

## Answers

### Do you or your third-party partners collect data from this app? — **Yes**

### Contact Info → Email Address

| Question | Answer |
|---|---|
| Used for | App Functionality |
| Linked to the user's identity | Yes |
| Used for tracking | No |

It's the account identifier. Nothing else.

### Contact Info → Name

| Question | Answer |
|---|---|
| Used for | App Functionality |
| Linked to the user's identity | Yes |
| Used for tracking | No |

The display name, so other people in the household know who's who.

### User Content → Other User Content

| Question | Answer |
|---|---|
| Used for | App Functionality |
| Linked to the user's identity | Yes |
| Used for tracking | No |

Recipes, meals, plans, shopping lists and cooked history — the substance of the
app.

### Everything else — **not collected**

Explicitly not, and worth being able to say so:

- No Identifiers (no device id, no advertising id, no user id beyond the account)
- No Usage Data (no analytics of any kind — no first-party telemetry either)
- No Diagnostics (no crash reporting SDK)
- No Location, Contacts, Health, Financial Info, Purchases, Search History,
  Browsing History, Sensitive Info, Photos, Audio
- No third-party SDKs at all, so nothing collects anything on our behalf

### Tracking — **No**

Nothing is combined with data from other companies' apps or sites, and nothing
is shared with data brokers. The app never calls `ATTrackingManager`, has no
`NSUserTrackingUsageDescription`, and needs neither.

## Privacy Policy URL

`https://meals.marcuslab.uk/privacy` — rendered from
[PRIVACY.md](../../PRIVACY.md), so it can't drift from the copy in the repo.

## Privacy manifest

The app has no `PrivacyInfo.xcprivacy` and doesn't need one: the requirement
covers apps using specified "required reason" APIs or third-party SDKs from
Apple's list. This app uses neither — no `UserDefaults`-adjacent reason APIs
beyond the ordinary, no file-timestamp or disk-space APIs, and no SDKs. If a
dependency is ever added, this needs revisiting before it will upload.

> The app *does* use `UserDefaults` (for the server URL), which is on the
> required-reason list. A manifest is required when an SDK you ship uses it;
> for a first-party app Apple's upload check has not demanded one here. If an
> upload is ever rejected with ITMS-91053, add a `PrivacyInfo.xcprivacy`
> declaring `NSPrivacyAccessedAPICategoryUserDefaults` with reason `CA92.1`
> (access to app's own defaults) and re-upload.
