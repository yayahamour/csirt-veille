import json
import sqlite3
from dataclasses import replace

import httpx
import pytest

from csirt.collector import collect_due, collect_source
from csirt.config import DEFAULT_SOURCES_PATH, load_sources
from csirt.db import connect, initialize, sync_sources
from conftest import rss


@pytest.fixture
def configurable(settings, tmp_path):
    path = tmp_path / "sources.toml"
    path.write_bytes(DEFAULT_SOURCES_PATH.read_bytes())
    return replace(settings, sources_path=path)


def edit(config, old, new):
    config.sources_path.write_text(config.sources_path.read_text(encoding="utf-8").replace(old, new), encoding="utf-8")


@pytest.mark.parametrize('old,new',[
    ('method = "GET"','method = "DELETE"'),
    ('https://bonjourlafuite.eu.org/feed.xml','http://example.org/rss'),
    ('poll_seconds = 1800','poll_seconds = 10'),
    ('kind = "rss"','kind = "company_search"'),
    ('enabled = true','enabeld = true'),
])
def test_invalid_configuration_preserves_state_before_network(configurable,old,new):
    edit(configurable,old,new)
    with pytest.raises(ValueError): sync_sources(configurable)
    with connect(configurable) as db:
        assert db.execute('SELECT COUNT(*) FROM sources WHERE enabled=1').fetchone()[0]==4


def test_changed_request_invalidates_cache_without_resetting_deadline(configurable):
    config = configurable
    with httpx.Client(transport=httpx.MockTransport(lambda req: httpx.Response(200, content=rss(), headers={"ETag": "old"}))) as client:
        collect_source(config, client, "bonjour-la-fuite")
    with connect(config) as db:
        before = dict(db.execute("SELECT * FROM sources WHERE id='bonjour-la-fuite'").fetchone())
    edit(config, "https://bonjourlafuite.eu.org/feed.xml", "https://example.org/new-rss")
    sync_sources(config)
    with connect(config) as db:
        after = dict(db.execute("SELECT * FROM sources WHERE id='bonjour-la-fuite'").fetchone())
        assert db.execute("SELECT COUNT(*) FROM observations").fetchone()[0] == 1
    assert after["etag"] is None and after["body_hash"] is None
    assert after["next_poll_at"] == before["next_poll_at"]
    assert after["feed_url"] == "https://example.org/new-rss"
    assert after["config_hash"] != before["config_hash"]
    # Cycle suivant, sans redemarrage : la nouvelle requete est utilisee.
    with connect(config) as db:
        db.execute("UPDATE sources SET next_poll_at=0 WHERE id='bonjour-la-fuite'")
    urls = []

    def handler(req):
        urls.append(str(req.url))
        return httpx.Response(200, content=rss())

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        collect_due(config, client)
    assert "https://example.org/new-rss" in urls


def test_disable_and_per_source_interval(configurable):
    edit(configurable, 'poll_seconds = 1800', 'poll_seconds = 7200')
    edit(configurable, 'name = "Bonjour la fuite"\nkind = "rss"\nenabled = true', 'name = "Bonjour la fuite"\nkind = "rss"\nenabled = false')
    with httpx.Client(transport=httpx.MockTransport(lambda req: httpx.Response(200, content=rss()))) as client:
        results = collect_due(configurable, client)
    assert len(results) == 3
    with connect(configurable) as db:
        row = db.execute("SELECT enabled, poll_seconds FROM sources WHERE id='bonjour-la-fuite'").fetchone()
        assert tuple(row) == (0, 7200)




def test_v1_migration_preserves_existing_observations(tmp_path):
    from csirt.config import Settings
    from csirt.db import SCHEMA

    settings = Settings(db_path=tmp_path / "v1.sqlite3")
    # Le schema des sources v1 est encore celui du CREATE initial ; les colonnes
    # v2 sont ajoutees par la migration et non par remplacement des tables.
    db = sqlite3.connect(settings.db_path)
    db.executescript(SCHEMA)
    db.execute("INSERT INTO sources(id,name,feed_url,etag,next_poll_at) VALUES ('bonjour-la-fuite','Old','https://bonjourlafuite.eu.org/feed.xml','keep',12345)")
    db.execute("INSERT INTO incidents(title,first_seen_at,last_changed_at) VALUES ('historique','2026-01-01','2026-01-01')")
    db.execute("INSERT INTO observations(incident_id,source_id,external_id,url,title,summary,kind,status,categories,content_hash,first_seen_at,last_changed_at) "
               "VALUES (1,'bonjour-la-fuite','historique','https://example.org/historique','Historique','','other','unknown','[]','hash','2026-01-01','2026-01-01')")
    db.commit()
    db.close()
    initialize(settings)
    initialize(settings)
    with connect(settings) as db:
        assert db.execute("SELECT MAX(version) FROM schema_version").fetchone()[0] == 5
        assert db.execute("SELECT COUNT(*) FROM observations").fetchone()[0] == 1
        source = db.execute("SELECT * FROM sources WHERE id='bonjour-la-fuite'").fetchone()
        assert source["etag"] == "keep"
        assert source["next_poll_at"] == 12345




def test_custom_rss_source_and_field_mapping_without_python_changes(configurable):
    configurable.sources_path.write_text(configurable.sources_path.read_text(encoding="utf-8") + '''
[[sources]]
id = "custom-rss"
name = "Custom"
kind = "rss"
organization_mode = "title"
[sources.request]
url = "https://example.org/rss?language=fr"
[sources.request.params]
format = "xml"
[sources.response]
title = "heading"
url = "permalink"
id = "identifier"
summary = "excerpt"
''', encoding="utf-8")

    def handler(req):
        if req.url.host == "example.org":
            assert req.url.params["language"] == "fr"
            assert req.url.params["format"] == "xml"
            return httpx.Response(200, content=b'<rss><channel><item><heading>Acme</heading><identifier>custom1</identifier><permalink>https://example.org/custom1</permalink><excerpt>Information</excerpt></item></channel></rss>')
        return httpx.Response(200, content=rss())

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        results = collect_due(configurable, client)
    assert len(results) == 5
    assert results[-1]["created"] == 1
    with connect(configurable) as db:
        row = db.execute("SELECT title, organization FROM observations WHERE source_id='custom-rss'").fetchone()
        assert tuple(row) == ("Acme", "Acme")
