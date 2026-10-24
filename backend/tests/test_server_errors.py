"""Unexpected server errors reach the browser as JSON with CORS headers (not as a fake "network error")."""

from fastapi.testclient import TestClient

from app.db.session import init_db


def test_unhandled_error_is_json_500_with_cors(monkeypatch):
    from app.main import app

    def boom(db):
        raise RuntimeError("UNIQUE constraint failed: something")

    monkeypatch.setattr("app.studio.routes.my_channel.list_connections", boom)
    init_db()
    with TestClient(app, raise_server_exceptions=False) as client:
        r = client.get("/api/v1/studio/my-channels", headers={"Origin": "http://localhost:3000"})
    assert r.status_code == 500
    assert r.headers.get("access-control-allow-origin") == "http://localhost:3000"
    assert "unexpected error" in r.json()["detail"].lower()
    assert "UNIQUE" not in r.text  # internals stay in the server log
