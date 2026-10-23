"""Statut editorial des alertes, distinct de l'identite juridique et du siege."""
import json

from csirt.feeds import normalize, classify

LABELS={'confirmed':'Confirmé selon la source','claimed':'Revendiqué','unreliable_claim':'Revendication peu fiable',
        'unknown':'Non renseigné','mixed':'Sources divergentes'}
INDEX_LABELS={'credible':'Crédible selon la source','unreliable':'Peu fiable selon la source',
              'mixed':'Indices divergents','unspecified':'Non publié','not_applicable':'Sans objet'}

# Expressions identiques a l'agregation Python ; recherches indexees par incident
# et statut. Aucun score de nom d'entreprise ne participe a cette decision.
def alert_status_sql(table='observations'):
    if table not in {'observations', 'period_observations'}:
        raise ValueError('Table de publications invalide')
    def has(status):
        return f"EXISTS(SELECT 1 FROM {table} oa WHERE oa.incident_id=i.id AND oa.status='{status}')"
    return (f"CASE WHEN {has('unreliable_claim')} AND ({has('confirmed')} OR {has('claimed')}) THEN 'mixed' "
            f"WHEN {has('confirmed')} THEN 'confirmed' WHEN {has('unreliable_claim')} THEN 'unreliable_claim' "
            f"WHEN {has('claimed')} THEN 'claimed' ELSE 'unknown' END")

ALERT_STATUS_SQL=alert_status_sql()


def publication_assessment(publication):
    status=publication.get('status','unknown')
    if status not in LABELS or status=='mixed':
        status='unknown'
    categories=publication.get('categories',[])
    if isinstance(categories,str):
        categories=json.loads(categories)
    title=publication.get('title','')
    markers=[]
    for category in categories:
        declared=classify('','',[category])[1]
        if declared!='unknown':
            markers.append({'field':'category','value':category,'status':declared})
    if title.startswith(('🟢','🟠','🔴')):
        markers.append({'field':'title_prefix','value':title[0],'status':classify(title,'',[])[1]})
    cats={normalize(c) for c in categories}
    credible=bool(cats & {'credible','claimed credible','unconfirmed credible'} or title.startswith('🟠'))
    unreliable=bool(cats & {'unreliable','peu fiable','claimed unreliable','unconfirmed unreliable'} or title.startswith('🔴'))
    index='mixed' if credible and unreliable else 'unreliable' if unreliable else 'credible' if credible else 'not_applicable' if status=='confirmed' else 'unspecified'
    return {'status':status,'status_label':LABELS[status],
            'claim_index':{'value':index,'label':INDEX_LABELS[index],'numeric_score':None},
            'basis':markers,'attribution':'publisher','independent_verification':False}


def summarize(publications):
    evidence=[]
    for publication in publications:
        assessment=publication_assessment(publication)
        evidence.append({'observation_id':publication.get('id'),'source_id':publication['source_id'],
            'source_name':publication.get('source_name',publication['source_id']),'url':publication['url'],
            'published_at':publication.get('published_at'),'title':publication['title'],**assessment})
    states={p['status'] for p in evidence}
    divergent='unreliable_claim' in states and bool(states & {'confirmed','claimed'})
    status='mixed' if divergent else next((s for s in ('confirmed','unreliable_claim','claimed') if s in states),'unknown')
    indexes={p['claim_index']['value'] for p in evidence}-{ 'unspecified','not_applicable'}
    index='mixed' if 'mixed' in indexes or {'credible','unreliable'}<=indexes else next(iter(indexes),'not_applicable' if status=='confirmed' else 'unspecified')
    return {'status':status,'status_label':LABELS[status],
            'claim_index':{'value':index,'label':INDEX_LABELS[index],'numeric_score':None},
            'divergent':divergent or index=='mixed','publication_count':len(evidence),
            'counts':{s:sum(p['status']==s for p in evidence) for s in ('confirmed','claimed','unreliable_claim','unknown')},
            'sources':evidence,'independent_verification':False}


def incident_alerts(db,incident_ids):
    if not incident_ids:
        return {}
    marks=','.join('?' for _ in incident_ids)
    grouped={incident_id:[] for incident_id in incident_ids}
    rows=db.execute("SELECT o.id,o.incident_id,o.source_id,o.url,o.title,o.published_at,o.status,o.categories,s.name AS source_name "
                    f"FROM observations o JOIN sources s ON s.id=o.source_id WHERE o.incident_id IN ({marks}) "
                    "ORDER BY o.published_at DESC,o.id DESC",incident_ids)
    for row in rows:
        grouped[row['incident_id']].append(dict(row))
    return {incident_id:summarize(rows) for incident_id,rows in grouped.items()}
