# Backups

A household's recipe library is mostly irreplaceable. Recipes ingested from a
URL can in principle be fetched again; the hand-typed family ones, the plan,
and the record of what you have actually cooked cannot. This directory is the
job that protects them, and — more importantly — the way back.

The shape:

- **Nightly `pg_dump -Fc`**, in a sidecar container next to the database.
- **Verified at write time.** A dump is only named `meals-*.dump` once
  `pg_restore --list` has read it back, so corruption is found on the day it is
  written rather than the day it is needed.
- **Rotated**: 7 daily, 4 weekly. The first dump of each ISO week is hard-linked
  into `weekly/`, so it costs no extra disk until `daily/` expires its copy.
- **Optionally copied off the node**, gpg AES-256 encrypted, to anywhere
  [rclone](https://rclone.org) can reach. A dump sitting next to the database
  survives `DROP TABLE` and nothing else.
- **Noisy when it stops.** The container reports unhealthy once the newest dump
  is older than 36h, and every run writes one event line to stdout.

What it deliberately isn't: WAL archiving / point-in-time recovery. This is a
few megabytes of family recipes, and the machinery would be larger than the
thing it protects. Losing at most a day of edits is the accepted trade.

## Running it

It is in [`docker-compose.yml`](../docker-compose.yml) already, so a self-hosted
stack backs itself up from the first `make up`. The first run happens as soon as
the container starts (nothing to restore from is the worst state to be in), and
every one after that at `BACKUP_AT`.

```bash
docker compose exec backup backup.sh          # take one now
docker compose exec backup freshness.sh       # is there a recent one?
docker compose exec backup ls -lh /backups/daily /backups/weekly
docker compose logs backup                    # one line per run
```

| Variable | Default | What it does |
|---|---|---|
| `PGHOST` `PGPORT` `PGUSER` `PGDATABASE` | `db` `5432` `meals` `meals` | Where to dump from |
| `PGPASSWORD` / `PGPASSWORD_FILE` | — | Password, or a file holding it (docker secret) |
| `BACKUP_DIR` | `/backups` | Holds `daily/` and `weekly/`. Mount a volume here |
| `BACKUP_AT` | `03:15` | Daily run time, **UTC** |
| `BACKUP_ON_START` | `auto` | `auto` dumps at startup only when there are none; `always`, `never` |
| `KEEP_DAILY` `KEEP_WEEKLY` | `7` `4` | Retention, local and offsite |
| `RCLONE_REMOTE` | — | e.g. `gdrive:meals-backups`. Empty means local-only |
| `RCLONE_CONFIG_FILE` | — | Read-only rclone config (a secret or a bind mount); copied to `RCLONE_CONFIG` (`/tmp/rclone.conf`), because rclone rewrites its config when it refreshes a token |
| `BACKUP_PASSPHRASE_FILE` | — | File holding the gpg passphrase. **Required** whenever `RCLONE_REMOTE` is set |
| `STALE_AFTER_HOURS` | `36` | How old the newest dump may be before the healthcheck fails |
| `LOG_FORMAT` | `json` | `text` for a human at a terminal |

The image is built `FROM postgres:17-alpine` to match the database, and that
matters: `pg_dump` refuses to dump a server newer than itself, so both tags move
together.

If you point `BACKUP_DIR` at a **bind-mounted host directory** rather than a
volume, set `user:` on the service to your own uid (`user: "1000:1000"`) —
otherwise the dumps are root-owned and mode 0600, and the person recovering the
database can't read them without sudo. That is the wrong friction on the day it
matters.

## Getting a copy off the machine

Anywhere else on the LAN is already a large improvement; off-site is better.
Both are the same two settings — an rclone remote and a passphrase:

```yaml
    environment:
      RCLONE_REMOTE: gdrive:meals-backups
      RCLONE_CONFIG_FILE: /run/secrets/rclone.conf
      BACKUP_PASSPHRASE_FILE: /run/secrets/backup-passphrase
```

Dumps carry bcrypt password hashes, bearer tokens and email addresses, so the
copy that leaves is encrypted first: `gpg --symmetric --cipher-algo AES256`
with that passphrase, uploaded as `meals-….dump.gpg`. If `RCLONE_REMOTE` is set
and `BACKUP_PASSPHRASE_FILE` is not, the run **fails** rather than quietly
sending a household's data somewhere in the clear.

Symmetric encryption on purpose: the only thing needed to read a dump back is a
passphrase you can keep in a password manager, rather than a key file that gets
lost alongside the server it was protecting. **Store it somewhere that is not
the server.** A dump you cannot decrypt is not a backup.

Remote retention deletes named files rather than syncing the directory, because
a sync mirrors deletions: the morning after the backup disk dies, it would
helpfully empty the off-node copy too.

The deployment this repo runs on uses Google Drive, wired up by the (untracked,
machine-specific) `deploy/setup-gdrive.sh`: `rclone config` makes a Drive remote
with the **`drive.file` scope**, so the credential can only ever see files it
uploaded itself and not the rest of the Drive. Nothing about the job is
Drive-specific — S3, B2, another box in the house, all the same setting.

## Restoring

`restore.sh` depends on nothing but `pg_restore`, `psql` and (for an encrypted
copy) `gpg`, so it runs unchanged inside the container or on a laptop with
libpq installed. It restores a **whole** dump: `household_id` columns carry no
`ON DELETE` and the delete order lives in application code (decision Q20), so a
selective restore can leave rows behind that the app itself would never have
produced. Restore whole into a scratch database, then copy out of it if you
need surgery.

**Check a backup is real** (do this occasionally; it is the only way to know):

```bash
docker compose exec backup restore.sh --target meals_restore_check --drop latest
```

It prints what came back — households, recipes, list items, cooked events. CI
runs exactly this against seeded data on every push, so the path stays warm.

**Recover the live database.** Stop the API first, so nothing writes into a
half-restored schema:

```bash
docker compose stop api mcp
docker compose exec backup restore.sh --target meals --drop --force /backups/daily/meals-….dump
docker compose start api mcp
```

**Recover from the offsite copy**, on any machine with rclone:

```bash
rclone copy gdrive:meals-backups/daily/meals-….dump.gpg .
gpg --decrypt --output meals.dump meals-….dump.gpg     # asks for the passphrase
pg_restore --dbname=meals --no-owner --no-privileges meals.dump
```

Nothing above needs this repo's code, which is the point: recovery must not
depend on the thing being recovered.

## Noticing when it stops

A backup job that silently fails is worse than none, because it is believed.
Three signals, in increasing order of how much infrastructure they need:

1. **The container healthcheck** (`freshness.sh`): unhealthy once the newest
   dump passes `STALE_AFTER_HOURS`. Free, visible in `docker ps`.
2. **The event line.** Every run writes one, in the same shape as the API's own
   event log:

   ```json
   {"ts":"2026-08-17T02:30:04Z","level":"INFO","logger":"meals.backup","msg":"backup",
    "outcome":"ok","bytes":1348221,"duration_s":2,"offsite":"ok","daily_kept":7,"weekly_kept":4}
   ```

   `outcome=error` carries a `stage` (`dump`, `verify`, `offsite`) so a failure
   says whether the database was unreachable or the upload was.
3. **An alert on the absence of that line.** On this deployment, a Grafana rule
   over Loki:

   ```logql
   sum(count_over_time({stack="meals", service="meals_backup"} | json | outcome = "ok" [36h]))
   ```

   Alerting when it drops below 1, with the no-data state also alerting —
   because "the backup container has stopped writing anything at all" is the
   failure this is for.

## The restore drill

An untested backup is a hypothesis. This one has been run, against the real
deployment, on **2026-08-17**:

1. `restore.sh --target meals_restore_check --drop latest` inside the backup
   container — a scratch database, so the live one is never in the blast radius.
2. A throwaway API service pointed at `meals_restore_check`. It ran
   `alembic upgrade head` against the restored schema (a no-op, which is the
   answer you want) and answered `/healthz`.
3. A token minted for the household's own user *in the scratch database*, and
   the data read back through the public API rather than by counting rows:

   ```
   shopping list:  20 items, 20 checked off, 2 hidden staples
                   6 aisles represented, quantity present on 16 items
   recipes:        17
   plans:          3
   meals:          12
   cooked history: 6 meals ever cooked, 6 cookings in total, most recent 2026-08-10
   ```

4. Scratch database dropped, throwaway service removed.

That third step is the one worth keeping when you repeat this. Row counts prove
a dump restored; the shopping list coming back with its aisles, quantities and
check-offs — and `times_cooked` still counting — proves the *application* was
restored, which is the actual question.

Two things the drill established beyond "it works":

- The dumps must be owned by a human. The first version of this ran as root and
  wrote mode-0600 dumps into a bind-mounted directory, so the person recovering
  the database couldn't so much as list them without sudo. The service runs as
  the host user now.
- Restoring took seconds, so nothing here needs to be faster. The slow part of
  a real recovery would be noticing, which is what the alerting above is for.
