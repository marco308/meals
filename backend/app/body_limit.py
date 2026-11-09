"""A ceiling on how much one request may send (`MAX_REQUEST_BODY_BYTES`).

The API runs one uvicorn worker and reads a JSON body whole before anything
looks at it, so without a ceiling the size of a request is the caller's
choice and the time it takes is taken from every household, `/healthz`
included. Nothing this API accepts is large: a recipe at the top of every
schema limit is a few tens of kilobytes.

Two checks, because a body announces its size or it doesn't. A
`Content-Length` over the limit is refused before a byte is read. A body
without one (chunked) is counted as it arrives, and the read that crosses the
limit raises the same 413 from inside whatever was reading it.

Plain ASGI rather than `@app.middleware("http")`: it has to wrap `receive`,
which is where the bytes come from.
"""

from fastapi import HTTPException
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send


def _too_large(limit: int) -> HTTPException:
    return HTTPException(
        status_code=413,
        detail=(
            f"request body is larger than this server accepts ({limit} bytes). Send less in one request: "
            "split a long list across several calls, and never send a file or an image, only its URL"
        ),
    )


def _refusal(limit: int) -> JSONResponse:
    refusal = _too_large(limit)
    return JSONResponse(status_code=refusal.status_code, content={"detail": refusal.detail})


class BodyLimitMiddleware:
    def __init__(self, app: ASGIApp, *, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or self.max_bytes <= 0:
            await self.app(scope, receive, send)
            return

        declared = dict(scope["headers"]).get(b"content-length")
        if declared is not None and declared.isdigit() and int(declared) > self.max_bytes:
            await _refusal(self.max_bytes)(scope, receive, send)
            return

        seen = 0
        over = False
        started = False

        async def counted() -> Message:
            nonlocal seen, over
            message = await receive()
            if message["type"] == "http.request":
                seen += len(message.get("body", b""))
                if seen > self.max_bytes:
                    # Stops whatever was reading. What it makes of the
                    # exception is its own business (FastAPI answers 400 for
                    # any failure to read a body), so the answer is replaced
                    # below rather than left to it.
                    over = True
                    raise _too_large(self.max_bytes)
            return message

        async def answered(message: Message) -> None:
            nonlocal started
            if over and not started:
                # The app's answer to a read that was cut off: ours goes out
                # in place of its first message, and the rest is dropped.
                if message["type"] == "http.response.start":
                    await _refusal(self.max_bytes)(scope, receive, send)
                return
            started = started or message["type"] == "http.response.start"
            await send(message)

        await self.app(scope, counted, answered)
