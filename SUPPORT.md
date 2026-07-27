# Support

Meals is a free, open-source, one-person project. There is no support desk and
no service-level anything — but questions do get answered.

**Ask here:** <https://github.com/marco308/meals/issues>

Expect a reply within a week. If something is private, use GitHub's
[private reporting form](https://github.com/marco308/meals/security/advisories/new)
instead.

## Before you open an issue

Say which server you're talking to (your own, or someone else's), the app
version and build from **Settings → About**, and what you expected to happen.
For anything server-side, the response body of the failing request is worth ten
paragraphs of description — every error this API returns tries to tell you what
to do instead.

## The question everyone asks first

### The app asks for a server URL. What do I put in it?

Meals has no cloud. You run the server, so the app needs to know where it is.
You have two options:

1. **Run it yourself.** It is free, open source, and one command:

   ```bash
   git clone https://github.com/marco308/meals && cd meals && make dev
   ```

   That starts the API and a database in Docker at `http://localhost:8000`. To
   use it from a phone rather than the simulator, put it somewhere your phone
   can reach — a machine on your home network, or a small VPS. The
   [README](https://github.com/marco308/meals#readme) covers deployment.

2. **Join a server someone else runs.** If a member of your household already
   has one, ask them for its URL and an invite code (Settings → Invite someone,
   on their device). Enter the code when you create your account and you land in
   their household, sharing its recipes, plan and shopping list.

`meals.marcuslab.uk` is the author's own household instance, not a public
service. Registration on it is closed, so it won't accept an account you create.

### Do I need an account?

Yes, on your own server. The account is how the server knows which household's
shopping list to hand back, and it never leaves that server.

### How do I delete my account?

**Settings → Delete account.** It asks for your password and a typed
confirmation, then deletes immediately — no grace period, no undo. If you are
the last person in your household, its recipes, plans and lists are deleted
with you.

### Can I use it offline?

The shopping list, yes — that's the point of it. Ticking items off, adding
things, and putting items back all work with no signal: they render instantly,
queue on disk, survive a relaunch, and sync when you're back in range. Plans
and the recipe library show a cached copy offline but need a connection to
change.

### Why are imperial units rejected?

Everything is metric (g, kg, ml, l) or a count of a natural unit — "2 tins",
"3 cloves". One convention means quantities from different recipes merge into
one shopping-list line instead of sitting there as "1 lb" and "450 g". If you
type an imperial amount, the error tells you the exact conversion to use.

### An AI assistant said it couldn't read a recipe page

Recipe import reads structured recipe data (schema.org JSON-LD) from the page
and nothing else — the server never calls an AI model. Pages without it return
a 422 that tells the assistant to read the page itself and submit the recipe
directly, which any capable assistant can do. You can also just type the recipe
in.

## Reporting a security problem

See [SECURITY.md](SECURITY.md). Please don't test against
`meals.marcuslab.uk` — it holds one household's real data. Run your own.
