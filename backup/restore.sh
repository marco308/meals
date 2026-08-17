#!/bin/sh
# Restore a dump into a database, and say what came back.
#
#   restore.sh latest                        # newest dump -> meals_restore_check
#   restore.sh --target meals_restore_check --drop /backups/daily/meals-….dump
#   restore.sh --target meals --drop /backups/daily/meals-….dump   # the real thing
#
# This is the script someone runs on a bad day, so it depends on nothing but
# pg_restore, psql and (for an off-node copy) gpg — no config file, no image of
# its own. It runs unchanged inside the backup container and on a laptop with
# libpq installed.
#
# It restores a *whole* dump. Cherry-picking tables looks tempting when only
# one thing was lost, but household_id columns carry no ON DELETE and the
# delete order lives in application code (decision Q20), so a selective restore
# can leave rows behind that the app would never have produced. Restore whole,
# into a scratch database, and copy out of it if you need surgery.
set -eu

TARGET="${TARGET:-meals_restore_check}"
DROP=0
FORCE=0
DUMP=""

usage() {
    sed -n '2,14p' "$0" | sed 's/^# \{0,1\}//'
    exit "${1:-0}"
}

while [ $# -gt 0 ]; do
    case "$1" in
        --target) TARGET="$2"; shift 2 ;;
        --drop) DROP=1; shift ;;
        # Restoring over a database that is in use is a deliberate act, not a
        # typo, so it needs saying out loud.
        --force) FORCE=1; shift ;;
        -h | --help) usage 0 ;;
        -*) echo "unknown option: $1" >&2; usage 1 ;;
        *) DUMP="$1"; shift ;;
    esac
done

[ -n "${DUMP}" ] || usage 1

if [ -n "${PGPASSWORD_FILE:-}" ]; then
    PGPASSWORD="$(cat "${PGPASSWORD_FILE}")"
    export PGPASSWORD
fi
export PGHOST="${PGHOST:-db}"
export PGPORT="${PGPORT:-5432}"
export PGUSER="${PGUSER:-meals}"
export PGDATABASE="${PGDATABASE:-meals}"

BACKUP_DIR="${BACKUP_DIR:-/backups}"
if [ "${DUMP}" = "latest" ]; then
    DUMP="$(ls -1 "${BACKUP_DIR}"/daily/meals-*.dump 2>/dev/null | sort -r | head -1)"
    [ -n "${DUMP}" ] || { echo "no dumps in ${BACKUP_DIR}/daily" >&2; exit 1; }
    echo "==> newest dump: ${DUMP}"
fi
[ -f "${DUMP}" ] || { echo "no such dump: ${DUMP}" >&2; exit 1; }

if [ "${TARGET}" = "${PGDATABASE}" ] && [ "${FORCE}" != "1" ]; then
    cat >&2 <<EOF
refusing to restore into "${TARGET}", the database this connection uses.

Restore into a scratch database first and look at it:
    restore.sh --target ${PGDATABASE}_restore_check --drop ${DUMP}

If you really are recovering the live database, stop the API first (so it
cannot write into a half-restored schema) and pass --force.
EOF
    exit 1
fi

# An off-node copy arrives encrypted; decrypt it to a temp file rather than
# leaving a plaintext dump next to the ciphertext.
CLEARTEXT=""
# An `&&` here would make the trap's status the script's on the ordinary path
# where there is nothing to clean up: a restore that worked would exit 1.
cleanup() { if [ -n "${CLEARTEXT}" ]; then rm -f "${CLEARTEXT}"; fi; }
trap cleanup EXIT
case "${DUMP}" in
    *.gpg)
        CLEARTEXT="$(mktemp)"
        echo "==> decrypting ${DUMP}"
        if [ -n "${BACKUP_PASSPHRASE_FILE:-}" ]; then
            gpg --batch --quiet --pinentry-mode loopback \
                --passphrase-file "${BACKUP_PASSPHRASE_FILE}" \
                --decrypt --output "${CLEARTEXT}" "${DUMP}"
        else
            # No passphrase file: ask. This is the restore-from-Drive path,
            # where the passphrase is in a password manager and a human is
            # watching.
            gpg --quiet --decrypt --output "${CLEARTEXT}" "${DUMP}"
        fi
        DUMP="${CLEARTEXT}"
        ;;
esac

echo "==> verifying the dump is readable"
pg_restore --list "${DUMP}" > /dev/null

if [ "${DROP}" = "1" ]; then
    echo "==> dropping and creating ${TARGET}"
    psql --dbname=postgres --quiet --set=ON_ERROR_STOP=1 -c "DROP DATABASE IF EXISTS \"${TARGET}\" WITH (FORCE)"
    psql --dbname=postgres --quiet --set=ON_ERROR_STOP=1 -c "CREATE DATABASE \"${TARGET}\""
elif ! psql --dbname=postgres --quiet --tuples-only --no-align \
    -c "SELECT 1 FROM pg_database WHERE datname = '${TARGET}'" | grep -q 1; then
    echo "==> creating ${TARGET}"
    psql --dbname=postgres --quiet --set=ON_ERROR_STOP=1 -c "CREATE DATABASE \"${TARGET}\""
fi

echo "==> restoring into ${TARGET}"
# --no-owner/--no-privileges so a dump taken as one role restores as another:
# on the day it matters the database may well be a fresh container.
pg_restore --dbname="${TARGET}" --no-owner --no-privileges --exit-on-error "${DUMP}"

echo "==> what came back"
# The point of a restore drill is seeing the household's own data, not a
# schema. These are the tables that would be missed: the library, the plan,
# the list, and the record of what has actually been cooked.
psql --dbname="${TARGET}" --quiet <<'SQL'
SELECT 'households' AS relation, count(*) FROM households
UNION ALL SELECT 'users', count(*) FROM users
UNION ALL SELECT 'recipes', count(*) FROM recipes
UNION ALL SELECT 'ingredients', count(*) FROM ingredients
UNION ALL SELECT 'meals', count(*) FROM meals
UNION ALL SELECT 'plans', count(*) FROM plans
UNION ALL SELECT 'shopping_lists', count(*) FROM shopping_lists
UNION ALL SELECT 'list_items', count(*) FROM list_items
UNION ALL SELECT 'list_item_sources', count(*) FROM list_item_sources
UNION ALL SELECT 'cooked_events', count(*) FROM cooked_events
ORDER BY 1;
SQL

echo "==> restored into ${TARGET}"
echo "    Point an API at it to check it end to end:"
echo "    DATABASE_URL=postgresql+asyncpg://${PGUSER}:…@${PGHOST}:${PGPORT}/${TARGET}"
