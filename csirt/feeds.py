import hashlib
import html
import json
import re
import unicodedata
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from defusedxml import ElementTree as ET

UTC = timezone.utc


class TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style", "iframe"}:
            self.hidden += 1
        if not self.hidden:
            self.parts.append(" ")

    def handle_endtag(self, tag):
        if tag in {"script", "style", "iframe"} and self.hidden:
            self.hidden -= 1
        if not self.hidden:
            self.parts.append(" ")

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def plain_text(value: str, limit: int = 2000) -> str:
    parser = TextExtractor()
    parser.feed(value)
    parser.close()
    return " ".join(html.unescape("".join(parser.parts)).split())[:limit]


def normalize(value: str) -> str:
    value = unicodedata.normalize("NFKD", value.casefold())
    value = "".join(c for c in value if not unicodedata.combining(c))
    return " ".join(re.findall(r"[a-z0-9]+", value))


def canonical_url(value: str) -> str:
    parts = urlsplit(value.strip())
    if parts.scheme not in {"https", "http"} or not parts.hostname or parts.username:
        raise ValueError("Lien de publication HTTP(S) requis")
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
             if not k.casefold().startswith("utm_") and k.casefold() not in {"fbclid", "gclid"}]
    # Bonjour la fuite identifie chaque incident par son ancre : ne pas la supprimer.
    fragment = parts.fragment if parts.hostname.lower() == "bonjourlafuite.eu.org" else ""
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path or "/",
                       urlencode(query), fragment))


def parse_date(value: str | None) -> str | None:
    if not value:
        return None
    try:
        date = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        try:
            date = parsedate_to_datetime(value)
        except (ValueError, TypeError, OverflowError):
            return None
    if date.tzinfo is None:
        date = date.replace(tzinfo=UTC)
    return date.astimezone(UTC).isoformat(timespec="seconds")


def organization(title: str, source: str, mode: str | None = None) -> str | None:
    clean = title.lstrip("🟢🟠🔴 \u00a0")
    if mode == "title" or (mode is None and source in {"bonjour-la-fuite", "frenchbreaches"}):
        return clean[:200]
    # Extraction volontairement limitee aux titres explicites. Aucun appel a un LLM.
    patterns = (
        r"^(?:cyberattaque (?:contre|chez)|fuite de donnees (?:chez|a|au|contre))\s+(.+?)(?:\s*[:：]|$)",
        r"^(.+?)\s+(?:confirme|pirate|piratee|pirates|piratees)\b",
        r"^(.+?)\s*[:：]",
    )
    folded = unicodedata.normalize("NFKD", clean)
    folded = "".join(c for c in folded if not unicodedata.combining(c))
    for pattern in patterns:
        match = re.search(pattern, folded, re.IGNORECASE)
        if match:
            # Les indices changent avec certains accents : conserver le nom normalise.
            candidate = match.group(1).strip()
            candidate = re.sub(r"^(?:l['’]|le |la |les |un |une )", "", candidate, flags=re.I)
            if 2 <= len(candidate) <= 120 and normalize(candidate) not in {"un", "une", "le", "la", "les", "pirate", "hacker"}:
                return candidate
    return None


def classify(title: str, summary: str, categories: list[str]) -> tuple[str, str]:
    # Les statuts de la source priment. Ne jamais deduire une confirmation d'un
    # simple mot trouve dans le resume (ex. « ne confirme pas »).
    cats = {normalize(c) for c in categories}
    if title.startswith("🟢") or cats & {"confirmed", "confirmee", "confirme"}:
        status = "confirmed"
    elif title.startswith("🔴") or cats & {"unreliable", "peu fiable", "claimed unreliable", "unconfirmed unreliable"}:
        status = "unreliable_claim"
    elif title.startswith("🟠") or cats & {"claimed", "credible", "unconfirmed", "revendiquee", "claimed credible", "unconfirmed credible"}:
        status = "claimed"
    else:
        status = "unknown"
    text = normalize(" ".join([title, summary, *categories]))
    if any(k in text for k in ("ransomware", "rancongiciel")):
        kind = "ransomware"
    elif any(k in text for k in ("fuite", "leak", "breach", "donnees expose", "en fuite")):
        kind = "data_breach"
    elif any(k in text for k in ("faille", "vulnerabilite")):
        kind = "vulnerability"
    elif any(k in text for k in ("attaque", "pirat")):
        kind = "cyberattack"
    else:
        kind = "other"
    return kind, status


@dataclass(frozen=True)
class Entry:
    external_id: str
    url: str
    title: str
    summary: str
    published_at: str | None
    updated_at: str | None
    organization: str | None
    kind: str
    status: str
    categories: list[str]

    @property
    def content_hash(self):
        return hashlib.sha256(json.dumps(asdict(self), sort_keys=True, ensure_ascii=False).encode()).hexdigest()


@dataclass(frozen=True)
class ParsedFeed:
    entries: list[Entry]
    rejected: int
    ttl_seconds: int


def parse_feed(data: bytes, source: str, max_entries: int = 500, *, fields: dict | None = None,
               organization_mode: str | None = None) -> ParsedFeed:
    root = ET.fromstring(data, forbid_dtd=True)
    atom = "{http://www.w3.org/2005/Atom}"
    if root.tag == "rss":
        channel = root.find("channel")
        if channel is None:
            raise ValueError("Flux RSS sans channel")
        items = channel.findall("item")
        ttl = channel.findtext("ttl", "0")
        ttl_seconds = max(0, int(ttl)) * 60 if ttl.isdigit() else 0
    elif root.tag == atom + "feed":
        items = root.findall(atom + "entry")
        ttl_seconds = 0
    else:
        raise ValueError("Format RSS 2.0 ou Atom attendu")
    if len(items) > max_entries:
        # Ne pas valider le cache d'un flux tronque : demander une intervention.
        raise ValueError("Le nombre d'entrees du flux depasse la limite configuree")
    entries, rejected = [], 0
    fields = fields or {}
    for item in items:
        def field(*names):
            for name in names:
                value = item.findtext(name)
                if value and value.strip():
                    return value.strip()
            return None

        try:
            title = plain_text(field(fields.get("title", "title"), atom + "title") or "", 500)
            link = field(fields.get("url", "link"))
            if not link:
                link = next((n.get("href") for n in item.findall(atom + "link")
                             if n.get("rel", "alternate") == "alternate"), None)
            if not title or not link:
                raise ValueError("Titre ou lien absent")
            url = canonical_url(link)
            external_id = (field(fields.get("id", "guid"), atom + "id") or url)[:2048]
            summary = plain_text(field(fields.get("summary", "description"), atom + "summary", atom + "content") or "")
            categories = [plain_text(n.text or n.get("term", ""), 100)
                          for n in [*item.findall(fields.get("categories", "category")), *item.findall(atom + "category")]][:30]
            kind, status = classify(title, summary, categories)
            if kind == "other" and (organization_mode == "title" or source in {"bonjour-la-fuite", "frenchbreaches"}):
                kind = "data_breach"
            entries.append(Entry(external_id, url, title, summary,
                                 parse_date(field(fields.get("published_at", "pubDate"), atom + "published")),
                                 parse_date(field(fields.get("updated_at", atom + "updated"))),
                                 organization(title, source, organization_mode), kind, status, categories))
        except (ValueError, TypeError):
            rejected += 1
    if items and not entries:
        raise ValueError("Toutes les entrees du flux sont invalides")
    return ParsedFeed(entries, rejected, ttl_seconds)
