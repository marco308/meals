# App Store screenshots

```bash
make ios-screenshots
```

Roughly 90 seconds, no arguments, no setup. It starts a throwaway API on SQLite,
seeds the demo household, creates a simulator of its own, freezes the status bar
at 9:41, drives the app through
[`MealsUITests/ScreenshotTests.swift`](../Meals/MealsUITests/ScreenshotTests.swift),
and leaves PNGs in `ios/screenshots/<device-type>/`.

The output is gitignored: it's a build product, and the recipe is the thing
worth keeping. Regenerate rather than retouch — a screenshot that no longer
matches the app is worse than no screenshot, and Apple compares.

## What it shoots

| File | Screen | Why it's in the set |
|---|---|---|
| `01-plan.png` | The plan | A pool of options grouped by slot, one already cooked. The thing that makes this not-a-calendar. |
| `02-shopping-list.png` | Shopping | Aisle-sorted, mid-shop, every line saying which meals it's for. The distinctive screen. |
| `03-recipe-detail.png` | A recipe | Times, cooked history, and per-ingredient aisle and value badges. |
| `04-recipe-library.png` | Recipes | That there's a library, and it's searchable. |
| `05-settings.png` | Settings | Your server, your data — the honest version of what this app is. |

## Nothing here touches your machine's state

- The database is a temp file, deleted on exit.
- The simulator is created and destroyed per run (`KEEP_SIM=1` to keep it), so
  your own simulators and their data are never erased.
- The API runs on port 8123, and the script **aborts if that port is already
  taken** rather than using whatever is there.

That last one is not paranoia. The first version of this passed the server URL
to the test through an environment variable, the variable silently didn't
arrive, the test fell back to port 8000 — where a real dev stack was running —
and produced five polished screenshots of a real household's shopping list. So
now the port is a fixed contract between the script and the test, and the test
asserts the Settings screen shows the server it expected before it will publish
anything.

## Sizes

Apple requires **one** iPhone size: 6.9" (1320 × 2868), which is what the
default `iPhone-17-Pro-Max` produces. Everything smaller is scaled from it by
App Store Connect. To shoot another size for real:

```bash
DEVICES="iPhone-17-Pro-Max iPhone-16-Plus" make ios-screenshots
```

There is no iPad set because the app is iPhone-only
(`TARGETED_DEVICE_FAMILY: "1"`).

## When it breaks

The failing xcodebuild log is copied to `ios/screenshots/last-failure.log`.
The usual causes are a UI change that moved something the test taps by name,
and a tab or title that got renamed — both of which are the test doing its job.
