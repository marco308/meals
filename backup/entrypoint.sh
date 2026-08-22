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
# Nothing below may block forever. backup.sh bounds the step most likely to
# hang (see its dump section), but this loop is the thing that has to survive
# a hang nobody predicted: one wedged run and no backup is ever taken again.
RUN_TIMEOUT_S="${RUN_TIMEOUT_S:-7200}"

# busybox and coreutils both ship `timeout`, so this is belt and braces: if some
# future base image lacks it, run unbounded rather than not at all. Depending on
# a missing command here would recreate the exact bug the timeout is for.
if command -v timeout >/dev/null 2>&1; then
    run_backup() { timeout "${RUN_TIMEOUT_S}" /usr/local/bin/backup.sh; }
else
    run_backup() { /usr/local/bin/backup.sh; }
fi

hours="${BACKUP_AT%%:*}"
minutes="${BACKUP_AT#*:}"
# Strip a leading zero: "08" is not a number to shell arithmetic, it is a
# malformed octal.
target=$(( ${hours#0} * 3600 + ${minutes#0} * 60 ))

# A fresh deployment should not spend its first night unprotected, and neither
# should one that has fallen behind — so take a dump now if there is no *recent*
# one. Set BACKUP_ON_START=always to dump on every restart, or =never for a
# container that only ever runs on schedule.
#
# The test is freshness.sh, which is also the HEALTHCHECK, and the agreement
# between the two is the point. Swarm kills this container when the check
# fails, so a start that does not fix what the check is complaining about is
# just killed again ~25 minutes later, forever, and the scheduled run below is
# never reached. Asking only "does a dump exist" was exactly that bug: one
# missed night on 2026-08-20 left a stale-but-present dump, and the service
# then took no backup at all for three days while looking merely restarty.
case "${BACKUP_ON_START:-auto}" in
    always) first_run=1 ;;
    never) first_run=0 ;;
    *)
        first_run=0
        /usr/local/bin/freshness.sh >/dev/null 2>&1 || first_run=1
        ;;
esac
if [ "${first_run}" = "1" ]; then
    # `|| true`: a failed backup must not kill the loop, or one unreachable
    # database means no further attempts are ever made. backup.sh has already
    # logged why, and the healthcheck goes unhealthy if this keeps up.
    run_backup || true
fi

while :; do
    now="$(date -u +%s)"
    # Seconds since midnight UTC is (epoch mod 86400), which avoids parsing
    # the clock back out of a formatted date.
    next=$(( now - now % 86400 + target ))
    [ "${next}" -le "${now}" ] && next=$(( next + 86400 ))
    sleep $(( next - now ))
    run_backup || true
done
