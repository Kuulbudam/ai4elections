from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app, raise_server_exceptions=False)


def test_health_ok():
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "version": "0.1.0"}


def test_unknown_route_returns_contract_error_shape():
    r = client.get("/does-not-exist")
    assert r.status_code == 404
    body = r.json()
    assert body["code"] == "NOT_FOUND"
    assert set(body) <= {"code", "message", "request_id", "details"}


def test_unexpected_error_hides_internals():
    @app.get("/_boom_test")
    def boom():
        raise RuntimeError("secret internal detail: password=hunter2")

    r = client.get("/_boom_test")
    assert r.status_code == 500
    assert r.json()["code"] == "INTERNAL"
    assert "hunter2" not in r.text and "Traceback" not in r.text


def test_cors_rejects_unknown_origin():
    r = client.options("/health", headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "GET"})
    assert r.headers.get("access-control-allow-origin") is None


def test_security_headers_on_every_response():
    from fastapi.testclient import TestClient

    from app.main import app

    c = TestClient(app)
    for r in (c.get("/health"), c.get("/findings/not-a-uuid"), c.get("/nope")):
        assert r.headers["X-Content-Type-Options"] == "nosniff"
        assert r.headers["X-Frame-Options"] == "DENY"
        assert "default-src 'none'" in r.headers["Content-Security-Policy"]
    assert c.get("/findings/not-a-uuid").headers["Cache-Control"] == "no-store"
