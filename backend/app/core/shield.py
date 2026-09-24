"""Request shield: an ASGI middleware that rejects hostile requests before any route runs.

Checks (in order, all O(size of the request)):
1. Method allowlist (GET, POST, PATCH, HEAD, OPTIONS)            -> 405
2. Path: length, NUL / control characters, traversal (`..`, `//`, `\\`),
   including percent-encoded forms                                  -> 400
3. Query string: length, NoSQL-operator injection in keys or values
   (`?email[$ne]=x`, `?q=$where`), NUL bytes                         -> 400
4. Rate limit per client (bearer token or IP), stricter for the
   agent, the n8n integration and the login                         -> 429
5. Body: size limit, JSON content type for POST/PATCH (multipart only on the upload
   endpoint), valid JSON, bounded depth, and NO key starting with `$` or containing
   NUL anywhere (NoSQL operator injection / mass assignment)        -> 400 / 413 / 415
The body is buffered once and replayed to the application unchanged.

Security headers and an `X-Request-ID` are added to every response.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import time
import uuid
from collections import defaultdict, deque
from typing import Any, Awaitable, Callable, Optional
from urllib.parse import parse_qsl, unquote

from app.core.config import settings

logger = logging.getLogger("shield")

Scope = dict[str, Any]
Message = dict[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]
ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]

ALLOWED_METHODS = frozenset({"GET", "POST", "PATCH", "HEAD", "OPTIONS"})
BODY_METHODS = frozenset({"POST", "PATCH"})
MULTIPART_PATHS = frozenset({"/api/upload-data"})
MAX_PATH_LENGTH = 512
MAX_QUERY_LENGTH = 2048
MAX_JSON_DEPTH = 12
DOCS_PREFIXES = ("/docs", "/redoc", "/openapi.json")

# Encoded "/" stays allowed (alert keys are URL-encoded path segments); traversal needs "..".
_TRAVERSAL = re.compile(r"(\.\.|//|\\|%2e%2e|%2e\.|\.%2e|%5c|%00)", re.IGNORECASE)
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_QUERY_OPERATOR = re.compile(r"(\$[a-z]+|\[\s*\$|\{\s*\"?\$)", re.IGNORECASE)

# (path prefix, requests per window) — the first matching prefix wins.
RATE_RULES: tuple[tuple[str, int], ...] = (
    ("/api/auth/login", settings.RATE_LIMIT_LOGIN_PER_MIN),
    ("/api/chat", settings.RATE_LIMIT_AGENT_PER_MIN),
    ("/api/query", settings.RATE_LIMIT_AGENT_PER_MIN),
    ("/api/integrations", settings.RATE_LIMIT_AGENT_PER_MIN),
    ("/api/records", settings.RATE_LIMIT_WRITE_PER_MIN),
    ("/api", settings.RATE_LIMIT_PER_MIN),
)
RATE_WINDOW_SECONDS = 60.0


class ShieldRejection(Exception):
    def __init__(self, status: int, detail: str, headers: Optional[dict[str, str]] = None) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail
        self.headers = headers or {}


class SlidingWindowLimiter:
    """In-memory sliding-window limiter (per process). Keys are hashed, never stored raw."""

    def __init__(self, window: float = RATE_WINDOW_SECONDS, max_keys: int = 50_000) -> None:
        self.window = window
        self.max_keys = max_keys
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def hit(self, key: str, limit: int, now: Optional[float] = None) -> Optional[int]:
        """Registers a request. Returns None if allowed, else the seconds to wait."""
        now = time.monotonic() if now is None else now
        if len(self._hits) > self.max_keys:  # bounded memory under a flood of distinct keys
            self._hits.clear()
        hits = self._hits[key]
        while hits and now - hits[0] >= self.window:
            hits.popleft()
        if len(hits) >= limit:
            return max(1, int(self.window - (now - hits[0])) + 1)
        hits.append(now)
        return None

    def reset(self) -> None:
        self._hits.clear()


limiter = SlidingWindowLimiter()


def _header(scope: Scope, name: bytes) -> str:
    for key, value in scope.get("headers") or []:
        if key == name:
            return value.decode("latin-1")
    return ""


def _client_key(scope: Scope) -> str:
    """Bearer token / integration key when present (per identity), otherwise the client IP."""
    credential = _header(scope, b"authorization") or _header(scope, b"x-integration-key")
    if credential:
        return "t:" + hashlib.sha256(credential.encode()).hexdigest()[:32]
    client = scope.get("client") or ("unknown", 0)
    return f"ip:{client[0]}"


def check_path(path: str, raw_path: bytes) -> None:
    raw = raw_path.decode("latin-1") if raw_path else path
    if len(raw) > MAX_PATH_LENGTH:
        raise ShieldRejection(414, "Path too long")
    for candidate in (raw, unquote(raw)):
        if _CONTROL.search(candidate):
            raise ShieldRejection(400, "Invalid characters in path")
    if _TRAVERSAL.search(raw):
        raise ShieldRejection(400, "Invalid path")


def check_query(query_string: bytes) -> None:
    if not query_string:
        return
    if len(query_string) > MAX_QUERY_LENGTH:
        raise ShieldRejection(414, "Query string too long")
    text = query_string.decode("latin-1")
    for key, value in parse_qsl(text, keep_blank_values=True):
        if _CONTROL.search(key) or "\x00" in value:
            raise ShieldRejection(400, "Invalid characters in query")
        if "[" in key or "]" in key or _QUERY_OPERATOR.search(key) or _QUERY_OPERATOR.search(value):
            raise ShieldRejection(400, "Operators are not allowed in query parameters")


def check_json_node(node: Any, depth: int = 0) -> None:
    """Rejects `$`-prefixed / NUL keys at any depth and excessive nesting."""
    if depth > MAX_JSON_DEPTH:
        raise ShieldRejection(400, "JSON body is nested too deeply")
    if isinstance(node, dict):
        for key, value in node.items():
            if not isinstance(key, str) or key.startswith("$") or "\x00" in key:
                raise ShieldRejection(400, "Operators or invalid keys are not allowed in the body")
            check_json_node(value, depth + 1)
    elif isinstance(node, list):
        for item in node:
            check_json_node(item, depth + 1)
    elif isinstance(node, str) and "\x00" in node:
        raise ShieldRejection(400, "Invalid characters in the body")


def check_body(method: str, path: str, content_type: str, body: bytes) -> None:
    if method not in BODY_METHODS or not body:
        return
    media_type = content_type.split(";")[0].strip().lower()
    if path in MULTIPART_PATHS:
        if media_type != "multipart/form-data":
            raise ShieldRejection(415, "multipart/form-data is required")
        return
    if media_type != "application/json":
        raise ShieldRejection(415, "Content-Type must be application/json")
    if len(body) > settings.API_MAX_JSON_BYTES:
        raise ShieldRejection(413, "Request body too large")
    try:
        payload = json.loads(body)
    except (ValueError, UnicodeDecodeError):
        raise ShieldRejection(400, "Malformed JSON body")
    check_json_node(payload)


def check_rate(scope: Scope, path: str) -> None:
    if not settings.RATE_LIMIT_ENABLED:
        return
    for prefix, limit in RATE_RULES:
        if path.startswith(prefix):
            retry_after = limiter.hit(f"{prefix}|{_client_key(scope)}", limit)
            if retry_after is not None:
                raise ShieldRejection(429, "Too many requests", {"Retry-After": str(retry_after)})
            return


def security_headers(path: str, request_id: str) -> list[tuple[bytes, bytes]]:
    headers = [
        (b"x-content-type-options", b"nosniff"),
        (b"x-frame-options", b"DENY"),
        (b"referrer-policy", b"no-referrer"),
        (b"cache-control", b"no-store"),
        (b"cross-origin-resource-policy", b"same-origin"),
        (b"permissions-policy", b"camera=(), microphone=(), geolocation=()"),
        (b"x-request-id", request_id.encode()),
    ]
    if not path.startswith(DOCS_PREFIXES):  # Swagger UI needs its own scripts
        headers.append((b"content-security-policy", b"default-src 'none'; frame-ancestors 'none'"))
    return headers


class RequestShieldMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request_id = _header(scope, b"x-request-id")[:64] or uuid.uuid4().hex
        request_id = re.sub(r"[^A-Za-z0-9_-]", "", request_id) or uuid.uuid4().hex
        path: str = scope.get("path", "")
        method: str = scope.get("method", "GET").upper()
        extra_headers = security_headers(path, request_id)

        try:
            if method not in ALLOWED_METHODS:
                raise ShieldRejection(405, "Method not allowed")
            check_path(path, scope.get("raw_path") or b"")
            check_query(scope.get("query_string") or b"")
            check_rate(scope, path)
            body, receive = await self._buffer_body(scope, receive, method, path)
            check_body(method, path, _header(scope, b"content-type"), body)
        except ShieldRejection as rejection:
            logger.warning("request_rejected", extra={"data": {
                "request_id": request_id, "status": rejection.status, "reason": rejection.detail,
                "method": method, "path": path[:200]}})
            await self._reject(send, rejection, extra_headers)
            return

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                existing = {k.lower() for k, _ in message.get("headers", [])}
                message["headers"] = list(message.get("headers", [])) + [
                    (k, v) for k, v in extra_headers if k not in existing]
            await send(message)

        await self.app(scope, receive, send_with_headers)

    async def _buffer_body(self, scope: Scope, receive: Receive, method: str, path: str) -> tuple[bytes, Receive]:
        """Reads the JSON body once (bounded) and returns a `receive` that replays it.

        The multipart upload is NOT buffered here: it is streamed and size-checked by its route.
        """
        if method not in BODY_METHODS or path in MULTIPART_PATHS:
            if path in MULTIPART_PATHS:
                content_type = _header(scope, b"content-type")
                check_body(method, path, content_type, b"x")  # content-type check only
            return b"", receive
        declared = _header(scope, b"content-length")
        if declared.isdigit() and int(declared) > settings.API_MAX_JSON_BYTES:
            raise ShieldRejection(413, "Request body too large")
        chunks: list[bytes] = []
        size = 0
        more = True
        while more:
            message = await receive()
            if message["type"] == "http.disconnect":
                break
            chunk = message.get("body", b"")
            size += len(chunk)
            if size > settings.API_MAX_JSON_BYTES:
                raise ShieldRejection(413, "Request body too large")
            chunks.append(chunk)
            more = message.get("more_body", False)
        body = b"".join(chunks)
        replayed = False

        async def replay() -> Message:
            nonlocal replayed
            if not replayed:
                replayed = True
                return {"type": "http.request", "body": body, "more_body": False}
            return await receive()

        return body, replay

    @staticmethod
    async def _reject(send: Send, rejection: ShieldRejection, extra_headers: list[tuple[bytes, bytes]]) -> None:
        payload = json.dumps({"detail": rejection.detail, "code": "request_rejected"}).encode()
        headers = [(b"content-type", b"application/json"), (b"content-length", str(len(payload)).encode()),
                   *((k.lower().encode(), v.encode()) for k, v in rejection.headers.items()), *extra_headers]
        await send({"type": "http.response.start", "status": rejection.status, "headers": headers})
        await send({"type": "http.response.body", "body": payload})
