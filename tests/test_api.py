import httpx
from fastapi.testclient import TestClient

from csirt.api import create_app
from csirt.collector import collect_source
from csirt.db import connect
from conftest import rss


def test_filters_search_and_pagination(settings):
    body = rss(title="🟢 École Exemple", summary="Fuite de données médicales")
    with httpx.Client(transport=httpx.MockTransport(lambda req: httpx.Response(200, content=body))) as client:
        collect_source(settings, client, "bonjour-la-fuite")
    with TestClient(create_app(settings)) as api:
        assert api.get("/health").json()["status"] == "ok"
        assert len(api.get("/sources").json()) == 4
        result = api.get("/observations", params={"q": "ecole medicales", "status": "confirmed", "kind": "data_breach"})
        assert result.status_code == 200
        assert result.json()["total"] == 1
        assert api.get("/observations?source=frenchbreaches").json()["total"] == 0
        assert api.get("/observations?since=2026-10-21T00:00:00Z").json()["total"] == 0
        assert api.get("/observations?limit=1&offset=1").json()["items"] == []
        assert api.get("/observations?limit=101").status_code == 422
        assert api.get("/observations?sort=title").status_code == 422
        assert api.get("/observations?q=%22%20OR%201%3D1").status_code == 200
        assert api.get("/observations?q=!!!").status_code == 422
        assert api.get("/observations?since=2026-10-21&until=2026-10-20").status_code == 422
        incident = api.get("/incidents").json()["items"][0]
        detail = api.get(f'/incidents/{incident["id"]}').json()
        assert detail["grouping"] == "single_source"
        assert detail["observations"][0]["url"] == "https://example.org/incident-1"
        assert api.get("/incidents/9999").status_code == 404
        assert api.post("/collect").status_code == 404


def test_search_index_updates_with_observation(settings):
    for summary in ("ancienmot", "nouveaumot"):
        with connect(settings) as db:
            db.execute("UPDATE sources SET next_poll_at=0")
        with httpx.Client(transport=httpx.MockTransport(lambda req: httpx.Response(200, content=rss(summary=summary)))) as client:
            collect_source(settings, client, "bonjour-la-fuite")
    with TestClient(create_app(settings)) as api:
        assert api.get("/observations?q=ancienmot").json()["total"] == 0
        assert api.get("/observations?q=nouveaumot").json()["total"] == 1
