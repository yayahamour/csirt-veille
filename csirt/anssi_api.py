"""API locale de consultation et de suivi des publications CERT-FR."""
import json
from datetime import date, datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from csirt.anssi_collect import MANUAL_COOLDOWN, queue
from csirt.anssi_sources import CVE
from csirt.anssi_store import TRACKING, timestamp, write
from csirt.db import connect
from csirt.feeds import normalize
from csirt.runtime import collector_status

Severity = Literal["critical", "high", "medium", "low", "none"]
Treatment = Literal["new", "review", "action", "closed"]
CHANGE_RECENCY_DAYS = 7

SELECTION = """
WITH scores AS (
    SELECT c.reference, MAX(json_extract(m.value,'$.score')) AS score
    FROM anssi_cves c JOIN anssi_cache a ON a.source='NVD' AND a.cache_key=c.cve,
         json_each(a.data, '$.scores') m GROUP BY c.reference
), publications AS (
    SELECT p.*, s.score, t.severity_reason, t.severity AS internal_severity,
      COALESCE(t.read,0) AS read, COALESCE(t.important,0) AS important, COALESCE(t.watch,0) AS watch,
      COALESCE(t.status,'new') AS treatment,
      CASE WHEN t.important OR t.watch OR t.status IN ('review','action') THEN 'tracked'
           WHEN t.read THEN 'read' ELSE 'unread' END AS consultation,
      COALESCE(json_extract(p.data,'$.official_severity'),t.severity,
         CASE WHEN s.score>=9 THEN 'critical' WHEN s.score>=7 THEN 'high'
              WHEN s.score>=4 THEN 'medium' WHEN s.score>0 THEN 'low'
              WHEN s.score=0 THEN 'none' END) AS severity,
      EXISTS(SELECT 1 FROM anssi_cves c JOIN anssi_kev k USING(cve) WHERE c.reference=p.reference) AS kev
    FROM anssi_publications p LEFT JOIN anssi_tracking t USING(reference)
    LEFT JOIN scores s USING(reference)
)
"""


class TrackingUpdate(BaseModel):
    read: bool = False
    important: bool = False
    watch: bool = False
    status: Treatment = "new"
    note: str = Field("", max_length=8000)
    severity: Severity | None = None
    severity_reason: str = Field("", max_length=1000)


def job_payload(row):
    if row is None:
        return None
    item = dict(row)
    item.pop("lease_until")
    item["errors"] = json.loads(item["errors"])
    item["next_manual_at"] = (
        datetime.fromisoformat(item["finished_at"]) + timedelta(seconds=MANUAL_COOLDOWN)
    ).isoformat() if item["finished_at"] else None
    return item


def filters_sql(filters, since, until):
    clauses, params = [], []
    for column in ("kind", "consultation", "treatment", "severity"):
        if filters[column]:
            clauses.append(f"{column}=?")
            params.append(filters[column])
    if filters["q"]:
        for term in normalize(filters["q"]).split()[:20]:
            clauses.append("instr(search_text,?)>0")
            params.append(term)
    if filters["product"]:
        for term in normalize(filters["product"]).split()[:20]:
            clauses.append("instr(product_search,?)>0")
            params.append(term)
    if filters["cve"]:
        if not CVE.fullmatch(filters["cve"]):
            raise HTTPException(422, "Identifiant CVE invalide")
        clauses.append("EXISTS(SELECT 1 FROM anssi_cves c WHERE c.reference=p.reference AND c.cve=?)")
        params.append(filters["cve"].upper())
    if since and until and since > until:
        raise HTTPException(422, "La date de début doit précéder la date de fin")
    for operator, value in ((">=", since), ("<=", until)):
        if value:
            clauses.append(f"substr(published_at,1,10) {operator} ?")
            params.append(value.isoformat())
    for key in ("patch", "active", "poc"):
        if filters[key] is not None:
            predicate = f"COALESCE(json_extract(data,'$.evidence.{key}'),0)"
            if key == "active":
                predicate = f"({predicate} OR kev)"
            clauses.append(f"{predicate}=?")
            params.append(int(filters[key]))
    return (" WHERE " + " AND ".join(clauses) if clauses else ""), params


def publication_payload(row):
    item = json.loads(row["data"])
    if item.get("official_severity"):
        source = "CERT-FR"
        reason = item.get("official_severity_reason")
    elif row["internal_severity"]:
        source = "Interne"
        reason = row["severity_reason"]
    elif row["score"] is not None:
        source = "CVSS / NVD"
        reason = f"Score de base maximal parmi les CVE du bulletin ({row['score']}/10)."
    else:
        source, reason = None, None
    return {**item, "severity": row["severity"], "severity_source": source, "severity_reason": reason,
            "consultation": row["consultation"], "kev": bool(row["kev"]), "verified_at": row["checked_at"],
            "changed_at": row["changed_at"]}


def local_mutation(request: Request):
    # Envoi par le navigateur avec préflight obligatoire depuis une autre origine.
    if request.headers.get("x-csirt-client") != "observatoire":
        raise HTTPException(403, "En-tête X-CSIRT-Client requis")


def router(settings):
    api = APIRouter(prefix="/anssi", tags=["CERT-FR"])

    @api.get("/summary")
    def summary():
        cutoff = (datetime.now(timezone.utc) - timedelta(days=CHANGE_RECENCY_DAYS)).isoformat(timespec="seconds")
        with connect(settings) as db:
            db.execute("BEGIN")
            counts = db.execute(SELECTION + """
                SELECT COUNT(*) AS total,
                    COUNT(*) FILTER(WHERE kind='alerte' AND read=0) AS new_alerts,
                    COUNT(*) FILTER(WHERE kind='alerte' AND severity='critical') AS critical_alerts,
                    COUNT(*) FILTER(WHERE kind='alerte' AND
                        ((updated_at>published_at AND updated_at>=?) OR changed_at>=?)) AS updated_alerts
                FROM publications""", (cutoff, cutoff)).fetchone()
            cves = db.execute("SELECT COUNT(DISTINCT cve) FROM anssi_cves").fetchone()[0]
            last = db.execute("SELECT * FROM anssi_sync ORDER BY id DESC LIMIT 1").fetchone()
            last_success = db.execute("""SELECT MAX(finished_at) FROM anssi_sync
                                         WHERE status IN ('success','partial')""").fetchone()[0]
            return {**dict(counts), "cves": cves, "last_sync_at": last_success,
                    "job": job_payload(last), "collector": collector_status(db)}

    @api.post("/sync", status_code=202)
    def synchronize(request: Request):
        local_mutation(request)
        return job_payload(queue(settings))

    @api.get("/publications")
    def publications(
        kind: Literal["alerte", "avis", "cti", "ioc"] | None = None,
        consultation: Literal["unread", "read", "tracked"] | None = None,
        treatment: Treatment | None = None,
        severity: Severity | None = None,
        since: date | None = None, until: date | None = None,
        q: str | None = Query(None, max_length=128),
        product: str | None = Query(None, max_length=128),
        cve: str | None = Query(None, max_length=32),
        patch: bool | None = None, active: bool | None = None, poc: bool | None = None,
        sort: Literal["published_at", "updated_at", "title", "reference", "severity"] = "published_at",
        order: Literal["asc", "desc"] = "desc",
        limit: int = Query(25, ge=1, le=100), offset: int = Query(0, ge=0),
    ):
        where, params = filters_sql(locals(), since, until)
        sort_by = {
            "severity": "CASE severity WHEN 'critical' THEN 4 WHEN 'high' THEN 3 WHEN 'medium' THEN 2 "
                        "WHEN 'low' THEN 1 WHEN 'none' THEN 0 END",
            "title": "title COLLATE ALPHA",
        }.get(sort, sort)
        with connect(settings) as db:
            db.execute("BEGIN")
            total = db.execute(SELECTION + "SELECT COUNT(*) FROM publications p" + where, params).fetchone()[0]
            rows = db.execute(SELECTION + "SELECT * FROM publications p" + where +
                              f" ORDER BY {sort_by} {order} NULLS LAST,reference {order} LIMIT ? OFFSET ?",
                              [*params, limit, offset]).fetchall()
        return {"total": total, "items": [publication_payload(row) for row in rows], "offset": offset}

    @api.get("/publications/{reference}")
    def detail(reference: str):
        with connect(settings) as db:
            db.execute("BEGIN")
            row = db.execute(SELECTION + "SELECT * FROM publications WHERE reference=?", (reference,)).fetchone()
            if row is None:
                raise HTTPException(404, "Publication introuvable")
            item = publication_payload(row)
            tracking = db.execute("SELECT * FROM anssi_tracking WHERE reference=?", (reference,)).fetchone()
            item["tracking"] = {key: tracking[key] for key in TRACKING} if tracking else TRACKING.copy()
            changes = db.execute("""SELECT detected_at,fields FROM anssi_changes
                                    WHERE reference=? ORDER BY id DESC LIMIT 30""", (reference,))
            item["changes"] = [{**dict(change), "fields": json.loads(change["fields"])} for change in changes]
            item["enrichment"] = {cve: {} for cve in item["cves"]}
            item["official_evidence_by_cve"] = {
                cve: [key for key, refs in item.get("evidence_cves", {}).items() if cve in refs]
                for cve in item["cves"]
            }
            rows = db.execute("""SELECT a.* FROM anssi_cves c JOIN anssi_cache a ON a.cache_key=c.cve
                                 WHERE c.reference=? AND a.data IS NOT NULL""", (reference,))
            for cache in rows:
                item["enrichment"][cache["cache_key"]][cache["source"]] = {
                    "data": json.loads(cache["data"]), "retrieved_at": cache["retrieved_at"],
                }
            catalogue = db.execute("SELECT retrieved_at FROM anssi_cache WHERE source='CISA KEV' AND cache_key='catalogue'").fetchone()
            if catalogue and catalogue[0]:
                kev = {k["cve"]: k["date_added"] for k in db.execute("""SELECT k.* FROM anssi_cves c
                       JOIN anssi_kev k USING(cve) WHERE c.reference=?""", (reference,))}
                for cve, records in item["enrichment"].items():
                    records["CISA KEV"] = {"data": {"listed": cve in kev, "date_added": kev.get(cve)},
                                           "retrieved_at": catalogue[0]}
            return item

    @api.patch("/publications/{reference}/tracking")
    def track(reference: str, change: TrackingUpdate, request: Request):
        local_mutation(request)
        with write(settings) as db:
            if not db.execute("SELECT 1 FROM anssi_publications WHERE reference=?", (reference,)).fetchone():
                raise HTTPException(404, "Publication introuvable")
            db.execute("INSERT OR IGNORE INTO anssi_tracking(reference) VALUES(?)", (reference,))
            existing = db.execute("SELECT * FROM anssi_tracking WHERE reference=?", (reference,)).fetchone()
            changes = change.model_dump(exclude_unset=True)
            severity = changes.get("severity", existing["severity"])
            if severity and not changes.get("severity_reason", existing["severity_reason"]).strip():
                raise HTTPException(422, "Justifiez la criticité interne")
            changes["updated_at"] = timestamp()
            assignments = ",".join(f"{key}=?" for key in changes)
            db.execute(f"UPDATE anssi_tracking SET {assignments} WHERE reference=?", (*changes.values(), reference))
            return {key: changes.get(key, existing[key]) for key in TRACKING}

    return api
