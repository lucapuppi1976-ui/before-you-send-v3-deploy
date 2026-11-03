"""Pure ASGI middlewares.

GuardMiddleware and BodySizeLimitMiddleware run before FastAPI reads the request
body, so an unauthenticated or oversized upload is rejected without being read.
"""
from __future__ import annotations

import hmac
from typing import Mapping, Optional

from fastapi import HTTPException
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from .gate import Gate
from .ratelimit import RateLimiter

# Unlocked code and the AI rate limits: the paths that call Claude or another service at our cost, and the check of a
# payment with Paddle (it needs the code, and each call asks Paddle).
AI_PATHS = frozenset(
    {"/api/decode/text", "/api/score/text", "/api/decode/audio", "/api/decode/image", "/api/replies/translate"}
)
PACK_PATH = "/api/pack/paid"
GUARDED_PATHS = AI_PATHS | {PACK_PATH}
UNLOCK_PATH = "/api/access/unlock"
ADMIN_PREFIX = "/admin"
LOCALHOST = "127.0.0.1"

# Paddle.js comes from (sandbox-)cdn.paddle.com with its stylesheet, and its checkout opens in a frame of
# (sandbox-)buy.paddle.com: as in Plantatam and Vistobueno, seen in the sandbox on 31/10.
PADDLE_SOURCES = "https://*.paddle.com"
PADDLE_CHECKOUT_ORIGINS = '"https://sandbox-buy.paddle.com" "https://buy.paddle.com"'


def content_security_policy(payments: bool = False) -> str:
    paddle = f" {PADDLE_SOURCES}" if payments else ""
    frames = f"frame-src{paddle}; " if payments else ""
    return (
        f"default-src 'self'; script-src 'self'{paddle}; style-src 'self' 'unsafe-inline'{paddle}; "
        f"img-src 'self' data: blob:{paddle}; media-src 'self' blob:; connect-src 'self'{paddle}; {frames}"
        "frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
    )


def permissions_policy(payments: bool = False) -> str:
    # The page itself may use the microphone ("Record": a voice note played on the loudspeaker); never the camera.
    # Apple Pay and Google Pay run in Paddle's checkout frame.
    payment = f"(self {PADDLE_CHECKOUT_ORIGINS})" if payments else "()"
    return f"camera=(), microphone=(self), geolocation=(), payment={payment}, usb=()"


def security_headers(payments: bool = False) -> tuple[tuple[bytes, bytes], ...]:
    return (
        (b"content-security-policy", content_security_policy(payments).encode("ascii")),
        (b"x-content-type-options", b"nosniff"),
        (b"referrer-policy", b"strict-origin-when-cross-origin"),
        (b"permissions-policy", permissions_policy(payments).encode("ascii")),
        (b"x-frame-options", b"DENY"),
    )


CONTENT_SECURITY_POLICY = content_security_policy()  # without the pack for sale
PERMISSIONS_POLICY = permissions_policy()
_SECURITY_HEADER_NAMES = {name for name, _ in security_headers()}


def client_host(scope: Scope) -> str:
    """The client IP; behind Caddy this relies on uvicorn --proxy-headers --forwarded-allow-ips 127.0.0.1."""
    client = scope.get("client")
    return client[0] if client else "unknown"


def _header(scope: Scope, name: bytes) -> Optional[str]:
    for key, value in scope.get("headers", []):
        if key.lower() == name:
            return value.decode("latin-1")
    return None


async def _reject(
    scope: Scope, receive: Receive, send: Send, status: int, detail: str, headers: Optional[Mapping[str, str]] = None
) -> None:
    await JSONResponse({"detail": detail}, status_code=status, headers=dict(headers or {}))(scope, receive, send)


class SecurityHeadersMiddleware:
    def __init__(self, app: ASGIApp, *, payments: bool = False) -> None:
        self.app = app
        self.headers = security_headers(payments)  # Paddle's checkout is allowed only when the pack can be bought

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path = scope["path"]
        no_store = path.startswith("/api/") or path == ADMIN_PREFIX or path.startswith(ADMIN_PREFIX + "/")

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                drop = set(_SECURITY_HEADER_NAMES)
                if no_store:
                    drop.add(b"cache-control")
                headers = [(key, value) for key, value in message.get("headers", []) if key.lower() not in drop]
                headers.extend(self.headers)
                if no_store:
                    headers.append((b"cache-control", b"no-store"))
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_with_headers)


class GuardMiddleware:
    """Gate check and per-IP rate limits for AI endpoints, unlock throttling, and the admin locality check."""

    def __init__(
        self, app: ASGIApp, *, gate: Gate, ai_limiter: RateLimiter, unlock_limiter: RateLimiter, admin_token: str
    ) -> None:
        self.app = app
        self.gate = gate
        self.ai_limiter = ai_limiter
        self.unlock_limiter = unlock_limiter
        self.admin_token = admin_token

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path = scope["path"]
        if path in GUARDED_PATHS:
            access = self.gate.access_from_cookies(Request(scope).cookies)
            if access is None:
                await _reject(scope, receive, send, 401, "Access locked. Enter your access code to continue.")
                return
            retry_after = self.ai_limiter.hit(client_host(scope))
            if retry_after is not None:
                await _reject(scope, receive, send, 429, "Too many requests.", {"Retry-After": str(retry_after)})
                return
            scope.setdefault("state", {})["bys_access"] = access
        elif path == UNLOCK_PATH:
            retry_after = self.unlock_limiter.hit(client_host(scope))
            if retry_after is not None:
                await _reject(scope, receive, send, 429, "Too many attempts.", {"Retry-After": str(retry_after)})
                return
        elif path == ADMIN_PREFIX or path.startswith(ADMIN_PREFIX + "/"):
            # Only direct local callers (the owner or n8n on the same machine), never via the proxy.
            proxied = any(_header(scope, name) is not None for name in (b"x-forwarded-for", b"forwarded"))
            if not self.admin_token or client_host(scope) != LOCALHOST or proxied:
                await _reject(scope, receive, send, 404, "Not Found")
                return
            supplied = (_header(scope, b"authorization") or "").encode("utf-8")
            if not hmac.compare_digest(supplied, f"Bearer {self.admin_token}".encode()):
                await _reject(scope, receive, send, 401, "Unauthorized", {"WWW-Authenticate": "Bearer"})
                return
        await self.app(scope, receive, send)


class BodyTooLarge(HTTPException):
    def __init__(self) -> None:
        super().__init__(status_code=413, detail="Request body too large.")


class BodySizeLimitMiddleware:
    """Reject bodies over the limit from Content-Length, or abort once streamed bytes exceed it."""

    def __init__(self, app: ASGIApp, *, default_limit: int, path_limits: Mapping[str, int]) -> None:
        self.app = app
        self.default_limit = default_limit
        self.path_limits = dict(path_limits)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        limit = self.path_limits.get(scope["path"], self.default_limit)
        declared = _header(scope, b"content-length")
        if declared is not None:
            declared = declared.strip()
            if not declared.isdigit():
                await _reject(scope, receive, send, 400, "Invalid Content-Length.")
                return
            if int(declared) > limit:
                await _reject(scope, receive, send, 413, "Request body too large.")
                return

        received = 0
        response_started = False

        async def limited_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    # An HTTPException, so FastAPI's body parsing re-raises it and answers 413.
                    raise BodyTooLarge()
            return message

        async def tracking_send(message: Message) -> None:
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, limited_receive, tracking_send)
        except BodyTooLarge:
            if not response_started:
                await _reject(scope, receive, send, 413, "Request body too large.")
