"""Request shield middleware: injection, traversal, content type, size, rate limit, headers."""
import pytest

from app.core import shield
from app.core.shield import ShieldRejection, SlidingWindowLimiter, check_body, check_json_node, check_path, check_query


@pytest.mark.parametrize("path", ["/api/../etc/passwd", "/api/%2e%2e/users", "/api//users", "/api\\users",
                                  "/api/.%2e/x", "/api/a%00b", "/api/" + "a" * 600])
def test_bad_paths_are_rejected(path):
    with pytest.raises(ShieldRejection):
        check_path(path, path.encode())


def test_encoded_alert_keys_are_allowed():
    check_path("/api/alerts/occupancy:UCI", b"/api/alerts/occupancy%3AUNIDAD%20DE%20CUIDADO%2FX")


@pytest.mark.parametrize("query", [b"email[$ne]=x", b"q=$where", b"filter={\"$gt\":1}", b"a=%00", b"x[y]=1",
                                   b"a=" + b"b" * 3000])
def test_operator_injection_in_query_is_rejected(query):
    with pytest.raises(ShieldRejection):
        check_query(query)


def test_normal_queries_pass():
    check_query(b"start=2026-09-01&bed_group=UNIDAD%20DE%20CUIDADO&sections=trends&sections=root_cause&lang=es")


@pytest.mark.parametrize("payload", [{"$where": "1"}, {"email": {"$ne": None}}, {"a": [{"b": {"$set": 1}}]},
                                     {"a\x00": 1}, {"a": "x\x00"}])
def test_operator_keys_in_json_are_rejected(payload):
    with pytest.raises(ShieldRejection):
        check_json_node(payload)


def test_deeply_nested_json_is_rejected():
    node: dict = {}
    for _ in range(20):
        node = {"a": node}
    with pytest.raises(ShieldRejection):
        check_json_node(node)


def test_body_rules():
    check_body("POST", "/api/chat", "application/json; charset=utf-8", b'{"question": "hola $set"}')  # values are data
    with pytest.raises(ShieldRejection, match="application/json"):
        check_body("POST", "/api/chat", "text/plain", b"hello")
    with pytest.raises(ShieldRejection, match="Malformed"):
        check_body("POST", "/api/chat", "application/json", b"{nope")
    with pytest.raises(ShieldRejection, match="multipart"):
        check_body("POST", "/api/upload-data", "application/json", b"{}")
    check_body("GET", "/api/kpis", "", b"")


def test_sliding_window_limiter():
    limiter = SlidingWindowLimiter(window=10)
    assert [limiter.hit("k", 2, now=t) for t in (0, 1)] == [None, None]
    assert limiter.hit("k", 2, now=2) == 9
    assert limiter.hit("k", 2, now=11) is None  # the first hit left the window


async def test_rejections_through_the_app(user_client, anon_client):
    assert (await user_client.put("/users")).status_code == 405
    assert (await user_client.get("/kpis", params={"bed_group[$ne]": "x"})).status_code == 400
    # httpx normalises a literal "..", so send the encoded form an attacker would use.
    assert (await user_client.get("/alerts/%2e%2e/users")).status_code == 400
    response = await user_client.post("/chat", json={"question": "hola", "$where": "sleep(1)"})
    assert response.status_code == 400 and response.json()["code"] == "request_rejected"
    response = await user_client.post("/chat", content=b"question=hola", headers={"Content-Type": "text/plain"})
    assert response.status_code == 415
    big = {"question": "x" * 70_000}
    assert (await user_client.post("/chat", json=big)).status_code == 413
    # Login is protected too (NoSQL injection on the email field).
    assert (await anon_client.post("/auth/login", json={"email": {"$ne": ""}, "password": "x"})).status_code == 400


async def test_extra_fields_are_rejected_by_strict_models(user_client):
    response = await user_client.post("/chat", json={"question": "hola", "role": "admin"})
    assert response.status_code == 422


async def test_security_headers_and_request_id(anon_client):
    response = await anon_client.get("/health", headers={"X-Request-ID": "abc-123"})
    assert response.headers["x-request-id"] == "abc-123"
    assert response.headers["x-frame-options"] == "DENY"
    assert "default-src 'none'" in response.headers["content-security-policy"]
    rejected = await anon_client.put("/health")
    assert rejected.status_code == 405 and rejected.headers["x-content-type-options"] == "nosniff"


async def test_rate_limit_returns_429(user_client, monkeypatch):
    monkeypatch.setattr(shield, "RATE_RULES", (("/api", 3),))
    codes = [(await user_client.get("/alerts/summary")).status_code for _ in range(4)]
    assert codes[:3] == [200, 200, 200] and codes[3] == 429


async def test_rate_limit_can_be_disabled(user_client, monkeypatch):
    monkeypatch.setattr(shield, "RATE_RULES", (("/api", 1),))
    monkeypatch.setattr(shield.settings, "RATE_LIMIT_ENABLED", False)
    codes = [(await user_client.get("/alerts/summary")).status_code for _ in range(3)]
    assert codes == [200, 200, 200]
