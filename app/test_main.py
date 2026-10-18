import fakeredis
import pytest
import redis

from app import main


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(main, "db", fakeredis.FakeRedis(decode_responses=True))
    return main.app.test_client()


def test_add_and_list_notes(client):
    assert client.post("/notes", json={"text": "hello"}).status_code == 201
    assert client.get("/notes").get_json() == ["hello"]


def test_rejects_empty_note(client):
    assert client.post("/notes", json={}).status_code == 400


def test_health_and_version(client):
    assert client.get("/healthz").status_code == 200
    assert client.get("/readyz").status_code == 200
    assert "version" in client.get("/version").get_json()


def test_metrics_count_requests(client):
    client.get("/notes")
    assert 'http_requests_total{method="GET",path="/notes",status="200"}' in client.get("/metrics").text


def test_redis_down_returns_503(client, monkeypatch):
    broken = fakeredis.FakeRedis()
    monkeypatch.setattr(broken, "ping", lambda: (_ for _ in ()).throw(redis.ConnectionError("down")))
    monkeypatch.setattr(main, "db", broken)
    assert client.get("/readyz").status_code == 503
