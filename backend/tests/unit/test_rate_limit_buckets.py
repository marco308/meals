"""The in-memory auth limiter's bookkeeping (#255): a caller who never comes
back must not keep a bucket for the life of the process, and an IPv6 caller
is charged by the /64 they were handed rather than by one of its 2^64
addresses."""

from fastapi import Request

from app.deps import _Buckets, _rate_limit_key


def test_a_window_that_empties_on_refund_frees_its_key():
    buckets = _Buckets(window_seconds=60)
    assert buckets.charge("1.2.3.4", limit=3, now=0.0)
    assert len(buckets) == 1
    buckets.refund("1.2.3.4")
    assert len(buckets) == 0
    buckets.refund("1.2.3.4")  # a second refund has nothing to give back and does not raise


def test_a_full_table_is_swept_of_callers_who_aged_out():
    buckets = _Buckets(window_seconds=60, sweep_at=4)
    for i in range(4):
        assert buckets.charge(f"10.0.0.{i}", limit=3, now=0.0)
    assert len(buckets) == 4
    # Within the window nothing is stale, so a fifth caller only grows the table.
    assert buckets.charge("10.0.0.99", limit=3, now=30.0)
    assert len(buckets) == 5
    # Past it the sweep runs before the charge and takes the four that aged out.
    assert buckets.charge("10.0.1.1", limit=3, now=61.0)
    assert len(buckets) == 2  # 10.0.0.99 (charged at 30) and the newcomer


def test_the_limit_still_holds_across_a_sweep():
    buckets = _Buckets(window_seconds=60, sweep_at=1)
    for _ in range(3):
        assert buckets.charge("1.2.3.4", limit=3, now=10.0)
    assert not buckets.charge("1.2.3.4", limit=3, now=20.0)  # refused, and the sweep did not forgive it
    assert buckets.charge("1.2.3.4", limit=3, now=71.0)  # the window has moved on


def _request_from(host: str) -> Request:
    return Request({"type": "http", "method": "POST", "path": "/auth/login", "headers": [], "client": (host, 1234)})


def test_ipv4_callers_are_charged_by_address():
    assert _rate_limit_key(_request_from("203.0.113.9")) == "203.0.113.9"


def test_ipv6_callers_are_charged_by_their_64():
    key = _rate_limit_key(_request_from("2001:db8:1234:5678:aaaa:bbbb:cccc:dddd"))
    assert key == "2001:db8:1234:5678::/64"
    assert _rate_limit_key(_request_from("2001:db8:1234:5678::1")) == key
    assert _rate_limit_key(_request_from("2001:db8:1234:5679::1")) != key


def test_mapped_and_zoned_addresses_still_resolve_to_a_bucket():
    assert _rate_limit_key(_request_from("::ffff:203.0.113.9")) == "::ffff:203.0.113.9"
    assert _rate_limit_key(_request_from("fe80::1%eth0")) == "fe80::/64"


def test_a_caller_with_no_address_shares_one_bucket():
    request = Request({"type": "http", "method": "POST", "path": "/auth/login", "headers": [], "client": None})
    assert _rate_limit_key(request) == "unknown"
