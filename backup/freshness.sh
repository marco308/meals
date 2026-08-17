#!/bin/sh
# Is there a recent backup? Exits 0 if yes, 1 with a reason if not.
#
# This is the container's HEALTHCHECK, which is the cheapest possible answer to
# "notice when it stops": a backup job that silently fails is worse than none,
# because it is believed. `docker ps` showing the backup service unhealthy is a
# signal a self-hoster gets for free, without a monitoring stack; the deployment
# here also alerts on the log line (see backup/README.md).
#
# It deliberately checks the *file*, not whether the last run said it worked —
# the question is whether a restorable dump exists, and the answer has to
# survive the container that wrote it being replaced.
set -eu

BACKUP_DIR="${BACKUP_DIR:-/backups}"
STALE_AFTER_HOURS="${STALE_AFTER_HOURS:-36}"

if [ ! -d "${BACKUP_DIR}/daily" ] || [ -z "$(ls -1 "${BACKUP_DIR}"/daily/meals-*.dump 2>/dev/null | head -1)" ]; then
    echo "no backup has ever been taken into ${BACKUP_DIR}/daily"
    exit 1
fi

if [ -z "$(find "${BACKUP_DIR}/daily" -name 'meals-*.dump' -mmin "-$((STALE_AFTER_HOURS * 60))" | head -1)" ]; then
    echo "newest dump in ${BACKUP_DIR}/daily is older than ${STALE_AFTER_HOURS}h"
    exit 1
fi
