#!/bin/sh
# One backup run: dump, verify it can be read back, rotate, and put an
# encrypted copy somewhere that is not this machine.
#
# Safe to run by hand at any time (`docker compose exec backup backup.sh`) —
# it takes no locks the database cares about and never touches an existing
# dump other than to expire it.
#
# The order below is the whole design. A dump is only named `meals-*.dump`
# once `pg_restore --list` has read it, so a half-written or corrupt file can
# never be counted as a backup, expire a good one, or be uploaded. Corruption
# gets found on the day it is written, not on the day it is needed.
set -eu

BACKUP_DIR="${BACKUP_DIR:-/backups}"
KEEP_DAILY="${KEEP_DAILY:-7}"
KEEP_WEEKLY="${KEEP_WEEKLY:-4}"
# Off-node copy. Empty means "local only", which is a backup against DROP TABLE
# and nothing else — see backup/README.md.
RCLONE_REMOTE="${RCLONE_REMOTE:-}"
# Dumps carry password hashes, bearer tokens and email addresses, so anything
# leaving the LAN is encrypted first. No passphrase, no upload: this script
# refuses rather than quietly sending a household's data somewhere in the clear.
BACKUP_PASSPHRASE_FILE="${BACKUP_PASSPHRASE_FILE:-}"
LOG_FORMAT="${LOG_FORMAT:-json}"

DAILY_DIR="${BACKUP_DIR}/daily"
WEEKLY_DIR="${BACKUP_DIR}/weekly"
# Which ISO week the weekly slot already holds. A marker beats parsing week
# numbers back out of file names, and it keeps the two directories to one
# naming scheme so retention is the same three lines for both.
WEEK_MARKER="${WEEKLY_DIR}/.week"

# Dumps are readable by their owner only, wherever they land.
umask 077

# ------------------------------------------------------------------ logging
#
# One line per run, same shape as the API's own event log (app/observability.py):
# JSON with ts/level/logger/msg in production, key=value text for a human at a
# terminal. That is what makes `{stack="meals"} | json | logger="meals.backup"`
# a query that spans the app and the thing backing it up, and it is what the
# staleness alert watches for.
log_event() {
    _msg="$1"
    shift
    _now="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    if [ "${LOG_FORMAT}" = "text" ]; then
        printf '%s meals.backup %s  %s\n' "${_now}" "${_msg}" "$*"
        return
    fi
    _fields=""
    for _pair in "$@"; do
        _key="${_pair%%=*}"
        _value="${_pair#*=}"
        case "${_value}" in
            # Numbers stay numbers so a dashboard can graph them; everything
            # else is quoted and escaped.
            '' | *[!0-9]*) _value="\"$(printf '%s' "${_value}" | sed 's/\\/\\\\/g; s/"/\\"/g')\"" ;;
        esac
        _fields="${_fields},\"${_key}\":${_value}"
    done
    printf '{"ts":"%s","level":"INFO","logger":"meals.backup","msg":"%s"%s}\n' "${_now}" "${_msg}" "${_fields}"
}

fail() {
    # stage= says which half of the job broke, which is the difference between
    # "the database is unreachable" and "Google Drive is having a day".
    _logged=1
    log_event backup outcome=error stage="$1" detail="$2"
    exit 1
}

# Every run ends in exactly one event line, including the ways of ending nobody
# thought of: an alert that watches for "a successful backup happened" is only
# as good as the guarantee that an unsuccessful one says so.
_logged=0
on_exit() {
    _status=$?
    if [ "${_logged}" = "0" ] && [ "${_status}" != "0" ]; then
        log_event backup outcome=error stage=unexpected detail="the run exited with status ${_status}"
    fi
}
trap on_exit EXIT

# ------------------------------------------------------------------ setup

# A swarm secret is a file, not an environment variable, so accept both.
if [ -n "${PGPASSWORD_FILE:-}" ]; then
    PGPASSWORD="$(cat "${PGPASSWORD_FILE}")"
    export PGPASSWORD
fi
export PGHOST="${PGHOST:-db}"
export PGPORT="${PGPORT:-5432}"
export PGUSER="${PGUSER:-meals}"
export PGDATABASE="${PGDATABASE:-meals}"

mkdir -p "${DAILY_DIR}" "${WEEKLY_DIR}"

if [ -n "${RCLONE_REMOTE}" ]; then
    if [ -z "${BACKUP_PASSPHRASE_FILE}" ]; then
        fail offsite "RCLONE_REMOTE is set but BACKUP_PASSPHRASE_FILE is not; refusing to upload an unencrypted dump"
    fi
    # rclone rewrites its config whenever it refreshes an OAuth token, so the
    # config it uses cannot be the read-only file a swarm secret mounts. Copy
    # the secret to somewhere writable — losing the rewritten copy on restart
    # costs nothing, because what actually authorises the upload is the refresh
    # token that came in the secret.
    # Under /tmp rather than somewhere tidier because this container is
    # expected to run as an unprivileged uid (so the dumps on a bind-mounted
    # host directory belong to a human), and /tmp is the one place any uid can
    # write. umask 077 above applies to the copy.
    export RCLONE_CONFIG="${RCLONE_CONFIG:-/tmp/rclone.conf}"
    mkdir -p "$(dirname "${RCLONE_CONFIG}")"
    if [ -n "${RCLONE_CONFIG_FILE:-}" ] && [ ! -f "${RCLONE_CONFIG}" ]; then
        cp "${RCLONE_CONFIG_FILE}" "${RCLONE_CONFIG}"
    fi
    # An absent config is a legitimate setup (a plain path is a local remote),
    # but rclone says so on stderr every run unless the file exists.
    touch "${RCLONE_CONFIG}"
fi

started="$(date -u +%s)"
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
name="meals-${stamp}.dump"
partial="${DAILY_DIR}/.${name}.part"
final="${DAILY_DIR}/${name}"

# ------------------------------------------------------------------ dump

# -Fc (custom format) is the only one worth taking: compressed, and
# pg_restore can list it, restore it selectively, and parallelise. The
# database is a few megabytes of family recipes — PITR/WAL archiving would be
# more machinery than the thing it protects.
pg_dump --format=custom --compress=9 --file="${partial}" || fail dump "pg_dump failed"

# The verification that makes this a backup rather than a file: read the table
# of contents back out of what was just written.
pg_restore --list "${partial}" > /dev/null 2>&1 || fail verify "pg_restore --list could not read the dump"

mv "${partial}" "${final}"
bytes="$(wc -c < "${final}" | tr -d ' ')"

# ------------------------------------------------------------------ rotate

# Grandfather-father-son, cheaply: the first dump of each ISO week is also
# hard-linked into weekly/, so it costs no disk until daily/ expires its copy.
promoted=0
week="$(date -u +%G-W%V)"
if [ "$(cat "${WEEK_MARKER}" 2>/dev/null || true)" != "${week}" ]; then
    ln "${final}" "${WEEKLY_DIR}/${name}" 2>/dev/null || cp "${final}" "${WEEKLY_DIR}/${name}"
    printf '%s\n' "${week}" > "${WEEK_MARKER}"
    promoted=1
fi

# Names are UTC timestamps, so a reverse sort is newest-first and retention is
# "drop everything past the keep count". Unbounded dumps fill the disk they
# exist to protect.
prune_local() {
    _dir="$1"
    _keep="$2"
    ls -1 "${_dir}"/meals-*.dump 2>/dev/null | sort -r | tail -n "+$((_keep + 1))" | while read -r _old; do
        rm -f "${_old}"
    done
}
prune_local "${DAILY_DIR}" "${KEEP_DAILY}"
prune_local "${WEEKLY_DIR}" "${KEEP_WEEKLY}"

daily_kept="$(ls -1 "${DAILY_DIR}"/meals-*.dump 2>/dev/null | wc -l | tr -d ' ')"
weekly_kept="$(ls -1 "${WEEKLY_DIR}"/meals-*.dump 2>/dev/null | wc -l | tr -d ' ')"

# ------------------------------------------------------------------ offsite

offsite="skipped"
if [ -n "${RCLONE_REMOTE}" ]; then
    offsite="error"
    # gpg wants a home it can write to, and the container has no real user.
    GNUPGHOME="$(mktemp -d)"
    export GNUPGHOME
    encrypted="${GNUPGHOME}/${name}.gpg"

    # Symmetric AES-256: the only thing needed to read a dump back is the
    # passphrase, which lives in a password manager rather than as a key file
    # that can be lost alongside the server it protects.
    gpg --batch --yes --quiet --pinentry-mode loopback \
        --passphrase-file "${BACKUP_PASSPHRASE_FILE}" \
        --symmetric --cipher-algo AES256 \
        --output "${encrypted}" "${final}" || fail offsite "gpg could not encrypt the dump"

    rclone copyto "${encrypted}" "${RCLONE_REMOTE}/daily/${name}.gpg" || fail offsite "rclone could not upload the dump"
    if [ "${promoted}" = "1" ]; then
        rclone copyto "${encrypted}" "${RCLONE_REMOTE}/weekly/${name}.gpg" || fail offsite "rclone could not upload the weekly dump"
    fi
    rm -rf "${GNUPGHOME}"

    # Remote retention deliberately *deletes named files* rather than syncing
    # the local directory: a sync mirrors deletions, so the morning after the
    # backup disk dies it would helpfully empty the off-node copy too. This
    # only ever removes the oldest beyond the keep count, and only after the
    # upload above succeeded.
    prune_remote() {
        _sub="$1"
        _keep="$2"
        rclone lsf "${RCLONE_REMOTE}/${_sub}" --include 'meals-*.dump.gpg' 2>/dev/null |
            sort -r | tail -n "+$((_keep + 1))" | while read -r _old; do
            rclone deletefile "${RCLONE_REMOTE}/${_sub}/${_old}" || true
        done
    }
    prune_remote daily "${KEEP_DAILY}"
    prune_remote weekly "${KEEP_WEEKLY}"
    offsite="ok"
fi

_logged=1
# Seconds, not milliseconds: busybox date has no sub-second format, and a
# nightly job measured to the millisecond would be precision this cannot back up.
log_event backup outcome=ok bytes="${bytes}" duration_s="$(($(date -u +%s) - started))" \
    offsite="${offsite}" daily_kept="${daily_kept}" weekly_kept="${weekly_kept}" file="${name}"
