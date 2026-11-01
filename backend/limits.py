from starlette.exceptions import HTTPException


class BodyLimitMiddleware:
    """Enforce the cap while reading, including requests without Content-Length."""

    def __init__(self, app, limit):
        self.app, self.limit = app, limit

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        count = 0

        async def bounded_receive():
            nonlocal count
            message = await receive()
            if message["type"] == "http.request":
                count += len(message.get("body", b""))
                if count > self.limit:
                    raise HTTPException(413, "Upload exceeds the server limit")
            return message

        await self.app(scope, bounded_receive, send)
