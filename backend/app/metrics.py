"""Prometheus metrics: the numbers half of app/observability.py.

Everything lives on a private registry rather than prometheus_client's global
default, so exposition serves exactly what this module declares (plus the
standard process/platform/GC collectors) and a re-import can never
double-register.

Exposure is `GET /metrics` in main.py, guarded by `METRICS_TOKEN`: reverse
proxies route every path on the public host to this app, so the endpoint has
to be safe while publicly reachable. Unset, it 404s and a self-hoster who
never heard of it loses nothing; set, it answers only a matching
`Authorization: Bearer` (Prometheus scrape configs carry that natively).

Label discipline: `route` is the matched route *template* (a bounded set),
never the raw path — otherwise every scanner probing /wp-login.php mints a new
timeseries. Requests that matched nothing are all "unmatched". The usage
gauges are whole-server counts on purpose (they exist to answer "is anyone
using this"), which is why those queries have no household_id filter — they
count rows, and read nothing anyone entered.
"""

import asyncio
import logging
import math

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Gauge,
    GCCollector,
    Histogram,
    PlatformCollector,
    ProcessCollector,
    generate_latest,
)
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import SessionLocal
from app.models import Household, Recipe, User

logger = logging.getLogger(__name__)

registry = CollectorRegistry(auto_describe=True)
ProcessCollector(registry=registry)
PlatformCollector(registry=registry)
GCCollector(registry=registry)

_HTTP_REQUESTS = Counter(
    "meals_http_requests_total",
    "HTTP requests handled, by matched route template.",
    labelnames=("method", "route", "status", "client_platform"),
    registry=registry,
)
_HTTP_DURATION = Histogram(
    "meals_http_request_duration_seconds",
    "Request wall time, by matched route template.",
    labelnames=("method", "route"),
    registry=registry,
)
_EVENTS = Counter(
    "meals_events_total",
    "Domain events; the counterpart of the meals.events log stream.",
    labelnames=("event", "outcome"),
    registry=registry,
)

# Slow-moving, whole-server usage gauges — refreshed by the lifespan task in
# main.py (only when metrics are enabled), not per scrape, so a scrape never
# touches the database.
_USAGE_GAUGES: tuple[tuple[Gauge, type], ...] = (
    (Gauge("meals_households_total", "Households on this server.", registry=registry), Household),
    (Gauge("meals_users_total", "User accounts on this server.", registry=registry), User),
    (Gauge("meals_recipes_total", "Recipes across all households.", registry=registry), Recipe),
)

# The instance ceilings beside the counts they bound, so a dashboard can divide
# one by the other and see "nearly full" before somebody is turned away. An
# unset ceiling reports `+Inf` rather than 0: a gauge left at its default would
# read as "this server allows no households", and the ratio would be a division
# by zero instead of the 0% that is true.
# Read straight off the settings rather than through app.limits: that module
# imports app.observability, which imports this one, and closing that loop
# breaks whichever of the three is imported first.
_CEILING_GAUGES: tuple[tuple[Gauge, str], ...] = (
    (Gauge("meals_households_limit", "MAX_HOUSEHOLDS, or +Inf when unset.", registry=registry), "max_households"),
    (Gauge("meals_users_limit", "MAX_USERS, or +Inf when unset.", registry=registry), "max_users"),
)


# Billing webhooks, by what this server made of each one (app/services/billing.py).
# This exists because the expensive failure is the *quiet* one: a webhook that
# is refused, orphaned or never verified means somebody paid and was not
# credited, and nobody finds that by reading logs. Alert on it:
#
#   increase(meals_billing_webhooks_total{outcome=~"orphan|refused|bad_signature|unsigned|stale|unreadable"}[1h]) > 0
#
# `granted` and `duplicate` are the healthy outcomes; `duplicate` is a retry
# doing its job, not a problem.
_BILLING_WEBHOOKS = Counter(
    "meals_billing_webhooks_total",
    "Billing webhooks received, by outcome.",
    labelnames=("outcome",),
    registry=registry,
)


def count_billing_webhook(outcome: str) -> None:
    _BILLING_WEBHOOKS.labels(outcome=outcome).inc()


def observe_request(method: str, route: str, status: int, client_platform: str | None, duration_seconds: float) -> None:
    _HTTP_REQUESTS.labels(
        method=method, route=route, status=str(status), client_platform=client_platform or "none"
    ).inc()
    _HTTP_DURATION.labels(method=method, route=route).observe(duration_seconds)


def count_event(event: str, outcome: str = "") -> None:
    _EVENTS.labels(event=event, outcome=outcome).inc()


async def refresh_usage_gauges(session: AsyncSession) -> None:
    for gauge, model in _USAGE_GAUGES:
        count = (await session.execute(select(func.count()).select_from(model))).scalar_one()
        gauge.set(count)
    settings = get_settings()
    for gauge, setting in _CEILING_GAUGES:
        ceiling = getattr(settings, setting)
        gauge.set(math.inf if ceiling is None else ceiling)


async def usage_gauge_refresher(interval_seconds: float = 60.0) -> None:
    """Run forever, refreshing the usage gauges. A failed refresh (db
    restarting mid-deploy, say) keeps the previous values and tries again next
    round — stale-by-a-minute is the accepted precision here anyway."""
    while True:
        try:
            async with SessionLocal() as session:
                await refresh_usage_gauges(session)
        except Exception:
            logger.warning("usage gauge refresh failed; keeping previous values", exc_info=True)
        await asyncio.sleep(interval_seconds)


def render() -> tuple[bytes, str]:
    """The exposition body and its content type."""
    return generate_latest(registry), CONTENT_TYPE_LATEST
