import pytest

from meals_mcp import server


@pytest.fixture(autouse=True)
def _stdio_unless_a_test_builds_the_http_app():
    """`http_app()` flips the process into remote mode for good, which is right
    in a container and wrong across a test session: the stdio tests that
    follow would be refused their env token."""
    yield
    server._serving_http = False
