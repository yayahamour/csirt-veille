"""Regroupement local d'alertes : variantes attestées, dates et type d'événement."""
import json
import re
import time
from dataclasses import asdict
from datetime import datetime,timedelta,timezone
from itertools import combinations
from csirt.feeds import normalize,organization

VERSION='alerts-context-48h-v4'
WINDOW_DAYS=2
GENERIC={'france','societe','groupe','group','association','centre','services','inconnu','un','une','reseau','sud','nord'}
STOPWORDS=set('de des du le la les l d et en un une a au aux pour par sur chez est son ses sa une fait objet donnees donnee fuite cyberattaque attaque informations information clients personnes lignes fiches millions million milliers pres plus visant revendiquee revendique confirme confirmee mise jour octobre septembre janvier fevrier mars avril mai juin juillet aout novembre decembre'.split())


def grouping_key(name):
    value=normalize((name or '').replace('œ','oe').replace('Œ','OE').replace('æ','ae'))
    compact=value.replace(' ','')
    if not value or value in GENERIC:return ''
    return compact if len(compact)>=5 else 'exact:'+value


def _base(name):
    name=re.sub(r'\([^()]*\)','',name or '').strip()
    name=re.sub(r"^(?:l['’]|le\s+|la\s+|les\s+)", '',name,flags=re.I).strip()
    # Un préfixe descriptif ne doit pas supprimer le seul mot distinctif du nom.
    match=re.match(r'^(?:soci[eé]t[eé]|r[eé]seau|groupe|association)\s+(.+)$',name,re.I)
    if match:
        tail=match[1].strip(); tokens=normalize(tail).split()
        letters=re.sub(r'[^A-Za-zÀ-ÿ0-9]','',tail)
        if (len(tokens)>=2 and len(''.join(tokens))>=10) or (len(letters)>=3 and letters.isupper()): name=tail
    return name


def aliases(name):
    """Les parenthèses fournissent un sigle explicite, jamais un propriétaire implicite."""
    if not name:return set()
    variants={grouping_key(name),grouping_key(_base(name))}
    base_words=[w for w in normalize(_base(name)).split() if w not in STOPWORDS]
    initials=''.join(w[0] for w in base_words)
    for annotation in re.findall(r'\(([^()]*)\)',name):
        word=annotation.strip()
        if re.fullmatch(r'[A-Za-z0-9]{2,12}',word) and (word.isupper() or word.casefold()==initials or re.match(r'^[A-Z]{2,}',word)):
            variants.add(grouping_key(word))
    return variants-{''}


def _qualifiers(name):
    # Deux lieux/qualificatifs explicites différents ne deviennent pas identiques
    # simplement parce qu'on a retiré les parenthèses.
    result=set()
    for value in re.findall(r'\(([^()]*)\)',name or ''):
        key=grouping_key(value)
        if key and key not in aliases(name): result.add(normalize(value))
    return result


def _one_typo(left,right):
    """Une seule insertion, suppression ou substitution sur des noms assez longs."""
    if min(len(left),len(right))<6 or abs(len(left)-len(right))>1:return False
    if len(left)==len(right):return sum(a!=b for a,b in zip(left,right))<=1
    short,long=sorted((left,right),key=len)
    index=next((i for i,(a,b) in enumerate(zip(short,long)) if a!=b),len(short))
    return short[index:]==long[index+1:]


def _attested_lead(publication):
    name=publication.get('organization')
    # Une variante n'est admise que si la source la nomme elle-même dans
    # l'accroche du résumé et qu'une seule coquille la sépare du nom du titre.
    lead=organization(publication.get('summary','')[:240],'','article')
    if lead and _one_typo(grouping_key(_base(name)).removeprefix('exact:'),grouping_key(_base(lead)).removeprefix('exact:')):return lead
    return None


def publication_aliases(publication):
    keys=aliases(publication.get('organization'))
    keys.update(aliases(_attested_lead(publication)))
    return keys


def _service_agreement(left,right):
    for owner,service in ((left,right),(right,left)):
        name=owner.get('organization')
        if not name:continue
        parent=grouping_key(_base(name))
        if parent not in {grouping_key(q) for q in _qualifiers(service.get('organization'))}:continue
        subject=normalize(_base(service.get('organization')))
        if len(subject.replace(' ',''))<3:continue
        # La victime/service doit être le sujet explicite des données exposées,
        # pas une simple mention d'un hébergeur, d'un fournisseur ou d'une région.
        text=normalize(owner.get('title','')+' '+owner.get('summary',''))
        pattern=r'\b(?:base|fichier|donnees|dossiers|comptes)(?:\s+\w+){0,5}\s+(?:a|de|du|des)\s+(?:l\s+)?'+re.escape(subject)+r'\b'
        if re.search(pattern,text):return True
    return False


def publication_keys(publication):
    keys=publication_aliases(publication)
    contexts={_base(publication.get('organization')),*_qualifiers(publication.get('organization'))}
    for value in contexts:
        key=grouping_key(value)
        if key and len(key.removeprefix('exact:'))>=8:keys.add('context:'+key)
    return keys


def name_agreement(left,right):
    a,b=left.get('organization'),right.get('organization')
    if not publication_aliases(left)&publication_aliases(right):return _service_agreement(left,right)
    # Un sigle partagé ne suffit pas à réunir deux développements différents.
    ba,bb=_base(a),_base(b)
    developed_left={grouping_key(ba),grouping_key(_base(_attested_lead(left)))}-{''}
    developed_right={grouping_key(bb),grouping_key(_base(_attested_lead(right)))}-{''}
    if len(normalize(ba).split())>=2 and len(normalize(bb).split())>=2 and not developed_left&developed_right:
        return False
    qa,qb=_qualifiers(a),_qualifiers(b)
    return not (qa and qb and qa.isdisjoint(qb))


def canonical_incident_id(db,incident_id):
    row=db.execute('SELECT incident_id FROM incident_merges WHERE old_id=?',(incident_id,)).fetchone()
    return row[0] if row else incident_id


def next_incident_id(db):
    current=db.execute('SELECT COALESCE(MAX(id),0) FROM incidents').fetchone()[0]
    archived=db.execute('SELECT COALESCE(MAX(old_id),0) FROM incident_merges').fetchone()[0]
    return max(current,archived)+1


def queue_grouping(db,incident_id,name):
    rows=_publications(db,incident_id)
    keys=publication_keys({'organization':name})
    for row in rows:keys.update(publication_keys(row))
    db.execute('UPDATE incidents SET grouping_key=?,grouping_version=? WHERE id=?',(grouping_key(name),VERSION,incident_id))
    db.execute('DELETE FROM incident_grouping_aliases WHERE incident_id=?',(incident_id,))
    db.executemany('INSERT INTO incident_grouping_aliases VALUES(?,?)',[(incident_id,k) for k in sorted(keys)])
    db.execute('INSERT OR IGNORE INTO incident_grouping_queue VALUES (?)',(incident_id,))


def backfill_grouping_keys(settings,limit=100):
    from csirt.db import connect
    with connect(settings) as db:
        rows=db.execute('SELECT id,organization FROM incidents WHERE grouping_version IS NULL OR grouping_version<>? ORDER BY id LIMIT ?',(VERSION,limit)).fetchall()
        for row in rows:queue_grouping(db,row['id'],row['organization'])
    return len(rows)


def _date(value):
    if not value:return None
    try:
        date=datetime.fromisoformat(value.replace('Z','+00:00'))
        return date.replace(tzinfo=date.tzinfo or timezone.utc).astimezone(timezone.utc)
    except ValueError:return None


def date_bounds(value,days=WINDOW_DAYS):
    date=_date(value)
    return ((date-timedelta(days=days)).isoformat(timespec='seconds'),(date+timedelta(days=days)).isoformat(timespec='seconds'))


def compatible_kinds(kinds):
    return not ('vulnerability' in kinds and kinds & {'data_breach','cyberattack','ransomware'})


def event_family(publication):
    title=normalize(publication.get('title',''))
    if re.search(r'\bddos\b|\bdeni de service\b',title):return 'availability'
    text=normalize(publication.get('summary',''))
    if re.search(r'\bddos\b|\bdeni de service\b',text) and publication.get('kind')!='data_breach':return 'availability'
    # Une faille peut être la cause d'une fuite déjà survenue : ce n'est alors
    # pas une simple alerte de vulnérabilité (ex. correctif IDOR après fuite).
    exposure=re.search(r'\bfuite\b|\ben fuite\b|donnees.{0,70}(?:revendiqu|vole|expos|derob|publi)',title+' '+text)
    if publication.get('kind')=='vulnerability' and not exposure:return 'vulnerability'
    return 'intrusion'


def publications_agree(left,right):
    if not name_agreement(left,right):return False
    if event_family(left)!=event_family(right):return False
    dates=[_date(left.get('published_at')),_date(right.get('published_at'))]
    if not all(dates):return False
    span=abs(dates[0]-dates[1])
    return span<timedelta(days=WINDOW_DAYS)


def _publications(db,incident_id):
    return [dict(r) for r in db.execute('SELECT * FROM observations WHERE incident_id=? ORDER BY id',(incident_id,))]


def _cohort_agrees(groups):
    all_rows=[p for group in groups for p in group]
    sources=[p['source_id'] for p in all_rows]
    if len(sources)!=len(set(sources)):return False
    dates=[_date(p.get('published_at')) for p in all_rows]
    if not dates or not all(dates) or max(dates)-min(dates)>=timedelta(days=WINDOW_DAYS):return False
    # Comparaison de toutes les publications : pas de fusion par chaîne de sigles/dates.
    return all(publications_agree(a,b) for a,b in combinations(all_rows,2))


def _candidates(db,keys,published_at):
    if not keys or not _date(published_at):return []
    low,high=date_bounds(published_at)
    marks=','.join('?' for _ in keys)
    rows=db.execute(f'SELECT DISTINCT i.* FROM incident_grouping_aliases a JOIN incidents i ON i.id=a.incident_id WHERE a.alias_key IN ({marks}) AND i.reference_at BETWEEN ? AND ? ORDER BY i.id LIMIT 33',[*sorted(keys),low,high]).fetchall()
    return rows if len(rows)<=32 else []


def find_incident(db,name,published_at,source_id,kind,*,entry=None):
    incoming={**(asdict(entry) if entry else {}),'organization':name,'published_at':published_at,'source_id':source_id,'kind':kind}
    candidates=[row for row in _candidates(db,publication_keys(incoming),published_at) if _cohort_agrees([_publications(db,row['id']),[incoming]])]
    return candidates[0] if len(candidates)==1 and _unambiguous(db,candidates) else None


def mergeable(db,incidents):
    return _cohort_agrees([_publications(db,r['id']) for r in incidents])


def _unambiguous(db,cohort):
    ids={r['id'] for r in cohort}
    for incident in cohort:
        keys={r[0] for r in db.execute('SELECT alias_key FROM incident_grouping_aliases WHERE incident_id=?',(incident['id'],))}
        for other in _candidates(db,keys,incident['reference_at']):
            if other['id'] not in ids and mergeable(db,[incident,other]):return False
    return True


def merge_incidents(db,target,old):
    snapshot={'incidents':[dict(r) for r in db.execute('SELECT * FROM incidents WHERE id IN (?,?)',(target,old))],
              'observations':[dict(r) for r in db.execute('SELECT * FROM observations WHERE incident_id IN (?,?)',(target,old))]}
    db.execute('UPDATE observations SET incident_id=? WHERE incident_id=?',(target,old))
    db.execute('DELETE FROM incident_grouping_queue WHERE incident_id=?',(old,))
    db.execute('UPDATE incident_merges SET incident_id=? WHERE incident_id=?',(target,old))
    db.execute('INSERT INTO incident_merges VALUES (?,?,?,?,?)',(old,target,time.time(),'organization_alias_same_event',json.dumps(snapshot)))
    db.execute('UPDATE incidents SET first_seen_at=?,last_changed_at=?,activity_seq=? WHERE id=?',
               (min(r['first_seen_at'] for r in snapshot['incidents']),datetime.now(timezone.utc).isoformat(timespec='seconds'),max(r['activity_seq'] for r in snapshot['incidents']),target))
    db.execute('DELETE FROM incidents WHERE id=?',(old,))
    # Quand une source nomme le parent et les autres le service explicitement
    # visé, afficher le nom précis du service tout en gardant chaque citation.
    for row in snapshot['observations']:
        owner={'organization':snapshot['incidents'][0]['organization'],
               'title':' '.join(r['title'] for r in snapshot['observations']),
               'summary':' '.join(r['summary'] for r in snapshot['observations'])}
        if _service_agreement(owner,row) and grouping_key(_base(owner['organization'])) in {grouping_key(q) for q in _qualifiers(row['organization'])}:
            db.execute('UPDATE incidents SET organization=?,organization_key=? WHERE id=?',(row['organization'],normalize(row['organization']),target))
            break
    name=db.execute('SELECT organization FROM incidents WHERE id=?',(target,)).fetchone()[0]
    queue_grouping(db,target,name)


def _split_incompatible(db,incident):
    rows=_publications(db,incident['id'])
    groups=[]
    for row in sorted(rows,key=lambda r:(_date(r['published_at']) or datetime.min.replace(tzinfo=timezone.utc),r['id'])):
        date=_date(row['published_at'])
        group=next((g for g in groups if date and _date(g[0]['published_at']) and
                    event_family(g[0])==event_family(row) and
                    date-_date(g[0]['published_at'])<timedelta(days=WINDOW_DAYS)),None)
        if group is None:groups.append([row])
        else:group.append(row)
    if len(groups)<2:return []
    # Corriger aussi l'historique : aucune chaîne de dates ne dépasse 48 h.
    original={**dict(incident),'observations':rows};created=[]
    for moved in groups:
        if any(row['id']==rows[0]['id'] for row in moved):continue
        new_id=next_incident_id(db);first=moved[0]
        db.execute('INSERT INTO incidents(id,organization,organization_key,title,reference_at,first_seen_at,last_changed_at,activity_seq) VALUES(?,?,?,?,?,?,?,?)',
                   (new_id,first['organization'],normalize(first['organization'] or ''),first['title'],first['published_at'],min(r['first_seen_at'] for r in moved),incident['last_changed_at'],incident['activity_seq']))
        db.executemany('UPDATE observations SET incident_id=? WHERE id=?',[(new_id,r['id']) for r in moved])
        db.execute('INSERT INTO incident_splits(original_id,new_id,split_at,reason,snapshot) VALUES(?,?,?,?,?)',
                   (incident['id'],new_id,time.time(),'incompatible_event_or_publication_gap_48h',json.dumps(original)))
        moved_ids={row['id'] for row in moved}
        for merge in db.execute('SELECT old_id,snapshot FROM incident_merges WHERE incident_id=?',(incident['id'],)).fetchall():
            archived=json.loads(merge['snapshot']).get('observations',[])
            old_ids={row['id'] for row in archived if row['incident_id']==merge['old_id']}
            if old_ids and old_ids<=moved_ids:
                db.execute('UPDATE incident_merges SET incident_id=? WHERE old_id=?',(new_id,merge['old_id']))
        queue_grouping(db,new_id,first['organization']);created.append(new_id)
    queue_grouping(db,incident['id'],incident['organization'])
    return created


def repair_duplicate_incidents(settings,limit=100):
    from csirt.db import connect
    backfill_grouping_keys(settings,limit)
    merged=[]
    with connect(settings) as db:
        db.execute('BEGIN IMMEDIATE')
        queued=db.execute('SELECT incident_id FROM incident_grouping_queue ORDER BY incident_id LIMIT ?',(limit,)).fetchall()
        for queued_row in queued:
            incident=db.execute('SELECT * FROM incidents WHERE id=?',(queued_row[0],)).fetchone()
            if not incident:continue
            db.execute('DELETE FROM incident_grouping_queue WHERE incident_id=?',(incident['id'],))
            if _split_incompatible(db,incident):continue
            if not incident['reference_at']:continue
            keys={r[0] for r in db.execute('SELECT alias_key FROM incident_grouping_aliases WHERE incident_id=?',(incident['id'],))}
            candidates=[r for r in _candidates(db,keys,incident['reference_at']) if r['id']!=incident['id'] and mergeable(db,[incident,r])]
            cohort=[incident,*candidates]
            if not candidates or not mergeable(db,cohort) or not _unambiguous(db,cohort):continue
            target=min(r['id'] for r in cohort)
            for old in sorted(r['id'] for r in cohort if r['id']!=target):
                merge_incidents(db,target,old);merged.append({'old_id':old,'incident_id':target})
    return merged
