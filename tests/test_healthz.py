import pytest


@pytest.mark.django_db
def test_healthz_returns_ok_when_database_reachable(client):
    response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
