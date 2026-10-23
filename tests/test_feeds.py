import pytest
from defusedxml.common import DefusedXmlException

from csirt.feeds import canonical_url, normalize, parse_feed, plain_text
from conftest import rss


def test_bonjour_fragments_are_distinct():
    a = canonical_url("https://bonjourlafuite.eu.org/?utm_source=test#Exemple-2026-10-20")
    b = canonical_url("https://bonjourlafuite.eu.org/#Autre-2026-10-20")
    assert a != b
    assert "utm_" not in a
    assert a.endswith("#Exemple-2026-10-20")


def test_rss_normalizes_dates_and_strips_html():
    entry = parse_feed(rss(summary='<p>Email</p><script>secret()</script><ul><li>Nom</li></ul>'), "bonjour-la-fuite").entries[0]
    assert entry.summary == "Email Nom"
    assert entry.published_at == "2026-10-20T10:00:00+00:00"
    assert entry.status == "confirmed"
    assert entry.kind == "data_breach"
    assert entry.organization == "Exemple"
    assert normalize("École — Exemple") == "ecole exemple"


def test_plain_text_with_unescaped_ampersand_at_end():
    assert plain_text("PRO&Cie") == "PRO&Cie"


def test_atom_with_missing_date_and_fallback_id():
    body = b'<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>Example</title><link href="https://example.org/a"/><summary>Hi</summary><updated>2026-10-20T12:00:00+02:00</updated></entry></feed>'
    entry = parse_feed(body, "fuites-infos").entries[0]
    assert entry.published_at is None
    assert entry.updated_at == "2026-10-20T10:00:00+00:00"
    assert entry.external_id == "https://example.org/a"


def test_publisher_status_and_no_guessed_confirmation():
    assert parse_feed(rss(title="Exemple", extra="<category>credible</category>"), "frenchbreaches").entries[0].status == "claimed"
    entry = parse_feed(rss(title="Exemple", summary="Exemple ne confirme pas la fuite"), "cyberattaque").entries[0]
    assert entry.status == "unknown"
    assert parse_feed(rss(title="🔴 Exemple"), "bonjour-la-fuite").entries[0].status == "unreliable_claim"


def test_rejects_xml_entities_and_nonfeeds():
    with pytest.raises(DefusedXmlException):
        parse_feed(b'<!DOCTYPE rss [<!ENTITY test "expand">]><rss><channel>&test;</channel></rss>', "test")
    with pytest.raises(ValueError):
        parse_feed(b"<html>error</html>", "test")
    with pytest.raises(ValueError):
        parse_feed(rss(), "test", max_entries=0)


def test_partial_feed_reports_bad_entries():
    body = rss().replace(b"</channel>", b"<item><title>Missing link</title></item></channel>")
    parsed = parse_feed(body, "test")
    assert parsed.rejected == 1
    assert len(parsed.entries) == 1


@pytest.mark.parametrize("url", ["javascript:alert(1)", "file:///etc/passwd", "https://user:secret@example.org/a"])
def test_invalid_links(url):
    with pytest.raises(ValueError):
        canonical_url(url)
