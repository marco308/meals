import httpx
import pytest
import respx

from app.services.recipe_parser import RecipeFetchError, fetch_page


@respx.mock
async def test_fetch_page_returns_body_and_follows_redirects():
    respx.get("https://example.com/chilli").mock(
        return_value=httpx.Response(301, headers={"location": "https://example.com/chilli-v2"})
    )
    respx.get("https://example.com/chilli-v2").mock(return_value=httpx.Response(200, text="<html>hi</html>"))
    assert await fetch_page("https://example.com/chilli") == "<html>hi</html>"


@respx.mock
async def test_http_error_becomes_actionable_message():
    respx.get("https://example.com/blocked").mock(return_value=httpx.Response(403))
    with pytest.raises(RecipeFetchError, match="HTTP 403") as exc_info:
        await fetch_page("https://example.com/blocked")
    assert "POST /recipes" in str(exc_info.value)


@respx.mock
async def test_network_error_becomes_actionable_message():
    respx.get("https://example.com/gone").mock(side_effect=httpx.ConnectError("boom"))
    with pytest.raises(RecipeFetchError, match="could not fetch") as exc_info:
        await fetch_page("https://example.com/gone")
    assert "POST /recipes" in str(exc_info.value)
