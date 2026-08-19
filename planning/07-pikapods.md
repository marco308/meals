# 07 — PikaPods listing (idea post + submission notes)

Copy-paste source for the **feedback.pikapods.com** suggestion post, which is
how their catalogue takes app requests, plus a record of the packaging work
done so the post could make its claims plainly.
Background and why this channel is worth doing at all: [`06-marketing.md`](06-marketing.md)
§1a. Issue [#23](https://github.com/marco308/meals/issues/23) tracks the action.

Two things to know before posting:

- The forum sign-in is the account owner's, so this file is the draft, not the
  post. Paste it into the "Share your idea…" box (rich text editor, so the
  headings and bullets below survive) and put the title in the field under it.
- **Post first, email second.** `hello@pikapods.com` is in the issue, but a
  public suggestion is a vote-able thing their catalogue already reads, and it
  costs nothing to point the email at it afterwards.

## Title

```
Add YAMP (Yet Another Meal Planner)
```

## Body

> YAMP is an open source, self-hosted meal planner: a recipe library, plans,
> and an aisle-sorted shopping list, with a documented REST API and a
> first-party MCP server so it can be driven by your own AI assistant. It is
> AGPL-3.0, I am the author, and I would be glad to take the 20% revenue
> share.
>
> ### Why it might fit the food category
>
> Grocy and Mealie are both here and both good. YAMP is deliberately a
> different shape:
>
> - **Plans are pools, not calendars.** A plan is five dinner *options* in no
>   particular order. There is no Mon-to-Sun grid, so a takeaway on Wednesday
>   breaks nothing and nobody has to re-plan the week.
> - **Bring your own AI.** There is no LLM inside the app and there never will
>   be. It ships an MCP server with 29 task-level tools, and the running
>   instance publishes its own skill and prompt pack at `/skill` and
>   `/prompt-pack`, so an assistant learns the API from the server it is
>   pointed at. No established meal planner ships an official MCP server, and
>   the community has written dozens of third-party wrappers for the ones that
>   don't.
> - **The shopping list knows why every line is on it.** Each item carries the
>   meals that put it there, quantities merge only on an exact unit match, and
>   removing a meal removes exactly its own contribution.
> - **There is a native iPhone app on the App Store** ("Yet Another Meal
>   Planner"), offline-first, so the list still works in the shop with no
>   signal. A pod would be its backend, which is the part people currently
>   have to build themselves.
> - **One instance serves several families.** Registering creates a household;
>   other people join it with a single-use invite code. `REGISTRATION_ENABLED=false`
>   closes public signup while still honouring invites, so a pod can be shared
>   deliberately rather than left open.
>
> ### Technical fit
>
> - **One container, and it is the whole product**: API, the web client at
>   `/app`, the skill it publishes about itself at `/skill`, and the MCP
>   endpoint at `/mcp`, all served by one process on one port.
> - **`docker run` with no arguments works.** It defaults to SQLite under
>   `/data`, so the cheapest pod needs no database add-on; `DATABASE_URL`
>   switches it to Postgres for anyone who wants that. Migrations run at
>   startup on both, so an upgrade is a new image and nothing else.
>
>   ```
>   docker run -d -p 8000:8000 -v yamp-data:/data ghcr.io/marco308/meals:latest
>   ```
>
> - **Runs as uid 1000**, writes to exactly one path (`/data`), and the rest of
>   the filesystem is root-owned and untouched at runtime.
> - **Published for linux/amd64 and linux/arm64** on every release tag, from
>   the same build CI boots on every push.
> - **`GET /healthz`** is unauthenticated and cheap, and the image carries its
>   own `HEALTHCHECK`. Logs are JSON on stdout, one line per request.
> - **All configuration is environment variables**: `DATABASE_URL`,
>   `REGISTRATION_ENABLED`, `MARKETING_URL`, `LOG_FORMAT=json`, `MCP_ENABLED`,
>   optional `SMTP_*` (password reset only, plain SMTP so any relay works),
>   optional `METRICS_TOKEN` for Prometheus.
> - **No Redis, no queue, no background workers, no cron.** The only outbound
>   requests the server ever makes are fetching a recipe page a user asked it
>   to import, and SMTP if password reset is configured. It never calls an LLM.
> - **The whole thing is tested in CI on every push**, including the
>   one-container shape: the image is run with no arguments and no database
>   service, then an MCP client calls a tool through it and gets real data
>   back.
>
> ### Links
>
> - Repo: https://github.com/marco308/meals
> - Site: https://marco308.github.io/meals/
> - Image: `ghcr.io/marco308/meals` (and `ghcr.io/marco308/meals-mcp` for the
>   MCP server alone, if you ever want it on its own)
> - Reference deployment: https://github.com/marco308/meals/blob/main/docker-compose.yml
> - Licence: AGPL-3.0
>
> Happy to change anything about the packaging that does not fit your
> conventions: data path, user, ports, config names.

## What was fixed before posting

The first draft of this post carried three caveats. They are gone, which is
why the post above can be short about the packaging:

1. **Published images and a release process.** `.github/workflows/release.yml`
   builds both images for amd64 and arm64 on a `v*` tag, pushes them to GHCR,
   runs the published API image with no arguments to prove it works, and cuts
   the GitHub release. Also an [awesome-selfhosted](https://github.com/awesome-selfhosted/awesome-selfhosted)
   entry criterion, so it pays for itself twice.
2. **Non-root, one writable path.** Both images run as uid 1000; the API's
   default database is SQLite at `/data/meals.db` and `/data` is the only
   thing it owns.
3. **The second container is no longer required.** The MCP server is mounted
   in the API process at `/mcp` (`app/mcp_mount.py`), so a single container
   answers everything. Its own image still exists for split deployments, and
   `MCP_ENABLED=false` removes the built-in one.

## Still worth doing, whether or not they answer

- The marketing site's "Run it" section still teaches `make up`, which needs a
  checkout. Add the `docker run` line **after** the first release is published
  and pullable, not before.
- `deploy/` still builds images on the swarm node. It could pull the published
  digest instead, which is the other half of the BACKLOG's image-pipeline item.
