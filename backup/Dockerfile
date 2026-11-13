# The backup sidecar: postgres client tools, rclone for the off-node copy, gpg
# for encrypting it.
#
# Based on the same image tag as the database itself, and that is load-bearing:
# pg_dump refuses to dump a server of a newer major version than itself, so the
# major here has to move whenever the db service's does. Minor versions need not
# match, so this stays a floating tag (rebuilt with the newest 17.x on every
# release) even where a deployment pins its db by digest. Nothing else here is
# version-sensitive.
FROM postgres:17-alpine

RUN apk add --no-cache rclone gnupg

COPY backup.sh restore.sh freshness.sh entrypoint.sh /usr/local/bin/
RUN chmod +x /usr/local/bin/backup.sh /usr/local/bin/restore.sh \
    /usr/local/bin/freshness.sh /usr/local/bin/entrypoint.sh

# Unprivileged, like the API image (uid 1000 there too): this container holds
# the database password, the rclone token and the passphrase, and nothing it
# does needs root (#261). /backups is owned by that uid before it is declared
# a volume, so a fresh named volume inherits the ownership; a bind mount is
# the operator's to chown (README). HOME is where rclone and gpg keep their
# scratch, and the uid has no home of its own.
RUN addgroup -g 1000 backup \
    && adduser -D -H -u 1000 -G backup -s /sbin/nologin backup \
    && mkdir -p /backups && chown backup:backup /backups
ENV HOME=/tmp
USER backup

VOLUME /backups

# Unhealthy = no restorable dump in the last STALE_AFTER_HOURS. start_period
# covers the first run on a fresh volume.
HEALTHCHECK --interval=15m --timeout=10s --start-period=10m \
    CMD /usr/local/bin/freshness.sh

# The base image's entrypoint exists to initialise a database cluster; this
# container has no data directory and no server, so replace it outright.
ENTRYPOINT []
CMD ["/usr/local/bin/entrypoint.sh"]
