"""Adaptateurs des publications officielles et des sources d’enrichissement."""
import json
import re
import time
from urllib.parse import urlsplit

from csirt.feeds import parse_date, parse_feed, plain_text
from csirt.http import fetch_request, retry_after

CERT = "https://cert.cyber.gouv.fr"
KEV = "https://www.cisa.gov/sites/default/files/feeds/known_exploited_vulnerabilities.json"
NVD = "https://services.nvd.nist.gov/rest/json/cves/2.0"
EPSS = "https://api.first.org/data/v1/epss"
CVE_API = "https://cveawg.mitre.org/api/cve/"
KINDS = {"alerte": "ALE", "avis": "AVI", "cti": "CTI", "ioc": "IOC"}
REFERENCE = re.compile(r"(?:CERTFR|CERTA)-20[0-9]{2}-(?:ALE|AVI|CTI|IOC)-[0-9]{2,4}")
CVE = re.compile(r"\bCVE-[0-9]{4}-[0-9]{4,19}\b", re.I)
HEADINGS = re.compile(r"<h[1-6]\b[^>]*>(.*?)</h[1-6]>|^#{1,6}\s+([^\n]+)", re.I | re.M | re.S)


class SourceError(ValueError):
    def __init__(self, message, delay=0):
        super().__init__(message)
        self.delay = delay


def request(client, settings, url, params=None, *, rss=False):
    result = fetch_request(client, {"request": {
        "url": url, "method": "GET", "params": params or {}, "headers_env": {},
        "headers": {} if rss else {"Accept": "application/json"},
    }}, settings)
    if result.status != 200:
        raise SourceError(f"HTTP {result.status}", retry_after(result.headers.get("Retry-After"), time.time()))
    return result.body if rss else json.loads(result.body)


def object_or_empty(value):
    return value if isinstance(value, dict) else {}


def array(value):
    return value if isinstance(value, list) else []


def clean(value, limit=1000):
    return plain_text(value, limit) if isinstance(value, str) else ""


def date(value):
    return parse_date(value) if isinstance(value, str) else None


def matches(reference, kind):
    return isinstance(reference, str) and REFERENCE.fullmatch(reference) and reference.split("-")[2] == KINDS[kind]


def sections(content):
    headings = list(HEADINGS.finditer(content))
    result = {"patch": [], "workaround": [], "recommendations": []}
    for index, heading in enumerate(headings):
        label = clean(heading[1] or heading[2]).casefold()
        end = headings[index + 1].start() if index + 1 < len(headings) else len(content)
        body = clean(content[heading.end():end], 16000)
        if not body:
            continue
        if label.startswith(("solution", "correctif")):
            result["patch"].append(body)
        elif label.startswith("contournement"):
            result["workaround"].append(body)
        elif label.startswith("recommandation"):
            result["recommendations"].append(body)
    return result


def publication(raw, kind):
    reference = raw.get("reference")
    title = clean(raw.get("title"), 500)
    if not matches(reference, kind) or not title:
        raise SourceError("Référence ou titre CERT-FR invalide")
    products = []
    for system in map(object_or_empty, array(raw.get("affected_systems"))):
        product = object_or_empty(system.get("product"))
        vendor = object_or_empty(product.get("vendor"))
        item = dict(vendor=clean(vendor.get("name")), name=clean(product.get("name")),
                    affected=clean(system.get("description"), 5000))
        item = {key: value for key, value in item.items() if value and value != "N/A"}
        if item and item not in products:
            products.append(item)
    description = clean(raw.get("summary"), 16000)
    content = raw.get("content") if isinstance(raw.get("content"), str) else ""
    products_text = clean(raw.get("affected_systems_content"), 16000)
    explicit = [clean(object_or_empty(item).get("name")) for item in array(raw.get("cves"))]
    content_text = clean(content, 200000)
    cves = sorted({match.upper() for match in CVE.findall(" ".join([
        title, description, content_text, *explicit,
    ]))})
    revisions = [
        dict(date=date(item.get("revision_date")), description=clean(item.get("description"), 4000))
        for item in map(object_or_empty, array(raw.get("revisions")))
        if item.get("revision_date") or item.get("description")
    ]
    dates = sorted(item["date"] for item in revisions if item["date"])
    risks = [clean(object_or_empty(item).get("description")) for item in array(raw.get("risks"))]
    remediation = sections(content)
    statements = re.split(r"(?<=[.!?])\s+", content_text + " " + description)
    patterns = {
        "patch": r"(?:des|un) correctifs? (?:sont|est) disponibles?\b|^correctifs? disponibles?\b",
        "poc": r"\ba publié une preuve de concept\b|\bont publié une preuve de concept\b|"
               r"^une preuve de concept (?:publique est (?:désormais )?disponible|est publiquement disponible)\b",
        "active": r"\b(?:est|sont) activement exploit[ée]e?s?\b",
    }
    evidence = {}
    evidence_cves = {}
    for key, pattern in patterns.items():
        associated = set()
        for statement in statements:
            if len(statement) <= 700 and re.search(pattern, statement, re.I) and not re.search(
                r"\b(?:si|pourrait|semble|pas|aucun|n’est|n'est)\b", statement, re.I
            ):
                evidence[key] = True
                mentioned = {value.upper() for value in CVE.findall(statement)}
                if len(mentioned) == 1:
                    associated.update(mentioned)
        if evidence.get(key) and len(cves) == 1:
            associated.update(cves)
        if associated:
            evidence_cves[key] = sorted(associated)
    severity = {
        "critique": "critical", "élevée": "high", "moyenne": "medium", "faible": "low", "nulle": "none",
    }.get(clean(raw.get("severity")).casefold())
    return dict(reference=reference, kind=kind, title=title, description=description,
                url=f"{CERT}/{kind}/{reference}/", products=products, products_text=products_text,
                published_at=date(raw.get("published_at")) or (dates[0] if dates else None),
                updated_at=max(filter(None, [date(raw.get("updated_at")), *dates]), default=None),
                cves=cves, risks=[risk for risk in risks if risk], revisions=revisions,
                remediation=remediation, evidence=evidence, evidence_cves=evidence_cves,
                official_severity=severity, official_severity_reason=clean(raw.get("severity_reason")) if severity else "")


def feed_publications(body, settings, kind):
    feed = parse_feed(body, "cert-fr", settings.max_entries)
    allowed = {"cert.cyber.gouv.fr", "www.cert.cyber.gouv.fr", "www.cert.ssi.gouv.fr", "cert.ssi.gouv.fr"}
    for entry in feed.entries:
        parts = urlsplit(entry.url)
        path = parts.path.strip("/").split("/")
        if parts.hostname in allowed and len(path) == 2 and path[0] == kind and matches(path[1], kind):
            yield publication(dict(reference=path[1], title=entry.title, summary=entry.summary,
                                   published_at=entry.published_at, updated_at=entry.updated_at), kind)


def nvd_record(payload, cve):
    records = [object_or_empty(item.get("cve")) for item in map(object_or_empty, array(payload.get("vulnerabilities")))]
    record = next((item for item in records if item.get("id") == cve), {})
    metrics = object_or_empty(record.get("metrics"))
    scores = []
    for version in ("cvssMetricV40", "cvssMetricV31", "cvssMetricV30", "cvssMetricV2"):
        for metric in map(object_or_empty, array(metrics.get(version))):
            data = object_or_empty(metric.get("cvssData"))
            score = data.get("baseScore")
            if type(score) in (int, float) and 0 <= score <= 10:
                scores.append(dict(score=score, version=clean(data.get("version")), issuer=clean(metric.get("source"))))
    descriptions = {item.get("lang"): clean(item.get("value"), 8000)
                    for item in map(object_or_empty, array(record.get("descriptions")))
                    if item.get("lang") in ("fr", "en")}
    return dict(scores=scores, description=descriptions.get("fr") or descriptions.get("en"))


def cve_record(payload, cve):
    if object_or_empty(payload.get("cveMetadata")).get("cveId") != cve:
        raise SourceError("Fiche CVE inattendue")
    cna = object_or_empty(object_or_empty(payload.get("containers")).get("cna"))
    types = [clean(desc.get("description")) for item in map(object_or_empty, array(cna.get("problemTypes")))
             for desc in map(object_or_empty, array(item.get("descriptions")))]
    affected = []
    for item in map(object_or_empty, array(cna.get("affected"))):
        versions = [{field: clean(version.get(field)) for field in
                     ("version", "lessThan", "lessThanOrEqual", "status", "versionType")}
                    for version in map(object_or_empty, array(item.get("versions")))]
        affected.append(dict(vendor=clean(item.get("vendor")), product=clean(item.get("product")), versions=versions))
    return dict(types=sorted(set(filter(None, types))), affected=affected)
