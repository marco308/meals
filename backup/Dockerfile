# The backup sidecar: postgres client tools, rclone for the off-node copy, gpg
# for encrypting it.
#
# Based on the same image tag as the database itself, and that is load-bearing:
# pg_dump refuses to dump a server newer than itself, so this tag has to move
# whenever the db service's does. Nothing else here is version-sensitive.
FROM postgres:17-alpine

RUN apk add --no-cache rclone gnupg

COPY backup.sh restore.sh freshness.sh entrypoint.sh /usr/local/bin/
RUN chmod +x /usr/local/bin/backup.sh /usr/local/bin/restore.sh \
    /usr/local/bin/freshness.sh /usr/local/bin/entrypoint.sh

VOLUME /backups

# Unhealthy = no restorable dump in the last STALE_AFTER_HOURS. start_period
# covers the first run on a fresh volume.
HEALTHCHECK --interval=15m --timeout=10s --start-period=10m \
    CMD /usr/local/bin/freshness.sh

# The base image's entrypoint exists to initialise a database cluster; this
# container has no data directory and no server, so replace it outright.
ENTRYPOINT []
CMD ["/usr/local/bin/entrypoint.sh"]
