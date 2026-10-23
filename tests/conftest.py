from pathlib import Path

import pytest

from csirt.config import Settings
from csirt.db import initialize


@pytest.fixture
def settings(tmp_path):
    config = Settings(db_path=tmp_path / "csirt.sqlite3")
    initialize(config)
    return config


def rss(title="🟢 Exemple", guid="incident-1", url="https://example.org/incident-1", summary="Adresses email", date="Tue, 20 Oct 2026 10:00:00 +0000", extra="", ttl=0):
    from xml.sax.saxutils import escape

    return (f'<rss version="2.0"><channel><title>Test</title><ttl>{ttl}</ttl><item>'
            f'<title>{escape(title)}</title><guid>{escape(guid)}</guid><link>{escape(url)}</link>'
            f'<description>{escape(summary)}</description><pubDate>{date}</pubDate>{extra}'
            '</item></channel></rss>').encode()
