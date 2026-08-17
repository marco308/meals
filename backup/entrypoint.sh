#!/bin/sh
# Take a backup every day at BACKUP_AT (UTC), forever.
#
# A sleep loop rather than cron on purpose: busybox crond posts a job's output
# to mail or syslog, and the whole point of the event line backup.sh writes is
# that it lands on the container's stdout where the log shipper — and `docker
# logs` — can see it. The loop keeps the job's output as the container's output.
set -eu

BACKUP_AT="${BACKUP_AT:-03:15}"
BACKUP_DIR="${BACKUP_DIR:-/backups}"

hours="${BACKUP_AT%%:*}"
minutes="${BACKUP_AT#*:}"
# Strip a leading zero: "08" is not a number to shell arithmetic, it is a
# malformed octal.
target=$(( ${hours#0} * 3600 + ${minutes#0} * 60 ))

# A fresh deployment should not spend its first night unprotected, and the
# healthcheck has nothing to look at until a dump exists — so take one now if
# there is none. Set BACKUP_ON_START=always to dump on every restart, or
# =never for a container that only ever runs on schedule.
case "${BACKUP_ON_START:-auto}" in
    always) first_run=1 ;;
    never) first_run=0 ;;
    *)
        first_run=0
        if [ -z "$(ls -1 "${BACKUP_DIR}"/daily/meals-*.dump 2>/dev/null | head -1)" ]; then
            first_run=1
        fi
        ;;
esac
if [ "${first_run}" = "1" ]; then
    # `|| true`: a failed backup must not kill the loop, or one unreachable
    # database means no further attempts are ever made. backup.sh has already
    # logged why, and the healthcheck goes unhealthy if this keeps up.
    /usr/local/bin/backup.sh || true
fi

while :; do
    now="$(date -u +%s)"
    # Seconds since midnight UTC is (epoch mod 86400), which avoids parsing
    # the clock back out of a formatted date.
    next=$(( now - now % 86400 + target ))
    [ "${next}" -le "${now}" ] && next=$(( next + 86400 ))
    sleep $(( next - now ))
    /usr/local/bin/backup.sh || true
done
