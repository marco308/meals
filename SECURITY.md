# Security policy

## Reporting a vulnerability

Please **don't open a public issue.** Use GitHub's
[private vulnerability reporting](https://github.com/marco308/meals/security/advisories/new)
(Security → Report a vulnerability), which reaches me directly.

This is a one-person project, not a company with an on-call rota. Expect an
acknowledgement within a week. If you don't get one, feel free to nudge by
opening a public issue that says only "please check your security advisories" —
no details.

There's no bounty. There is genuine gratitude, and credit in the fix commit if
you'd like it.

## Scope

In scope: the backend API, the MCP server, the published skill and prompt pack,
the iOS app, and the deployment guidance in this repo.

`https://meals.marcuslab.uk` is a personal instance holding one household's real
data. Please **don't test against it** — run your own with `make dev` (a full
stack in Docker) or `make run` (SQLite, no services). Reading the unauthenticated
`/skill`, `/prompt-pack` and `/healthz` endpoints is fine.

Out of scope: anything requiring a stolen credential or physical device access,
and reports that amount to "self-hosting this insecurely is insecure".

## What the threat model already assumes

Worth knowing before you write it up — these are deliberate, documented choices,
not oversights:

- **A household is the entire authorisation boundary.** Everyone in a household
  sees and can edit all of its recipes, plans and shopping lists. There are no
  roles, no per-user permissions, and no admin. Inviting someone in
  (`POST /auth/invites`) grants them all of it, which is why the invite code
  should be treated like a password.
- **Registration creates a *new* household** (decision Q19). An account never
  joins existing data without a valid single-use invite code. Reports that a
  stranger's signup can see somebody else's library are exactly what this policy
  is for — but check you're not looking at a pre-Q19 deployment first.
- **`REGISTRATION_ENABLED=false` still honours invites**, by design: a closed
  server must remain able to admit the people its household chose.
- **API tokens (PATs) are bearer credentials** stored as SHA-256 hashes, shown
  once, and deliberately survive a password change so that rotating a password
  doesn't silently break every AI client. Revoke them with
  `DELETE /auth/tokens/{id}`.
- **The remote MCP server holds no credentials.** In http mode it forwards each
  request's `Authorization` header to the API verbatim. A server-side token
  fallback would be a vulnerability, and there are tests asserting one hasn't
  been added.
- **Rate limiting is per-process and in-memory**, which is honest for a
  single-replica deployment and inadequate for a scaled-out one. Known; see
  BACKLOG.md.
- **The backend never calls an LLM** (decision Q13) and never executes anything
  it fetches. Recipe ingestion is schema.org JSON-LD extraction only.
- **There is no password reset and no account deletion yet.** Both are known
  gaps in BACKLOG.md rather than things to report.

## Self-hosting: the things that actually bite

- Set `REGISTRATION_ENABLED=false` once your household's accounts exist, and
  hand out invites instead.
- `cors_origins` defaults to `["*"]`. That's safe for a bearer-token API with no
  cookies — no browser will attach a credential automatically — but narrow it
  anyway if you put a web frontend in front of it.
- Terminate TLS at your ingress. The app speaks plain HTTP and assumes something
  in front of it doesn't.
- Back up Postgres. Nothing in this repo does it for you.
